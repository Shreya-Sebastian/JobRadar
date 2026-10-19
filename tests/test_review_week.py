"""scripts/review_week.py without calling Claude: prompts, examples, answer checking, the CLI's output and a whole
run against a stand-in for `claude`."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def rw():
    spec = importlib.util.spec_from_file_location("review_week", ROOT / "scripts" / "review_week.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _posting(i, title="Backend Engineer", text="Python and Go. Minimaal 3 jaar ervaring."):
    return {"id": i, "title": title, "company": "Acme", "text": text}


def _item(i, title, split="dev", **labels):
    base = {"is_tech": True, "role_family": "backend", "seniority": "unknown", "years_experience": None,
            "dutch_required": True, "visa_sponsorship": None, "posting_language": "nl"}
    return {"id": i, "title": title, "company": "Acme", "split": split, "description": "Wij zoeken. 3 jaar ervaring.",
            "labels": {**base, **labels}}


def _field(value, confidence=0.9, reason="stated"):
    return {"value": value, "confidence": confidence, "reason": reason}


def _answer(pid, **over):
    a = {"id": pid, "is_tech": _field(True), "role_family": _field("backend"), "seniority": _field("medior"),
         "years_experience": _field(3), "dutch_required": _field(True), "english_required": _field(False),
         "visa_sponsorship": _field(None)}
    a.update(over)
    return a


def test_batches_of_twenty(rw):
    sizes = [len(b) for b in rw.batches(list(range(45)), 20)]
    assert sizes == [20, 20, 5]


def test_the_instructions_are_fixed_and_the_postings_follow(rw):
    long = {"id": 2, "title": "Backend Engineer", "company": "Acme", "description": "x " * 5000}  # a labelled one
    batch = [_posting(1), long]
    prompt = rw.build_prompt(batch, [_item(9, "Java Developer")])
    assert "### Posting id=1" in prompt and "### Posting id=2" in prompt
    assert prompt.index("Example title: Java Developer") < prompt.index("### Posting id=1")
    assert "[...]" in prompt and len(prompt) < 7500  # the long text trimmed to about 6,000 characters
    cmd = rw.claude_command("claude", "opus", None, "instructions.txt")
    assert cmd[cmd.index("--system-prompt-file") + 1] == "instructions.txt"
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd and "--bare" not in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == rw.SCHEMA
    assert len(" ".join(cmd)) < 6000  # fits a claude.cmd command line (8,191 characters)
    assert rw.claude_command("claude", "opus", "medium", "i.txt")[-2:] == ["--effort", "medium"]


def test_examples_are_the_closest_titles_and_never_the_batch_itself(rw):
    pool = [_item(1, "Senior Data Engineer"), _item(2, "Office Manager"), _item(3, "Data Engineer Azure"),
            _item(4, "Data Engineer")]
    got = [e["id"] for e in rw.choose_examples([_posting(4, "Data Engineer")], pool, k=2)]
    assert got == [1, 3]  # 4 is the posting being read


def test_example_labels_leave_out_what_the_set_does_not_label(rw):
    assert "english_required" not in rw._labels(_item(1, "Ontwikkelaar"))
    assert rw._labels(_item(2, "Developer", posting_language="en", dutch_required=False))["english_required"] is True


def test_excerpt_keeps_the_opening_and_the_deciding_sentences(rw):
    text = "We build payments. Our office has plants. You have 5 years of experience. Dutch is a plus."
    assert rw.excerpt(text) == "We build payments. You have 5 years of experience. Dutch is a plus."


def test_valid_answers_become_decisions_with_english_only_added(rw):
    batch = [_posting(1), _posting(2)]
    decisions, invalid = rw.validate({"postings": [_answer(1), _answer(2, dutch_required=_field(False, 0.7))]},
                                     batch, "claude-opus-5-5")
    assert invalid == []
    by = {(d["posting_id"], d["field"]): d for d in decisions}
    assert len(decisions) == 2 * 8
    assert by[(1, "english_only")]["value"] is False and by[(2, "english_only")]["value"] is True
    assert by[(2, "english_only")]["confidence"] == 0.7 and by[(1, "years_experience")]["value"] == 3
    assert by[(1, "is_tech")]["model"] == "claude-opus-5-5"


def test_invalid_answers_are_logged_field_by_field(rw):
    batch = [_posting(1), _posting(2), _posting(3)]
    answer = {"postings": [
        _answer(1, seniority=_field("wizard"), years_experience=_field(3, confidence=2)),
        _answer(1),  # answered twice
        _answer(7),  # not in this batch
        {**_answer(2), "visa_sponsorship": {"value": True}},  # no confidence
    ]}
    decisions, invalid = rw.validate(answer, batch, "m")
    errors = {(i["posting_id"], i["field"]): i["error"] for i in invalid}
    assert errors[(1, "seniority")].startswith("seniority must be")
    assert errors[(1, "years_experience")] == "confidence must be between 0 and 1"
    assert errors[(1, None)] == "answered twice" and errors[(7, None)] == "not a posting of this batch"
    assert errors[(2, "visa_sponsorship")] == "missing value, confidence or reason"
    assert errors[(3, None)] == "no answer"
    fields_1 = {d["field"] for d in decisions if d["posting_id"] == 1}
    assert "seniority" not in fields_1 and "role_family" in fields_1  # the rest of the answer still counts
    assert rw.validate([], batch, "m") == ([], [{"posting_id": None, "field": None,
                                                 "error": "the answer has no postings list"}])


def test_cli_output_is_read_from_the_structured_output_or_the_text(rw):
    usage = {"input_tokens": 10, "output_tokens": 5}
    answer, meta = rw.parse_output(json.dumps({"structured_output": {"postings": []}, "usage": usage,
                                               "modelUsage": {"claude-opus-5-5": {"outputTokens": 5}}}))
    assert answer == {"postings": []} and meta["usage"] == usage
    assert rw.model_id(meta, "opus") == "claude-opus-5-5"
    answer, _ = rw.parse_output(json.dumps({"result": '```json\n{"postings": [1]}\n```'}))
    assert answer == {"postings": [1]}
    assert rw.parse_output(json.dumps({"is_error": True, "result": "limit reached"}))[1]["error"] == "limit reached"
    assert rw.parse_output("not json")[0] is None
    assert rw.model_id({}, "haiku") == "claude-haiku-5-5"


def test_the_child_gets_no_api_keys_or_parent_session(rw, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("RADAR_SOMETHING", "kept")
    env = rw.child_env()
    assert "RADAR_SOMETHING" in env
    assert not any(k.startswith(("ANTHROPIC_", "CLAUDE")) or k == "OPENAI_API_KEY" for k in env)


def test_a_golden_run_against_a_stand_in_claude(rw, tmp_path):
    """The whole loop: batches go to the executable, answers are checked and written down, nothing is sent."""
    golden = tmp_path / "set.jsonl"
    golden.write_text("".join(json.dumps(_item(i, f"Engineer {i}", split="dev" if i < 4 else "test")) + "\n"
                              for i in range(1, 7)), encoding="utf-8")
    fake = tmp_path / "fake_claude.py"
    fake.write_text(
        "import json, re, sys\n"
        "system = open(sys.argv[sys.argv.index('--system-prompt-file') + 1], encoding='utf-8').read()\n"
        "assert system.startswith('You read job postings')\n"
        "ids = [int(i) for i in re.findall(r'### Posting id=(\\d+)', sys.stdin.read())]\n"
        "f = lambda v: {'value': v, 'confidence': 0.9, 'reason': 'r'}\n"
        "posts = [{'id': i, 'is_tech': f(True), 'role_family': f('backend'), 'seniority': f('medior'),\n"
        "          'years_experience': f(3), 'dutch_required': f(True), 'english_required': f(False),\n"
        "          'visa_sponsorship': f(None)} for i in ids[1:]]\n"  # leaves the first posting unanswered
        "print(json.dumps({'structured_output': {'postings': posts}, 'usage': {'output_tokens': 1},\n"
        "                  'modelUsage': {'claude-opus-5-5': {'outputTokens': 1}}}))\n", encoding="utf-8")
    launcher = tmp_path / ("claude.cmd" if sys.platform == "win32" else "claude")
    launcher.write_text(f'@"{sys.executable}" "{fake}" %*\n' if sys.platform == "win32"
                        else f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    out = tmp_path / "runs"
    assert rw.main(["--golden", str(golden), "--split", "test", "--batch-size", "2", "--examples", str(golden),
                    "--claude", str(launcher), "--out", str(out)]) == 0
    run = next(out.iterdir())
    summary = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert summary["postings"] == 3 and [b["postings"] for b in summary["batches"]] == [2, 1]
    decisions = [json.loads(x) for x in (run / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    invalid = [json.loads(x) for x in (run / "invalid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {d["posting_id"] for d in decisions} == {5} and len(decisions) == 8
    assert {(i["posting_id"], i["error"]) for i in invalid} == {(4, "no answer"), (6, "no answer")}
    assert summary["sent"] == 0 and decisions[0]["model"] == "claude-opus-5-5"


def test_a_dry_run_writes_prompts_and_calls_nothing(rw, tmp_path):
    golden = tmp_path / "set.jsonl"
    golden.write_text(json.dumps(_item(1, "Engineer")) + "\n", encoding="utf-8")
    out = tmp_path / "runs"
    assert rw.main(["--golden", str(golden), "--examples", str(golden), "--dry-run", "--out", str(out),
                    "--claude", str(tmp_path / "missing")]) == 0
    run = next(out.iterdir())
    assert (run / "prompt_001.txt").read_text(encoding="utf-8").startswith(rw.INSTRUCTIONS)
    assert not (run / "decisions.jsonl").exists()


def test_the_script_names_itself_so_the_site_does_not_refuse_it(rw):
    from radar.throttle import BLOCKED_AGENTS, check

    assert not BLOCKED_AGENTS.search(rw.USER_AGENT)
    assert check("/api/admin/review/candidates", "203.0.113.9", rw.USER_AGENT) is None


def test_server_texts_are_sent_as_they_come(rw):
    text = "y " * 4000  # already cut and completed by the server (radar/review.py review_text)
    assert "Text:\n" + text + "\n" in rw.build_prompt([_posting(3, text=text)], [])
