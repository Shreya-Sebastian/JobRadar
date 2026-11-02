from radar.enumerate import slugs_from_text


def test_slugs_from_text_extracts_unique_board_slugs_and_skips_vendor_hosts():
    text = "\n".join([
        "https://boards.greenhouse.io/adyen",
        "http://boards.greenhouse.io/adyen/",
        "https://boards.greenhouse.io/embed",
        "https://boards.greenhouse.io/picnic",
    ])
    assert slugs_from_text("greenhouse", text) == ["adyen", "picnic"]
    text = "https://Acme.recruitee.com/\nhttps://acme.recruitee.com\nhttps://careers-analytics.recruitee.com/\n"
    assert slugs_from_text("recruitee", text) == ["acme"]
    assert slugs_from_text("personio", "https://lepaya.jobs.personio.de/\n") == ["lepaya"]
    text = "https://nxp.wd3.myworkdayjobs.com/careers\nhttps://NXP.wd3.myworkdayjobs.com/en-US/careers/\n" \
           "https://acme.wd5.myworkdayjobs.com/wday\n"
    assert slugs_from_text("workday", text) == ["nxp.wd3/careers"]


def _raw(title, location):
    from radar.adapters.base import RawPosting

    return RawPosting(external_id=title, title=title, url=f"https://x/{title}", location=location)


def test_graduate_titles_counts_tech_roles_for_graduates_abroad_only():
    from radar.enumerate import graduate_titles

    postings = [
        _raw("Software Engineering Intern", "Berlin, Germany"),
        _raw("Graduate Data Analyst", "London, United Kingdom"),
        _raw("Junior Backend Developer", "Amsterdam, Netherlands"),  # the Dutch site's, counted as nl
        _raw("Senior Software Engineer", "Paris, France"),
        _raw("Marketing Intern", "Madrid, Spain"),
    ]
    assert graduate_titles(postings) == 2


def test_register_probed_adds_graduate_boards_only_when_asked(fresh_db, tmp_path, monkeypatch):
    import json

    from sqlalchemy import select

    from radar.config import settings
    from radar.db import session_scope
    from radar.enumerate import register_probed
    from radar.models import Source

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    (tmp_path / "enumerated").mkdir()
    (tmp_path / "enumerated" / "probed_greenhouse.json").write_text(json.dumps({
        "dutchco": {"slug": "dutchco", "status": "ok", "total": 30, "nl": 4, "grad": 0},
        "gradco": {"slug": "gradco", "status": "ok", "total": 900, "nl": 0, "grad": 25},
        "fewgrads": {"slug": "fewgrads", "status": "ok", "total": 40, "nl": 0, "grad": 1},
        "gone": {"slug": "gone", "status": "not_found"},
    }), encoding="utf-8")
    with session_scope() as s:
        assert register_probed(s, "greenhouse") == 1
        assert register_probed(s, "greenhouse", min_grad=5) == 1
        by = {src.slug: src.discovered_by for src in s.scalars(select(Source))}
    assert by == {"dutchco": "enumerate", "gradco": "enumerate-grad"}


def test_graduate_probe_revisits_live_boards_without_a_graduate_count(tmp_path, monkeypatch):
    import json

    from radar import enumerate as en
    from radar.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    (tmp_path / "enumerated").mkdir()
    (tmp_path / "enumerated" / "probed_lever.json").write_text(json.dumps({
        "big": {"slug": "big", "status": "ok", "total": 200, "nl": 0},
        "tiny": {"slug": "tiny", "status": "ok", "total": 3, "nl": 0},
        "done": {"slug": "done", "status": "ok", "total": 200, "nl": 0, "grad": 7},
        "gone": {"slug": "gone", "status": "not_found"},
    }), encoding="utf-8")
    probed = []

    def fake_probe(ats, slug, attempts=4, graduates=False):
        probed.append((slug, graduates))
        return {"slug": slug, "status": "ok", "total": 200, "nl": 0, "grad": 12}

    monkeypatch.setattr(en, "_probe_one", fake_probe)
    en.probe_slugs("lever", ["big", "tiny", "done", "gone"], workers=1)
    assert probed == []
    summary = en.probe_slugs("lever", ["big", "tiny", "done", "gone"], workers=1, graduates=True)
    assert probed == [("big", True)]
    assert summary["boards_with_grad"] == 2 and summary["grad_titles_seen"] == 19


def test_greenhouse_slugs_come_from_old_and_new_board_addresses():
    text = "https://boards.greenhouse.io/adyen\nhttps://job-boards.greenhouse.io/monzo/jobs/123456\n" \
           "https://job-boards.greenhouse.io/monzo\nhttps://job-boards.greenhouse.io/embed/job_app\n"
    assert slugs_from_text("greenhouse", text) == ["adyen", "monzo"]
