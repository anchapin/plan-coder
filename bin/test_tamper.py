#!/usr/bin/env python3
"""Test-tamper flag check for worker PRs (plan-coder issue #15).

A worker PR that weakens the repo's own tests can look green: the
assertion is deleted, the tolerance is loosened, the failing case is
marked skip/xfail. The shepherd's merge policy says a PR that was
flagged is not merge-eligible, but nothing produced the flag. This
script makes the check mechanical.

The check is DIFF-ONLY and reads one PR's diff:

  changed-file list   the PR's changed files with their status
                      (A = added, M = modified, D = deleted) so a
                      brand-new test file is never mistaken for an
                      existing one.
  diff text           the PR's unified diff (`gh pr diff <n>`).

It flags an EXISTING test file that was modified or deleted without
brief authorization, when the diff shows assertion weakening:
removed assertions, a loosened tolerance, a relaxed comparison, a
changed expected value, or an added skip/xfail marker. New test files
alone are never a flag, and a brief that authorized test changes is
never a flag.

The script FLAGS ONLY. It never merges, never blocks by force, never
edits a PR, and never touches the branch. The shepherd prompt decides
what to do with a flag (leave it for the operator).

Rollout (like the goldfish/jittest spikes): the check ships DISABLED
and is config-gated. `shepherd.test_tamper.enabled` in the deployment
config must be true for the check to run; otherwise the script exits 2
with a "disabled" message, exactly as jittest.py does. `--force` runs
the analysis anyway (operator one-off use). Enabling is a separate
operator decision.

Usage:
    python3 bin/test_tamper.py --diff-file PR.diff
                              [--changed-files-file PR.files]
                              [--changed-files "M tests/a.py,A tests/b.py"]
                              [--authorized] [--authorized-files tests/a.py]
                              [--brief-file BRIEF.md] [--force] [--json]

Exit 0 = no tampering flagged; 1 = flagged (one-line reason + evidence);
2 = check disabled in the deployment config / usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config

# ---------------------------------------------------------------- paths

TEST_DIR_MARKERS = ("/tests/", "/test/", "/spec/", "/specs/")
TEST_FILE_SUFFIXES = ("_test.py", "_test.go", "_test.js", "_test.ts",
                      "_test.rb", ".test.js", ".test.ts", ".spec.js",
                      ".spec.ts")
TEST_FILE_PREFIXES = ("test_", "test.")


def is_test_path(path: str) -> bool:
    """True when the path looks like a test file (not a doc, not src)."""
    low = (path or "").lower().replace("\\", "/")
    if not low:
        return False
    base = low.rsplit("/", 1)[-1]
    # Changed-file lists are repo-relative ("tests/conftest.py"), so pad a
    # leading slash before the directory-marker check: without it, a path
    # that *starts* with a test directory never contains "/tests/" and
    # non-test_-prefixed files under it (conftest, fixtures, helpers)
    # evade the flag.
    padded = low if low.startswith("/") else "/" + low
    if any(marker in padded for marker in TEST_DIR_MARKERS):
        return True
    if base.startswith(TEST_FILE_PREFIXES):
        return True
    if base.endswith(TEST_FILE_SUFFIXES):
        return True
    if ".test." in base or ".spec." in base:
        return True
    return False


# ---------------------------------------------------------------- diff

NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
OP_RE = re.compile(r"(==|!=|<=|>=|<|>)")

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class FileChange:
    """One file in a PR diff: its status and its +/- lines."""

    def __init__(self, path: str, status: str = "M"):
        self.path = path
        self.status = status.upper()[:1] or "M"
        self.removed: list[tuple[int, str]] = []
        self.added: list[tuple[int, str]] = []


def _strip_prefix(path: str) -> str:
    path = path.strip()
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_diff(text: str) -> list[FileChange]:
    """Parse a unified diff into per-file changes with line numbers."""
    files: list[FileChange] = []
    current: FileChange | None = None
    old_line = new_line = 0
    for raw in (text or "").splitlines():
        if raw.startswith("diff --git "):
            rest = raw[len("diff --git "):].strip()
            parts = rest.split(" ")
            path = _strip_prefix(parts[-1]) if parts else ""
            current = FileChange(path, "M")
            files.append(current)
            continue
        if raw.startswith("new file mode"):
            if current:
                current.status = "A"
            continue
        if raw.startswith("deleted file mode"):
            if current:
                current.status = "D"
            continue
        if raw.startswith(("rename ", "similarity ")):
            continue
        if raw.startswith("--- "):
            path = _strip_prefix(raw[4:].strip())
            if path == "/dev/null":
                if current:
                    current.status = "A"
                continue
            if current is None:
                current = FileChange(path, "M")
                files.append(current)
            else:
                current.path = path
            continue
        if raw.startswith("+++ "):
            path = _strip_prefix(raw[4:].strip())
            if path == "/dev/null":
                if current:
                    current.status = "D"
                continue
            if current is not None and not current.path:
                current.path = path
            continue
        hunk = HUNK_RE.match(raw)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(3))
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            current.added.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            current.removed.append((old_line, raw[1:]))
            old_line += 1
    return files


def parse_changed_files(text: str) -> list[tuple[str, str]]:
    """Parse a changed-file list into (status, path) pairs.

    Accepts `git diff --name-status` output ("M\\tpath"), "M path",
    and a bare path (status M).
    """
    out: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].upper() in ("A", "M", "D",
                                                    "R", "C", "T"):
            out.append((parts[0].upper(), parts[1].strip()))
        else:
            out.append(("M", line))
    return out


# ---------------------------------------------------------------- signals

ASSERT_RE = re.compile(r"\bassert\b|assert[A-Z]\w*\(|\.assert[A-Z]\w*\(")
STRICT_EQ_RE = re.compile(r"assertEqual\(|assert\s+.*==|assertIs\(|assert "
                          r".*\bis\b")
# Assertion forms that accept more values than a strict equality.
LOOSE_ASSERT_RE = re.compile(r"assertTrue\(|assertFalse\(|assertAlmostEqual\(")
SKIP_RE = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\(|pytest\.xfail\(|"
    r"unittest\.skip|unittest\.SkipTest|self\.skipTest|"
    r"@skip\b|describe\.skip|it\.skip|\bxit\(|\bxtest\(")

# (pattern, direction): "up" = a bigger number is a looser tolerance,
# "down" = a smaller number is a looser tolerance.
TOLERANCE_PATTERNS = (
    (re.compile(r"approx\([^)]*rel\s*=\s*([0-9.eE+-]+)"), "up"),
    (re.compile(r"approx\([^)]*abs\s*=\s*([0-9.eE+-]+)"), "up"),
    (re.compile(r"\bplaces\s*=\s*(\d+)"), "down"),
    (re.compile(r"\bdelta\s*=\s*([0-9.eE+-]+)"), "up"),
    (re.compile(r"\b(?:atol|rtol|tol|tolerance)\s*=\s*([0-9.eE+-]+)"),
     "up"),
    (re.compile(r"abs\([^)]*\)\s*<=?\s*([0-9.eE+-]+)"), "up"),
    (re.compile(r"round\([^)]*,\s*(\d+)\)"), "down"),
)

# operator in the removed line -> operator in the added line that
# accepts strictly more values.
RELAXED_OPS = {
    ("==", ">="), ("==", "<="),
    (">", ">="), ("<", "<="),
}


def skeleton(text: str) -> str:
    """Line shape with numbers and comparison operators normalized."""
    return OP_RE.sub("OP", NUM_RE.sub("#", text.strip()))


TOL_KWARG_RE = re.compile(
    r"\b(rel|abs|places|delta|atol|rtol|tol|tolerance)\s*=\s*"
    r"[0-9.eE+-]+")


def tol_skeleton(text: str) -> str:
    """Line shape with tolerance arguments removed.

    `assertAlmostEqual(x, 5)` and `assertAlmostEqual(x, 5, places=1)`
    share a shape this way, so a tolerance that appears, disappears or
    changes is comparable across the two sides of a change.
    """
    stripped = TOL_KWARG_RE.sub("TOL", text)
    return OP_RE.sub("OP", NUM_RE.sub("#", stripped.strip()))


def tolerances(text: str) -> list[tuple[str, float, str]]:
    """[(kind, value, direction)] for every tolerance in the line."""
    out: list[tuple[str, float, str]] = []
    for pattern, direction in TOLERANCE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            out.append((pattern.pattern, value, direction))
    return out


def _looser(old: tuple[str, float, str],
            new: tuple[str, float, str]) -> bool:
    if old[0] != new[0]:
        return False
    if old[2] == "up":
        return new[1] > old[1]
    return new[1] < old[1]


def _pair_by_shape(removed, added, shape):
    """Pair removed and added lines with the same shape, in order."""
    buckets: dict[str, list] = {}
    for item in added:
        buckets.setdefault(shape(item[1]), []).append(item)
    pairs = []
    for rem in removed:
        bucket = buckets.get(shape(rem[1]))
        if bucket:
            pairs.append((rem, bucket.pop(0)))
    return pairs


def _assert_lines(lines):
    return [item for item in lines if ASSERT_RE.search(item[1])]


def _bare_numbers_changed(old: str, new: str) -> bool:
    """True when a number outside a tolerance argument changed.

    A tolerance change is reported as a loosened tolerance; it must
    not be reported twice as a changed expected value.
    """
    stripped_old = TOL_KWARG_RE.sub("TOL", old)
    stripped_new = TOL_KWARG_RE.sub("TOL", new)
    return NUM_RE.findall(stripped_old) != NUM_RE.findall(stripped_new)


def analyze_file(change: FileChange) -> list[dict]:
    """Findings for one changed file (empty when nothing was weakened)."""
    findings: list[dict] = []
    removed = change.removed
    added = change.added

    removed_asserts = _assert_lines(removed)
    added_asserts = _assert_lines(added)

    # An assertion is deleted when no added assertion has the same
    # shape: the worker removed the check without replacing it.
    shape_pairs = _pair_by_shape(removed_asserts, added_asserts, skeleton)
    paired = {id(rem) for rem, _ in shape_pairs}
    deleted = [r for r in removed_asserts if id(r) not in paired]
    if deleted:
        findings.append({
            "kind": "removed_assertion",
            "file": change.path,
            "count": len(deleted),
            "evidence": [f"{change.path}:{r[0]}: removed assertion: "
                         f"`{r[1].strip()}`" for r in deleted[:3]],
        })

    # Tolerances pair on the shape with the tolerance argument
    # removed, so `places=7` -> `places=3` compares like for like.
    tol_pairs = _pair_by_shape(removed_asserts, added_asserts,
                               tol_skeleton)
    for rem, add in tol_pairs:
        old_tols = tolerances(rem[1])
        new_tols = tolerances(add[1])
        for old in old_tols:
            for new in new_tols:
                if _looser(old, new):
                    findings.append({
                        "kind": "loosened_tolerance",
                        "file": change.path,
                        "count": 1,
                        "evidence": [(f"{change.path}:{add[0]}: loosened "
                                     f"tolerance: `{rem[1].strip()}` -> "
                                     f"`{add[1].strip()}`")],
                    })
                    break
            else:
                continue
            break

    for rem, add in shape_pairs:
        old_ops = OP_RE.findall(rem[1])
        new_ops = OP_RE.findall(add[1])
        if len(old_ops) == 1 and len(new_ops) == 1:
            if (old_ops[0], new_ops[0]) in RELAXED_OPS:
                findings.append({
                    "kind": "relaxed_comparison",
                    "file": change.path,
                    "count": 1,
                    "evidence": [(f"{change.path}:{add[0]}: relaxed "
                                 f"comparison: `{rem[1].strip()}` -> "
                                 f"`{add[1].strip()}`")],
                })

    for rem, add in shape_pairs:
        if (STRICT_EQ_RE.search(rem[1]) and STRICT_EQ_RE.search(add[1])
                and NUM_RE.search(rem[1]) and NUM_RE.search(add[1])
                and rem[1].strip() != add[1].strip()
                and _bare_numbers_changed(rem[1], add[1])):
            findings.append({
                "kind": "changed_expected_value",
                "file": change.path,
                "count": 1,
                "evidence": [(f"{change.path}:{add[0]}: changed expected "
                             f"value: `{rem[1].strip()}` -> "
                             f"`{add[1].strip()}`")],
            })

    # Form: a strict equality replaced by a looser assertion form.
    removed_strict = [r for r in removed_asserts
                      if STRICT_EQ_RE.search(r[1])
                      and not LOOSE_ASSERT_RE.search(r[1])]
    added_strict = [a for a in added_asserts if STRICT_EQ_RE.search(a[1])]
    added_loose = [a for a in added_asserts
                   if LOOSE_ASSERT_RE.search(a[1])
                   and not STRICT_EQ_RE.search(a[1])]
    if len(removed_strict) > len(added_strict) and added_loose:
        findings.append({
            "kind": "weakened_assertion_form",
            "file": change.path,
            "count": len(added_loose),
            "evidence": [f"{change.path}:{a[0]}: weakened assertion: "
                         f"`{removed_strict[0][1].strip()}` -> "
                         f"`{a[1].strip()}`" for a in added_loose[:3]],
        })

    for line_no, text in added:
        if SKIP_RE.search(text):
            findings.append({
                "kind": "added_skip",
                "file": change.path,
                "count": 1,
                "evidence": [(f"{change.path}:{line_no}: added skip/xfail: "
                             f"`{text.strip()}`")],
            })

    return findings


# ---------------------------------------------------------------- brief

AUTHORIZATION_PATTERNS = (
    re.compile(r"(?i)authoriz(?:e|ed|es|ing|ation)\b[^.\n]{0,80}\btests?\b"),
    re.compile(r"(?i)\b(?:may|can|allowed to|permitted to)\b[^.\n]{0,40}"
               r"\b(?:modif|chang|updat|edit|rewrit)\w*[^.\n]{0,30}\btests?\b"),
    re.compile(r"(?i)\btests?\b[^.\n]{0,40}\b(?:may|can|are allowed to)\b"
               r"[^.\n]{0,30}\b(?:modif|chang|updat|edit|rewrit)\w*"),
    re.compile(r"(?i)\b(?:update|change|modify|edit) (?:the )?existing "
               r"tests?\b"),
)


def brief_authorizes_tests(text: str) -> bool:
    """True when the brief text explicitly authorizes test changes."""
    return any(p.search(text or "") for p in AUTHORIZATION_PATTERNS)


# ---------------------------------------------------------------- check

def check(diff_text: str,
          changed_files: list[tuple[str, str]] | None = None,
          authorized: bool = False,
          authorized_files: list[str] | None = None,
          brief_text: str = "") -> dict:
    """Analyze one PR diff. Returns the flag result (never acts)."""
    changes = parse_diff(diff_text)
    status_by_path: dict[str, str] = {}
    if changed_files:
        for status, path in changed_files:
            status_by_path[_strip_prefix(path)] = status

    findings: list[dict] = []
    inspected: list[str] = []
    for change in changes:
        path = change.path
        status = status_by_path.get(path, change.status)
        if status in ("R", "C", "T"):
            continue
        if not is_test_path(path):
            continue
        if status == "A":
            # A brand-new test file is not tampering.
            continue
        if authorized or path in (authorized_files or []):
            continue
        if path not in status_by_path and changed_files:
            # Not part of this PR's changed-file list.
            continue
        inspected.append(path)
        if status == "D":
            findings.append({
                "kind": "deleted_test_file",
                "file": path,
                "count": 1,
                "evidence": [f"{path}: existing test file deleted"],
            })
            continue
        # Any unauthorized change to an existing test file is reported;
        # the weakening findings add the evidence for why.
        findings.append({
            "kind": "modified_test_file",
            "file": path,
            "count": 1,
            "evidence": [(f"{path}: existing test file modified without "
                         f"brief authorization")],
        })
        findings.extend(analyze_file(change))

    if authorized or brief_authorizes_tests(brief_text):
        return {
            "flag": False,
            "authorized": True,
            "reason": "brief authorized test changes — no flag",
            "evidence": [],
            "findings": [],
            "inspected_test_files": inspected,
        }

    flagged = bool(findings)
    reason = ""
    if flagged:
        kinds = sorted({f["kind"] for f in findings})
        files = sorted({f["file"] for f in findings})
        weakening = [k for k in kinds if k != "modified_test_file"]
        if weakening:
            reason = (f"test tampering suspected in {len(files)} existing "
                      f"test file(s) ({', '.join(files)}): "
                      f"{', '.join(weakening)}")
        else:
            reason = (f"existing test file(s) modified without brief "
                      f"authorization ({', '.join(files)})")
    return {
        "flag": flagged,
        "authorized": False,
        "reason": reason,
        "evidence": [e for f in findings for e in f["evidence"]],
        "findings": findings,
        "inspected_test_files": inspected,
    }


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def enabled_in_config() -> bool:
    """shepherd.test_tamper.enabled in the deployment config."""
    cfg = _load_deployment_config() or {}
    shepherd = cfg.get("shepherd") or {}
    section = shepherd.get("test_tamper") or {}
    return section.get("enabled") is True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diff-file", default=None,
                    help="file holding the PR's unified diff "
                         "(default: stdin)")
    ap.add_argument("--changed-files-file", default=None,
                    help="file holding the PR's changed-file list "
                         "(git diff --name-status output)")
    ap.add_argument("--changed-files", default=None,
                    help="changed-file list inline: 'M tests/a.py,"
                         "A tests/b.py' (comma separated)")
    ap.add_argument("--authorized", action="store_true",
                    help="the brief authorized test changes")
    ap.add_argument("--authorized-files", default=None,
                    help="comma separated test files the brief authorized")
    ap.add_argument("--brief-file", default=None,
                    help="the brief text; an explicit authorization of test "
                         "changes in it suppresses the flag")
    ap.add_argument("--force", action="store_true",
                    help="run even when the deployment config has the "
                         "check disabled")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.force and not enabled_in_config():
        print("test_tamper: disabled in the deployment config "
              "(shepherd.test_tamper.enabled is not true) — no check run",
              file=sys.stderr)
        return 2

    diff_text = _read(args.diff_file)
    if not diff_text and args.diff_file is None:
        diff_text = sys.stdin.read()
    if not diff_text.strip():
        print("test_tamper: no diff text (use --diff-file or stdin)",
              file=sys.stderr)
        return 2

    changed_files: list[tuple[str, str]] = []
    if args.changed_files_file:
        changed_files.extend(parse_changed_files(
            _read(args.changed_files_file)))
    if args.changed_files:
        changed_files.extend(parse_changed_files(
            args.changed_files.replace(",", "\n")))

    authorized_files = [f.strip() for f in (args.authorized_files or "").split(",")
                        if f.strip()]

    result = check(diff_text,
                    changed_files=changed_files or None,
                    authorized=args.authorized,
                    authorized_files=authorized_files,
                    brief_text=_read(args.brief_file))

    if args.json:
        print(json.dumps(result, indent=1))
    elif result["flag"]:
        print(f"FLAG: {result['reason']}")
        for line in result["evidence"]:
            print(f"  {line}")
    else:
        print(f"OK: no test tampering flagged "
              f"({len(result['inspected_test_files'])} existing test file(s) "
              f"inspected)")
    return 1 if result["flag"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
