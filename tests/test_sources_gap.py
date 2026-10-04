from radar.adapters.jsonld import _JOB_LINK, _nl_url
from radar.adapters.lever import LeverAdapter


def test_lever_eu_boards():
    a = LeverAdapter()
    assert a._url("tomtom") == "https://api.lever.co/v0/postings/tomtom"
    assert a._url("eu/tomtom") == "https://api.eu.lever.co/v0/postings/tomtom"


def test_dutch_job_urls():
    assert _JOB_LINK.search("https://www.dnb.nl/werken-bij/banen/data-engineer/")
    assert _nl_url().search("https://mycareers.frieslandcampina.com/job/Bodegraven-Planner-2411NL/1365726657/")
    assert not _nl_url().search("https://mycareers.frieslandcampina.com/job/Lummen-Key-Account-Manager-3560/135989/")


def test_source_default_country(fresh_db):
    from radar.adapters.base import RawPosting
    from radar.crawler import ingest
    from radar.db import session_scope
    from radar.models import Posting, Source

    with session_scope() as s:
        src = Source(company="Canon Production Printing", ats="jsonld", slug="https://x/sitemap.xml",
                     default_country="NL")
        s.add(src)
        s.flush()
        ingest(s, src, [RawPosting(external_id="1", title="Software Engineer", url="https://x/job/1",
                                   location=None, description_text="Python and C++ for printers. " * 20)])
        ingest(s, src, [RawPosting(external_id="2", title="Software Engineer", url="https://x/job/2",
                                   location="Tokyo, Japan", description_text="Python. " * 50)])
        assert s.query(Posting).filter_by(external_id="1").one().country == "NL"
        assert s.query(Posting).filter_by(external_id="2").count() == 0  # a stated place abroad still counts
