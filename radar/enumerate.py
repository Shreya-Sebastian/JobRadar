"""Enumerate every board on an ATS platform, then probe them all for NL postings (and graduate roles abroad).

Company-name guessing finds the boards you already know about. Web-archive URL indexes know about every
board that was ever linked from anywhere, so enumerating them and keeping the ones with NL postings is the
biggest coverage lever available without partnerships.

Two steps:
  enumerate_slugs(ats)        -> query the Wayback CDX index with a regex that keeps board-root URLs only
  probe_slugs(session, ats, slugs) -> fetch every board once, register those with >=1 NL posting
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from radar.adapters import get_adapter
from radar.adapters.base import AdapterError, SourceNotFound
from radar.config import settings
from radar.normalize import detect_country
from radar.registry import _pretty, upsert_source

log = logging.getLogger(__name__)

CDX = "https://web.archive.org/cdx/search/cdx"

# (url pattern, matchType, regex keeping only board roots, slug-capturing regex)
PATTERNS: dict[str, tuple[str, str, str, str]] = {
    "greenhouse": ("boards.greenhouse.io/", "prefix", r"^https?://boards\.greenhouse\.io/[A-Za-z0-9_-]+/?$",
                   r"(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    "lever": ("jobs.lever.co/", "prefix", r"^https?://jobs\.lever\.co/[A-Za-z0-9_-]+/?$",
              r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    "ashby": ("jobs.ashbyhq.com/", "prefix", r"^https?://jobs\.ashbyhq\.com/[A-Za-z0-9_-]+/?$",
              r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"),
    "workable": ("apply.workable.com/", "prefix", r"^https?://apply\.workable\.com/[A-Za-z0-9_-]+/?$",
                 r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    "recruitee": ("recruitee.com", "domain", r"^https?://[a-z0-9-]+\.recruitee\.com/?$",
                  r"https?://([a-z0-9-]+)\.recruitee\.com"),
    "teamtailor": ("teamtailor.com", "domain", r"^https?://[a-z0-9-]+\.teamtailor\.com/?$",
                   r"https?://([a-z0-9-]+)\.teamtailor\.com"),
    "personio": ("jobs.personio.de", "domain", r"^https?://[a-z0-9-]+\.jobs\.personio\.de/?$",
                 r"https?://([a-z0-9-]+)\.jobs\.personio\.de"),
    "smartrecruiters": ("careers.smartrecruiters.com/", "prefix",
                        r"^https?://careers\.smartrecruiters\.com/[A-Za-z0-9_-]+/?$",
                        r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    "workday": ("myworkdayjobs.com", "domain",
                r"^https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+/?$",
                r"https?://([a-z0-9-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"),
}
# further addresses of the same boards: Greenhouse moved boards to job-boards.greenhouse.io in 2024, and its links
# mostly point at single jobs there, so job pages count too (their slug is the board's)
EXTRA_QUERIES: dict[str, list[tuple[str, str, str]]] = {
    "greenhouse": [("job-boards.greenhouse.io/", "prefix",
                    r"^https?://job-boards\.greenhouse\.io/[A-Za-z0-9_-]+(/jobs/[0-9]+)?/?$")],
}
_VENDOR_SLUGS = {"www", "app", "api", "cdn", "static", "assets", "careers-analytics", "help", "support", "blog",
                 "embed", "boards", "job-boards", "status", "docs", "tt", "feed", "images", "attachments"}


def enumerate_slugs(ats: str, timeout: float = 600.0) -> list[str]:
    """Query the Wayback CDX index and return unique board slugs for an ATS."""
    import time

    url_pattern, match_type, keep_rx, _ = PATTERNS[ats]
    texts = []
    with httpx.Client(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
        for url, match, keep in [(url_pattern, match_type, keep_rx), *EXTRA_QUERIES.get(ats, [])]:
            params = {"url": url, "matchType": match, "fl": "original", "collapse": "urlkey",
                      "filter": f"original:{keep}"}
            for attempt in range(3):
                resp = client.get(CDX, params=params)
                if resp.status_code < 500 or attempt == 2:
                    break
                time.sleep(30 * (attempt + 1))  # the Wayback index often times out (504) on a large query
            resp.raise_for_status()
            texts.append(resp.text)
    return slugs_from_text(ats, "\n".join(texts))


def slugs_from_text(ats: str, text: str) -> list[str]:
    slug_rx = re.compile(PATTERNS[ats][3], re.I)
    seen: dict[str, None] = {}
    for line in text.splitlines():
        m = slug_rx.search(line)
        if not m:
            continue
        slug = m.group(1)
        if ats == "workday":
            site = m.group(2)
            if site.lower() in {"wday", "login", "job", "jobs", "search"}:
                continue
            slug = f"{slug.lower()}/{site}"
        if ats in ("recruitee", "teamtailor", "personio"):
            slug = slug.lower()
        if slug.lower() in _VENDOR_SLUGS or len(slug) < 2:
            continue
        seen.setdefault(slug, None)
    return list(seen)


def graduate_titles(postings) -> int:
    """How many postings outside the Netherlands look like tech roles for students and graduates by title alone: a
    board's worth to the entry-level site (RADAR_ENTRY_LEVEL_COUNTRIES), where the crawler then reads each text."""
    from radar.classify import is_tech
    from radar.extract.rules import entry_level_title

    return sum(1 for p in postings if detect_country(p.location, p.country) != "NL"
               and entry_level_title(p.title) and is_tech(p.title))


def _probe_one(ats: str, slug: str, attempts: int = 4, graduates: bool = False) -> dict:
    """One board's NL postings and total; `grad` counts its graduate tech titles abroad (always for boards whose list
    is fetched whole; for Workday only with graduates=True, as it costs a few searches per board)."""
    import time

    client = httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), headers={"User-Agent": settings.user_agent},
                          follow_redirects=True)
    adapter = get_adapter(ats)
    adapter._client = client
    try:
        for attempt in range(attempts):
            try:
                if hasattr(adapter, "count_nl"):
                    # Workday: the country facet on page one gives the NL count without fetching every posting.
                    nl, total = adapter.count_nl(slug)
                    res = {"slug": slug, "status": "ok", "total": total, "nl": nl}
                    if graduates:
                        res["grad"] = adapter.count_graduates(slug)
                    return res
                postings = adapter.fetch(slug)
                break
            except SourceNotFound:
                return {"slug": slug, "status": "not_found"}
            except AdapterError as e:
                msg = str(e)
                if ("429" in msg or "503" in msg) and attempt < attempts - 1:
                    time.sleep(3 * (attempt + 1))  # rate limited: back off and retry
                    continue
                return {"slug": slug, "status": "error", "error": msg[:200]}
            except Exception as e:
                return {"slug": slug, "status": "exception", "error": f"{type(e).__name__}"[:100]}
    finally:
        client.close()
    nl = sum(1 for p in postings if detect_country(p.location, p.country) == "NL")
    return {"slug": slug, "status": "ok", "total": len(postings), "nl": nl, "grad": graduate_titles(postings)}


def _state_path(ats: str) -> Path:
    return Path(settings.data_dir) / "enumerated" / f"probed_{ats}.json"


def _load_state(ats: str) -> dict[str, dict]:
    p = _state_path(ats)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_state(ats: str, state: dict[str, dict]) -> None:
    import time

    p = _state_path(ats)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(p)
            return
        except PermissionError:
            # Windows refuses to replace a file another process has open for reading (e.g. a progress
            # report). Wait and retry; fall back to an in-place write rather than losing an hour of probing.
            time.sleep(0.5 * (attempt + 1))
    p.write_text(json.dumps(state), encoding="utf-8")


def probe_slugs(ats: str, slugs: list[str], workers: int = 12, graduates: bool = False, min_total: int = 10) -> dict:
    """Fetch every board once and remember the result in data/enumerated/probed_{ats}.json.
    Deliberately touches no database, so several platforms can be probed in parallel processes;
    `register_probed` turns the state file into sources afterwards. graduates=True also re-probes live boards of at
    least min_total jobs that have no graduate count yet (probed before the entry-level site), for their `grad`."""
    state = _load_state(ats)
    # Re-probe transient failures (rate limits, timeouts); skip boards that answered definitively.
    todo = [s for s in slugs if s not in state or state[s].get("status") in ("error", "exception")
            or (graduates and state[s].get("status") == "ok" and "grad" not in state[s]
                and state[s].get("total", 0) >= min_total)]
    log.info("%s: %d slugs, %d already probed, %d to probe", ats, len(slugs), len(slugs) - len(todo), len(todo))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_probe_one, ats, s, graduates=graduates): s for s in todo}
        for fut in as_completed(futures):
            res = fut.result()
            state[res["slug"]] = res
            done += 1
            if res["status"] == "ok" and (res.get("nl", 0) > 0 or res.get("grad", 0) > 0):
                log.info("%s/%s: nl=%d grad=%d total=%d", ats, res["slug"], res["nl"], res.get("grad", 0),
                         res["total"])
            if done % 100 == 0:
                _save_state(ats, state)
                with_nl = sum(1 for v in state.values() if v.get("nl", 0) > 0)
                log.info("%s: %d/%d probed, %d boards with NL postings so far", ats, done, len(todo), with_nl)
    _save_state(ats, state)
    return summarize_state(ats, state) | {"probed_now": done}


def summarize_state(ats: str, state: dict[str, dict] | None = None) -> dict:
    state = state if state is not None else _load_state(ats)
    return {
        "ats": ats,
        "probed": len(state),
        "boards_alive": sum(1 for v in state.values() if v["status"] == "ok"),
        "boards_with_nl": sum(1 for v in state.values() if v.get("nl", 0) > 0),
        "nl_postings_seen": sum(v.get("nl", 0) for v in state.values()),
        "boards_with_grad": sum(1 for v in state.values() if v.get("grad", 0) > 0),
        "grad_titles_seen": sum(v.get("grad", 0) for v in state.values()),
    }


def register_probed(session: Session, ats: str, min_nl: int = 1, min_grad: int | None = None) -> int:
    """Create sources for every probed board with at least min_nl NL postings, or (min_grad set) at least min_grad
    graduate tech titles abroad for the entry-level site. Idempotent."""
    added = 0
    for slug, res in _load_state(ats).items():
        if res.get("status") != "ok":
            continue
        if res.get("nl", 0) >= min_nl:
            by = "enumerate"
        elif min_grad is not None and res.get("grad", 0) >= min_grad:
            by = "enumerate-grad"
        else:
            continue
        _, new = upsert_source(session, _pretty(slug), ats, slug, discovered_by=by)
        added += int(new)
    return added
