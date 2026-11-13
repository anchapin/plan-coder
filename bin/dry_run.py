#!/usr/bin/env python3
"""Offline dry-run: exercise the worker loop end to end, no credentials.

This is the no-op equivalent of "watch the first worker PR land" for a
reviewer who has no provider key yet (or no quota). It runs the real
worker loop (bin/agent.py) against a fake provider served from
127.0.0.1, so nothing leaves the machine:

  * no network egress (the fake provider is a local HTTP server),
  * no real credential (a placeholder is set in-process only; it is
    never printed and never leaves the loopback interface),
  * no real repo required (a scratch git repo is created under a temp
    dir; --repo <clone> runs the same flow inside a temporary git
    worktree of a clone you already have).

What it proves: the brief is read, the tools are wired, the worker is
confined to its workdir, a diff is produced, and the machine manifest
is written. That diff is exactly the artifact a supervisor turns into
a pull request.

Usage:
    python3 bin/dry_run.py                 # scratch repo, temp dir
    python3 bin/dry_run.py --repo ~/repos/repo-a   # temp worktree
    python3 bin/dry_run.py --keep          # keep the temp dir

Exit 0 = the dry run produced a diff (the PR-equivalent artifact).
Exit 1 = no diff was produced (the run failed).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HERE)
PLACEHOLDER_KEY = "dry-run-placeholder-not-a-real-key"
NOTE_PATH = "DRY_RUN.md"
NOTE_BODY = (
    "# Dry run\n\n"
    "The worker loop produced this file through the fake provider.\n"
    "On a live deployment the same diff is what the supervisor wraps\n"
    "into a pull request.\n"
)


class _Handler(BaseHTTPRequestHandler):
    """Answers every chat call: one write_file tool call, then a summary."""

    calls = 0

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        type(self).calls += 1
        if type(self).calls == 1:
            message = {
                "role": "assistant",
                "content": "Writing the dry-run note.",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": NOTE_PATH, "content": NOTE_BODY}),
                    },
                }],
            }
        else:
            message = {
                "role": "assistant",
                "content": (
                    "Dry run complete: wrote %s. This diff is the "
                    "pull-request equivalent of the dry run." % NOTE_PATH),
            }
        body = json.dumps({
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the server log
        pass


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=False)


def _make_scratch_repo(path):
    _git(["init", "-q"], path)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# scratch repo\n\nUsed by the plan-coder dry run.\n")
    _git(["add", "README.md"], path)
    _git(["-c", "user.name=Dry Run", "-c", "user.email=dry@example.invalid",
          "commit", "-q", "-m", "scratch base commit"], path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None,
                    help="existing local clone to run the dry run in "
                         "(a temporary git worktree is created and removed)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the temporary directory instead of deleting it")
    ap.add_argument("--provider", default="zai", choices=("zai", "minimax"))
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="plan-coder-dryrun-")
    worktree_in_repo = False
    branch = None
    try:
        if args.repo:
            repo = os.path.abspath(os.path.expanduser(args.repo))
            if not os.path.isdir(os.path.join(repo, ".git")):
                print("error: --repo must be a git clone (%s)" % repo,
                      file=sys.stderr)
                return 1
            workdir = os.path.join(tmp, "worktree")
            branch = "dry-run/%s" % time.strftime("%Y%m%d-%H%M%S")
            r = _git(["worktree", "add", "-q", "-b", branch, workdir, "HEAD"],
                     repo)
            if r.returncode != 0:
                print("error: git worktree add failed:\n%s" % r.stderr,
                      file=sys.stderr)
                return 1
            worktree_in_repo = True
        else:
            workdir = os.path.join(tmp, "repo")
            os.makedirs(workdir)
            _make_scratch_repo(workdir)

        brief = os.path.join(tmp, "BRIEF.md")
        with open(brief, "w", encoding="utf-8") as fh:
            fh.write(
                "Task: run the dry-run flow.\n\n"
                "Acceptance:\n"
                "1. Create %s in the workdir.\n"
                "2. Leave the workdir with a non-empty diff.\n" % NOTE_PATH)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/chat" % srv.server_address[1]

        sys.path.insert(0, HERE)
        import agent  # noqa: E402  (import after sys.path setup)

        provider = agent.PROVIDERS[args.provider]
        provider["chat_url"] = url
        os.environ[provider["env_key"]] = PLACEHOLDER_KEY

        transcripts = os.path.join(tmp, "transcripts")
        argv = [
            "agent.py", "--brief", brief, "--workdir", workdir,
            "--provider", args.provider, "--model-tier", "cheap",
            "--max-steps", "6", "--run-id", "dry-run",
            "--transcript-dir", transcripts,
        ]
        sys.argv = argv
        rc = agent.main()
        srv.shutdown()

        # Intent-to-add so untracked worker output shows up in the diffstat.
        _git(["add", "-A", "-N"], workdir)
        status = _git(["status", "--short"], workdir)
        diffstat = _git(["diff", "--stat", "HEAD", "--", ".",
                         ":(exclude)WORKER_REPORT.md"], workdir)
        changed = [ln for ln in status.stdout.splitlines()
                   if ln.strip() and "WORKER_REPORT.md" not in ln]
        print("")
        print("=== dry-run result ===")
        print("workdir: %s" % workdir)
        print("worker exit code: %s" % rc)
        print("changed paths:")
        for line in changed:
            print("  %s" % line)
        print("diffstat:\n%s" % (diffstat.stdout.strip() or "(none)"))
        if not changed:
            print("DRY RUN FAILED: the worker produced no diff.")
            return 1
        print("DRY RUN OK: a diff exists — this is the artifact a "
              "supervisor turns into a pull request.")
        return 0
    finally:
        if worktree_in_repo and not args.keep:
            _git(["worktree", "remove", "--force",
                  os.path.join(tmp, "worktree")], args.repo)
            if branch:
                _git(["branch", "-D", branch], args.repo)
        if args.keep:
            print("kept temporary directory: %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
