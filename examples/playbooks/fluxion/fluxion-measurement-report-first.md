---
id: fluxion-measurement-report-first
repo: fluxion
title: Measurement/bench runs: the report is the deliverable — skeleton first, numbers incrementally
status: active
created: 2026-11-26
keywords: measurement, bench, spike, report, benchmark
failure_classes: incomplete-report
paths: benches/, docs/perf/
---

# E2 — Measurement/bench runs: the report is the deliverable — skeleton first, numbers incrementally

- **Lesson:** On fluxion measurement, bench and spike runs (deliverable = a `docs/perf/*.md` report and/or a bench harness, not a behaviour change), write the report skeleton with its verdict structure *before* running the harness, and fill each measurement into the report as soon as it exists. A finished harness with no report is a failed run: this cluster produced every `failed:incomplete-report` in the window.
- **Evidence:** All **6** fluxion `failed:incomplete-report` rows in the window are in the #4688–#4699 measurement cluster (#4688 ×3, #4694, #4697, #4699). #4690 and #4691 reached `pr_opened` only with `supervisor_completed` measurement/report work (ledger notes). #3988 verification run: `worker_hit_cap=1, supervisor_completed=1`.
- **Trigger / attachment:** Attach when the brief's deliverable is a measurement/benchmark/spike report or a `benches/` harness (identifiable from the brief's SCOPE/done-definition).

Source: Phase 0 report for plan-coder #184 (2026-11-26), entry E2, used verbatim.
