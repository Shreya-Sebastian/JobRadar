from fastapi.testclient import TestClient

from radar import cache


def test_store_and_seconds_left(fresh_db):
    assert cache.seconds_left("/api/thing?") is None
    cache.store("/api/thing?", {"n": 1}, ttl=100)
    left = cache.seconds_left("/api/thing?")
    assert left is not None and 95 < left <= 100
    assert cache.cached("/api/thing?", lambda: {"n": 2}) == {"n": 1}  # the stored value answers


def test_warm_once_builds_what_pages_ask_for_and_requests_then_hit_it(fresh_db, monkeypatch):
    from radar import api
    from radar.db import new_session

    session = new_session()
    try:
        assert set(api.warm_once(session)) == {"/api/overview?", "/api/filters?", "sitemap.xml"}
        assert api.warm_once(session) == []  # nothing is about to expire
        assert set(api.warm_once(session, force=True)) == {"/api/overview?", "/api/filters?", "sitemap.xml"}
    finally:
        session.close()

    def no_rebuild(*a, **k):
        raise AssertionError("a request rebuilt a warmed response")

    monkeypatch.setattr(api, "_overview_data", no_rebuild)
    monkeypatch.setattr(api, "_filter_data", no_rebuild)
    monkeypatch.setattr(api, "_sitemap_xml", no_rebuild)
    c = TestClient(api.app)
    assert c.get("/api/overview").status_code == 200 and c.get("/api/filters").status_code == 200
    assert c.get("/sitemap.xml").status_code == 200


def test_rows_wait_loads_the_current_version(fresh_db):
    from radar import stats
    from radar.db import new_session

    session = new_session()
    try:
        stats.CACHE.rows(session)
        before = stats.CACHE.loads
        cache.bump_data_version()
        stats.CACHE.rows(session, wait=True)  # loaded now, not in the background
        assert stats.CACHE.loads == before + 1
    finally:
        session.close()
