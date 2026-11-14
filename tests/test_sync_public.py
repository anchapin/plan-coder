"""Tests for bin/sync_public.py — the fail-closed publish gate (issue #35).

The gate is the only thing standing between the live working tree and
the public remote, so its contract is worth pinning:

* it scans exactly what git would publish (tracked + untracked-not-
  ignored) and aborts on any internal identifier or secret shape;
* it compiles ``bin/*.py`` and runs the repo's own test suite before
  it will commit anything;
* a failing gate leaves the tree untouched — no commit, no push, no
  state file.

No network and no real push: the ``git push`` boundary is mocked with a
``git`` shim placed first on PATH that records the invocation and never
contacts a remote. Every fixture tree, file name and value is synthetic.

Run: python3 -m pytest tests/test_sync_public.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "bin" / "sync_public.py"

# The gate assembles its forbidden literals from fragments on purpose,
# because the scan covers every published file — including the test
# file that exercises it. Spelling them out here would make the real
# repo's own scan hit this file, so the fragments stay split.
LIVE_PATH = "/home/" + "hatch" + "/plan-coder-live"
OWNER_SLUG = "ancha" + "pin"
OWNER_FIRST = "Al" + "ex"
FAKE_SECRET = "sk-" + "EXAMPLENOTAREALKEY123"
FAKE_PAT = "ghp_" + "EXAMPLENOTAREALTOKEN123"
FAKE_AWS_KEY = "AKIA" + "EXAMPLENOTAREAL01"
FAKE_PEM = "BEGIN " + "PRIVATE KEY"

GITIGNORE = """__pycache__/
*.pyc
config/deployment.local.json
local/
"""

# A synthetic "live-only" deployment file: it carries the shape of the
# operator-private config (a home path, a secret, an owner name) and
# must never be published — only scanned when it is not gitignored.
LIVE_CONFIG = json.dumps({
    "operator": {"name": OWNER_FIRST + " Example"},
    "repos": {"alpha": {"local_clone": LIVE_PATH}},
    "publish_gate": {},
    "api": {"token": FAKE_SECRET},
}, indent=1)

GIT_SHIM = '''#!/usr/bin/env python3
"""A `git` shim: every command is forwarded to the real git except
`push`, which is recorded in a log file and never leaves the machine."""
import os
import sys

REAL_GIT = os.environ["PC_REAL_GIT"]
ARGS = sys.argv[1:]
if ARGS[:1] == ["push"]:
    with open(os.environ["PC_PUSH_LOG"], "a", encoding="utf-8") as fh:
        fh.write(" ".join(ARGS) + "\\n")
    sys.exit(int(os.environ.get("PC_PUSH_EXIT", "0")))
os.execv(REAL_GIT, [REAL_GIT] + ARGS)
'''


def load_gate(path: Path):
    """Import the gate script from `path` (a fixture copy) so that its
    module-level REPO points at the fixture tree."""
    spec = importlib.util.spec_from_file_location("sync_public_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Fixture:
    """A throwaway public-repo clone: the gate script, a git repo and a
    synthetic tree. Nothing here is copied from a live deployment."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="pc-gate-"))
        self.root = self.base / "repo"
        self.home = self.base / "home"
        self.shim_dir = self.base / "shimbin"
        self.push_log = self.base / "push.log"
        for path in (self.root, self.home, self.shim_dir):
            path.mkdir(parents=True, exist_ok=True)
        (self.root / "bin").mkdir()
        # the gate's state file lives in local/ (gitignored in a real
        # clone); the directory has to exist for the state write.
        (self.root / "local").mkdir()
        shutil.copyfile(GATE, self.root / "bin" / "sync_public.py")
        (self.shim_dir / "git").write_text(GIT_SHIM, encoding="utf-8")
        (self.shim_dir / "git").chmod(0o755)
        self.real_git = shutil.which("git") or "git"
        self.write(".gitignore", GITIGNORE)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "gate-fixture@example.invalid")
        self.git("config", "user.name", "Gate Fixture")
        self.git("config", "commit.gpgsign", "false")

    # -- fixture construction ------------------------------------------
    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root,
                              capture_output=True, text=True, check=check)

    def commit_all(self, message: str = "base") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def head_count(self) -> int:
        out = self.git("rev-list", "--count", "HEAD").stdout.strip()
        return int(out) if out else 0

    def head_subject(self) -> str:
        return self.git("log", "-1", "--pretty=%s").stdout.strip()

    def tracked_files(self) -> set[str]:
        out = self.git("ls-files").stdout
        return {line for line in out.splitlines() if line}

    def commit_files(self, ref: str = "HEAD") -> set[str]:
        out = self.git("show", "--pretty=format:", "--name-only", ref).stdout
        return {line.strip() for line in out.splitlines() if line.strip()}

    # -- running the gate ---------------------------------------------
    def env(self, push_exit: str = "0", **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "PATH": str(self.shim_dir) + os.pathsep + env.get("PATH", ""),
            "HOME": str(self.home),
            "PC_REAL_GIT": self.real_git,
            "PC_PUSH_LOG": str(self.push_log),
            "PC_PUSH_EXIT": str(push_exit),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        })
        env.pop("PLAN_CODER_CONFIG", None)
        env.update(extra)
        return env

    def run_gate(self, *args: str, push_exit: str = "0",
                 env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "bin" / "sync_public.py"), *args],
            cwd=self.root, capture_output=True, text=True, check=False,
            env=env if env is not None else self.env(push_exit=push_exit))

    def push_calls(self) -> list[str]:
        if not self.push_log.exists():
            return []
        return [line for line in
                self.push_log.read_text(encoding="utf-8").splitlines() if line]

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def result_of(completed: subprocess.CompletedProcess) -> dict:
    """The gate prints its JSON result on the last stdout line."""
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


class TestScanTreeInclusionExclusion(unittest.TestCase):
    """scan_tree() must cover exactly what git would publish."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.write("README.md", "# Synthetic fixture\n")
        self.fx.write("docs/OPERATIONS.md", "Synthetic operations doc.\n")
        self.fx.write("bin/tool.py", "print('synthetic')\n")
        self.fx.write("tests/test_ok.py",
                      "import unittest\n\n\n"
                      "class T(unittest.TestCase):\n"
                      "    def test_ok(self):\n"
                      "        self.assertTrue(True)\n")
        self.fx.commit_all()

    def scan(self):
        mod = load_gate(self.fx.root / "bin" / "sync_public.py")
        return mod.scan_tree()

    def test_gitignored_live_only_file_is_not_scanned(self):
        # The operator-private deployment file has the shape of a live
        # file (home path, secret, owner name) — gitignored, so it is
        # neither scanned nor published.
        self.fx.write("config/deployment.local.json", LIVE_CONFIG)
        self.fx.write("local/sync_state.json", "{}\n")
        self.assertEqual(self.scan(), [])

    def test_same_live_content_in_a_non_ignored_file_is_scanned(self):
        # Same content, but not covered by .gitignore: git would publish
        # it, so the gate must flag it.
        self.fx.write("config/deployment.local.json.bak", LIVE_CONFIG)
        hits = self.scan()
        self.assertTrue(hits, "expected a scan hit for a publishable file")
        self.assertTrue(any("deployment.local.json.bak" in h for h in hits))

    def test_tracked_file_with_forbidden_path_is_scanned(self):
        self.fx.write("docs/leak.md", "clone at " + LIVE_PATH + "\n")
        self.fx.commit_all("add leak")
        hits = self.scan()
        self.assertTrue(any("leak.md" in h and "forbidden string" in h
                            for h in hits), hits)

    def test_secret_shapes_are_scanned(self):
        cases = {
            "docs/secret_key.md": FAKE_SECRET,
            "docs/secret_pat.md": FAKE_PAT,
            "docs/secret_aws.md": FAKE_AWS_KEY,
            "docs/secret_pem.md": FAKE_PEM,
        }
        for rel, value in cases.items():
            with self.subTest(rel=rel):
                self.fx.write(rel, "value: " + value + "\n")
                self.fx.commit_all("add secret")
                hits = self.scan()
                self.assertTrue(any(rel.split("/")[-1] in h for h in hits), hits)

    def test_owner_slug_is_flagged_outside_license(self):
        self.fx.write("docs/owner.md", "repo: " + OWNER_SLUG + "/plan-coder\n")
        self.fx.commit_all("add owner")
        hits = self.scan()
        self.assertTrue(any("owner.md" in h and "owner" in h for h in hits), hits)

    def test_owner_slug_is_allowed_in_license(self):
        self.fx.write("LICENSE", OWNER_SLUG + "\n")
        self.fx.commit_all("add license")
        self.assertEqual(self.scan(), [])

    def test_undecodable_file_is_skipped_not_fatal(self):
        # Binary assets are not readable as UTF-8; the scan skips them
        # instead of blowing up.
        path = self.fx.root / "assets" / "logo.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe\x00\x01binary\x80")
        self.fx.commit_all("add binary asset")
        self.assertEqual(self.scan(), [])

    def test_broken_symlink_is_skipped_not_fatal(self):
        link = self.fx.root / "assets" / "dangling"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(self.fx.root / "assets" / "nowhere")
        self.fx.commit_all("add dangling link")
        self.assertEqual(self.scan(), [])

    def test_clean_fixture_tree_has_no_hits(self):
        self.assertEqual(self.scan(), [])


class TestDocSources(unittest.TestCase):
    """_doc_sources() enumerates the live sources behind the docs."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def load(self, config: str | None) -> tuple[object, list[str]]:
        """Import the gate with `config` as the deployment config and
        return (module, sources enumerated while the env is in place).

        The sources are collected inside the patched environment: the
        gate reads PLAN_CODER_CONFIG at call time, not at import time
        only.
        """
        env = {"HOME": str(self.fx.home)}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("PLAN_CODER_CONFIG", None)
            if config is not None:
                os.environ["PLAN_CODER_CONFIG"] = config
            mod = load_gate(self.fx.root / "bin" / "sync_public.py")
            sources = mod._doc_sources()
            return mod, sources

    def test_no_config_yields_no_sources(self):
        mod, sources = self.load(None)
        self.assertEqual(sources, [])
        self.assertEqual(mod.DOC_SOURCES, [])

    def test_missing_config_file_yields_no_sources(self):
        _, sources = self.load(str(self.fx.base / "absent.json"))
        self.assertEqual(sources, [])

    def test_config_without_publish_gate_yields_no_sources(self):
        cfg = self.fx.base / "cfg.json"
        cfg.write_text(json.dumps({"operator": {"name": "X"}}), encoding="utf-8")
        _, sources = self.load(str(cfg))
        self.assertEqual(sources, [])

    def test_publish_gate_doc_sources_are_enumerated_and_expanded(self):
        cfg = self.fx.base / "cfg.json"
        cfg.write_text(json.dumps({
            "publish_gate": {"doc_sources": [
                "~/ops/OPERATIONS.md",
                str(self.fx.base / "absolute.md"),
            ]},
        }), encoding="utf-8")
        mod, sources = self.load(str(cfg))
        # `~` expands against the fixture HOME, not the real one
        expected = [str(self.fx.home / "ops" / "OPERATIONS.md"),
                    str(self.fx.base / "absolute.md")]
        self.assertEqual(sources, expected)
        self.assertEqual(mod.DOC_SOURCES, expected)

    def test_env_config_wins_over_the_repo_local_config(self):
        # The fixture repo carries no config/deployment.local.json, so
        # the env-provided one is the only source of doc_sources.
        cfg = self.fx.base / "cfg.json"
        cfg.write_text(json.dumps({
            "publish_gate": {"doc_sources": [str(self.fx.base / "env.md")]},
        }), encoding="utf-8")
        _, sources = self.load(str(cfg))
        self.assertEqual(sources, [str(self.fx.base / "env.md")])


class TestFailClosedPaths(unittest.TestCase):
    """Every reachable abort path must exit non-zero and change nothing."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.write("README.md", "# Synthetic fixture\n")
        self.fx.write("bin/tool.py", "print('synthetic')\n")
        self.fx.write("tests/test_ok.py",
                      "import unittest\n\n\n"
                      "class T(unittest.TestCase):\n"
                      "    def test_ok(self):\n"
                      "        self.assertTrue(True)\n")
        self.fx.commit_all()
        self.base_commits = self.fx.head_count()

    def assert_no_side_effects(self, before: int):
        self.assertEqual(self.fx.head_count(), before,
                         "the gate committed despite aborting")
        self.assertEqual(self.fx.push_calls(), [],
                         "the gate pushed despite aborting")
        self.assertFalse((self.fx.root / "local" / "sync_state.json").exists(),
                         "the gate wrote state despite aborting")

    def test_scan_hit_aborts_without_commit(self):
        self.fx.write("docs/leak.md", "clone at " + LIVE_PATH + "\n")
        self.fx.commit_all("add leak")
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ABORT: public-tree scan hits", proc.stdout)
        self.assert_no_side_effects(before)

    def test_unexpected_file_class_in_a_dirty_tree_aborts_without_commit(self):
        # A dirty tree that also carries a publishable live-only file:
        # the scan hit wins, nothing is committed.
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        self.fx.write("config/deployment.local.json.bak", LIVE_CONFIG)
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ABORT: public-tree scan hits", proc.stdout)
        self.assert_no_side_effects(before)

    def test_py_compile_failure_aborts_without_commit(self):
        self.fx.write("bin/broken.py", "def oops(:\n")
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ABORT: py_compile failed for bin/broken.py", proc.stdout)
        self.assert_no_side_effects(before)

    def test_failing_test_suite_aborts_without_commit(self):
        self.fx.write("tests/test_bad.py",
                      "import unittest\n\n\n"
                      "class T(unittest.TestCase):\n"
                      "    def test_bad(self):\n"
                      "        self.fail('synthetic failure')\n")
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ABORT: repo test suite failed", proc.stdout)
        self.assert_no_side_effects(before)

    def test_git_commit_failure_aborts_without_commit(self):
        # No committer identity in the fixture repo -> git refuses the
        # commit; the gate must fail closed instead of pushing.
        self.fx.git("config", "--unset", "user.email")
        self.fx.git("config", "--unset", "user.name")
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ABORT: git commit failed", proc.stdout)
        self.assert_no_side_effects(before)

    def test_malformed_state_file_aborts_without_commit(self):
        # A corrupt local/sync_state.json must not lead to a publish:
        # the gate exits non-zero and commits nothing.
        self.fx.write("local/sync_state.json", "{not json")
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        before = self.fx.head_count()
        proc = self.fx.run_gate("--no-push")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.fx.head_count(), before,
                         "the gate committed despite aborting")
        self.assertEqual(self.fx.push_calls(), [],
                         "the gate pushed despite aborting")

    def test_missing_state_directory_is_reported_as_a_failure(self):
        # Current gate behaviour, pinned as a tripwire: the state file
        # lives in local/, and the gate does not create that directory.
        # With local/ absent the run dies writing the state file after
        # the publish, so the exit code is non-zero. If the gate is
        # fixed to create the directory (or tolerate its absence), this
        # test must be updated together with that change.
        shutil.rmtree(self.fx.root / "local")
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        proc = self.fx.run_gate("--no-push")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.fx.commit_files(), {"README.md"})

    def test_push_failure_exits_3_after_a_local_commit(self):
        # The one branch with a side effect by design: the local commit
        # exists, the push failed, the exit code is 3 (not 2).
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        before = self.fx.head_count()
        proc = self.fx.run_gate(push_exit="1")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("PUSH FAILED after local commit", proc.stdout)
        self.assertEqual(self.fx.head_count(), before + 1)
        self.assertEqual(self.fx.push_calls(), ["push origin main"])


class TestHappyPath(unittest.TestCase):
    """The clean path: exactly the expected files get published."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.write("README.md", "# Synthetic fixture\n")
        self.fx.write("docs/OPERATIONS.md", "Synthetic operations doc.\n")
        self.fx.write("bin/tool.py", "print('synthetic')\n")
        self.fx.write("tests/test_ok.py",
                      "import unittest\n\n\n"
                      "class T(unittest.TestCase):\n"
                      "    def test_ok(self):\n"
                      "        self.assertTrue(True)\n")
        self.fx.commit_all()
        self.base_commits = self.fx.head_count()

    def test_clean_tree_reports_no_changes_and_commits_nothing(self):
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = result_of(proc)
        self.assertEqual(result["status"], "no changes")
        self.assertEqual(result["changed_files"], [])
        self.assertFalse(result["docs_may_be_stale"])
        self.assertEqual(self.fx.head_count(), self.base_commits)
        self.assertEqual(self.fx.push_calls(), [])

    def test_dry_run_reports_the_dirty_set_without_committing(self):
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        proc = self.fx.run_gate("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = result_of(proc)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(result["changed_files"], ["README.md"])
        self.assertEqual(self.fx.head_count(), self.base_commits)
        self.assertFalse((self.fx.root / "local" / "sync_state.json").exists())

    def test_dirty_tree_publishes_exactly_the_expected_file_set(self):
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        self.fx.write("docs/NEW.md", "New synthetic doc.\n")
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = result_of(proc)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(sorted(result["changed_files"]),
                         ["README.md", "docs/NEW.md"])
        self.assertEqual(self.fx.commit_files(),
                         {"README.md", "docs/NEW.md"})
        self.assertEqual(self.fx.head_count(), self.base_commits + 1)
        # the published tree is the fixture tree plus the two edits --
        # the gitignored state file is never published
        self.assertEqual(self.fx.tracked_files(), {
            ".gitignore", "README.md", "bin/sync_public.py", "bin/tool.py",
            "docs/NEW.md", "docs/OPERATIONS.md", "tests/test_ok.py",
        })
        self.assertNotIn("local/sync_state.json", self.fx.tracked_files())

    def test_push_is_mocked_and_the_published_set_is_recorded(self):
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        proc = self.fx.run_gate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = result_of(proc)
        self.assertEqual(result["status"], "synced")
        self.assertTrue(result["pushed"])
        self.assertEqual(self.fx.push_calls(), ["push origin main"])
        self.assertEqual(self.fx.commit_files(), {"README.md"})

    def test_explicit_message_is_used_for_the_commit(self):
        self.fx.write("README.md", "# Synthetic fixture, edited\n")
        proc = self.fx.run_gate("--no-push", "--message",
                                "synthetic sync message")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.fx.head_subject(), "synthetic sync message")
        self.assertEqual(self.fx.commit_files(), {"README.md"})

    def test_docs_may_be_stale_when_a_source_disappears(self):
        first = self.fx.base / "first.md"
        second = self.fx.base / "second.md"
        first.write_text("v1\n", encoding="utf-8")
        second.write_text("v1\n", encoding="utf-8")
        cfg = self.fx.base / "cfg.json"
        cfg.write_text(json.dumps({
            "publish_gate": {"doc_sources": [str(first), str(second)]},
        }), encoding="utf-8")
        env = self.fx.env(PLAN_CODER_CONFIG=str(cfg))
        proc = self.fx.run_gate("--no-push", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(result_of(proc)["docs_may_be_stale"])
        cfg.write_text(json.dumps({
            "publish_gate": {"doc_sources": [str(first)]},
        }), encoding="utf-8")
        proc = self.fx.run_gate("--no-push", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(result_of(proc)["docs_may_be_stale"])

    def test_state_file_records_the_last_result(self):
        proc = self.fx.run_gate("--no-push")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = json.loads(
            (self.fx.root / "local" / "sync_state.json").read_text())
        self.assertEqual(state["last_result"]["status"], "no changes")
        self.assertIn("last_sync_ts", state)

    def test_docs_may_be_stale_is_reported_when_a_source_changed(self):
        source = self.fx.base / "ops.md"
        source.write_text("v1\n", encoding="utf-8")
        cfg = self.fx.base / "cfg.json"
        cfg.write_text(json.dumps({
            "publish_gate": {"doc_sources": [str(source)]},
        }), encoding="utf-8")
        env = self.fx.env(PLAN_CODER_CONFIG=str(cfg))
        first = self.fx.run_gate("--no-push", env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertFalse(result_of(first)["docs_may_be_stale"])
        source.write_text("v2\n", encoding="utf-8")
        second = self.fx.run_gate("--no-push", env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(result_of(second)["docs_may_be_stale"])


if __name__ == "__main__":
    unittest.main()
