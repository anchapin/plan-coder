#!/usr/bin/env python3
"""Token-plan coding worker.

Runs a tool-calling agent loop against cheap token-plan models
(z.ai GLM Coding Plan; MiniMax), confined to one workdir.
Reads a markdown brief, works the task, writes WORKER_REPORT.md.
Never commits, merges, or pushes — the manager agent does that.

Auth: two modes, tried in order per provider. (1) The provider's API
key from the environment (ZAI_API_KEY / MINIMAX_API_KEY). (2) A
credential-helper module named `dynamic_credentials` — a platform shim
that injects a vault-held credential into the request without this
process ever seeing key material — found via $PLAN_CODER_CRED_DIR or
~/.plan-coder/cred/ when present. Keys are never printed, logged, or
written to the transcript or report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from codemap import (
    CODE_QUERY_TOOL,
    CodeMap,
    build_code_map,
    code_map_enabled,
)
from plans import (
    DeploymentConfigError,
    Plan,
    PlanLockError,
    Route,
    RouteResolutionError,
    plans_from_config,
    resolve_dispatch as _resolve_dispatch,
    validate_plans,
)

PROVIDERS = {
    "zai": {
        "chat_url": "https://api.z.ai/api/coding/paas/v4/chat/completions",
        "env_key": "ZAI_API_KEY",
        "credential": "custom.zai",
        "hosts": ("api.z.ai",),
        "default_model": "glm-5.3-flash",
        "tiers": {
            "cheap": "glm-5.3-flashx",
            "balanced": "glm-5.3-flash",
            "flagship": "glm-5.3",
        },
    },
    "minimax": {
        "chat_url": "https://api.minimax.io/v1/chat/completions",
        "env_key": "MINIMAX_API_KEY",
        "credential": "custom.minimax",
        "hosts": ("api.minimax.io",),
        "default_model": "MiniMax-M2.7",
        "tiers": {
            "cheap": "MiniMax-M2.5-highspeed",
            "balanced": "MiniMax-M2.7",
            "flagship": "MiniMax-M3",
        },
    },
}

# --- deployment configuration (optional) ---------------------------------
# A deployment config customizes this worker for one installation without
# code changes: provider credential-helper labels, the credential-helper
# directory, and the default transcript directory. Search order:
# $PLAN_CODER_CONFIG, then config/deployment.local.json next to this
# repo's root, then ~/.plan-coder/deployment.json. The committed template
# is config/deployment.example.json; the filled local file is gitignored.
# Missing or malformed config is never fatal — built-in defaults apply.

def _load_deployment_config() -> dict:
    candidates = []
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    candidates.append(os.path.join(
        repo_root, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001 - config is a convenience, not a gate
            continue
        if isinstance(data, dict):
            # A syntactically valid config with malformed plan data is an
            # operator error, not a missing optional setting.  Validate it
            # outside the parse guard so DeploymentConfigError is not
            # silently swallowed (or replaced by another config candidate).
            validate_plans(data)
            return data
    return {}


DEPLOYMENT = _load_deployment_config()


def _cfg(*keys, default=None):
    node = DEPLOYMENT
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


for _pname, _prov in PROVIDERS.items():
    _cred = _cfg("providers", _pname, "credential")
    if _cred:
        _prov["credential"] = _cred
    _env_key = _cfg("providers", _pname, "env_key")
    if _env_key:
        _prov["env_key"] = _env_key


# Plans are the capacity unit.  With no explicit plans section, every
# legacy provider is represented as one API-only plan, preserving the old
# provider order, model tiers, and failover behavior exactly.
_plans = plans_from_config(DEPLOYMENT, PROVIDERS)
_plan_order = _cfg("workers", "plan_order")
if not _plan_order:
    if "plans" in DEPLOYMENT:
        # Explicit capacity plans keep their declaration order.
        _plan_order = [plan.id for plan in _plans]
    else:
        # Legacy API plans retain the configured provider order.
        _plan_order = (_cfg("workers", "provider_order")
                       or list(PROVIDERS))
if not isinstance(_plan_order, list):
    _plan_order = ([plan.id for plan in _plans]
                   if "plans" in DEPLOYMENT else list(PROVIDERS))
_plan_by_id = {plan.id: plan for plan in _plans}
PLANS = (
    tuple(plan for plan_id in _plan_order
          for plan in (_plan_by_id.get(plan_id),) if plan is not None)
    + tuple(plan for plan in _plans if plan.id not in _plan_order)
)
PLAN_BY_ID = {plan.id: plan for plan in PLANS}


def resolve_dispatch(plan_or_id: Plan | str,
                     requested_route: Route | dict | str,
                     tier: str = "balanced",
                     model: str | None = None):
    """Resolve a dispatch by plan id (or normalized plan) with lock checks."""
    if isinstance(plan_or_id, str):
        plan = PLAN_BY_ID.get(plan_or_id)
        if plan is None:
            raise RouteResolutionError(
                f"unknown plan {plan_or_id!r}; configured plans: "
                f"{', '.join(PLAN_BY_ID) or '(none)'}")
    else:
        plan = plan_or_id
    return _resolve_dispatch(plan, requested_route, tier=tier, model=model)


def resolve_plan_dispatch(plan_id: str,
                          requested_route: Route | dict | str,
                          tier: str = "balanced",
                          model: str | None = None):
    """Resolve a plan dispatch and fail closed on a harness-lock mismatch."""
    return resolve_dispatch(plan_id, requested_route, tier=tier, model=model)


SYSTEM_PROMPT = """You are a coding worker operating inside a single project
worktree directory. A manager gave you a task brief (the user message).
Work the task with the available tools.

Rules:
- Stay inside the work directory. Never read or write outside it — that
  includes shell commands: no `find /`, no listing home or workspace
  roots, no absolute paths outside the workdir. If you cannot locate
  the workdir, say so in your summary instead of searching the
  filesystem for it.
- Explore before changing: read relevant files first.
- Make your first scoped edit early. Exploration, benchmarking, or
  planning that has produced no change by the halfway point of your
  step budget almost never finishes — runs that reach the step cap
  still exploring end with an empty diff. If the task genuinely needs
  no change, run the acceptance/repro commands and quote their output
  instead of continuing to explore.
- Make minimal, focused changes that satisfy the brief's acceptance criteria.
- Run the test commands from the brief (or the repo's obvious test command)
  before finishing. Fix failures you introduced.
- Do NOT commit, merge, or push. Leave changes uncommitted.
- A mechanical command guard may deny a shell command that touches
  secrets, mutates GitHub state, or deletes outside the work area
  (denials start with `exec_denied:` and name the allowed alternative).
  Follow the alternative; do not rephrase the same command to evade it.
- Do NOT ask the user questions; make reasonable choices and note them.
- When done (or stuck), write a final summary: what changed, test results,
  and anything you could not resolve.
- Tool output can be capped. When a tool result carries a "[ctx]" summary and
  a handle, the full output was stored on disk and part of it was dropped from
  your context on purpose. Before you rely on a capped result, call ctx_read
  with that handle (add start_line/end_line, or a needle, to narrow it) to
  retrieve the missing part. Never assume dropped content is gone, and never
  report a fact you only saw in the dropped part without retrieving it first.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Run a shell command in the work directory. Returns stdout, stderr, exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file relative to the work directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 200},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file relative to the work directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file relative to the work directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List a directory relative to the work directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                },
            },
        },
    },
]

# --- Context Mode: stored tool output + ctx_read retrieval ---------------
# Issue #52, built from docs/spikes/headroom-phase1.md (Probe C -- Context
# Mode pattern: PASS with caveats).  The pattern: raw tool output is written
# to a per-run store on disk, and the context receives a capped summary plus
# a handle; `ctx_read` puts the raw output back into the context on demand.
# The measured basis: capping stored output at 6 lines / 400 chars with the
# full output recoverable removed 26.36% of a replayed tool-heavy context,
# and the dropped information stayed recoverable for ~0.3% of that context.
#
# OFF by default: the live A/B (stored-output on vs off) is the acceptance
# gate for this build and has not run yet, so the harness keeps its current
# behaviour until the supervisor validates it.  Enable with --stored-output
# or WORKER_STORED_OUTPUT=1.

CTX_HANDLE_PREFIX = "ctx:"
# The cap the Phase 1 report measured (6 lines / 400 chars); it was a
# plausible default there, not an optimised one, so both are overridable.
CTX_SUMMARY_MAX_LINES = int(os.environ.get("WORKER_CTX_MAX_LINES", "6"))
CTX_SUMMARY_MAX_CHARS = int(os.environ.get("WORKER_CTX_MAX_CHARS", "400"))
# How much a single ctx_read may put back into the context at once (the
# old exec cap, so one retrieval costs no more than the output it replaces).
CTX_READ_MAX_CHARS = int(os.environ.get("WORKER_CTX_READ_MAX_CHARS", "8000"))
# The store keeps at most this much of one tool output on disk; beyond it
# the stored copy is itself capped, and the summary says so.
CTX_STORE_MAX_CHARS = int(os.environ.get("WORKER_CTX_STORE_MAX_CHARS",
                                          "1000000"))

CTX_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "ctx_read",
        "description": (
            "Retrieve stored tool output by handle. Use it whenever a tool "
            "result carries a [ctx] summary: the full output was stored on "
            "disk and part of it was dropped from the context. Narrow the "
            "result with a line range or a needle when the output is large."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string",
                           "description": "handle from the [ctx] summary, "
                                          "e.g. ctx:ab12cd34ef56"},
                "start_line": {"type": "integer",
                               "description": "first line to return "
                                              "(1-based, default 1)"},
                "end_line": {"type": "integer",
                             "description": "last line to return "
                                            "(default: last line)"},
                "needle": {"type": "string",
                           "description": "return only the lines that "
                                          "contain this text"},
            },
            "required": ["handle"],
        },
    },
}


# --- Native code map: the code_query tool ---------------------------------
# Issue #80, section 2: bin/codemap.py builds a native index of the
# worktree at run start, so a worker can ask where a symbol is defined or
# used instead of grepping the tree by hand.
#
# OFF by default: the gate is $WORKER_CODE_MAP=1, and the paired A/B is
# coordinator work for a later cycle.  The tool is advertised only when the
# gate is on AND the index was built; a failed or timed-out build leaves
# the run exactly as it was (no tool, no prompt paragraph) and is recorded
# in the transcript as a `codemap_build_failed` event.
CODE_MAP_PROMPT = """- A code map for this run is available: `code_query(mode="def"|"callers"|"symbols_in"|"refs", query="<name or file path>")` tells you where a symbol is defined, where its name occurs, what a file defines, or every reference to a name. The map is a snapshot of the worktree from the start of this run: it cannot see the edits you make now, so confirm anything it reports against the current file."""


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag ("1"/"true"/"yes"/"on" = on)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to an int; anything unusable falls back."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stored_output_enabled(cli_value: bool | None = None) -> bool:
    """Resolve the Context Mode switch.

    The CLI flag wins, then $WORKER_STORED_OUTPUT, then the default: off.
    Off is the default because the live A/B (stored-output on vs off) is
    the acceptance gate for this build and has not run yet -- the harness
    keeps its pre-#52 behaviour until the supervisor validates the mode.
    """
    if cli_value is None:
        return _env_flag("WORKER_STORED_OUTPUT", False)
    return bool(cli_value)


def _atomic_write_text(full: str, content: str) -> None:
    """Write a file atomically: temp file in the same directory, then
    os.replace. A failed or interrupted write never leaves the target
    truncated (a truncate-in-place open("w") destroyed files in the
    2026-10-26 write fault)."""
    directory = os.path.dirname(full) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".agent-write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, full)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ContextStore:
    """Per-run store of raw tool output (Context Mode).

    One file per stored output, content-addressed by the sha256 of the
    stored text: the handle is stable, the same output is stored once, and
    the file name can be recomputed from the handle alone.  The store lives
    outside the worktree (next to the run's transcript) so a run's scratch
    never looks like a worktree change to the empty-diff gate."""

    def __init__(self, directory: str, max_chars: int = CTX_STORE_MAX_CHARS):
        self.directory = directory
        self.max_chars = max_chars
        self.meta: dict[str, dict] = {}
        os.makedirs(directory, exist_ok=True)

    @staticmethod
    def handle_for(text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        return CTX_HANDLE_PREFIX + digest[:12]

    def _path(self, handle: str) -> str | None:
        name = handle.strip()
        if not name.startswith(CTX_HANDLE_PREFIX):
            return None
        stem = name.removeprefix(CTX_HANDLE_PREFIX)
        if not stem or any(c not in "0123456789abcdef" for c in stem):
            return None
        return os.path.join(self.directory, stem + ".txt")

    def put(self, text: str) -> str:
        """Store raw output; return its handle. The stored copy is capped
        at max_chars; how much was left out is recorded in meta[handle] so
        the summary can say so (nothing is dropped silently)."""
        handle = self.handle_for(text)
        stored = text
        dropped = 0
        if len(text) > self.max_chars:
            stored = text[:self.max_chars]
            dropped = len(text) - len(stored)
        path = self._path(handle)
        if path is None:  # pragma: no cover - handle_for always fits
            raise ValueError(f"invalid handle: {handle}")
        _atomic_write_text(path, stored)
        self.meta[handle] = {
            "total_chars": len(text),
            "stored_chars": len(stored),
            "dropped_chars": dropped,
        }
        return handle

    def get(self, handle: str) -> str | None:
        path = self._path(handle)
        if path is None:
            return None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None


def _ctx_store_dir(workdir: str, transcript_dir: str, run_id: str) -> str:
    """Where this run's Context Mode store lives.

    Default: a per-run directory next to the run's transcript, i.e.
    outside the worktree -- a store inside the worktree would look like a
    worker edit to the empty-diff gate.  $WORKER_CTX_STORE_DIR overrides
    the root.  A store that would land inside the workdir is refused and a
    temporary directory outside it is used instead."""
    root = os.path.expanduser(
        os.environ.get("WORKER_CTX_STORE_DIR")
        or os.path.join(transcript_dir, "ctx"))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id) or "run"
    directory = os.path.join(root, safe)
    real = os.path.realpath(directory)
    base = os.path.realpath(workdir)
    if real == base or real.startswith(base + os.sep):
        return tempfile.mkdtemp(prefix="pc-ctx-")
    return directory


def summarize_stored_output(text: str, handle: str,
                            max_lines: int = CTX_SUMMARY_MAX_LINES,
                            max_chars: int = CTX_SUMMARY_MAX_CHARS,
                            store_meta: dict | None = None) -> str:
    """Capped summary of a stored tool output.

    The summary says how big the output is, shows the capped head, states
    exactly how many lines and characters were dropped, and names the
    handle to retrieve the rest with.  Nothing is dropped silently.

    The counts are over the line-joined text: a trailing newline is not
    content, so it is not counted as kept or as dropped.
    """
    lines = text.splitlines()
    total_lines = len(lines)
    total_chars = len("\n".join(lines))
    body = "\n".join(lines[:max_lines])
    if len(body) > max_chars:
        body = body[:max_chars]
    kept_lines = len(body.splitlines())
    kept_chars = len(body)
    dropped_lines = total_lines - kept_lines
    dropped_chars = total_chars - kept_chars
    out = [(f"[ctx] stored output: {total_lines} lines, {total_chars} chars "
            f"(handle {handle})")]
    if body:
        out.append(body)
    if dropped_lines or dropped_chars:
        out.append(
            f"[ctx] TRUNCATED for context: kept {kept_lines} lines / "
            f"{kept_chars} chars, dropped {dropped_lines} lines / "
            f"{dropped_chars} chars -- retrieve the dropped part with "
            f'ctx_read(handle="{handle}", start_line={kept_lines + 1})')
    else:
        out.append(
            f'[ctx] complete: nothing dropped; the full output is also '
            f'readable with ctx_read(handle="{handle}")')
    meta = store_meta or {}
    if meta.get("dropped_chars"):
        out.append(
            f'[ctx] note: the stored copy itself was capped at '
            f'{meta.get("stored_chars")} chars, so {meta.get("dropped_chars")} '
            f'chars of this output were never stored and cannot be retrieved')
    return "\n".join(out)


# Empty-diff completion gate (adapted from Raven's completion_gates.py): a
# worker that tries to finish with no code changes gets a budgeted nudge
# demanding discriminating evidence. Every guardrail is env-gated and the
# default is stated explicitly here, so a new gate can never silently arm
# itself.
EMPTY_DIFF_GATE_ENABLED = os.environ.get("WORKER_EMPTY_DIFF_GATE", "1") == "1"
EMPTY_DIFF_GATE_MAX_ROUNDS = int(os.environ.get("WORKER_EMPTY_DIFF_GATE_ROUNDS", "2"))

EMPTY_DIFF_GATE_PROMPT = """COMPLETION GATE: you are trying to finish with no code changes in the worktree (no diff against the base commit, no new files). That is only acceptable if the task genuinely requires no change. Provide discriminating evidence: run the task's own acceptance or repro steps from the brief against the current unmodified code and quote the exact command and its output. Verifying with an unrelated or pre-existing check does not count. If the evidence shows the work is truly unnecessary, say so explicitly with the quoted evidence. Otherwise make the required change now."""

# Mid-run no-edit gate (harness-tune 2026-10-25): in the week reviewed,
# every failed:empty-diff run (13) burned all 60 steps exploring and
# never edited, while PR-opened runs made their first edit at a median
# step of ~21. One nudge at the halfway point, while half the budget
# remains, is the cheapest point to break that pattern. Env-gated like
# the empty-diff gate, fires at most once per run.
MIDRUN_GATE_ENABLED = os.environ.get("WORKER_MIDRUN_GATE", "1") == "1"

MIDRUN_GATE_PROMPT = """MID-RUN CHECKPOINT: half your step budget is used and the worktree still has no changes (no diff against the base commit, no new files). Do not spend the remaining steps purely exploring, benchmarking, or planning — runs that reach the step cap still exploring end with an empty diff and are discarded. Choose one now: (1) make the smallest scoped edit that satisfies one acceptance criterion and build from there; (2) if the task genuinely requires no change, run the brief's acceptance/repro commands now and quote the exact commands and output as evidence; or (3) if you are blocked, state exactly what blocks you and finish with that in your summary."""

# Per-run output-token guard (trace-grader spike, 2026-11-07): checked
# after each step like the step cap. Off by default (0): enable with
# --max-tokens-out, $WORKER_MAX_TOKENS_OUT, or workers.max_tokens_out
# in the deployment config. It caps completion tokens only — prompt
# tokens are dominated by cache reads (a typical run shows ~2.3M in
# vs ~24k out) and do not separate good runs from bad ones, so they
# are never capped. A tripped guard stops the run cleanly, marks the
# report "[stopped: token budget reached]", and is recorded as ledger
# outcome stopped:token-cap by the supervisor — it is a burn guard
# against pathological runs, not a quota limit.


def _git_out(workdir: str, *args: str) -> str | None:
    """Run a read-only git command in workdir; None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", workdir, *args],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _worktree_changed(workdir: str, base: str | None) -> bool | None:
    """True if the worktree differs from base (tracked or untracked files).
    None when git cannot tell (not a repo, git missing) — the gate skips."""
    if base is None:
        return None
    diff_names = _git_out(workdir, "diff", "--name-only", base)
    untracked = _git_out(workdir, "ls-files", "--others", "--exclude-standard")
    if diff_names is None or untracked is None:
        return None
    names = [n for n in diff_names.split() + untracked.split()
             if n != "WORKER_REPORT.md"]
    return len(names) > 0


_SECRET_SHAPE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})")


def _redact(text: str) -> str:
    """Hygiene for stored transcript lines (harness-tune 2026-10-25,
    ideas brief T4): mask secret-shaped strings and shorten the home
    directory prefix to ~. This is not a security boundary — workers
    are worktree-confined and keys never pass through this process —
    but tool output a worker catted can carry paths or token shapes
    into the stored logs. Fails toward masking more, never toward
    skipping the write: error classes, exit codes, and the first line
    of output all survive."""
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "~")
    return _SECRET_SHAPE.sub("***", text)


# ---------------------------------------------------------------------------
# Exec guard (exec-guard spike, Phase 1 built 2026-11-10; brief:
# your_files/exec-guard-spike-brief.md, Phase 0: exec-guard-phase0/).
# Every worker shell command passes through Worker._exec; historically it
# ran with shell=True, no denylist, and env=dict(os.environ) — in env-key
# auth mode that hands the provider keys to any shell the model spawns,
# and _safe() never applied because a command has no single path to check.
# Part A (env scrub): the shell subprocess gets a baseline environment,
# never the process environment; secret-shaped names and every provider's
# configured env_key are stripped. The strip set is DERIVED from PROVIDERS
# at call time so a future provider is covered automatically.
# Part B (scoped denylist): positive-match-only rules, re-scoped by the
# Phase 0 replay over 19,776 recorded commands (0 denies on pr_opened runs
# is the bar the rule set was fitted to). env-dump and broad filesystem
# scans are deliberately NOT rules: the corpus showed both are load-bearing
# worker behaviour, and Part A removes what an env dump could leak.
# Policy lives in deployment config workers.exec_guard; ships disabled.
# scrub_always runs Part A even while Part B is disabled. With both off,
# _exec behaves byte-identically to the pre-guard code.
_EXEC_GUARD_BASELINE = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
                        "TERM", "TMPDIR", "USER", "SHELL")
_EXEC_GUARD_STRIP = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL")
_EXEC_GUARD_HEREDOC = re.compile(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?")


def _exec_guard_cfg() -> dict:
    cfg = _cfg("workers", "exec_guard", default={})
    return cfg if isinstance(cfg, dict) else {}


def _exec_guard_enabled() -> bool:
    return bool(_exec_guard_cfg().get("enabled", False))


def _exec_guard_strip_names() -> set:
    names = {p["env_key"] for p in PROVIDERS.values() if p.get("env_key")}
    for name in _exec_guard_cfg().get("strip_extra", []) or []:
        names.add(str(name))
    return names


def exec_guard_env() -> dict:
    """Baseline + explicit pass-through environment for worker shells."""
    strip = _exec_guard_strip_names()
    env: dict = {}
    for name in _EXEC_GUARD_BASELINE:
        if name in os.environ:
            env[name] = os.environ[name]
    for name in _exec_guard_cfg().get("passthrough", []) or []:
        name = str(name)
        if (name in os.environ and name not in strip
                and not _EXEC_GUARD_STRIP.search(name)):
            env[name] = os.environ[name]
    # Worker shells are non-interactive (~/.profile never sourced): the
    # cargo and ~/.local/bin dirs would be missing from PATH. Phase 0
    # showed repeated `find / -name uv` hunts caused by the second one.
    path = env.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    for extra in (os.path.expanduser("~/.local/bin"),
                  os.path.expanduser("~/.cargo/bin")):
        if extra not in parts:
            parts.insert(0, extra)
    env["PATH"] = os.pathsep.join(parts)
    return env


def _exec_guard_segments(command: str):
    """Split a shell string into argv segments on top-level operators.
    Returns (segments, unparsed). Heredoc bodies are data, not segments.
    Unparseable commands are allowed by the caller (fail-open on parse,
    fail-closed on match) and counted via the result taxonomy."""
    lines = command.split("\n")
    kept, i = [], 0
    while i < len(lines):
        kept.append(lines[i])
        m = _EXEC_GUARD_HEREDOC.search(lines[i])
        if m:
            delim = m.group(1)
            i += 1
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            if i < len(lines):
                kept.append(lines[i])
        i += 1
    try:
        lex = shlex.shlex("\n".join(kept), posix=True,
                          punctuation_chars="|&;")
        lex.whitespace_split = True
        lex.commenters = ""
        toks = list(lex)
    except ValueError:
        return None, True
    segs, cur = [], []
    for tok in toks:
        if tok in ("|", "||", "&&", ";", "&"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs, False


def _exec_guard_resolve(target: str, cwd: str) -> str:
    if target.startswith("~"):
        target = os.path.expanduser("~") + target[1:]
    target = target.replace("$HOME", os.path.expanduser("~"))
    if not os.path.isabs(target):
        target = os.path.join(cwd, target)
    return os.path.realpath(target)


_EXEC_GUARD_READ_HEADS = {"cat", "head", "tail", "less", "more", "grep",
                          "egrep", "fgrep", "sed", "awk", "cp", "source", "."}
_EXEC_GUARD_GH_MUTATE = {("pr", "merge"), ("workflow", "run"),
                         ("workflow", "enable"), ("workflow", "disable"),
                         ("run", "rerun"), ("run", "cancel"),
                         ("release", "create"), ("release", "delete"),
                         ("secret", "set"), ("secret", "delete"),
                         ("repo", "delete")}
_EXEC_GUARD_SENSITIVE = ("token-plan-coding-manager-setup",
                         "plan-coder-repo", "/.ssh", "/.aws", "/.gnupg",
                         "custom-connectors")


def exec_guard_check(command: str, workdir: str):
    """Return the denied rule name, or None when the command is allowed.
    Positive-match only; an unparseable command returns None here and is
    surfaced by the caller through the exec_guard:unparsed taxonomy when
    it needs counting — the guard never kills a run over a parse edge."""
    segs, unparsed = _exec_guard_segments(command)
    if unparsed or not segs:
        return None
    home = os.path.expanduser("~")
    cwd = workdir

    def in_scope(path: str) -> bool:
        return (path.startswith("/tmp/") or path == "/tmp"
                or path.startswith("/var/tmp/")
                or path == workdir or path.startswith(workdir + os.sep))

    for argv in segs:
        if not argv:
            continue
        if argv[0] == "cd" and len(argv) >= 2:
            cwd = _exec_guard_resolve(argv[1], cwd)
            continue
        head = os.path.basename(argv[0])
        rest = argv[1:]
        if head == "sudo":
            return "sudo"
        if head == "rm":
            flags = "".join(a.lstrip("-") for a in rest
                            if a.startswith("-"))
            if "r" in flags and "f" in flags:
                caches = (home + "/.cache", home + "/.npm")
                for target in (a for a in rest if not a.startswith("-")):
                    if target.rstrip("/") in ("/tmp/*", "/*", "~/*",
                                               "/var/tmp/*"):
                        return "rm-outside-worktree"
                    rp = _exec_guard_resolve(target, cwd)
                    if any(rp == c or rp.startswith(c + os.sep)
                           for c in caches):
                        continue
                    if rp in ("/", home) or rp == workdir:
                        return "rm-outside-worktree"
                    if not in_scope(rp):
                        return "rm-outside-worktree"
        if head == "git" and rest[:1] == ["push"]:
            return "git-push"
        if head == "gh" and tuple(rest[:2]) in _EXEC_GUARD_GH_MUTATE:
            return "gh-mutate"
        if head in _EXEC_GUARD_READ_HEADS:
            for a in rest:
                base = os.path.basename(a)
                if base == ".env" or (base.startswith(".env.")
                                      and base != ".env.example"):
                    return "dotenv-read"
        if head == "find" and rest:
            roots = [x for x in rest if not x.startswith("-")]
            if roots:
                rp = _exec_guard_resolve(roots[0], cwd)
                if (any(s in rp for s in _EXEC_GUARD_SENSITIVE)
                        and not in_scope(rp)):
                    return "fs-scan"
    if re.search(r"(curl|wget)[^&;]*\|\s*(sudo\s+)?(ba|z)?sh\b", command):
        return "curl-pipe-sh"
    return None


_EXEC_GUARD_ALTERNATIVES = {
    "sudo": "run it without sudo — workers have no sudo by design",
    "rm-outside-worktree": "delete only inside the workdir, /tmp, "
                           "/var/tmp, or tool caches (~/.cache, ~/.npm)",
    "dotenv-read": "read .env.example instead — real .env files are secrets",
    "git-push": "workers never push; leave changes uncommitted for the "
                "supervisor",
    "gh-mutate": "read-only gh only; merges and mutations are the "
                 "shepherd's job",
    "curl-pipe-sh": "download to a file first, inspect it, then run it",
    "fs-scan": "search inside the workdir; harness state and credential "
               "paths are off limits",
}


def _tool_result_kind(result: str) -> str:
    """Typed tool-error taxonomy (harness-tune 2026-10-25, ideas brief
    T2): a small closed vocabulary recorded alongside the raw result
    string, never instead of it, so failure modes are countable
    without breaking grep-based transcript review."""
    if result.startswith("tool error: exec_denied:"):
        rule = result[len("tool error: exec_denied:"):].split(":", 1)[0].strip()
        return f"exec_denied:{rule}" if rule else "exec_denied"
    if result.startswith("TIMEOUT after"):
        return "exec_timeout"
    if result.startswith("exit="):
        code = result[5:].split("\n", 1)[0].strip()
        return "ok" if code == "0" else "exec_nonzero"
    if "edit failed: old_text not found" in result:
        return "edit_miss"
    if result.startswith("tool error:"):
        if "path escapes workdir" in result:
            return "path_escape"
        exc = result[len("tool error:"):].split(":", 1)[0].strip()
        return f"tool_error:{exc}" if exc else "tool_error"
    return "ok"


def _machine_manifest(workdir: str, base: str | None, gate_triggers: int,
                      midrun_triggers: int = 0,
                      token_cap: tuple | None = None,
                      stored_output: bool = False,
                      exec_denied: int = 0) -> str:
    """Harness-generated git facts for the supervisor to verify.
    Never model prose: unreadable facts degrade to status unknown with a
    named blocker instead of a fabricated 'ready'."""
    lines = ["## Machine manifest (harness-generated — verify, do not trust prose above)", ""]
    head = _git_out(workdir, "rev-parse", "HEAD")
    if base is None or head is None:
        blocker = "base commit not pinned" if base is None else "HEAD unreadable"
        lines.append(f"- status: unknown (blocker: {blocker})")
    else:
        tracked = (_git_out(workdir, "diff", "--name-only", base) or "").split()
        untracked = [u for u in
                     (_git_out(workdir, "ls-files", "--others", "--exclude-standard") or "").split()
                     if u != "WORKER_REPORT.md"]
        changed = [n for n in tracked + untracked if n != "WORKER_REPORT.md"]
        lines.append(f"- base_commit: {base}")
        lines.append(f"- head_commit: {head}")
        lines.append(f"- files_changed_git: {', '.join(changed) or '(none)'}")
        if changed:
            # git diff --stat omits untracked files; summarize those separately.
            stat = _git_out(workdir, "diff", "--stat", base) or ""
            new_summ = []
            for u in untracked:
                try:
                    with open(os.path.join(workdir, u),
                              encoding="utf-8", errors="replace") as fh:
                        nlines = sum(1 for _ in fh)
                    new_summ.append(f"{u} (+{nlines} new)")
                except OSError:
                    new_summ.append(f"{u} (unreadable)")
            if new_summ:
                stat = (stat + "\n" if stat else "") + "\n".join(new_summ)
            if len(stat) > 1500:
                stat = stat[:1500] + "\n...[truncated]"
            lines.append("- diffstat:")
            lines.append("```")
            lines.append(stat)
            lines.append("```")
    lines.append(f"- gate_triggers: empty_diff={gate_triggers}, "
                 f"midrun_no_edit={midrun_triggers}, "
                 f"exec_denied={exec_denied}")
    lines.append(f"- stored_output: {'on' if stored_output else 'off'} "
                 "(Context Mode: raw tool output stored + ctx_read)")
    if token_cap is not None:
        total_out, cap = token_cap
        lines.append(f"- token_cap: {'tripped' if total_out >= cap else 'not tripped'} "
                     f"at {total_out} out (cap {cap})")
    return "\n".join(lines)


class Worker:
    def __init__(self, workdir: str, exec_timeout: int,
                 ctx_store: ContextStore | None = None,
                 codemap: CodeMap | None = None):
        self.workdir = os.path.realpath(workdir)
        self.exec_timeout = exec_timeout
        self.files_changed: list[str] = []
        self.commands: list[str] = []
        # Context Mode: when a store is attached, exec/read_file store
        # their raw output on disk and return a capped summary + handle;
        # ctx_read puts the raw output back into the context on demand.
        self.ctx_store = ctx_store
        self.exec_denied_count = 0
        # Native code map (issue #80): when an index is attached,
        # code_query answers questions about the worktree's symbols.
        self.codemap = codemap

    def _safe(self, path: str) -> str:
        full = os.path.realpath(os.path.join(self.workdir, path))
        if full != self.workdir and not full.startswith(self.workdir + os.sep):
            raise ValueError(f"path escapes workdir: {path}")
        return full

    def run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "exec":
                return self._exec(args["command"], int(args.get("timeout", self.exec_timeout)))
            if name == "read_file":
                return self._read(args["path"], int(args.get("offset", 1)), int(args.get("limit", 200)))
            if name == "write_file":
                return self._write(args["path"], args["content"])
            if name == "edit_file":
                return self._edit(args["path"], args["old_text"], args["new_text"])
            if name == "list_dir":
                return self._list(args.get("path", "."))
            if name == "ctx_read":
                return self._ctx_read(args.get("handle", ""),
                                      args.get("start_line"),
                                      args.get("end_line"),
                                      args.get("needle"))
            if name == "code_query":
                return self._code_query(args.get("mode", ""),
                                        args.get("query", ""))
            return f"unknown tool: {name}"
        except Exception as exc:  # noqa: BLE001
            return f"tool error: {type(exc).__name__}: {exc}"

    def _exec(self, command: str, timeout: int) -> str:
        self.commands.append(command)
        guard_cfg = _exec_guard_cfg()
        if guard_cfg.get("enabled", False):
            rule = exec_guard_check(command, self.workdir)
            if rule:
                self.exec_denied_count += 1
                alt = _EXEC_GUARD_ALTERNATIVES.get(rule, "use another way")
                return (f"tool error: exec_denied:{rule}: command denied "
                        f"by the exec guard ({rule}); instead: {alt}. "
                        f"Do not rephrase the same command to evade it.")
        if guard_cfg.get("enabled", False) or guard_cfg.get(
                "scrub_always", False):
            env = exec_guard_env()
        else:
            # Pre-guard behaviour, unchanged while the spike is disabled.
            # Worker shells are non-interactive: ~/.profile is never
            # sourced, so the cargo bin dir (and similar) would be
            # missing from PATH. Set it explicitly — Rust workers need
            # cargo for build/test.
            env = dict(os.environ)
            cargo_bin = os.path.expanduser("~/.cargo/bin")
            if cargo_bin not in env.get("PATH", "").split(os.pathsep):
                env["PATH"] = cargo_bin + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.workdir, timeout=timeout,
                capture_output=True, text=True, env=env,
            )
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout}s: {command}"
        out = (proc.stdout or "") + (proc.stderr or "")
        if self.ctx_store is not None:
            return self._exec_stored(proc.returncode, out)
        if len(out) > 8000:
            out = out[:8000] + "\n...[truncated]"
        return f"exit={proc.returncode}\n{out.strip()}"

    def _exec_stored(self, returncode: int, out: str) -> str:
        """Context Mode exec: the raw output goes to the store, the context
        gets a capped summary plus a handle.

        The leading "exit=<code>" line is preserved verbatim: bin/grade_run.py
        detects test evidence by it and the tool-kind taxonomy reads the code
        from it, so the [ctx] summary goes *after* that line, never before.
        """
        body = out.strip()
        if not body:
            return f"exit={returncode}\n"
        try:
            text = _redact(body)
            handle = self.ctx_store.put(text)
        except OSError as exc:
            # The store failed: fall back to the legacy cap, but say what
            # was dropped -- a silent drop is exactly what this mode exists
            # to prevent.
            capped = body[:8000]
            dropped = len(body) - len(capped)
            return (f"exit={returncode}\n{capped}\n...[truncated]\n"
                    f"[ctx] storage failed ({type(exc).__name__}: {exc}); "
                    f"{dropped} chars dropped and NOT retrievable")
        summary = summarize_stored_output(
            text, handle, store_meta=self.ctx_store.meta.get(handle))
        return f"exit={returncode}\n{summary}"

    def _ctx_read(self, handle: str, start_line=None, end_line=None,
                  needle: str | None = None) -> str:
        """Put a stored tool output back into the context.

        The handle comes from a [ctx] summary; start_line/end_line narrow it
        to a line range and needle keeps only the matching lines.  The
        returned text is capped, and the cap states what was left out.
        """
        if self.ctx_store is None:
            return ("ctx_read: stored-output mode is off, so no output was "
                    "stored and no handle exists.")
        handle = str(handle or "").strip()
        text = self.ctx_store.get(handle)
        if text is None:
            return (f"ctx_read: unknown handle {handle!r} -- no stored "
                    "output with that handle in this run.")
        lines = text.splitlines()
        total = len(lines)
        start = _as_int(start_line, 1)
        end = _as_int(end_line, total)
        start = max(1, min(start, max(total, 1)))
        end = max(start - 1, min(end, total))
        selected = lines[start - 1:end]
        if needle:
            needle = str(needle)
            selected = [line for line in selected if needle in line]
        body = "\n".join(selected)
        header = (f"[ctx] ctx_read handle={handle} lines {start}-{end} of "
                  f"{total} ({len(selected)} lines, {len(body)} chars of "
                  f"{len(text)})")
        if needle:
            header += f' needle="{needle}"'
        if not selected:
            return header + ":\n[ctx] no matching lines"
        if len(body) > CTX_READ_MAX_CHARS:
            dropped = len(body) - CTX_READ_MAX_CHARS
            body = body[:CTX_READ_MAX_CHARS]
            return (f"{header}:\n{body}\n"
                    f"[ctx] TRUNCATED: {dropped} more chars are available -- "
                    "narrow the range with start_line/end_line or use a "
                    "needle")
        return f"{header}:\n{body}"

    def _code_query(self, mode: str, query: str) -> str:
        """Answer a code-map question from this run's index.

        The index is a snapshot of the worktree at run start, so every
        answer carries the index's HEAD sha, its build time, and the note
        that it is as of run start.
        """
        if self.codemap is None:
            return ("code_query: the code map is off for this run, so no "
                    "index was built and there is nothing to query.")
        return self.codemap.query(mode, query)

    def _read(self, path: str, offset: int, limit: int) -> str:
        full = self._safe(path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        start = max(0, offset - 1)
        chunk = lines[start : start + limit]
        text = "".join(chunk)
        header = (f"{path} (lines {offset}-{offset + len(chunk) - 1} of "
                  f"{len(lines)}):")
        if self.ctx_store is not None:
            # Context Mode read: store the whole chunk, return a capped
            # summary plus a handle (the old 12000-char cap dropped text
            # silently -- the summary says what was dropped instead).
            body = text.strip()
            if not body:
                return header
            try:
                stored_text = _redact(body)
                handle = self.ctx_store.put(stored_text)
            except OSError as exc:
                capped = body[:12000]
                dropped = len(body) - len(capped)
                return (f"{header}\n{capped}\n...[truncated]\n"
                        f"[ctx] storage failed ({type(exc).__name__}: {exc}); "
                        f"{dropped} chars dropped and NOT retrievable")
            summary = summarize_stored_output(
                stored_text, handle,
                store_meta=self.ctx_store.meta.get(handle))
            return f"{header}\n{summary}"
        if len(text) > 12000:
            text = text[:12000] + "\n...[truncated]"
        return f"{header}\n{text}"

    @staticmethod
    def _atomic_write(full: str, content: str) -> None:
        # Write to a temp file in the same directory and os.replace it
        # into place, so a failed/interrupted write never leaves the
        # target truncated (a truncate-in-place open("w") destroyed
        # files in the 2026-10-26 write fault).
        _atomic_write_text(full, content)

    def _write(self, path: str, content: str) -> str:
        full = self._safe(path)
        os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
        self._atomic_write(full, content)
        if path not in self.files_changed:
            self.files_changed.append(path)
        return f"wrote {path} ({len(content)} bytes)"

    def _edit(self, path: str, old_text: str, new_text: str) -> str:
        full = self._safe(path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        if old_text not in content:
            return f"edit failed: old_text not found in {path}"
        content = content.replace(old_text, new_text, 1)
        self._atomic_write(full, content)
        if path not in self.files_changed:
            self.files_changed.append(path)
        return f"edited {path}"

    def _list(self, path: str) -> str:
        full = self._safe(path)
        entries = sorted(os.listdir(full))
        return "\n".join(entries) if entries else "(empty)"


class _ProviderExhausted(RuntimeError):
    """A provider stayed rate/quota-limited through all polite retries."""


class _WorkerAborted(RuntimeError):
    """The model loop aborted (all providers exhausted)."""


_DC_UNSET = object()
_dc_module = _DC_UNSET


def _load_dc():
    """Return the credential-helper module if one is configured, else
    None. Search order: $PLAN_CODER_CRED_DIR, then ~/.plan-coder/cred.
    Lazy and optional — env-key-only setups never need the helper."""
    global _dc_module
    if _dc_module is not _DC_UNSET:
        return _dc_module
    _dc_module = None
    candidates = []
    env_dir = os.environ.get("PLAN_CODER_CRED_DIR")
    if env_dir:
        candidates.append(env_dir)
    cfg_dir = _cfg("credentials", "helper_dir")
    if cfg_dir:
        candidates.append(os.path.expanduser(cfg_dir))
    candidates.append(os.path.expanduser("~/.plan-coder/cred"))
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "dynamic_credentials.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            try:
                import dynamic_credentials

                _dc_module = dynamic_credentials
            except Exception:  # noqa: BLE001 - a broken helper is a config error
                _dc_module = None
            break
    return _dc_module


def _apply_auth(req, provider: dict) -> str:
    """Attach credentials to the request. Returns "env" or "helper".
    Raises RuntimeError on missing/broken credentials: that is a setup
    problem, not scarcity, and must fail fast rather than burn the
    fallback provider's quota."""
    key = os.environ.get(provider["env_key"], "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
        return "env"
    dc = _load_dc()
    if dc is not None:
        dc.add_surrogate_to_request(
            req, provider["credential"], allowed_hosts=provider["hosts"])
        return "helper"
    raise RuntimeError(
        f"no credential available: set {provider['env_key']} in the "
        "environment, or configure a credential helper "
        "($PLAN_CODER_CRED_DIR or ~/.plan-coder/cred)")


def _read_response(resp, mode: str) -> dict:
    if mode == "helper":
        return _dc_module.read_json_response(resp)
    return json.loads(resp.read().decode("utf-8"))


def chat_once(provider: dict, model: str, messages: list, tools: list) -> dict:
    """Try one provider with polite backoff. Raises _ProviderExhausted when
    it stays limited; RuntimeError for credential/config problems."""
    payload = json.dumps(
        {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto"}
    ).encode()
    last_err: Exception | None = None
    limited = False
    for attempt in range(6):
        req = urllib.request.Request(
            provider["chat_url"], data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        mode = _apply_auth(req, provider)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return _read_response(resp, mode)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # Rate limited: back off politely, honor Retry-After.
                limited = True
                retry_after = exc.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 45
                time.sleep(wait)
                last_err = exc
                continue
            if exc.code in (500, 502, 503, 529):
                last_err = exc
                time.sleep(10 * (attempt + 1))
                continue
            # 401/403 etc: credential or config problem — fail fast, don't
            # burn the fallback provider's quota on a broken setup.
            raise RuntimeError(f"model call failed: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_err = exc
            time.sleep(5 * (attempt + 1))
    if limited:
        raise _ProviderExhausted(f"still limited after retries: {last_err}")
    raise RuntimeError(f"model call failed after retries: {last_err}")


def chat(chain: list, messages: list, tools: list) -> tuple:
    """Try each (name, provider, model) in priority order, failing over to
    the next when one is quota/rate-limited. Returns (data, name, model)."""
    problems = []
    for name, provider, model in chain:
        try:
            return chat_once(provider, model, messages, tools), name, model
        except _ProviderExhausted as exc:
            problems.append(f"{name}: {exc}")
    raise RuntimeError("all providers exhausted: " + "; ".join(problems))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--provider", default="minimax,zai",
                    help="comma-separated provider priority list "
                         f"(choices: {', '.join(PROVIDERS)}); MiniMax first, "
                         "z.ai as fallback")
    ap.add_argument("--model", default=None,
                    help="explicit model id; overrides --model-tier")
    ap.add_argument("--model-tier", default="balanced",
                    choices=("cheap", "balanced", "flagship"),
                    help="model tier: cheap = small specified jobs, "
                         "balanced = ordinary issues, flagship = hard work")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--max-tokens-out", type=int, default=None,
                    help="stop the run once completion tokens reach this "
                         "many (0 or omitted = guard off; default comes "
                         "from $WORKER_MAX_TOKENS_OUT or the deployment "
                         "config's workers.max_tokens_out)")
    ap.add_argument("--exec-timeout", type=int, default=120)
    ap.add_argument("--run-id", default=None,
                    help="run identifier, used for the transcript log filename "
                         "(default: timestamp)")
    ap.add_argument("--transcript-dir", default=None,
                    help="directory for the JSONL transcript log "
                         "(default: $PLAN_CODER_TRANSCRIPT_DIR or "
                         "~/.plan-coder/transcripts)")
    ap.add_argument("--stored-output", dest="stored_output",
                    action="store_true", default=None,
                    help="Context Mode: store raw tool output on disk and "
                         "return a capped summary plus a handle (default: "
                         "off; on when $WORKER_STORED_OUTPUT=1)")
    ap.add_argument("--no-stored-output", dest="stored_output",
                    action="store_false",
                    help="Context Mode off (the pre-#52 behaviour)")
    args = ap.parse_args()

    # Context Mode switch: CLI wins, then the environment, then the
    # default (off -- the live A/B has not validated the mode yet).
    stored_output = _stored_output_enabled(args.stored_output)

    provider_names = [p.strip() for p in args.provider.split(",") if p.strip()]
    unknown = [p for p in provider_names if p not in PROVIDERS]
    if not provider_names or unknown:
        raise SystemExit(f"unknown provider(s): {', '.join(unknown)}")
    chain = []
    for pname in provider_names:
        p = PROVIDERS[pname]
        tiers = p.get("tiers", {})
        if args.model:
            pmodel, tier = args.model, "explicit"
        else:
            tier = args.model_tier
            pmodel = tiers.get(tier, p["default_model"])
        chain.append((pname, p, pmodel))
    workdir = os.path.realpath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    # Transcript log: one JSON line per step, feeding the weekly
    # harness-tuning review. Fields are truncated hard so a long run stays
    # small. This process never sees key material, but tool output can
    # carry absolute paths or secret-shaped strings from files the
    # worker read, so every line passes through _redact() before it is
    # written (hygiene for stored logs, not a security boundary).
    # Every event carries a UTC `ts`; step events also carry
    # `duration_ms` and typed `tool_result_kinds` (harness-tune
    # 2026-10-25, ideas brief T1/T2).
    run_id = args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    transcript_dir = os.path.expanduser(
        args.transcript_dir
        or os.environ.get("PLAN_CODER_TRANSCRIPT_DIR")
        or _cfg("state", "transcripts_dir")
        or "~/.plan-coder/transcripts")
    os.makedirs(transcript_dir, exist_ok=True)
    transcript_path = os.path.join(transcript_dir, f"{run_id}.jsonl")

    # Context Mode store: one directory per run, next to the transcript.
    ctx_store = None
    ctx_store_dir = ""
    if stored_output:
        ctx_store_dir = _ctx_store_dir(workdir, transcript_dir, run_id)
        ctx_store = ContextStore(ctx_store_dir)

    def _trunc(value: object, limit: int) -> str:
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "...[truncated]"

    _tlog = open(transcript_path, "w", encoding="utf-8")

    def tlog(obj: dict) -> None:
        obj = {"ts": datetime.now(timezone.utc).isoformat(), **obj}
        _tlog.write(_redact(json.dumps(obj, ensure_ascii=False)) + "\n")
        _tlog.flush()

    run_started = time.monotonic()

    # Native code map (issue #80, section 2): built at run start when the
    # gate is on, and stored outside the worktree.  A failed or timed-out
    # build changes nothing about the run -- no tool, no prompt paragraph,
    # the same tool set as before -- and is recorded in the transcript as
    # a `codemap_build_failed` event so the failure is visible.
    codemap = None
    if code_map_enabled():
        try:
            codemap = build_code_map(workdir, transcript_dir, run_id)
        except Exception as exc:  # noqa: BLE001 - a map must not kill a run
            tlog({"event": "codemap_build_failed",
                  "error": _trunc(f"{type(exc).__name__}: {exc}", 300)})

    # Token-out guard resolution: CLI wins, then env, then config; 0 = off.
    if args.max_tokens_out is not None:
        max_tokens_out = args.max_tokens_out
    elif os.environ.get("WORKER_MAX_TOKENS_OUT"):
        try:
            max_tokens_out = int(os.environ["WORKER_MAX_TOKENS_OUT"])
        except ValueError:
            max_tokens_out = 0
    else:
        try:
            max_tokens_out = int(
                _cfg("workers", "max_tokens_out", default=0) or 0)
        except (TypeError, ValueError):
            max_tokens_out = 0

    tlog({"event": "started", "run_id": run_id, "tier": tier,
          "chain": [f"{n}/{m}" for n, _, m in chain],
          "stored_output": stored_output,
          "code_map": codemap is not None,
          "ctx_store_dir": ctx_store_dir or None})

    with open(args.brief, encoding="utf-8") as fh:
        brief = fh.read()

    worker = Worker(workdir, args.exec_timeout, ctx_store=ctx_store,
                    codemap=codemap)
    # Context Mode: the retrieval tool is advertised only when the mode is
    # on, so an off run is byte-for-byte the pre-#52 tool set.  The code
    # map tool follows the same rule: it is advertised only when the gate
    # is on and the index was built, so a gate-off run keeps exactly the
    # tool set it has today.
    tools = list(TOOLS) + ([CTX_READ_TOOL] if ctx_store is not None else []) \
        + ([CODE_QUERY_TOOL] if codemap is not None else [])
    # The code-map paragraph is part of the same gate: with the tool
    # absent, the worker is not told about it.
    system_prompt = SYSTEM_PROMPT
    if codemap is not None:
        system_prompt += "\n" + CODE_MAP_PROMPT
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": brief},
    ]

    final_text = "(no summary produced)"
    steps_used = 0
    total_in = 0
    total_out = 0
    providers_used: set[str] = set()
    gate_triggers = 0
    gate_rounds_used = 0
    midrun_triggers = 0
    token_cap_tripped = False
    midrun_step = max(1, args.max_steps // 2)
    # Pin the base commit before the worker touches anything: the
    # empty-diff gate and the machine manifest measure against this.
    base_commit = _git_out(workdir, "rev-parse", "HEAD")

    def run_step() -> bool:
        """One chat + tool-execution round. Returns True to keep looping,
        False when the worker produced no tool calls (wants to finish)."""
        nonlocal final_text, total_in, total_out
        step_started = time.monotonic()
        try:
            data, used_provider, used_model = chat(chain, messages, tools)
            providers_used.add(f"{used_provider}/{used_model}")
        except RuntimeError as exc:
            tlog({"step": steps_used, "event": "aborted",
                  "error": _trunc(exc, 300)})
            raise _WorkerAborted(str(exc))
        usage = data.get("usage") or {}
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)
        msg = data["choices"][0]["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if msg.get("content"):
            final_text = msg["content"]
        if not tool_calls:
            return False
        calls_log = []
        results_log = []
        kinds_log = []
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                fargs = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                fargs = {}
            calls_log.append({"name": name, "args": _trunc(
                tc["function"].get("arguments") or "{}", 500)})
            result = worker.run_tool(name, fargs)
            results_log.append(_trunc(result, 800))
            kinds_log.append(_tool_result_kind(result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": result,
                }
            )
        tlog({"step": steps_used, "provider": used_provider,
              "model": used_model,
              "duration_ms": int((time.monotonic() - step_started) * 1000),
              "tokens_in": usage.get("prompt_tokens", 0),
              "tokens_out": usage.get("completion_tokens", 0),
              "assistant_text": _trunc(msg.get("content") or "", 400),
              "tool_calls": calls_log, "tool_results": results_log,
              "tool_result_kinds": kinds_log})
        return True

    hit_max_steps = True
    step = 0
    while step < args.max_steps:
        step += 1
        steps_used = step
        try:
            keep_going = run_step()
        except _WorkerAborted as exc:
            final_text = f"WORKER ABORTED: {exc}"
            hit_max_steps = False
            break
        # Token-out guard: checked after every step like the step cap.
        # Tripping stops the run cleanly — no more model calls, the
        # report is still written (marked), and no PR is opened by
        # this process (it never opens PRs; the supervisor records
        # the outcome as stopped:token-cap).
        if max_tokens_out and total_out >= max_tokens_out:
            token_cap_tripped = True
            tlog({"step": step, "event": "token_cap",
                  "total_out": total_out, "cap": max_tokens_out})
            final_text += "\n\n[stopped: token budget reached]"
            hit_max_steps = False
            break
        if keep_going:
            # Mid-run no-edit gate: at the halfway point, a worktree
            # with still no changes gets one checkpoint nudge while
            # half the budget remains to act on it.
            if (MIDRUN_GATE_ENABLED and midrun_triggers == 0
                    and step >= midrun_step
                    and _worktree_changed(workdir, base_commit) is False):
                midrun_triggers += 1
                tlog({"step": step, "event": "gate_trigger",
                      "gate": "midrun_no_edit", "round": midrun_triggers,
                      "max_rounds": 1})
                messages.append({"role": "user",
                                 "content": MIDRUN_GATE_PROMPT})
            continue
        # The worker wants to finish: empty-diff completion gate.
        changed = _worktree_changed(workdir, base_commit)
        if (EMPTY_DIFF_GATE_ENABLED and changed is False
                and gate_rounds_used < EMPTY_DIFF_GATE_MAX_ROUNDS):
            gate_rounds_used += 1
            gate_triggers += 1
            tlog({"step": step, "event": "gate_trigger",
                  "gate": "empty_diff", "round": gate_rounds_used,
                  "max_rounds": EMPTY_DIFF_GATE_MAX_ROUNDS})
            messages.append({"role": "user", "content": EMPTY_DIFF_GATE_PROMPT})
            continue
        hit_max_steps = False
        break
    if hit_max_steps:
        final_text += "\n\n[stopped: max steps reached]"

    chain_desc = ", ".join(f"{n}/{m}" for n, _, m in chain)
    used_desc = ", ".join(sorted(providers_used)) or "(none)"
    report = (
        f"# Worker report\n\n- Provider chain: {chain_desc} (tier {tier})\n"
        f"- Providers used: {used_desc}\n"
        f"- Tokens used: ~{total_in} in / ~{total_out} out "
        f"({total_in + total_out} total)\n"
        f"- Steps used: {steps_used}/{args.max_steps}\n"
        f"- Files changed: {', '.join(worker.files_changed) or '(none)'}\n"
        f"- Commands run: {len(worker.commands)}\n\n"
        f"## Summary\n\n{final_text}\n\n"
        + _machine_manifest(workdir, base_commit, gate_triggers,
                            midrun_triggers,
                            token_cap=((total_out, max_tokens_out)
                                     if max_tokens_out else None),
                            stored_output=stored_output,
                            exec_denied=worker.exec_denied_count)
        + "\n"
    )
    with open(os.path.join(workdir, "WORKER_REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    tlog({"event": "finished", "steps": steps_used,
          "duration_ms": int((time.monotonic() - run_started) * 1000),
          "total_in": total_in, "total_out": total_out,
          "files_changed": worker.files_changed,
          "commands_run": len(worker.commands),
          "gate_triggers": gate_triggers,
          "midrun_gate_triggers": midrun_triggers,
          "exec_denied": worker.exec_denied_count,
          "token_cap": token_cap_tripped,
          "max_tokens_out": max_tokens_out,
          "stored_output": stored_output,
          "code_map": codemap is not None,
          "ctx_store_dir": ctx_store_dir or None})
    _tlog.close()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
