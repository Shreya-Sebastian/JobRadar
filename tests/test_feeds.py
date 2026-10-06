from datetime import datetime
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from radar.db import session_scope
from radar.models import Posting, Source


def test_feed_lists_the_newest_matching_jobs_as_rss(fresh_db):
    from radar.api import app

    now = datetime.utcnow()
    with session_scope() as s:
        src = Source(company="Acme", ats="lever", slug="acme")
        s.add(src)
        s.flush()
        for n, (title, city) in enumerate([("Data Engineer", "Utrecht"), ("Backend Developer", "Amsterdam")]):
            s.add(Posting(source_id=src.id, external_id=str(n), title=title, company="Acme", url=f"https://x/{n}",
                          content_hash=str(n), dedup_key=str(n), first_seen=now, last_seen=now, posted_at=now,
                          city=city, country="NL", is_tech=True, description="Python and SQL."))
    client = TestClient(app)
    r = client.get("/feed.xml?city=Utrecht")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    channel = ElementTree.fromstring(r.text).find("channel")
    items = channel.findall("item")
    assert channel.findtext("title") == "TechJar: new jobs (Utrecht)"
    assert [i.findtext("title") for i in items] == ["Data Engineer · Acme"] and "/job/" in items[0].findtext("link")
    nl = ElementTree.fromstring(client.get("/nl/feed.xml").text).find("channel")
    assert len(nl.findall("item")) == 2 and "/nl/vacature/" in nl.find("item").findtext("link")
