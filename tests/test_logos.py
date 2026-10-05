from datetime import datetime

from fastapi.testclient import TestClient

from radar import logos
from radar.db import session_scope
from radar.models import CompanyLogo

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


def test_own_site_icons_rank_touch_icon_first_and_fall_back_to_favicon():
    html = ('<link rel="icon" href="/small.png" sizes="16x16"><link rel="apple-touch-icon" href="/touch.png" '
            'sizes="180x180"><img class="site-logo" src="/img/logo.svg">')
    got = logos.candidates(html, "https://www.example.nl/careers")
    assert got[0] == "https://www.example.nl/touch.png" and "https://www.example.nl/favicon.ico" == got[-1]


def test_ats_board_takes_the_employers_logo_not_the_ats_brand():
    html = ('<link rel="icon" href="/favicon.ico"><a class="main-header-logo" href="https://acme.example">'
            '<img alt="Acme logo" src="https://lever-client-logos.s3.amazonaws.com/f71d-1787.png"></a>'
            '<img alt="Lever logo" src="/img/lever-logo-refresh.svg" class="footer-logo">')
    assert logos.candidates(html, "https://jobs.lever.co/acme") == ["https://lever-client-logos.s3.amazonaws.com/f71d-1787.png"]
    # no logo image: the employer's own site, as the board names it
    assert logos.website('{"publicWebsite":"https://clickhouse.com/"}', "https://jobs.ashbyhq.com/clickhouse") == \
        "https://clickhouse.com/"


def test_board_urls_and_type_sniffing():
    assert logos.board_url("lever", "eu/acme", None) == "https://jobs.eu.lever.co/acme"
    assert logos.board_url("recruitee", "acme", None) == "https://acme.recruitee.com/"
    assert logos.board_url("workday", "acme/External", None) is None
    assert logos.sniff(PNG) == "image/png" and logos.sniff(b"<svg xmlns='x'></svg>") == "image/svg+xml"
    assert logos.sniff(b"not an image") is None


def test_logo_is_served_from_this_site_and_named_on_postings(fresh_db):
    from radar.api import app

    with session_scope() as s:
        s.add(CompanyLogo(company="Acme", slug="acme", status="ok", content_type="image/png", data=PNG,
                          fetched_at=datetime.utcnow()))
    logos._known.clear()
    client = TestClient(app)
    r = client.get("/logo/acme")
    assert r.status_code == 200 and r.content == PNG and "sandbox" in r.headers["content-security-policy"]
    assert client.get("/logo/nobody").status_code == 404
    with session_scope() as s:
        assert logos.known(s) == {"Acme": "acme"}


def test_names_that_differ_only_in_case_get_their_own_logo_address(fresh_db):
    from radar.models import Posting, Source

    now = datetime.utcnow()
    with session_scope() as s:
        for n, name in enumerate(["DeepHealth", "Deephealth"]):
            src = Source(company=name, ats="recruitee", slug=f"deephealth{n}", kind="employer")
            s.add(src)
            s.flush()
            s.add(Posting(source_id=src.id, external_id=str(n), title="Engineer", company=name, url=f"https://x/{n}",
                          content_hash=str(n), dedup_key=str(n), first_seen=now, last_seen=now, is_tech=True))
    with session_scope() as s:
        original = logos.find_logo
        logos.find_logo = lambda url, client, depth=0: ("https://img/x.png", "image/png", PNG)
        try:
            out = logos.refresh(s, now=now, client=object())
        finally:
            logos.find_logo = original
        assert out["found"] == 2
        assert sorted(c.slug for c in s.query(CompanyLogo)) == ["deephealth", "deephealth-2"]
