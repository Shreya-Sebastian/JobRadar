from urllib.parse import urlparse

import httpx

from radar.adapters.base import Adapter, RawPosting, SourceNotFound, parse_dt


class RecruiteeAdapter(Adapter):
    """Recruitee public offers API: https://{slug}.recruitee.com/api/offers/ (Dutch ATS, common in NL)."""

    ats = "recruitee"
    BASE = "https://{slug}.recruitee.com/api/offers/"

    def _page_ok(self, url: str) -> bool | None:
        """True when the page loads on the employer's career site, False when it is gone or Recruitee sends the
        visitor to its own homepage (the career site is switched off) or the host cannot be reached (a lapsed domain
        no longer resolves), None when the request failed in another way (a timeout)."""
        try:
            resp = self.client.get(url)
        except httpx.ConnectError:
            return False
        except Exception:
            return None
        return resp.status_code < 400 and urlparse(str(resp.url)).netloc != "recruitee.com"

    def _career_base(self, slug: str, offers: list[dict]) -> str | None:
        """Where the offer pages live. The API keeps listing offers after an employer switches its career site off
        (the pages redirect to recruitee.com) or lets its custom domain lapse (careers.interstellar.nl no longer
        resolves, werkenbijfivoor.nl serves another site), so the links would be dead. None: keep the API's links;
        otherwise the {slug}.recruitee.com base to link to instead."""
        first = next((o.get("careers_url") for o in offers if o.get("careers_url")), None)
        own = f"{slug}.recruitee.com"
        if first and urlparse(first).netloc != own:
            if self._page_ok(first) is not False:
                return None  # the custom domain works (or the check failed: keep what the API says)
        sub = self._page_ok(f"https://{own}/")
        if sub is False:
            raise SourceNotFound(f"career site of {slug} is not hosted any more")
        return None if (sub is None or not first or urlparse(first).netloc == own) else f"https://{own}"

    def fetch(self, slug: str) -> list[RawPosting]:
        data = self._get_json(self.BASE.format(slug=slug))
        offers = [j for j in data.get("offers", []) if j.get("status") in (None, "published")]
        base = self._career_base(slug, offers) if offers else None
        out: list[RawPosting] = []
        for job in offers:
            url = job.get("careers_url") or job.get("url") or ""
            if base and job.get("slug"):
                url = f"{base}/o/{job['slug']}"
            country_code = job.get("country_code")
            out.append(
                RawPosting(
                    external_id=str(job["id"]),
                    title=job.get("title") or "",
                    url=url,
                    location=job.get("location") or None,
                    city=job.get("city"),
                    country=country_code.upper() if country_code and len(country_code) == 2 else None,
                    remote=True if job.get("remote") else None,
                    description_html="\n".join(s for s in (job.get("description"), job.get("requirements")) if s)
                    or None,
                    posted_at=parse_dt(job.get("published_at") or job.get("created_at")),
                    raw={"department": job.get("department"), "employment_type": job.get("employment_type_code")},
                )
            )
        return out

    def probe(self, slug: str) -> bool:
        try:
            resp = self.client.get(self.BASE.format(slug=slug))
        except Exception:
            return False
        return resp.status_code == 200 and "offers" in resp.text[:200]
