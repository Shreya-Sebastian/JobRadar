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


def test_long_running_vacancies_after_the_rest_in_every_order():
    now = datetime.utcnow()

    def row(n, company, employees, days_ago):
        return stats.Row(id=n, title="Engineer", company=company, city="Utrecht", remote=False, url=f"https://x/{n}",
                         posted_at=now - timedelta(days=days_ago), first_seen=now - timedelta(days=days_ago),
                         closed_at=None, ats="lever", employees=employees)
    rows = [row(1, "Giant", 300_000, 476), row(2, "Small", 20, 3), row(3, "Mid", 5_000, 200), row(4, "Mid", 5_000, 10)]
    for sort in ("size_large", "size_small", "newest"):
        order = [i["id"] for i in stats.posting_dicts(rows, sort=sort)["items"]]
        assert set(order[:2]) == {2, 4} and set(order[2:]) == {1, 3}, (sort, order)


def test_newest_first_with_employers_taking_turns_within_a_day():
    """An agency that posts five jobs the same day does not fill the top: that day's employers take turns (larger
    first), and an older day's jobs still come after every job of a newer day."""
    rows = [_row(1, "Agency", None, 0), _row(2, "Agency", None, 0), _row(3, "Agency", None, 0),
            _row(4, "Giant", 300_000, 0), _row(5, "Mid", 5_000, 0), _row(6, "Giant", 300_000, 1)]
    order = [i["id"] for i in stats.posting_dicts(rows, sort="newest")["items"]]
    assert order[:3] == [4, 5, 1] and set(order[3:5]) == {2, 3} and order[5] == 6


def test_a_misread_future_date_does_not_pin_a_job_to_the_top():
    rows = [_row(1, "Agency", None, 0), _row(2, "Giant", 300_000, 0)]
    rows[0].posted_at = datetime(2792, 9, 29)  # read wrongly from the page; the radar first saw it on 26 Sept 2026
    assert [i["id"] for i in stats.posting_dicts(rows, sort="newest")["items"]] == [2, 1]
