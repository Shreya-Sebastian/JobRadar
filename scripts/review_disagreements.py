"""The week's review decisions that disagree with the rules, grouped, with the posting texts: the input for the weekly
rule proposals (rule changes with tests, committed for the owner to approve; RULES_VERSION bumped when rules change).

Reads the admin API of the site (needs RADAR_ADMIN_TOKEN) and writes review_runs/disagreements-<date>.md (to read)
and .json (with the texts, to test rule changes against). Only confident decisions count, and for the fields other
than is_tech only postings the rules hold to be tech, since only those are listed on the site.

    python scripts/review_disagreements.py --server https://techjobsradar.nl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.review import FIELDS  # noqa: E402

USER_AGENT = "TechJobsRadar-review/1.0 (admin script)"


def fetch(server: str, token: str, since: str) -> tuple[list[dict], dict[int, dict]]:
    """(decisions, {posting id: candidate with text and the rules' values})."""
    import httpx

    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    decisions: list[dict] = []
    postings: dict[int, dict] = {}
    with httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=120) as c:
        for field in FIELDS:  # one field at a time: the list stops at 2,000 rows
            r = c.get("/api/admin/review/decisions", params={"field": field, "limit": 2000})
            r.raise_for_status()
            decisions += [d for d in r.json()["items"] if d["created_at"][:10] >= since]
        after = 0
        while True:
            r = c.get("/api/admin/review/candidates",
                      params={"since": since, "limit": 500, "after_id": after, "include_reviewed": True})
            r.raise_for_status()
            page = r.json()
            postings.update({p["id"]: p for p in page["items"]})
            if page["next_after_id"] is None:
                break
            after = page["next_after_id"]
    return decisions, postings


def disagreements(decisions: list[dict], postings: dict[int, dict], min_confidence: float) -> dict[str, list[dict]]:
    """{"field: rules -> review": [decision with its posting]}, the newest decision per posting and field."""
    newest: dict[tuple[int, str], dict] = {}
    for d in sorted(decisions, key=lambda d: (d["created_at"], d["id"])):
        if not d["rejected"]:
            newest[(d["posting_id"], d["field"])] = d
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in newest.values():
        p = postings.get(d["posting_id"])
        if p is None or d["confidence"] < min_confidence or d["old"] == d["new"]:
            continue
        if d["field"] != "is_tech" and not p["rules"]["is_tech"]:
            continue  # not listed on the site: its other fields do not matter yet
        groups[f"{d['field']}: {d['old']} -> {d['new']}"].append({**d, "text": p["text"], "url": p["url"]})
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


def report(groups: dict[str, list[dict]], since: str, min_confidence: float) -> str:
    out = [f"# Review disagreements since {since} (confidence {min_confidence} or more, rules' tech postings)\n"]
    out.append("| Disagreement | Postings |\n|---|---|")
    out += [f"| {k} | {len(v)} |" for k, v in groups.items()]
    for k, items in groups.items():
        out.append(f"\n## {k} ({len(items)})\n")
        for d in sorted(items, key=lambda d: -d["confidence"]):
            out.append(f"- {d['posting_id']} {d['title']} ({d['company']}), {d['confidence']:.2f}: {d['reason']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", required=True)
    ap.add_argument("--since", default=(date.today() - timedelta(days=7)).isoformat())
    ap.add_argument("--min-confidence", type=float, default=0.8)
    ap.add_argument("--out", default="review_runs")
    args = ap.parse_args(argv)
    token = os.environ.get("RADAR_ADMIN_TOKEN") or sys.exit("set RADAR_ADMIN_TOKEN to the site's admin token")
    decisions, postings = fetch(args.server, token, args.since)
    groups = disagreements(decisions, postings, args.min_confidence)
    base = Path(args.out) / f"disagreements-{date.today().isoformat()}"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".md").write_text(report(groups, args.since, args.min_confidence), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(groups, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(decisions)} decisions on {len(postings)} postings; {sum(map(len, groups.values()))} disagreements "
          f"in {len(groups)} groups; written to {base}.md and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
