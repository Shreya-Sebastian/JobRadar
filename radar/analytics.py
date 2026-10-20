"""Anonymous site analytics for the admin page (/admin/analytics).

What is recorded per request: time, path, status, response time, size, the referring domain, utm_source, the
country (from Cloudflare's CF-IPCountry header), the IP address, browser, OS, device class, whether it is a bot,
and for a redirect where it pointed. What is not: the full user agent, cookies, or anything from request bodies
(pasted CV text never reaches here).

The IP address is kept for security and abuse prevention (telling heavy or hostile clients apart) and is erased
after IP_DAYS, as the privacy statement says. Unique visitors are counted with `visitor`, a hash of IP + user agent
with a salt that is replaced every day, so the daily counts do not depend on the address being kept.

Rows are buffered in memory and written in batches every few seconds, so a page view costs no database round
trip. Raw rows are kept 90 days (API calls 14 days); `rollup` keeps daily totals for the all-time figures.

Who counts as a person: the user agent is only a claim, and scrapers send a browser's along with a made-up Google
referrer. So a visit that names a browser is still counted as a bot when it comes from a cloud provider's network,
probes for files this site does not have (/wp-login.php, /.env), or never runs the page's script: every page sends a
`seen` event from theme.js, and a visitor without one did not run JavaScript. `_audit` applies these rules when the
dashboard and the daily totals are computed, so they also hold for rows recorded before the rules existed.

Who counts as someone who used the site ("people"): a human visit as above that also sent `active`, which theme.js
sends on the first real key press, tap, click, scroll wheel or mouse movement on a page. Scripted browsers that load
pages to copy them run the script but rarely send input, so "humans" can still hold some of them; "people" should not.
Account figures (sign-ups, sign-ins, saved jobs) come from the account tables, as counts only, never per person.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import secrets
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import islice
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from radar.config import settings

log = logging.getLogger(__name__)

RAW_DAYS = 90
API_DAYS = 14
IP_DAYS = 30  # then the address is blanked; the rest of the row stays until RAW_DAYS
AUDIENCES = ("people", "humans", "bots", "all")
SESSION_GAP = timedelta(minutes=30)
EVENTS = {"nav", "job_click", "profile_save", "login_request", "gap", "import", "export", "star", "seen", "active",
          "quiz_open", "quiz_done", "search_save"}
JS_SINCE = "analytics_js_since"  # Meta key: when the first `seen` event arrived; visits before it are not checked
ACTIVE_SINCE = "analytics_active_since"  # Meta key: when the first `active` event arrived
# what people did, in the order a visit usually goes: (event, label). `login` is recorded by the server (radar/auth.py)
FUNNEL = [("active", "Used a page"), ("search", "Searched"), ("job_click", "Opened a job"),
          ("star", "Saved a job"), ("profile_save", "Saved a profile"), ("login_request", "Asked to sign in"),
          ("login", "Signed in")]
FUNNEL_DAYS = 7
_BOOKKEEPING = ("seen", "active")  # events that only tell people from bots, not something anyone did
JS_GRACE = timedelta(minutes=2)  # a page's `seen` event can arrive a moment after the page itself

# Paths that would only add noise: assets, health checks, polling, admin, the beacon itself
_SKIP = re.compile(r"^/(static/|logo/|metrics|healthz|readyz|favicon|api/version|api/admin|api/me|api/e$|admin/)")
_PAGE = re.compile(r"^/(|nl/?|vacatures/.+|jobs/.+|job/.+|nl/vacature/.+|company/.+|nl/bedrijf/.+|companies|"
                   r"privacy|nl/privacy|login|nl/inloggen|feedback|nl/feedback)$")
_CRAWL = {"/robots.txt", "/sitemap.xml"}
# requests for software this site does not run: vulnerability scanners
_PROBE = re.compile(r"\.(php|asp|aspx|jsp|cgi|env|git|bak|sql)\b|^/(wp-|wordpress|\.|cgi-bin|phpmyadmin)", re.I)

_BOTS = [
    ("Googlebot", r"googlebot|google-inspectiontool|googleother"), ("bingbot", r"bingbot|bingpreview"),
    ("Google (other)", r"mediapartners-google|adsbot-google|feedfetcher-google|google-read-aloud|googleimageproxy|"
                       r"google-pagerenderer|google web preview|apis-google|google-safety|googleproducer|"
                       r"google-site-verification|storebot-google|google-extended|google-cloudvertexbot"),
    ("GPTBot", r"gptbot"), ("ChatGPT", r"chatgpt-user|oai-searchbot"), ("ClaudeBot", r"claudebot|claude-user"),
    ("PerplexityBot", r"perplexity"), ("Applebot", r"applebot"), ("DuckDuckBot", r"duckduckbot"),
    ("YandexBot", r"yandex"), ("Baiduspider", r"baiduspider"), ("AhrefsBot", r"ahrefsbot"),
    ("SemrushBot", r"semrushbot"), ("MJ12bot", r"mj12bot"), ("DotBot", r"dotbot"), ("PetalBot", r"petalbot"),
    ("facebookexternalhit", r"facebookexternalhit|meta-externalagent"), ("LinkedInBot", r"linkedinbot"),
    ("Twitterbot", r"twitterbot"), ("Slackbot", r"slackbot"), ("WhatsApp", r"whatsapp"),
    ("Discordbot", r"discordbot"), ("Uptime monitor", r"uptimerobot|pingdom|statuscake|betteruptime"),
    ("curl", r"^curl/"), ("python", r"python-requests|python-httpx|aiohttp|urllib"), ("Go client", r"go-http-client"),
    ("Headless", r"headlesschrome|phantomjs|puppeteer|playwright|chrome-lighthouse"),
    ("Bytespider", r"bytespider"), ("Amazonbot", r"amazonbot"), ("Bot", r"bot\b|crawler|spider|scrape"),
]
_BOT_RX = [(name, re.compile(rx, re.I)) for name, rx in _BOTS]

# Cloud networks that sent browser-like scrapers with a made-up Google referrer (seen in October 2026). People do not
# browse from these; the other networks such bots use are caught by the script check instead.
_CLOUDS = {
    "Alibaba Cloud": ["8.208.0.0/12", "47.74.0.0/15", "47.76.0.0/14", "47.80.0.0/12", "47.96.0.0/11", "47.235.0.0/16",
                      "47.236.0.0/14", "47.240.0.0/13", "47.250.0.0/15", "47.252.0.0/15", "47.254.0.0/16",
                      "149.129.0.0/16", "161.117.0.0/16", "170.33.0.0/16", "198.11.128.0/18"],
    "Tencent Cloud": ["43.128.0.0/10", "49.51.0.0/16", "101.32.0.0/14", "119.28.0.0/15", "124.156.0.0/16",
                      "129.226.0.0/16", "150.109.0.0/16", "162.62.0.0/16", "170.106.0.0/16"],
    "Huawei Cloud": ["119.8.0.0/16", "159.138.0.0/16"],
}
_CLOUD_NETS = [(name, ipaddress.ip_network(net)) for name, nets in _CLOUDS.items() for net in nets]


@lru_cache(maxsize=20000)
def cloud_network(ip: str | None) -> str | None:
    """The cloud provider an address belongs to, if it is one of `_CLOUDS`."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    return next((name for name, net in _CLOUD_NETS if addr in net), None)


def parse_agent(ua: str) -> tuple[str, str, str, bool]:
    """(browser, os, device, is_bot) from a user-agent string."""
    ua = ua or ""
    if not ua:
        return "Unknown", "Unknown", "unknown", True
    for name, rx in _BOT_RX:
        if rx.search(ua):
            return name, _os(ua), "bot", True
    if re.search(r"edg/|edga/|edgios/", ua, re.I):
        browser = "Edge"
    elif re.search(r"opr/|opera", ua, re.I):
        browser = "Opera"
    elif re.search(r"samsungbrowser", ua, re.I):
        browser = "Samsung Internet"
    elif re.search(r"firefox/|fxios/", ua, re.I):
        browser = "Firefox"
    elif re.search(r"chrome/|crios/", ua, re.I):
        browser = "Chrome"
    elif re.search(r"safari/", ua, re.I):
        browser = "Safari"
    else:
        browser = "Other"
    if re.search(r"ipad|tablet", ua, re.I):
        device = "tablet"
    elif re.search(r"mobi|iphone|android", ua, re.I):
        device = "mobile"
    else:
        device = "desktop"
    return browser, _os(ua), device, False


def _os(ua: str) -> str:
    for name, rx in (("iOS", r"iphone|ipad|ipod"), ("Android", r"android"), ("Windows", r"windows"),
                     ("macOS", r"mac os x|macintosh"), ("ChromeOS", r"cros"), ("Linux", r"linux")):
        if re.search(rx, ua, re.I):
            return name
    return "Other"


def classify(path: str) -> str | None:
    if _SKIP.match(path):
        return None
    if path in _CRAWL:
        return "crawl"
    if path.startswith("/api/"):
        return "api"
    if _PAGE.match(path):
        return "page"
    return "other"


def _referrer(header: str | None) -> str | None:
    if not header:
        return None
    host = (urlparse(header).hostname or "").lower().removeprefix("www.")
    own = (urlparse(settings.site_url).hostname or "").removeprefix("www.")
    if not host or host == own or host in ("localhost", "127.0.0.1"):
        return None
    return host[:120]


def client_ip(request) -> str:
    """The visitor's address as Cloudflare reports it (the connection itself comes from Cloudflare/Traefik)."""
    for h in ("cf-connecting-ip", "x-forwarded-for"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else ""


# ---------- daily salt ----------
_salt: tuple[str, str] | None = None  # (day, salt)
_salt_lock = threading.Lock()


def _daily_salt(today: str) -> str:
    """Kept only in memory and in one row that is replaced every day, so yesterday's hashes cannot be recomputed."""
    global _salt
    with _salt_lock:
        if _salt and _salt[0] == today:
            return _salt[1]
        from radar.db import session_scope
        from radar.models import Meta

        with session_scope() as s:
            row = s.get(Meta, "analytics_salt")
            day, _, value = (row.value if row else "").partition(":")
            if day != today or not value:
                value = secrets.token_hex(16)
                if row is None:
                    s.add(Meta(key="analytics_salt", value=f"{today}:{value}"))
                else:
                    row.value = f"{today}:{value}"
        _salt = (today, value)
        return value


def visitor_id(ip: str, ua: str, today: str) -> str:
    return hashlib.sha256(f"{_daily_salt(today)}|{ip}|{ua}".encode()).hexdigest()[:16]


# ---------- recording ----------
_buffer: list[dict] = []
_buffer_lock = threading.Lock()
_flusher: threading.Thread | None = None


def _search_detail(query: str) -> str | None:
    """Filters of a job search (first page only, so paging is not counted as a new search)."""
    q = parse_qs(query)
    if q.get("page", ["1"])[0] not in ("", "1") or q.get("ids"):
        return None
    keep = ["q", "city", "role", "seniority", "skill", "language", "remote", "sponsorship", "english_only",
            "experience", "org_size"]
    parts = [f"{k}={q[k][0][:40]}" for k in keep if q.get(k) and q[k][0] not in ("", "false")]
    return "&".join(parts)[:200] if parts else None


IGNORE_COOKIE = "radar_noanalytics"  # set only in the admin's own browsers, from the analytics page


def excluded(request) -> bool:
    return request.cookies.get(IGNORE_COOKIE) == "1"


def forget_today(session: Session, visitor: str) -> int:
    """Remove today's rows of one visitor (the admin excluding their own browser), buffered ones included."""
    from radar.models import PageView

    now = datetime.utcnow()
    with _buffer_lock:
        _buffer[:] = [r for r in _buffer if r["visitor"] != visitor]
    n = session.execute(delete(PageView).where(PageView.visitor == visitor,
                                               PageView.ts >= datetime(now.year, now.month, now.day))).rowcount
    session.commit()
    return n or 0


def request_visitor(request) -> str:
    return visitor_id(client_ip(request), request.headers.get("user-agent", ""), datetime.utcnow().date().isoformat())


def record(request, status: int, ms: float, size: int, event: str | None = None, detail: str | None = None,
           path: str | None = None) -> None:
    if not settings.analytics_enabled or excluded(request):
        return
    path = path or request.url.path
    kind = "event" if event else classify(path)
    if kind is None:
        return
    ua = request.headers.get("user-agent", "")
    browser, os_name, device, bot = parse_agent(ua)
    ip = client_ip(request)
    if not bot and (label := cloud_network(ip) or ("Probe" if _PROBE.search(path) else None)):
        browser, device, bot = label, "bot", True
    now = datetime.utcnow()
    if kind == "api" and path == "/api/postings" and request.method == "GET":
        detail = _search_detail(request.url.query)
        event = "search" if detail else None
    elif kind == "api" and path == "/api/gap":
        event = "gap"
    elif kind == "api" and path == "/api/auth/request":
        event = "login_request"
    utm = parse_qs(request.url.query).get("utm_source", [None])[0]
    row = {
        "ts": now, "kind": kind, "path": path[:300], "status": status, "ms": int(ms), "bytes": int(size or 0),
        # where a visit came from: only the page load counts, not the API calls that page makes afterwards
        "referrer": _referrer(request.headers.get("referer")) if kind == "page" else None,
        "utm_source": utm[:60] if utm else None, "country": (request.headers.get("cf-ipcountry") or "")[:2] or None,
        "browser": browser, "os": os_name, "device": device, "bot": bot,
        "visitor": request_visitor(request), "ip": ip[:45] or None,
        "event": event, "detail": detail[:200] if detail else None,
    }
    with _buffer_lock:
        _buffer.append(row)
        if len(_buffer) > 5000:  # database away for a long time: drop the oldest rather than grow
            del _buffer[:1000]
    _ensure_flusher()


def buffer_size() -> int:
    return len(_buffer)


def flush() -> int:
    from radar.db import session_scope
    from radar.models import Meta, PageView

    with _buffer_lock:
        rows = _buffer[:]
        _buffer.clear()
    if not rows:
        return 0
    try:
        with session_scope() as s:
            s.bulk_insert_mappings(PageView, rows)
            for event, key in (("seen", JS_SINCE), ("active", ACTIVE_SINCE)):
                stamps = [r["ts"] for r in rows if r["event"] == event]
                if stamps and s.get(Meta, key) is None:
                    s.add(Meta(key=key, value=min(stamps).isoformat()))
    except Exception:
        log.exception("analytics flush failed; %d rows dropped", len(rows))
        return 0
    return len(rows)


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return

    def loop():
        while True:
            time.sleep(10)
            flush()

    _flusher = threading.Thread(target=loop, name="analytics-flush", daemon=True)
    _flusher.start()


# ---------- who is a person ----------
def js_since(session: Session) -> datetime | None:
    """When pages started sending the `seen` event; before that a missing one proves nothing."""
    from radar.models import Meta

    row = session.get(Meta, JS_SINCE)
    if row:
        return datetime.fromisoformat(row.value)
    buffered = [r["ts"] for r in list(_buffer) if r["event"] == "seen"]
    return min(buffered) if buffered else None


def _audit(rows: list, since: datetime | None, until: datetime | None = None) -> list:
    """`rows` with visits that only claim to be a person marked as bots (see the module docstring). `since` is when
    the script check started, `until` leaves the most recent visits alone while their `seen` event is on its way."""
    ran = {r.visitor for r in rows if r.event == "seen"}
    out = []
    for r in rows:
        label = None
        if not r.bot:
            label = cloud_network(r.ip) or ("Probe" if _PROBE.search(r.path or "") else None)
            if (label is None and since is not None and r.kind != "event" and r.visitor not in ran
                    and since <= r.ts and (until is None or r.ts < until)):
                label = "No JavaScript"
        out.append(r if label is None else SimpleNamespace(**{**_fields(r), "bot": True, "browser": label,
                                                             "device": "bot"}))
    return out


def _fields(r) -> dict:
    return r._asdict() if hasattr(r, "_asdict") else dict(vars(r))


# ---------- dashboard ----------
def _top(counter: Counter, n: int = 10) -> list[dict]:
    return [{"key": k, "hits": v} for k, v in counter.most_common(n) if k]


def _sessions(views: list) -> tuple[int, int, float]:
    """(sessions, bounced sessions, pages per session) from page views and SPA navigations of humans."""
    by_visitor: dict[str, list[datetime]] = defaultdict(list)
    for v in views:
        by_visitor[v.visitor].append(v.ts)
    sessions = bounced = pages = 0
    for times in by_visitor.values():
        times.sort()
        count = 1
        for prev, cur in zip(times, times[1:], strict=False):
            if cur - prev > SESSION_GAP:
                sessions += 1
                bounced += count == 1
                pages += count
                count = 1
            else:
                count += 1
        sessions += 1
        bounced += count == 1
        pages += count
    return sessions, bounced, (pages / sessions if sessions else 0.0)


def dashboard(session: Session, who: str = "humans", now: datetime | None = None, recent: int = 500,
              include_bots: bool | None = None) -> dict:
    """The analytics page. `who` filters every figure and list to humans, bots or all traffic (the timeline and the
    crawler list always show both, side by side)."""
    from radar.models import PageView, Posting

    if include_bots is not None:  # older callers
        who = "all" if include_bots else "humans"
    who = who if who in AUDIENCES else "humans"
    include_bots = who not in ("humans", "people")
    people: set[str] = set()

    def keep(r) -> bool:
        if who == "people":
            return not r.bot and r.visitor in people
        return who == "all" or (who == "bots") == bool(r.bot)

    now = now or datetime.utcnow()
    day_start = datetime(now.year, now.month, now.day)
    since = now - timedelta(hours=24)
    cols = [PageView.ts, PageView.kind, PageView.path, PageView.status, PageView.ms, PageView.bytes,
            PageView.referrer, PageView.utm_source, PageView.country, PageView.browser, PageView.os, PageView.device,
            PageView.bot, PageView.visitor, PageView.event, PageView.detail, PageView.ip]
    rows = session.execute(select(*cols).where(PageView.ts >= min(since, day_start)).order_by(PageView.ts)).all()
    rows += [_as_row(r) for r in list(_buffer)]  # not yet flushed
    rows = _audit(rows, js_since(session), until=now - JS_GRACE)
    people.update(r.visitor for r in rows if r.event == "active" and not r.bot)
    h24 = [r for r in rows if r.ts >= since]
    today = [r for r in rows if r.ts >= day_start]
    shown = [r for r in h24 if keep(r)]

    def is_view(r):
        return r.kind == "page" or r.event == "nav"

    views_today = [r for r in today if is_view(r) and keep(r)]
    views_24 = [r for r in shown if is_view(r)]
    human_views = [r for r in h24 if is_view(r) and not r.bot]
    served = [r for r in h24 if r.kind != "event"]
    served_shown = [r for r in served if keep(r)]
    times = sorted(r.ms for r in served)
    sessions, bounced, pps = _sessions(human_views)
    bytes_24 = sum(r.bytes for r in served)
    visitors_24 = {r.visitor for r in served}

    timeline = [{"hour": (since + timedelta(hours=i + 1)).strftime("%H:00"), "humans": 0, "bots": 0} for i in range(24)]
    for r in h24:
        if is_view(r):
            idx = min(23, int((r.ts - since).total_seconds() // 3600))
            timeline[idx]["bots" if r.bot else "humans"] += 1

    consumers: dict[str, dict] = {}
    for r in served_shown:
        key = r.ip or (r.browser if r.bot else f"visitor {(r.visitor or '')[:6]}")
        c = consumers.setdefault(key, {"who": key, "agent": r.browser, "country": r.country, "requests": 0, "bytes": 0,
                                       "bot": r.bot})
        c["requests"] += 1
        c["bytes"] += r.bytes

    clicks = [r for r in shown if r.event == "job_click" and r.detail and r.detail.isdigit()]
    ids = {int(r.detail) for r in clicks}
    posting = {p.id: p for p in session.scalars(select(Posting).where(Posting.id.in_(ids)))} if ids else {}
    by_employer = Counter(posting[int(r.detail)].company for r in clicks if int(r.detail) in posting)
    by_job = Counter(f"{posting[int(r.detail)].title} · {posting[int(r.detail)].company}"
                     for r in clicks if int(r.detail) in posting)

    # redirects served: where each one sent the visitor (`detail` holds the Location header)
    redirects = [r for r in served_shown if 300 <= (r.status or 0) < 400]
    grouped: dict[tuple, dict] = {}
    for r in redirects:
        g = grouped.setdefault((r.path, r.status, r.detail), {"path": r.path, "status": r.status, "to": r.detail,
                                                               "hits": 0, "last": r.ts})
        g["hits"] += 1
        g["last"] = max(g["last"], r.ts)
    redirect_list = [{**g, "last": g["last"].isoformat(timespec="seconds") + "Z"}
                     for g in sorted(grouped.values(), key=lambda g: (-g["hits"], g["path"]))[:100]]

    all_time = _all_time(session, today, day_start)
    return {
        "generated_at": now.isoformat(timespec="seconds") + "Z", "include_bots": include_bots, "who": who,
        "kpi": {
            "views_today": len(views_today), "uniques_today": len({r.visitor for r in views_today}),
            "views_24h": len(views_24), "uniques_24h": len({r.visitor for r in views_24}),
            "human_views_24h": len(human_views),
            "people_today": len({r.visitor for r in today if r.visitor in people}),
            "people_24h": len({r.visitor for r in h24 if r.visitor in people}),
            "humans_24h": len({r.visitor for r in h24 if not r.bot and r.kind != "event"}),
            "people_since": _meta_time(session, ACTIVE_SINCE),
            "bot_hits_24h": sum(1 for r in h24 if r.bot and r.kind != "event"),
            "bounce_rate": round(bounced / sessions, 3) if sessions else None, "pages_per_session": round(pps, 1),
            "avg_ms": round(sum(times) / len(times)) if times else 0,
            "p95_ms": times[int(len(times) * 0.95)] if times else 0,
            "bytes_today": sum(r.bytes for r in today if r.kind != "event"), "bytes_24h": bytes_24,
            "bytes_per_visitor": round(bytes_24 / len(visitors_24)) if visitors_24 else 0,
            "spa_navigations": sum(1 for r in shown if r.event == "nav"),
            "interactions": sum(1 for r in shown if (r.kind == "event" or r.event) and r.event not in _BOOKKEEPING),
            "crawler_fetches": sum(1 for r in h24 if r.kind == "crawl"),
            "job_clicks": len(clicks), "searches": sum(1 for r in shown if r.event == "search"),
            "buffered": buffer_size(),
        },
        "timeline": timeline,
        "top_pages": _top(Counter(r.path for r in views_24 if r.kind == "page")),
        "top_api": _top(Counter(r.path for r in shown if r.kind == "api")),
        "referrers": _top(Counter(r.referrer for r in shown if r.referrer)),
        "utm": _top(Counter(r.utm_source for r in shown if r.utm_source)),
        "countries": _top(Counter(r.country for r in shown if r.kind != "event" and r.country)),
        "os": _top(Counter(r.os for r in shown if r.kind != "event")),
        "browsers": _top(Counter(r.browser for r in served_shown)),
        "devices": _top(Counter(r.device for r in served_shown)),
        "bots": _top(Counter(r.browser for r in h24 if r.bot and r.kind != "event")),
        "statuses": _top(Counter(str(r.status) for r in served_shown)),
        "redirects": {"count": len(redirects), "items": redirect_list},
        "consumers": sorted(consumers.values(), key=lambda c: -c["bytes"])[:10],
        "job_clicks_by_employer": _top(by_employer),
        "job_clicks_by_job": _top(by_job),
        "searches": _top(Counter(r.detail for r in shown if r.event == "search" and r.detail), 15),
        "events": _top(Counter(r.event for r in shown
                               if r.event and r.event not in ("nav", "search", *_BOOKKEEPING)), 12),
        # every visit of the last 24 hours except the API calls a page makes while it loads, newest first
        "recent": [
            {"ts": r.ts.isoformat(timespec="seconds") + "Z", "kind": r.kind, "path": r.path, "status": r.status,
             "ms": r.ms, "bytes": r.bytes, "referrer": r.referrer, "utm": r.utm_source, "country": r.country,
             "browser": r.browser, "os": r.os, "device": r.device, "bot": bool(r.bot), "ip": r.ip,
             "visitor": (r.visitor or "")[:6], "event": r.event,
             "to": r.detail if 300 <= (r.status or 0) < 400 else None}
            for r in islice((r for r in reversed(h24) if r.kind != "api" and keep(r)), max(0, recent))
        ],
        "recent_total": sum(1 for r in h24 if r.kind != "api" and keep(r)),
        "all_time": all_time,
        "funnel": funnel(session, now),
        "accounts": accounts(session, now),
    }


def _meta_time(session: Session, key: str) -> str | None:
    from radar.models import Meta

    row = session.get(Meta, key)
    return row.value[:19] + "Z" if row else None


def funnel(session: Session, now: datetime | None = None, days: int = FUNNEL_DAYS) -> list[dict]:
    """How many people (visitors who sent `active`, see the module docstring) took each step in the past `days`.
    A visitor id is a daily hash, so someone who comes back on three days counts three times."""
    from radar.models import PageView

    now = now or datetime.utcnow()
    names = [e for e, _ in FUNNEL]
    rows = session.execute(select(PageView.ts, PageView.kind, PageView.path, PageView.bot, PageView.visitor,
                                  PageView.ip, PageView.event)
                           .where(PageView.ts >= now - timedelta(days=days), PageView.event.in_(names))).all()
    rows += [_as_row(r) for r in list(_buffer) if r["event"] in names]
    did: dict[str, set] = defaultdict(set)
    for r in _audit(rows, None):
        if not r.bot:
            did[r.event].add(r.visitor)
    users = did["active"]
    out = []
    for event, label in FUNNEL:
        n = len(users) if event == "active" else len(did[event] & users)
        out.append({"step": label, "event": event, "people": n, "share": round(n / len(users), 3) if users else None})
    return out


def accounts(session: Session, now: datetime | None = None) -> dict:
    """Account figures: totals and counts only, no addresses."""
    from radar.models import LoginToken, SavedSearch, User, UserData, UserSession

    now = now or datetime.utcnow()
    d1, d7, d30 = (now - timedelta(days=n) for n in (1, 7, 30))
    users = session.execute(select(User.id, User.created_at, User.last_login_at, User.alerts)).all()
    seen7 = set(session.scalars(select(UserSession.user_id).where(UserSession.last_seen_at >= d7)))
    saved_jobs = with_saved = with_skills = tracking = 0
    for saved, board, profile in session.execute(select(UserData.saved, UserData.board, UserData.profile)):
        saved_jobs += len(saved or [])
        with_saved += bool(saved)
        tracking += bool(board)
        with_skills += bool((profile or {}).get("skills"))
    searches = session.execute(select(SavedSearch.user_id, SavedSearch.frequency)).all()
    alerted = ({u.id for u in users if (u.alerts or "off") != "off"}
               | {x.user_id for x in searches if (x.frequency or "off") != "off"})
    links = session.execute(select(LoginToken.used_at).where(LoginToken.created_at >= d30)).all()
    day0 = datetime(now.year, now.month, now.day)
    daily = []
    for i in range(13, -1, -1):
        start = day0 - timedelta(days=i)
        end = start + timedelta(days=1)
        daily.append({"day": start.date().isoformat(),
                      "new": sum(1 for u in users if start <= u.created_at < end),
                      "signed_in": sum(1 for u in users if u.last_login_at and start <= u.last_login_at < end)})
    return {
        "total": len(users),
        "new_24h": sum(1 for u in users if u.created_at >= d1),
        "new_7d": sum(1 for u in users if u.created_at >= d7),
        "new_30d": sum(1 for u in users if u.created_at >= d30),
        "signed_in_7d": sum(1 for u in users if u.last_login_at and u.last_login_at >= d7),
        "signed_in_30d": sum(1 for u in users if u.last_login_at and u.last_login_at >= d30),
        "active_7d": len(seen7),
        "with_saved_jobs": with_saved, "saved_jobs": saved_jobs, "with_skills": with_skills,
        "tracking_applications": tracking, "saved_searches": len(searches), "with_alerts": len(alerted),
        "links_30d": len(links), "links_used_30d": sum(1 for x in links if x.used_at is not None),
        "daily": daily,
    }


def _as_row(d: dict):
    return SimpleNamespace(**d)


def _all_time(session: Session, today_rows: list, day_start: datetime) -> dict:
    from radar.models import DailyStat

    stats = session.execute(select(DailyStat.day, DailyStat.dim, DailyStat.key, DailyStat.hits, DailyStat.humans,
                                   DailyStat.uniques)).all()
    totals = [s for s in stats if s.dim == "total"]
    live = [r for r in today_rows if r.kind != "event"]
    hits = sum(s.hits for s in totals) + len(live)
    humans = sum(s.humans for s in totals) + sum(1 for r in live if not r.bot)
    uniques = sum(s.uniques for s in totals) + len({r.visitor for r in live if not r.bot})
    days = sorted({s.day for s in totals})
    since = days[0] if days else day_start.date()
    n_days = (day_start.date() - since).days + 1
    by = defaultdict(Counter)
    for s in stats:
        if s.dim not in ("total", "people"):
            by[s.dim][s.key] += s.humans
    for r in live:
        if not r.bot:
            if r.country:
                by["country"][r.country] += 1
            if r.referrer:
                by["referrer"][r.referrer] += 1
            if r.kind == "page":
                by["page"][r.path] += 1
    daily = {s.day: s for s in totals}
    people_daily = {s.day: s.uniques for s in stats if s.dim == "people"}
    last14 = []
    for i in range(13, -1, -1):
        d = day_start.date() - timedelta(days=i)
        if d == day_start.date():
            people = [r for r in live if not r.bot]
            last14.append({"day": d.isoformat(), "humans": len(people), "bots": len(live) - len(people),
                           "uniques": len({r.visitor for r in people}),
                           "people": len({r.visitor for r in today_rows if r.event == "active" and not r.bot})})
        else:
            s = daily.get(d)
            last14.append({"day": d.isoformat(), "humans": s.humans if s else 0,
                           "bots": (s.hits - s.humans) if s else 0, "uniques": s.uniques if s else 0,
                           "people": people_daily.get(d, 0)})
    return {
        "hits": hits, "humans": humans, "bots": hits - humans, "uniques": uniques, "since": since.isoformat(),
        "days": n_days, "countries": len(by["country"]), "avg_daily_humans": round(humans / n_days) if n_days else 0,
        "daily": last14, "top_countries": _top(by["country"]), "top_referrers": _top(by["referrer"]),
        "top_pages": _top(by["page"]),
    }


# ---------- nightly ----------
def rollup(session: Session, day: date) -> int:
    """Store the totals of one finished day (idempotent: re-running replaces them)."""
    from radar.models import DailyStat, PageView

    start = datetime(day.year, day.month, day.day)
    rows = session.execute(select(PageView.ts, PageView.kind, PageView.path, PageView.referrer, PageView.country,
                                  PageView.bot, PageView.visitor, PageView.ip, PageView.event)
                           .where(PageView.ts >= start, PageView.ts < start + timedelta(days=1),
                                  (PageView.kind != "event") | PageView.event.in_(("seen", "active")))).all()
    audited = _audit(rows, js_since(session))
    people = {r.visitor for r in audited if r.event == "active" and not r.bot}
    rows = [r for r in audited if r.kind != "event"]
    session.execute(delete(DailyStat).where(DailyStat.day == day))
    if not rows:
        return 0
    humans = [r for r in rows if not r.bot]
    out = [DailyStat(day=day, dim="total", key="all", hits=len(rows), humans=len(humans),
                     uniques=len({r.visitor for r in humans}))]
    if people:
        views = sum(1 for r in humans if r.visitor in people)
        out.append(DailyStat(day=day, dim="people", key="all", hits=views, humans=views, uniques=len(people)))
    for dim, values in (("country", [r.country for r in humans]), ("referrer", [r.referrer for r in humans]),
                        ("page", [r.path for r in humans if r.kind == "page"])):
        for key, n in Counter(v for v in values if v).most_common(200):
            out.append(DailyStat(day=day, dim=dim, key=key[:200], hits=n, humans=n))
    session.add_all(out)
    return len(out)


def rollup_all(session: Session, now: datetime | None = None) -> dict:
    """Redo the totals of every finished day whose raw rows are still kept, after the rules for bots changed.
    Rows older than IP_DAYS have lost their address, so the cloud-network rule can no longer reach them."""
    from radar.models import DailyStat, PageView

    today = (now or datetime.utcnow()).date()
    first = session.scalar(select(func.min(PageView.ts)))
    if first is None:
        return {"days": 0}
    before = _google_humans(session)
    days = [first.date() + timedelta(days=i) for i in range((today - first.date()).days)]
    for d in days:
        rollup(session, d)
    session.commit()
    return {"days": len(days), "from": days[0].isoformat() if days else None,
            "google_humans_before": before, "google_humans_after": _google_humans(session),
            "human_hits_after": session.scalar(select(func.sum(DailyStat.humans)).where(DailyStat.dim == "total"))}


def _google_humans(session: Session) -> int:
    from radar.models import DailyStat

    return session.scalar(select(func.coalesce(func.sum(DailyStat.humans), 0))
                          .where(DailyStat.dim == "referrer", DailyStat.key.like("google.%"))) or 0


def nightly(session: Session, now: datetime | None = None) -> dict:
    """Roll up the last two finished days, then drop raw rows past their retention."""
    from radar.models import PageView

    now = now or datetime.utcnow()
    today = now.date()
    rolled = sum(rollup(session, today - timedelta(days=i)) for i in (1, 2))
    old = session.execute(delete(PageView).where(PageView.ts < now - timedelta(days=RAW_DAYS))).rowcount
    old_api = session.execute(delete(PageView).where(PageView.kind == "api",
                                                     PageView.ts < now - timedelta(days=API_DAYS))).rowcount
    session.execute(update(PageView).where(PageView.ts < now - timedelta(days=IP_DAYS), PageView.ip.is_not(None))
                    .values(ip=None))
    session.commit()
    first = session.scalar(select(func.min(PageView.ts)))
    return {"daily_rows": rolled, "deleted": (old or 0) + (old_api or 0), "oldest_raw": first and first.isoformat()}
