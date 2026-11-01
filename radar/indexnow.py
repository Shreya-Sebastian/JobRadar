"""Tell Bing, Yandex, Seznam and Naver about new and closed job pages through IndexNow, so a vacancy appears in their
results (and in DuckDuckGo, Ecosia, Yahoo and ChatGPT search, which use Bing's index) within hours.

IndexNow needs no account: the site publishes a key at /<key>.txt (radar/api.py) and posts the changed URLs with it
to api.indexnow.org, which passes them on to every engine that takes part. Each run sends the job pages first seen or
closed since the previous run, at most URLS_PER_RUN; the first run sends every live job page. The time of the last
successful run is kept in Meta, so nothing is sent twice.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar.config import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
LAST_RUN = "indexnow_last_run"  # Meta key
URLS_PER_RUN = 10000  # the protocol's limit for one request


def changed(session: Session, since: datetime | None) -> list:
    """Tech postings first seen or closed after `since` (every live one when `since` is None), newest first."""
    from radar.models import Posting
    from radar.stats import site_postings

    q = select(Posting).where(Posting.is_tech.is_(True), Posting.duplicate_of.is_(None), site_postings())
    if since is None:
        q = q.where(Posting.closed_at.is_(None))
    else:
        q = q.where(or_(Posting.first_seen > since, Posting.closed_at > since))
    return list(session.scalars(q.order_by(Posting.first_seen.desc()).limit(URLS_PER_RUN)))


def notify(session: Session, client: httpx.Client | None = None, dry_run: bool = False) -> dict:
    from radar.indexing import job_url
    from radar.models import Meta

    key = settings.indexnow_key
    if not key:
        return {"skipped": "no IndexNow key"}
    now = datetime.utcnow()
    row = session.get(Meta, LAST_RUN)
    since = datetime.fromisoformat(row.value) if row else None
    urls = list(dict.fromkeys(job_url(p) for p in changed(session, since)))
    if dry_run or not urls:
        return {"would_send" if dry_run else "sent": len(urls), "since": since and since.isoformat(),
                "urls": urls[:50] if dry_run else []}
    base = settings.site_url.rstrip("/")
    body = {"host": urlparse(base).hostname, "key": key, "keyLocation": f"{base}/{key}.txt", "urlList": urls}
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(ENDPOINT, json=body, headers={"Content-Type": "application/json; charset=utf-8"})
    finally:
        if own:
            client.close()
    # 200 and 202 are both success (202: the key is still being checked); anything else is tried again next run
    if resp.status_code not in (200, 202):
        log.warning("indexnow: %s %s", resp.status_code, resp.text[:200])
        return {"sent": 0, "failed": len(urls), "status": resp.status_code}
    if row is None:
        session.add(Meta(key=LAST_RUN, value=now.isoformat()))
    else:
        row.value = now.isoformat()
    session.flush()
    log.info("indexnow: sent %d URLs", len(urls))
    return {"sent": len(urls), "status": resp.status_code}
