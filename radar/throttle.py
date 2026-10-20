"""Limits on how fast one visitor can fetch pages and API responses, and a refusal for bulk scrapers.

Every listing is public, so nothing stops a patient scraper from collecting it slowly; the point is that nobody can
pull the whole site in a few minutes or load the server doing it. Requests are counted per client IP in one-minute
windows (in Redis when configured, so all API replicas share the count), with a lower limit for the listing and
detail endpoints a scraper would page through. Known AI-training crawlers and plain HTTP libraries are refused
outright; search engines are not, and robots.txt tells the crawlers that honour it the same thing. AI search
crawlers (ChatGPT search, Perplexity) are search engines too: they link back. A search engine's own crawler,
confirmed by reverse and forward DNS as its operators recommend, is not counted against the limits, so it can
index thousands of new job pages without being slowed down.
"""

from __future__ import annotations

import re
import socket
import threading
import time

from radar.config import settings

# AI-training and bulk crawlers, and HTTP libraries that no browser sends (AI search crawlers are allowed)
BLOCKED_AGENTS = re.compile(
    r"GPTBot|CCBot|ClaudeBot|Claude-Web|anthropic-ai|Google-Extended|Bytespider|"
    r"Amazonbot|meta-externalagent|FacebookBot|Applebot-Extended|cohere-ai|Diffbot|ImagesiftBot|"
    r"Omgili|Timpibot|Scrapy|python-requests|python-urllib|aiohttp|httpx|Go-http-client|okhttp|curl/|Wget|"
    r"libwww-perl|HeadlessChrome|PhantomJS",
    re.I,
)
AI_CRAWLERS = ("GPTBot", "CCBot", "ClaudeBot", "anthropic-ai", "Google-Extended", "Bytespider", "Amazonbot",
               "meta-externalagent", "Applebot-Extended", "cohere-ai", "Diffbot", "ImagesiftBot", "Omgili", "Timpibot")

# search engines whose crawlers can be confirmed by DNS: the agent they send, and the host names their IPs resolve to
_SEARCH_BOTS = re.compile(r"Googlebot|Google-InspectionTool|AdsBot-Google|bingbot|Applebot(?!-Extended)|YandexBot",
                          re.I)
_BOT_HOSTS = (".googlebot.com", ".google.com", ".googleusercontent.com", ".search.msn.com", ".applebot.apple.com",
              ".yandex.ru", ".yandex.net", ".yandex.com")
_verified: dict[str, tuple[bool, float]] = {}


def verified_crawler(ip: str, agent: str) -> bool:
    """A search engine's own crawler: the agent says so, the IP's host name belongs to the search engine and that
    name resolves back to the IP. Cached a day per IP (a fake "Googlebot" costs one lookup a day)."""
    if not ip or not agent or not _SEARCH_BOTS.search(agent):
        return False
    now = time.time()
    hit = _verified.get(ip)
    if hit and hit[1] > now:
        return hit[0]
    ok = False
    try:
        host = socket.gethostbyaddr(ip)[0].lower().rstrip(".")
        if host.endswith(_BOT_HOSTS):
            ok = ip in {a[4][0] for a in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        ok = False
    with _local_lock:
        if len(_verified) > 20000:
            _verified.clear()
        _verified[ip] = (ok, now + 86400)
    return ok

_EXEMPT = ("/healthz", "/readyz", "/metrics", "/robots.txt", "/static/", "/favicon", "/logo/")
_DETAIL = re.compile(r"^/api/postings|^/(?:nl/)?(?:jobs|job|vacatures|vacature|company|bedrijf)/")

_local: dict[str, int] = {}
_local_lock = threading.Lock()
_redis = None
_redis_checked = False


def _client():
    global _redis, _redis_checked
    if not _redis_checked:
        _redis_checked = True
        if settings.redis_url:
            try:
                import redis

                _redis = redis.Redis.from_url(settings.redis_url, socket_timeout=1)
                _redis.ping()
            except Exception:
                _redis = None
    return _redis


def _count(key: str) -> int:
    r = _client()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, 120)
            return int(pipe.execute()[0])
        except Exception:
            pass  # Redis down: fall back to counting in this process
    with _local_lock:
        if len(_local) > 50_000:
            _local.clear()
        _local[key] = _local.get(key, 0) + 1
        return _local[key]


def check(path: str, ip: str, agent: str) -> tuple[int, str] | None:
    """None when the request may go ahead, else (status, reason)."""
    if path.startswith(_EXEMPT):
        return None
    if settings.indexnow_key and path == f"/{settings.indexnow_key}.txt":
        return None  # the IndexNow key file: search engines check it with whatever HTTP client they use
    if settings.block_scraper_agents and (not agent or BLOCKED_AGENTS.search(agent)):
        return 403, "automated access is not permitted, see /robots.txt"
    if settings.rate_limit_per_minute <= 0 or not ip:
        return None
    if verified_crawler(ip, agent):
        return None
    minute = int(time.time() // 60)
    if _count(f"rl:{minute}:{ip}") > settings.rate_limit_per_minute:
        return 429, "too many requests"
    if _DETAIL.match(path) and _count(f"rld:{minute}:{ip}") > settings.rate_limit_detail_per_minute:
        return 429, "too many requests"
    return None


def reset() -> None:
    global _redis, _redis_checked
    with _local_lock:
        _local.clear()
    _redis, _redis_checked = None, False
