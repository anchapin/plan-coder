---
id: fluxion-core-struct-literals
repo: fluxion
title: Adding a field to a core struct: sweep all literals first, compile the test target early
status: active
created: 2026-11-26
keywords: struct, field, literal, ground_conduction, ground_coupling
failure_classes: step-cap, bad-diff, periodic-verification
paths: src/physics/, src/sim/, fluxion-core/src/
---

# E5 — Adding a field to a core struct: sweep *all* literals first, compile the test target early

- **Lesson:** Fluxion core structs (e.g. in `src/physics/ground_conduction.rs`, `fluxion-core/src/ground_coupling.rs`) are constructed by literals scattered across the main crate, the `fluxion-core` crate, ASHRAE test files and unit-test modules. Before editing, grep-enumerate every literal of the struct, update them in one pass, and compile the *test* target immediately — discovering literals one compile error at a time consumed whole runs in the ground-conduction cluster.
- **Evidence:** #4774 (4 runs: `failed:periodic-verification`, `failed:provider-abort`, 2× pr_opened) — transcript run 2 mid-run: "Now I need to add the two new fields to the existing struct literals: 1. tests/ashrae_140/ground_slab_steady_state.rs (two literals)… 2. src/physics/ground_conduction.rs unit test module (one literal)". #4775 (4 runs on the same files: `failed:bad-diff`, `failed:session-killed`, `failed:step-cap`, pr_opened). Combined: 8 runs, 2 issues, 5 non-`pr_opened` outcomes, all around `ground_conduction.rs` / `ground_coupling.rs`.
- **Trigger / attachment:** Attach when SCOPE adds/changes a field on a struct in `src/physics/`, `src/sim/` or `fluxion-core/src/`, or touches `ground_conduction.rs`-class long-lived core modules.

Source: Phase 0 report for plan-coder #184 (2026-11-26), entry E5, used verbatim.
