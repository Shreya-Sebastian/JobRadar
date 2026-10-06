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
PAGE_BYTES = 800_000  # a page is read this far: the logo and the icons sit in its head and header
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
    found = found[:6]
    if own_site:  # always the last try, also when the page lists icons that are gone (Deloitte's are all 404s)
        add("/favicon.ico")
    return found


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


def _light(colour: str) -> bool:
    """A CSS colour that would vanish on a white plate."""
    c = colour.strip().lower()
    if c in ("white", "#fff", "#ffffff", "#ffffffff"):
        return True
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.85
    m = re.fullmatch(r"rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+).*", c)
    if m:
        r, g, b = (int(x) for x in m.groups())
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.85
    return False


def tidy(ctype: str, data: bytes) -> tuple[str, bytes, bool] | None:
    """Check that a logo shows something and tidy it: (type, bytes, needs a dark plate), or None for a blank one
    (a white square, a fully transparent or tiny image). Raster logos are cropped to what they show and scaled
    to at most 128 px, as PNG; an SVG stays as it is. A logo drawn in white for a dark header gets a dark plate."""
    import io

    if ctype == "image/svg+xml":
        text = data.decode("utf-8", "ignore").lower()
        colours = re.findall(r'(?:fill|stroke|stop-color)\s*[:=]\s*["\']?\s*'
                             r'(#[0-9a-f]{3,8}|white|rgba?\([^)]*\))', text)
        return ctype, data, bool(colours) and all(_light(c) for c in colours)
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGBA")
    except Exception:
        return None
    alpha = im.getchannel("A").point(lambda a: 255 if a > 16 else 0)
    box = alpha.getbbox()
    if not box:
        return None  # fully transparent
    im = im.crop(box)
    if max(im.size) < 12:
        return None
    raw = im.tobytes()
    n = len(raw) // 4
    on_white = on_dark = opaque = 0
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if a <= 32:
            continue
        lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        on_white += lum < 0.82
        on_dark += lum > 0.35
        opaque += a > 240
    if on_white >= 0.03 * n:
        dark = False
    elif 0.03 * n <= on_dark <= 0.85 * n:
        dark = True  # light marks on a transparent background (a near-solid white tile is a placeholder, not a mark)
    else:
        return None  # nothing that would show: a white square, a blank placeholder
    im.thumbnail((128, 128))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return "image/png", out.getvalue(), dark


def download(url: str, client: httpx.Client) -> tuple[str, bytes, bool] | None:
    from radar import robots

    if not robots.allowed(url, client):
        return None
    got = _read(url, client, MAX_BYTES + 1)
    if got is None:
        return None
    status, header_type, data, _ = got
    ctype = sniff(data) or header_type
    least = 100 if ctype == "image/svg+xml" else 300  # smaller rasters are blank or 1-pixel placeholders
    if status != 200 or ctype not in TYPES or not least <= len(data) <= MAX_BYTES:
        return None
    if ctype == "image/svg+xml" and (b"<script" in data.lower() or b"<svg" not in data[:2000].lower()):
        return None
    return tidy(ctype, data)


def _read(url: str, client: httpx.Client, limit: int) -> tuple[int, str, bytes, str] | None:
    """(status, content type, at most `limit` bytes of the body, final url), streamed so that a huge page or file
    never sits in memory whole."""
    try:
        with client.stream("GET", url) as r:
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= limit:
                    break
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            return r.status_code, ctype, b"".join(chunks)[:limit], str(r.url)
    except httpx.HTTPError:
        return None


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
    page = _read(page_url, client, PAGE_BYTES)
    if page is None or page[0] != 200 or "html" not in page[1]:
        # a site that turns automated page reads away often still serves its icons at the standard addresses
        u = urlparse(page_url)
        if ATS_HOSTS.search(u.netloc):
            return None
        for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico"):
            got = download(f"{u.scheme}://{u.netloc}{path}", client)
            if got:
                return f"{u.scheme}://{u.netloc}{path}", *got
        return None
    html, final = page[2].decode("utf-8", "replace"), page[3]
    del page
    for url in candidates(html, final):
        got = download(url, client)
        if got:
            return url, *got
    site = website(html, final) if ATS_HOSTS.search(urlparse(final).netloc) else None
    return find_logo(site, client, depth + 1) if site and depth == 0 else None


def _client() -> httpx.Client:
    from radar.ratelimit import throttle_request

    return httpx.Client(timeout=settings.http_timeout, follow_redirects=True,
                        headers={"User-Agent": settings.user_agent, "Accept": "text/html,image/*;q=0.9,*/*;q=0.5"},
                        event_hooks={"request": [throttle_request]})


def refresh(session, limit: int = 120, now: datetime | None = None, client: httpx.Client | None = None,
            retry_missing: bool = False) -> dict:
    """Look up logos for employers with live postings that have none yet (or whose lookup is due again).
    `retry_missing`: look again now for employers looked up before without a result."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import defer

    from radar.models import CompanyLogo, Posting, Source

    now = now or datetime.utcnow()
    rows = session.execute(
        select(Posting.company, Source.ats, Source.slug, Source.url, Source.kind, Source.company,
               func.count(Posting.id).label("n"))
        .join(Source, Source.id == Posting.source_id)
        .where(Posting.closed_at.is_(None), Posting.is_tech.is_(True))
        .group_by(Posting.company, Source.ats, Source.slug, Source.url, Source.kind, Source.company)
    ).all()
    # a source's page shows the logo of the posting's employer only when it is that employer's own: an employer's
    # board, or an agency or job board posting under its own name. Others (a board listing many employers) give no
    # page; those employers can still get a logo through data/company_domains.tsv.
    best: dict[str, tuple] = {}
    total: dict[str, int] = {}
    for company, ats, slug, url, kind, source_company, n in rows:
        if not company:
            continue
        total[company] = total.get(company, 0) + n
        # "Fridayrecruitment" and "Friday Recruitment" are one name: compare without spaces and dashes
        own = kind == "employer" or slug_of(source_company or "").replace("-", "") == slug_of(company).replace("-", "")
        cand = (ats, slug, url, n) if own else (None, None, None, 0)
        if company not in best or cand[3] > best[company][3]:
            best[company] = cand
    # without the image data: only which employers are done, and when
    known = {c.company: c for c in session.scalars(select(CompanyLogo).options(defer(CompanyLogo.data)))}
    due = [c for c in sorted(best, key=lambda c: -total[c])
           if c not in known or (retry_missing and known[c].status != "ok")
           or known[c].fetched_at < now - timedelta(days=REFRESH_DAYS if known[c].status == "ok" else RETRY_DAYS)]
    due = due[:limit]
    client = client or _client()
    found = missing = 0
    domains = known_domains()
    slugs = {c.slug for c in known.values()}
    errors = 0
    for company in due:
        ats, slug, url, _ = best[company]
        page = board_url(ats, slug, url) if ats else None
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
        row = known.get(company)
        if row is None:
            # "DeepHealth" and "Deephealth" are two names to the radar but one slug: number the second
            slug = base = slug_of(company)
            n = 1
            while slug in slugs:
                n += 1
                slug = f"{base}-{n}"
            slugs.add(slug)
            row = CompanyLogo(company=company, slug=slug)
        row.fetched_at, row.page_url = now, (page or "")[:500]
        if got:
            row.status, row.image_url, row.content_type, row.data, row.dark = "ok", got[0][:500], got[1], got[2], got[3]
            found += 1
        elif row.status != "ok":  # keep an older logo when a later lookup fails
            row.status, row.data = "none", None
            missing += 1
        try:
            session.add(row)
            session.commit()
            session.expunge(row)  # the image is stored; keep it out of memory
        except Exception as e:
            session.rollback()
            log.warning("logo for %s not saved: %s", company, e)
    _known.clear()
    return {"looked_up": len(due), "found": found, "missing": missing, "errors": errors}


def known_domains() -> dict[str, str]:
    """data/company_domains.tsv: employer -> official website, for employers whose board shows no logo."""
    from pathlib import Path

    path = Path(settings.data_dir) / "company_domains.tsv"  # the installed package has no data folder beside it
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "\t" in line:
                company, domain = line.split("\t")[:2]
                out[company.strip()] = domain.strip()
    return out


_known: dict[str, dict[str, dict]] = {}
_known_at: list[float] = [0.0]


def recheck(session) -> dict:
    """Run every stored logo through `tidy` again (no network): blank ones are dropped (status "none", so a later
    lookup tries the next candidate), the rest cropped, scaled and marked for a dark plate where they need one."""
    from sqlalchemy import select

    from radar.models import CompanyLogo

    kept = dropped = 0
    for company in session.scalars(select(CompanyLogo.company).where(CompanyLogo.status == "ok")).all():
        row = session.get(CompanyLogo, company)
        got = tidy(row.content_type or "", row.data or b"")
        if got is None:
            row.status, row.data = "none", None
            dropped += 1
        else:
            row.content_type, row.data, row.dark = got
            kept += 1
        session.commit()
        session.expunge(row)
    _known.clear()
    return {"kept": kept, "dropped": dropped}


def known(session) -> dict[str, dict]:
    """company -> {"slug", "dark"} for every employer with a stored logo (kept 10 minutes in memory)."""
    import time

    from sqlalchemy import select

    from radar.models import CompanyLogo

    if "map" not in _known or time.monotonic() - _known_at[0] > 600:
        _known["map"] = {c: {"slug": s, "dark": bool(d)} for c, s, d in session.execute(
            select(CompanyLogo.company, CompanyLogo.slug, CompanyLogo.dark).where(CompanyLogo.status == "ok")).all()}
        _known_at[0] = time.monotonic()
    return _known["map"]
