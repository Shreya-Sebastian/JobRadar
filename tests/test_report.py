from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from radar import stats
from radar.db import session_scope
from radar.models import Posting, Source


def _row(n, days_ago, skills, english_only=False, city="Utrecht", now=datetime(2026, 10, 20, 12)):
    return stats.Row(id=n, title="Data Engineer", company=f"Co{n}", city=city, remote=False, url=f"https://x/{n}",
                     posted_at=now - timedelta(days=days_ago), first_seen=now - timedelta(days=days_ago),
                     closed_at=None, ats="lever", ex={"skills_required": skills, "english_only": english_only})


def test_report_compares_the_last_seven_days_with_the_seven_before():
    now = datetime(2026, 10, 20, 12)
    rows = [_row(1, 1, ["Python"], True), _row(2, 3, ["Python", "SQL"]), _row(3, 9, ["SQL"]), _row(4, 30, ["Go"])]
    rep = stats.market_report(rows, tracking_start=datetime(2026, 9, 26), now=now)
    assert (rep["new"], rep["new_before"], rep["no_dutch"]) == (2, 1, 0.5)
    python = next(s for s in rep["skills"] if s["skill"] == "Python")
    assert python == {"skill": "Python", "share": 1.0, "change": 1.0}  # in both new jobs, in none the week before
    assert rep["positions"] == [{"key": "data_engineer", "count": 2}] and rep["cities"] == [{"key": "Utrecht", "count": 2}]
    # tracking that does not cover the earlier week: no comparison
    assert stats.market_report(rows, tracking_start=now - timedelta(days=10), now=now)["new_before"] is None


def test_report_page_is_public_and_names_no_employer(fresh_db):
    from radar.api import app

    now = datetime.utcnow()
    with session_scope() as s:
        src = Source(company="Acme Secret BV", ats="lever", slug="acme")
        s.add(src)
        s.flush()
        s.add(Posting(source_id=src.id, external_id="1", title="Data Engineer", company="Acme Secret BV",
                      url="https://x/1", content_hash="1", dedup_key="1", first_seen=now, last_seen=now,
                      posted_at=now, city="Utrecht", country="NL", is_tech=True, description="Python"))
    client = TestClient(app)
    for path in ("/market-report", "/nl/marktrapport"):
        r = client.get(path)
        assert r.status_code == 200 and "{{" not in r.text and "Acme" not in r.text
    assert "The Dutch tech job market this week" in client.get("/market-report").text
    assert client.get("/api/market/report").json()["new"] == 1
