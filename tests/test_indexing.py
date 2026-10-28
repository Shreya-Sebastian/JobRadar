import json
from datetime import datetime, timedelta

import httpx

from radar.adapters.base import RawPosting


def _key(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    path = tmp_path / "key.json"
    path.write_text(json.dumps({"client_email": "radar@example.iam.gserviceaccount.com", "private_key": pem,
                                "private_key_id": "k1", "token_uri": "https://oauth2.example/token"}))
    return path


def test_announces_new_jobs_and_withdraws_closed_ones(fresh_db, tmp_path, monkeypatch):
    from radar import indexing
    from radar.config import settings
    from radar.crawler import ingest
    from radar.db import session_scope
    from radar.models import IndexNotice, Posting, Source

    monkeypatch.setattr(settings, "google_indexing_key", str(_key(tmp_path)))
    monkeypatch.setattr(settings, "site_url", "https://techjobsradar.com")
    indexing._token_cache.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.example":
            return httpx.Response(200, json={"access_token": "t0k", "expires_in": 3600})
        calls.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer t0k"
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with session_scope() as s:
        src = Source(company="Acme", ats="greenhouse", slug="acme")
        s.add(src)
        s.flush()
        ingest(s, src, [RawPosting(external_id=str(i), title=f"Backend Engineer {i}", url=f"https://acme/{i}",
                                   location="Utrecht, Netherlands", description_text="Python, AWS, Docker. " * 20)
                        for i in range(3)])
        assert indexing.notify(s, client=client)["sent"] == 3
        assert {c["type"] for c in calls} == {"URL_UPDATED"} and calls[0]["url"].startswith("https://techjobsradar.com/job/")
        assert indexing.notify(s, client=client)["sent"] == 0  # nothing twice
        first = s.query(Posting).filter_by(external_id="0").one()
        first.closed_at = datetime.utcnow()
        s.flush()
        calls.clear()
        assert indexing.notify(s, client=client)["sent"] == 1 and calls[0]["type"] == "URL_DELETED"
        assert s.get(IndexNotice, first.id).kind == "URL_DELETED"


def test_daily_quota_and_missing_key(fresh_db, tmp_path, monkeypatch):
    from radar import indexing
    from radar.config import settings
    from radar.db import session_scope
    from radar.models import IndexNotice

    monkeypatch.setattr(settings, "google_indexing_key", str(tmp_path / "absent.json"))
    with session_scope() as s:
        assert indexing.notify(s) == {"skipped": "no service account key"}
        monkeypatch.setattr(settings, "google_indexing_key", str(_key(tmp_path)))
        monkeypatch.setattr(settings, "indexing_daily", 2)
        now = datetime.utcnow()
        s.add_all([IndexNotice(posting_id=900 + i, url="u", kind="URL_UPDATED", sent_at=now - timedelta(hours=1))
                   for i in range(2)])
        s.flush()
        assert indexing.notify(s, dry_run=True)["would_send"] == 0  # the day's quota is used
