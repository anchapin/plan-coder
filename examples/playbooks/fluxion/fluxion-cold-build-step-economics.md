---
id: fluxion-cold-build-step-economics
repo: fluxion
title: Cold-build step economics: warm the target before you write code
status: active
created: 2026-11-26
keywords: cargo, build, cold-build, cargo-check, step-cap
failure_classes: step-cap, build-wait
paths: Cargo.toml, benches/
---

# E1 — Cold-build step economics: warm the target before you write code

- **Lesson:** A fresh fluxion worktree's first cargo invocation pays the full dependency build (CONTRACT already notes a cold build "dwarfs the run" for the validate smoke). Start a warm-up `cargo check`/build in the background as one of the first actions, interleave code/report writing while it runs, and never let the *first* build happen late in the run — workers that do burn the step cap waiting on builds instead of verifying.
- **Evidence:** Ledger note, #4690 (pr_opened only after supervisor rescue): "Worker hit 60/60 cap waiting on release build… Supervisor ran the finished harness binary… completed report sections 3–8". Ledger note, #4688: "Worker killed by runtime restart at step 28/60 during bench build; supervisor completed builds+measurements". Transcript #4774 run 2: worker discovering "The build takes longer than 120s… use timeout=600" mid-run. Transcript #4695 run 3: repeated "The build is still going… Let me wait" steps.
- **Trigger / attachment:** Attach to every fluxion brief whose verification includes a cargo command (i.e. nearly all).

Source: Phase 0 report for plan-coder #184 (2026-11-26), entry E1, used verbatim.
