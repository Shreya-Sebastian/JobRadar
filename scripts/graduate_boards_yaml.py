"""Write the boards with graduate tech roles abroad to data/seeds/global_graduate_boards.yaml.

Usage: python scripts/graduate_boards_yaml.py [--min-grad 3]
Reads the probe state files (`radar probe-boards --graduates`, kept out of git) and lists every live board with at
least --min-grad graduate tech titles outside the Netherlands, biggest first, in the format `radar seed --yaml`
reads. The file is tracked, so the server can add the same boards:
`radar seed --yaml data/seeds/global_graduate_boards.yaml`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from radar.enumerate import PATTERNS, _load_state  # noqa: E402
from radar.registry import _pretty, load_source_kinds  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "seeds" / "global_graduate_boards.yaml"
# staffing firms and job aggregators: they repost other employers' "junior" roles, often many times over
SKIP = {"integratedresourcesinc", "jobsbridge1", "artechinformationsystemllc", "mindlance2", "360itprofessionals1",
        "krgtechnologyinc", "betasoftsystems3", "jobsforhumanity", "jobgether", "hadronts", "goventi", "testnisc"}


def _known_names() -> dict[tuple[str, str], str]:
    """The names the local database already shows, so seeding never replaces a curated name."""
    from sqlalchemy import select

    from radar.db import init_db, session_scope
    from radar.models import Source

    init_db()
    with session_scope() as s:
        return {(src.ats, src.slug.lower()): src.company for src in s.scalars(select(Source))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-grad", type=int, default=1)
    # a Workday board's graduate pass costs about 16 searches per crawl, the others one request: ask more of it
    ap.add_argument("--min-grad-workday", type=int, default=2)
    args = ap.parse_args()
    kinds = load_source_kinds()
    skip = SKIP | {s for k in ("aggregator", "agency", "test", "board") for s in kinds[k]}
    names = _known_names()
    rows = []
    for ats in PATTERNS:
        least = args.min_grad_workday if ats == "workday" else args.min_grad
        for slug, res in _load_state(ats).items():
            if res.get("status") == "ok" and res.get("grad", 0) >= least and slug.lower() not in skip:
                rows.append((res["grad"], ats, slug))
    rows.sort(key=lambda r: (-r[0], r[1], r[2]))
    entries = [{"company": names.get((ats, slug.lower())) or _pretty(slug), "ats": ats, "slug": slug}
               for _, ats, slug in rows]
    header = (f"# Boards with at least {args.min_grad} graduate tech titles outside the Netherlands "
              f"({args.min_grad_workday} on Workday), for\n"
              "# the entry-level site (RADAR_ENTRY_LEVEL_COUNTRIES). Made by scripts/graduate_boards_yaml.py from the\n"
              "# probe state files.\n")
    OUT.write_text(header + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{len(entries)} boards, {sum(r[0] for r in rows)} graduate titles -> {OUT}")


if __name__ == "__main__":
    main()
