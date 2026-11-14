"""Regression test for the API-measured "landed" seam (plan-coder PR #46, JiTTest catch folded in as a permanent regression test).

Hypothesis H1 (class: seam):

    The GitHub-API diff path (``fetch_diff_gh``) never populates the
    ``head``/``base`` keys of the stats dict, so ``measure_rows`` computes
    ``landed`` as False for every API-measured row, which silently disables
    the "known-good (landed) PR" filter in ``select_cases``.

This test drives the REAL producer (``fetch_diff_gh``, via the REAL
``measure_rows`` consumer) with only the ``gh`` subprocess boundary
stubbed, against a REAL temporary git clone whose worker-branch tip IS an
ancestor of the default branch.  The measured record must carry
``landed == True``.

No network, no wall clock, no unseeded randomness.

Run: python3 -m pytest tests/test_ponytail_phase0_landed.py -q
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
SCRIPT = REPO / "bin" / "ponytail_phase0.py"

# A canned `gh api repos/<owner>/<repo>/pulls/<n>/files` payload: one file,
# 10 additions, 2 deletions.
GH_FILES_PAYLOAD = [
    {
        "filename": "demo.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "changes": 12,
    }
]


def load_module():
    spec = importlib.util.spec_from_file_location("ponytail_phase0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_git(repo_dir, *args):
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Phase0 Test",
            "GIT_AUTHOR_EMAIL": "phase0@example.invalid",
            "GIT_COMMITTER_NAME": "Phase0 Test",
            "GIT_COMMITTER_EMAIL": "phase0@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


@unittest.skipUnless(SCRIPT.exists(), "bin/ponytail_phase0.py not present")
class ApiMeasuredRowIsLandedTest(unittest.TestCase):
    """An API-measured row whose branch tip is landed must be flagged landed."""

    def _make_clone(self, root: Path) -> Path:
        """A clone whose default branch (origin/main) contains the worker tip."""
        repo_dir = root / "demo-repo"
        repo_dir.mkdir(parents=True)
        run_git(repo_dir, "init", "-q")
        run_git(repo_dir, "symbolic-ref", "HEAD", "refs/heads/main")

        (repo_dir / "base.txt").write_text("base\n", encoding="utf-8")
        run_git(repo_dir, "add", "-A")
        run_git(repo_dir, "commit", "-q", "-m", "base")

        run_git(repo_dir, "checkout", "-q", "-b", "worker-branch")
        (repo_dir / "demo.py").write_text("print('x')\n", encoding="utf-8")
        run_git(repo_dir, "add", "-A")
        run_git(repo_dir, "commit", "-q", "-m", "worker change")
        worker_sha = run_git(repo_dir, "rev-parse", "HEAD")

        # The worker's branch is merged into the default branch, so its tip is
        # an ancestor of the default branch == "the PR landed".
        run_git(repo_dir, "checkout", "-q", "main")
        run_git(repo_dir, "merge", "-q", "--ff-only", "worker-branch")

        # Make the default branch resolvable the way the script expects:
        # refs/remotes/origin/main plus origin/HEAD.
        run_git(repo_dir, "update-ref", "refs/remotes/origin/main", worker_sha)
        run_git(
            repo_dir,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        )
        return repo_dir

    def _ledger_row(self):
        return {
            "run_id": "demo-repo-3-1700000000",
            "repo": "demo-repo",
            "issue": 3,
            "tier": "cheap",
            "outcome": "pr_opened",
            "pr_url": "https://github.com/anchapin/demo-repo/pull/7",
            "branch": "worker-branch",
            "tokens_in": 1000,
            "tokens_out": 2000,
            "steps": "12/60",
            "grade": 90,
            "ts": "1700000000",
        }

    def test_api_measured_row_is_marked_landed(self):
        module = load_module()
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            # Only the `gh` boundary is stubbed; git keeps running for real.
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "gh":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(GH_FILES_PAYLOAD), stderr=""
                )
            return real_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir = self._make_clone(root)

            module.subprocess.run = fake_run
            try:
                table = module.measure_rows(
                    [self._ledger_row()],
                    str(root),
                    {"demo-repo": str(repo_dir)},
                    True,
                )
            finally:
                module.subprocess.run = real_run

            self.assertEqual(len(table), 1)
            record = table[0]
            # The row was measured through the GitHub API (not the git
            # fallback), so `head`/`base` are whatever fetch_diff_gh produced.
            self.assertEqual(record["status"], "measured")
            self.assertIn("gh api", record["diff_source"])
            self.assertEqual(record["files_changed"], 1)
            self.assertEqual(record["net_lines"], 8)
            # The seam: the worker branch tip is an ancestor of origin/main,
            # so this row must be flagged as landed.
            self.assertTrue(
                record["landed"],
                "API-measured row with a landed branch tip was not flagged landed "
                f"(landed={record['landed']}, diff_source={record['diff_source']!r})",
            )

    def test_landed_row_is_used_as_known_good_case(self):
        """select_cases must be able to treat the landed row as known-good."""
        module = load_module()
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "gh":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(GH_FILES_PAYLOAD), stderr=""
                )
            return real_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir = self._make_clone(root)

            module.subprocess.run = fake_run
            try:
                table = module.measure_rows(
                    [self._ledger_row()],
                    str(root),
                    {"demo-repo": str(repo_dir)},
                    True,
                )
            finally:
                module.subprocess.run = real_run

            known_good = [row for row in table if row.get("landed")]
            self.assertTrue(
                known_good,
                "no row was classified as a known-good (landed) PR, so the "
                "known-good filter in select_cases is silently disabled",
            )


if __name__ == "__main__":
    unittest.main()
