from datetime import datetime, timedelta

from radar import stats
from radar.db import session_scope
from radar.models import Posting, Source


def test_weekly_counts_start_at_the_first_full_week_and_compare_like_with_like(fresh_db):
    now = datetime(2026, 10, 7, 12, 0)  # a Wednesday
    start = datetime(2026, 9, 26, 15, 0)  # tracking began on a Saturday
    with session_scope() as s:
        src = Source(company="Acme", ats="lever", slug="acme")
        s.add(src)
        s.flush()
        for n, posted, seen, closed in [
            (1, datetime(2026, 7, 1), start, None),                                 # before tracking: left out
            (2, datetime(2026, 9, 29), start + timedelta(days=3), None),            # the first full week
            (3, datetime(2026, 9, 30, 9), start + timedelta(days=4), now),          # closed since: still counted
            (4, datetime(2026, 10, 6), now, None),                                  # this week
            (5, None, datetime(2026, 10, 7, 9), None),                              # no employer date: day first seen
        ]:
            s.add(Posting(source_id=src.id, external_id=str(n), title="Engineer", company="Acme", url=f"https://x/{n}",
                          content_hash=str(n), dedup_key=str(n), first_seen=seen, last_seen=now, posted_at=posted,
                          closed_at=closed, is_tech=True))
    with session_scope() as s:
        out = stats.weekly_new(s, now=now)
    assert out["since"] == "2026-09-26"
    assert out["weeks"] == [{"week": "2026-09-28", "count": 2, "partial": False},
                            {"week": "2026-10-05", "count": 2, "partial": True}]
    # last week from Monday 00:00 to Wednesday 12:00: jobs 2 (Tuesday) and 3 (Wednesday 09:00)
    assert out["this_week"] == 2 and out["last_week_so_far"] == 2


def test_new_jobs_is_a_rolling_seven_days(fresh_db):
    now = datetime(2026, 10, 20, 9, 0)  # a Tuesday: a calendar week would hold one day
    with session_scope() as s:
        src = Source(company="Acme", ats="lever", slug="acme")
        s.add(src)
        s.flush()
        for n, posted in enumerate([now - timedelta(days=1), now - timedelta(days=6), now - timedelta(days=9)]):
            s.add(Posting(source_id=src.id, external_id=str(n), title="Engineer", company="Acme", url=f"https://x/{n}",
                          content_hash=str(n), dedup_key=str(n), first_seen=datetime(2026, 9, 26), last_seen=now,
                          posted_at=posted, is_tech=True))
    with session_scope() as s:
        assert stats.new_jobs(s, now=now) == {"recent": 2, "before": 1, "days": 7}
        # tracking that began 10 days ago does not cover the earlier window: no comparison
        assert stats.new_jobs(s, now=datetime(2026, 10, 6))["before"] is None
