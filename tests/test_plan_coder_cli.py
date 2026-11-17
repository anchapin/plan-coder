"""Tests for bin/plan_coder.py — the read-only manager CLI (issue #73).

Phase 0 of #73 adds a manager-side CLI that answers four questions
mechanically -- `status`, `collect --dry-run`, `shepherd-check` and
`next-actions` -- over the harness state dir, plus four versioned JSON
Schemas.  The properties these tests pin down:

  * every subcommand emits valid JSON under `--json`;
  * the output matches the versioned schemas (workers, shepherd,
    ledger-line, next-actions);
  * nothing in the state dir is written, by any subcommand;
  * the live PR facts are injectable (a fixture file stands in for
    `gh`), so the shepherd path is testable offline.

The fixtures are built in a temp dir per test -- the operator's live
state dir is never read and never written.

Run: python3 -m pytest tests/test_plan_coder_cli.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "plan_coder.py"
SCHEMA_DIR = REPO / "schemas"
EXAMPLE_CONFIG = REPO / "config" / "deployment.example.json"


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_cli():
    return load_module(SCRIPT, "plan_coder_cli_under_test")


def run_cli(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


# --- a small JSON-Schema subset validator (stdlib only) ---------------
TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def validate(instance, schema, path="$") -> list:
    """Return a list of human-readable validation errors."""
    errors: list = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        ok = False
        for name in names:
            if name == "integer":
                if isinstance(instance, int) and not isinstance(instance, bool):
                    ok = True
            elif name == "number":
                if isinstance(instance, (int, float)) \
                        and not isinstance(instance, bool):
                    ok = True
            elif name == "boolean":
                if isinstance(instance, bool):
                    ok = True
            else:
                wanted = TYPES.get(name)
                if wanted is not None and isinstance(instance, wanted) \
                        and not (wanted is dict and isinstance(instance, bool)):
                    ok = True
        if not ok:
            errors.append(f"{path}: expected type {expected}, got "
                          f"{type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got "
                      f"{instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties") or {}
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate(value, properties[key],
                                       f"{path}.{key}"))
            else:
                extra = schema.get("additionalProperties")
                if extra is False:
                    errors.append(f"{path}: unexpected property {key!r}")
                elif isinstance(extra, dict):
                    errors.extend(validate(value, extra, f"{path}.{key}"))

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate(item, item_schema,
                                       f"{path}[{index}]"))

    return errors


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def fingerprint(directory: Path) -> dict:
    """Content hash + size + mtime of every file under `directory`."""
    out = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[str(path.relative_to(directory))] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                stat.st_size, stat.st_mtime_ns)
    return out


def utc_stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60, check=False)


# ---------------------------------------------------------------------
# fixture state dir
# ---------------------------------------------------------------------
def build_state_dir(root: Path, today: datetime) -> Path:
    """A temp state dir with workers.json, shepherd.json and a ledger."""
    root.mkdir(parents=True, exist_ok=True)

    workers = {
        "repo-a-123": {
            "branch": "worker/123-retry-backoff", "issue": 123,
            "note": "worker is still running", "pr": None,
            "provider_used": "minimax/MiniMax-M2.7 (balanced)",
            "repo": "repo-a", "started_at": utc_stamp(today - timedelta(hours=1)),
            "status": "working", "tokens": 100,
            "workdir": str(root.parent / "worktrees" / "repo-a-worker-123"),
        },
        "repo-a-456": {
            "branch": "worker/456-flagged", "issue": 456,
            "note": "the worker asked for a split", "pr": None,
            "provider_used": "minimax/MiniMax-M2.7 (balanced)",
            "repo": "repo-a", "started_at": utc_stamp(today - timedelta(hours=2)),
            "status": "split_suggested", "tokens": 200, "workdir": None,
        },
        "repo-b-78": {
            "branch": "worker/78-rename-config", "issue": 78,
            "note": "failed:empty-diff", "pr": None,
            "provider_used": "minimax/MiniMax-M2.5-highspeed (cheap)",
            "repo": "repo-b", "started_at": utc_stamp(today - timedelta(hours=3)),
            "status": "escalated", "tokens": 300, "workdir": None,
        },
        "repo-b-79": {
            "branch": "worker/79-collect", "issue": 79,
            "note": "BACKLOG EMPTY: repo-b", "pr": None,
            "provider_used": "minimax/MiniMax-M2.5-highspeed (cheap)",
            "repo": "repo-b", "started_at": utc_stamp(today - timedelta(hours=4)),
            "status": "done", "tokens": 400, "workdir": None,
        },
    }
    (root / "workers.json").write_text(json.dumps(workers), encoding="utf-8")

    shepherd = {
        "tracked_prs": {
            "https://github.com/you/repo-a/pull/456": {
                "repo": "repo-a", "pr": 456, "rerun_count": 0,
                "ci_fix_attempts": 0, "added_at": utc_stamp(today),
            },
            "https://github.com/you/repo-b/pull/789": {
                "repo": "repo-b", "pr": 789, "rerun_count": 1,
                "ci_fix_attempts": 2, "jittest:catch": True,
                "added_at": utc_stamp(today),
            },
        },
        "seen_merged": ["https://github.com/you/repo-a/pull/1"],
        "pending_trigger": {"reason": "PR(s) 456 merged", "merges": [456],
                            "requested_at": utc_stamp(today)},
        "last_trigger": {"requested_at": utc_stamp(today)},
    }
    (root / "shepherd.json").write_text(json.dumps(shepherd), encoding="utf-8")

    rows = [
        {"ts": utc_stamp(today - timedelta(hours=1)), "repo": "repo-a",
         "issue": 123, "branch": "worker/123-retry-backoff",
         "run_id": "repo-a-123-1", "tier": "balanced",
         "providers_used": "minimax/MiniMax-M2.7", "tokens_in": 90,
         "tokens_out": 10, "tokens_total": 100, "steps": "10/60",
         "outcome": "pr_opened", "pr_url": "", "gate_triggers": "empty_diff=0",
         "grade": 80},
        {"ts": utc_stamp(today - timedelta(days=1)), "repo": "repo-b",
         "issue": 78, "branch": "worker/78-rename-config",
         "run_id": "repo-b-78-1", "tier": "cheap",
         "providers_used": "minimax/MiniMax-M2.5-highspeed", "tokens_in": 280,
         "tokens_out": 20, "tokens_total": 300, "steps": "60/60",
         "outcome": "failed:empty-diff", "pr_url": "",
         "gate_triggers": "empty_diff=2", "grade": 41},
        {"ts": utc_stamp(today - timedelta(days=9)), "repo": "repo-a",
         "issue": 1, "branch": "worker/1-old", "run_id": "repo-a-1-1",
         "tier": "cheap", "tokens_in": 900, "tokens_out": 100,
         "tokens_total": 1000, "outcome": "pr_opened", "pr_url": ""},
    ]
    with open(root / "worker_ledger.jsonl", "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)
    return root


def build_pr_data(root: Path) -> Path:
    """Fixture PR facts (stands in for `gh`)."""
    data = {
        "you/repo-a#456": {
            "state": "OPEN", "mergeable": "MERGEABLE",
            "files": [{"path": "src/app.py"}],
            "checks": [{"name": "build", "state": "SUCCESS"}],
        },
        "you/repo-b#789": {
            "state": "OPEN", "mergeable": "MERGEABLE",
            "files": [{"path": ".github/workflows/ci.yml"}],
            "checks": [{"name": "build", "state": "FAILURE"},
                       {"name": "lint", "state": "SUCCESS"}],
        },
    }
    path = root / "pr_data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@unittest.skipUnless(SCRIPT.exists(), "bin/plan_coder.py not present")
class PlanCoderCliTest(unittest.TestCase):
    """Every subcommand works on a fixture state dir and writes nothing."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="plan-coder-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.today = datetime.now(UTC)
        self.state = build_state_dir(self.tmp / "state", self.today)
        self.pr_data = build_pr_data(self.tmp)
        self.module = load_cli()

    def cli(self, *args):
        code, out, err = run_cli(self.module, [
            *args, "--state-dir", str(self.state),
            "--config", str(EXAMPLE_CONFIG)])
        self.assertEqual(code, 0, err)
        return out

    def cli_json(self, *args):
        return json.loads(self.cli(*args, "--json"))

    # -- status --------------------------------------------------------
    def test_status_json_matches_the_schemas(self):
        result = self.cli_json("status")
        self.assertEqual(result["schema"], 1)
        self.assertTrue(result["generated_at"].endswith("Z"))

        workers_schema = load_schema("workers.schema.json")
        self.assertTrue(result["workers"]["in_flight"])
        for worker in result["workers"]["in_flight"]:
            self.assertEqual(validate(worker, workers_schema), [])

        shepherd_schema = load_schema("shepherd.schema.json")
        for pr in result["shepherd"]["tracked_prs"]:
            self.assertEqual(validate(pr, shepherd_schema), [])

        self.assertEqual(result["slots"]["max_parallel"], 6)
        self.assertEqual(result["slots"]["active"], 1)
        self.assertEqual(result["slots"]["free"], 5)
        self.assertEqual(result["shepherd"]["pending_trigger"]["merges"], [456])
        self.assertIn("repo-b", result["backlog_empty_repos"])

    def test_status_token_totals_come_from_the_ledger(self):
        result = self.cli_json("status")
        # one row today (100 tokens), two rows inside the 7-day window
        self.assertEqual(result["tokens"]["today"]["tokens_total"], 100)
        self.assertEqual(result["tokens"]["today"]["rows"], 1)
        self.assertEqual(result["tokens"]["week"]["tokens_total"], 400)
        self.assertEqual(result["tokens"]["week"]["rows"], 2)

    def test_ledger_lines_match_the_ledger_line_schema(self):
        schema = load_schema("ledger-line.schema.json")
        with open(self.state / "worker_ledger.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    self.assertEqual(validate(json.loads(line), schema), [])

    # -- collect -------------------------------------------------------
    def test_collect_dry_run_reports_the_diff_check(self):
        result = self.cli_json("collect", "--dry-run")
        self.assertTrue(result["dry_run"])
        collectible = {entry["issue"]: entry
                       for entry in result["collectible"]}
        # `working` and `escalated` are collectible; `split_suggested`,
        # `done` and `failed` are not.
        self.assertIn(123, collectible)
        self.assertIn(78, collectible)
        self.assertNotIn(456, collectible)
        self.assertNotIn(79, collectible)
        self.assertEqual(collectible[123]["diff_check"]["status"], "unknown")
        self.assertTrue(result["skipped"])

    def test_collect_dry_run_matches_manifest_and_git(self):
        workdir = self.tmp / "worktrees" / "repo-a-worker-123"
        workdir.mkdir(parents=True, exist_ok=True)
        if git("init", cwd=workdir).returncode != 0:
            self.skipTest("git is not available")
        git("config", "user.email", "test@example.com", cwd=workdir)
        git("config", "user.name", "test", cwd=workdir)
        (workdir / "a.txt").write_text("one\n", encoding="utf-8")
        git("add", "a.txt", cwd=workdir)
        git("commit", "-m", "base", cwd=workdir)
        base = git("rev-parse", "HEAD", cwd=workdir).stdout.strip()
        (workdir / "a.txt").write_text("two\n", encoding="utf-8")
        (workdir / "b.txt").write_text("new\n", encoding="utf-8")
        (workdir / "WORKER_REPORT.md").write_text(
            "# Worker report\n\n"
            "- Files changed: a.txt, b.txt\n"
            f"- files_changed_git: a.txt, b.txt\n"
            f"- base_commit: {base}\n", encoding="utf-8")

        result = self.cli_json("collect", "--dry-run")
        entry = {e["issue"]: e for e in result["collectible"]}[123]
        self.assertEqual(entry["diff_check"]["status"], "match")

    # -- shepherd-check ------------------------------------------------
    def test_shepherd_check_reports_mergeable_ci_and_hard_stops(self):
        result = self.cli_json("shepherd-check", "--pr-data", str(self.pr_data))
        prs = {pr["pr"]: pr for pr in result["prs"]}
        self.assertEqual(prs[456]["mergeable"], "MERGEABLE")
        self.assertEqual(prs[456]["ci"]["classification"], "green")
        self.assertEqual(prs[456]["hard_stops"], [])
        self.assertEqual(prs[789]["ci"]["classification"], "pr_specific")
        self.assertEqual(prs[789]["ci"]["failing"], ["build"])
        self.assertEqual(prs[789]["workflow_touch"], "workflows")
        self.assertEqual(prs[789]["hard_stops"][0]["kind"], "workflows")
        self.assertEqual(prs[789]["flags"], ["jittest:catch"])

    def test_shepherd_check_classifies_known_repo_wide_failures(self):
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        for repo in config["repos"]:
            if repo["name"] == "repo-b":
                repo["known_ci_failures"] = ["build"]
        config_path = self.tmp / "deployment.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        code, out, err = run_cli(self.module, [
            "shepherd-check", "--state-dir", str(self.state),
            "--config", str(config_path), "--pr-data", str(self.pr_data),
            "--json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        prs = {pr["pr"]: pr for pr in result["prs"]}
        self.assertEqual(prs[789]["ci"]["classification"], "repo_wide")

    # -- next-actions --------------------------------------------------
    def test_next_actions_matches_the_schema(self):
        result = self.cli_json("next-actions", "--pr-data", str(self.pr_data))
        schema = load_schema("next-actions.schema.json")
        self.assertEqual(validate(result, schema), [])

        kinds = {action["kind"] for action in result["actions"]}
        self.assertEqual(kinds, {"merge", "flag", "split", "escalation",
                                 "backlog_empty"})
        for action in result["actions"]:
            self.assertTrue(action["reason_1line"])
            self.assertTrue(action["evidence"])

    def test_next_actions_flag_carries_the_flag_review_fields(self):
        result = self.cli_json("next-actions", "--pr-data", str(self.pr_data))
        flags = [a for a in result["actions"] if a["kind"] == "flag"]
        self.assertTrue(flags)
        for action in flags:
            self.assertIn("reds_classified", action)
            self.assertIn("underlying_question", action)
            self.assertIn("ci_signal_present", action)
            self.assertIn("workflow_touch", action)
        flagged = [a for a in flags if a["pr"] == 789]
        self.assertTrue(flagged)
        self.assertEqual(flagged[0]["workflow_touch"], "workflows")
        self.assertTrue(flagged[0]["ci_signal_present"])
        self.assertEqual(flagged[0]["reds_classified"],
                         [{"red": "build", "class": "pr_specific"}])

    # -- read-only guarantee -------------------------------------------
    def test_no_subcommand_writes_to_the_state_dir(self):
        before = fingerprint(self.state)
        self.cli_json("status")
        self.cli_json("collect", "--dry-run")
        self.cli_json("shepherd-check", "--pr-data", str(self.pr_data))
        self.cli_json("next-actions", "--pr-data", str(self.pr_data))
        after = fingerprint(self.state)
        self.assertEqual(before, after)

    def test_missing_state_dir_exits_2(self):
        code, _out, err = run_cli(self.module, [
            "status", "--state-dir", str(self.tmp / "nope"),
            "--config", str(EXAMPLE_CONFIG), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("state dir not found", err)


@unittest.skipUnless(SCRIPT.exists(), "bin/plan_coder.py not present")
class PlanCoderSchemaTest(unittest.TestCase):
    """The four schemas exist and carry the version marker."""

    def test_schemas_exist_and_are_versioned(self):
        for name in ("workers.schema.json", "shepherd.schema.json",
                     "ledger-line.schema.json", "next-actions.schema.json"):
            path = SCHEMA_DIR / name
            self.assertTrue(path.exists(), f"{name} is missing")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), 1, name)
            self.assertIn("$schema", data, name)

    def test_schema_files_are_json(self):
        for name in ("workers.schema.json", "shepherd.schema.json",
                     "ledger-line.schema.json", "next-actions.schema.json"):
            json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
