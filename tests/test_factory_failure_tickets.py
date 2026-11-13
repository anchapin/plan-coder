"""Tests for bin/factory_failure_tickets.py (issue #27, "Fix-lite").

No network, no real `gh`: the GitHub boundary is mocked two ways —
in-process (a fake `gh` callable injected into capture_failure) and
end-to-end (a fake `gh` executable on PATH in a temp dir).

Run: python3 -m pytest tests/test_factory_failure_tickets.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"

_spec = importlib.util.spec_from_file_location(
    "factory_failure_tickets", BIN / "factory_failure_tickets.py")
fft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fft)


FAKE_GH = '''#!/usr/bin/env python3
"""A fake `gh` CLI: no network, deterministic, state in a JSON file."""
import json
import os
import sys

STATE = os.environ["FAKE_GH_STATE"]
LOG = os.environ["FAKE_GH_LOG"]


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"issues": [], "comments": [], "created": 0, "next": 1}


def save(state):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def flag(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(argv):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\\n")
    state = load()
    args = list(argv)
    if args[:2] == ["issue", "list"]:
        search = flag(args, "--search", "") or ""
        want = ""
        if "factory-failure-fingerprint:" in search:
            want = search.split("factory-failure-fingerprint:")[1]
            want = want.split("in:body")[0].strip()
        label = flag(args, "--label", "") or ""
        out = []
        for issue in state["issues"]:
            if issue.get("state", "open") != "open":
                continue
            if label and label not in issue.get("labels", []):
                continue
            if want and ("factory-failure-fingerprint: " + want) \\
                    not in issue.get("body", ""):
                continue
            out.append({"number": issue["number"], "title": issue["title"],
                        "url": issue["url"], "body": issue["body"]})
        print(json.dumps(out))
        return 0
    if args[:2] == ["issue", "create"]:
        number = state.get("next", 1)
        state["next"] = number + 1
        repo = flag(args, "--repo", "you/repo")
        issue = {
            "number": number,
            "title": flag(args, "--title", ""),
            "body": flag(args, "--body", ""),
            "labels": [flag(args, "--label", "")],
            "state": "open",
            "url": "https://github.com/%s/issues/%d" % (repo, number),
        }
        state["issues"].append(issue)
        state["created"] = state.get("created", 0) + 1
        save(state)
        print(issue["url"])
        return 0
    if args[:2] == ["issue", "comment"]:
        number = flag(args, "--body", None)
        body = flag(args, "--body", "")
        state["comments"].append(body)
        save(state)
        print("commented")
        return 0
    print("fake gh: unsupported command %r" % (args,), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


class FakeGh:
    """In-process stand-in for the `gh` boundary."""

    def __init__(self, issues=None, fail_on=None):
        self.issues = list(issues or [])
        self.calls = []
        self.comments = []
        self.created = []
        self.fail_on = fail_on

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail_on and self.fail_on in args:
            raise fft.GhError("fake gh failure")
        if args[:2] == ["issue", "list"]:
            search = ""
            if "--search" in args:
                search = args[args.index("--search") + 1]
            want = ""
            if fft.FINGERPRINT_KEY in search:
                want = search.split(fft.FINGERPRINT_KEY + ":")[1]
                want = want.split("in:body")[0].strip()
            hits = []
            for issue in self.issues:
                if want and (fft.FINGERPRINT_LINE + want) not in issue["body"]:
                    continue
                hits.append(issue)
            return json.dumps(hits)
        if args[:2] == ["issue", "create"]:
            number = len(self.created) + 100
            body = args[args.index("--body") + 1]
            title = args[args.index("--title") + 1]
            label = args[args.index("--label") + 1]
            issue = {"number": number, "title": title, "body": body,
                     "labels": [label],
                     "url": "https://github.com/you/alpha/issues/%d" % number}
            self.created.append(issue)
            self.issues.append(issue)
            return issue["url"] + "\n"
        if args[:2] == ["issue", "comment"]:
            body = args[args.index("--body") + 1]
            self.comments.append(body)
            return "commented\n"
        raise fft.GhError("unsupported fake gh command: %r" % (args,))


def record(cron="backlog-supervisor", run_id="run-1",
           timestamp="2026-11-13T04:21:07Z", error_class="timeout",
           error_text="worker timed out after 30s", repo="you/alpha",
           last_good_run="run-0"):
    return {"repo": repo, "cron": cron, "run_id": run_id,
            "timestamp": timestamp, "error_class": error_class,
            "error_text": error_text, "last_good_run": last_good_run}


class TestFingerprint(unittest.TestCase):
    def test_same_failure_different_volatile_parts_same_fingerprint(self):
        a = fft.fingerprint("pr-shepherd", "timeout",
                            "run-42 timed out after 30s at /tmp/a.log",
                            "run-42")
        b = fft.fingerprint("pr-shepherd", "timeout",
                            "run-99 timed out after 12s at /var/b.log",
                            "run-99")
        self.assertEqual(a, b)

    def test_timestamps_and_ids_normalized_out(self):
        text = ("2026-11-13T04:21:07Z run-7 failed after 42 retries "
                "in /home/x/worktrees/alpha-worker-3")
        norm = fft.normalize(text, "run-7")
        self.assertNotIn("2026-11-13", norm)
        self.assertNotIn("42", norm)
        self.assertNotIn("run-7", norm)
        self.assertIn("<ts>", norm)
        self.assertIn("<n>", norm)
        self.assertIn("<path>", norm)

    def test_different_class_or_cron_different_fingerprint(self):
        base = fft.fingerprint("pr-shepherd", "timeout", "boom")
        self.assertNotEqual(base, fft.fingerprint("pr-shepherd", "crash",
                                                 "boom"))
        self.assertNotEqual(base, fft.fingerprint("harness-tune", "timeout",
                                                 "boom"))

    def test_fingerprint_is_stable_hex(self):
        fp = fft.fingerprint("harness-tune", "timeout", "boom")
        self.assertEqual(len(fp), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))
        self.assertEqual(fp, fft.fingerprint("harness-tune", "timeout",
                                             "boom"))


class TestCaptureOneIssuePerFailure(unittest.TestCase):
    def test_each_cron_files_exactly_one_issue(self):
        for cron in fft.CRON_IDS:
            with self.subTest(cron=cron):
                gh = FakeGh()
                result = fft.capture_failure(record(cron=cron), gh=gh)
                self.assertEqual(result["action"], "filed")
                self.assertEqual(len(gh.created), 1)
                issue = gh.created[0]
                self.assertTrue(issue["title"].startswith(fft.TITLE_PREFIX))
                self.assertIn(fft.LABEL, issue["labels"])

    def test_issue_body_carries_the_required_fields(self):
        gh = FakeGh()
        rec = record(cron="pr-shepherd", run_id="run-77",
                     timestamp="2026-11-13T04:21:07Z",
                     error_text="shepherd loop crashed",
                     last_good_run="run-70")
        result = fft.capture_failure(rec, gh=gh)
        body = gh.created[0]["body"]
        self.assertIn(fft.FINGERPRINT_LINE + result["fingerprint"], body)
        self.assertIn("pr-shepherd", body)
        self.assertIn("run-77", body)
        self.assertIn("2026-11-13T04:21:07Z", body)
        self.assertIn("shepherd loop crashed", body)
        self.assertIn("run-70", body)

    def test_excerpt_is_bounded_to_30_lines(self):
        gh = FakeGh()
        rec = record(error_text="\n".join("line %d" % i for i in range(1, 101)))
        fft.capture_failure(rec, gh=gh)
        body = gh.created[0]["body"]
        excerpt = body.split("```")[1].strip().splitlines()
        self.assertLessEqual(len(excerpt), 30)
        self.assertEqual(excerpt[0], "line 1")
        self.assertNotIn("line 40", body)

    def test_repeat_failure_comments_instead_of_second_issue(self):
        gh = FakeGh()
        first = fft.capture_failure(record(run_id="run-1"), gh=gh)
        second = fft.capture_failure(record(run_id="run-2",
                                            timestamp="2026-11-13T05:00:00Z"),
                                     gh=gh)
        self.assertEqual(first["action"], "filed")
        self.assertEqual(second["action"], "commented")
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(len(gh.comments), 1)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertIn("run-2", gh.comments[0])
        self.assertIn("2026-11-13T05:00:00Z", gh.comments[0])

    def test_different_failure_files_a_second_issue(self):
        gh = FakeGh()
        fft.capture_failure(record(error_class="timeout"), gh=gh)
        fft.capture_failure(record(error_class="crash"), gh=gh)
        self.assertEqual(len(gh.created), 2)


class TestCaptureStepFailureFilesNothing(unittest.TestCase):
    def test_missing_field_is_a_capture_failure(self):
        gh = FakeGh()
        bad = record()
        del bad["error_class"]
        with self.assertRaises(fft.CaptureError):
            fft.capture_failure(bad, gh=gh)
        self.assertEqual(gh.calls, [])
        self.assertEqual(gh.created, [])

    def test_capture_step_never_files_about_itself(self):
        gh = FakeGh()
        with self.assertRaises(fft.CaptureError):
            fft.capture_failure(record(cron=fft.SELF_CRON_ID), gh=gh)
        self.assertEqual(gh.calls, [])
        self.assertEqual(gh.created, [])

    def test_gh_failure_is_fail_silent(self):
        gh = FakeGh(fail_on="issue")
        with self.assertRaises(fft.GhError):
            fft.capture_failure(record(), gh=gh)
        self.assertEqual(gh.created, [])


class FakeGhCli:
    """End-to-end harness: a fake `gh` executable on PATH."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pc-fft-"))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state = self.dir / "state.json"
        self.log = self.dir / "calls.jsonl"
        self.env = dict(os.environ)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.env["FAKE_GH_STATE"] = str(self.state)
        self.env["FAKE_GH_LOG"] = str(self.log)

    def run(self, args, stdin=None):
        return subprocess.run([sys.executable, str(BIN /
                                                  "factory_failure_tickets.py")]
                              + list(args), input=stdin, env=self.env,
                              capture_output=True, text=True, check=False)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text().splitlines() if line.strip()]

    def issues(self):
        if not self.state.exists():
            return []
        return json.loads(self.state.read_text())["issues"]

    def comments(self):
        if not self.state.exists():
            return []
        return json.loads(self.state.read_text())["comments"]


class TestEndToEnd(unittest.TestCase):
    def test_one_issue_per_cron_then_recurrence_comment(self):
        cli = FakeGhCli()
        for cron in fft.CRON_IDS:
            with self.subTest(cron=cron):
                payload = json.dumps(record(cron=cron))
                r = cli.run([], stdin=payload)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                r2 = cli.run([], stdin=json.dumps(record(
                    cron=cron, run_id="run-2",
                    timestamp="2026-11-13T06:00:00Z")))
                self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
                self.assertIn("RECURRENCE", r2.stdout)
                self.assertIn("FILED", r.stdout)
        issues = cli.issues()
        self.assertEqual(len(issues), 3)
        for issue in issues:
            self.assertTrue(issue["title"].startswith(fft.TITLE_PREFIX))
            self.assertIn(fft.LABEL, issue["labels"])
            self.assertIn(fft.FINGERPRINT_KEY, issue["body"])
        self.assertEqual(len(cli.comments()), 3)

    def test_capture_failure_exits_nonzero_and_files_nothing(self):
        cli = FakeGhCli()
        handoff = cli.dir / "handoff.json"
        handoff.write_text("{not json")
        before = handoff.read_bytes()
        r = cli.run(["--input", str(handoff)])
        self.assertEqual(r.returncode, 2)
        self.assertIn("capture failed", r.stderr)
        self.assertEqual(cli.calls(), [])
        self.assertEqual(cli.issues(), [])
        self.assertEqual(handoff.read_bytes(), before)

    def test_gh_unreachable_exits_3_and_files_nothing(self):
        cli = FakeGhCli()
        cli.env["PATH"] = str(cli.bin / "empty")
        (cli.bin / "empty").mkdir(exist_ok=True)
        r = cli.run([], stdin=json.dumps(record()))
        self.assertEqual(r.returncode, 3)
        self.assertIn("no ticket filed", r.stderr)
        self.assertEqual(cli.issues(), [])

    def test_json_output_carries_fingerprint_and_number(self):
        cli = FakeGhCli()
        r = cli.run(["--json"], stdin=json.dumps(record()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["action"], "filed")
        self.assertEqual(len(out["fingerprint"]), 16)
        self.assertEqual(out["issue_number"], 1)


class TestSupervisorPromptRule(unittest.TestCase):
    """The auto-filed exclusion must exist in the picking rules and be
    tied to the script's constants (single source of truth)."""

    def setUp(self):
        self.prompt = (REPO / "prompts" / "backlog-supervisor.md").read_text(
            encoding="utf-8")

    def test_prompt_mentions_the_constants(self):
        self.assertIn(fft.TITLE_PREFIX, self.prompt)
        self.assertIn(fft.LABEL, self.prompt)

    def test_prompt_skips_auto_filed_issues_until_human_triage(self):
        hits = [line for line in self.prompt.splitlines()
                if fft.TITLE_PREFIX in line and fft.LABEL in line]
        self.assertTrue(hits, "no picking rule mentions the auto-filed marker")
        rule = hits[0]
        self.assertIn("Skip", rule)
        self.assertIn("human", rule)

    def test_rule_lives_in_the_picking_section(self):
        picking = self.prompt.split("3. Pick well-specified issues", 1)[1]
        picking = picking.split("\n4.", 1)[0]
        self.assertIn(fft.TITLE_PREFIX, picking)
        self.assertIn(fft.LABEL, picking)


if __name__ == "__main__":
    unittest.main()
