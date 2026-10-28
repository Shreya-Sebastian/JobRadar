import json
from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from radar.adapters.base import RawPosting

KEY = "0123456789abcdef0123456789abcdef"


def test_sends_new_and_closed_job_pages_once(fresh_db, monkeypatch):
    from radar import indexnow
    from radar.config import settings
    from radar.crawler import ingest
    from radar.db import session_scope
    from radar.models import Posting, Source

    monkeypatch.setattr(settings, "site_url", "https://techjobsradar.com")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(202)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with session_scope() as s:
        monkeypatch.setattr(settings, "indexnow_key", None)
        assert indexnow.notify(s, client=client) == {"skipped": "no IndexNow key"}
        monkeypatch.setattr(settings, "indexnow_key", KEY)
        src = Source(company="Acme", ats="greenhouse", slug="acme")
        s.add(src)
        s.flush()
        ingest(s, src, [RawPosting(external_id=str(i), title=f"Backend Engineer {i}", url=f"https://acme/{i}",
                                   location="Utrecht, Netherlands", description_text="Python, AWS, Docker. " * 20)
                        for i in range(3)])
        assert indexnow.notify(s, client=client)["sent"] == 3  # the first run: every live job page
        body = calls[0]
        assert body["host"] == "techjobsradar.com" and body["key"] == KEY
        assert body["keyLocation"] == f"https://techjobsradar.com/{KEY}.txt"
        assert all(u.startswith("https://techjobsradar.com/job/") for u in body["urlList"])
        assert indexnow.notify(s, client=client)["sent"] == 0  # nothing twice
        first = s.query(Posting).filter_by(external_id="0").one()
        first.closed_at = datetime.utcnow() + timedelta(seconds=1)
        s.flush()
        assert indexnow.notify(s, client=client)["sent"] == 1  # a closed page is sent again, so engines drop it

        def refused(request):
            return httpx.Response(403, text="key not valid")

        first.closed_at = datetime.utcnow() + timedelta(seconds=1)
        s.flush()
        bad = httpx.Client(transport=httpx.MockTransport(refused))
        assert indexnow.notify(s, client=bad)["sent"] == 0
        assert indexnow.notify(s, client=client)["sent"] == 1  # a refused run is tried again


def test_the_key_file_is_served_only_for_the_key(fresh_db, monkeypatch):
    from radar.api import app
    from radar.config import settings

    monkeypatch.setattr(settings, "indexnow_key", KEY)
    with TestClient(app) as c:
        r = c.get(f"/{KEY}.txt")
        assert r.status_code == 200 and r.text == KEY
        assert c.get("/other.txt").status_code == 404
        assert c.get("/robots.txt").text.startswith("User-agent")
        assert c.get(f"/{KEY}.txt", headers={"user-agent": "Go-http-client/2.0"}).status_code == 200
        assert c.get("/", headers={"user-agent": "Go-http-client/2.0"}).status_code == 403
