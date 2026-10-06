from datetime import datetime

from radar import commute, stats


def _row(n, city, remote=False):
    now = datetime(2026, 10, 20)
    return stats.Row(id=n, title="Engineer", company="Co", city=city, remote=remote, url=f"https://x/{n}",
                     posted_at=now, first_seen=now, closed_at=None, ats="lever", ex={})


def test_train_times_between_job_cities():
    assert commute.minutes("Amsterdam", "Utrecht") == 27  # a real NS time
    assert commute.minutes("Utrecht", "Utrecht") == 15  # within the city
    assert commute.minutes("Bussum", "Naarden") >= 10  # never closer than the floor
    assert commute.minutes("Atlantis", "Utrecht") is None and commute.minutes("Utrecht", None) is None
    assert "Eindhoven" in commute.origins() and commute.from_city("Utrecht")["Amsterdam"] == 27


def test_the_commute_filter_keeps_jobs_within_reach_and_remote_ones():
    rows = [_row(1, "Utrecht"), _row(2, "Amsterdam"), _row(3, "Groningen"), _row(4, None, remote=True)]
    near = stats.Filters(commute_from="Utrecht", commute_max=45).apply(rows)
    assert [r.id for r in near] == [1, 2, 4]
    assert [r.id for r in stats.Filters(commute_from="Utrecht", commute_max=45, commute_remote=False).apply(rows)] == [1, 2]


def test_saved_search_keeps_the_commute_filter():
    from datetime import UTC, datetime

    from radar.alerts import search_filters

    f = search_filters("commute_from=Utrecht&commute_max=45&commute_remote=false", datetime.now(UTC))
    assert (f.commute_from, f.commute_max, f.commute_remote) == ("Utrecht", 45, False)
