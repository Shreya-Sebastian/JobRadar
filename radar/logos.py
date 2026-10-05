"""Employer logos for the job cards, served from this site.

For each employer with live postings, `refresh` reads one public page once: the employer's own careers site when the
source has one (its touch icon or favicon), otherwise the employer's board on its applicant tracking system (the
company logo shown in the board's header; never the ATS's own icon). The image is downloaded once, kept in the
`company_logos` table and served at /logo/<slug>, so a visitor's browser never contacts the employer or a logo
service. The fetches go through the crawler's client: named user agent, robots.txt and the per-host rate limits.
Employers without a usable logo keep the letter tile; they are tried again after RETRY_DAYS.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from radar.config import settings

log = logging.getLogger(__name__)

MAX_BYTES = 150_000
RETRY_DAYS = 30
REFRESH_DAYS = 90
TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml", "image/x-icon",
         "image/vnd.microsoft.icon"}
# the public board page of each tracking system, from the source's slug
BOARDS = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com/",
    "workable": "https://apply.workable.com/{slug}/",
    "personio": "https://{slug}.jobs.personio.de/",
    "teamtailor": "https://{slug}.teamtailor.com/",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
}
# hosts that belong to a tracking system or job board: their favicon is theirs, not the employer's
ATS_HOSTS = re.compile(r"greenhouse|lever\.co|ashbyhq|recruitee|workable|personio|teamtailor|smartrecruiters|"
                       r"myworkdayjobs|workday|icims|avature|successfactors|homerun|join\.com|werkenbij-online", re.I)
_LOGO_HINT = re.compile(r"logo", re.I)
_JSON_LOGO = re.compile(r'"[a-zA-Z]*[Ll]ogo[a-zA-Z]*"\s*:\s*"(https?:[^"]+)"')
# the employer's own website, as an ATS board names it in its page data
_JSON_SITE = re.compile(r'"(?:publicWebsite|website|websiteUrl|companyWebsite|company_url|companyUrl|homepageUrl)"'
                        r'\s*:\s*"(https?://[^"]+)"')
_BRANDS = ("greenhouse", "lever", "ashby", "recruitee", "workable", "personio", "teamtailor", "smartrecruiters",
           "workday", "icims", "avature", "powered")


def slug_of(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:120] or "company"


def board_url(ats: str, slug: str, url: str | None) -> str | None:
    """The page that shows the employer's logo: the source's own careers URL, else its public ATS board."""
    if url:
        return url
    if ats == "lever":
        return f"https://jobs.eu.lever.co/{slug[3:]}" if slug.startswith("eu/") else f"https://jobs.lever.co/{slug}"
    pattern = BOARDS.get(ats)
    return pattern.format(slug=slug) if pattern and "/" not in slug else None


def candidates(html: str, page_url: str) -> list[str]:
    """Image URLs that are likely the employer's logo, best first. On an ATS page only images named as a logo
    (or a logo URL in the page's data) count; on the employer's own site its touch icon and icons count too."""
    soup = BeautifulSoup(html, "lxml")
    own_site = not ATS_HOSTS.search(urlparse(page_url).netloc)
    found: list[str] = []

    def add(u: str | None) -> None:
        if u and not u.startswith("data:"):
            full = urljoin(page_url, u.strip())
            if full.startswith("http") and full not in found and not (ATS_HOSTS.search(full) and _ats_brand(full)):
                found.append(full)

    if own_site:
        icons = []
        for link in soup.find_all("link", href=True):
            rel = " ".join(link.get("rel") or []).lower()
            if "apple-touch-icon" in rel:
                icons.append((0, -_size(link.get("sizes")), link["href"]))
            elif "icon" in rel:
                svg = (link.get("type") or "").endswith("svg") or link["href"].lower().endswith(".svg")
                size = _size(link.get("sizes"))
                icons.append((1 if svg or size >= 48 else 2, -size, link["href"]))
        for _, _, href in sorted(icons):
            add(href)
    for img in soup.find_all("img"):
        attrs = " ".join(str(img.get(k) or "") for k in ("class", "id", "alt", "src"))
        if _LOGO_HINT.search(attrs):
            add(img.get("src") or img.get("data-src"))
    # in page data, a square logo fits the card's tile better than a wide wordmark
    for m in sorted(_JSON_LOGO.finditer(html), key=lambda m: "square" not in m.group(0).lower()):
        add(m.group(1).encode().decode("unicode_escape") if "\\u" in m.group(1) else m.group(1))
    if own_site:
        add("/favicon.ico")
    return found[:6]


def _ats_brand(url: str) -> bool:
    """An image of the tracking system itself (its logo or favicon), not of the employer: a file on the ATS's own
    host named after it ("lever-logo-refresh.svg", "favicon.ico"); employers' uploads sit under ids, not names."""
    path = urlparse(url).path.lower()
    name = path.rsplit("/", 1)[-1]
    return "favicon" in name or "/sr-logo/" in path or any(b in name for b in _BRANDS)


def website(html: str, page_url: str) -> str | None:
    """The employer's own site named on an ATS board: in the page data, or where the header logo links to."""
    m = _JSON_SITE.search(html)
    if m and not ATS_HOSTS.search(urlparse(m.group(1)).netloc):
        return m.group(1)
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        attrs = " ".join(str(a.get(k) or "") for k in ("class", "id", "aria-label", "title"))
        if _LOGO_HINT.search(attrs) or a.find("img", alt=_LOGO_HINT) or a.find("img", class_=_LOGO_HINT):
            href = urljoin(page_url, a["href"])
            host = urlparse(href).netloc
            if href.startswith("http") and host and not ATS_HOSTS.search(host) and host != urlparse(page_url).netloc:
                return href
    return None


def _size(sizes: str | None) -> int:
    m = re.match(r"(\d+)x\d+", sizes or "")
    return int(m.group(1)) if m else 0


def download(url: str, client: httpx.Client) -> tuple[str, bytes] | None:
    from radar import robots

    if not robots.allowed(url, client):
        return None
    try:
        r = client.get(url)
    except httpx.HTTPError:
        return None
    data = r.content
    ctype = sniff(data) or r.headers.get("content-type", "").split(";")[0].strip().lower()
    least = 100 if ctype == "image/svg+xml" else 300  # smaller rasters are blank or 1-pixel placeholders
    if r.status_code != 200 or ctype not in TYPES or not least <= len(data) <= MAX_BYTES:
        return None
    if ctype == "image/svg+xml" and (b"<script" in data.lower() or b"<svg" not in data[:2000].lower()):
        return None
    return ctype, data


def sniff(data: bytes) -> str | None:
    """The image type from the file's first bytes: some CDNs serve logos as application/octet-stream."""
    head = data[:16]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if b"<svg" in data[:2000].lower():
        return "image/svg+xml"
    return None


def find_logo(page_url: str, client: httpx.Client, depth: int = 0) -> tuple[str, str, bytes] | None:
    """(image url, content type, bytes) of the first usable logo on the page, or None. An ATS board without one
    leads to the employer's own site once."""
    from radar import robots

    if not robots.allowed(page_url, client):
        return None
    try:
        r = client.get(page_url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return None
    for url in candidates(r.text, str(r.url)):
        got = download(url, client)
        if got:
            return url, got[0], got[1]
    site = website(r.text, str(r.url)) if ATS_HOSTS.search(urlparse(str(r.url)).netloc) else None
    return find_logo(site, client, depth + 1) if site and depth == 0 else None


def _client() -> httpx.Client:
    from radar.ratelimit import throttle_request

    return httpx.Client(timeout=settings.http_timeout, follow_redirects=True,
                        headers={"User-Agent": settings.user_agent, "Accept": "text/html,image/*;q=0.9,*/*;q=0.5"},
                        event_hooks={"request": [throttle_request]})


def refresh(session, limit: int = 120, now: datetime | None = None, client: httpx.Client | None = None) -> dict:
    """Look up logos for employers with live postings that have none yet (or whose lookup is due again)."""
    from sqlalchemy import func, select

    from radar.models import CompanyLogo, Posting, Source

    now = now or datetime.utcnow()
    rows = session.execute(
        select(Posting.company, Source.ats, Source.slug, Source.url, func.count(Posting.id).label("n"))
        .join(Source, Source.id == Posting.source_id)
        # only an employer's own source shows that employer's logo (an aggregator's page shows many, or its own)
        .where(Posting.closed_at.is_(None), Posting.is_tech.is_(True), Source.kind == "employer")
        .group_by(Posting.company, Source.ats, Source.slug, Source.url)
    ).all()
    best: dict[str, tuple] = {}
    for company, ats, slug, url, n in rows:
        if company and (company not in best or n > best[company][3]):
            best[company] = (ats, slug, url, n)
    known = {c.company: c for c in session.scalars(select(CompanyLogo))}
    due = [c for c in sorted(best, key=lambda c: -best[c][3])
           if c not in known or known[c].fetched_at < now - timedelta(days=REFRESH_DAYS if known[c].status == "ok"
                                                                         else RETRY_DAYS)][:limit]
    client = client or _client()
    found = missing = 0
    domains = known_domains()
    errors = 0
    for company in due:
        ats, slug, url, _ = best[company]
        page = board_url(ats, slug, url)
        try:
            got = find_logo(page, client) if page else None
            if not got and company in domains:  # boards built entirely in JavaScript (Workday): the employer's site
                page = f"https://{domains[company]}/"
                got = find_logo(page, client)
        except Exception as e:  # a rate-limiter hiccup or an odd page: this employer is tried again next run
            log.warning("logo lookup for %s failed: %s", company, e)
            errors += 1
            if errors >= 10:
                break
            continue
        errors = 0
        row = known.get(company) or CompanyLogo(company=company, slug=slug_of(company))
        row.fetched_at, row.page_url = now, (page or "")[:500]
        if got:
            row.status, row.image_url, row.content_type, row.data = "ok", got[0][:500], got[1], got[2]
            found += 1
        elif row.status != "ok":  # keep an older logo when a later lookup fails
            row.status, row.data = "none", None
            missing += 1
        session.add(row)
        session.commit()
    _known.clear()
    return {"looked_up": len(due), "found": found, "missing": missing, "errors": errors}


def known_domains() -> dict[str, str]:
    """data/company_domains.tsv: employer -> official website, for employers whose board shows no logo."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "data" / "company_domains.tsv"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "\t" in line:
                company, domain = line.split("\t")[:2]
                out[company.strip()] = domain.strip()
    return out


_known: dict[str, dict[str, str]] = {}
_known_at: list[float] = [0.0]


def known(session) -> dict[str, str]:
    """company -> logo slug for every employer with a stored logo (kept 10 minutes in memory)."""
    import time

    from sqlalchemy import select

    from radar.models import CompanyLogo

    if "map" not in _known or time.monotonic() - _known_at[0] > 600:
        _known["map"] = dict(session.execute(select(CompanyLogo.company, CompanyLogo.slug)
                                             .where(CompanyLogo.status == "ok")).all())
        _known_at[0] = time.monotonic()
    return _known["map"]
