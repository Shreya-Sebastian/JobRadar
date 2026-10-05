"""Server-rendered employer pages: /companies, /company/<slug> and the Dutch /nl/bedrijf/<slug>.

The dashboard is a single page driven by hash routes, which search engines do not index. These plain HTML
pages exist so that "<employer> tech jobs" searches can land on the radar: one page per employer with live
tech postings, listing its open roles with links to the employer's own pages, plus a link into the
interactive view. They are rendered from the same in-memory rows as the API and cached like it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path

from radar.config import settings
from radar.stats import Row

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_SLUG_RX = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_RX.sub("-", name.lower()).strip("-")
    return s or "company"


def live_tech(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.closed_at is None and r.ex.get("is_tech", True)]


# Employer pages with fewer live tech jobs than this are kept for visitors but marked noindex and left out of the
# sitemap: a page with one listing is thin content to a search engine
MIN_INDEXED_POSTINGS = 2


def companies(rows: list[Row]) -> list[tuple[str, str, int]]:
    """(name, slug, live tech postings) for every employer with at least one, most postings first.
    Spellings that share a slug ("SURF" and "Surf") are one employer with one page, shown under the most used
    spelling."""
    counts = Counter(r.company for r in rows)
    by_slug: dict[str, list[tuple[str, int]]] = {}
    for name, n in counts.items():
        by_slug.setdefault(slugify(name), []).append((name, n))
    out = []
    for slug, names in by_slug.items():
        names.sort(key=lambda t: (-t[1], t[0]))
        out.append((names[0][0], slug, sum(n for _, n in names)))
    out.sort(key=lambda t: (-t[2], t[0].lower()))
    return out


def find_company(rows: list[Row], slug: str) -> str | None:
    for name, s, _ in companies(rows):
        if s == slug:
            return name
    return None


def company_rows(rows: list[Row], slug: str) -> list[Row]:
    return [r for r in rows if slugify(r.company) == slug]


def _template(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def _brand(html: str) -> str:
    return (html.replace("{{SITE_NAME}}", escape(settings.site_name))
                .replace("{{SITE_URL}}", settings.site_url.rstrip("/"))
                .replace("{{SITE_TAGLINE}}", escape(settings.site_tagline)))


_TEXT = {
    "en": {
        "title": "{c} jobs in the Netherlands", "subtitle": "tech jobs in the Netherlands",
        "open": "open tech roles", "nodutch": "need no Dutch", "visa": "mention visa sponsorship",
        "employees": "employees", "cities": "Cities", "skills": "Skills asked for",
        "interactive": "Open the interactive view",
        "roles": "Open tech roles",
        "age": "Age", "t_title": "Title", "city": "City", "level": "Level", "skills_col": "Skills",
        "language": "Language", "all": "All employers", "overview": "Overview", "jobs": "Jobs", "market": "Market",
        "employers": "Employers", "empty": "No open tech roles right now.",
        "footer": "lists tech vacancies in the Netherlands read directly from employers' career sites.",
        "desc": "Open tech jobs at {c} in the Netherlands", "desc_in": ", in {x}",
        "desc_skills": ". Most asked skills: {x}",
        "desc_end": ". Read from the employer's own career site; every listing links to the original page.",
        "today": "today", "yesterday": "yesterday", "days": "{d}d ago",
    },
    "nl": {
        "title": "{c} vacatures in Nederland", "subtitle": "techvacatures in Nederland",
        "open": "open techvacatures", "nodutch": "zonder Nederlands", "visa": "noemen visumsponsoring",
        "employees": "medewerkers", "cities": "Steden", "skills": "Gevraagde skills",
        "interactive": "Open de interactieve weergave",
        "roles": "Open techvacatures",
        "age": "Leeftijd", "t_title": "Functie", "city": "Plaats", "level": "Niveau", "skills_col": "Skills",
        "language": "Taal", "all": "Alle werkgevers", "overview": "Overzicht", "jobs": "Vacatures",
        "market": "Markt", "employers": "Werkgevers",
        "empty": "Op dit moment geen open techvacatures.",
        "footer": "toont techvacatures in Nederland, rechtstreeks van de carrièresites van werkgevers.",
        "desc": "Openstaande techvacatures bij {c} in Nederland", "desc_in": ", in {x}",
        "desc_skills": ". Meest gevraagde skills: {x}",
        "desc_end": ". Rechtstreeks van de carrièresite van de werkgever; elke vacature linkt naar de "
                    "originele pagina.",
        "today": "vandaag", "yesterday": "gisteren", "days": "{d} dagen geleden",
    },
}
TAGLINE_NL = "Tech-, software-, data- en IT-vacatures in Nederland, rechtstreeks van de sites van werkgevers."


def company_path(slug: str, lang: str = "en") -> str:
    return f"/nl/bedrijf/{slug}" if lang == "nl" else f"/company/{slug}"


def _age(r: Row, lang: str = "en") -> str:
    t = _TEXT[lang]
    d = (datetime.utcnow() - (r.posted_at or r.first_seen)).days
    return t["today"] if d <= 0 else t["yesterday"] if d == 1 else t["days"].format(d=d)


def render_company(name: str, rows: list[Row], lang: str = "en") -> str:
    from radar.seo import city_nl

    t = _TEXT["nl" if lang == "nl" else "en"]
    slug = slugify(name)
    mine = sorted(company_rows(rows, slug), key=lambda r: r.posted_at or r.first_seen, reverse=True)
    base = settings.site_url.rstrip("/")

    def city(c):
        return city_nl(c) if lang == "nl" and c else c

    cities = Counter(city(r.city) for r in mine if r.city)
    skills = Counter(s for r in mine for s in r.skills)
    english = sum(1 for r in mine if r.ex.get("english_only"))
    visa = sum(1 for r in mine if r.ex.get("visa_sponsorship") is True)
    headcount = next((r.employees for r in mine if r.employees), None)
    rows_html = "".join(
        f"<tr><td class=\"muted\">{_age(r, lang)}</td>"
        f"<td><a href=\"{job_path(r.id, r.title, lang)}\">{escape(r.title)}</a></td>"
        f"<td>{escape(city(r.city) or ('Remote' if r.remote else ''))}</td>"
        f"<td>{escape(r.ex.get('seniority') or '') if r.ex.get('seniority') != 'unknown' else ''}</td>"
        f"<td>{escape(', '.join(r.skills[:6]))}</td>"
        f"<td>{'EN' if r.ex.get('english_only') else 'NL'}</td></tr>"
        for r in mine
    )
    desc = (t["desc"].format(c=name)
            + (t["desc_in"].format(x=", ".join(c for c, _ in cities.most_common(3))) if cities else "")
            + (t["desc_skills"].format(x=", ".join(s for s, _ in skills.most_common(5))) if skills else "")
            + t["desc_end"])
    self_path = company_path(slug, lang)
    ld = {
        "@context": "https://schema.org", "@type": "CollectionPage", "inLanguage": lang,
        "name": t["title"].format(c=name), "url": base + self_path, "description": desc,
        "isPartOf": {"@type": "WebSite", "name": settings.site_name, "url": base},
        "about": {"@type": "Organization", "name": name,
                  **({"numberOfEmployees": {"@type": "QuantitativeValue", "value": headcount}} if headcount else {})},
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(mine), "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": base + job_path(r.id, r.title, lang), "name": r.title}
            for i, r in enumerate(mine[:50])
        ]},
    }
    sep = "." if lang == "nl" else ","
    emp_kpi = (f'<div class="kpi"><b>{f"{headcount:,}".replace(",", sep)}</b><span>{t["employees"]}</span></div>'
               if headcount else "")
    html = _template("company.html")
    for key, value in t.items():
        html = html.replace("{{L_" + key.upper() + "}}", escape(value.replace("{c}", name)))
    return _brand(html).replace("{{LANG}}", "nl" if lang == "nl" else "en") \
        .replace("{{TITLE}}", escape(t["title"].format(c=name))) \
        .replace("{{TAGLINE}}", escape(TAGLINE_NL if lang == "nl" else settings.site_tagline)) \
        .replace("{{HOME}}", "/nl/" if lang == "nl" else "/") \
        .replace("{{COMPANIES}}", "/companies") \
        .replace("{{SELF_PATH}}", self_path) \
        .replace("{{EN_PATH}}", company_path(slug, "en")).replace("{{NL_PATH}}", company_path(slug, "nl")) \
        .replace("{{EN_ON}}", " on" if lang != "nl" else "").replace("{{NL_ON}}", " on" if lang == "nl" else "") \
        .replace("{{COMPANY}}", escape(name)) \
        .replace("{{SECTOR}}", escape(_sector_label(mine, lang))) \
        .replace("{{ROBOTS}}", "" if len(mine) >= MIN_INDEXED_POSTINGS
                 else '<meta name="robots" content="noindex, follow">') \
        .replace("{{COUNT}}", str(len(mine))) \
        .replace("{{EMPLOYEES_KPI}}", emp_kpi) \
        .replace("{{CITIES}}", escape(", ".join(f"{c} ({n})" for c, n in cities.most_common(6))) or "-") \
        .replace("{{SKILLS}}", "".join(f"<span class=\"chip\">{escape(s)} <span class=\"muted\">{n}</span></span>"
                                      for s, n in skills.most_common(15)) or "-") \
        .replace("{{ENGLISH}}", str(english)).replace("{{VISA}}", str(visa)) \
        .replace("{{ROWS}}", rows_html or f"<tr><td colspan=\"6\" class=\"muted\">{escape(t['empty'])}</td></tr>") \
        .replace("{{DESCRIPTION}}", escape(desc)) \
        .replace("{{JSONLD}}", json.dumps(ld, ensure_ascii=False)) \
        .replace("{{COMPANY_URLENC}}", escape(name).replace(" ", "%20"))


# ---------- one page per listing: /job/<id>/<slug> and /nl/vacature/<id>/<slug> ----------

def job_path(posting_id: int, title: str, lang: str = "en") -> str:
    return f"/nl/vacature/{posting_id}/{slugify(title)}" if lang == "nl" else f"/job/{posting_id}/{slugify(title)}"


_JOB_TEXT = {
    "en": {
        "overview": "Overview", "jobs": "Jobs", "market": "Market", "employers": "Employers",
        "back": "← All jobs", "apply": "View and apply on", "more_at": "More jobs at",
        "description": "Job description",
        "credit": "From {c}'s job posting, as published on {h}. The text belongs to {c}; read the original at {u}.",
        "closed": "This vacancy is no longer open.", "open_days": "Open for {n} days", "open_new": "New on the radar",
        "reposted": "posted before: {n}× since {d}", "similar": "Similar jobs",
        "posted": "posted {d}", "remote": "Remote",
        "footer": "lists tech vacancies in the Netherlands read directly from employers' career sites.",
        "title": "{t} at {c}", "in": " in {x}",
        "no_text": "The employer's page has no description that could be read. See the original posting.",
        "level": {"intern": "Internship", "trainee": "Trainee / graduate programme", "junior": "Junior",
                  "medior": "Medior", "senior": "Senior", "lead": "Lead", "staff": "Staff / principal",
                  "manager": "Manager"},
        "years": "{n}+ years of experience", "years_text": "{n} years of experience",
        "one_year": "1 year of experience",
        "no_exp": "No experience asked",
        "degree": {"phd": "PhD", "msc": "Master's degree", "bsc": "Bachelor's degree", "hbo": "HBO degree",
                   "mbo": "MBO", "none": "No degree asked"},
        "english": "English, no Dutch required", "dutch": "Dutch required",
        "policy": {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"},
        "visa_yes": "Visa sponsorship mentioned", "visa_no": "No visa sponsorship",
        "enrol": "For enrolled students", "salary": "€{lo} – €{hi} a year", "salary_from": "from €{lo} a year",
        "skills": "Skills asked for", "nice": "Nice to have", "related": "More like this",
        "home_crumb": "Tech jobs", "skills_short": "Skills",
    },
    "nl": {
        "overview": "Overzicht", "jobs": "Vacatures", "market": "Markt", "employers": "Werkgevers",
        "back": "← Alle vacatures", "apply": "Bekijk en solliciteer op", "more_at": "Meer vacatures bij",
        "description": "Vacaturetekst",
        "credit": "Uit de vacature van {c}, zoals gepubliceerd op {h}. De tekst is van {c}; lees het origineel op {u}.",
        "closed": "Deze vacature is niet meer open.", "open_days": "{n} dagen open", "open_new": "Nieuw op de radar",
        "reposted": "eerder geplaatst: {n}× sinds {d}", "similar": "Vergelijkbare vacatures",
        "posted": "geplaatst {d}", "remote": "Op afstand",
        "footer": "toont techvacatures in Nederland, rechtstreeks van de carrièresites van werkgevers.",
        "title": "{t} bij {c}", "in": " in {x}",
        "no_text": "De pagina van de werkgever heeft geen leesbare vacaturetekst. Bekijk de originele vacature.",
        "level": {"intern": "Stage", "trainee": "Traineeship / starterprogramma", "junior": "Junior",
                  "medior": "Medior", "senior": "Senior", "lead": "Lead", "staff": "Staff / principal",
                  "manager": "Manager"},
        "years": "{n}+ jaar ervaring", "years_text": "{n} jaar ervaring", "one_year": "1 jaar ervaring",
        "no_exp": "Geen ervaring gevraagd",
        "degree": {"phd": "PhD", "msc": "Master", "bsc": "Bachelor", "hbo": "Hbo", "mbo": "Mbo",
                   "none": "Geen opleiding gevraagd"},
        "english": "Engels, geen Nederlands nodig", "dutch": "Nederlands vereist",
        "policy": {"remote": "Op afstand", "hybrid": "Hybride", "onsite": "Op locatie"},
        "visa_yes": "Visumsponsoring genoemd", "visa_no": "Geen visumsponsoring",
        "enrol": "Voor ingeschreven studenten", "salary": "€{lo} – €{hi} per jaar",
        "salary_from": "vanaf €{lo} per jaar",
        "skills": "Gevraagde skills", "nice": "Pluspunten", "related": "Vergelijkbare vacatures",
        "home_crumb": "Techvacatures", "skills_short": "Skills",
    },
}


def _body(p) -> str:
    """The description as HTML: the employer's formatting when the source gave it (cleaned again here, so nothing
    stored can bypass the allowlist), else paragraphs, lists and headings made from the plain text."""
    from radar.htmlclean import clean_html, drop_leading, text_to_html

    body = clean_html(p.description_html) or text_to_html(p.description or "")
    return drop_leading(body, (p.title, p.company))


_FULL_TIME = re.compile(r"\bfull[- ]?time\b|\bfulltime\b|\b(?:36|38|40)\s*(?:uur|hours)\b", re.I)
_PART_TIME = re.compile(r"\bpart[- ]?time\b|\bparttime\b|\b(?:16|20|24|28|32)\s*(?:-\s*\d+\s*)?(?:uur|hours)\b", re.I)


def employment_type(ex: dict, text: str) -> list[str] | None:
    """schema.org employmentType from the level and the hours the posting names; None when it does not say."""
    if ex.get("seniority") == "intern":
        return ["INTERN"]
    kinds = [k for k, rx in (("FULL_TIME", _FULL_TIME), ("PART_TIME", _PART_TIME)) if rx.search(text or "")]
    return kinds or None


def render_job(p, lang: str = "en", sector: str | None = None, related: list[tuple[str, str]] | None = None,
               history: dict | None = None, similar: list | None = None) -> str:
    """`related`: (path, title) of the landing pages this job appears on, most specific first. `history`: how long
    it has been open and whether the employer listed the same title before (job_history); `similar`: open jobs of
    the same kind elsewhere (similar_jobs)."""
    """The listing page: what the radar read from the posting, then the employer's own text with credit and links
    back to the original. `p` is a radar.models.Posting."""
    from urllib.parse import urlparse

    from radar.seo import city_nl
    from radar.stats import experience_band

    lang = "nl" if lang == "nl" else "en"
    t = _JOB_TEXT[lang]
    ex = p.extraction or {}
    base = settings.site_url.rstrip("/")
    host = urlparse(p.url).netloc.removeprefix("www.") or p.url
    sep = "." if lang == "nl" else ","

    def money(v):
        return f"{int(v):,}".replace(",", sep)

    city = (city_nl(p.city) if lang == "nl" else p.city) if p.city else (t["remote"] if p.remote else "")
    when = p.posted_at or p.first_seen
    meta = " · ".join(x for x in (
        f'<a href="{company_path(slugify(p.company), lang)}">{escape(p.company)}</a>',
        escape(city) if city else "",
        escape(t["posted"].format(d=when.strftime("%d-%m-%Y"))) if when else "",
    ) if x)

    facts = []
    if ex.get("seniority") in t["level"]:
        facts.append(t["level"][ex["seniority"]])
    years = ex.get("years_experience")
    label = ex.get("years_experience_text")
    if years and label:
        facts.append(t["one_year"] if label == "1" else t["years_text"].format(n=label.replace("-", "–")))
    elif years:
        facts.append(t["years"].format(n=years))
    elif experience_band(ex, p.title) == "none":
        facts.append(t["no_exp"])
    if ex.get("degree_required") in t["degree"]:
        facts.append(t["degree"][ex["degree_required"]])
    facts.append(t["english"] if ex.get("english_only") else t["dutch"] if ex.get("dutch_required") else "")
    facts.append(t["policy"].get(ex.get("remote_policy") or "", ""))
    if ex.get("visa_sponsorship") is True:
        facts.append(t["visa_yes"])
    elif ex.get("visa_sponsorship") is False:
        facts.append(t["visa_no"])
    if ex.get("enrollment_required") is True:
        facts.append(t["enrol"])
    lo, hi = ex.get("salary_min_eur"), ex.get("salary_max_eur")
    if lo and hi and hi > lo:
        facts.append(t["salary"].format(lo=money(lo), hi=money(hi)))
    elif lo:
        facts.append(t["salary_from"].format(lo=money(lo)))
    if sector and sector != "other":
        from radar.sectors import LABELS

        facts.insert(0, LABELS[lang][sector])
    from radar.positions import LABELS as POSITION_LABELS
    from radar.positions import position

    kind = position(p.title)
    if kind != "other":
        facts.insert(0, POSITION_LABELS[lang][kind])
    facts_html = "".join(f'<span class="chip">{escape(f)}</span>' for f in facts if f)

    req, nice = ex.get("skills_required") or [], ex.get("skills_nice") or []
    skills_html = ""
    if req:
        skills_html += f'<p class="small mb-1"><b>{t["skills"]}</b></p><div class="chips">' + "".join(
            f'<span class="chip have">{escape(s)}</span>' for s in req) + "</div>"
    if nice:
        skills_html += f'<p class="small mb-1"><b>{t["nice"]}</b></p><div class="chips">' + "".join(
            f'<span class="chip">{escape(s)}</span>' for s in nice) + "</div>"

    text = p.description or ""
    title = t["title"].format(t=p.title, c=p.company)
    summary = " ".join(text.split())
    head = title + (t["in"].format(x=city) if city else "")
    shown = [f for f in facts if f][:4]
    if shown or req:
        desc = head + (": " + ", ".join(shown) if shown else "") + "."
        if req:
            desc += f" {t['skills_short']}: " + ", ".join(req[:4]) + "."
        if len(desc) > 160:
            desc = desc[:157].rstrip(" ,.:") + "…"
    else:
        desc = (head + ". " + summary)[:155].rstrip() + ("…" if summary else "")
    url_link = f'<a href="{escape(p.url)}" rel="noopener" target="_blank">{escape(host)}</a>'
    credit = escape(t["credit"]).replace("{c}", escape(p.company)).replace("{h}", escape(host)).replace("{u}", url_link)
    self_path = job_path(p.id, p.title, lang)
    closed = p.closed_at is not None

    # schema.org JobPosting, so the page can appear in Google's job search; left out once the vacancy is closed
    ld = ""
    if not closed and text:
        posting = {
            "@context": "https://schema.org", "@type": "JobPosting", "title": p.title,
            "description": _body(p), "datePosted": when.date().isoformat() if when else None,
            "hiringOrganization": {"@type": "Organization", "name": p.company},
            "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressCountry": "NL",
                                                          **({"addressLocality": p.city} if p.city else {})}},
            "url": base + self_path, "identifier": {"@type": "PropertyValue", "name": p.company,
                                                     "value": str(p.external_id)},
            "directApply": False,
        }
        if p.valid_through:
            posting["validThrough"] = p.valid_through.isoformat()
        if ex.get("remote_policy") == "remote" or p.remote:
            posting["jobLocationType"] = "TELECOMMUTE"
            posting["applicantLocationRequirements"] = {"@type": "Country", "name": "NL"}
        kinds = employment_type(ex, text)
        if kinds:
            posting["employmentType"] = kinds if len(kinds) > 1 else kinds[0]
        if lo:
            posting["baseSalary"] = {"@type": "MonetaryAmount", "currency": "EUR", "value": {
                "@type": "QuantitativeValue", "unitText": "YEAR", "minValue": lo, **({"maxValue": hi} if hi else {})}}
        posting = {k: v for k, v in posting.items() if v is not None}
        ld = ('<script type="application/ld+json">' + json.dumps(posting, ensure_ascii=False).replace("</", "<\\/")
              + "</script>")
    # breadcrumbs: the site, the most specific landing page this job is on, the job
    crumbs = [(t["home_crumb"], base + ("/nl/" if lang == "nl" else "/"))]
    if related:
        crumbs.append((related[0][1], base + related[0][0]))
    crumbs.append((p.title, base + self_path))
    ld += ('<script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u}
                            for i, (n, u) in enumerate(crumbs)]}, ensure_ascii=False).replace("</", "<\\/")
           + "</script>")
    history_html = ""
    if history:
        bits = [t["open_days"].format(n=history["open_days"]) if history["open_days"] > 1 else t["open_new"]]
        if history.get("earlier"):
            bits.append(t["reposted"].format(n=history["earlier"], d=history["first"].strftime("%d-%m-%Y")))
        history_html = f'<p class="small muted job-history">{escape(" · ".join(bits))}</p>'
    similar_html = ""
    if similar:
        from radar.seo import city_nl

        items = "".join(
            f'<li><a href="{job_path(r.id, r.title, lang)}">{escape(r.title)}</a>'
            f'<span class="muted"> · {escape(r.company)}'
            f'{" · " + escape(city_nl(r.city) if lang == "nl" else r.city) if r.city else ""}</span></li>'
            for r in similar)
        similar_html = (f'<section class="card job-similar"><h2>{escape(t["similar"])}</h2>'
                        f'<ul class="similar-list">{items}</ul></section>')
    related_html = ""
    if related:
        related_html = (f'<p class="small job-related"><b>{escape(t["related"])}:</b> '
                        + " · ".join(f'<a href="{escape(path)}">{escape(name)}</a>' for path, name in related[:5])
                        + "</p>")

    html = _template("job.html")
    for key, value in t.items():
        if isinstance(value, str):
            html = html.replace("{{L_" + key.upper() + "}}", escape(value))
    return _brand(html).replace("{{LANG}}", lang) \
        .replace("{{TITLE}}", escape(title)) \
        .replace("{{DESCRIPTION}}", escape(desc)) \
        .replace("{{SELF_PATH}}", self_path) \
        .replace("{{EN_PATH}}", job_path(p.id, p.title, "en")).replace("{{NL_PATH}}", job_path(p.id, p.title, "nl")) \
        .replace("{{EN_ON}}", " on" if lang == "en" else "").replace("{{NL_ON}}", " on" if lang == "nl" else "") \
        .replace("{{ROBOTS}}", '<meta name="robots" content="noindex, follow">' if closed else "") \
        .replace("{{JSONLD}}", ld) \
        .replace("{{HOME}}", "/nl/" if lang == "nl" else "/") \
        .replace("{{COMPANIES}}", "/companies") \
        .replace("{{CLOSED}}", f'<p class="card bad">{escape(t["closed"])}</p>' if closed else "") \
        .replace("{{JOB_TITLE}}", escape(p.title)) \
        .replace("{{META}}", meta) \
        .replace("{{HISTORY}}", history_html) \
        .replace("{{SIMILAR}}", similar_html) \
        .replace("{{FACTS}}", facts_html) \
        .replace("{{SKILLS}}", skills_html) \
        .replace("{{URL}}", escape(p.url)) \
        .replace("{{HOST}}", escape(host)) \
        .replace("{{COMPANY_PATH}}", company_path(slugify(p.company), lang)) \
        .replace("{{COMPANY}}", escape(p.company)) \
        .replace("{{CREDIT}}", credit) \
        .replace("{{RELATED}}", related_html) \
        .replace("{{TEXT}}", _body(p) or f'<p class="muted">{escape(t["no_text"])}</p>')


def job_history(session, p) -> dict:
    """How long the vacancy has been open, and how often the employer listed the same title before (a repost:
    an earlier posting with that title, now closed or merged away)."""
    import re
    from datetime import datetime

    from sqlalchemy import func, select

    from radar.models import Posting

    start = p.posted_at or p.first_seen
    end = p.closed_at or datetime.utcnow()
    norm = re.sub(r"\s*\((?:m|f|v|x|d)(?:/[mfvxd])*\)\s*$", "", p.title.strip(), flags=re.I).lower()
    earlier = session.execute(
        select(func.count(), func.min(func.coalesce(Posting.posted_at, Posting.first_seen)))
        .where(Posting.company == p.company, Posting.id != p.id, Posting.duplicate_of.is_(None),
               func.lower(Posting.title).startswith(norm, autoescape=True), Posting.first_seen < p.first_seen,
               Posting.closed_at.is_not(None))).one()
    return {"open_days": max(0, (end - start).days), "earlier": earlier[0] or 0, "first": earlier[1]}


def similar_jobs(rows: list[Row], p, limit: int = 5) -> list[Row]:
    """Open jobs of the same position type at other employers, the same city first, newest first."""
    from radar.positions import position

    kind = position(p.title)
    if kind == "other":
        return []
    same = [r for r in rows if r.closed_at is None and r.id != p.id and r.company != p.company and r.position == kind]
    same.sort(key=lambda r: (r.city != p.city, -(r.posted_at or r.first_seen).timestamp()))
    seen, out = set(), []
    for r in same:
        if r.company not in seen:  # one job per employer, for variety
            seen.add(r.company)
            out.append(r)
        if len(out) == limit:
            break
    return out


REPORT_TEXT = {'en': {'title': 'The Dutch tech job market this week',
                      'doc': 'Dutch tech job market this week',
                      'desc': 'A weekly look at tech jobs in the Netherlands: how many were posted, the skills '
                              'employers ask for, how many need no Dutch, and where the work is. Updated every day.',
                      'range': 'Jobs posted {a} to {b}',
                      'new': 'new jobs',
                      'before': 'the week before: {n}',
                      'no_dutch': 'need no Dutch',
                      'visa': 'mention visa sponsorship',
                      'remote': 'remote or hybrid',
                      'salary': 'median stated salary',
                      'salary_n': '{n} postings state one',
                      'skills': 'Skills asked for',
                      'positions': 'Most posted positions',
                      'cities': 'Where',
                      'exp': 'Experience asked',
                      'change': 'change in share against the week before',
                      'jobs': 'See the jobs',
                      'home': '/',
                      'method': "Counted from employers' own career sites, by the date each job was posted, closed "
                                'jobs included. No employer is singled out.',
                      'bands': {'none': 'none asked',
                                '1': 'up to 1 year',
                                '2-3': '2-3 years',
                                '4-5': '4-5 years',
                                '6+': '6+ years',
                                'unspecified': 'not stated'}},
               'nl': {'title': 'De Nederlandse techarbeidsmarkt deze week',
                      'doc': 'Techarbeidsmarkt deze week',
                      'desc': 'Een wekelijkse blik op techvacatures in Nederland: hoeveel er geplaatst zijn, welke '
                              'skills werkgevers vragen, hoeveel geen Nederlands vereisen en waar het werk is. '
                              'Dagelijks bijgewerkt.',
                      'range': 'Vacatures geplaatst van {a} tot {b}',
                      'new': 'nieuwe vacatures',
                      'before': 'de week ervoor: {n}',
                      'no_dutch': 'zonder Nederlands',
                      'visa': 'noemen visumsponsoring',
                      'remote': 'thuis of hybride',
                      'salary': 'mediaan vermeld salaris',
                      'salary_n': '{n} vacatures noemen er een',
                      'skills': 'Gevraagde skills',
                      'positions': 'Meest geplaatste functies',
                      'cities': 'Waar',
                      'exp': 'Gevraagde ervaring',
                      'change': 'verschil in aandeel met de week ervoor',
                      'jobs': 'Bekijk de vacatures',
                      'home': '/nl/',
                      'method': 'Geteld op de carrièresites van werkgevers zelf, op de datum waarop elke vacature '
                                'geplaatst is, gesloten vacatures meegeteld. Er wordt geen werkgever uitgelicht.',
                      'bands': {'none': 'geen',
                                '1': 'tot 1 jaar',
                                '2-3': '2-3 jaar',
                                '4-5': '4-5 jaar',
                                '6+': '6+ jaar',
                                'unspecified': 'niet vermeld'}}}


def render_report(rep: dict, lang: str) -> str:
    """/market-report and /nl/marktrapport: the weekly market report as a page to share and to find."""
    from datetime import date

    from radar.positions import LABELS as POSITION_LABELS
    from radar.seo import city_nl

    lang = "nl" if lang == "nl" else "en"
    t = REPORT_TEXT[lang]
    sep = "." if lang == "nl" else ","

    def num(n) -> str:
        return f"{int(n):,}".replace(",", sep)

    def pct(v) -> str:
        return "-" if v is None else f"{round(v * 100)}%"

    def day(iso: str) -> str:
        d = date.fromisoformat(iso)
        return d.strftime("%d-%m-%Y") if lang == "nl" else d.strftime("%d %b %Y").lstrip("0")

    before = t["before"].format(n=num(rep["new_before"])) if rep["new_before"] is not None else ""
    tiles = [(num(rep["new"]), t["new"], before),
             (pct(rep["no_dutch"]), t["no_dutch"], ""), (num(rep["visa"]), t["visa"], ""),
             (pct(rep["remote"]), t["remote"], "")]
    if rep["salary_median"]:
        tiles.append((f"€{num(round(rep['salary_median'], -3))}", t["salary"], t["salary_n"].format(n=rep["salary_n"])))
    tiles_html = "".join(f'<div class="kpi"><b>{escape(v)}</b><span>{escape(label)}</span>'
                         + (f'<span class="kpi-sub">{escape(sub)}</span>' if sub else "") + "</div>"
                         for v, label, sub in tiles)

    def bars(items) -> str:
        top = max((n for _, n, _ in items), default=1) or 1
        return "".join(f'<div class="rbar"><span>{escape(k)}</span><i style="width:{round(n / top * 100)}%"></i>'
                       f'<b>{escape(v)}</b></div>' for k, n, v in items)

    def change(v) -> str:
        if v is None:
            return ""
        arrow = "▲" if v > 0.004 else "▼" if v < -0.004 else "="
        tone = "ok" if v > 0.004 else "bad" if v < -0.004 else "muted"
        return f' <small class="{tone}" title="{escape(t["change"])}">{arrow} {abs(round(v * 100))}</small>'

    skills = bars([(s["skill"], s["share"], pct(s["share"])) for s in rep["skills"]])
    skills = "".join(line.replace("</b></div>", "</b>" + change(s.get("change")) + "</div>", 1)
                     for line, s in zip(skills.split('<div class="rbar">')[1:], rep["skills"], strict=True)
                     for line in ['<div class="rbar">' + line])
    labels = POSITION_LABELS[lang]
    positions = bars([(labels.get(p["key"], p["key"]), p["count"], num(p["count"])) for p in rep["positions"]])
    cities = bars([(city_nl(c["key"]) if lang == "nl" else c["key"], c["count"], num(c["count"]))
                   for c in rep["cities"]])
    order = ["none", "1", "2-3", "4-5", "6+", "unspecified"]
    exp = bars([(t["bands"][k], rep["experience"].get(k, 0), pct(rep["experience"].get(k, 0)))
                for k in order if k in rep["experience"]])
    base = settings.site_url.rstrip("/")
    self_path = "/nl/marktrapport" if lang == "nl" else "/market-report"
    ld = {"@context": "https://schema.org", "@type": "Report", "name": t["title"], "inLanguage": lang,
          "url": base + self_path, "datePublished": rep["to"], "description": t["desc"],
          "publisher": {"@type": "Organization", "name": settings.site_name, "url": base}}
    return _brand(_template("report.html")) \
        .replace("{{LANG}}", lang).replace("{{DOC_TITLE}}", escape(t["doc"])).replace("{{TITLE}}", escape(t["title"])) \
        .replace("{{DESCRIPTION}}", escape(t["desc"])).replace("{{SELF_PATH}}", self_path) \
        .replace("{{EN_PATH}}", "/market-report").replace("{{NL_PATH}}", "/nl/marktrapport") \
        .replace("{{HOME}}", t["home"]) \
        .replace("{{RANGE}}", escape(t["range"].format(a=day(rep["from"]), b=day(rep["to"])))) \
        .replace("{{TILES}}", tiles_html) \
        .replace("{{L_SKILLS}}", escape(t["skills"])).replace("{{SKILLS}}", skills) \
        .replace("{{L_POSITIONS}}", escape(t["positions"])).replace("{{POSITIONS}}", positions) \
        .replace("{{L_CITIES}}", escape(t["cities"])).replace("{{CITIES}}", cities) \
        .replace("{{L_EXP}}", escape(t["exp"])).replace("{{EXP}}", exp) \
        .replace("{{L_JOBS}}", escape(t["jobs"])).replace("{{METHOD}}", escape(t["method"])) \
        .replace("{{JSONLD}}", json.dumps(ld, ensure_ascii=False))


def _sector_label(rows: list[Row], lang: str) -> str:
    from radar.sectors import LABELS

    sector = next((r.sector for r in rows if r.sector and r.sector != "other"), None)
    return LABELS["nl" if lang == "nl" else "en"][sector] if sector else ""


def render_companies(rows: list[Row]) -> str:
    items = companies(rows)
    base = settings.site_url.rstrip("/")
    lis = "".join(f"<li><a href=\"/company/{slug}\">{escape(name)}</a> <span class=\"muted\">{n}</span></li>"
                  for name, slug, n in items)
    desc = ("Employers with open tech jobs in the Netherlands, read from their own career sites and "
            "updated daily.")
    ld = {"@context": "https://schema.org", "@type": "CollectionPage",
          "name": "Employers hiring for tech in the Netherlands", "url": f"{base}/companies", "description": desc,
          "isPartOf": {"@type": "WebSite", "name": settings.site_name, "url": base}}
    return _brand(_template("companies.html")).replace("{{COUNT}}", str(len(items))) \
        .replace("{{ITEMS}}", lis).replace("{{DESCRIPTION}}", escape(desc)) \
        .replace("{{JSONLD}}", json.dumps(ld, ensure_ascii=False))
