"""Queue round trip on an in-memory Redis: schedule -> worker (burst) -> finalize."""

import fakeredis
import httpx
import respx
from rq import SimpleWorker

from radar import queue as q
from radar.db import session_scope
from radar.models import Posting, Source
from radar.tasks import finalize, schedule


@respx.mock
def test_schedule_worker_finalize_round_trip(fresh_db, monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(q, "get_redis", lambda: fake)
    monkeypatch.setattr("radar.config.settings.redis_url", "redis://fake")
    respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=[
        {"id": "x1", "text": "Platform Engineer", "hostedUrl": "https://jobs.lever.co/acme/x1",
         "categories": {"location": "Amsterdam"}, "country": "NL", "descriptionPlain": "Kubernetes and Terraform.",
         "createdAt": 1700000000000}]))
    with session_scope() as s:
        s.add(Source(company="Acme", ats="lever", slug="acme"))

    out = schedule()
    assert out == {"due": 1, "queued": 1}
    assert q.get_queue(q.LIGHT_QUEUE).count == 1  # an ATS API board: the quick queue
    assert schedule()["queued"] == 0  # same job already queued: not duplicated

    worker = SimpleWorker([q.get_queue(q.LIGHT_QUEUE)], connection=fake)
    worker.work(burst=True, with_scheduler=False)
    with session_scope() as s:
        p = s.query(Posting).one()
        assert p.title == "Platform Engineer" and p.extraction["skills_required"] == ["Kubernetes", "Terraform"]
        assert s.query(Source).one().last_status == "ok"

    result = finalize()
    assert result["live"] == 1 and result["tech"] == 1
    assert fake.get("radar:data_version") is not None


ORDER: list[str] = []


def _note(name):
    ORDER.append(name)


def test_the_worker_takes_turns_between_queues():
    # with strict priority the quick ATS boards (crawl-light) waited for ever behind a crawl queue that never emptied
    import fakeredis
    from rq import Queue, SimpleWorker

    conn = fakeredis.FakeStrictRedis()
    crawl, light = Queue("crawl", connection=conn), Queue("crawl-light", connection=conn)
    for i in range(3):
        crawl.enqueue(_note, f"sitemap{i}")
        light.enqueue(_note, f"board{i}")
    ORDER.clear()
    SimpleWorker([crawl, light], connection=conn).work(burst=True, dequeue_strategy="round_robin")
    assert ORDER[:4] == ["sitemap0", "board0", "sitemap1", "board1"]


def test_the_light_lane_serves_the_quick_boards_on_its_own():
    import time

    import fakeredis
    from rq import Queue

    from radar.cli import start_light_lane

    conn = fakeredis.FakeStrictRedis()
    light = Queue("crawl-light", connection=conn)
    ORDER.clear()
    for i in range(3):
        light.enqueue(_note, f"board{i}")
    start_light_lane(light, conn)
    for _ in range(100):
        if len(ORDER) == 3:
            break
        time.sleep(0.05)
    assert ORDER == ["board0", "board1", "board2"]
