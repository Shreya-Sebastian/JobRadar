from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from radar import analytics
from radar.config import settings
from radar.db import session_scope
from radar.models import DailyStat, Meta, PageView

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/128.0 Safari/537.36")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/17.0 Mobile/15E148 Safari/604.1")
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def test_parse_agent():
    assert analytics.parse_agent(CHROME) == ("Chrome", "Windows", "desktop", False)
    assert analytics.parse_agent(IPHONE) == ("Safari", "iOS", "mobile", False)
    assert analytics.parse_agent(GOOGLEBOT)[0::3] == ("Googlebot", True)
    assert analytics.parse_agent("")[3] is True
    assert analytics.classify("/static/app.js") is None and analytics.classify("/api/version") is None
    assert analytics.classify("/vacatures/ict-utrecht") == "page" and analytics.classify("/sitemap.xml") == "crawl"


@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "analytics_enabled", True)
    monkeypatch.setattr(settings, "admin_token", "secret-token")
    from radar.api import app

    with TestClient(app) as c:
        yield c


def _seed_posting():
    from radar.models import Posting, Source

    with session_scope() as s:
        src = Source(company="Acme", ats="lever", slug="acme")
        s.add(src)
        s.flush()
        p = Posting(source_id=src.id, external_id="1", title="Platform Engineer", company="Acme", url="https://x/1",
                    content_hash="h", dedup_key="d", first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
                    is_tech=True)
        s.add(p)
        s.flush()
        return p.id


def test_visits_are_recorded_and_shown_to_the_admin(client):
    pid = _seed_posting()
    human = {"user-agent": CHROME, "cf-connecting-ip": "203.0.113.5", "cf-ipcountry": "NL",
             "referer": "https://www.google.com/search?q=tech+jobs"}
    client.get("/?utm_source=newsletter", headers=human)
    client.get("/api/postings?q=python&city=Utrecht", headers=human)
    client.get("/api/postings?q=python&city=Utrecht&page=2", headers=human)  # paging is not a new search
    client.post("/api/e", json={"e": "job_click", "d": str(pid), "p": "/#jobs"}, headers=human)
    client.post("/api/e", json={"e": "nav", "p": "/#market"}, headers=human)
    client.post("/api/e", json={"e": "not-an-event"}, headers=human)
    client.get("/", headers={"user-agent": IPHONE, "cf-connecting-ip": "198.51.100.7", "cf-ipcountry": "BE"})
    client.get("/robots.txt", headers={"user-agent": GOOGLEBOT, "cf-connecting-ip": "66.249.66.1"})
    client.get("/static/app.js", headers=human)  # assets are not counted
    assert analytics.flush() >= 6

    with session_scope() as s:
        rows = s.query(PageView).all()
        assert {r.event for r in rows} >= {"job_click", "nav", "search"} and "not-an-event" not in {r.event for r in rows}
        assert {r.ip for r in rows} == {"203.0.113.5", "198.51.100.7", "66.249.66.1"}  # kept 30 days, for abuse
        assert not any(r.path.startswith("/static") for r in rows)

    assert client.get("/api/admin/analytics").status_code == 401
    d = client.get("/api/admin/analytics", headers={"Authorization": "Bearer secret-token"}).json()
    k = d["kpi"]
    assert k["views_today"] == 3 and k["uniques_today"] == 2  # two page loads + one tab switch, two people
    assert k["job_clicks"] == 1 and k["searches"] == 1 and k["bounce_rate"] is not None
    assert {"key": "google.com", "hits": 1} in d["referrers"] and d["utm"][0]["key"] == "newsletter"
    assert {c["key"] for c in d["countries"]} == {"NL", "BE"}
    assert d["job_clicks_by_employer"] == [{"key": "Acme", "hits": 1}]
    assert d["searches"][0]["key"] == "q=python&city=Utrecht"
    assert all("Googlebot" != b["key"] for b in d["browsers"])  # bots hidden by default
    assert d["who"] == "humans" and all(not r["bot"] for r in d["recent"])
    assert {r["ip"] for r in d["recent"]} == {"203.0.113.5", "198.51.100.7"}
    with_bots = client.get("/api/admin/analytics?bots=true", headers={"Authorization": "Bearer secret-token"}).json()
    assert {"key": "Googlebot", "hits": 1} in with_bots["bots"] and with_bots["who"] == "all"
    only_bots = client.get("/api/admin/analytics?who=bots", headers={"Authorization": "Bearer secret-token"}).json()
    assert [r["path"] for r in only_bots["recent"]] == ["/robots.txt"] and only_bots["recent"][0]["bot"] is True
    assert only_bots["kpi"]["views_today"] == 0 and only_bots["countries"] == []
    page = client.get("/admin/analytics")
    assert page.status_code == 200 and "noindex" in page.headers["x-robots-tag"] and "{{" not in page.text


def test_admin_analytics_needs_the_token_configured(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", None)
    from radar.api import app

    assert TestClient(app).get("/api/admin/analytics", headers={"Authorization": "Bearer x"}).status_code == 404


def test_visitor_hash_changes_every_day(fresh_db):
    a = analytics.visitor_id("203.0.113.5", CHROME, "2026-09-29")
    assert a == analytics.visitor_id("203.0.113.5", CHROME, "2026-09-29") and len(a) == 16
    assert analytics.visitor_id("203.0.113.5", CHROME, "2026-09-30") != a


def test_nightly_rollup_keeps_daily_totals_and_drops_old_rows(fresh_db):
    now = datetime(2026, 9, 30, 0, 10)
    with session_scope() as s:
        for i, (ts, bot, country) in enumerate([(now - timedelta(hours=5), False, "NL"),
                                                (now - timedelta(hours=4), False, "NL"),
                                                (now - timedelta(hours=3), True, "US"),
                                                (now - timedelta(days=100), False, "DE")]):
            s.add(PageView(ts=ts, kind="page", path="/", status=200, ms=5, bytes=100, bot=bot, country=country,
                           visitor=f"v{i % 2}"))
    with session_scope() as s:
        out = analytics.nightly(s, now=now)
        assert out["deleted"] == 1
        total = s.query(DailyStat).filter_by(day=(now - timedelta(days=1)).date(), dim="total").one()
        assert (total.hits, total.humans, total.uniques) == (3, 2, 2)
        assert s.query(DailyStat).filter_by(dim="country", key="NL").one().humans == 2
        at = analytics._all_time(s, [], datetime(2026, 9, 30))
        assert at["hits"] == 3 and at["humans"] == 2 and at["countries"] == 1


def test_admin_can_exclude_their_own_browser(client):
    me = {"user-agent": CHROME, "cf-connecting-ip": "203.0.113.9"}
    auth = {"Authorization": "Bearer secret-token", **me}
    client.get("/", headers=me)
    analytics.flush()
    client.get("/", headers=me)  # still in the buffer
    assert client.post("/api/admin/analytics/exclude", headers=me).status_code == 401
    r = client.post("/api/admin/analytics/exclude", headers=auth)
    assert r.json() == {"excluded": True, "forgotten": 1, "account": False} and "radar_noanalytics=1" in r.headers["set-cookie"]
    client.get("/", headers=me)  # the cookie is now sent: not recorded
    analytics.flush()
    with session_scope() as s:
        assert s.query(PageView).count() == 0
    assert client.get("/api/admin/analytics", headers=auth).json()["this_browser_excluded"] is True
    client.delete("/api/admin/analytics/exclude", headers=auth)
    client.get("/", headers=me)
    analytics.flush()
    with session_scope() as s:
        assert s.query(PageView).count() == 1


def test_redirects_are_counted_with_their_target(client):
    pid = _seed_posting()
    r = client.get(f"/job/{pid}", headers={"user-agent": CHROME}, follow_redirects=False)  # no slug: 301 to the full path
    assert r.status_code == 301
    analytics.flush()
    d = client.get("/api/admin/analytics", headers={"Authorization": "Bearer secret-token"}).json()
    assert d["redirects"]["count"] == 1
    item = d["redirects"]["items"][0]
    assert (item["path"], item["status"], item["hits"]) == (f"/job/{pid}", 301, 1) and item["to"].endswith(f"/job/{pid}/platform-engineer")
    assert d["recent"][0]["to"] == item["to"]


def test_job_pages_count_as_page_views():
    assert analytics.classify("/job/12/data-engineer") == "page"
    assert analytics.classify("/nl/vacature/12/data-engineer") == "page"
    assert analytics.classify("/nl/bedrijf/adyen") == "page"


def test_ip_addresses_are_erased_after_30_days(fresh_db):
    now = datetime(2026, 10, 5, 3, 0)
    with session_scope() as s:
        s.add(PageView(ts=now - timedelta(days=31), kind="page", path="/", status=200, ip="203.0.113.5"))
        s.add(PageView(ts=now - timedelta(days=2), kind="page", path="/", status=200, ip="198.51.100.7"))
    with session_scope() as s:
        analytics.nightly(s, now=now)
        assert sorted(r.ip or "-" for r in s.query(PageView)) == ["-", "198.51.100.7"]


def test_scrapers_posing_as_people_are_counted_as_bots(client):
    """What the live site saw: browser user agents with a Google referrer from cloud networks, and pages fetched
    without their script ever running. Only visitors whose page sent `seen` count as people."""
    google = "https://www.google.com/"
    person = {"user-agent": CHROME, "cf-connecting-ip": "203.0.113.5", "referer": google}
    client.get("/", headers=person)
    client.post("/api/e", json={"e": "seen", "p": "/"}, headers=person)  # theme.js, once the page has loaded
    phone = {"user-agent": IPHONE, "cf-connecting-ip": "198.51.100.7", "referer": google}
    client.get("/companies", headers=phone)
    client.post("/api/e", json={"e": "seen", "p": "/companies"}, headers=phone)
    client.get("/companies", headers={"user-agent": CHROME, "cf-connecting-ip": "47.79.205.241", "referer": google})
    client.get("/", headers={"user-agent": IPHONE, "cf-connecting-ip": "43.159.140.236"})
    client.get("/wp-login.php", headers={"user-agent": CHROME, "cf-connecting-ip": "45.141.235.110"})
    no_script = {"user-agent": CHROME, "cf-connecting-ip": "200.1.2.3", "referer": google}
    client.get("/", headers=no_script)
    analytics.flush()

    with session_scope() as s:  # the script check leaves the last two minutes alone, so move every visit back
        for r in s.query(PageView):
            r.ts -= timedelta(minutes=5)
        s.get(Meta, analytics.JS_SINCE).value = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        assert {r.browser for r in s.query(PageView) if r.bot} == {"Alibaba Cloud", "Tencent Cloud", "Probe"}
    d = client.get("/api/admin/analytics", headers={"Authorization": "Bearer secret-token"}).json()
    assert d["kpi"]["views_24h"] == 2 and d["referrers"] == [{"key": "google.com", "hits": 2}]
    assert {b["key"] for b in d["bots"]} == {"Alibaba Cloud", "Tencent Cloud", "Probe", "No JavaScript"}
    assert d["kpi"]["interactions"] == 0 and d["events"] == []  # `seen` is bookkeeping, not something people did

    with session_scope() as s:  # the daily totals apply the same rules
        day = s.query(PageView).first().ts.date()
        analytics.rollup(s, day)
        google_humans = s.query(DailyStat).filter_by(day=day, dim="referrer", key="google.com").one().humans
        assert google_humans == 2


def test_rules_reach_rows_recorded_before_them(fresh_db):
    """Rows stored as people by the old rules are counted again by `rollup_all`; the script check starts only when
    the first `seen` event arrived, since earlier pages did not send one."""
    day = datetime(2026, 10, 1, 12, 0)
    with session_scope() as s:
        for ip, visitor in (("47.79.1.1", "a"), ("43.156.2.2", "b"), ("203.0.113.5", "c")):
            s.add(PageView(ts=day, kind="page", path="/", status=200, ip=ip, visitor=visitor, bot=False,
                           referrer="google.com", browser="Chrome"))
    with session_scope() as s:
        out = analytics.rollup_all(s, now=datetime(2026, 10, 3, 3, 0))
    assert out["days"] == 2 and out["google_humans_after"] == 1


def test_cloud_networks():
    assert analytics.cloud_network("47.79.207.7") == "Alibaba Cloud"
    assert analytics.cloud_network("170.106.35.153") == "Tencent Cloud"
    assert analytics.cloud_network("62.108.7.25") is None and analytics.cloud_network("2a02:a44c::1") is None
    assert analytics.cloud_network("") is None and analytics.cloud_network(None) is None
    assert analytics.parse_agent("Mediapartners-Google")[3] is True


def test_people_are_visitors_who_used_the_page(client):
    """A browser that runs the script but never sends input (a scripted copy of the page) stays out of "people";
    the funnel and the account figures count only people and accounts, never addresses."""
    from urllib.parse import parse_qs, urlparse

    from radar import auth, mailer
    from radar.models import User, UserData

    mailer.OUTBOX.clear()
    auth._ip_hits.clear()
    person = {"user-agent": CHROME, "cf-connecting-ip": "203.0.113.5", "X-Requested-With": "radar"}
    client.get("/", headers=person)
    for e in ("seen", "active", "star", "job_click", "quiz_done"):
        client.post("/api/e", json={"e": e, "d": "1" if e == "job_click" else None, "p": "/"}, headers=person)
    client.get("/api/postings?q=python", headers=person)
    client.post("/api/auth/request", json={"email": "ada@example.org"}, headers=person)
    body = mailer.OUTBOX[-1].get_body(("plain",)).get_content()
    token = parse_qs(urlparse(next(w for w in body.split() if "token=" in w)).query)["token"][0]
    client.get(f"/auth/verify?token={token}", headers=person, follow_redirects=False)
    copier = {"user-agent": CHROME, "cf-connecting-ip": "198.51.100.7"}
    client.get("/", headers=copier)
    client.post("/api/e", json={"e": "seen", "p": "/"}, headers=copier)  # ran the script, never used the page
    analytics.flush()
    with session_scope() as s:
        s.query(UserData).filter_by(user_id=s.query(User).one().id).delete()
        s.add(UserData(user_id=s.query(User).one().id, saved=[1, 2, 3], profile={"skills": ["Python"]}, board={}))

    auth_h = {"Authorization": "Bearer secret-token"}
    d = client.get("/api/admin/analytics?who=people", headers=auth_h).json()
    assert d["kpi"]["people_24h"] == 1 and d["kpi"]["humans_24h"] == 2 and d["kpi"]["people_since"]
    assert {r["ip"] for r in d["recent"]} == {"203.0.113.5"}
    steps = {f["event"]: f["people"] for f in d["funnel"]}
    assert steps == {"active": 1, "search": 1, "job_click": 1, "star": 1, "profile_save": 0, "login_request": 1,
                     "login": 1}
    acc = d["accounts"]
    assert acc["total"] == acc["new_24h"] == acc["signed_in_7d"] == 1
    assert acc["saved_jobs"] == 3 and acc["with_saved_jobs"] == acc["with_skills"] == 1
    assert acc["links_30d"] == acc["links_used_30d"] == 1 and "ada@example.org" not in str(d)
    assert {"key": "quiz_done", "hits": 1} in d["events"] and {"key": "signup", "hits": 1} in d["events"]

    with session_scope() as s:  # people per day survive in the daily totals
        analytics.rollup(s, datetime.utcnow().date())
        assert s.query(DailyStat).filter_by(dim="people").one().uniques == 1


def test_the_owner_account_is_not_counted_on_any_device(client):
    """Excluding a browser that is signed in also excludes the account: another device that signs in to it, or that
    was already signed in and loads the site, stops being counted, and forgets what it did today."""
    from urllib.parse import parse_qs, urlparse

    from radar import auth, mailer
    from radar.api import app

    mailer.OUTBOX.clear()
    auth._ip_hits.clear()

    def sign_in(c, headers):
        c.post("/api/auth/request", json={"email": "owner@example.org"}, headers=headers)
        body = mailer.OUTBOX[-1].get_body(("plain",)).get_content()
        token = parse_qs(urlparse(next(w for w in body.split() if "token=" in w)).query)["token"][0]
        return c.get(f"/auth/verify?token={token}", headers=headers, follow_redirects=False)

    admin = {"Authorization": "Bearer secret-token"}
    laptop = {"user-agent": CHROME, "cf-connecting-ip": "203.0.113.5", "X-Requested-With": "radar"}
    phone = {"user-agent": IPHONE, "cf-connecting-ip": "198.51.100.7", "X-Requested-With": "radar"}
    sign_in(client, laptop)
    with TestClient(app) as tablet:  # already signed in before the owner was excluded
        sign_in(tablet, {**phone, "user-agent": IPHONE + " tablet"})
        r = client.post("/api/admin/analytics/exclude", headers={**admin, **laptop})
        assert r.json()["account"] is True and r.json()["excluded"] is True
        d = client.get("/api/admin/analytics", headers={**admin, **laptop}).json()
        assert d["account_excluded"] and d["this_browser_excluded"]

        tablet.get("/", headers={**phone, "user-agent": IPHONE + " tablet"})
        tablet.get("/api/me", headers={**phone, "user-agent": IPHONE + " tablet"})  # the page loads: excluded now
        assert tablet.cookies.get(analytics.IGNORE_COOKIE) == "1"
        tablet.get("/companies", headers={**phone, "user-agent": IPHONE + " tablet"})
        with TestClient(app) as mobile:
            assert sign_in(mobile, phone).cookies.get(analytics.IGNORE_COOKIE) == "1"
            mobile.get("/", headers=phone)
            mobile.post("/api/e", json={"e": "active", "p": "/"}, headers=phone)
    analytics.flush()
    with session_scope() as s:
        assert not s.query(PageView).filter(PageView.ip.in_(["198.51.100.7"])).count()
        assert not s.query(PageView).filter_by(event="login", ip="198.51.100.7").count()

    client.delete("/api/admin/analytics/exclude", headers={**admin, **laptop})
    assert not client.get("/api/admin/analytics", headers={**admin, **laptop}).json()["account_excluded"]


def test_the_admin_can_list_accounts_with_their_addresses(client):
    from radar.models import SavedSearch, User, UserData

    with session_scope() as s:
        u = User(email="ada@example.org", alerts="weekly")
        s.add(u)
        s.flush()
        s.add(UserData(user_id=u.id, saved=[1, 2], board={"1": {"col": "applied"}}, profile={"skills": ["Go"]}))
        s.add(SavedSearch(user_id=u.id, name="Go in Utrecht", query="skill=Go"))
    assert client.get("/api/admin/accounts").status_code == 401
    r = client.get("/api/admin/accounts", headers={"Authorization": "Bearer secret-token"})
    assert r.headers["cache-control"] == "no-store"
    (a,) = r.json()["accounts"]
    assert a["email"] == "ada@example.org" and a["saved_jobs"] == 2 and a["tracked"] == 1 and a["skills"] == 1
    assert a["searches"] == 1 and a["alerts"] == "weekly" and a["owner"] is False
