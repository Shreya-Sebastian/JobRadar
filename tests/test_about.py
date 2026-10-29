from fastapi.testclient import TestClient
from test_job_page import BROWSER, _setup


def test_about_page_in_both_languages_and_in_the_sitemap(fresh_db):
    from radar.api import app

    c = TestClient(app)
    en, nl = c.get("/about", headers=BROWSER), c.get("/nl/over-ons", headers=BROWSER)
    assert en.status_code == 200 and nl.status_code == 200
    assert 'lang="en"' in en.text and "Where the jobs come from" in en.text and "For employers" in en.text
    assert 'lang="nl"' in nl.text and "Waar de vacatures vandaan komen" in nl.text and "Voor werkgevers" in nl.text
    assert 'name="robots"' not in en.text  # meant to be found
    assert 'rel="canonical" href="https://techjobsradar.nl/nl/over-ons"' in nl.text and "{{" not in en.text
    sm = c.get("/sitemap.xml").text
    assert "/about</loc>" in sm and "/nl/over-ons</loc>" in sm


def test_a_former_domain_redirects_to_the_same_page(fresh_db, monkeypatch):
    from radar.api import app
    from radar.config import settings

    monkeypatch.setattr(settings, "site_url", "https://techjobsradar.com")
    monkeypatch.setattr(settings, "redirect_hosts", "techjobsradar.nl, www.techjobsradar.nl")
    c = TestClient(app)
    r = c.get("/jobs/phd-postdoc?city=Delft", headers={"host": "techjobsradar.nl"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://techjobsradar.com/jobs/phd-postdoc?city=Delft"
    assert c.get("/about", headers={**BROWSER, "host": "techjobsradar.com"}).status_code == 200


def test_the_entry_level_site_is_english_only_and_names_no_country(fresh_db, monkeypatch):
    from radar.api import app
    from radar.config import settings
    from radar.wording import neutral

    monkeypatch.setattr(settings, "entry_level_only", True)
    c = TestClient(app)
    for nl, en in (("/nl/", "/"), ("/nl/over-ons", "/about"), ("/nl/vacature/12/data-engineer", "/job/12/data-engineer"),
                   ("/nl/bedrijf/acme", "/company/acme")):
        r = c.get(nl, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == en, nl
    home = c.get("/", headers=BROWSER).text
    assert 'data-entry="1"' in home and "Netherlands" not in home.split("<body")[0]
    assert neutral("Tech internships in the Netherlands") == "Tech internships"
    assert neutral("English, no Dutch required") == "English is enough"
    assert neutral("Dutch required") == "Local language required"
    assert "Dutch data protection authority" in neutral("complain to the Dutch data protection authority")
    for page in ("/about", "/privacy"):
        html = c.get(page, headers=BROWSER).text
        assert 'class="nl"' not in html and 'hreflang="nl"' not in html and "in the Netherlands" not in html, page
    monkeypatch.setattr(settings, "entry_level_only", False)
    assert neutral("no Dutch needed") == "no Dutch needed"  # the Dutch site is unchanged


def test_job_pages_link_about_and_a_report_of_the_job(fresh_db):
    from radar.api import app

    pid, _ = _setup()
    c = TestClient(app)
    en = c.get(f"/job/{pid}/senior-backend-engineer", headers=BROWSER).text
    assert 'href="/about"' in en and 'href="/privacy"' in en
    assert f'href="/feedback?kind=listing&amp;from=/job/{pid}/senior-backend-engineer"' in en.replace("%2F", "/")
    assert "Report a problem with this job" in en
    nl = c.get(f"/nl/vacature/{pid}/senior-backend-engineer", headers=BROWSER).text
    assert 'href="/nl/over-ons"' in nl and "Een probleem met deze vacature melden" in nl and "/nl/feedback?kind=listing" in nl
