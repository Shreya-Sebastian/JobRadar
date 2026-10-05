import httpx
import respx

from radar.adapters.avature import AvatureAdapter
from radar.adapters.icims import IcimsAdapter


@respx.mock
def test_icims_career_site_api():
    def job(i, country="NL", city="Amsterdam"):
        return {"data": {"req_id": str(i), "slug": str(i), "title": f"Data Engineer {i}", "language": "en-us",
                         "city": city, "country_code": country, "full_location": f"{city}, Netherlands",
                         "description": "Build data pipelines in Python and Spark. " * 10,
                         "posted_date": "2026-09-21T07:21:00+0000", "employment_type": "FULL_TIME",
                         "hiring_organization": "Booking.com"}}

    route = respx.get("https://jobs.booking.com/api/jobs").mock(side_effect=[
        httpx.Response(200, json={"jobs": [job(i) for i in range(100)], "totalCount": 101}),
        httpx.Response(200, json={"jobs": [job(100, "NL", "Utrecht")], "totalCount": 101}),
    ])
    raws = IcimsAdapter().fetch("jobs.booking.com/booking")
    assert len(raws) == 101 and route.call_count == 2
    assert route.calls[0].request.url.params["location"] == "Netherlands"
    r = raws[0]
    assert r.url == "https://jobs.booking.com/booking/jobs/0?lang=en-us" and r.country == "NL" and r.city == "Amsterdam"
    assert r.company == "Booking.com" and "Spark" in r.text() and r.posted_at.year == 2026


_LIST = """<html><body>
<article class="article--result"><h3><a href="https://jobs.example.com/en_US/careers/JobDetail/101">Cloud Engineer</a></h3>
<div>Den Haag , Zuid-Holland , Netherlands &bull; Job ID: 101 &bull; IT</div></article>
<article class="article--result"><h3><a href="https://jobs.example.com/en_US/careers/JobDetail/102">Data Lead</a></h3>
<div>Multiple Locations &bull; Job ID: 102 &bull; IT</div></article>
<article class="article--result"><h3><a href="https://jobs.example.com/en_US/careers/JobDetail/103">Sales Manager</a></h3>
<div>Multiple Locations &bull; Job ID: 103 &bull; Sales</div></article>
<article class="article--result"><h3><a href="https://jobs.example.com/en_US/careers/JobDetail/104">Engineer</a></h3>
<div>Munich , Bavaria , Germany &bull; Job ID: 104 &bull; IT</div></article>
</body></html>"""


def _detail(loc_label, loc):
    return (f'<html><body><div class="article__content__view__field"><div class="article__content__view__field__label">'
            f'Posted since</div><div class="article__content__view__field__value">19-Aug-2026</div></div>'
            f'<div class="article__content__view__field tf_locations"><div class="article__content__view__field__label">'
            f'{loc_label}</div><div class="article__content__view__field__value"><ul><li>{loc}</li></ul></div></div>'
            f'<div class="article__content__view">{"Kubernetes, Terraform and Python for our cloud platform. " * 10}'
            f'</div></body></html>')


@respx.mock
def test_avature_portal_keeps_dutch_jobs_only():
    base = "https://jobs.example.com/en_US/careers"
    respx.get(f"{base}/SearchJobs/").mock(side_effect=[httpx.Response(200, text=_LIST),
                                                     httpx.Response(200, text="<html><body>no more</body></html>")])
    respx.get(f"{base}/JobDetail/101").mock(return_value=httpx.Response(200, text=_detail("Location(s)",
                                                                                         "Den Haag - Zuid-Holland - Netherlands")))
    respx.get(f"{base}/JobDetail/102").mock(return_value=httpx.Response(200, text=_detail("Any location in", "Netherlands")))
    respx.get(f"{base}/JobDetail/103").mock(return_value=httpx.Response(200, text=_detail("Location(s)", "Germany")))
    a = AvatureAdapter()
    raws = a.fetch("jobs.example.com/en_US/careers")
    assert [r.external_id for r in raws] == ["101", "102"]  # the German job and the multi-location one abroad are out
    assert all(r.country == "NL" for r in raws) and "Terraform" in raws[0].text()
    assert raws[0].posted_at.date().isoformat() == "2026-08-19"
    a.known_urls = {"101": "101", "102": "102"}
    respx.get(f"{base}/SearchJobs/").mock(side_effect=[httpx.Response(200, text=_LIST),
                                                     httpx.Response(200, text="<html><body>no more</body></html>")])
    again = a.fetch("jobs.example.com/en_US/careers")
    assert {r.external_id for r in again} <= {"101", "102"} and a.still_listed | {r.external_id for r in again} == {"101", "102"}
