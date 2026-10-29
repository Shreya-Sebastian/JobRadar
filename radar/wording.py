"""Wording for the entry-level site (RADAR_ENTRY_LEVEL_ONLY): it is for students and graduates anywhere in Europe, so
the site's own English texts do not say "in the Netherlands" or "no Dutch required", and speak of graduates rather
than "no experience". Applied to the interface's text tables only, never to an employer's job text."""

from __future__ import annotations

import re
from pathlib import Path

from radar.config import settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# longest first: "English, no Dutch required" before "no Dutch required" before "Dutch". Facts about who runs the
# site ("Eindhoven, the Netherlands", "the Dutch data protection authority") are left as they are
_PHRASES = [
    (r"\s+in the Netherlands\b", ""),
    (r"\b(?:need|needs|needed)\s+no Dutch\b", "need no local language"),
    (r"\bno Dutch needed\b", "English is enough"),
    (r"\bThe Dutch tech job market\b", "The entry-level tech job market"),
    (r"\bthe Dutch tech job market\b", "the entry-level tech job market"),
    (r"\bEnglish, no Dutch required\b", "English is enough"),
    (r"\b[Nn]o Dutch (?:required|needed)\b", "English is enough"),
    (r"\bDutch required\b", "Local language required"),
    (r"\bDutch-speaking\b", "local-language"),
    (r"\bDutch\b(?!\s+data protection)", "the local language"),
    # graduates, not "no experience": the site is for students and recent graduates, and says so plainly
    (r"\bNo experience asked\b", "Open to graduates"),
    (r"\bno experience needed\b", "open to graduates"),
]
_RX = [(re.compile(p), r) for p, r in _PHRASES]


def neutral(text: str) -> str:
    """The text in country-neutral wording on the entry-level site; unchanged on the Dutch one."""
    if not settings.entry_level_only:
        return text
    for rx, repl in _RX:
        text = rx.sub(repl, text)
    return text


# an element holding the Dutch half of a bilingual page: <section class="nl">, <span class="nl">, <p class="nl">
_NL_SECTION = re.compile(r'<(section|span|p|li|h[1-6]|a|div)\s+class="nl"[^>]*>.*?</\1>\s*', re.S)
# the link to the Dutch page: in the head, the header's NL/EN switch, a landing page's "Nederlands" button
_NL_ALTERNATE = re.compile(r'<link rel="alternate" hreflang="nl"[^>]*>\s*|<div class="langswitch">.*?</div>|'
                           r'<a class="btn" href="\{\{OTHER_PATH\}\}">\{\{OTHER_LANG\}\}</a>')


def template(name: str) -> str:
    """A page template from web/. On the entry-level site: English only (its Dutch half and the link to the Dutch
    page removed) and in country-neutral wording. Templates hold only the site's own text: employer text is put in
    afterwards, so it is never rewritten."""
    html = (WEB_DIR / name).read_text(encoding="utf-8")
    if not settings.entry_level_only:
        return html
    return neutral(_NL_ALTERNATE.sub("", _NL_SECTION.sub("", html)))


def neutralize(table: dict) -> None:
    """Rewrite a table of English interface strings in place, when the site is the entry-level one."""
    if not settings.entry_level_only:
        return
    for k, v in table.items():
        if isinstance(v, str):
            table[k] = neutral(v)
        elif isinstance(v, dict):
            neutralize(v)


ENTRY_TAGLINE = ("Tech jobs for students and recent graduates, read from employers' own career sites: internships, "
                 "graduate programmes, junior roles and PhDs.")
_DUTCH_ALIAS = re.compile(r"netherlands|\bnl\b|vacatures|techjobsnl", re.I)


def tagline() -> str:
    return ENTRY_TAGLINE if settings.entry_level_only else settings.site_tagline


def aliases() -> list[str]:
    """The names people may search for the site by; the entry-level site drops the Dutch ones ("ICT vacatures")."""
    names = [a.strip() for a in settings.site_aliases.split(",") if a.strip()]
    return [a for a in names if not _DUTCH_ALIAS.search(a)] if settings.entry_level_only else names


def country_suffix() -> str:
    """What follows a landing page's English title: " in the Netherlands", or nothing on the entry-level site."""
    return "" if settings.entry_level_only else " in the Netherlands"
