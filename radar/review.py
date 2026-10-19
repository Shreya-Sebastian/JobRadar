"""Agentic review of the postings the rules are unsure about.

The rules (radar/classify.py, radar/extract/rules.py) stay first. A script on the owner's laptop
(scripts/review_week.py) fetches the uncertain open postings from the admin API, has each batch read, and posts back
one decision per field with a confidence and a reason. Decisions are kept in their own table (ReviewDecision),
apart from Posting.extraction, so the nightly re-extraction never wipes them, and merged into the rows when they
load (radar/stats.py load_rows) and into the job page. Only the fields in RADAR_REVIEW_FIELDS at or above
RADAR_REVIEW_THRESHOLD are applied; with either unset (the default) nothing reaches the site.

`is_tech` decisions are stored and measured but never applied: the column is read directly in many places (counts,
job pages, the sitemap, Google notices), which would disagree with the merged rows.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, get_args

from sqlalchemy import case, exists, func, select
from sqlalchemy.orm import Session

from radar.config import settings
from radar.extract.schema import RoleFamily, Seniority
from radar.models import Posting, ReviewDecision

FIELDS = ("is_tech", "role_family", "seniority", "years_experience", "english_only", "dutch_required",
          "english_required", "visa_sponsorship")
APPLICABLE = frozenset(FIELDS) - {"is_tech"}
TEXT_LIMIT = 6000  # characters of the posting text sent for review

_EXPERIENCE_WORDS = re.compile(r"experien|ervaring|\byears?\b|\bjaar\b|\bjaren\b", re.I)
_VISA_WORDS = re.compile(r"visa|sponsor|relocat|kennismigrant|highly skilled migrant", re.I)
_ENUMS = {"role_family": frozenset(get_args(RoleFamily)), "seniority": frozenset(get_args(Seniority))}


def uncertain_points(title: str, description: str, is_tech: bool | None, score: float | None,
                     ex: dict[str, Any] | None) -> list[str]:
    """Which of the rules' readings of a posting are unsure. Only tech postings have an extraction to doubt; a
    posting whose tech score sits near the line is unsure either way."""
    from radar.stats import experience_band

    points = []
    if score is not None and 0.3 <= score <= 0.7:
        points.append("tech_score")
    if not is_tech:
        return points
    ex = ex or {}
    text = description or ""
    if (ex.get("role_family") or "other") == "other":
        points.append("role_family")
    if experience_band(ex, title) == "unspecified" and (ex.get("seniority") or "unknown") == "unknown" \
            and _EXPERIENCE_WORDS.search(text):
        points.append("experience")
    if not ex.get("english_only") and not ex.get("dutch_required"):
        points.append("language")
    if ex.get("visa_sponsorship") is None and _VISA_WORDS.search(text):
        points.append("visa")
    return points


def rules_values(is_tech: bool | None, score: float | None, ex: dict[str, Any] | None) -> dict[str, Any]:
    ex = ex or {}
    out: dict[str, Any] = {"is_tech": is_tech, "tech_score": score}
    for f in FIELDS[1:]:
        out[f] = ex.get(f)
    out["years_experience_text"] = ex.get("years_experience_text")
    out["posting_language"] = ex.get("posting_language")
    return out


def _trim(text: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text.rfind(" ", int(limit * 0.9), limit)
    return text[:cut if cut > 0 else limit].rstrip(), True


# sentences after the cut that can decide a field: universities and large employers put relocation, the 30% ruling,
# permits and language requirements in the closing sections of long postings
_DECIDING = re.compile(r"visa|vis(?:um|a)|sponsor|relocat|verhuis|kennismigrant|highly skilled|30\s?%|permit|"
                       r"vergunning|nationalit|citizen|passport|right to work|immigration|your move|welcome cent|"
                       r"\bdutch\b|nederlands|english|engels|experien|ervaring", re.I)
TAIL_LIMIT = 1500


def review_text(text: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    """The text sent for review: the first `limit` characters and, when it is longer, the sentences after the cut
    that can decide a field, each with the sentence that follows it ("Will you need to relocate? We make your move
    smooth"), up to TAIL_LIMIT characters. Returns (text, truncated)."""
    head, truncated = _trim(text or "", limit)
    if not truncated:
        return head, False
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text[len(head):]) if s.strip()]
    keep = sorted({j for i, s in enumerate(sentences) if _DECIDING.search(s) for j in (i, i + 1)
                   if j < len(sentences)})
    picked, size = [], 0
    for i in keep:
        if size + len(sentences[i]) > TAIL_LIMIT:
            break
        picked.append(sentences[i])
        size += len(sentences[i]) + 1
    if not picked:
        return head + " [...]", True
    return head + " [...]\n\nLater in the posting: " + " ".join(picked), True


def candidates(session: Session, since: datetime | None = None, limit: int = 100, after_id: int = 0,
               include_reviewed: bool = False) -> dict[str, Any]:
    """Uncertain open postings in id order, `limit` at most; `next_after_id` continues the scan (None at the end).
    Postings that already have a decision are left out unless `include_reviewed`."""
    stmt = (
        select(Posting.id, Posting.title, Posting.company, Posting.url, Posting.first_seen, Posting.description,
               Posting.is_tech, Posting.tech_score, Posting.extraction)
        .where(Posting.closed_at.is_(None), Posting.duplicate_of.is_(None), Posting.id > after_id)
        .where(Posting.is_tech.is_(True) | Posting.tech_score.between(0.3, 0.7))
        .order_by(Posting.id)
        .execution_options(yield_per=500)  # descriptions are large: never all of them in memory at once
    )
    if since is not None:
        stmt = stmt.where(Posting.first_seen >= since)
    if not include_reviewed:
        stmt = stmt.where(~exists().where(ReviewDecision.posting_id == Posting.id))
    items: list[dict[str, Any]] = []
    more = False
    for p in session.execute(stmt):
        points = uncertain_points(p.title, p.description, p.is_tech, p.tech_score, p.extraction)
        if not points:
            continue
        if len(items) >= limit:
            more = True
            break
        text, truncated = review_text(p.description or "")
        items.append({"id": p.id, "title": p.title, "company": p.company, "url": p.url,
                      "first_seen": p.first_seen.isoformat(), "text": text, "truncated": truncated,
                      "rules": rules_values(p.is_tech, p.tech_score, p.extraction), "uncertain": points})
    return {"items": items, "next_after_id": items[-1]["id"] if more and items else None, "fields": list(FIELDS)}


def check_value(field: str, value: Any) -> str | None:
    """Why a decided value is not valid for its field, or None. "Not stated" is None for the yes/no fields and the
    years, "unknown" for the level and "other" for the field of work."""
    if field not in FIELDS:
        return f"unknown field {field!r}"
    if field == "is_tech":
        return None if isinstance(value, bool) else "is_tech must be true or false"
    if field in _ENUMS:
        return None if value in _ENUMS[field] else f"{field} must be one of {sorted(_ENUMS[field])}"
    if field == "years_experience":
        if value is None or (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 40):
            return None
        return "years_experience must be a whole number from 0 to 40, or null"
    return None if value is None or isinstance(value, bool) else f"{field} must be true, false or null"


def record(session: Session, run_id: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the valid decisions with the rules' current value beside each; report the invalid ones by index.
    The caller commits."""
    ids = {d.get("posting_id") for d in decisions if isinstance(d.get("posting_id"), int)}
    known = {p.id: p for p in session.execute(
        select(Posting.id, Posting.is_tech, Posting.extraction).where(Posting.id.in_(ids)))} if ids else {}
    now = datetime.utcnow()
    invalid = []
    stored = 0
    for i, d in enumerate(decisions):
        field, value, confidence = d.get("field"), d.get("value"), d.get("confidence")
        p = known.get(d.get("posting_id"))
        error = check_value(field, value)
        if error is None and p is None:
            error = "no such posting"
        if error is None and not (isinstance(confidence, int | float) and not isinstance(confidence, bool)
                                  and 0.0 <= confidence <= 1.0):
            error = "confidence must be between 0 and 1"
        if error is None and not (d.get("model") or "").strip():
            error = "model is required"
        if error:
            invalid.append({"index": i, "posting_id": d.get("posting_id"), "field": field, "error": error})
            continue
        old = p.is_tech if field == "is_tech" else (p.extraction or {}).get(field)
        session.add(ReviewDecision(posting_id=p.id, field=field, old_value=old, new_value=value,
                                   confidence=float(confidence), reason=(d.get("reason") or "")[:2000],
                                   model=d["model"].strip()[:60], run_id=run_id[:60], created_at=now))
        stored += 1
    session.flush()
    return {"stored": stored, "invalid": invalid, "applied_changed": reapply(session)}


def enabled_fields() -> set[str]:
    return {f.strip() for f in (settings.review_fields or "").split(",") if f.strip()} & APPLICABLE


def reapply(session: Session) -> int:
    """Mark which decisions are in effect: per posting and field the newest one not rejected, when its field is
    switched on and its confidence reaches the threshold (a newer, less sure reading replaces an older one rather
    than falling back to it). Returns how many changed; the caller commits and bumps the data version."""
    threshold, fields = settings.review_threshold, enabled_fields()
    newest: dict[tuple[int, str], Any] = {}
    rows = session.execute(select(ReviewDecision.id, ReviewDecision.posting_id, ReviewDecision.field,
                                  ReviewDecision.confidence, ReviewDecision.rejected_at, ReviewDecision.applied)
                           .order_by(ReviewDecision.created_at, ReviewDecision.id)).all()
    for r in rows:
        if r.rejected_at is None:
            newest[(r.posting_id, r.field)] = r
    keep = {r.id for r in newest.values()
            if threshold is not None and r.field in fields and r.confidence >= threshold}
    flips = [r.id for r in rows if bool(r.applied) != (r.id in keep)]
    for i in range(0, len(flips), 500):
        for d in session.scalars(select(ReviewDecision).where(ReviewDecision.id.in_(flips[i:i + 500]))):
            d.applied = d.id in keep
    session.flush()
    return len(flips)


def overrides(session: Session, posting_id: int | None = None) -> dict[int, dict[str, Any]]:
    """{posting id: {field: value}} of the decisions in effect."""
    stmt = select(ReviewDecision.posting_id, ReviewDecision.field, ReviewDecision.new_value).where(
        ReviewDecision.applied.is_(True))
    if posting_id is not None:
        stmt = stmt.where(ReviewDecision.posting_id == posting_id)
    out: dict[int, dict[str, Any]] = {}
    for pid, field, value in session.execute(stmt):
        if field in APPLICABLE:
            out.setdefault(pid, {})[field] = value
    return out


def merge(ex: dict[str, Any], ov: dict[str, Any] | None) -> dict[str, Any]:
    """The extraction with the decisions in effect laid over it. A changed number of years replaces the worded
    range too ("3-5" no longer holds once the years read 2)."""
    if not ov:
        return ex
    out = {**ex, **ov}
    if "years_experience" in ov and ov["years_experience"] != ex.get("years_experience"):
        out["years_experience_text"] = str(ov["years_experience"]) if ov["years_experience"] else None
    return out


def latest(session: Session, limit: int = 200, field: str | None = None, run_id: str | None = None,
           status: str | None = None) -> dict[str, Any]:
    """The newest decisions for the admin page, with each posting's title, and the recent runs."""
    from radar.pages import job_path

    stmt = (select(ReviewDecision, Posting.title, Posting.company)
            .join(Posting, Posting.id == ReviewDecision.posting_id)
            .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc()).limit(limit))
    if field:
        stmt = stmt.where(ReviewDecision.field == field)
    if run_id:
        stmt = stmt.where(ReviewDecision.run_id == run_id)
    if status == "applied":
        stmt = stmt.where(ReviewDecision.applied.is_(True))
    elif status == "rejected":
        stmt = stmt.where(ReviewDecision.rejected_at.is_not(None))
    items = []
    for d, title, company in session.execute(stmt):
        items.append({"id": d.id, "posting_id": d.posting_id, "title": title, "company": company,
                      "path": job_path(d.posting_id, title, "en"), "field": d.field, "old": d.old_value,
                      "new": d.new_value, "confidence": d.confidence, "reason": d.reason, "model": d.model,
                      "run_id": d.run_id, "created_at": d.created_at.isoformat(timespec="seconds"),
                      "applied": d.applied, "rejected": d.rejected_at is not None})
    runs = [{"run_id": r.run_id, "model": r.model, "decisions": r.n, "applied": int(r.applied or 0),
             "at": r.at.isoformat(timespec="seconds")}
            for r in session.execute(
                select(ReviewDecision.run_id, ReviewDecision.model, func.count().label("n"),
                       func.sum(case((ReviewDecision.applied.is_(True), 1), else_=0)).label("applied"),
                       func.max(ReviewDecision.created_at).label("at"))
                .group_by(ReviewDecision.run_id, ReviewDecision.model)
                .order_by(func.max(ReviewDecision.created_at).desc()).limit(20))]
    return {"items": items, "runs": runs, "fields": list(FIELDS),
            "settings": {"threshold": settings.review_threshold, "fields": sorted(enabled_fields())}}


def reject(session: Session, decision_id: int, undo: bool = False) -> tuple[ReviewDecision | None, int]:
    """Reject a decision in a spot check (or take the rejection back). Returns the decision and how many applied
    flags changed; the caller commits."""
    d = session.get(ReviewDecision, decision_id)
    if d is None:
        return None, 0
    d.rejected_at = None if undo else (d.rejected_at or datetime.utcnow())
    session.flush()
    return d, reapply(session)
