"""Tell Google about new and closed job pages through its Indexing API, so a vacancy shows up in Google's job search
within hours of being found and drops out once it closes.

Google allows the Indexing API for pages with JobPosting structured data (every live /job/ page has it). The site
signs in as a service account (key file mounted from the radar-google-indexing secret; the account is an owner of
the Search Console property) and sends URL_UPDATED for each new live tech posting and URL_DELETED for a posting it
announced that has since closed (its page then carries noindex). The default quota is 200 notifications a day: each
run sends a few, closed postings first, then the newest new ones, and never more than INDEXING_DAILY in 24 hours.
Every notification is recorded in index_notices, so nothing is sent twice.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.config import settings

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/indexing"
PUBLISH = "https://indexing.googleapis.com/v3/urlNotifications:publish"
_token_cache: dict[str, tuple[str, float]] = {}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def access_token(key: dict, client: httpx.Client) -> str:
    """An OAuth access token for the service account: a signed JWT exchanged at Google's token endpoint."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    cached = _token_cache.get(key["client_email"])
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": key.get("private_key_id", "")}
    claims = {"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"], "iat": now, "exp": now + 3600}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}".encode()
    private_key = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    resp = client.post(key["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                "assertion": f"{signing_input.decode()}.{_b64(signature)}"})
    resp.raise_for_status()
    body = resp.json()
    _token_cache[key["client_email"]] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
    return body["access_token"]


def job_url(posting) -> str:
    from radar.pages import job_path

    lang = "nl" if (posting.extraction or {}).get("posting_language") == "nl" else "en"
    return settings.site_url.rstrip("/") + job_path(posting.id, posting.title, lang)


def pending(session: Session, budget: int) -> list[tuple[str, object]]:
    """What to send now, within `budget`: (kind, posting). Closed postings that were announced come first."""
    from radar.models import IndexNotice, Posting
    from radar.stats import site_postings

    out: list[tuple[str, object]] = []
    gone = session.execute(
        select(Posting).join(IndexNotice, IndexNotice.posting_id == Posting.id)
        .where(IndexNotice.kind == "URL_UPDATED",
               (Posting.closed_at.is_not(None)) | (Posting.duplicate_of.is_not(None)) | (Posting.is_tech.is_(False)))
        .order_by(IndexNotice.sent_at).limit(budget)
    ).scalars().all()
    out += [("URL_DELETED", p) for p in gone]
    if len(out) < budget:
        fresh = session.execute(
            select(Posting).outerjoin(IndexNotice, IndexNotice.posting_id == Posting.id)
            .where(IndexNotice.posting_id.is_(None), Posting.closed_at.is_(None), Posting.duplicate_of.is_(None),
                   Posting.is_tech.is_(True), site_postings())  # this site's jobs only: none abroad on the Dutch site
            .order_by(Posting.first_seen.desc()).limit(budget - len(out))
        ).scalars().all()
        out += [("URL_UPDATED", p) for p in fresh]
    return out


def notify(session: Session, client: httpx.Client | None = None, dry_run: bool = False) -> dict:
    """Send this run's notifications. Returns counts, or why nothing was sent."""
    from radar.models import IndexNotice

    key_path = settings.google_indexing_key
    if not key_path or not Path(key_path).exists():
        return {"skipped": "no service account key"}
    now = datetime.utcnow()
    used = session.scalar(select(func.count()).select_from(IndexNotice)
                          .where(IndexNotice.sent_at >= now - timedelta(hours=24))) or 0
    budget = max(0, min(settings.indexing_per_run, settings.indexing_daily - used))
    todo = pending(session, budget)
    if dry_run or not todo:
        return {"would_send" if dry_run else "sent": len(todo), "used_24h": used,
                "urls": [(kind, job_url(p)) for kind, p in todo][:50] if dry_run else []}
    key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    own = client is None
    client = client or httpx.Client(timeout=30)
    sent = failed = 0
    try:
        token = access_token(key, client)
        for kind, p in todo:
            url = job_url(p)
            resp = client.post(PUBLISH, json={"url": url, "type": kind}, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 429:
                log.warning("indexing quota reached after %d notifications", sent)
                break
            if resp.status_code >= 400:
                failed += 1
                log.warning("indexing %s %s: %s %s", kind, url, resp.status_code, resp.text[:200])
                if resp.status_code in (401, 403):
                    break  # the account is not an owner of the property, or the API is off: no point continuing
                continue
            row = session.get(IndexNotice, p.id)
            if row is None:
                session.add(IndexNotice(posting_id=p.id, url=url[:1000], kind=kind, sent_at=datetime.utcnow()))
            else:
                row.kind, row.url, row.sent_at = kind, url[:1000], datetime.utcnow()
            sent += 1
        session.flush()
    finally:
        if own:
            client.close()
    log.info("indexing: sent %d, failed %d, %d in the last 24 h before this run", sent, failed, used)
    return {"sent": sent, "failed": failed, "used_24h": used + sent}
