"""Workday adapter. Slug format: "{tenant}.wd{n}/{site}", e.g. "nxp.wd3/careers".

Workday career sites expose an unauthenticated JSON API used by their own front end:
  POST https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs   (listing, paged, faceted)
  GET  https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}   (detail)
We read the country facet from the first page, restrict the listing to the Netherlands, then fetch details
only for those postings, so a 1,000-job corporate board costs a few dozen requests, not a thousand.
"""

from __future__ import annotations

import re

from radar.adapters.base import Adapter, AdapterError, RawPosting, SourceNotFound, parse_dt

_SLUG = re.compile(r"^([a-z0-9-]+)\.(wd\d+)/([A-Za-z0-9_-]+)$", re.I)
_NL = re.compile(r"netherlands|nederland", re.I)


def _find_nl_facet(facets: list, param: str | None = None, label: str = "") -> tuple[str | None, str | None, int]:
    """Return (facetParameter, value id, count) for the Netherlands country value.

    Workday tenants differ: some expose a flat `Location_Country` facet, others nest it as
    locationMainGroup -> "Country/Region" (facetParameter locationHierarchy1) -> values. Walk everything, but
    only accept a match inside a country-like facet (by parameter name or by the parent's label) so
    "Netherlands - Remote Based" under City does not win."""
    best: tuple[str | None, str | None, int] = (None, None, 0)
    for facet in facets or []:
        fp = facet.get("facetParameter") or param
        for v in facet.get("values") or []:
            desc = (v.get("descriptor") or "").strip()
            if v.get("values") or v.get("facetParameter"):
                found = _find_nl_facet([v], v.get("facetParameter") or fp, desc)
                if found[1] and found[2] >= best[2]:
                    best = found
                continue
            country_like = re.search(r"country", f"{fp or ''} {label}", re.I)
            if desc.lower() in ("netherlands", "the netherlands", "nederland") and country_like:
                if int(v.get("count") or 0) >= best[2]:
                    best = (fp, v.get("id"), int(v.get("count") or 0))
    return best


def _is_dutch_place(text: str) -> bool:
    from radar.normalize import detect_city

    return bool(_NL.search(text or "")) or bool(detect_city(text or ""))


def _find_nl_locations(facets: list, param: str | None = None) -> tuple[str | None, list[str]]:
    """Fallback when a tenant has no country facet (ASML): the location facet's values that are Dutch places,
    e.g. "Veldhoven", "Eindhoven", "Delft". Returns (facetParameter, [value ids])."""
    for facet in facets or []:
        fp = facet.get("facetParameter") or param
        values = facet.get("values") or []
        nested = [v for v in values if v.get("values")]
        if nested:
            found = _find_nl_locations(nested, fp)
            if found[1]:
                return found
        if re.search(r"location", fp or "", re.I):
            ids = [v.get("id") for v in values if v.get("id") and _is_dutch_place(v.get("descriptor") or "")]
            if ids:
                return fp, ids
    return None, []


class WorkdayAdapter(Adapter):
    ats = "workday"
    PAGE = 20
    MAX_DETAILS = 400

    def _base(self, slug: str) -> tuple[str, str]:
        m = _SLUG.match(slug)
        if not m:
            raise AdapterError(f"workday slug must look like tenant.wd3/site, got {slug!r}")
        tenant, wd, site = m.group(1), m.group(2), m.group(3)
        host = f"https://{tenant}.{wd}.myworkdayjobs.com"
        return host, f"{host}/wday/cxs/{tenant}/{site}"

    def _list(self, api: str, facets: dict, offset: int, search: str = "") -> dict:
        resp = self.client.post(f"{api}/jobs", json={"appliedFacets": facets, "limit": self.PAGE,
                                                      "offset": offset, "searchText": search},
                                headers={"Accept": "application/json", "Content-Type": "application/json"})
        if resp.status_code == 404:
            raise SourceNotFound(api)
        if resp.status_code >= 400:
            raise AdapterError(f"{resp.status_code} for {api}/jobs")
        return resp.json()

    def fetch(self, slug: str) -> list[RawPosting]:
        host, api = self._base(slug)
        first = self._list(api, {}, 0)
        nl_param, nl_id, _ = _find_nl_facet(first.get("facets", []))
        loc_param, loc_ids = (None, []) if nl_id else _find_nl_locations(first.get("facets", []))
        search = ""
        if nl_id:
            facets = {nl_param: [nl_id]}
            page = self._list(api, facets, 0)
        elif loc_ids:
            # no country facet, but a location facet listing Dutch sites: select those
            facets = {loc_param: loc_ids}
            page = self._list(api, facets, 0)
            nl_id = "locations"
        else:
            # No country facet on this tenant: use the site's own search for "Netherlands" so NL postings deep
            # in a 3,000-job list are found, then keep only those whose location text says so.
            facets, search = {}, "Netherlands"
            page = self._list(api, facets, 0, search)
        total = int(page.get("total") or 0)
        listings = list(page.get("jobPostings") or [])
        offset = self.PAGE
        while offset < total and offset < 2000:
            more = self._list(api, facets, offset, search)
            listings.extend(more.get("jobPostings") or [])
            offset += self.PAGE
        if not nl_id:
            listings = [j for j in listings if _is_dutch_place(j.get("locationsText") or "")]

        out: list[RawPosting] = []
        for job in listings[: self.MAX_DETAILS]:
            description, posted, c = self._detail(api, job.get("externalPath") or "")
            out.append(self._raw(host, slug, job, description, posted, None if c and not _is_dutch_place(c) else "NL"))
        self.abroad_kept = False
        on, countries = self.abroad_countries()
        if on:
            # once a day: the search costs a minute or two per large board
            if self.abroad_due(slug):
                out.extend(self._graduates_abroad(host, api, slug, {r.url for r in out}, countries))
            else:
                self.abroad_kept = True  # read today already: the crawler keeps the stored ones (radar/crawler.py)
        return out

    def _detail(self, api: str, path: str) -> tuple[str | None, object, str]:
        """(description html, posting date, the country as the board names it) of one job; empty when unreadable."""
        try:
            detail = self.client.get(f"{api}{path}", headers={"Accept": "application/json"})
            if detail.status_code == 200:
                info = detail.json().get("jobPostingInfo") or {}
                return (info.get("jobDescription"), parse_dt(info.get("startDate")),
                        (info.get("country") or {}).get("descriptor") or "")
        except Exception:
            pass
        return None, None, ""

    def _raw(self, host: str, slug: str, job: dict, description, posted, country: str | None) -> RawPosting:
        path = job.get("externalPath") or ""
        return RawPosting(
            external_id=str((job.get("bulletFields") or [path])[0] or path),
            title=job.get("title") or "",
            url=f"{host}/{slug.split('/', 1)[1]}{path}",
            location=job.get("locationsText"),
            country=country,
            description_html=description,
            posted_at=posted,
            raw={"posted_on": job.get("postedOn")},
        )

    PAGES_PER_SEARCH = 5
    MAX_ABROAD_DETAILS = 150

    def _graduates_abroad(self, host: str, api: str, slug: str, seen: set[str], countries: set[str] | None):
        """Jobs for students and graduates outside the Netherlands (RADAR_ENTRY_LEVEL_COUNTRIES): found with the
        board's search, kept by title before their text is fetched. The crawler decides what is stored."""
        from radar.extract.rules import entry_level_title
        from radar.normalize import place_country

        found: dict[str, dict] = {}
        for word in self.GRADUATE_SEARCHES:
            offset, total = 0, 1
            while offset < total and offset < self.PAGE * self.PAGES_PER_SEARCH:
                try:
                    page = self._list(api, {}, offset, word)
                except AdapterError:
                    break
                total = int(page.get("total") or 0)
                for job in page.get("jobPostings") or []:
                    path = job.get("externalPath") or ""
                    if path and path not in found and entry_level_title(job.get("title") or "") \
                            and not _is_dutch_place(job.get("locationsText") or ""):
                        found[path] = job
                offset += self.PAGE
        out: list[RawPosting] = []
        for path, job in list(found.items())[: self.MAX_ABROAD_DETAILS]:
            if f"{host}/{slug.split('/', 1)[1]}{path}" in seen:
                continue
            description, posted, c = self._detail(api, path)
            code = place_country(c) or place_country(job.get("locationsText") or "")
            if not code or (countries is not None and code not in countries):
                continue
            out.append(self._raw(host, slug, job, description, posted, code))
        return out

    def count_nl(self, slug: str) -> tuple[int, int]:
        """(nl_postings, total_postings) from the first listing page's country facet: one request per board."""
        _, api = self._base(slug)
        first = self._list(api, {}, 0)
        total = int(first.get("total") or 0)
        param, nl_id, count = _find_nl_facet(first.get("facets", []))
        if param:
            return count, total
        # no country facet: ask the site's search; count the matches whose location text names the Netherlands
        page = self._list(api, {}, 0, "Netherlands")
        hits = list(page.get("jobPostings") or [])
        offset = self.PAGE
        while offset < int(page.get("total") or 0) and offset < 400:
            hits.extend(self._list(api, {}, offset, "Netherlands").get("jobPostings") or [])
            offset += self.PAGE
        nl = sum(1 for j in hits if _NL.search(j.get("locationsText") or ""))
        return nl, total

    def probe(self, slug: str) -> bool:
        try:
            _, api = self._base(slug)
            data = self._list(api, {}, 0)
        except Exception:
            return False
        return "jobPostings" in data
