import re
import zlib
from datetime import datetime

from bs4 import BeautifulSoup

from radar.adapters.base import Adapter, AdapterError, RawPosting, parse_dt


class AvatureAdapter(Adapter):
    """Avature career portals (Siemens, Siemens Energy, IBM, Avanade), e.g. https://jobs.siemens.com/en_US/externaljobs.

    The portal's search page is rendered on the server: a search for "Netherlands" lists the matching jobs a few
    at a time (paged with folderOffset), each with its title, link and stated location. Only jobs whose location
    names the Netherlands are kept. The job text lives on the job's own page, which can be large, so like the
    SmartRecruiters adapter this one is incremental: the crawler sets `known_urls` (external id -> id of postings
    already stored with their text); those are read again only one crawl in twenty, and are reported in
    `still_listed` so the crawler keeps them open. The slug is the portal address without scheme."""

    ats = "avature"
    MAX_PAGES = 60
    MAX_DETAILS = 300
    REFRESH_EVERY = 20
    _DETAIL = re.compile(r"/JobDetail/(?:[^/?#\"]+/)?(\d+)")
    _NL = re.compile(r"\bnetherlands\b|\bnederland\b", re.I)

    def __init__(self, client=None):
        super().__init__(client)
        self.known_urls: dict[str, str] = {}
        self.still_listed: set[str] = set()

    @staticmethod
    def _base(slug: str) -> str:
        slug = slug.removeprefix("https://").removeprefix("http://").strip("/")
        return f"https://{slug}"

    def _page(self, url: str, **params) -> str:
        resp = self.client.get(url, params=params or None)
        if resp.status_code >= 400:
            raise AdapterError(f"{resp.status_code} for {url}")
        return resp.text

    def listings(self, slug: str, search: str = "Netherlands", pages: int | None = None) -> list[dict]:
        """The jobs a search lists (by default for the Netherlands): {id, title, url, location}, in page order."""
        base = self._base(slug)
        seen: dict[str, dict] = {}
        offset = 0
        for _ in range(pages or self.MAX_PAGES):
            html = self._page(f"{base}/SearchJobs/", search=search, folderOffset=offset)
            soup = BeautifulSoup(html, "lxml")
            found = 0
            for a in soup.find_all("a", href=self._DETAIL):
                title = a.get_text(" ", strip=True)
                m = self._DETAIL.search(a["href"])
                if not m or not title or title.lower() in ("learn more", "meer informatie", "read more"):
                    continue
                job_id = m.group(1)
                if job_id in seen:
                    continue
                item = a
                for _ in range(6):  # climb to the list item that also holds the location line
                    item = item.parent
                    if item is None or "Job ID" in item.get_text(" ", strip=True) or "Location" in str(item)[:4000]:
                        break
                info = " ".join(item.get_text(" ", strip=True).split()) if item is not None else ""
                info = info.replace(title, "", 1).strip()
                location = info.split("•")[0].strip() if "•" in info else info[:120]
                seen[job_id] = {"id": job_id, "title": title, "url": a["href"].split("?")[0], "location": location}
                found += 1
            if not found:
                break
            offset += found
        return list(seen.values())

    def _detail(self, url: str) -> tuple[str | None, datetime | None, str]:
        """The job text, posting date and listed locations from a job's own page."""
        soup = BeautifulSoup(self._page(url), "lxml")
        for el in soup.find_all(["script", "style", "noscript", "form", "nav", "header", "footer"]):
            el.decompose()
        posted = None
        text = " ".join(soup.get_text(" ").split())
        m = re.search(r"Posted since\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", text)
        if m:
            posted = parse_dt(m.group(1))
        # the "Location(s)" field: a label and a value, one line per location ("Den Haag - Zuid-Holland - Netherlands")
        # (labelled "Location(s)" or "Any Siemens location in"; the field's class names it either way)
        locations = ""
        field = soup.find(class_=re.compile(r"\btf_locations\b"))
        if field is None:
            label = soup.find(lambda t: t.name == "div" and t.get_text(strip=True) in ("Location(s)", "Locations"))
            field = label.parent if label is not None else None
        if field is not None:
            label = field.find(class_=re.compile(r"field__label"))
            skip = label.get_text(strip=True) if label is not None else ""
            locations = " | ".join(x for x in field.stripped_strings if x != skip)
        blocks = soup.find_all(class_=re.compile(r"article__content__view"))
        body = max(blocks, key=lambda e: len(e.get_text()), default=None)
        return (str(body) if body is not None else None), posted, locations

    @staticmethod
    def _verdicts():
        """Redis, to remember for a week which multi-location jobs are not in the Netherlands (a forked worker job
        keeps nothing in memory), or None."""
        try:
            from radar.cache import _redis

            return _redis()
        except Exception:
            return None

    def fetch(self, slug: str) -> list[RawPosting]:
        self.still_listed = set()
        salt = datetime.utcnow().timetuple().tm_yday
        details_left = self.MAX_DETAILS
        out: list[RawPosting] = []
        verdicts = self._verdicts()
        for job in self.listings(slug):
            multi = job["location"].lower() == "multiple locations"
            if not multi and not self._NL.search(job["location"]):
                continue  # the search also matches jobs that only mention the Netherlands
            job_id = job["id"]
            if job_id in self.known_urls and (zlib.crc32(job_id.encode()) + salt) % self.REFRESH_EVERY:
                self.still_listed.add(job_id)
                continue
            if multi and verdicts is not None and verdicts.get(f"avature:abroad:{job_id}"):
                continue  # read before: none of its locations is in the Netherlands
            html, posted, locations = None, None, ""
            if details_left > 0:
                details_left -= 1
                try:
                    html, posted, locations = self._detail(job["url"])
                except Exception:
                    html = None  # the crawler keeps any text it already has
            if multi:
                if not self._NL.search(locations):
                    if locations and verdicts is not None:
                        verdicts.setex(f"avature:abroad:{job_id}", 7 * 86400, "1")
                    continue
                location = next((x.strip() for x in locations.split(" | ") if self._NL.search(x)), "Netherlands")
            else:
                location = job["location"]
            out.append(RawPosting(
                external_id=job_id, title=job["title"], url=job["url"], location=location,
                country="NL", description_html=html, posted_at=posted,
            ))
        self.abroad_kept = False
        on, countries = self.abroad_countries()
        if on:
            # once a day: job pages are large (RADAR_ENTRY_LEVEL_COUNTRIES)
            if self.abroad_due(slug):
                out += self._graduates_abroad(slug, {r.external_id for r in out} | self.still_listed, countries)
            else:
                self.abroad_kept = True  # read today already: the crawler keeps the stored ones (radar/crawler.py)
        return out

    MAX_ABROAD_DETAILS = 100

    def _graduates_abroad(self, slug: str, have: set[str], countries: set[str] | None) -> list[RawPosting]:
        """Roles for students and graduates outside the Netherlands: found with the portal's search for graduate
        terms, kept by title before their (large) page is read. The crawler decides what is stored."""
        from radar.extract.rules import entry_level_title
        from radar.normalize import place_country

        found: dict[str, dict] = {}
        for word in self.GRADUATE_SEARCHES:
            try:
                jobs = self.listings(slug, search=word, pages=5)
            except Exception:
                continue
            for job in jobs:
                if job["id"] not in have and job["id"] not in found and not self._NL.search(job["location"]) \
                        and entry_level_title(job["title"]):
                    found[job["id"]] = job
        out: list[RawPosting] = []
        for job in list(found.values())[: self.MAX_ABROAD_DETAILS]:
            try:
                html, posted, locations = self._detail(job["url"])
            except Exception:
                continue
            location = locations.split(" | ")[0] if job["location"].lower() == "multiple locations" else job["location"]
            code = place_country(location) or place_country(locations)
            if not code or code == "NL" or (countries is not None and code not in countries):
                continue
            out.append(RawPosting(external_id=job["id"], title=job["title"], url=job["url"], location=location,
                                  country=code, description_html=html, posted_at=posted))
        return out

    def probe(self, slug: str) -> bool:
        try:
            html = self._page(f"{self._base(slug)}/SearchJobs/")
        except Exception:
            return False
        return bool(self._DETAIL.search(html))
