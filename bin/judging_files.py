#!/usr/bin/env python3
"""Judging-file flag for worker PRs (issue #96, from the #94
verification spike, Part C — Phase 0 PROCEED 2026-11-17).

Phase 0 audited 492 merged worker PRs: 19 touched gate-logic /
lint-config / CONTRACT-class files outside the routine-bump class,
and only 1 of the 19 had ever been flagged. No covert weakening was
found — the gap is VISIBILITY, not tampering. This check surfaces
those touches mechanically, in the test_tamper.py pattern (diff-only,
deterministic, flag-only). It is a NEW script, not a test_tamper
extension: the file universe (non-test judging files), the
classification (routine / creation / modification) and the
authorisation semantics are all different, and each check keeps its
own exit/flag contract clean.

Classification (Phase 0's c2_analyze2 judge_class, verbatim
semantics), by path:
  ci_workflow            .github/workflows/* — already hard-stopped;
                         counted, never flagged by this check
  contract               CONTRACT.md exactly
  ratchet_baseline_script  basename in BASELINE_SCRIPTS — routine
  gate_script            basename check_*.(py|mjs|js|ts), or a
                         *gate* script basename
  ratchet_data           "ratchet" anywhere in the path — routine
                         (baseline bumps are the sanctioned ratchet
                         motion)
  routine_count          test_inventory(.json|_baseline.json)
  lint_config            .oxlintrc.json, eslint.config.*, .eslintrc*,
                         ruff.toml, deny.toml, .config/nextest.toml
  test_file              tests/spec dirs and test-file name patterns —
                         test_tamper's territory; counted, not flagged

Flag rules (changed-file status from git name-status):
  - status A (added) on a gate/lint/contract file -> `creation`,
    NEVER flagged (creating a gate is the healthiest touch there is)
  - status M or D (modified/deleted) on a gate_script file -> flag,
    class `modification`
  - status M or D on a lint_config file -> flag, class `lint-config`
  - status M or D on CONTRACT.md -> flag, class `contract`
  - routine classes are classified and counted, NEVER flagged

The judging list at a PR's base SHA: the pattern rules above are
code, but a repo may ALSO carry a list file (default path
`judging_files.json`: {"extra": [paths], "exclude": [paths]}) with
repo-specific judging paths. When --base-sha and --repo-dir are
given, that file is read with `git show <base-sha>:<path>` — from
the base commit, NEVER from the working tree or the PR head, so a PR
that edits the list cannot shrink its own judging set (a test proves
this). Extra paths classify as gate_script; excluded paths classify
as None (they leave the judging set entirely, routine included).

The check FLAGS ONLY. It never merges, never blocks by force, never
edits a PR. The shepherd prompt decides what to do with a flag
(leave it for the operator). Ships disabled:
`shepherd.judging_files.enabled` in the deployment config must be
true; otherwise the script exits 2, exactly as test_tamper does.
`--force` runs the analysis anyway (operator one-off use).

Usage:
    python3 bin/judging_files.py [--changed-files-file PR.files]
                                 [--changed-files "M a.py,A b.py"]
                                 [--repo-dir DIR] [--base-sha SHA]
                                 [--list-path judging_files.json]
                                 [--force] [--json]

Exit 0 = no flag; 1 = flagged (one-line reason + evidence);
2 = check disabled in the deployment config / usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config

BASELINE_SCRIPTS = {
    "check_test_inventory_drift.py", "check_physics_sim_cycle.py",
    "check_ashrae_cases_cycle.py", "check_module_size.py",
    "check_orphan_modules.py", "generate_quarantine_registry.py",
    "check_cycle_downward_trend.py",
}
LINT_BASENAMES = {
    ".oxlintrc.json", "eslint.config.mjs", "eslint.config.js",
    ".eslintrc.json", "ruff.toml", "deny.toml",
}
FLAG_CLASSES = {"gate_script": "modification", "lint_config": "lint-config",
                "contract": "contract"}
ROUTINE_CLASSES = ("ratchet_baseline_script", "ratchet_data",
                   "routine_count")


def judge_class(path: str) -> str | None:
    """Phase 0's classifier, verbatim semantics."""
    p = path
    b = os.path.basename(p)
    if p.startswith(".github/workflows/"):
        return "ci_workflow"
    if p == "CONTRACT.md":
        return "contract"
    if b in BASELINE_SCRIPTS:
        return "ratchet_baseline_script"
    if re.match(r"check_[^/]*\.(py|mjs|js|ts)$", b) or re.search(
            r"(^|[-_])gate([-_.]|s\.(py|mjs|js|ts)$)", b):
        return "gate_script"
    if "ratchet" in p:
        return "ratchet_data"
    if re.search(r"(test_inventory\.json|test_inventory_baseline\.json)", p):
        return "routine_count"
    if b in LINT_BASENAMES or p.endswith(".config/nextest.toml"):
        return "lint_config"
    if re.search(r"(^|/)(tests?|spec|__tests__)/", p) or re.search(
            r"(test_[^/]*\.py|[^/]*_test\.(py|rs|go)|[^/]*\.test\.(ts|tsx|js)|"
            r"[^/]*\.spec\.(ts|tsx|js)|[^/]*_test\.gd|test_[^/]*\.gd)$", p):
        return "test_file"
    return None


def parse_changed_files(text: str) -> list[tuple[str, str]]:
    """git name-status lines (or 'STATUS path' / 'STATUS:path' /
    bare paths, which default to status M) -> [(status, path)]."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[A-Z]\d*", parts[0]):
            status, path = parts[0][0], parts[1].strip()
            if status == "R":  # a rename modifies a pre-existing file
                status = "M"
        elif re.match(r"^[A-Z]:", line):
            status, path = line[0], line[2:].strip()
        else:
            status, path = "M", line
        # renames: take the destination path
        if " -> " in path:
            path = path.split(" -> ")[-1]
        out.append((status, path))
    return out


def load_list_at_base(repo_dir: str, base_sha: str,
                      list_path: str) -> dict | None:
    """Read the repo's judging-list file AT THE BASE SHA via git show.

    Returns {"extra": [...], "exclude": [...]} or None when the file
    does not exist at that commit. The working tree is never read:
    a PR that edits the list cannot shrink its own judging set.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "show", f"{base_sha}:{list_path}"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {"extra": [str(x) for x in data.get("extra", [])],
            "exclude": [str(x) for x in data.get("exclude", [])]}


def classify_pr(changed: list[tuple[str, str]],
                extra: set[str] | None = None,
                exclude: set[str] | None = None) -> dict:
    extra = extra or set()
    exclude = exclude or set()
    per_file = []
    flags = []
    counts: dict[str, int] = {}
    for status, path in changed:
        if path in exclude:
            cls = None
        elif path in extra:
            cls = "gate_script"
        else:
            cls = judge_class(path)
        if cls is None:
            continue
        counts[cls] = counts.get(cls, 0) + 1
        disposition = "counted"
        if cls in FLAG_CLASSES:
            if status == "A":
                disposition = "creation"
                counts["creation"] = counts.get("creation", 0) + 1
            elif status in ("M", "D"):
                disposition = "flag"
                flags.append({
                    "path": path,
                    "status": status,
                    "judge_class": cls,
                    "flag_class": FLAG_CLASSES[cls],
                    "reason": f"{path} ({FLAG_CLASSES[cls]}): pre-existing "
                              f"{cls.replace('_', ' ')} modified by this PR",
                })
        elif cls in ROUTINE_CLASSES:
            disposition = "routine"
        per_file.append({"path": path, "status": status,
                         "judge_class": cls, "disposition": disposition})
    reason = ""
    if flags:
        named = "; ".join(f["reason"] for f in flags)
        reason = (f"judging-file flag: {len(flags)} pre-existing judging "
                  f"file(s) modified — {named}")
    return {"flagged": bool(flags), "reason": reason, "flags": flags,
            "counts": counts, "files": per_file}


def enabled_in_config() -> bool:
    """shepherd.judging_files.enabled in the deployment config."""
    cfg = _load_deployment_config() or {}
    shepherd = cfg.get("shepherd") or {}
    section = shepherd.get("judging_files") or {}
    return section.get("enabled") is True


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--changed-files-file", default=None,
                    help="file holding git name-status output")
    ap.add_argument("--changed-files", default=None,
                    help="changed-file list inline: 'M a.py,A b.py' "
                         "(comma separated; bare paths default to M)")
    ap.add_argument("--repo-dir", default=None,
                    help="target repo checkout (for --base-sha list reads)")
    ap.add_argument("--base-sha", default=None,
                    help="the PR's base commit; the judging-list file is "
                         "read at this SHA, never from the working tree")
    ap.add_argument("--list-path", default="judging_files.json",
                    help="repo-relative path of the judging-list file")
    ap.add_argument("--force", action="store_true",
                    help="run even when the deployment config has the "
                         "check disabled")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.force and not enabled_in_config():
        print("judging_files: disabled in the deployment config "
              "(shepherd.judging_files.enabled is not true) — no check run",
              file=sys.stderr)
        return 2

    text = ""
    if args.changed_files_file:
        text += _read(args.changed_files_file)
    if args.changed_files:
        text += "\n" + args.changed_files.replace(",", "\n")
    if not text.strip():
        print("judging_files: no changed files (use --changed-files-file "
              "or --changed-files)", file=sys.stderr)
        return 2
    changed = parse_changed_files(text)

    extra: set[str] = set()
    exclude: set[str] = set()
    if args.repo_dir and args.base_sha:
        lst = load_list_at_base(args.repo_dir, args.base_sha,
                                args.list_path)
        if lst:
            extra = set(lst["extra"])
            exclude = set(lst["exclude"])

    result = classify_pr(changed, extra=extra, exclude=exclude)
    result["list_source"] = (
        f"base:{args.base_sha}" if (args.repo_dir and args.base_sha)
        else "patterns-only")
    if args.json:
        print(json.dumps(result, indent=1))
    elif result["flagged"]:
        print(result["reason"])
        for f in result["flags"]:
            print(f"  evidence: {f['status']} {f['path']} "
                  f"[{f['judge_class']} -> {f['flag_class']}]")
    else:
        summary = ", ".join(f"{k}={v}" for k, v in
                            sorted(result["counts"].items())) or "none"
        print(f"judging_files: no flag (judging touches: {summary})")
    return 1 if result["flagged"] else 0


if __name__ == "__main__":
    sys.exit(main())
