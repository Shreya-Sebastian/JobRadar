from __future__ import annotations

import logging
import mimetypes
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from radar import auth, feedback, stats
from radar.cache import cached
from radar.config import settings
from radar.db import get_engine, init_db, new_session
from radar.metrics import API_REQUESTS, API_SECONDS, QUEUE_DEPTH
from radar.models import CrawlRun, Source
from radar.pages import brand_html

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger(__name__)
mimetypes.add_type("font/woff2", ".woff2")  # served from /static/fonts; Python's table does not know it everywhere


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.warm_caches:
        threading.Thread(target=_keep_warm, name="warm-caches", daemon=True).start()
    yield
    from radar import analytics

    analytics.flush()  # page views still in the buffer at shutdown


app = FastAPI(title=settings.site_name, version="0.2.0", lifespan=lifespan, description=settings.site_tagline)
app.include_router(auth.router)
app.include_router(feedback.router)
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["X-Robots-Tag"] = "noindex"  # fetched to render pages, never a search result itself
    if response.headers.get("content-type", "").startswith("text/html") and "cache-control" not in response.headers:
        # pages name the current app.js/app.css versions: a browser must check for a new page, or it keeps running
        # the previous release's script after a deploy
        response.headers["Cache-Control"] = "no-cache"
    try:
        from radar import analytics

        moved = response.headers.get("location") if 300 <= response.status_code < 400 else None
        analytics.record(request, response.status_code, (time.perf_counter() - t0) * 1000,
                         int(response.headers.get("content-length") or 0), detail=moved)
    except Exception:  # statistics must never break a response
        pass
    if path.startswith("/api/") or path in ("/", "/healthz", "/readyz"):
        # collapse dynamic segments so label cardinality stays small
        label = "/api/breakdown/*" if path.startswith("/api/breakdown/") else path
        API_REQUESTS.labels(label, str(response.status_code)).inc()
        API_SECONDS.labels(label).observe(time.perf_counter() - t0)
    return response


@app.middleware("http")
async def _throttle_middleware(request: Request, call_next):
    # added after the metrics middleware, so it runs first and refused requests cost nothing further
    from starlette.concurrency import run_in_threadpool

    from radar import analytics, throttle

    refused = await run_in_threadpool(throttle.check, request.url.path, analytics.client_ip(request),
                                      request.headers.get("user-agent", ""))
    if refused:
        status, reason = refused
        return Response(reason + "\n", status_code=status, media_type="text/plain",
                        headers={"Retry-After": "60"} if status == 429 else None)
    return await call_next(request)


_NOT_FOUND = {
    "en": ("Page not found", "This page does not exist, or the vacancy has been taken down.", "/", "Back to the jobs"),
    "nl": ("Pagina niet gevonden", "Deze pagina bestaat niet, of de vacature is offline gehaald.", "/nl/",
           "Terug naar de vacatures"),
}


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    from fastapi.exception_handlers import http_exception_handler

    path = request.url.path
    wants_page = "text/html" in request.headers.get("accept", "")
    if exc.status_code not in (404, 410) or path.startswith("/api/") or not wants_page:
        return await http_exception_handler(request, exc)  # API clients keep the JSON error
    from html import escape

    lang = "nl" if path.startswith("/nl/") or path == "/nl" else "en"
    title, text, home, back = _NOT_FOUND[lang]
    page = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f'<title>{escape(title)} · {escape(settings.site_name)}</title>'
            '<link rel="stylesheet" href="/static/app.css?v=50"></head><body>'
            f'<main class="wrap" style="padding-top:4rem;padding-bottom:4rem"><h1>{escape(title)}</h1>'
            f'<p class="muted">{escape(text)}</p>'
            f'<p><a class="btn primary" href="{home}">{escape(back)}</a></p></main></body></html>')
    return Response(page, status_code=exc.status_code, media_type="text/html")


def db() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
    finally:
        s.close()


def filters(
    role: str | None = None,
    seniority: str | None = None,
    city: str | None = None,
    company: str | None = None,
    english_only: bool | None = None,
    sponsorship: bool | None = None,
    remote: str | None = None,
    days: int | None = Query(None, ge=1, le=365),
    q: str | None = None,
    skill: str | None = None,
    include_closed: bool = False,
    exclude_agencies: bool = False,
    exclude_companies: str | None = None,
    since: str | None = None,
    skills_any: str | None = None,
    ids: str | None = None,
    language: str | None = None,
    experience: str | None = None,
    enrollment: str | None = None,
    org_size: str | None = None,
    confirmed_days: int | None = Query(None, ge=1, le=90),
    degree: str | None = None,
    employees: str | None = None,
    sector: str | None = None,
    position: str | None = None,
    exclude_ids: str | None = Query(None, max_length=6000),
    distance_from: str | None = Query(None, max_length=60),
    distance_km: int | None = Query(None, ge=1, le=300),
    distance_remote: bool = True,
) -> stats.Filters:
    return stats.Filters(role, seniority, city, company, english_only, sponsorship, remote, days, q, skill,
                         include_closed, exclude_agencies, exclude_companies, since, skills_any, ids, language,
                         experience, enrollment, org_size, confirmed_days, degree, employees, sector, position,
                         exclude_ids, distance_from, distance_km, distance_remote)


def _rows(session: Session, f: stats.Filters) -> list[stats.Row]:
    return f.apply(stats.CACHE.rows(session))


def _key(request: Request) -> str:
    return f"{request.url.path}?{request.url.query}"


def _overview_data(session: Session) -> dict:
    return {**stats.overview(session), "new": stats.new_jobs(session)}  # the "new in the last 7 days" figure


OVERVIEW_TTL = 120  # it names the last crawl, which moves without a data change


@app.get("/api/overview")
def overview(request: Request, session: Session = Depends(db)):
    return cached(_key(request), lambda: _overview_data(session), ttl=OVERVIEW_TTL)


@app.get("/api/skills")
def skills(request: Request, top: int = Query(40, le=200), f: stats.Filters = Depends(filters),
           session: Session = Depends(db)):
    def compute():
        rows = _rows(session, f)
        return {"n": len(rows), "skills": stats.skill_counts(rows, top)}

    return cached(_key(request), compute)


@app.get("/api/cooccurrence")
def cooccurrence(request: Request, top: int = Query(30, le=80), f: stats.Filters = Depends(filters),
                 session: Session = Depends(db)):
    def compute():
        rows = _rows(session, f)
        return {"n": len(rows), **stats.cooccurrence(rows, top)}

    return cached(_key(request), compute)


@app.get("/api/breakdown/{key}")
def breakdown(request: Request, key: str, top: int = Query(20, le=100), f: stats.Filters = Depends(filters),
              session: Session = Depends(db)):
    allowed = {"city", "company", "ats", "seniority", "role_family", "remote_policy", "degree_required",
               "posting_language", "experience", "org_size", "degree", "employees", "sector", "position"}
    if key not in allowed:
        raise HTTPException(400, f"key must be one of {sorted(allowed)}")

    def compute():
        rows = _rows(session, f)
        return {"n": len(rows), "items": stats.breakdown(rows, key, top)}

    return cached(_key(request), compute)


@app.get("/api/trends")
def trends(request: Request, skills: str | None = None, weeks: int = Query(12, le=52),
           f: stats.Filters = Depends(filters), session: Session = Depends(db)):
    def compute():
        f.include_closed = True
        rows = _rows(session, f)
        wanted = [s.strip() for s in skills.split(",")] if skills else None
        return stats.trends(rows, wanted, weeks)

    return cached(_key(request), compute)


@app.get("/api/salary")
def salary(request: Request, f: stats.Filters = Depends(filters), session: Session = Depends(db)):
    return cached(_key(request), lambda: stats.salary(_rows(session, f)))


@app.get("/api/postings")
def postings(request: Request, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
             sort: str = "newest", skills_have: str | None = None, f: stats.Filters = Depends(filters),
             session: Session = Depends(db)):
    from radar import logos
    from radar.taxonomy import canonicalise

    have = set(canonicalise((skills_have or "").split(",")))
    out = cached(_key(request), lambda: stats.posting_dicts(_rows(session, f), page, size, sort, have))
    marks = logos.known(session)
    return {**out, "items": [{**i, "logo": (marks.get(i["company"]) or {}).get("slug"),
                              "logo_dark": (marks.get(i["company"]) or {}).get("dark", False),
                              "logo_v": (marks.get(i["company"]) or {}).get("v")} for i in out["items"]]}


@app.get("/api/distance")
def city_distances(origin: str | None = Query(None, alias="from", max_length=60)):
    """Kilometres from one job city to every other (radar/distance.py); without `from`, the cities."""
    from radar import distance

    if not origin:
        return {"origins": distance.origins()}
    km = distance.from_city(origin)
    if not km:
        raise HTTPException(404, "unknown city")
    return {"from": origin, "km": km}


@app.get("/logo/{slug}", include_in_schema=False)
def logo(slug: str, session: Session = Depends(db)):
    """An employer's logo, stored by radar/logos.py: served from this site, so visitors contact no one else."""
    from sqlalchemy import select

    from radar.models import CompanyLogo

    row = session.scalars(select(CompanyLogo).where(CompanyLogo.slug == slug, CompanyLogo.status == "ok")).first()
    if row is None or not row.data:
        raise HTTPException(404, "no logo")
    headers = {"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff",
               # an SVG opened on its own must not run anything
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}
    return Response(row.data, media_type=row.content_type, headers=headers)


def _market_report(session: Session) -> dict:
    from sqlalchemy import func, select

    from radar.models import Posting

    start = session.scalar(select(func.min(Posting.first_seen)))
    return cached("market:report", lambda: stats.market_report(stats.CACHE.rows(session), start), ttl=900)


@app.get("/api/market/report")
def market_report(session: Session = Depends(db)):
    """This week in the Dutch tech job market (the Market tab and /market-report)."""
    return _market_report(session)


@app.get("/market-report", include_in_schema=False)
@app.get("/nl/marktrapport", include_in_schema=False)
def market_report_page(request: Request, session: Session = Depends(db)):
    from radar import pages

    lang = "nl" if request.url.path.startswith("/nl/") else "en"
    return Response(pages.render_report(_market_report(session), lang), media_type="text/html")


@app.get("/feed.xml", include_in_schema=False)
@app.get("/nl/feed.xml", include_in_schema=False)
def feed(request: Request, f: stats.Filters = Depends(filters), session: Session = Depends(db)):
    """The newest 50 jobs of a search as RSS (radar/feeds.py)."""
    from radar import feeds

    lang = "nl" if request.url.path.startswith("/nl/") else "en"
    params = {k: v for k, v in request.query_params.items() if v}

    def build():
        items = stats.posting_dicts(_rows(session, f), 1, 50, "newest", set())["items"]
        return feeds.render(items, params, lang)

    return Response(cached(_key(request), build, ttl=900), media_type="application/rss+xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=900"})


@app.get("/api/skills/canonical")
def canonical_skills(names: str):
    """Map typed skill names to the tracked canonical names: LLM -> LLMs, ml -> Machine Learning, k8s -> Kubernetes."""
    from radar.taxonomy import canonical_skill

    return {n.strip(): canonical_skill(n) for n in names.split(",") if n.strip()}


@app.get("/api/coverage")
def coverage(request: Request, session: Session = Depends(db)):
    """Top-employer tracker and source counts by kind, for the Coverage tab."""
    from collections import Counter

    from radar.recall import published
    from radar.tracker import evaluate

    def compute():
        entries = evaluate(session)
        by_kind = Counter()
        healthy = Counter()
        for s in session.scalars(select(Source)):
            by_kind[s.kind or "employer"] += 1
            if s.last_status == "ok":
                healthy[s.kind or "employer"] += 1
        return {
            "tracker": [{"name": e.name, "group": e.group, "status": e.status, "platform": e.platform,
                         "live_postings": e.live_postings} for e in entries],
            "summary": {k: sum(1 for e in entries if e.status == k) for k in ("covered", "registered", "missing")},
            "sources_by_kind": dict(by_kind),
            "healthy_by_kind": dict(healthy),
            "recall": published(session),
        }

    return cached(_key(request), compute, ttl=600)


def _filter_data(session: Session) -> dict:
    rows = [r for r in stats.CACHE.rows(session) if r.closed_at is None]
    return {
        "cities": [d["key"] for d in stats.breakdown(rows, "city", 40)],
        "companies": [d["key"] for d in stats.breakdown(rows, "company", 300)],
        "roles": [d["key"] for d in stats.breakdown(rows, "role_family", 20)],
        "seniorities": [d["key"] for d in stats.breakdown(rows, "seniority", 10)],
        "sectors": [d["key"] for d in stats.breakdown(rows, "sector", 30)],
        "positions": [d["key"] for d in stats.breakdown(rows, "position", 100)],
        "skills": [d["skill"] for d in stats.skill_counts(rows, 120)],
    }


@app.get("/api/filters")
def filter_options(request: Request, session: Session = Depends(db)):
    return cached(_key(request), lambda: _filter_data(session))


@app.get("/api/sources")
def sources(session: Session = Depends(db)):
    items = []
    for s in session.scalars(select(Source).order_by(Source.ats, Source.company)):
        items.append({
            "id": s.id, "company": s.company, "ats": s.ats, "slug": s.slug, "active": s.active,
            "status": s.last_status, "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "total": s.last_count, "nl": s.last_nl_count, "failures": s.consecutive_failures,
            "discovered_by": s.discovered_by, "kind": s.kind, "note": s.last_error,
        })
    runs = [
        {"id": r.id, "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat()
         if r.finished_at else None, "sources_ok": r.sources_ok, "sources_failed": r.sources_failed,
         "seen": r.postings_seen, "new": r.postings_new, "closed": r.postings_closed}
        for r in session.scalars(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(20))
    ]
    return {"sources": items, "runs": runs}


class GapRequest(BaseModel):
    cv_text: str | None = Field(default=None, max_length=50_000)
    skills: list[str] | None = Field(default=None, max_length=200)
    role: str | None = None
    seniority: str | None = None
    city: str | None = None
    english_only: bool | None = None
    language: str | None = None
    exclude_agencies: bool = False
    days: int | None = 90


@app.post("/api/gap")
def gap(req: GapRequest, session: Session = Depends(db)):
    if not (req.cv_text and len(req.cv_text.strip()) >= 20) and not req.skills:
        raise HTTPException(422, "provide cv_text (20+ characters) or a skills list")
    f = stats.Filters(role=req.role, seniority=req.seniority, city=req.city, english_only=req.english_only,
                      days=req.days, exclude_agencies=req.exclude_agencies, language=req.language)
    rows = _rows(session, f)
    return stats.gap_analysis(rows, req.cv_text, req.skills)


@app.get("/api/version")
def version(session: Session = Depends(db)):
    """Cheap freshness check for the page: it polls this and offers a reload when a crawl has finished."""
    from radar.cache import data_version

    last = session.scalar(select(CrawlRun.finished_at).where(CrawlRun.finished_at.is_not(None))
                          .order_by(CrawlRun.finished_at.desc()).limit(1))
    return {"version": data_version(), "last_crawl_at": last.isoformat() if last else None}


class AdminCrawl(BaseModel):
    scope: str = "due"  # due | all | company
    company: str | None = None
    force: bool = False


def _admin(request: Request) -> None:
    from radar import admin

    if not admin.enabled():
        raise HTTPException(404, "not found")
    if not admin.authorised(request.headers.get("authorization")):
        raise HTTPException(401, "admin token required")


@app.post("/api/admin/crawl", include_in_schema=False)
def admin_crawl(body: AdminCrawl, request: Request):
    from fastapi.responses import JSONResponse

    from radar import admin

    _admin(request)
    code, payload = admin.trigger(body.scope, body.company, body.force)
    return JSONResponse(payload, status_code=code)


class Beacon(BaseModel):
    e: str = Field(max_length=30)
    d: str | None = Field(default=None, max_length=200)
    p: str | None = Field(default=None, max_length=300)


@app.post("/api/e", include_in_schema=False, status_code=204)
def beacon(body: Beacon, request: Request):
    """Events the server cannot see itself: tab switches inside the page, clicks through to a job, saves."""
    from radar import analytics

    if body.e in analytics.EVENTS:
        analytics.record(request, 204, 0, 0, event=body.e, detail=body.d, path=(body.p or "/")[:300])
    return Response(status_code=204)


@app.get("/api/admin/analytics", include_in_schema=False)
def admin_analytics(request: Request, who: str = "humans", bots: bool = False, recent: int = 500,
                    session: Session = Depends(db)):
    from radar import analytics

    _admin(request)
    who = "all" if bots and who == "humans" else who  # ?bots=true: the older switch
    return {**analytics.dashboard(session, who=who, recent=min(max(recent, 0), 5000)),
            "this_browser_excluded": analytics.excluded(request)}


@app.post("/api/admin/analytics/exclude", include_in_schema=False)
def admin_analytics_exclude(request: Request, session: Session = Depends(db)):
    """Stop counting this browser (a cookie in the admin's own browser only) and forget its visits of today."""
    from radar import analytics

    _admin(request)
    forgotten = analytics.forget_today(session, analytics.request_visitor(request))
    resp = Response(content=f'{{"excluded": true, "forgotten": {forgotten}}}', media_type="application/json")
    local = request.url.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
    resp.set_cookie(analytics.IGNORE_COOKIE, "1", max_age=400 * 86400, httponly=True, samesite="lax",
                    secure=not local, path="/")
    return resp


@app.delete("/api/admin/analytics/exclude", include_in_schema=False)
def admin_analytics_include(request: Request):
    from radar import analytics

    _admin(request)
    resp = Response(content='{"excluded": false}', media_type="application/json")
    resp.delete_cookie(analytics.IGNORE_COOKIE, path="/")
    return resp


@app.get("/api/admin/status", include_in_schema=False)
def admin_status(request: Request):
    from radar import admin

    _admin(request)
    return admin.status()


@app.get("/api/admin/review/candidates", include_in_schema=False)
def admin_review_candidates(request: Request, since: str | None = None, limit: int = Query(100, ge=1, le=1000),
                            after_id: int = Query(0, ge=0), include_reviewed: bool = False,
                            session: Session = Depends(db)):
    """The open postings the rules are unsure about, for the review script on the owner's laptop (radar/review.py)."""
    from datetime import datetime

    from radar import review

    _admin(request)
    try:
        cutoff = datetime.fromisoformat(since.replace("Z", "")) if since else None
    except ValueError as e:
        raise HTTPException(422, "since must be an ISO date or time") from e
    return review.candidates(session, cutoff, limit, after_id, include_reviewed)


class ReviewDecisionIn(BaseModel):
    posting_id: int
    field: str = Field(max_length=30)
    # checked per decision in review.record, so one bad answer does not refuse the rest of the batch
    value: Any = None
    confidence: Any = None
    reason: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=60)


class ReviewDecisionsIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=60)
    decisions: list[ReviewDecisionIn] = Field(max_length=5000)


@app.post("/api/admin/review/decisions", include_in_schema=False)
def admin_review_decide(body: ReviewDecisionsIn, request: Request, session: Session = Depends(db)):
    """Store the review's decisions; invalid ones come back by index. Applied only per RADAR_REVIEW_FIELDS and
    RADAR_REVIEW_THRESHOLD."""
    from radar import review
    from radar.cache import bump_data_version

    _admin(request)
    out = review.record(session, body.run_id, [d.model_dump() for d in body.decisions])
    session.commit()
    if out["applied_changed"]:
        bump_data_version()
    return out


@app.get("/api/admin/review/decisions", include_in_schema=False)
def admin_review_list(request: Request, limit: int = Query(200, ge=1, le=2000), field: str | None = None,
                      run_id: str | None = None, status: str | None = None, session: Session = Depends(db)):
    from radar import review

    _admin(request)
    return review.latest(session, limit, field, run_id, status)


@app.post("/api/admin/review/decisions/{decision_id}/reject", include_in_schema=False)
def admin_review_reject(decision_id: int, request: Request, undo: bool = False, session: Session = Depends(db)):
    from radar import review
    from radar.cache import bump_data_version

    _admin(request)
    d, changed = review.reject(session, decision_id, undo)
    if d is None:
        raise HTTPException(404, "no such decision")
    session.commit()
    if changed:
        bump_data_version()
    return {"id": d.id, "rejected": d.rejected_at is not None, "applied": d.applied}


@app.get("/healthz")
def healthz():
    """Liveness: the process is up, and which build it runs (the commit, set at image build)."""
    import os

    return {"ok": True, "build": os.environ.get("RADAR_BUILD", "dev")[:12]}


@app.get("/readyz")
def readyz():
    """Readiness: the database answers. Kubernetes stops routing to a replica that fails this."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, f"database unavailable: {type(e).__name__}") from e
    return {"ok": True}


@app.get("/metrics", include_in_schema=False)
def metrics():
    if settings.redis_url:
        from radar.queue import queue_depths

        for name, depth in queue_depths().items():
            QUEUE_DEPTH.labels(name).set(depth)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    # The dashboard builds its content from /api/, so search engines may fetch it to render the page; the API
    # responses themselves carry X-Robots-Tag: noindex. Accounts, login links and admin stay out, and AI-training
    # crawlers are asked to stay away altogether (radar/throttle.py refuses them too).
    from radar.throttle import AI_CRAWLERS

    ai = "".join(f"User-agent: {a}\n" for a in AI_CRAWLERS) + "Disallow: /\n\n"
    return Response(ai + "User-agent: *\nAllow: /\nDisallow: /api/admin/\nDisallow: /api/me\nDisallow: /api/auth/\n"
                    f"Disallow: /auth/\nDisallow: /admin/\nSitemap: {settings.site_url.rstrip('/')}/sitemap.xml\n",
                    media_type="text/plain")


def _live_tech_rows(session: Session) -> list[stats.Row]:
    return stats.Filters().apply(stats.CACHE.rows(session))


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(session: Session = Depends(db)):
    global _last_sitemap
    try:
        xml = cached("sitemap.xml", lambda: _sitemap_xml(session), ttl=SITEMAP_TTL)
    except Exception:
        if _last_sitemap is None:
            raise
        log.exception("sitemap build failed; serving the last one built")
        xml = _last_sitemap
    _last_sitemap = xml
    return Response(xml, media_type="application/xml")


SITEMAP_TTL = 3600


def _sitemap_xml(session: Session) -> str:
    from xml.sax.saxutils import escape as xml_escape

    from radar import pages, seo

    base = settings.site_url.rstrip("/")

    def lastmod(rows) -> str | None:
        # when the newest listing on the page appeared: search engines re-crawl pages whose date moved
        newest = max((r.first_seen for r in rows), default=None)
        return newest.date().isoformat() if newest else None

    tech = _live_tech_rows(session)
    rows, landing_pages = _pages_objects(session)
    live = [r for r in rows if r.closed_at is None]
    home = lastmod(tech)
    urls = [(f"{base}/", home), (f"{base}/nl/", home), (f"{base}/companies", home),
            (f"{base}/market-report", home), (f"{base}/nl/marktrapport", home),
            (f"{base}/about", None), (f"{base}/nl/over-ons", None)]
    for lp in landing_pages:
        day = lastmod(seo._filtered(live, lp.filters, lp.city))
        urls += [(base + lp.nl_path, day), (base + lp.en_path, day)]
    for _, slug, n in pages.companies(tech):
        if n >= pages.MIN_INDEXED_POSTINGS:
            day = lastmod(pages.company_rows(tech, slug))
            urls += [(f"{base}/company/{slug}", day), (f"{base}/nl/bedrijf/{slug}", day)]
    for r in tech:
        # one URL per live listing, in the language the vacancy is written in (as the Indexing API notices,
        # radar/indexing.py): both pages carry the same employer text, and listing both halves how many jobs a
        # search engine gets to; the other language stays linked from the page through hreflang
        day = (r.posted_at or r.first_seen).date().isoformat()
        lang = "nl" if r.ex.get("posting_language") == "nl" else "en"
        urls.append((base + pages.job_path(r.id, r.title, lang), day))
    body = "".join(f"<url><loc>{xml_escape(u)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>"
                   for u, d in urls)
    return (f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{body}</urlset>")


_last_sitemap: str | None = None  # served when a rebuild fails, so a crawler never gets an error for the sitemap


# What every page load or crawler asks for, rebuilt shortly before it expires (and right after a data change), so
# no visitor waits for a rebuild: on the small server the overview takes over a second and the sitemap several.
_WARM_MARGIN = 45  # seconds before expiry


def _warm_items():
    from radar.cache import store

    def sitemap_store(session: Session) -> None:
        global _last_sitemap
        _last_sitemap = _sitemap_xml(session)
        store("sitemap.xml", _last_sitemap, SITEMAP_TTL)

    return [("/api/overview?", lambda s: store("/api/overview?", _overview_data(s), OVERVIEW_TTL)),
            ("/api/filters?", lambda s: store("/api/filters?", _filter_data(s))),
            ("sitemap.xml", sitemap_store)]


def warm_once(session: Session, force: bool = False) -> list[str]:
    """Rebuild the hot cached responses that are missing or about to expire; returns the keys rebuilt."""
    from radar.cache import seconds_left

    stats.CACHE.rows(session, wait=True)  # never build from the previous rows after a data change
    done = []
    for key, build in _warm_items():
        left = seconds_left(key)
        if force or left is None or left < _WARM_MARGIN:
            build(session)
            done.append(key)
    return done


def _keep_warm() -> None:
    from radar.cache import data_version

    seen = None
    while True:
        session = new_session()
        try:
            version = data_version()
            warm_once(session, force=seen is not None and version != seen)  # a data change: rebuild everything
            seen = version
        except Exception:
            log.exception("cache warm-up failed")
        finally:
            session.close()
        time.sleep(20)


@app.get("/companies", include_in_schema=False)
def companies_page(session: Session = Depends(db)):
    from radar import pages

    return Response(cached("page:companies", lambda: pages.render_companies(_live_tech_rows(session)), ttl=3600),
                    media_type="text/html")


@app.get("/nl/bedrijf/{slug}", include_in_schema=False)
def company_page_nl(slug: str, session: Session = Depends(db)):
    return company_page(slug, session, lang="nl")


@app.get("/company/{slug}", include_in_schema=False)
def company_page(slug: str, session: Session = Depends(db), lang: str = "en"):
    from radar import pages

    rows = _live_tech_rows(session)
    name = pages.find_company(rows, slug)
    if name is None:
        raise HTTPException(404, "no employer with live tech postings under that name")
    return Response(cached(f"page:company:{lang}:{slug}", lambda: pages.render_company(name, rows, lang), ttl=3600),
                    media_type="text/html")


@app.get("/job/{posting_id}", include_in_schema=False)
@app.get("/job/{posting_id}/{slug}", include_in_schema=False)
def job_page(posting_id: int, slug: str = "", session: Session = Depends(db), lang: str = "en"):
    """One listing: what the radar read from it and the employer's own text, credited and linked to the original."""
    from fastapi.responses import RedirectResponse

    from radar import pages
    from radar.models import Posting

    p = session.get(Posting, posting_id)
    if p is not None and p.duplicate_of:
        p = session.get(Posting, p.duplicate_of)  # a merged copy: the page of the posting it was merged into
    if p is None:
        raise HTTPException(404, "no such listing")
    if not p.is_tech:  # read again and found not to be a tech job: gone for good, so search engines drop it at once
        raise HTTPException(410, "listing removed")
    path = pages.job_path(p.id, p.title, lang)
    if path != (f"/nl/vacature/{posting_id}/{slug}" if lang == "nl" else f"/job/{posting_id}/{slug}"):
        return RedirectResponse(path, status_code=301)  # the title changed, or a merged copy: one address per job
    from radar.models import Employer

    emp = session.get(Employer, p.company)
    sector = emp.sector if emp else None

    def render():
        from radar import review

        similar = pages.similar_jobs(stats.CACHE.rows(session), p) if p.closed_at is None else []
        ov = review.overrides(session, p.id).get(p.id)
        return pages.render_job(p, lang, sector, _related_pages(session, p.id, lang), pages.job_history(session, p),
                                similar, ex=review.merge(p.extraction or {}, ov) if ov else None)

    html = cached(f"page:job:{lang}:{p.id}:{p.content_hash}:{p.closed_at is None}:{sector}:{stats.CACHE.loads}",
                  render, ttl=3600)
    return Response(html, media_type="text/html")


@app.get("/nl/vacature/{posting_id}", include_in_schema=False)
@app.get("/nl/vacature/{posting_id}/{slug}", include_in_schema=False)
def job_page_nl(posting_id: int, slug: str = "", session: Session = Depends(db)):
    return job_page(posting_id, slug, session, lang="nl")


def _related_pages(session: Session, posting_id: int, lang: str) -> list[tuple[str, str]]:
    """The search landing pages this job appears on, most specific first (a city and role page before a city page):
    links from every job page into the landing pages, and its breadcrumb."""
    from radar import seo

    rows, page_dicts = _seo_pages(session)
    row = next((r for r in rows if r.id == posting_id), None)
    if row is None:
        return []
    found = []
    for d in page_dicts:
        page = seo.Page(**d)
        if seo._filtered([row], page.filters, page.city):
            specific = bool(page.city) + bool(page.role) + bool(page.theme)
            found.append((specific, page.count, page.nl_path if lang == "nl" else page.en_path,
                          page.nl_title if lang == "nl" else page.en_title))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [(path, title) for _, _, path, title in found]


def _seo_pages(session: Session):
    from radar import seo

    rows = stats.CACHE.rows(session)
    return rows, cached("seo:pages", lambda: [p.__dict__ for p in seo.build_pages(rows)], ttl=600)


def _pages_objects(session: Session):
    from radar import seo

    rows, dicts = _seo_pages(session)
    return rows, [seo.Page(**d) for d in dicts]


def _source_link(lang: str = "en", sentence: bool = False) -> str:
    """A link to the public source repository, or nothing while RADAR_SOURCE_URL is unset (a private repository
    would only give visitors a 404)."""
    if not settings.source_url:
        return ""
    from html import escape

    url = escape(settings.source_url)
    if sentence:
        return (f' De broncode staat openbaar op <a href="{url}" rel="noopener">GitHub</a>.' if lang == "nl"
                else f' The source code is public on <a href="{url}" rel="noopener">GitHub</a>.')
    label = "Broncode op GitHub" if lang == "nl" else "Source code on GitHub"
    return f' · <a href="{url}" rel="noopener">{label}</a>'


def _render_index(session: Session | None = None, lang: str = "en") -> str:
    """index.html with branding, a localised title and description with the brand line, hreflang, and a
    footer of popular search pages. Renaming the site is one env var."""
    import json as _json

    from radar import seo

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    aliases = [a.strip() for a in settings.site_aliases.split(",") if a.strip()]
    links = ""
    if session is not None:
        _rows, pages = _pages_objects(session)
        links = seo.popular_links(pages, lang)
    # what the site does, not a running count: a count dates within hours and reads like an advert
    if lang == "nl":
        title = settings.site_headline_nl
        desc = ("Techvacatures in Nederland van de eigen vacaturesites van werkgevers. Filter op Engelstalig, "
                "visumsponsoring en jaren ervaring. Dagelijks bijgewerkt.")
    else:
        title = settings.site_headline
        desc = ("Tech jobs in the Netherlands from employers' own career sites. Filter by English-speaking, "
                "visa sponsorship and years of experience. Updated daily.")
    base = settings.site_url.rstrip("/")
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": f"{base}/#website", "name": settings.site_name, "alternateName": aliases,
         "url": settings.site_url, "description": settings.site_tagline, "inLanguage": ["en", "nl"],
         "publisher": {"@id": f"{base}/#organization"}},
        {"@type": "Organization", "@id": f"{base}/#organization", "name": settings.site_name, "url": settings.site_url,
         "logo": {"@type": "ImageObject", "url": f"{base}/static/icon-512.png", "width": 512, "height": 512},
         "email": "contact@techjobsradar.nl"},
    ]}
    return (html.replace("{{BRAND}}", brand_html()).replace("{{SITE_NAME}}", settings.site_name)
                .replace("{{SITE_URL}}", settings.site_url.rstrip("/"))
                .replace("{{SITE_TAGLINE}}", settings.site_tagline)
                .replace("{{SITE_ALIASES}}", ", ".join(aliases))
                .replace("{{SITE_JSONLD}}", _json.dumps(ld))
                .replace("{{HTML_LANG}}", lang)
                .replace("{{HOME_PATH}}", "/nl/" if lang == "nl" else "/")
                .replace("{{FEED_PATH}}", "/nl/feed.xml" if lang == "nl" else "/feed.xml")
                # the header's name, then for search engines and screen readers what the site is
                .replace("{{H1_SUFFIX}}",
                         ": techvacatures in Nederland" if lang == "nl" else ": tech jobs in the Netherlands")
                .replace("{{PAGE_TITLE}}", seo.doc_title(title))
                .replace("{{META_DESCRIPTION}}", desc)
                .replace("{{VERIFY}}", seo.verification_meta())
                .replace("{{SOURCE_LINK}}", _source_link(lang))
                .replace("{{PRIVACY_PATH}}", "/nl/privacy" if lang == "nl" else "/privacy")
                .replace("{{ABOUT_PATH}}", "/nl/over-ons" if lang == "nl" else "/about")
                .replace("{{FEEDBACK_PATH}}", "/nl/feedback" if lang == "nl" else "/feedback")
                .replace("{{SEO_LINKS}}", links))


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        from fastapi.responses import FileResponse

        return FileResponse(WEB_DIR / "favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/", include_in_schema=False)
    def index(session: Session = Depends(db)):
        stats.CACHE.rows(session)  # the title carries the live count: cache the page per load of the rows
        return Response(cached(f"page:index:en:{stats.CACHE.loads}", lambda: _render_index(session, "en"), ttl=300),
                        media_type="text/html")

    @app.get("/nl/", include_in_schema=False)
    @app.get("/nl", include_in_schema=False)
    def index_nl(session: Session = Depends(db)):
        stats.CACHE.rows(session)
        return Response(cached(f"page:index:nl:{stats.CACHE.loads}", lambda: _render_index(session, "nl"), ttl=300),
                        media_type="text/html")

    @app.get("/login", include_in_schema=False)
    @app.get("/nl/inloggen", include_in_schema=False)
    def login_page(request: Request):
        lang = "nl" if request.url.path.startswith("/nl") else "en"
        html = (WEB_DIR / "login.html").read_text(encoding="utf-8").replace("{{BRAND}}", brand_html())
        return Response(html.replace("{{HTML_LANG}}", lang).replace("{{SITE_NAME}}", settings.site_name),
                        media_type="text/html", headers={"X-Robots-Tag": "noindex"})

    @app.get("/feedback", include_in_schema=False)
    @app.get("/nl/feedback", include_in_schema=False)
    def feedback_page(request: Request):
        lang = "nl" if request.url.path.startswith("/nl") else "en"
        html = (WEB_DIR / "feedback.html").read_text(encoding="utf-8").replace("{{BRAND}}", brand_html())
        return Response(html.replace("{{HTML_LANG}}", lang).replace("{{SITE_NAME}}", settings.site_name),
                        media_type="text/html", headers={"X-Robots-Tag": "noindex"})

    @app.get("/admin/analytics", include_in_schema=False)
    def analytics_page():
        return Response((WEB_DIR / "analytics.html").read_text(encoding="utf-8")
                        .replace("{{SITE_NAME}}", settings.site_name), media_type="text/html",
                        headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})

    @app.get("/admin/review", include_in_schema=False)
    def review_page():
        return Response((WEB_DIR / "review.html").read_text(encoding="utf-8")
                        .replace("{{SITE_NAME}}", settings.site_name), media_type="text/html",
                        headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})

    @app.get("/privacy", include_in_schema=False)
    @app.get("/nl/privacy", include_in_schema=False)
    def privacy(request: Request):
        lang = "nl" if request.url.path.startswith("/nl") else "en"
        html = (WEB_DIR / "privacy.html").read_text(encoding="utf-8").replace("{{BRAND}}", brand_html())
        return Response(html.replace("{{HTML_LANG}}", lang).replace("{{SITE_NAME}}", settings.site_name)
                        .replace("{{SOURCE_EN}}", _source_link("en", sentence=True))
                        .replace("{{SOURCE_NL}}", _source_link("nl", sentence=True)), media_type="text/html")

    @app.get("/about", include_in_schema=False)
    @app.get("/nl/over-ons", include_in_schema=False)
    def about(request: Request):
        """Who runs the site, where the jobs come from, how the filters work, employers, terms and contact."""
        from html import escape

        from radar.seo import doc_title

        nl = request.url.path.startswith("/nl")
        html = (WEB_DIR / "about.html").read_text(encoding="utf-8")
        desc = (f"Hoe {settings.site_name} werkt: vacatures van de eigen carrièresites van werkgevers, hoe de filters "
                "werken, informatie voor werkgevers, voorwaarden en contact." if nl else
                f"How {settings.site_name} works: jobs from employers' own career sites, how the filters work, "
                "information for employers, terms of use and contact.")
        html = (html.replace("{{HTML_LANG}}", "nl" if nl else "en")
                .replace("{{DOC_TITLE}}", escape(doc_title(f"Over {settings.site_name}" if nl
                                                           else f"About {settings.site_name}")))
                .replace("{{DESCRIPTION}}", escape(desc)).replace("{{SELF_PATH}}", "/nl/over-ons" if nl else "/about")
                .replace("{{SITE_URL}}", settings.site_url.rstrip("/"))
                .replace("{{BRAND}}", brand_html()).replace("{{SITE_NAME}}", escape(settings.site_name)))
        return Response(html, media_type="text/html")

    @app.get("/vacatures/{slug}", include_in_schema=False)
    @app.get("/jobs/{slug}", include_in_schema=False)
    def landing(slug: str, request: Request, session: Session = Depends(db)):
        from radar import seo

        rows, pages = _pages_objects(session)
        hit = seo.find(pages, request.url.path)
        if hit is None:
            raise HTTPException(404, "no page for this search yet")
        page, lang = hit
        body = cached(f"page:landing:{request.url.path}", lambda: seo.render(page, lang, rows, pages), ttl=600)
        return Response(body, media_type="text/html")
