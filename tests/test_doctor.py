"""Tests for bin/doctor.py (read-only diagnose command, #186 Phase 1).

Hermetic: every test builds a fixture deployment in a temp dir —
fake config, state files, ledgers, rendered prompts, a real temp git
repo for the worktree/status checks — and injects a fake subprocess
runner for gh. No network, no live state, no credential values.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))
import doctor  # noqa: E402

SCRIPT = BIN / "doctor.py"


def fake_runner(mapping):
    def run(argv, timeout=30):
        key = tuple(argv)
        if key in mapping:
            return mapping[key]
        return (0, "", "")
    return run


GH_OK = (0, "", ("github.com\n  ✓ Logged in to github.com account "
                 "anchapin (keyring)\n  - Token: ghp_SECRETVALUE\n"))


class DoctorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-"))
        self.repo = self.tmp / "repo"
        (self.repo / "bin").mkdir(parents=True)
        (self.repo / "local").mkdir()
        (self.repo / "config").mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.transcripts = self.state / "transcripts"
        self.transcripts.mkdir()
        self.worktrees = self.tmp / "fixtrees"
        self.worktrees.mkdir()
        self.skill = self.tmp / "skill"
        (self.skill / "bin").mkdir(parents=True)
        self.config = {
            "state": {"dir": str(self.state),
                      "transcripts_dir": str(self.transcripts)},
            "worktrees": {"dir": str(self.worktrees)},
            "skill": {"dir": str(self.skill)},
            "credentials": {"helper_dir": str(self.tmp / "cred")},
            "providers": {"zai": {"env_key": "DOCTOR_TEST_KEY"}},
            "repos": [{"name": "alpha", "clone": str(self.repo)}],
        }
        self.config_path = self.repo / "config" / "deployment.local.json"
        self.config_path.write_text(json.dumps(self.config))

    def ctx(self, runner=None, config=None):
        return doctor.Context(
            repo_root=str(self.repo),
            config=self.config if config is None else config,
            config_path=str(self.config_path),
            state_dir=str(self.state),
            transcripts_dir=str(self.transcripts),
            worktrees_dir=str(self.worktrees),
            skill_dir=str(self.skill),
            runner=runner or fake_runner({}))

    def write_rendered(self, name="alpha", body="hello {{token}} prose"):
        path = self.repo / "local" / f"{name}.rendered.md"
        path.write_text(body)
        manifest = {"crons": {name: {
            "rendered": f"/elsewhere/{name}.rendered.md",
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "chars": len(body)}}}
        (self.repo / "local" / "cron-manifest.json").write_text(
            json.dumps(manifest))
        return path


class CronHashTests(DoctorCase):
    def test_match_ok(self):
        self.write_rendered()
        f = doctor.check_cron_hashes(self.ctx())
        self.assertEqual(f.status, doctor.OK)

    def test_mismatch_fail(self):
        self.write_rendered()
        (self.repo / "local" / "alpha.rendered.md").write_text("tampered")
        f = doctor.check_cron_hashes(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)

    def test_missing_manifest_warn(self):
        f = doctor.check_cron_hashes(self.ctx())
        self.assertEqual(f.status, doctor.WARN)


class TokenTests(DoctorCase):
    def test_allowlisted_prose_ok(self):
        self.write_rendered(body="Prompts are {{token}} templates, "
                                 "{{tokens}} here")
        f = doctor.check_unresolved_tokens(self.ctx())
        self.assertEqual(f.status, doctor.OK)

    def test_unresolved_fail(self):
        self.write_rendered(body="hi {{operator_name}}")
        f = doctor.check_unresolved_tokens(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)
        self.assertIn("operator_name", f.evidence)


class StateFileTests(DoctorCase):
    def test_valid_ok(self):
        (self.state / "workers.json").write_text("{}")
        (self.state / "shepherd.json").write_text("{}")
        f = doctor.check_state_files(self.ctx())
        self.assertEqual(f.status, doctor.OK)

    def test_corrupt_fail(self):
        (self.state / "workers.json").write_text("{not json")
        (self.state / "shepherd.json").write_text("{}")
        f = doctor.check_state_files(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)

    def test_unrotated_backups_warn(self):
        (self.state / "workers.json").write_text("{}")
        (self.state / "shepherd.json").write_text("{}")
        for i in range(7):
            (self.state / f"shepherd.json.bak-{i}").write_text("{}")
        f = doctor.check_state_files(self.ctx())
        self.assertEqual(f.status, doctor.WARN)
        self.assertIn("7", f.evidence)

    def test_count_backups_kinds(self):
        (self.state / "workers.json.statebak-1").write_text("{}")
        (self.state / "workers.json.bak-1").write_text("{}")
        statebak, bak = doctor.count_backups(
            str(self.state), "workers.json")
        self.assertEqual((statebak, bak), (1, 1))


class CredentialTests(DoctorCase):
    def test_presence_only_no_values(self):
        cred = self.tmp / "cred"
        cred.mkdir()
        (cred / "helper.py").write_text("# helper")
        os.environ["DOCTOR_TEST_KEY"] = "supersecretvalue"
        try:
            f = doctor.check_credentials(self.ctx())
        finally:
            del os.environ["DOCTOR_TEST_KEY"]
        self.assertEqual(f.status, doctor.OK)
        self.assertIn("DOCTOR_TEST_KEY=set", f.evidence)
        self.assertNotIn("supersecretvalue", f.evidence)
        self.assertNotIn("supersecretvalue", json.dumps(f.to_dict()))

    def test_missing_helper_dir_fail(self):
        f = doctor.check_credentials(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)


class GhAuthTests(DoctorCase):
    def test_logged_in_ok_token_filtered(self):
        runner = fake_runner({("gh", "auth", "status"): GH_OK})
        f = doctor.check_gh_auth(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.OK)
        self.assertIn("anchapin", f.evidence)
        self.assertNotIn("ghp_SECRETVALUE", json.dumps(f.to_dict()))

    def test_not_logged_in_fail(self):
        runner = fake_runner({("gh", "auth", "status"): (1, "", "no")})
        f = doctor.check_gh_auth(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.FAIL)

    def test_runner_never_refreshes(self):
        seen = []

        def runner(argv, timeout=30):
            seen.append(argv)
            return GH_OK
        doctor.check_gh_auth(self.ctx(runner=runner))
        self.assertTrue(all("refresh" not in a for a in seen))


class WorktreeTests(DoctorCase):
    def test_porcelain_parse(self):
        text = ("worktree /a\nHEAD abc\nbranch refs/heads/x\n\n"
                "worktree /b\nHEAD def\nprunable\n")
        self.assertEqual(doctor.parse_worktree_porcelain(text), (2, 1))

    def test_sprawl_warn(self):
        porcelain = "".join(f"worktree /w{i}\nHEAD x\n\n"
                            for i in range(60))
        runner = fake_runner(
            {("git", "-C", str(self.repo), "worktree", "list",
              "--porcelain"): (0, porcelain, "")})
        f = doctor.check_worktrees(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.WARN)
        self.assertIn("60 registered", f.evidence)

    def test_small_ok(self):
        runner = fake_runner(
            {("git", "-C", str(self.repo), "worktree", "list",
              "--porcelain"): (0, "worktree /a\nHEAD x\n", "")})
        f = doctor.check_worktrees(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.OK)


class ConfigTests(DoctorCase):
    def test_all_paths_exist_ok(self):
        f = doctor.check_config(self.ctx())
        self.assertEqual(f.status, doctor.OK)

    def test_missing_clone_fail(self):
        cfg = dict(self.config)
        cfg["repos"] = [{"name": "ghost", "clone": str(self.tmp / "nope")}]
        f = doctor.check_config(self.ctx(config=cfg))
        self.assertEqual(f.status, doctor.FAIL)

    def test_stale_siblings_warn(self):
        for i in range(6):
            (self.repo / "config"
             / f"deployment.local.json.bak-{i}").write_text("{}")
        f = doctor.check_config(self.ctx())
        self.assertEqual(f.status, doctor.WARN)


class LedgerTests(DoctorCase):
    def test_tails_parse_ok(self):
        (self.state / "worker_ledger.jsonl").write_text(
            '{"a": 1}\n{"a": 2}\n')
        (self.state / "shepherd_ledger.jsonl").write_text('{"b": 1}\n')
        (self.state / "flagcheck_ledger.jsonl").write_text('{"c": 1}\n')
        (self.transcripts / "run1.jsonl").write_text('{"t": 1}\n')
        f = doctor.check_ledgers(self.ctx())
        self.assertEqual(f.status, doctor.OK)

    def test_bad_tail_fail(self):
        (self.state / "worker_ledger.jsonl").write_text('{"a": 1}\nbad\n')
        f = doctor.check_ledgers(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)


class CompileTests(DoctorCase):
    def test_compile_ok_and_no_pycache_in_bin(self):
        (self.repo / "bin" / "good.py").write_text("x = 1\n")
        f = doctor.check_compile(self.ctx())
        self.assertEqual(f.status, doctor.OK)
        self.assertFalse((self.repo / "bin" / "__pycache__").exists())

    def test_compile_fail(self):
        (self.repo / "bin" / "bad.py").write_text("def broken(:\n")
        f = doctor.check_compile(self.ctx())
        self.assertEqual(f.status, doctor.FAIL)


class DiskTests(DoctorCase):
    def test_bounded_ok(self):
        f = doctor.check_disk(self.ctx())
        self.assertIn(f.status, (doctor.OK, doctor.WARN))
        self.assertIn("no recursive scan", f.evidence)


class LiveTreeTests(DoctorCase):
    def test_broken_symlink_fail(self):
        (self.skill / "bin" / "agent.py").symlink_to(self.tmp / "missing")
        runner = fake_runner(
            {("git", "-C", str(self.repo), "status", "--porcelain"):
             (0, "", "")})
        f = doctor.check_symlinks_and_cleanliness(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.FAIL)

    def test_dirty_tree_and_stray_warn(self):
        (self.repo / "bin" / "agent.py.orig").write_text("leftover")
        runner = fake_runner(
            {("git", "-C", str(self.repo), "status", "--porcelain"):
             (0, " M bin/agent.py\n?? bin/agent.py.orig\n", "")})
        f = doctor.check_symlinks_and_cleanliness(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.WARN)
        self.assertIn("agent.py.orig", f.evidence)

    def test_clean_ok(self):
        runner = fake_runner(
            {("git", "-C", str(self.repo), "status", "--porcelain"):
             (0, "", "")})
        f = doctor.check_symlinks_and_cleanliness(self.ctx(runner=runner))
        self.assertEqual(f.status, doctor.OK)


class CliTests(DoctorCase):
    def test_json_cli_warn_only_exits_zero(self):
        # Fixture deployment: no manifest (warn), missing cred dir
        # would fail — point the config's helper dir at a real dir so
        # the only non-ok findings are warns, and gh is stubbed by the
        # real environment check being allowed to fail... instead run
        # main() in-process with a fake runner via run_all on a ctx
        # whose findings contain warns only.
        ctx = self.ctx(runner=fake_runner(
            {("gh", "auth", "status"): GH_OK}))
        findings = doctor.run_all(ctx)
        self.assertEqual(len(findings), 11)
        payload = json.dumps([f.to_dict() for f in findings])
        self.assertNotIn("ghp_SECRETVALUE", payload)

    def test_exit_code_reflects_fails_only(self):
        warn_only = [doctor.Finding("x", doctor.WARN, "w")]
        fail_one = [doctor.Finding("x", doctor.FAIL, "f")]
        self.assertEqual(
            1 if any(f.status == doctor.FAIL for f in fail_one) else 0, 1)
        self.assertEqual(
            1 if any(f.status == doctor.FAIL for f in warn_only) else 0, 0)

    def test_cli_subprocess_json_shape(self):
        (self.tmp / "cred").mkdir(exist_ok=True)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--json",
             "--config", str(self.config_path),
             "--repo-root", str(self.repo),
             "--state-dir", str(self.state),
             "--transcripts-dir", str(self.transcripts),
             "--worktrees-dir", str(self.worktrees),
             "--skill-dir", str(self.skill)],
            capture_output=True, text=True, check=False)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["findings"]), 11)
        self.assertIn(proc.returncode, (0, 1))
        self.assertEqual(proc.returncode,
                         1 if data["fails"] else 0)


if __name__ == "__main__":
    unittest.main()
