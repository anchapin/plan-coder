#!/usr/bin/env bash
# Generic plan-coder shepherd adapter (issue #74, Phase 0).
#
# This wrapper is deliberately thin.  It resolves the manager command and
# the rendered PR-shepherd prompt from the environment, runs the manager
# command with the prompt as its last argument, and exits with the manager
# command's own status.  It contains no policy of its own: merge policy,
# brief discipline, verification gates and hard stops live in
# docs/OPERATIONS.md, docs/MANAGER.md and prompts/ and are identical for
# every host.  Only scheduling and approval plumbing differ per host, and
# that plumbing is the host's own configuration -- never this file.
#
# Contract: adapters/CONTRACT.md
#
# Environment:
#   PLAN_CODER_MANAGER_CMD      (required) the command that runs the
#                               plan-coder manager, e.g.
#                               "python3 bin/plan_coder.py shepherd-check --json"
#                               or a host CLI that takes a prompt path.
#   PLAN_CODER_SHEPHERD_PROMPT  (optional) path to the rendered shepherd
#                               prompt.  Set it to an empty value to run in
#                               CLI mode (no prompt argument).  When unset,
#                               the conventional rendered file
#                               local/pr-shepherd.rendered.md is used if it
#                               exists, otherwise no prompt argument is
#                               passed.
#
# Secrets: provider keys are injected by the host into the environment of
# the manager command.  This wrapper never reads, writes, prints or logs a
# credential.
#
# Exit codes: the manager command's exit status, or 2 for a configuration
# error (missing manager command, missing prompt file).
set -euo pipefail

ROLE="shepherd"
PROMPT_VAR="PLAN_CODER_SHEPHERD_PROMPT"
DEFAULT_PROMPT_REL="local/pr-shepherd.rendered.md"

die() {
  printf '%s adapter: %s\n' "$ROLE" "$*" >&2
  exit 2
}

if [ -z "${PLAN_CODER_MANAGER_CMD:-}" ]; then
  die "PLAN_CODER_MANAGER_CMD is not set; export the command that runs the plan-coder manager (see adapters/CONTRACT.md)"
fi

repo_root="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"

# Resolve the prompt: explicit setting wins (an empty value means "no
# prompt"), otherwise fall back to the conventional rendered file.
if [ -n "${PROMPT_VAR+set}" ]; then
  prompt_raw="${!PROMPT_VAR}"
else
  prompt_raw=""
  if [ -f "$repo_root/$DEFAULT_PROMPT_REL" ]; then
    prompt_raw="$DEFAULT_PROMPT_REL"
  fi
fi

prompt_arg=""
if [ -n "$prompt_raw" ]; then
  case "$prompt_raw" in
    "~/"*) prompt_path="$HOME/${prompt_raw#~/}" ;;
    /*)    prompt_path="$prompt_raw" ;;
    *)     prompt_path="$repo_root/$prompt_raw" ;;
  esac
  [ -f "$prompt_path" ] || die "prompt file not found: $prompt_path"
  prompt_arg="$prompt_path"
fi

printf '%s adapter: running manager command (prompt: %s)\n' \
  "$ROLE" "${prompt_arg:-none}" >&2

# Split the configured command into words and run it with the prompt (when
# configured) as the final argument.
eval "set -- ${PLAN_CODER_MANAGER_CMD}"
if [ -n "$prompt_arg" ]; then
  set -- "$@" "$prompt_arg"
fi

exec "$@"
