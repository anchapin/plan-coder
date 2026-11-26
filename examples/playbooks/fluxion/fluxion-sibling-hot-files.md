---
id: fluxion-sibling-hot-files
repo: fluxion
title: Sibling-split clusters share hot files: keep hot-file edits minimal and last
status: active
created: 2026-11-26
keywords: sibling, hot-file, rebase, conflict, mod.rs
failure_classes: rebase-conflict, bad-diff
paths: src/physics/mod.rs, src/sim/mod.rs, src/lib.rs, AGENTS.md
---

# E4 — Sibling-split clusters share hot files: keep hot-file edits minimal and last

- **Lesson:** Fluxion work is routinely split into sibling sub-issues that land in parallel and edit the same shared files (`src/physics/mod.rs`, `src/sim/mod.rs`, `src/lib.rs`, `AGENTS.md`, the test-inventory/drift files). Expect your diff to conflict with a sibling at collection/rebase. Keep edits to those hot files minimal, make them as the last edits of the run, and do not be surprised by (or try to resolve around) a sibling's version of the same file.
- **Evidence:** Ledger note, #4706: "Rebase conflicted (sibling splits #4710/#4712 landed in same file during run) — aborted, PR #4714 opened with merge conflicts per protocol" (the run had also hit 60/60 on final test-constructor edits). Transcript #4695 run 2 contains a cross-worktree `git checkout --ours AGENTS.md scripts/check_test_inventory…` conflict resolution in sibling worktree `fluxion-4710-w2`. 21/84 window runs have a hot file in their final diff (below).
- **Trigger / attachment:** Attach when the issue belongs to a numbered sibling cluster (adjacent issue numbers / same parent epic) or the SCOPE touches a hot file.

Source: Phase 0 report for plan-coder #184 (2026-11-26), entry E4, used verbatim. (E3, the test-inventory lock-step, was deliberately excluded in Phase 0 as already covered by CONTRACT.md.)
