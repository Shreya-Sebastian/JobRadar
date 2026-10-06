from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from radar import alerts, auth, mailer
from radar.db import session_scope
from radar.models import SavedSearch, User
from tests.test_alerts import _jobs

H = {"X-Requested-With": "radar"}


def _signed_in(client):
    client.post("/api/auth/request", json={"email": "cy@example.org"}, headers=H)
    link = next(w for w in mailer.OUTBOX[-1].get_body(("plain",)).get_content().split() if "token=" in w)
    client.get("/auth/verify?token=" + parse_qs(urlparse(link).query)["token"][0], follow_redirects=False)


def test_saved_searches_can_be_listed_changed_and_removed(fresh_db):
    mailer.OUTBOX.clear()
    auth._ip_hits.clear()
    from radar.api import app

    with TestClient(app) as client:
        assert client.get("/api/me/searches").status_code == 401
        _signed_in(client)
        body = {"name": "Data in Utrecht", "query": "role=data&city=Utrecht&page=3&exclude_ids=1,2", "frequency": "daily"}
        assert client.post("/api/me/searches", json=body).status_code == 403  # header required
        made = client.post("/api/me/searches", json=body, headers=H).json()
        assert made["query"] == "role=data&city=Utrecht"  # the filters only, not the page or opened jobs
        assert client.put(f"/api/me/searches/{made['id']}", json={"frequency": "weekly"}, headers=H).json()["frequency"] == "weekly"
        assert [x["name"] for x in client.get("/api/me/searches").json()["items"]] == ["Data in Utrecht"]
        assert client.get("/api/me/export").json()["saved_searches"][0]["name"] == "Data in Utrecht"
        for i in range(9):
            client.post("/api/me/searches", json={"name": f"s{i}"}, headers=H)
        assert client.post("/api/me/searches", json={"name": "one too many"}, headers=H).status_code == 409
        assert client.delete(f"/api/me/searches/{made['id']}", headers=H).json() == {"ok": True}
        assert client.delete(f"/api/me/searches/{made['id']}", headers=H).status_code == 404


def test_a_saved_search_mails_its_new_jobs_with_its_own_unsubscribe(fresh_db):
    mailer.OUTBOX.clear()
    _jobs()
    with session_scope() as s:
        u = User(email="dee@example.org", lang="en")
        s.add(u)
        s.flush()
        s.add(SavedSearch(user_id=u.id, name="Utrecht", query="city=Utrecht", frequency="daily",
                          sent_at=datetime.utcnow() - timedelta(days=2), created_at=datetime.utcnow()))
    with session_scope() as s:
        assert alerts.run_searches(s) == {"due": 1, "sent": 1, "empty": 0, "failed": 0}
        assert alerts.run_searches(s)["due"] == 0  # not again until tomorrow
    msg = mailer.OUTBOX[-1]
    text = msg.get_body(("plain",)).get_content()
    assert msg["Subject"] == "1 new tech job for “Utrecht”" and "Backend Developer" in text and "Frontend" not in text
    q = parse_qs(urlparse(msg["List-Unsubscribe"].strip("<>")).query)
    from radar.api import app

    client = TestClient(app)
    assert client.get("/alerts/unsubscribe", params={"s": q["s"][0], "t": "wrong"}).status_code == 400
    assert client.post("/alerts/unsubscribe", params={"s": q["s"][0], "t": q["t"][0]}).status_code == 200
    with session_scope() as s:
        assert s.query(SavedSearch).one().frequency == "off"
