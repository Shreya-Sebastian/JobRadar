from datetime import UTC, datetime

from radar import distance, stats
from radar.alerts import search_filters
from radar.normalize import NL_CITIES


def _row(n, city, remote=False, policy=None):
    now = datetime(2026, 10, 20)
    return stats.Row(id=n, title="Engineer", company="Co", city=city, remote=remote, url=f"https://x/{n}",
                     posted_at=now, first_seen=now, closed_at=None, ats="lever",
                     ex={"remote_policy": policy} if policy else {})


def test_every_job_city_has_a_centre():
    assert set(NL_CITIES) <= set(distance.CENTRES)


def test_straight_line_km_between_cities():
    assert 30 <= distance.km("Amsterdam", "Utrecht") <= 40
    assert distance.km("Utrecht", "Utrecht") == 0
    assert distance.km("Atlantis", "Utrecht") is None and distance.km("Utrecht", None) is None
    assert distance.from_city("Utrecht")["Amsterdam"] == distance.km("Utrecht", "Amsterdam")
    assert distance.from_city("Atlantis") == {}


def test_the_distance_filter_keeps_nearby_jobs_and_remote_ones():
    rows = [_row(1, "Utrecht"), _row(2, "Amsterdam"), _row(3, "Groningen"), _row(4, None, remote=True),
            _row(5, "Groningen", remote=True, policy="hybrid")]  # hybrid: the office has to be near
    assert [r.id for r in stats.Filters(distance_from="Utrecht", distance_km=50).apply(rows)] == [1, 2, 4]
    assert [r.id for r in stats.Filters(distance_from="Utrecht", distance_km=50, distance_remote=False).apply(rows)] == [1, 2]


def test_saved_search_keeps_the_distance_filter():
    f = search_filters("distance_from=Utrecht&distance_km=25&distance_remote=false", datetime.now(UTC))
    assert (f.distance_from, f.distance_km, f.distance_remote) == ("Utrecht", 25, False)
