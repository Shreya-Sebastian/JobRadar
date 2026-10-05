from datetime import datetime, timedelta

from radar import stats


def _row(n, company, employees, days_ago):
    return stats.Row(id=n, title="Engineer", company=company, city="Utrecht", remote=False, url=f"https://x/{n}",
                     posted_at=datetime(2026, 10, 1) - timedelta(days=days_ago), first_seen=datetime(2026, 9, 26),
                     closed_at=None, ats="lever", employees=employees)


def test_larger_organisations_first_take_turns():
    rows = [_row(1, "Giant", 300_000, 1), _row(2, "Giant", 300_000, 2), _row(3, "Giant", 300_000, 3),
            _row(4, "Mid", 5_000, 1), _row(5, "Small", None, 0), _row(6, "Mid", 5_000, 4)]
    order = [i["id"] for i in stats.posting_dicts(rows, sort="size_large")["items"]]
    # each organisation's newest job, largest first (unknown size last), then the next round
    assert order == [1, 4, 5, 2, 6, 3]
