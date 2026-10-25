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
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

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
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001 - config is a convenience, not a gate
            continue
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
- Do NOT ask the user questions; make reasonable choices and note them.
- When done (or stuck), write a final summary: what changed, test results,
  and anything you could not resolve.
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


def _tool_result_kind(result: str) -> str:
    """Typed tool-error taxonomy (harness-tune 2026-10-25, ideas brief
    T2): a small closed vocabulary recorded alongside the raw result
    string, never instead of it, so failure modes are countable
    without breaking grep-based transcript review."""
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
                      midrun_triggers: int = 0) -> str:
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
                 f"midrun_no_edit={midrun_triggers}")
    return "\n".join(lines)


class Worker:
    def __init__(self, workdir: str, exec_timeout: int):
        self.workdir = os.path.realpath(workdir)
        self.exec_timeout = exec_timeout
        self.files_changed: list[str] = []
        self.commands: list[str] = []

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
            return f"unknown tool: {name}"
        except Exception as exc:  # noqa: BLE001
            return f"tool error: {type(exc).__name__}: {exc}"

    def _exec(self, command: str, timeout: int) -> str:
        self.commands.append(command)
        # Worker shells are non-interactive: ~/.profile is never sourced,
        # so the cargo bin dir (and similar) would be missing from PATH.
        # Set it explicitly — Rust workers need cargo for build/test.
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
        if len(out) > 8000:
            out = out[:8000] + "\n...[truncated]"
        return f"exit={proc.returncode}\n{out.strip()}"

    def _read(self, path: str, offset: int, limit: int) -> str:
        full = self._safe(path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        start = max(0, offset - 1)
        chunk = lines[start : start + limit]
        text = "".join(chunk)
        if len(text) > 12000:
            text = text[:12000] + "\n...[truncated]"
        return f"{path} (lines {offset}-{offset + len(chunk) - 1} of {len(lines)}):\n{text}"

    def _write(self, path: str, content: str) -> str:
        full = self._safe(path)
        os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
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
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
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
    ap.add_argument("--exec-timeout", type=int, default=120)
    ap.add_argument("--run-id", default=None,
                    help="run identifier, used for the transcript log filename "
                         "(default: timestamp)")
    ap.add_argument("--transcript-dir", default=None,
                    help="directory for the JSONL transcript log "
                         "(default: $PLAN_CODER_TRANSCRIPT_DIR or "
                         "~/.plan-coder/transcripts)")
    args = ap.parse_args()

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

    def _trunc(value: object, limit: int) -> str:
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "...[truncated]"

    _tlog = open(transcript_path, "w", encoding="utf-8")

    def tlog(obj: dict) -> None:
        obj = {"ts": datetime.now(timezone.utc).isoformat(), **obj}
        _tlog.write(_redact(json.dumps(obj, ensure_ascii=False)) + "\n")
        _tlog.flush()

    run_started = time.monotonic()

    tlog({"event": "started", "run_id": run_id, "tier": tier,
          "chain": [f"{n}/{m}" for n, _, m in chain]})

    with open(args.brief, encoding="utf-8") as fh:
        brief = fh.read()

    worker = Worker(workdir, args.exec_timeout)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
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
            data, used_provider, used_model = chat(chain, messages, TOOLS)
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
                            midrun_triggers)
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
          "midrun_gate_triggers": midrun_triggers})
    _tlog.close()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
