"""Have the postings the rules are unsure about read by Claude, and send back one decision per field.

A dev tool for the owner's laptop: it runs `claude -p` (Claude Code in print mode, on the owner's own plan login;
no API key, nothing on the server calls Claude). The server only hands out the work and stores the answers
(radar/review.py, /api/admin/review/*). Postings go in batches of about 20: the instructions and field definitions
come first and are identical in every batch, then a few similar labelled examples from the "dev" split of the
filter test set, then the postings. Every answer is checked against the schema; valid decisions are sent (or, with
--golden, only written down for scoring), invalid ones are logged.

    # the week's uncertain postings from the live site (needs RADAR_ADMIN_TOKEN in the environment)
    python scripts/review_week.py --server https://techjobsradar.nl --since 2026-10-11
    # the labelled set, written to review_runs/<run id>/ and never sent anywhere
    python scripts/review_week.py --golden data/golden/filters.jsonl --split dev
    # build the prompts and stop, without calling Claude
    python scripts/review_week.py --golden data/golden/filters.jsonl --split dev --dry-run

Each run writes review_runs/<run id>/: run.json (settings, counts, token use per batch), decisions.jsonl,
invalid.jsonl and answers.jsonl (every raw answer).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.extract.schema import RoleFamily, Seniority  # noqa: E402
from radar.review import TEXT_LIMIT, check_value  # noqa: E402

# what Claude answers; english_only is derived from dutch_required (a job needing no Dutch is "English, no Dutch")
FIELDS = ("is_tech", "role_family", "seniority", "years_experience", "dutch_required", "english_required",
          "visa_sponsorship")
MODEL_IDS = {"opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
EXAMPLES_PER_BATCH = 4
EXAMPLE_CHARS = 700
# the site refuses plain HTTP libraries by their user agent (radar/throttle.py): say what this is instead
USER_AGENT = "TechJobsRadar-review/1.0 (admin script)"

INSTRUCTIONS = """\
You read job postings from the Netherlands (Dutch or English) for a job board and decide a few facts about each,
the way a careful job seeker would. Decide only from the posting's title, company and text. Never guess beyond the
text: when the text does not say, give the "not stated" value. You have no tools; answer only with the JSON object
described at the end.

For every field give:
- value: as defined below;
- confidence: the probability (0 to 1) that a careful reader following these definitions would choose the same
  value. Use 0.9 or more only when the text states it plainly; use less than 0.6 when the text is ambiguous or two
  careful readers could disagree;
- reason: the words of the posting that decided it (a short quote, at most 25 words), or why it is not stated.

Fields

is_tech (true/false): the job needs a computer science or IT background.
- Tech: software, web, mobile, data, analytics and BI, data science, ML and AI, cloud, DevOps and SRE,
  infrastructure, networking, databases, security and cyber, QA and testing, embedded and firmware, IT support and
  IT management (systeembeheer, applicatiebeheer, functioneel beheer, servicedesk and helpdesk, werkplekbeheer),
  ERP, SAP and CRM consultants, technical product roles (product manager or owner of digital or software products,
  technical program manager, scrum master, agile coach on software teams), UX and UI designers of digital
  products, solutions, sales and pre-sales engineers and consultants who need a technical IT background, IT
  auditors, IT business analysts, simulation and computational engineering (writing models or software), robotics,
  PLC and OT engineering with programming; any role whose posting asks for a degree or background in computer
  science, informatics, IT, software engineering, data science, AI, computer engineering or similar, whatever the
  title; internships, traineeships and graduate programmes in any of these.
- Not tech: roles that need no CS or IT background, even at tech companies: sales and account roles without a
  technical requirement, marketing, communication, copywriting, HR and recruiting, finance and accounting, legal,
  operations, customer service, logistics, facilities, general management, art and graphic design, healthcare,
  teaching; mechanical, electrical, civil or process engineering without software work; physical security.

role_family: one of backend, frontend, fullstack, mobile, data, ml, platform, security, qa, embedded, simulation,
it_support, product, design, solutions, other.
- backend: general software development (including "Software Engineer", Java, .NET or Python developer, low-code
  Mendix, OutSystems or Power Platform developer); frontend; fullstack; mobile (iOS, Android);
- data: data engineering, analytics, BI, data analyst, business or IT analyst work centred on data, GIS;
- ml: data science, ML, AI engineering;
- platform: DevOps, SRE, cloud, infrastructure, networking, database administration, middleware;
- security: security, cyber, IAM, IT audit and risk; qa: testing and test automation;
- embedded: embedded, firmware, hardware, FPGA, PLC and OT, robotics, IoT;
- simulation: simulation, CAE, computational modelling;
- it_support: helpdesk, servicedesk, werkplek, systeembeheer, applicatiebeheer, functioneel beheer, M365 and
  Modern Workplace;
- product: product owner or manager, scrum master, agile coach, project or program manager of IT;
- design: UX, UI and product design;
- solutions: solutions, sales and pre-sales engineers, solutions consultants, value engineers, field application
  engineers (the technical side of selling a product);
- other: everything else (including ERP and SAP consultants, IT business analysts not about data, and every job
  that is not tech).

seniority: one of intern, trainee, junior, medior, senior, lead, staff, manager, unknown.
1. If the title states a level, use it: intern, stage, stagiair, afstudeer, werkstudent, thesis = intern; trainee,
   traineeship, graduate programme, starter programme = trainee; junior, starter, graduate, entry level, associate
   (alone) = junior; medior, mid-level, "Engineer II" = medior; senior, sr, ervaren (5+ years asked) = senior; lead,
   tech lead, team lead, architect = lead; staff, principal, distinguished = staff; manager of people, head of,
   director, hoofd, CTO = manager (a "Product Manager", "Project Manager" or "Account Manager" is not a people
   manager: take the level from other words, else unknown). Two levels ("Junior/Medior", "Medior/Senior"): the
   lower one.
2. If the title has no level, use the minimum years of experience asked: 0-2 junior, 3-4 medior, 5+ senior.
3. Else the opening lines of the text, if they name the level of this job ("we zoeken een junior..."); else unknown.

years_experience (whole number or null): the minimum years of work experience asked. Ranges take the lower bound
("3-5 jaar" = 3, "tussen de 2 en 4" = 2). "5+" = 5. Written numbers count ("drie jaar" = 3). Half years round up
("1,5 jaar" = 2). Null when not stated, vague ("enkele jaren", "a few years", "ruime ervaring"), or the number is
not about experience (contract length, company age, "within 2 years you will..."); "0-2 years" or "up to 2 years"
for entry-level roles = null.

dutch_required (true/false/null): does the job require speaking Dutch?
- true: a posting written in Dutch, unless it says Dutch is not needed; an English posting that requires Dutch
  ("fluent Dutch", "Dutch is a must", "you speak Dutch and English", "NL/EN", "Nederlands op C1-niveau").
- false: Dutch is optional ("a plus", "nice to have", "een pre", "preferred", "or willing to learn"), "Dutch or
  English" (either language), an English posting that does not ask for Dutch, or "no Dutch needed".
- null: only when the language the job needs cannot be told at all (for example an empty text).

english_required (true/false/null): does the job need English?
- true: the posting is written in English, or it asks for English ("goede beheersing van het Engels", "fluent
  English").
- false: a Dutch posting that does not ask for English (Dutch is enough).
- null: only when it cannot be told at all.

visa_sponsorship (true/false/null):
- true: the employer offers visa or work-permit sponsorship, is a recognised sponsor (erkend referent,
  kennismigrant or highly skilled migrant support), offers relocation support or a relocation package, or the 30%
  ruling; unless the same posting says a work permit is required.
- false: it says it cannot sponsor, no visa sponsorship, no relocation, or requires an existing right to work, a
  valid work permit or EU citizenship.
- null: not mentioned.

Answer

Answer every posting under "Postings to read", none of the examples, as one JSON object:
{"postings": [{"id": <posting id>, "is_tech": {"value": ..., "confidence": ..., "reason": "..."},
  "role_family": {...}, "seniority": {...}, "years_experience": {...}, "dutch_required": {...},
  "english_required": {...}, "visa_sponsorship": {...}}, ...]}
"""


def _field(value: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["value", "confidence", "reason"],
            "properties": {"value": value, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                           "reason": {"type": "string", "maxLength": 300}}}


_NULLABLE_BOOL = {"type": ["boolean", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["postings"],
    "properties": {"postings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", *FIELDS],
        "properties": {
            "id": {"type": "integer"},
            "is_tech": _field({"type": "boolean"}),
            "role_family": _field({"type": "string", "enum": list(RoleFamily.__args__)}),
            "seniority": _field({"type": "string", "enum": list(Seniority.__args__)}),
            "years_experience": _field({"type": ["integer", "null"], "minimum": 0, "maximum": 40}),
            "dutch_required": _field(_NULLABLE_BOOL),
            "english_required": _field(_NULLABLE_BOOL),
            "visa_sponsorship": _field(_NULLABLE_BOOL),
        }}}},
}


# picking examples

_WORD = re.compile(r"[a-zà-ÿ0-9+#.]{3,}")
_KEY_SENTENCE = re.compile(r"ervaring|experien|\byears?\b|\bjaar\b|dutch|nederlands|english|engels|visa|sponsor|"
                           r"relocat|kennismigrant|30 ?%", re.I)


def _words(title: str) -> set[str]:
    return set(_WORD.findall((title or "").lower()))


def excerpt(text: str, limit: int = EXAMPLE_CHARS) -> str:
    """The opening line and the sentences that usually decide the fields (experience, language, visa)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]
    if not sentences:
        return ""
    picked = [sentences[0]] + [s for s in sentences[1:] if _KEY_SENTENCE.search(s)]
    out = ""
    for s in picked:
        if len(out) + len(s) + 1 > limit:
            break
        out += (" " if out else "") + s
    return out or sentences[0][:limit]


def choose_examples(batch: list[dict], pool: list[dict], k: int = EXAMPLES_PER_BATCH) -> list[dict]:
    """The k labelled postings whose titles share most words with a posting in the batch (never one of the batch's
    own postings)."""
    ids = {p["id"] for p in batch}
    titles = [_words(p["title"]) for p in batch]

    def score(item: dict) -> float:
        w = _words(item["title"])
        return max((len(w & t) / len(w | t) for t in titles if w | t), default=0.0)

    ranked = sorted((it for it in pool if it["id"] not in ids), key=lambda it: (-score(it), it["id"]))
    return ranked[:k]


def _labels(item: dict) -> dict:
    lab = item["labels"]
    out = {"is_tech": lab["is_tech"], "role_family": lab["role_family"], "seniority": lab["seniority"],
           "years_experience": lab.get("years_experience"), "dutch_required": lab.get("dutch_required"),
           "visa_sponsorship": lab.get("visa_sponsorship")}
    # the test set does not label english_required: a posting in English needs it; otherwise leave it out
    if lab.get("english_required") is not None:
        out["english_required"] = lab["english_required"]
    elif lab.get("posting_language") == "en":
        out["english_required"] = True
    return out


def trim(text: str, limit: int = TEXT_LIMIT) -> str:
    if len(text or "") <= limit:
        return text or ""
    cut = text.rfind(" ", int(limit * 0.9), limit)
    return text[:cut if cut > 0 else limit].rstrip() + " [...]"


def build_prompt(batch: list[dict], examples: list[dict]) -> str:
    parts = ["Examples labelled by a careful reader (do not answer these):\n"]
    for ex in examples:
        parts.append(f"Example title: {ex['title']}\nCompany: {ex['company']}\nExcerpt: {excerpt(ex['description'])}\n"
                     f"Labels: {json.dumps(_labels(ex), ensure_ascii=False)}\n")
    parts.append(f"\nPostings to read ({len(batch)}; answer every one):\n")
    for p in batch:
        parts.append(f"\n### Posting id={p['id']}\nTitle: {p['title']}\nCompany: {p['company']}\nText:\n"
                     f"{trim(p.get('text') or p.get('description') or '')}\n")
    return "".join(parts)


# running Claude Code

def find_claude(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    from shutil import which

    found = which("claude")
    if found:
        return found
    # the copy the Claude desktop app keeps, newest version first
    local = os.environ.get("LOCALAPPDATA", "")
    copies = sorted(glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe")),
                    key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"[.\\/]", p)], reverse=True)
    if copies:
        return copies[0]
    sys.exit("claude not found: install Claude Code or pass --claude <path>")


def child_env() -> dict[str, str]:
    """The environment for `claude -p`: without API keys or a parent session's settings, so it runs on the
    plan login (~/.claude/.credentials.json) as it would from a plain terminal."""
    drop = ("ANTHROPIC_", "CLAUDE_", "CLAUDECODE", "OPENAI_API_KEY")
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}


def claude_command(claude: str, model: str, effort: str | None, instructions_file: str) -> list[str]:
    # the instructions go in a file: a command line through claude.cmd (an npm install) stops at 8,191 characters
    cmd = [claude, "-p", "--model", model, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--system-prompt-file", instructions_file, "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--no-session-persistence"]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def parse_output(stdout: str) -> tuple[Any, dict]:
    """(the answer object or None, metadata) from `claude -p --output-format json`. The answer is the structured
    output when the CLI gives one, else the result text read as JSON (code fences allowed)."""
    try:
        out = json.loads(stdout)
    except json.JSONDecodeError:
        return None, {"error": "the CLI output is not JSON", "raw": stdout[-2000:]}
    meta = {k: out.get(k) for k in ("is_error", "subtype", "duration_ms", "num_turns", "usage", "modelUsage",
                                    "total_cost_usd") if k in out}
    if out.get("is_error"):
        return None, {**meta, "error": str(out.get("result"))[:2000]}
    answer = out.get("structured_output")
    if answer is None:
        text = (out.get("result") or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            return None, {**meta, "error": "the answer is not JSON", "raw": text[-2000:]}
    return answer, meta


def run_claude(prompt: str, model: str, claude: str, effort: str | None, timeout: int = 900) -> tuple[Any, dict]:
    with tempfile.TemporaryDirectory(prefix="review-") as empty:  # no project files or CLAUDE.md to pick up
        instructions = Path(empty) / "instructions.txt"
        instructions.write_text(INSTRUCTIONS, encoding="utf-8")
        try:
            proc = subprocess.run(claude_command(claude, model, effort, str(instructions)), input=prompt,
                                  capture_output=True, text=True, encoding="utf-8", cwd=empty, env=child_env(),
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, {"error": f"no answer within {timeout} s"}
    if proc.returncode != 0 and not proc.stdout.strip():
        return None, {"error": f"claude exited with {proc.returncode}: {proc.stderr.strip()[-1000:]}"}
    return parse_output(proc.stdout)


def model_id(meta: dict, alias: str) -> str:
    used = list((meta.get("modelUsage") or {}).keys())
    return max(used, key=lambda m: (meta["modelUsage"][m] or {}).get("outputTokens", 0)) if used else \
        MODEL_IDS.get(alias, alias)


# checking answers

def validate(answer: Any, batch: list[dict], model: str) -> tuple[list[dict], list[dict]]:
    """(valid decisions, invalid items). Each field of each posting stands on its own: one bad field does not drop
    the rest of that posting's answer. english_only is added from dutch_required."""
    want = {p["id"] for p in batch}
    invalid: list[dict] = []
    if not isinstance(answer, dict) or not isinstance(answer.get("postings"), list):
        return [], [{"posting_id": None, "field": None, "error": "the answer has no postings list"}]
    decisions: list[dict] = []
    seen: set[int] = set()
    for item in answer["postings"]:
        pid = item.get("id") if isinstance(item, dict) else None
        if pid not in want:
            invalid.append({"posting_id": pid, "field": None, "error": "not a posting of this batch"})
            continue
        if pid in seen:
            invalid.append({"posting_id": pid, "field": None, "error": "answered twice"})
            continue
        seen.add(pid)
        for field in FIELDS:
            f = item.get(field)
            error = None
            if not isinstance(f, dict) or not {"value", "confidence", "reason"} <= set(f):
                error = "missing value, confidence or reason"
            else:
                error = check_value(field, f["value"])
                c = f["confidence"]
                if error is None and not (isinstance(c, int | float) and not isinstance(c, bool) and 0 <= c <= 1):
                    error = "confidence must be between 0 and 1"
                if error is None and not isinstance(f["reason"], str):
                    error = "reason must be text"
            if error:
                invalid.append({"posting_id": pid, "field": field, "error": error, "answer": f})
                continue
            d = {"posting_id": pid, "field": field, "value": f["value"], "confidence": float(f["confidence"]),
                 "reason": f["reason"][:300], "model": model}
            decisions.append(d)
            if field == "dutch_required":
                decisions.append({**d, "field": "english_only",
                                  "value": None if f["value"] is None else not f["value"]})
    for pid in sorted(want - seen):
        invalid.append({"posting_id": pid, "field": None, "error": "no answer"})
    return decisions, invalid


# the server

def fetch_candidates(server: str, token: str, since: str | None, limit: int) -> list[dict]:
    import httpx

    items: list[dict] = []
    after = 0
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    with httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=60) as c:
        while len(items) < limit:
            params = {"limit": min(500, limit - len(items)), "after_id": after}
            if since:
                params["since"] = since
            r = c.get("/api/admin/review/candidates", params=params)
            r.raise_for_status()
            page = r.json()
            items += page["items"]
            if page["next_after_id"] is None:
                break
            after = page["next_after_id"]
    return items


def post_decisions(server: str, token: str, run_id: str, decisions: list[dict]) -> dict:
    import httpx

    for attempt in range(3):
        try:
            r = httpx.post(f"{server.rstrip('/')}/api/admin/review/decisions", timeout=60,
                           headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
                           json={"run_id": run_id, "decisions": decisions})
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return {}


# the run

def load_golden(path: str, split: str | None) -> list[dict]:
    items = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [it for it in items if split in (None, "all") or it.get("split") == split]


def batches(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _append(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--server", help="the site to fetch candidates from and send decisions to")
    src.add_argument("--golden", help="read postings from a labelled set instead (nothing is sent)")
    ap.add_argument("--split", default="dev", help="with --golden: dev, test or all")
    ap.add_argument("--ids", help="with --golden: only these posting ids (comma-separated)")
    ap.add_argument("--since", default=(date.today() - timedelta(days=7)).isoformat(),
                    help="with --server: postings first seen on or after this date (default: a week ago)")
    ap.add_argument("--limit", type=int, default=1000, help="postings at most")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--max-batches", type=int, help="stop after this many batches")
    ap.add_argument("--model", default="opus", help="opus (weekly review, labelled-set runs) or haiku (backfill)")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), help="default: the CLI's")
    ap.add_argument("--examples", default="data/golden/filters.jsonl", help="labelled set to take examples from")
    ap.add_argument("--claude", help="path to the claude executable")
    ap.add_argument("--out", default="review_runs", help="folder for the run's files")
    ap.add_argument("--no-post", action="store_true", help="with --server: write decisions down, do not send them")
    ap.add_argument("--dry-run", action="store_true", help="build the prompts and stop; Claude is not called")
    args = ap.parse_args(argv)

    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.model}"
    token = None
    if args.server:
        token = os.environ.get("RADAR_ADMIN_TOKEN")
        if not token:
            sys.exit("set RADAR_ADMIN_TOKEN to the site's admin token")
        items = fetch_candidates(args.server, token, args.since, args.limit)
    else:
        items = load_golden(args.golden, args.split)
        if args.ids:
            wanted = {int(i) for i in args.ids.split(",")}
            items = [it for it in items if it["id"] in wanted]
        items = items[:args.limit]
    pool = load_golden(args.examples, "dev")  # examples only ever from the dev split: test stays unseen
    work = batches(items, args.batch_size)[:args.max_batches]
    print(f"{run_id}: {len(items)} postings, {len(work)} batches of up to {args.batch_size}, model {args.model}")

    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"run_id": run_id, "model": args.model, "effort": args.effort,
                               "source": args.server or f"{args.golden}:{args.split}", "postings": len(items),
                               "batch_size": args.batch_size, "started": datetime.now().isoformat(timespec="seconds"),
                               "batches": [], "decisions": 0, "invalid": 0, "sent": 0}
    claude = None if args.dry_run else find_claude(args.claude)
    for n, batch in enumerate(work, 1):
        prompt = build_prompt(batch, choose_examples(batch, pool))
        if args.dry_run:
            (run_dir / f"prompt_{n:03d}.txt").write_text(INSTRUCTIONS + "\n\n" + prompt, encoding="utf-8")
            summary["batches"].append({"n": n, "postings": len(batch), "prompt_chars": len(INSTRUCTIONS) + len(prompt)})
            continue
        t0 = time.monotonic()
        answer, meta = run_claude(prompt, args.model, claude, args.effort)
        if answer is None and "error" in meta:  # one retry for a failed call or unreadable answer
            print(f"  batch {n}: {meta['error'][:200]}; retrying once")
            answer, meta = run_claude(prompt, args.model, claude, args.effort)
        used = model_id(meta, args.model)
        decisions, invalid = validate(answer, batch, used) if answer is not None else (
            [], [{"posting_id": p["id"], "field": None, "error": meta.get("error", "no answer")} for p in batch])
        _append(run_dir / "answers.jsonl", [{"batch": n, "ids": [p["id"] for p in batch], "meta": meta,
                                             "answer": answer}])
        _append(run_dir / "decisions.jsonl", [{**d, "run_id": run_id} for d in decisions])
        _append(run_dir / "invalid.jsonl", [{**i, "batch": n, "run_id": run_id} for i in invalid])
        sent = None
        if args.server and not args.no_post and decisions:
            sent = post_decisions(args.server, token, run_id, decisions)
            summary["sent"] += sent.get("stored", 0)
            if sent.get("invalid"):
                _append(run_dir / "invalid.jsonl", [{**i, "batch": n, "run_id": run_id, "by": "server"}
                                                    for i in sent["invalid"]])
        summary["decisions"] += len(decisions)
        summary["invalid"] += len(invalid)
        summary["batches"].append({"n": n, "postings": len(batch), "decisions": len(decisions),
                                   "invalid": len(invalid), "seconds": round(time.monotonic() - t0, 1),
                                   "model": used, "usage": meta.get("usage"), "error": meta.get("error")})
        print(f"  batch {n}/{len(work)}: {len(decisions)} decisions, {len(invalid)} invalid, "
              f"{summary['batches'][-1]['seconds']} s" + (f", sent {sent.get('stored', 0)}" if sent else ""))
        (run_dir / "run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    (run_dir / "run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"done: {summary['decisions']} decisions, {summary['invalid']} invalid; files in {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
