---
name: plan-coder-manager
description: >-
  Run plan-coder as the manager: select issues, write briefs, dispatch and
  collect workers, review diffs, open PRs, and apply the merge policy and hard
  stops. Use when working in this repository as the orchestrating agent, or
  when asked to brief, dispatch, collect or merge plan-coder workers.
---

# plan-coder manager skill (stub)

The skill body lives in [`AGENTS.md`](AGENTS.md) at the repo root — read it
first; it is the canonical, host-neutral entry point for the manager role.

- Roles, run lifecycle, never-dos, where each operation lives, how to see
  status today, when to escalate: [`AGENTS.md`](AGENTS.md).
- The mechanical, step-by-step checklist (issue selection, brief discipline,
  collect/review verification, merge policy, hard stops, escalation kinds):
  [`docs/MANAGER.md`](docs/MANAGER.md).
- The operating policy in prose: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

This stub exists only so the repo root can be used as a skill directory by a
host that expects `SKILL.md` there. It deliberately contains no policy of its
own: edit `AGENTS.md` (and `docs/MANAGER.md`) instead, and keep this file a
pointer.
