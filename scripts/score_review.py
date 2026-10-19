"""Score the rules alone against the rules with the agentic review's decisions, on the filter test set.

Reads the decisions a `review_week.py --golden` run wrote down and applies them as the site would: only to the
postings the rules are unsure about (radar/review.py uncertain_points) and only at or above a confidence threshold.
Without --threshold it tries several and shows, per field, how many rule errors the review fixes and how many right
readings it breaks; choose the threshold on "dev", then score "test" once with it.

    python scripts/score_review.py review_runs/<run id> --split dev
    python scripts/score_review.py review_runs/<run id> --split test --threshold 0.85
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.classify import is_tech, tech_score  # noqa: E402
from radar.extract.rules import extract_rules  # noqa: E402
from radar.filter_eval import run_filter_eval, wilson  # noqa: E402
from radar.review import uncertain_points  # noqa: E402

THRESHOLDS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)
# what each decided field is checked against in the labels; english_only is "no Dutch required"
LABEL = {"is_tech": lambda lab: lab["is_tech"], "role_family": lambda lab: lab.get("role_family"),
         "seniority": lambda lab: lab.get("seniority"), "years_experience": lambda lab: lab.get("years_experience"),
         "dutch_required": lambda lab: lab.get("dutch_required"),
         "english_only": lambda lab: None if lab.get("dutch_required") is None else not lab["dutch_required"],
         "visa_sponsorship": lambda lab: lab.get("visa_sponsorship")}
# the measures of radar/filter_eval.py that each field moves
MEASURES = {"is_tech": ["tech: accuracy", "tech: precision", "tech: recall"], "role_family": ["role family"],
            "seniority": ["level"], "years_experience": ["years of experience", "experience band"],
            "dutch_required": ["Dutch required"],
            "english_only": ["English filter: no Dutch needed", "English filter: found"],
            "visa_sponsorship": ["visa", "visa filter: correct"]}


def load_items(path: str, split: str) -> list[dict]:
    items = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    return [it for it in items if split == "all" or it.get("split") == split]


def load_decisions(run_dirs: list[str]) -> dict[int, dict[str, tuple]]:
    """{posting id: {field: (value, confidence)}}; a later run's decision replaces an earlier one."""
    out: dict[int, dict[str, tuple]] = {}
    for d in run_dirs:
        for line in (Path(d) / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            out.setdefault(x["posting_id"], {})[x["field"]] = (x["value"], x["confidence"])
    return out


def rules_reading(it: dict) -> tuple[bool, float, dict]:
    tech = is_tech(it["title"], it["description"])
    ex = extract_rules(it["title"], it["description"]).model_dump()
    return tech, tech_score(it["title"], it["description"]), ex


def candidates(items: list[dict]) -> set[int]:
    """The postings the server would hand to the review."""
    out = set()
    for it in items:
        tech, score, ex = rules_reading(it)
        if uncertain_points(it["title"], it["description"], tech, score, ex if tech else None):
            out.add(it["id"])
    return out


def overrides(decisions: dict, ids: set[int], threshold: float, fields: set[str] | None = None) -> dict:
    return {pid: {f: v for f, (v, c) in fs.items() if c >= threshold and (fields is None or f in fields)}
            for pid, fs in decisions.items() if pid in ids}


def fixes_and_breaks(items: list[dict], decisions: dict, ids: set[int], threshold: float) -> dict[str, list[int]]:
    """Per field [applied, fixed, broken, both wrong]: a fix turns a wrong rule reading right, a break a right one
    wrong. Fields other than is_tech count only on postings that are tech by their label, as filter_eval does."""
    out = {f: [0, 0, 0, 0] for f in LABEL}
    for it in items:
        if it["id"] not in ids:
            continue
        tech, _, ex = rules_reading(it)
        rules = {"is_tech": tech, **ex, "english_only": ex.get("english_only")}
        for field, (value, conf) in decisions.get(it["id"], {}).items():
            if field not in LABEL or conf < threshold or (field != "is_tech" and not it["labels"]["is_tech"]):
                continue
            want = LABEL[field](it["labels"])
            old = rules.get(field)
            if field == "english_only":  # the filter shows a job when english_only is set
                old, value = bool(old), bool(value)
            if value == old:
                continue
            row = out[field]
            row[0] += 1
            if old != want and value == want:
                row[1] += 1
            elif old == want and value != want:
                row[2] += 1
            else:
                row[3] += 1
    return out


def fmt(m) -> str:
    lo, hi = wilson(m.k, m.n)
    return f"{m.value:.1%} ({m.k}/{m.n}, {lo:.0%}-{hi:.0%})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="review_runs/<run id> folders")
    ap.add_argument("--set", default="data/golden/filters.jsonl")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--threshold", type=float, help="score at this threshold only")
    ap.add_argument("--fields", help="only these fields (comma-separated), as RADAR_REVIEW_FIELDS")
    args = ap.parse_args(argv)

    items = load_items(args.set, args.split)
    decisions = load_decisions(args.runs)
    ids_all = {it["id"] for it in items}
    reviewed = ids_all & set(decisions)
    cand = candidates(items) & reviewed
    fields = set(args.fields.split(",")) if args.fields else None
    print(f"{args.split}: {len(items)} postings, {len(reviewed)} with decisions, {len(cand)} of them uncertain by "
          f"the rules (the ones the site would review)\n")

    if args.threshold is None:
        print("Fixed / broken rule readings per field on the uncertain postings, by confidence threshold\n")
        print("| threshold | " + " | ".join(LABEL) + " | net |")
        print("|---|" + "---|" * (len(LABEL) + 1))
        for t in THRESHOLDS:
            fb = fixes_and_breaks(items, decisions, cand, t)
            net = sum(fb[f][1] - fb[f][2] for f in LABEL if fields is None or f in fields)
            print(f"| {t:.2f} | " + " | ".join(f"+{fb[f][1]} -{fb[f][2]}" for f in LABEL) + f" | {net:+d} |")
        print()
        threshold = 0.0
    else:
        threshold = args.threshold

    base = run_filter_eval(args.set, args.split)
    both = run_filter_eval(args.set, args.split, overrides(decisions, cand, threshold, fields))
    every = run_filter_eval(args.set, args.split, overrides(decisions, reviewed, threshold, fields))
    fb = fixes_and_breaks(items, decisions, cand, threshold)
    print(f"Per filter at threshold {threshold:.2f} (95% intervals in brackets)\n")
    print("| field | measure | rules | rules + review | change | applied | fixed | broken | both wrong |"
          " if every posting were reviewed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for field, names in MEASURES.items():
        if fields is not None and field not in fields:
            continue
        for i, name in enumerate(names):
            a, b, e = base.get(name), both.get(name), every.get(name)
            counts = " | ".join(str(x) for x in fb[field]) if i == 0 else " | | |"
            print(f"| {field if i == 0 else ''} | {name} | {fmt(a)} | {fmt(b)} | {b.value - a.value:+.1%} | "
                  f"{counts} | {fmt(e)} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
