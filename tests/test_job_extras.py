from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from radar.db import session_scope
from radar.models import Posting, Source


def test_job_page_shows_how_long_it_is_open_reposts_and_similar_jobs(fresh_db):
    from radar.api import app

    now = datetime.utcnow()
    with session_scope() as s:
        acme, beta = Source(company="Acme", ats="lever", slug="acme"), Source(company="Beta", ats="lever", slug="beta")
        s.add_all([acme, beta])
        s.flush()

        def job(n, src, company, title, posted, first_seen, closed=None, city="Utrecht"):
            s.add(Posting(source_id=src.id, external_id=str(n), title=title, company=company, url=f"https://x/{n}",
                          content_hash=str(n), dedup_key=str(n), first_seen=first_seen, last_seen=now,
                          posted_at=posted, closed_at=closed, city=city, country="NL", is_tech=True,
                          description="Build data pipelines in Python and SQL."))

        job(1, acme, "Acme", "Data Engineer (m/v/x)", now - timedelta(days=60), now - timedelta(days=60),
            closed=now - timedelta(days=30))                                         # the earlier listing
        job(2, acme, "Acme", "Data Engineer", now - timedelta(days=12), now - timedelta(days=12))  # this one
        job(3, beta, "Beta", "Senior Data Engineer", now - timedelta(days=2), now - timedelta(days=2))
    r = TestClient(app).get("/job/2/data-engineer")
    assert r.status_code == 200
    assert "Open for 12 days" in r.text and "posted before: 1×" in r.text
    assert "Similar jobs" in r.text and "/job/3/senior-data-engineer" in r.text
