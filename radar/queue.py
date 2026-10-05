"""Job queue on Redis (RQ). One job per source, so workers scale horizontally and a slow board never
blocks the others. `radar schedule` enqueues, `radar worker` consumes, `radar finalize` post-processes.

Crawls go to one of two queues. Reading a career sitemap page by page (jsonld) takes seconds to fifteen minutes and
is 98% of crawl time; an ATS API answers in a few seconds. On one queue the quick boards waited hours behind the
sitemaps, so sitemaps go to `crawl` and everything else to `crawl-light`, which a second small worker serves."""

from __future__ import annotations

from functools import lru_cache

from radar.config import settings

CRAWL_QUEUE = "crawl"  # sitemap crawls, page by page
LIGHT_QUEUE = "crawl-light"  # ATS APIs
MAINT_QUEUE = "maintenance"
HEAVY_ATS = {"jsonld", "avature"}  # page by page (Avature job pages are large)


def crawl_queue_for(ats: str | None) -> str:
    """The quick queue for a known ATS; sitemaps, and a source whose platform is unknown, go to the main queue
    (served by the main worker, which also takes quick jobs)."""
    return LIGHT_QUEUE if ats and ats not in HEAVY_ATS else CRAWL_QUEUE


@lru_cache(maxsize=1)
def get_redis():
    import redis

    if not settings.redis_url:
        raise RuntimeError("RADAR_REDIS_URL is not set; the queue needs Redis")
    return redis.Redis.from_url(settings.redis_url)


def get_queue(name: str = CRAWL_QUEUE):
    from rq import Queue

    return Queue(name, connection=get_redis(), default_timeout=1800)


def _ats_of(source_id: int) -> str | None:
    from radar.db import session_scope
    from radar.models import Source

    try:
        with session_scope() as s:
            src = s.get(Source, source_id)
            return src.ats if src else None
    except Exception:
        return None  # no database at hand: the main queue takes it


def enqueue_crawl(source_id: int, ats: str | None = None):
    """Enqueue a crawl for one source unless an identical job is already queued or running. `ats` picks the
    queue (looked up when not given)."""
    from rq.job import Job

    q = get_queue(crawl_queue_for(ats if ats is not None else _ats_of(source_id)))
    job_id = f"crawl-{source_id}"
    try:
        existing = Job.fetch(job_id, connection=q.connection)
        status = existing.get_status()
        if status == "scheduled" and _older_than(existing, hours=1):
            existing.delete()  # a retry that never ran (no scheduler at the time): replace it with a fresh job
        elif status in ("queued", "started", "deferred", "scheduled"):
            return None
    except Exception:
        pass
    return q.enqueue("radar.tasks.crawl_source", source_id, job_id=job_id, result_ttl=3600, failure_ttl=86400,
                     retry=_retry())


def _older_than(job, hours: float) -> bool:
    from datetime import UTC, datetime, timedelta

    at = job.ended_at or job.enqueued_at or job.created_at
    if at is None:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return datetime.now(UTC) - at > timedelta(hours=hours)


def _retry():
    from rq import Retry

    return Retry(max=2, interval=[60, 300])


def queue_depths() -> dict[str, int]:
    out = {}
    for name in (CRAWL_QUEUE, LIGHT_QUEUE, MAINT_QUEUE):
        try:
            out[name] = get_queue(name).count
        except Exception:
            out[name] = -1
    return out
