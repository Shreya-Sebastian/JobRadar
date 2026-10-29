"""RSS 2.0 feeds of the newest jobs for any search: /feed.xml takes the same filters as /api/postings, /nl/feed.xml
links to the Dutch job pages. For feed readers, job-alert bots and anyone who wants new jobs without an account."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape

from radar.config import settings
from radar.wording import neutral, tagline

# the query parameters a feed title mentions, in this order
_TITLE_PARTS = ("q", "position", "role", "city", "language", "sponsorship", "experience", "remote")


def title_for(params: dict[str, str], lang: str) -> str:
    nl = lang == "nl"
    parts = []
    for key in _TITLE_PARTS:
        v = params.get(key)
        if not v:
            continue
        if key == "q":
            parts.append(f'"{v}"')
        elif key == "language" and v == "en":
            parts.append("geen Nederlands nodig" if nl else neutral("no Dutch needed"))
        elif key == "sponsorship" and v == "true":
            parts.append("visumsponsoring" if nl else "visa sponsorship")
        elif key == "experience":
            parts.append(("ervaring " if nl else "experience ") + v.replace(",", ", "))
        else:
            parts.append(v.replace(",", ", ").replace("_", " "))
    head = f"{settings.site_name}: " + ("nieuwe vacatures" if nl else "new jobs")
    return head + (f" ({'; '.join(parts)})" if parts else "")


def _pub(iso_date: str) -> str:
    d = datetime.fromisoformat(iso_date)
    return format_datetime(d.replace(tzinfo=UTC) if d.tzinfo is None else d)


def render(items: list[dict], params: dict[str, str], lang: str) -> str:
    from urllib.parse import urlencode

    from radar.pages import job_path

    base = settings.site_url.rstrip("/")
    nl = lang == "nl"
    query = urlencode(params)
    home = f"{base}/nl/" if nl else f"{base}/"
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f"<title>{escape(title_for(params, lang))}</title>",
           f"<link>{escape(home + ('#discover?' + query if query else '#discover'))}</link>",
           f'<atom:link href="{escape(base + ("/nl" if nl else "") + "/feed.xml" + ("?" + query if query else ""))}" '
           'rel="self" type="application/rss+xml"/>',
           f"<description>{escape(tagline())}</description>",
           f"<language>{'nl' if nl else 'en'}</language>", "<ttl>60</ttl>"]
    for i in items:
        url = base + job_path(i["id"], i["title"], lang)
        bits = [i["company"]]
        if i.get("city"):
            bits.append(i["city"])
        if i.get("english_only"):
            bits.append("geen Nederlands nodig" if nl else neutral("no Dutch needed"))
        if i.get("visa") is True:
            bits.append("visumsponsoring" if nl else "visa sponsorship")
        if i.get("years_text") or i.get("years") is not None:
            bits.append(f"{i.get('years_text') or str(i['years']) + '+'} {'jaar' if nl else 'yrs'}")
        if i.get("skills"):
            bits.append(", ".join(i["skills"][:6]))
        out.append(f"<item><title>{escape(i['title'])} · {escape(i['company'])}</title><link>{escape(url)}</link>"
                   f'<guid isPermaLink="true">{escape(url)}</guid><pubDate>{_pub(i["posted_at"])}</pubDate>'
                   f"<description>{escape(' · '.join(bits))}</description></item>")
    out.append("</channel></rss>")
    return "\n".join(out)
