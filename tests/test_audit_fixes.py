"""Fixes for what the 5 Oct 2026 audit of the live listings found: dead links, postings abroad or past their expiry
date that stayed listed, non-tech jobs, Dutch-speaking jobs shown as English, missed enrolment wording."""

from datetime import datetime, timedelta

import httpx
import pytest
import respx

from radar.adapters import get_adapter
from radar.adapters.base import RawPosting, SourceNotFound
from radar.crawler import close_expired, deactivate, ingest, is_expired
from radar.db import session_scope
from radar.models import Posting, Source


def _src(s, slug="acme"):
    src = Source(company="Acme Robotics", ats="jsonld", slug=slug, url="https://acme.example", active=True)
    s.add(src)
    s.flush()
    return src


def _raw(ext, title="Backend Engineer", valid=None, location="Utrecht, Netherlands", url=None):
    return RawPosting(external_id=ext, title=title, location=location, url=url or f"https://acme.example/{ext}",
                      description_html="Python, Docker and Kubernetes at Acme Robotics.",
                      raw={"valid_through": valid} if valid else {})


def _still(ext):
    return RawPosting(external_id=ext, title="", url="", raw={"still_listed": True})


def _offers(*urls):
    return {"offers": [{"id": i, "slug": u.rsplit("/", 1)[-1], "title": "Backend Developer", "careers_url": u,
                        "city": "Utrecht", "country_code": "nl", "description": "<p>Python</p>",
                        "status": "published"} for i, u in enumerate(urls)]}


@respx.mock
def test_recruitee_board_whose_career_site_is_switched_off_is_not_found():
    respx.get("https://gone.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=_offers("https://gone.recruitee.com/o/backend-developer")))
    respx.get("https://gone.recruitee.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://recruitee.com/careers_not_hosted"}))
    respx.get("https://recruitee.com/careers_not_hosted").mock(return_value=httpx.Response(200, text="Recruitee"))
    with pytest.raises(SourceNotFound):
        get_adapter("recruitee").fetch("gone")


@respx.mock
def test_recruitee_dead_custom_domain_falls_back_to_the_recruitee_address():
    respx.get("https://acme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=_offers("https://careers.acme.example/o/backend-developer")))
    respx.get("https://careers.acme.example/o/backend-developer").mock(side_effect=httpx.ConnectError("no such host"))
    respx.get("https://acme.recruitee.com/").mock(return_value=httpx.Response(200, text="Jobs at Acme"))
    jobs = get_adapter("recruitee").fetch("acme")
    assert jobs[0].url == "https://acme.recruitee.com/o/backend-developer"


@respx.mock
def test_recruitee_working_custom_domain_is_kept():
    respx.get("https://acme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=_offers("https://werkenbij.acme.example/o/backend-developer")))
    respx.get("https://werkenbij.acme.example/o/backend-developer").mock(return_value=httpx.Response(200))
    jobs = get_adapter("recruitee").fetch("acme")
    assert jobs[0].url == "https://werkenbij.acme.example/o/backend-developer"


def test_a_date_without_time_is_open_all_that_day():
    now = datetime(2026, 10, 5, 14, 0)
    assert not is_expired(datetime(2026, 10, 5), now)
    assert is_expired(datetime(2026, 10, 4), now)
    assert is_expired(datetime(2026, 10, 5, 9, 0), now)
    assert not is_expired(None, now)


def test_pages_not_reread_are_still_checked_for_country_and_expiry(fresh_db):
    future = (datetime.utcnow() + timedelta(days=30)).date().isoformat()
    with session_scope() as s:
        src = _src(s)
        ingest(s, src, [_raw("nl", valid=future), _raw("abroad"), _raw("ends", valid=future)])
        s.query(Posting).filter_by(external_id="abroad").one().country = "IN"  # corrected after it was read
        s.query(Posting).filter_by(external_id="ends").one().valid_through = datetime.utcnow() - timedelta(days=2)
        ingest(s, src, [_still("nl"), _still("abroad"), _still("ends")])
        live = {p.external_id for p in s.query(Posting).filter(Posting.closed_at.is_(None))}
        assert live == {"nl"}


def test_a_moved_page_updates_the_link(fresh_db):
    with session_scope() as s:
        src = _src(s)
        ingest(s, src, [_raw("1", url="https://dead.acme.example/o/1")])
        ingest(s, src, [_raw("1", url="https://acme.recruitee.com/o/1")])
        assert s.query(Posting).one().url == "https://acme.recruitee.com/o/1"


def test_close_expired_between_crawls_respects_the_broken_template_guard(fresh_db):
    future = (datetime.utcnow() + timedelta(days=30)).isoformat()
    with session_scope() as s:
        good, broken = _src(s, "good"), _src(s, "broken")
        ingest(s, good, [_raw(str(i), f"Engineer {i}", future) for i in range(3)])
        ingest(s, broken, [_raw(f"b{i}", f"Developer {i}", future) for i in range(6)])
        s.query(Posting).filter_by(external_id="0").one().valid_through = datetime.utcnow() - timedelta(days=1)
        for p in s.query(Posting).filter(Posting.source_id == broken.id):
            p.valid_through = datetime.utcnow() - timedelta(days=300)
        assert close_expired(s) == 1
        assert s.query(Posting).filter(Posting.closed_at.is_not(None)).one().external_id == "0"


def test_deactivating_a_source_closes_its_postings(fresh_db):
    with session_scope() as s:
        src = _src(s)
        ingest(s, src, [_raw("1"), _raw("2", "Data Engineer")])
        assert deactivate(s, src) == 2
        assert not src.active and s.query(Posting).filter(Posting.closed_at.is_(None)).count() == 0


@pytest.mark.parametrize("title", [
    "CNC Programmeur Zoetermeer", "CAD/CAM Programmeur", "Programmeur Lasersnijmachine", "Elektromonteur Datacenters",
    "Elevator and/or Escalator Technician in the Netherlands", "Electrical Datacenter Engineer - Systems Integrator",
    "M&E Engineer - AI Infrastructure", "Site Selection Manager - AI Infrastructure",
    "AI Training Contributor - Dutch - Remote", "AI Text Validation Specialist (Dutch language)",
    "Senior inkoper ICT", "Inkoopadviseur ICT", "IT Contract & Vendor Manager", "Consultant werving&selectie IT",
    "Medewerker Facilitaire Servicedesk", "Directiesecretaresse IT", "Jurist IT en Privacy bij de Rijksoverheid",
    "Senior Underwriter Cyber – Belgium & Netherlands", "AI performance marketeer", "CG Art Director (AI-Driven)",
    "Onderofficier Constructiegenie (Force Support Engineer) (MBO 3)", "Netwerkarchitect laagspanning",
    "PhD position on electrochemical synthesis of ammonia in ceramic membrane reactors",
])
def test_other_trades_are_not_tech(title):
    from radar.classify import is_tech

    assert not is_tech(title, "Python, Kubernetes, AWS, Docker, SQL, data and cloud."), title


@pytest.mark.parametrize("title", [
    "SAP Inhouseday - Deloitte Consulting Career Week 17-20 November", "Business Process and IT Inhousedag",
    "Data & Responsible Insight - Contract in 1 day evenement", "Maven Securities' Women's Networking Lunch, Amsterdam",
    "(IT) Audit Business Course 2026", "General Technologist Submissions: Developers, DevOps, SysAdmin",
])
def test_recruitment_events_are_not_vacancies(title):
    from radar.classify import tech_score

    assert tech_score(title, "Python, Kubernetes, AWS, Docker, SQL") <= 0.05, title


@pytest.mark.parametrize("title", [
    "Electrical Automation Engineer", "Electrical and Controls Engineer - EU Engineering (NL)", "PLC Engineer",
    "Senior Technical SEO Specialist", "3D Technical Artist", "Cyber Security Engineer", "Network Engineer",
    "IT Support Technician | Dutch", "QA Engineer", "Data Engineer", "Software Engineer - Construction Tech",
])
def test_tech_titles_near_those_trades_stay_tech(title):
    from radar.classify import is_tech

    text = "You write Python and TypeScript, deploy with Docker and Kubernetes on AWS, and automate PLC logic in CI/CD."
    assert is_tech(title, text), title


def test_qa_in_food_or_medicine_production_is_not_software_qa():
    from radar.classify import is_tech

    gmp = "Je bewaakt HACCP en BRC, beoordeelt afwijkingen en CAPA's in de productielijn voor vlees."
    assert not is_tech("QA Officer", gmp)
    assert is_tech("QA Officer", "You test our web platform with Playwright, Cypress and Python in CI/CD pipelines.")


def test_a_title_naming_dutch_means_dutch_is_required():
    from radar.extract.rules import language_fields

    english = "You will support our users and laptops, configure Microsoft 365 and manage accounts. " * 3
    for title in ("IT Support Technician | Dutch", "AI Training Contributor - Dutch - Remote",
                  "AI Text Validation Specialist (Dutch language)"):
        _, dutch_required, english_only, _ = language_fields(title, english)
        assert dutch_required and not english_only, title
    assert language_fields("SOC Analyst (German or Dutch speaking)", english)[2] is True  # either language will do


@pytest.mark.parametrize("text", [
    "Wij zoeken een gemotiveerde MBO 3 of 4 student met een ICT gerelateerde opleiding.",
    "Je hebt een lopende mbo-4-opleiding richting ICT.",
    "Penultimate-year student in Computer Science with the ability to work full time.",
    "Student working toward a Master's degree in Electrical Engineering.",
    "Schiphol is de ultieme plek voor jouw meewerkstage.",
    "We bieden je een stagevergoeding van maximaal 400 euro (met een MBO-opleiding).",
])
def test_more_enrolment_wording(text):
    from radar.extract.rules import detect_enrollment

    assert detect_enrollment(text, "Internship") is True, text


def test_completed_or_studying_is_open_to_graduates():
    from radar.extract.rules import detect_enrollment

    text = "You have completed or are in the midst or at the end of a Master's degree in a technical discipline."
    assert detect_enrollment(text, "Internship: R&D Engineer") is False


def test_intern_in_brackets_in_a_dutch_posting_means_in_house():
    from radar.extract.rules import detect_seniority

    dutch = "Wij zoeken een ervaren security officer voor ons team. Je werkt aan informatiebeveiliging en de ISO-norm."
    assert detect_seniority("Technisch Information Security Officer (intern)", dutch) != "intern"
    assert detect_seniority("Software Engineer (Intern)", "You will join our team for six months.") == "intern"


def test_sector_list_ignores_case():
    from radar.sectors import sector_for

    assert sector_for("Quantware", "")[0] == sector_for("QuantWare", "")[0] == "hardware"


def test_title_field_label_is_dropped():
    from radar.normalize import normalize

    raw = RawPosting(external_id="1", title="Titel Industrial Automation Engineer", url="https://x.example/1",
                     location="Utrecht")
    assert normalize(raw, "Acme")["title"] == "Industrial Automation Engineer"
    raw.title = "Title Insurance Analyst"
    assert normalize(raw, "Acme")["title"] == "Title Insurance Analyst"


def test_english_and_dutch_locale_sitemaps_go_first():
    from radar.sitemaps import _locale_rank

    listed = ["https://c.example/ca/fr/sitemap_index.xml", "https://c.example/jp/ja/sitemap_index.xml",
              "https://c.example/global/en/sitemap_index.xml"]
    assert sorted(listed, key=_locale_rank)[0] == "https://c.example/global/en/sitemap_index.xml"


def test_explicit_employer_entry_overrides_an_agency_name():
    from radar.registry import classify_source, load_source_kinds

    kinds = load_source_kinds()
    assert classify_source("BJAK", "bjakcareer", kinds) == "employer"
    assert classify_source("MCFLY & BROWN", "https://www.mcflybrown.com/job/sitemap.xml", kinds) == "agency"


def test_source_kinds_keeps_agencies_labelled_at_registration(fresh_db):
    from radar.registry import apply_source_kinds

    with session_scope() as s:
        found = Source(company="Acme Detachering", ats="recruitee", slug="acmedetachering", active=True, kind="agency")
        fixed = Source(company="BJAK", ats="ashby", slug="bjakcareer", active=True, kind="agency")
        s.add_all([found, fixed])
        s.flush()
        changes: list = []
        apply_source_kinds(s, changes)
        assert found.kind == "agency" and fixed.kind == "employer"
        assert [c[3:] for c in changes] == [("agency", "employer")]


def test_finalize_closes_what_inactive_sources_left_listed(fresh_db):
    from radar.crawler import close_inactive

    with session_scope() as s:
        src = _src(s)
        ingest(s, src, [_raw("1")])
        src.active = False
        assert close_inactive(s) == 1


@respx.mock
def test_foreign_locale_sitemap_has_an_english_twin():
    from radar.sitemaps import better_locale_sitemap

    respx.get("https://c.example/robots.txt").mock(return_value=httpx.Response(200, text=(
        "Sitemap: https://c.example/ca/fr/sitemap_index.xml\nSitemap: https://c.example/global/en/sitemap_index.xml\n")))
    respx.get("https://c.example/global/en/sitemap_index.xml").mock(return_value=httpx.Response(
        200, text="<sitemapindex><sitemap><loc>https://c.example/global/en/sitemap1.xml</loc></sitemap></sitemapindex>"))
    assert better_locale_sitemap("https://c.example/ca/fr/sitemap_index.xml") == \
        "https://c.example/global/en/sitemap_index.xml"
    assert better_locale_sitemap("https://c.example/global/en/sitemap_index.xml") is None
