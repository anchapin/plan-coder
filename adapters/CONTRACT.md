# Host adapter contract

This document is the contract a **host adapter** must satisfy to run
plan-coder on an agent host. It is the normative reference for
`adapters/`; the sketches in [`README.md`](README.md) show how three
specific hosts map onto it.

Issue #74, Phase 0.

---

## 1. What an adapter is — and what it is not

plan-coder's operating behaviour is already host-neutral. It lives in
four places, and an adapter touches none of them:

| Layer | Where it lives |
|---|---|
| Roles, run lifecycle, never-dos | [`AGENTS.md`](../AGENTS.md) |
| The mechanical manager checklist | [`docs/MANAGER.md`](../docs/MANAGER.md) |
| The operating policy in prose | [`docs/OPERATIONS.md`](../docs/OPERATIONS.md) |
| The live source of orchestration behaviour | [`prompts/`](../prompts/) (token templates rendered by `bin/render_prompts.py`, installed by `bin/install_crons.py`) |

An adapter is the **thin plumbing** between a host and that behaviour.
It answers exactly two questions:

1. *When* is the manager invoked, and *with what*?
2. *Where* do next-actions and escalations go, and *how* does an
   approval come back?

### 1.1 The non-negotiable rule

> **Adapters differ in scheduling and approval plumbing ONLY — never in
> merge policy, brief discipline, verification gates, or hard stops.**

Any adapter text, comment, prompt or configuration that forks policy is
a **defect in that diff**, not a variation. If an adapter needs to state
a rule about what may be merged, how a brief is written, which gate must
pass, or when to stop and escalate, it must instead link to
[`docs/OPERATIONS.md`](../docs/OPERATIONS.md) (policy) and
[`docs/MANAGER.md`](../docs/MANAGER.md) (the checklist) and let the
prompt carry the behaviour.

Concretely, an adapter must **not**:

- restate, paraphrase or tighten the merge policy, the hard stops or the
  verification gates;
- contain its own brief template or issue-selection rules;
- decide on its own whether a run succeeded beyond the manager command's
  exit status;
- change what the supervisor or the shepherd is allowed to do.

### 1.2 No operator-specific values

An adapter file contains **no operator-specific values**: no real paths
outside the repository, no personal or host-account names, no tokens,
keys or other secrets, no live schedule ids, no chat or channel ids.
Deployment values come from the deployment config
(`config/deployment.local.json`, template
`config/deployment.example.json`) and are substituted at render time.

---

## 2. What a host must provide

These four capabilities are the whole of the host contract. A host that
cannot provide one of them says so explicitly rather than silently
degrading.

### 2.1 A periodic invoker

The host must be able to run an entry point on a cadence, twice:

| Role | Cadence | What it runs |
|---|---|---|
| supervisor | ~every 2 hours | one supervisor cycle (collect stragglers, select issues, dispatch workers, open PRs) |
| shepherd | ~every 30 minutes | one PR-shepherd pass (CI triage, bounded fixes, merge-under-policy) |

The cadence values themselves are deployment values (the `crons` section
of the deployment config); the adapter never hard-codes them. A weekly
tuning pass is a third, optional cadence for hosts that support it.

### 2.2 Delivery to the human, and a way back

The host must be able to:

- **deliver** the manager's next-actions and escalations to the human —
  the output of a supervisor or shepherd cycle (what the read-only
  manager CLI prints as `next-actions`, plus any escalation the cycle
  raises); and
- **receive** an approval or a decision back, and make it available to
  the next cycle.

The channel is the host's business (a chat, a thread, an inbox, a
terminal prompt). What the host must preserve is the *round trip*: the
human's decision reaches the next cycle as input. An approval that the
host swallows, or that arrives after the next cycle has already run, is
a broken host, not a policy question.

### 2.3 A next-cycle trigger, or an explicit heartbeat-only statement

The host must either:

- **trigger the next cycle** on an event (a new PR comment, a CI
  conclusion, a reply from the human) — the adapter then documents how
  that trigger is wired; or
- **state explicitly that it is heartbeat-only**: cycles are
  time-driven, no event triggers a cycle, and the human must not wait
  for an event to arrive.

The second option is legitimate, but it must be *said out loud* in the
adapter's documentation. Silence here reads as "event-triggered" and
makes a missing escalation look like a host bug.

### 2.4 Secret injection

Provider keys (the `providers` section of the deployment config) must
reach the manager process through the environment or a credential
helper. They must never be:

- written into a file the harness reads,
- interpolated into a prompt, a brief or a rendered body,
- passed on a command line,
- printed in a log, a transcript or a report.

An adapter that runs the manager inherits this obligation: it injects
the secret into the child process's environment and forgets about it.

### 2.5 Optional but expected

- **Observability**: the entry point's stdout/stderr and exit status are
  visible to the host's scheduler log.
- **Idempotence of the *host*, not the adapter**: the manager's own
  guard against overlapping cycles (a collectible-status check) lives in
  the prompts and the state files, not in the adapter.

---

## 3. The adapter surface

A host adapter provides, per role (supervisor, shepherd):

1. **An entry point** — an executable the host's scheduler can call.
2. **Configuration through the environment** — no values baked into the
   adapter. A missing configuration value is a **loud failure** (non-zero
   exit with a message naming the variable), never a silent default that
   invents behaviour.
3. **Exit-status propagation** — the adapter exits with the manager
   command's own status, so the host's scheduler can see a failed cycle.
   Adapter-level configuration errors use exit code `2`.
4. **No state writes** — the adapter never writes `workers.json`,
   `shepherd.json` or a ledger. State is written only through
   `bin/state_write.py`. (Phase 0's manager CLI, `bin/plan_coder.py`, is
   read-only, so an adapter built on it writes nothing at all.)
5. **A smoke test** — a stub-driven test that proves the adapter invokes
   the configured command with the expected arguments and propagates a
   failure. The generic adapter's test is
   [`tests/test_generic_adapters.py`](../tests/test_generic_adapters.py).

### 3.1 The generic adapter's configuration

`adapters/generic/supervisor.sh` and `adapters/generic/shepherd.sh` are
the reference implementation of this surface. They are host-agnostic: the
host's scheduler calls them, and they call the manager command.

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_CODER_MANAGER_CMD` | yes | The command that runs the plan-coder manager. Word-split, so it may carry arguments (e.g. `python3 bin/plan_coder.py next-actions --json`). |
| `PLAN_CODER_SUPERVISOR_PROMPT` | no | Path to the rendered supervisor prompt, passed to the manager command as its final argument. Set it to an **empty value** for CLI mode (no prompt argument). When unset, the conventional rendered file `local/backlog-supervisor.rendered.md` is used if it exists, otherwise no prompt argument is passed. |
| `PLAN_CODER_SHEPHERD_PROMPT` | no | The same, for the shepherd (`local/pr-shepherd.rendered.md`). |

Relative prompt paths resolve against the repository root; `~/` and
absolute paths are used as given. A configured prompt path that does not
exist is a configuration error (exit `2`).

Two modes, both supported:

- **Prompt mode** — the manager command takes a prompt path:
  `PLAN_CODER_MANAGER_CMD="<host cli>" PLAN_CODER_SUPERVISOR_PROMPT=local/backlog-supervisor.rendered.md adapters/generic/supervisor.sh`
- **CLI mode** — the manager command is self-sufficient (the read-only
  manager CLI's subcommand): set the prompt variable to an empty value so
  no prompt argument is appended.

---

## 4. Compliance checklist for a new host adapter

- [ ] The adapter changes **scheduling and approval plumbing only**.
- [ ] No policy is restated: merge policy, brief discipline, verification
      gates and hard stops are linked to
      [`docs/OPERATIONS.md`](../docs/OPERATIONS.md), not copied.
- [ ] No operator-specific values: no external paths, names, secrets,
      schedule ids or channel ids.
- [ ] Every configuration value comes from the environment, and a missing
      one fails loudly (exit `2`, message naming the variable).
- [ ] The manager command's exit status is propagated unchanged.
- [ ] The next-cycle trigger is documented, or the adapter says
      explicitly that it is heartbeat-only.
- [ ] Secret injection is environment-based, and the adapter never logs a
      credential.
- [ ] A stub-driven smoke test proves the invocation and the failure
      propagation.

---

## 5. Scope of this phase

Phase 0 delivers the contract and the generic shell adapter as the
proof. Deferred (and not to be assumed present):

- the Claude Code, OpenClaw/Hermes and other host-specific adapters —
  only sketches live in [`README.md`](README.md);
- the MCP server (Layer 4);
- the end-to-end live-cycle gate — a supervisor cycle plus a shepherd
  cycle run by a fresh manager on a test deployment. That gate is
  exercised by the operator after this change lands; the proof in this
  phase is the stub-driven smoke test.
