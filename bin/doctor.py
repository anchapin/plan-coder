#!/usr/bin/env python3
"""Read-only diagnose command for the plan-coder deployment (#186).

Phase 0 (2026-11-26) spec'd and hand-ran 11 checks, each mapped to a
historical incident class, and found 3 actionable conditions (worktree
sprawl, a dirty live harness tree, unrotated manual backups) on an
otherwise healthy deployment. This is that check list as one command,
runnable pre-cycle:

    python3 bin/doctor.py [--json] [--config PATH] [--repo-root PATH]
                          [--state-dir PATH] [--skill-dir PATH]

HARD RULES (the whole point of the tool):

* Completely read-only. Doctor never writes, edits, prunes, rotates or
  repairs anything. Compile checks direct their .pyc output to a temp
  dir; state, config and ledger files are only ever read.
* It NEVER runs `gh auth refresh` in any form (that broke local
  agents' gh auth; see MEMORY standing rule). Auth is checked with
  `gh auth status` only, and token lines are filtered out of anything
  doctor records.
* Credential checks report set/not-set and file name/size/exec only.
  No credential value is ever read into a finding or printed.

Findings are typed ok / warn / fail with a one-line evidence string.
The exit code reflects FAILS only: 1 if any check fails, else 0 —
warns (sprawl, clutter, dirty tree) are surfaced, not gated on.

Design constraints baked in from the Phase 0 hand run:

* C2 allowlists the literal {{token}} / {{tokens}} prose that appears
  intentionally in the rendered supervisor prompt; a naive regex
  false-positives on it.
* C10 is bounded: df-style usage numbers, entry counts and the slot
  registry's recorded artifact bytes — never a recursive `du`, which
  did not finish in 60 s over fixtrees + transcripts in Phase 0.
* C1 compares the rendered files in local/ against the manifest
  exactly (those matched byte-for-byte in Phase 0). On-disk cron
  definition copies carry a trailing newline the manifest hash does
  not, so doctor does not hash those copies — it would false-alarm
  on every run.

Stdlib only, Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

OK = "ok"
WARN = "warn"
FAIL = "fail"

STATE_FILES = ("workers.json", "shepherd.json", "worktree_slots.json",
               "spike_windows.json")
LEDGER_FILES = ("worker_ledger.jsonl", "shepherd_ledger.jsonl",
                "flagcheck_ledger.jsonl")
TOKEN_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.]*)\}\}")
TOKEN_ALLOWLIST = {"token", "tokens"}
BACKUP_WARN_THRESHOLD = 5
SPRAWL_REPO_WARN = 25
SPRAWL_UNTRACKED_WARN = 50


@dataclass
class Finding:
    check: str
    status: str
    evidence: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status,
                "evidence": self.evidence, "details": self.details}


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def run_cmd(argv, timeout=30):
    """Read-only subprocess helper; returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


@dataclass
class Context:
    repo_root: str
    config: dict
    config_path: str | None
    state_dir: str
    transcripts_dir: str
    worktrees_dir: str
    skill_dir: str | None
    runner: object = run_cmd

    @property
    def local_dir(self) -> str:
        return os.path.join(self.repo_root, "local")

    @property
    def bin_dir(self) -> str:
        return os.path.join(self.repo_root, "bin")


def load_config(config_path: str | None, repo_root: str):
    """Same search order as agent.py/trace_summary.py, plus an explicit
    --config override (used by tests and by hand-runs against a live
    deployment from a worktree). Returns (config dict, path or None)."""
    candidates = []
    if config_path:
        candidates.append(config_path)
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(repo_root, "config",
                                   "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data, path
        except Exception:  # noqa: BLE001,S112 - config is a convenience
            continue
    return {}, None


def build_context(args, runner=run_cmd) -> Context:
    repo_root = _expand(args.repo_root) if args.repo_root else os.path.dirname(
        os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
    config, config_path = load_config(args.config, repo_root)
    state_cfg = config.get("state") or {}
    state_dir = _expand(args.state_dir or state_cfg.get("dir")
                        or "~/.plan-coder/state")
    transcripts_dir = _expand(
        args.transcripts_dir or state_cfg.get("transcripts_dir")
        or os.path.join(state_dir, "transcripts"))
    worktrees_dir = _expand(
        args.worktrees_dir or (config.get("worktrees") or {}).get("dir")
        or "~/worktrees")
    skill_dir = None
    if args.skill_dir:
        skill_dir = _expand(args.skill_dir)
    elif (config.get("skill") or {}).get("dir"):
        skill_dir = _expand(config["skill"]["dir"])
    return Context(repo_root=repo_root, config=config,
                   config_path=config_path, state_dir=state_dir,
                   transcripts_dir=transcripts_dir,
                   worktrees_dir=worktrees_dir, skill_dir=skill_dir,
                   runner=runner)


def check_cron_hashes(ctx: Context) -> Finding:
    manifest_path = os.path.join(ctx.local_dir, "cron-manifest.json")
    if not os.path.isfile(manifest_path):
        return Finding("cron_hashes", WARN,
                       f"no cron manifest at {manifest_path}")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - reported as the finding
        return Finding("cron_hashes", FAIL,
                       f"cron manifest does not parse: {exc}")
    crons = manifest.get("crons") or {}
    if not crons:
        return Finding("cron_hashes", WARN, "manifest lists no crons")
    bad, checked = [], 0
    for name, entry in sorted(crons.items()):
        rendered = entry.get("rendered") or ""
        path = os.path.join(ctx.local_dir,  # re-anchor by basename:
                            os.path.basename(rendered))  # live path
        if not os.path.isfile(path):
            bad.append(f"{name}: rendered file missing")
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        checked += 1
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            bad.append(f"{name}: sha256 mismatch")
        elif entry.get("chars") is not None and len(raw.decode(
                "utf-8", "replace")) != entry["chars"]:
            bad.append(f"{name}: char count mismatch")
    if bad:
        return Finding("cron_hashes", FAIL, "; ".join(bad),
                       {"checked": checked})
    return Finding("cron_hashes", OK,
                   f"{checked}/{len(crons)} rendered cron bodies match "
                   "the manifest sha256 + char count",
                   {"checked": checked})


def find_unresolved_tokens(text: str) -> list:
    return sorted({m.group(1) for m in TOKEN_RE.finditer(text)
                   if m.group(1) not in TOKEN_ALLOWLIST})


def check_unresolved_tokens(ctx: Context) -> Finding:
    if not os.path.isdir(ctx.local_dir):
        return Finding("unresolved_tokens", WARN,
                       f"no local/ dir at {ctx.local_dir}")
    offenders = []
    files = 0
    for name in sorted(os.listdir(ctx.local_dir)):
        if not name.endswith(".rendered.md"):
            continue
        files += 1
        with open(os.path.join(ctx.local_dir, name),
                  encoding="utf-8", errors="replace") as fh:
            hits = find_unresolved_tokens(fh.read())
        if hits:
            offenders.append(f"{name}: {', '.join(hits)}")
    if offenders:
        return Finding("unresolved_tokens", FAIL,
                       "unresolved tokens in " + "; ".join(offenders))
    return Finding("unresolved_tokens", OK,
                   f"no unresolved tokens in {files} rendered file(s) "
                   "(literal {{token}} prose allowlisted)")


def count_backups(directory: str, stem: str) -> tuple:
    statebak = bak = 0
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            if name.startswith(stem + ".statebak-"):
                statebak += 1
            elif name.startswith(stem + ".bak-"):
                bak += 1
    return statebak, bak


def check_state_files(ctx: Context) -> Finding:
    problems, notes = [], []
    total_bak = 0
    for name in STATE_FILES:
        path = os.path.join(ctx.state_dir, name)
        statebak, bak = count_backups(ctx.state_dir, name)
        total_bak += bak
        if bak:
            notes.append(f"{name}.bak-* x{bak}")
        if statebak:
            notes.append(f"{name}.statebak-* x{statebak}")
        if not os.path.isfile(path):
            if name in ("workers.json", "shepherd.json"):
                problems.append(f"{name} missing")
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                json.load(fh)
        except Exception as exc:  # noqa: BLE001 - reported as finding
            problems.append(f"{name} does not parse: {exc}")
        staging = path + ".new"
        if os.path.isfile(staging):
            try:
                with open(staging, encoding="utf-8") as fh:
                    json.load(fh)
                notes.append(f"{name}.new staging present, valid")
            except Exception:  # noqa: BLE001 - reported as finding
                problems.append(f"{name}.new staging does not parse")
    if problems:
        return Finding("state_files", FAIL, "; ".join(problems),
                       {"backups": notes})
    if total_bak > BACKUP_WARN_THRESHOLD:
        return Finding(
            "state_files", WARN,
            f"state files parse; unrotated manual backups: "
            f"{total_bak} .bak-* files ({'; '.join(notes)})",
            {"backups": notes})
    return Finding("state_files", OK,
                   "state files parse; backups: "
                   + ("; ".join(notes) if notes else "none"),
                   {"backups": notes})


def check_credentials(ctx: Context) -> Finding:
    notes = []
    helper_dir = (ctx.config.get("credentials") or {}).get("helper_dir")
    status = OK
    if helper_dir:
        hdir = _expand(helper_dir)
        if not os.path.isdir(hdir):
            return Finding("credentials", FAIL,
                           f"configured credential helper dir missing: "
                           f"{hdir}")
        entries = []
        for name in sorted(os.listdir(hdir)):
            full = os.path.join(hdir, name)
            if os.path.isfile(full):
                entries.append(
                    f"{name} ({os.path.getsize(full)} B, "
                    f"{'exec' if os.access(full, os.X_OK) else 'non-exec'})")
        notes.append(f"helper dir present: {', '.join(entries) or 'empty'}")
    else:
        notes.append("no credential helper dir configured")
        status = WARN
    providers = ctx.config.get("providers") or {}
    env_notes = []
    for pname, pcfg in sorted(providers.items()):
        env_key = (pcfg or {}).get("env_key")
        if env_key:
            env_notes.append(
                f"{pname}: {env_key}="
                f"{'set' if os.environ.get(env_key) else 'not-set'}")
    if env_notes:
        notes.append("provider env keys (presence only): "
                     + ", ".join(env_notes))
    return Finding("credentials", status, "; ".join(notes))


def check_gh_auth(ctx: Context) -> Finding:
    rc, out, err = ctx.runner(["gh", "auth", "status"])
    text = out + err
    if rc != 0 or "Logged in" not in text:
        return Finding("gh_auth", FAIL,
                       "gh auth status does not show a logged-in account "
                       "(checked read-only; no refresh attempted)")
    account = ""
    for line in text.splitlines():
        if "Token:" in line:  # never record token material
            continue
        m = re.search(r"account ([A-Za-z0-9-]+)", line)
        if m:
            account = m.group(1)
            break
    return Finding("gh_auth", OK,
                   f"gh logged in{f' as {account}' if account else ''} "
                   "(status check only; no refresh attempted)")


def parse_worktree_porcelain(text: str) -> tuple:
    """(registered count, prunable count) from `git worktree list
    --porcelain` output."""
    count = prunable = 0
    for line in text.splitlines():
        if line.startswith("worktree "):
            count += 1
        elif line.strip() == "prunable":
            prunable += 1
    return count, prunable


def sprawl_status(total: int, per_repo: dict, tracked: int) -> str:
    untracked = total - tracked
    if untracked >= SPRAWL_UNTRACKED_WARN or (
            per_repo and max(per_repo.values()) >= SPRAWL_REPO_WARN):
        return WARN
    return OK


def check_worktrees(ctx: Context) -> Finding:
    per_repo, prunable_total, total = {}, 0, 0
    errors = []
    for repo in ctx.config.get("repos") or []:
        clone = _expand(repo.get("clone") or "")
        if not clone or not os.path.isdir(clone):
            continue
        rc, out, err = ctx.runner(
            ["git", "-C", clone, "worktree", "list", "--porcelain"])
        if rc != 0:
            errors.append(f"{repo.get('name')}: {err.strip()[:60]}")
            continue
        count, prunable = parse_worktree_porcelain(out)
        per_repo[repo.get("name") or clone] = count
        total += count
        prunable_total += prunable
    tracked = 0
    slots_path = os.path.join(ctx.state_dir, "worktree_slots.json")
    if os.path.isfile(slots_path):
        try:
            with open(slots_path, encoding="utf-8") as fh:
                slots = json.load(fh)
            rows = slots.get("slots") if isinstance(slots, dict) else slots
            if isinstance(rows, list):
                tracked = len(rows)
            elif isinstance(slots, dict):
                tracked = len(slots)
        except Exception:  # noqa: BLE001 - C3 reports the parse failure
            tracked = 0
    fixtree_entries = (len(os.listdir(ctx.worktrees_dir))
                       if os.path.isdir(ctx.worktrees_dir) else 0)
    status = sprawl_status(total, per_repo, tracked)
    if errors and not per_repo:
        status = WARN
    top = ", ".join(f"{k} {v}" for k, v in
                    sorted(per_repo.items(), key=lambda kv: -kv[1])[:3])
    return Finding(
        "worktrees", status,
        f"{total} registered worktrees across {len(per_repo)} clones "
        f"(top: {top}); {prunable_total} prunable; {tracked} "
        f"slot-tracked; {fixtree_entries} entries in worktrees dir"
        + (f"; errors: {'; '.join(errors)}" if errors else ""),
        {"per_repo": per_repo, "prunable": prunable_total,
         "slot_tracked": tracked})


def check_config(ctx: Context) -> Finding:
    if ctx.config_path is None:
        return Finding("config", FAIL,
                       "no deployment config found or it does not parse")
    missing_repos, missing_aux = [], []
    for repo in ctx.config.get("repos") or []:
        clone = repo.get("clone")
        if clone and not os.path.isdir(_expand(clone)):
            missing_repos.append(f"{repo.get('name')}: {clone}")
    aux = {"state dir": ctx.state_dir,
           "transcripts dir": ctx.transcripts_dir,
           "worktrees dir": ctx.worktrees_dir}
    if ctx.skill_dir:
        aux["skill dir"] = ctx.skill_dir
    public_clone = (ctx.config.get("public_repo") or {}).get("local_clone")
    if public_clone:
        aux["public clone"] = _expand(public_clone)
    for label, path in aux.items():
        if not os.path.isdir(path):
            missing_aux.append(f"{label}: {path}")
    config_dir = os.path.dirname(ctx.config_path)
    siblings = [n for n in os.listdir(config_dir)
                if n.startswith("deployment.local.json.")
                ] if os.path.isdir(config_dir) else []
    if missing_repos:
        return Finding("config", FAIL,
                       "config parses; missing repo clones: "
                       + "; ".join(missing_repos))
    if missing_aux or len(siblings) > BACKUP_WARN_THRESHOLD:
        bits = []
        if missing_aux:
            bits.append("missing paths: " + "; ".join(missing_aux))
        if len(siblings) > BACKUP_WARN_THRESHOLD:
            bits.append(f"{len(siblings)} stale config siblings in "
                        "config/ (backups/staging)")
        return Finding("config", WARN,
                       "config parses; " + "; ".join(bits),
                       {"config_siblings": siblings})
    return Finding("config", OK,
                   f"config parses ({ctx.config_path}); all repo clones "
                   "and configured paths exist",
                   {"config_siblings": siblings})


def _tail_bad_lines(path: str, tail: int = 200) -> tuple:
    """(line count, bad lines in the last `tail` non-empty lines)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    bad = 0
    for line in lines[-tail:]:
        if not line.strip():
            continue
        try:
            json.loads(line)
        except Exception:  # noqa: BLE001 - counted, not raised
            bad += 1
    return len(lines), bad


def check_ledgers(ctx: Context) -> Finding:
    problems, notes = [], []
    for name in LEDGER_FILES:
        path = os.path.join(ctx.state_dir, name)
        if not os.path.isfile(path):
            notes.append(f"{name} absent")
            continue
        count, bad = _tail_bad_lines(path)
        notes.append(f"{name} {count} lines, {bad} bad in tail")
        if bad:
            problems.append(f"{name}: {bad} unparseable tail line(s)")
    transcripts = []
    if os.path.isdir(ctx.transcripts_dir):
        transcripts = [os.path.join(ctx.transcripts_dir, n)
                       for n in os.listdir(ctx.transcripts_dir)
                       if n.endswith(".jsonl")]
    if transcripts:
        newest = max(transcripts, key=os.path.getmtime)
        _, bad = _tail_bad_lines(newest, tail=1)
        notes.append(f"{len(transcripts)} transcripts, newest tail "
                     f"{'bad' if bad else 'parses'}")
        if bad:
            problems.append("newest transcript tail does not parse")
    if problems:
        return Finding("ledgers", FAIL, "; ".join(problems),
                       {"notes": notes})
    status = WARN if any("absent" in n for n in notes) else OK
    return Finding("ledgers", status, "; ".join(notes) or "no ledgers",
                   {"notes": notes})


def check_compile(ctx: Context) -> Finding:
    files = sorted(n for n in os.listdir(ctx.bin_dir)
                   if n.endswith(".py")) \
        if os.path.isdir(ctx.bin_dir) else []
    if not files:
        return Finding("compile", WARN, f"no bin/*.py under {ctx.bin_dir}")
    failures = []
    with tempfile.TemporaryDirectory(prefix="doctor-pyc-") as tmp:
        for name in files:
            src = os.path.join(ctx.bin_dir, name)
            try:
                py_compile.compile(src, cfile=os.path.join(tmp, name + "c"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f"{name}: {exc.msg.splitlines()[0][:80]}")
    if failures:
        return Finding("compile", FAIL, "; ".join(failures))
    return Finding("compile", OK,
                   f"{len(files)}/{len(files)} bin/*.py compile clean "
                   "(bytecode directed to a temp dir)")


def check_disk(ctx: Context) -> Finding:
    usage = shutil.disk_usage(ctx.repo_root)
    free_pct = 100.0 * usage.free / usage.total if usage.total else 0.0
    gib = 1024 ** 3
    status = OK
    if free_pct < 5:
        status = FAIL
    elif free_pct < 15:
        status = WARN
    artifact_bytes = 0
    slots_path = os.path.join(ctx.state_dir, "worktree_slots.json")
    if os.path.isfile(slots_path):
        try:
            with open(slots_path, encoding="utf-8") as fh:
                slots = json.load(fh)
            rows = slots.get("slots") if isinstance(slots, dict) else slots
            if isinstance(rows, list):
                artifact_bytes = sum(
                    int(r.get("artifact_bytes") or 0)
                    for r in rows if isinstance(r, dict))
        except Exception:  # noqa: BLE001 - C3 reports parse failures
            artifact_bytes = 0
    entries = (len(os.listdir(ctx.worktrees_dir))
               if os.path.isdir(ctx.worktrees_dir) else 0)
    return Finding(
        "disk", status,
        f"volume {usage.total / gib:.0f} GiB, {usage.free / gib:.0f} GiB "
        f"free ({free_pct:.0f}%); worktrees dir has {entries} entries; "
        f"slot registry records {artifact_bytes / gib:.2f} GiB of "
        "preserved artifacts (bounded check, no recursive scan)")


def check_symlinks_and_cleanliness(ctx: Context) -> Finding:
    broken = []
    if ctx.skill_dir:
        skill_bin = os.path.join(ctx.skill_dir, "bin")
        if os.path.isdir(skill_bin):
            for name in sorted(os.listdir(skill_bin)):
                full = os.path.join(skill_bin, name)
                if os.path.islink(full) and not os.path.exists(full):
                    broken.append(name)
    rc, out, _ = ctx.runner(
        ["git", "-C", ctx.repo_root, "status", "--porcelain"])
    entries = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 \
        else []
    strays = []
    if os.path.isdir(ctx.bin_dir):
        strays = [n for n in sorted(os.listdir(ctx.bin_dir))
                  if n.endswith((".orig", ".bak"))]
    if broken:
        return Finding("live_tree", FAIL,
                       "broken skills-bin symlinks: " + ", ".join(broken))
    if entries or strays:
        modified = sum(1 for ln in entries if not ln.startswith("??"))
        untracked = sum(1 for ln in entries if ln.startswith("??"))
        bits = [(f"live repo tree dirty: {len(entries)} git status "
                 f"entries ({modified} tracked-modified, {untracked} "
                 "untracked) — live harness runs this tree via symlinks")]
        if strays:
            bits.append("stray files in bin/: " + ", ".join(strays))
        return Finding("live_tree", WARN, "; ".join(bits),
                       {"entries": entries[:20], "strays": strays})
    return Finding("live_tree", OK,
                   "skills-bin symlinks resolve; live repo tree clean")


CHECKS = (check_cron_hashes, check_unresolved_tokens, check_state_files,
          check_credentials, check_gh_auth, check_worktrees, check_config,
          check_ledgers, check_compile, check_disk,
          check_symlinks_and_cleanliness)


def run_all(ctx: Context) -> list:
    findings = []
    for fn in CHECKS:
        try:
            findings.append(fn(ctx))
        except Exception as exc:  # noqa: BLE001 - check failure = finding
            findings.append(Finding(fn.__name__, FAIL,
                                    f"check itself errored: {exc}"))
    return findings


def render_text(findings: list) -> str:
    marks = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}
    lines = ["plan-coder doctor — read-only deployment diagnosis"]
    for f in findings:
        lines.append(f"[{marks[f.status]}] {f.check}: {f.evidence}")
    fails = sum(1 for f in findings if f.status == FAIL)
    warns = sum(1 for f in findings if f.status == WARN)
    lines.append(f"summary: {fails} fail, {warns} warn, "
                 f"{len(findings) - fails - warns} ok "
                 f"(exit code reflects fails only)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only diagnosis of the plan-coder deployment.")
    ap.add_argument("--json", action="store_true",
                    help="emit findings as JSON")
    ap.add_argument("--config", default=None,
                    help="deployment config path (default: search order "
                         "$PLAN_CODER_CONFIG, <repo>/config/"
                         "deployment.local.json, ~/.plan-coder/)")
    ap.add_argument("--repo-root", default=None,
                    help="plan-coder repo clone (default: this file's "
                         "repo)")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--transcripts-dir", default=None)
    ap.add_argument("--worktrees-dir", default=None)
    ap.add_argument("--skill-dir", default=None)
    args = ap.parse_args(argv)
    ctx = build_context(args)
    findings = run_all(ctx)
    if args.json:
        print(json.dumps(
            {"findings": [f.to_dict() for f in findings],
             "fails": sum(1 for f in findings if f.status == FAIL),
             "warns": sum(1 for f in findings if f.status == WARN)},
            indent=2))
    else:
        print(render_text(findings))
    return 1 if any(f.status == FAIL for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
