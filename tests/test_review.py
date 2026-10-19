"""The agentic review's server side (radar/review.py): the uncertain postings handed out, the decisions stored
apart from the extraction, applied only per the threshold and field list, and rejected from the admin page."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from radar.config import settings

TOKEN = {"Authorization": "Bearer s3cret"}

SURE = {"role_family": "backend", "seniority": "senior", "years_experience": 5, "years_experience_text": "5+",
        "english_only": True, "dutch_required": False, "english_required": True, "visa_sponsorship": None,
        "posting_language": "en", "skills_required": ["Python"], "skills_nice": []}


@pytest.fixture
def admin(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    monkeypatch.setattr(settings, "review_threshold", None)
    monkeypatch.setattr(settings, "review_fields", "")
    from radar.api import app

    return TestClient(app)


def _add(**kw) -> int:
    from radar.db import session_scope
    from radar.models import Posting, Source

    with session_scope() as s:
        src = s.query(Source).first()
        if src is None:
            src = Source(company="Acme", ats="lever", slug="acme")
            s.add(src)
            s.flush()
        n = s.query(Posting).count() + 1
        fields = {"title": "Backend Engineer", "company": "Acme", "url": f"https://acme.example/{n}",
                  "description": "Build our APIs in Python.", "is_tech": True, "tech_score": 0.95,
                  "extraction": dict(SURE), "content_hash": f"h{n}", "dedup_key": f"d{n}", "city": "Utrecht",
                  "country": "NL", "first_seen": datetime.utcnow()}
        fields.update(kw)
        p = Posting(source_id=src.id, external_id=str(n), **fields)
        s.add(p)
        s.flush()
        return p.id


def _rows():
    from radar.db import new_session
    from radar.stats import load_rows

    s = new_session()
    try:
        return {r.id: r for r in load_rows(s)}
    finally:
        s.close()


def _decide(client, *decisions, run_id="run-1"):
    body = {"run_id": run_id, "decisions": [{"model": "claude-opus-5-5", "reason": "stated in the text", **d}
                                            for d in decisions]}
    r = client.post("/api/admin/review/decisions", json=body, headers=TOKEN)
    assert r.status_code == 200, r.text
    return r.json()


# which postings count as uncertain

def test_uncertain_points_name_each_doubt():
    from radar.review import uncertain_points

    assert uncertain_points("Backend Engineer", "Python", True, 0.95, SURE) == []
    assert uncertain_points("Analyst", "", False, 0.4, None) == ["tech_score"]
    assert uncertain_points("Analyst", "", False, 0.1, None) == []  # clearly not tech: nothing to doubt
    assert uncertain_points("Engineer", "", True, 0.9, {**SURE, "role_family": "other"}) == ["role_family"]
    vague = {**SURE, "seniority": "unknown", "years_experience": None, "years_experience_text": None}
    assert uncertain_points("Engineer", "Relevant experience in Go.", True, 0.9, vague) == ["experience"]
    assert uncertain_points("Engineer", "Go and Rust.", True, 0.9, vague) == []  # the text says nothing of it
    assert uncertain_points("Junior Engineer", "Some experience.", True, 0.9, vague) == []  # entry level: "none"
    assert uncertain_points("Engineer", "Go.", True, 0.9, {**SURE, "english_only": False}) == ["language"]
    assert uncertain_points("Engineer", "Relocation support offered.", True, 0.9, SURE) == ["visa"]
    assert uncertain_points("Engineer", "We sponsor visas.", True, 0.9, {**SURE, "visa_sponsorship": True}) == []


# candidates

def test_review_endpoints_need_the_admin_token(admin, monkeypatch):
    assert admin.get("/api/admin/review/candidates").status_code == 401
    assert admin.get("/api/admin/review/candidates", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert admin.post("/api/admin/review/decisions", json={"run_id": "x", "decisions": []}).status_code == 401
    assert admin.get("/api/admin/review/decisions").status_code == 401
    assert admin.post("/api/admin/review/decisions/1/reject").status_code == 401
    monkeypatch.setattr(settings, "admin_token", None)
    assert admin.get("/api/admin/review/candidates", headers=TOKEN).status_code == 404


def test_candidates_are_the_uncertain_open_postings_with_the_rules_values(admin):
    sure = _add()
    near_line = _add(title="Business Analyst", is_tech=False, tech_score=0.4, extraction=None)
    other_field = _add(extraction={**SURE, "role_family": "other"})
    visa = _add(description="Relocation package and visa support. " + "Python and Go. " * 600)
    _add(extraction={**SURE, "role_family": "other"}, closed_at=datetime.utcnow())  # closed
    _add(extraction={**SURE, "role_family": "other"}, duplicate_of=sure)  # a merged copy
    _add(title="Office Manager", is_tech=False, tech_score=0.05, extraction=None)  # clearly not tech

    out = admin.get("/api/admin/review/candidates", headers=TOKEN).json()
    items = {i["id"]: i for i in out["items"]}
    assert set(items) == {near_line, other_field, visa}
    assert out["next_after_id"] is None
    assert items[near_line]["uncertain"] == ["tech_score"]
    assert items[near_line]["rules"]["is_tech"] is False and items[near_line]["rules"]["tech_score"] == 0.4
    assert items[other_field]["rules"]["role_family"] == "other"
    assert items[visa]["uncertain"] == ["visa"]
    assert items[visa]["truncated"] and 5000 < len(items[visa]["text"]) <= 6010
    assert "is_tech" in out["fields"] and "visa_sponsorship" in out["fields"]


def test_candidates_page_by_id_and_skip_reviewed_postings(admin):
    ids = [_add(extraction={**SURE, "role_family": "other"}) for _ in range(5)]
    first = admin.get("/api/admin/review/candidates?limit=2", headers=TOKEN).json()
    assert [i["id"] for i in first["items"]] == ids[:2] and first["next_after_id"] == ids[1]
    rest = admin.get(f"/api/admin/review/candidates?limit=10&after_id={ids[1]}", headers=TOKEN).json()
    assert [i["id"] for i in rest["items"]] == ids[2:] and rest["next_after_id"] is None

    _decide(admin, {"posting_id": ids[0], "field": "role_family", "value": "data", "confidence": 0.9})
    again = admin.get("/api/admin/review/candidates", headers=TOKEN).json()
    assert ids[0] not in [i["id"] for i in again["items"]]
    every = admin.get("/api/admin/review/candidates?include_reviewed=true", headers=TOKEN).json()
    assert ids[0] in [i["id"] for i in every["items"]]


def test_candidates_since(admin):
    old = _add(extraction={**SURE, "role_family": "other"}, first_seen=datetime.utcnow() - timedelta(days=20))
    new = _add(extraction={**SURE, "role_family": "other"})
    since = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
    got = [i["id"] for i in admin.get(f"/api/admin/review/candidates?since={since}", headers=TOKEN).json()["items"]]
    assert got == [new] and old not in got
    assert admin.get("/api/admin/review/candidates?since=last-week", headers=TOKEN).status_code == 422


# decisions

def test_decisions_are_stored_with_the_rules_value_and_invalid_ones_reported(admin):
    from radar.db import session_scope
    from radar.models import Posting, ReviewDecision

    pid = _add(extraction={**SURE, "role_family": "other"})
    out = _decide(admin,
                  {"posting_id": pid, "field": "role_family", "value": "solutions", "confidence": 0.92},
                  {"posting_id": pid, "field": "visa_sponsorship", "value": None, "confidence": 0.8},
                  {"posting_id": pid, "field": "salary", "value": 1, "confidence": 0.9},
                  {"posting_id": pid, "field": "seniority", "value": "wizard", "confidence": 0.9},
                  {"posting_id": pid, "field": "years_experience", "value": "five", "confidence": 0.9},
                  {"posting_id": pid, "field": "is_tech", "value": True, "confidence": 1.4},
                  {"posting_id": pid, "field": "is_tech", "value": True, "confidence": 0.9, "model": ""},
                  {"posting_id": 99999, "field": "is_tech", "value": True, "confidence": 0.9})
    assert out["stored"] == 2 and out["applied_changed"] == 0
    assert [(e["index"], e["error"].split(" ")[0]) for e in out["invalid"]] == [
        (2, "unknown"), (3, "seniority"), (4, "years_experience"), (5, "confidence"), (6, "model"), (7, "no")]
    with session_scope() as s:
        d = s.query(ReviewDecision).filter_by(field="role_family").one()
        assert (d.old_value, d.new_value, d.confidence, d.run_id, d.applied) == ("other", "solutions", 0.92, "run-1",
                                                                                 False)
        assert s.query(ReviewDecision).filter_by(field="visa_sponsorship").one().new_value is None
        assert s.get(Posting, pid).extraction["role_family"] == "other"  # the extraction itself is untouched


def test_nothing_is_applied_by_default(admin):
    pid = _add(extraction={**SURE, "english_only": False, "dutch_required": False})
    _decide(admin, {"posting_id": pid, "field": "dutch_required", "value": True, "confidence": 0.99})
    assert _rows()[pid].ex["dutch_required"] is False


def test_applied_only_for_switched_on_fields_at_or_above_the_threshold(admin, monkeypatch):
    monkeypatch.setattr(settings, "review_threshold", 0.8)
    monkeypatch.setattr(settings, "review_fields", "visa_sponsorship,role_family,is_tech")
    a, b = _add(), _add()
    out = _decide(admin,
                  {"posting_id": a, "field": "visa_sponsorship", "value": True, "confidence": 0.8},
                  {"posting_id": b, "field": "visa_sponsorship", "value": True, "confidence": 0.79},
                  {"posting_id": a, "field": "seniority", "value": "lead", "confidence": 0.99},  # field not on
                  {"posting_id": b, "field": "is_tech", "value": False, "confidence": 0.99})  # never applied
    assert out["applied_changed"] == 1
    rows = _rows()
    assert rows[a].ex["visa_sponsorship"] is True and rows[a].ex["seniority"] == "senior"
    assert rows[b].ex["visa_sponsorship"] is None and b in rows


def test_the_newest_decision_wins_even_when_less_sure(admin, monkeypatch):
    monkeypatch.setattr(settings, "review_threshold", 0.8)
    monkeypatch.setattr(settings, "review_fields", "role_family")
    pid = _add(extraction={**SURE, "role_family": "other"})
    _decide(admin, {"posting_id": pid, "field": "role_family", "value": "data", "confidence": 0.9}, run_id="r1")
    assert _rows()[pid].ex["role_family"] == "data"
    _decide(admin, {"posting_id": pid, "field": "role_family", "value": "ml", "confidence": 0.95}, run_id="r2")
    assert _rows()[pid].ex["role_family"] == "ml"
    _decide(admin, {"posting_id": pid, "field": "role_family", "value": "backend", "confidence": 0.5}, run_id="r3")
    assert _rows()[pid].ex["role_family"] == "other"  # the latest reading is unsure: back to the rules


def test_applied_decisions_reach_the_filters_and_the_job_page(admin, monkeypatch):
    from radar.stats import Filters

    monkeypatch.setattr(settings, "review_threshold", 0.8)
    monkeypatch.setattr(settings, "review_fields", "visa_sponsorship,years_experience")
    pid = _add(extraction={**SURE, "years_experience": 3, "years_experience_text": "3-5"})
    _decide(admin, {"posting_id": pid, "field": "visa_sponsorship", "value": True, "confidence": 0.9},
            {"posting_id": pid, "field": "years_experience", "value": 7, "confidence": 0.9})
    row = _rows()[pid]
    assert row.ex["years_experience_text"] == "7" and row.experience_bands == {"6+"}  # the old range is gone
    assert [r.id for r in Filters(sponsorship=True).apply(list(_rows().values()))] == [pid]
    page = admin.get(f"/job/{pid}/backend-engineer").text
    assert "7 years" in page and "visa" in page.lower()


def test_merge_keeps_the_worded_range_when_the_years_agree():
    from radar.review import merge

    ex = {"years_experience": 3, "years_experience_text": "3-5"}
    assert merge(ex, {"years_experience": 3})["years_experience_text"] == "3-5"
    assert merge(ex, {"years_experience": None})["years_experience_text"] is None
    assert merge(ex, None) is ex


def test_decisions_outlast_the_nightly_reextraction(admin, monkeypatch):
    from radar import tasks
    from radar.db import session_scope
    from radar.models import Posting

    monkeypatch.setattr(settings, "review_threshold", 0.8)
    monkeypatch.setattr(settings, "review_fields", "visa_sponsorship")
    pid = _add(description="Build our APIs in Python and Go for the payments team.", extractor_version="rules-v1")
    _decide(admin, {"posting_id": pid, "field": "visa_sponsorship", "value": True, "confidence": 0.9})
    assert tasks.reextract()["reextracted"] == 1
    with session_scope() as s:
        assert s.get(Posting, pid).extraction["visa_sponsorship"] is None  # the rules' reading, rewritten
    assert _rows()[pid].ex["visa_sponsorship"] is True  # the decision still laid over it


def test_a_changed_threshold_takes_effect_at_the_nightly_run(admin, monkeypatch):
    from radar import tasks

    pid = _add()
    _decide(admin, {"posting_id": pid, "field": "visa_sponsorship", "value": True, "confidence": 0.9})
    assert _rows()[pid].ex["visa_sponsorship"] is None
    monkeypatch.setattr(settings, "review_threshold", 0.85)
    monkeypatch.setattr(settings, "review_fields", "visa_sponsorship")
    assert tasks.reextract()["review_changed"] == 1
    assert _rows()[pid].ex["visa_sponsorship"] is True


# the admin page

def test_reject_takes_a_decision_off_the_site_and_undo_puts_it_back(admin, monkeypatch):
    monkeypatch.setattr(settings, "review_threshold", 0.8)
    monkeypatch.setattr(settings, "review_fields", "visa_sponsorship")
    pid = _add()
    _decide(admin, {"posting_id": pid, "field": "visa_sponsorship", "value": True, "confidence": 0.9})
    listed = admin.get("/api/admin/review/decisions", headers=TOKEN).json()
    d = listed["items"][0]
    assert (d["posting_id"], d["field"], d["old"], d["new"], d["applied"], d["rejected"]) == (
        pid, "visa_sponsorship", None, True, True, False)
    assert d["path"].startswith(f"/job/{pid}/") and d["title"] == "Backend Engineer"
    assert listed["settings"] == {"threshold": 0.8, "fields": ["visa_sponsorship"]}
    assert listed["runs"] == [{"run_id": "run-1", "model": "claude-opus-5-5", "decisions": 1, "applied": 1,
                               "at": d["created_at"]}]

    r = admin.post(f"/api/admin/review/decisions/{d['id']}/reject", headers=TOKEN).json()
    assert r == {"id": d["id"], "rejected": True, "applied": False}
    assert _rows()[pid].ex["visa_sponsorship"] is None
    assert [x["id"] for x in admin.get("/api/admin/review/decisions?status=rejected", headers=TOKEN).json()["items"]] \
        == [d["id"]]
    assert admin.get("/api/admin/review/decisions?status=applied", headers=TOKEN).json()["items"] == []

    r = admin.post(f"/api/admin/review/decisions/{d['id']}/reject?undo=true", headers=TOKEN).json()
    assert r["rejected"] is False and r["applied"] is True
    assert _rows()[pid].ex["visa_sponsorship"] is True
    assert admin.post("/api/admin/review/decisions/999/reject", headers=TOKEN).status_code == 404


def test_the_list_filters_by_field_and_run(admin):
    pid = _add()
    _decide(admin, {"posting_id": pid, "field": "visa_sponsorship", "value": True, "confidence": 0.9}, run_id="a")
    _decide(admin, {"posting_id": pid, "field": "seniority", "value": "lead", "confidence": 0.9}, run_id="b")
    get = lambda q: [x["field"] for x in admin.get(f"/api/admin/review/decisions?{q}", headers=TOKEN).json()["items"]]  # noqa: E731
    assert get("") == ["seniority", "visa_sponsorship"]
    assert get("field=visa_sponsorship") == ["visa_sponsorship"]
    assert get("run_id=b") == ["seniority"]


def test_admin_page_is_served_unindexed_and_uncached(admin):
    r = admin.get("/admin/review")
    assert r.status_code == 200 and "/api/admin/review/decisions" in r.text
    assert r.headers["x-robots-tag"] == "noindex" and r.headers["cache-control"] == "no-store"
    assert "{{" not in r.text and "—" not in r.text


# scoring rules and review together

def test_filter_eval_scores_overrides_laid_over_the_rules():
    import json
    from pathlib import Path

    from radar.extract.rules import extract_rules
    from radar.filter_eval import run_filter_eval

    path = Path(__file__).resolve().parent.parent / "data" / "golden" / "filters.jsonl"
    items = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    miss = next(it for it in items if it["split"] == "dev" and it["labels"]["is_tech"]
                and extract_rules(it["title"], it["description"]).visa_sponsorship != it["labels"]["visa_sponsorship"])
    base = run_filter_eval(path, "dev")
    fixed = run_filter_eval(path, "dev", {miss["id"]: {"visa_sponsorship": miss["labels"]["visa_sponsorship"],
                                                        "is_tech": False}})
    assert fixed.get("visa").k == base.get("visa").k + 1 and fixed.get("visa").n == base.get("visa").n
    assert fixed.get("tech: recall").k == base.get("tech: recall").k - 1  # is_tech is overridden too


def test_long_texts_keep_the_deciding_sentences_after_the_cut():
    from radar.review import TAIL_LIMIT, TEXT_LIMIT, review_text

    body = "We build trading systems in Rust and Python. " * 200  # about 9,000 characters
    closing = ("The office has a gym. Our Welcome Centre arranges your visa and relocation. Non-Dutch candidates may "
               "qualify for the 30% ruling.")
    text, truncated = review_text(body + closing)
    assert truncated and text.startswith(body[:1000])
    tail = text.split("Later in the posting: ")[1]
    assert "visa and relocation" in tail and "30% ruling" in tail and "gym" not in tail
    assert len(text) <= TEXT_LIMIT + TAIL_LIMIT + 40
    assert review_text(body)[0].endswith(" [...]")  # nothing deciding after the cut
    assert review_text("short text") == ("short text", False)
