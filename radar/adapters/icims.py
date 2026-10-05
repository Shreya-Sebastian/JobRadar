from radar.adapters.base import Adapter, AdapterError, RawPosting, parse_dt


class IcimsAdapter(Adapter):
    """iCIMS career sites (the "Jibe" sites at jobs.<company>.com/<site>, e.g. Booking.com, Mammoet).

    The site's own JSON API lists the jobs with their full text and location: https://{host}/api/jobs?page=N.
    The slug is the career site's address without scheme, "jobs.booking.com/booking"; job pages live at
    https://{host}/{site}/jobs/{id}. Only jobs located in the Netherlands are asked for (the API's location filter,
    which also matches jobs with several locations), so a global employer costs a page or two per crawl."""

    ats = "icims"
    PAGE = 100
    MAX_PAGES = 20

    @staticmethod
    def _parts(slug: str) -> tuple[str, str]:
        slug = slug.removeprefix("https://").removeprefix("http://").strip("/")
        host, _, site = slug.partition("/")
        return host, site

    def fetch(self, slug: str) -> list[RawPosting]:
        host, site = self._parts(slug)
        out: list[RawPosting] = []
        for page in range(1, self.MAX_PAGES + 1):
            data = self._get_json(f"https://{host}/api/jobs", page=page, limit=self.PAGE, location="Netherlands")
            if not isinstance(data, dict) or "jobs" not in data:
                raise AdapterError(f"no iCIMS job list at {host}")
            jobs = [j.get("data") or {} for j in data.get("jobs") or []]
            for j in jobs:
                job_id = str(j.get("req_id") or j.get("slug") or "")
                if not job_id or not j.get("title"):
                    continue
                text = "\n\n".join(x for x in (j.get("description"), j.get("responsibilities"),
                                               j.get("qualifications")) if x)
                lang = j.get("language") or "en-us"
                path = f"/{site}/jobs/{j.get('slug') or job_id}" if site else f"/jobs/{j.get('slug') or job_id}"
                out.append(RawPosting(
                    external_id=job_id,
                    title=j["title"],
                    url=f"https://{host}{path}?lang={lang}",
                    company=j.get("hiring_organization") or j.get("brand") or None,
                    location=j.get("full_location") or j.get("short_location") or None,
                    city=j.get("city") or None,
                    country=(j.get("country_code") or "").upper() or None,
                    remote=True if str(j.get("location_type") or "").upper() == "REMOTE" else None,
                    description_text=text or None,
                    posted_at=parse_dt(j.get("posted_date") or j.get("create_date")),
                    raw={"employment_type": j.get("employment_type"), "category": j.get("category"),
                         "apply_url": j.get("apply_url")},
                ))
            total = int(data.get("totalCount") or 0)
            if not jobs or page * self.PAGE >= total:
                break
        return out

    def probe(self, slug: str) -> bool:
        host, _ = self._parts(slug)
        try:
            resp = self.client.get(f"https://{host}/api/jobs", params={"page": 1, "limit": 1})
        except Exception:
            return False
        return resp.status_code == 200 and '"totalCount"' in resp.text[:4000]
