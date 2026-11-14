# Phase 1 compatibility probes (issue #22)

Two independent half-day probes, each pass/fail with evidence, run
entirely in scratch space against a **replay of one recorded worker
session**.  Nothing is adopted, nothing is integrated: this spike only
judges whether a future native build brief is worth filing.

* **Probe H -- Headroom proxy** (external tool: an HTTP hop in front of
  a provider endpoint).  **Verdict: FAIL (blocked).**
* **Probe C -- Context Mode pattern** (native prototype inside a scratch
  copy of `bin/agent.py`'s tool layer).  **Verdict: PASS (with caveats).**

No harness file, config, or prompt was touched.  The live harness
(`bin/agent.py`, `prompts/`, rendered cron bodies) is unchanged; the
probes ran against a transcript-driven replay in scratch space only.
`config/deployment.local.json` was not created, edited, or read.

## Phase 0 gate (already cleared -- not redone here)

| gate input | value | source |
|---|---|---|
| median tool-output share (overall) | 11.77% | `docs/spikes/headroom-phase0.md` |
| p90 tool-output share (overall) | 16.60% | `docs/spikes/headroom-phase0.md` |
| p99 tool-output share (overall) | 22.35% | `docs/spikes/headroom-phase0.md` |
| kill threshold (median) | < 10.0% | `docs/spikes/headroom-phase0.md` |
| tail threshold (p90) | >= 20.0% | `docs/spikes/headroom-phase0.md` |
| verdict | **PHASE-1-ELIGIBLE** | `docs/spikes/headroom-phase0.md` |

Phase 0 was measurement only; it is not repeated here.  The two probes
below add *compatibility* evidence on top of that measurement.

## Replay setup (shared by both probes)

**Recorded session (the replay source, read-only):**
`~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts/fluxion-4689-1794225608c.jsonl`

| property | value |
|---|---|
| run id / repo | `fluxion-4689-1794225608c` (fluxion) |
| steps replayed | 60 |
| tool calls replayed | 60, all `exec` |
| recorded tool-result chars | 40,423 |
| truncation markers in recorded results | 45 |
| recorded `finished` event | `steps=60, total_in=2084207, total_out=41380, files_changed=[], gate_triggers=0, token_cap=false` |
| recorded final `tokens_in` (real provider) | 89,998 |
| selection rule | in the Phase 0 14-day window; all 60 tool-call argument payloads parse as JSON; highest exec-char count among such runs in the window |

**Harness.**  The replay drives the **real** provider layer of
`bin/agent.py` (`agent.chat` -> `agent.chat_once`, the real `TOOLS`
schema and the real `SYSTEM_PROMPT`); only the network peer is replaced.
A local mock endpoint (stdlib `http.server`) serves the *recorded*
assistant messages in order and measures the prompt it actually
receives.  A second local endpoint always answers `429` (with
`Retry-After: 0`) so the harness' failover path can be exercised.  A
third, transparent pass-through HTTP hop stands in for a proxy.

**Token estimator.**  4 characters per token, the same estimator Phase 0
used, applied to the reconstructed message list.

**Fidelity caveat (important, and the reason the absolute numbers below
are small).**  The harness truncates tool results to 800 characters
(`_trunc(result, 800)`) *before* writing them to the transcript, while
the live context carried up to 8,000 characters per `exec`.  The replay
therefore reconstructs a context that is smaller than the original
session's (final step: 18,178.8 replay tokens vs 89,998 recorded real
tokens).  The compressed-vs-uncompressed comparison is unaffected: both
replays use the identical reconstruction, and the only variable changed
is whether the `exec` result is capped.  The measured saving is
consequently a **floor** for the real run, not a ceiling.

**Scratch layout.**  `spikes-scratch/` in the worktree (deleted before
the diff was finalised): `probe.py` (mock endpoint, proxy hop, Probe C
prototype, replay driver), `summarise.py`, `ctx/` (the raw-output
store), `out_*.json` (run artefacts).

---

## Probe H -- Headroom proxy

### Verdict: **FAIL (blocked -- the tool cannot be obtained in this environment)**

Probe H requires `headroom proxy` to be run in front of one provider
endpoint.  `headroom` is not installed here and cannot be installed:
every package channel reachable from this environment is blocked by a
TLS-intercepting proxy that fails the handshake.

### Exact failure mode (verbatim)

```
$ python3 -m venv /tmp/hr-phase1-venv
$ /tmp/hr-phase1-venv/bin/pip install headroom
WARNING: Retrying (Retry(total=4, connect=None, read=None, timeout=None, status=None)) after connection broken by 'SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)'))': /simple/headroom/
...
Could not fetch URL https://pypi.org/simple/headroom/: There was a problem confirming the ssl certificate: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded with url: /simple/headroom/ (Caused by SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)')) - skipping
ERROR: Could not find a version that satisfies the requirement headroom (from versions: none)
ERROR: No matching distribution found for headroom
```

The same handshake failure hits every other channel:

```
$ curl -sS https://pypi.org/simple/headroom/    -> curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
$ curl -sS https://github.com                    -> curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
$ cargo search headroom                          -> error: failed to retrieve search results from the registry at https://crates.io
                                                  Caused by: [35] SSL connect error: error:0A00010B:SSL routines::wrong version number
$ npm view headroom version                      -> exit 124 (timeout; registry unreachable)
$ pip show headroom                             -> WARNING: Package(s) not found: headroom
$ which headroom                                 -> (not found)
```

So the three pass criteria of Probe H split as follows:

| Probe H criterion | result | evidence |
|---|---|---|
| `tool_calls` round-trip intact | **not testable with headroom**; the *harness-side* half passes (see pre-check) | headroom not installable |
| no auth / failover breakage | **not testable with headroom**; the *harness-side* half passes (see pre-check) | headroom not installable |
| measured `tokens_in` drops vs the uncompressed replay | **not testable with headroom** -- this is precisely the part headroom itself would have to do | headroom not installable |

Per the brief, this is recorded as a legitimate Fail with the exact
failure mode; the run was **not** spent fighting the install (one
attempt per channel, then move on).

### Interim compatibility pre-check (NOT headroom -- do not read it as a headroom pass)

To avoid discarding the probe entirely, the *harness-side* half of
Probe H was measured against a **transparent pass-through HTTP hop**
(the same wire shape a proxy sits in: `POST` body in, response out,
`Authorization` header preserved).  This says nothing about headroom's
own behaviour; it says the harness would survive a proxy hop.

```
$ python3 spikes-scratch/probe.py echo --out spikes-scratch/out_echo.json
direct        : round_trip_intact=True  (3/3 tool_calls, both directions)
through_proxy : round_trip_intact=True  (3/3 tool_calls, both directions, 3 proxy hops)
```

* `tool_calls` round-trip: the endpoint's canned `tool_call` came back
  to the harness with identical `name` and `arguments`, and the harness
  sent the same `tool_calls` (plus the `tool` result message) back
  through the hop -- verified on both the direct and the proxied path.
* auth: an `Authorization: Bearer ...` header (injected by the
  credential helper, never printed, redacted here) was present on all
  **60** proxied requests and reached the endpoint unchanged.
* failover: with the rate-limited endpoint first in the chain, that
  endpoint was hit **360** times (6 polite attempts x 60 steps) and the
  run still completed on the second provider; the replayed trajectory
  digest was unchanged (`93794dbe7c1b5da3`), i.e. failover did not
  corrupt the tool-call stream.

### Harness gap a future proxy adoption would have to close

`bin/agent.py` hard-codes the provider endpoint:

```python
PROVIDERS = {
    "zai": {"chat_url": "https://api.z.ai/api/coding/paas/v4/chat/completions", ...},
    "minimax": {"chat_url": "https://api.minimax.io/v1/chat/completions", ...},
}
```

There is no `chat_url` (base-URL) override in
`config/deployment.example.json` and none in the deployment-config
loader, so pointing the harness at `headroom proxy` needs a small
code/config change (a `providers.<name>.chat_url` key) before any
proxy can be used at all.  That is a known, bounded cost -- but it is
not free, and it is not exercised yet.

---

## Probe C -- Context Mode pattern (native prototype)

### Verdict: **PASS (with caveats)**

The MCP was **not** installed.  The pattern was prototyped natively
inside a scratch copy of `bin/agent.py`'s tool layer: an `exec`-variant
that runs the command, keeps the raw output in a local file, and returns
a capped summary plus a retrieval handle.

### The prototype

```python
def exec_context(self, command):            # scratch copy of Worker
    proc = subprocess.run(command, shell=True, cwd=self.workdir, ...)
    raw = "exit=%d\n%s" % (proc.returncode, stdout + stderr)
    handle = self.store.put(raw)            # raw -> ctx/<sha256[:12]>.txt
    return summarize(raw, handle)           # capped summary + handle

def summarize(raw, handle):                 # cap: first 6 lines / 400 chars
    return ("[ctx] command output captured (%d lines, %d chars)\n" % ...
            + capped_body
            + "... (%d more lines dropped)\n"
            + "full output: %s (retrieve with ctx_read)" % handle)
```

Retrieval is `ctx_read(handle, needle)` -> the matching lines of the
stored raw file.

**Live prototype demo** (real command, real subprocess, in the
worktree -- not a mock):

```
$ python3 spikes-scratch/probe.py proto --out spikes-scratch/out_proto.json
raw output   : 16,376 chars / 155 lines  (cat ruff.toml + 9,000 x 'x')
summary      : 326 chars + handle ctx:d0ec85b249b6
retrieve_ok  : True      (the dropped content is retrievable by handle)
```

So the pattern works end to end: the command runs, the raw output is on
disk, the context gets a capped summary plus a handle, and the handle
resolves back to the full raw text.

### Replay measurement (same replay, one variable changed)

The recorded `exec` results stand in for the command's raw output
(the commands refer to a worktree that does not exist in this
environment, so they cannot be re-executed; the recorded output *is* the
raw output for this replay).  Everything else -- the assistant messages,
the tool-call sequence, the prompt scaffolding -- is identical.

| metric | uncompressed replay | compressed replay | delta |
|---|---|---|---|
| sum of `tokens_in` over the 60 steps | 550,466.2 | 405,338.0 | **-26.36%** |
| `tokens_in` of the final step | 18,178.8 | 13,382.8 | **-26.38%** |
| tool calls replayed | 60 | 60 | identical |
| trajectory digest (sha256 of every tool call + args) | `93794dbe7c1b5da3` | `93794dbe7c1b5da3` | identical |

Per-step `tokens_in` on the tool-heavy steps (replay estimator):

| step | uncompressed | compressed | delta | recorded real `tokens_in` |
|---|---|---|---|---|
| 1 | 612.8 | 480.8 | 132.0 | 1,553 |
| 5 | 1,769.5 | 1,171.5 | 598.0 | 3,333 |
| 10 | 3,165.2 | 2,148.2 | 1,017.0 | 10,919 |
| 20 | 6,144.5 | 4,386.0 | 1,758.5 | 20,832 |
| 30 | 9,139.2 | 6,831.2 | 2,308.0 | 30,183 |
| 40 | 12,003.2 | 8,996.8 | 3,006.5 | 44,308 |
| 50 | 14,484.2 | 10,779.5 | 3,704.8 | 56,497 |
| 55 | 16,313.2 | 12,027.0 | 4,286.2 | 71,802 |
| 58 | 17,438.0 | 12,910.0 | 4,528.0 | 82,562 |
| 59 | 17,796.8 | 13,134.5 | 4,662.2 | 86,375 |
| 60 | 18,178.8 | 13,382.8 | 4,796.0 | 89,998 |

The saving grows with the step, i.e. it lands exactly on the
tool-heavy steps where the tool output dominates the context -- which is
what the pattern is for.

### Did the worker lose information it later needed?

No -- but it would have, without retrieval.  Analysis of the replay:

* **Later-need items: 301.**  These are identifiers (file/path-like
  tokens, >= 8 chars) that appeared in an earlier `exec` output, were
  dropped by the 6-line/400-char cap, and are referenced later in the
  trajectory (a later tool call names them).
* **Retrievable: 301 / 301 (100%).**  Every one of them was found
  again through its handle in the raw store (`ctx_read` hit, 0 misses).
* **Cost of serving every one of them: 6,753 chars = 1,688 tokens.**
  Compressed *and* retrieving every need still lands at 407,026.2
  tokens -- **26.06% below** the uncompressed replay.

So the information is *deferred*, not destroyed: it costs ~0.3% of the
context to restore all of it (1,688 of 550,466 tokens).

### Did the replayed run ever retrieve?

**No -- and that is the honest answer.**  The recorded trajectory
predates the pattern: it contains 0 retrieval calls, because the
recorded worker had no `ctx_read` tool and no handle.  The replay
therefore *cannot* show a worker spontaneously retrieving; what it shows
is that the handles the pattern would have handed out cover 100% of the
information the trajectory later referenced.

### Caveats on Probe C

1. **The compressed replay keeps the recorded trajectory by
   construction** (the mock endpoint serves the recorded assistant
   messages regardless of what the client sends).  "Identical task
   outcome" is therefore a property of the replay design, not an
   emergent property of a worker that has to decide when to retrieve.
   A real build must be re-validated live: a worker that never
   retrieves would silently work with a lossy context.
2. **The later-need analysis is oracle-assisted**: it uses the recorded
   *future* trajectory to decide what "needed later" means.  A live
   worker would have to notice the need itself.
3. **The measured 26.36% is a floor** for the real run (the replay's
   `exec` outputs are the transcript's 800-char-truncated versions, not
   the live 8,000-char ones).  On the live shape the same cap would
   remove proportionally more.
4. The cap (6 lines / 400 chars) was chosen as a plausible default,
   not optimised.  It is one line in the prototype.

---

## Recommendations (Go / No-go for a future build brief)

| probe | verdict | recommendation |
|---|---|---|
| **H -- Headroom proxy** | FAIL (blocked: not installable) | **No-go for filing a Headroom build brief now.**  The token-drop criterion -- the only criterion that distinguishes Headroom from the native pattern -- is untestable here, and an untested external dependency that must be fetched at deploy time is a supply-chain and availability risk for a harness whose whole point is cheap, reliable token plans.  **Revisit** if/when the package becomes reachable: the harness-side half is green (tool_calls round-trip, auth, failover all survive an HTTP hop), and the only harness change a proxy needs is a `providers.<name>.chat_url` override.  That makes a retry cheap: ~half a day, same replay. |
| **C -- Context Mode pattern (native)** | PASS (with caveats) | **Go -- file a native build brief**, scoped as: (a) an `exec`-variant in `bin/agent.py`'s tool layer that stores raw output in a local file and returns a capped summary + handle; (b) a `ctx_read` retrieval tool; (c) a harness gate so a run cannot silently lose information -- e.g. the *summary* must state what was dropped and the *brief* must tell the worker to retrieve before relying on truncated output.  Expected effect from this replay: **>= 26% of the replayed context** on a tool-heavy session, with the full information still recoverable for ~0.3% of the context.  The brief must also close caveat 1 above: a live A/B (compressed vs not) on a real issue, checking that the worker actually retrieves and that the grade holds. |

Both probes stay in the spike: nothing was adopted, nothing was
integrated, and this spike ships no code into the harness.

---

## Reproduction

Scratch-only (the script lived in `spikes-scratch/`, deleted after the
run; a copy of the scratch tree is outside the diff, under
`/tmp/hr-phase1-scratch/`):

```
python3 spikes-scratch/probe.py replay --transcript <transcript> --mode full
python3 spikes-scratch/probe.py replay --transcript <transcript> --mode compressed
python3 spikes-scratch/probe.py replay --transcript <transcript> --mode full --proxy --failover
python3 spikes-scratch/probe.py echo
python3 spikes-scratch/probe.py proto
python3 spikes-scratch/summarise.py
```

The three lines that decide the numbers, verbatim from the scratch
script (`spikes-scratch/probe.py`):

```python
# the tool layer under test (Probe C prototype)
if mode == "compressed" and name == "exec":
    handle = store.put(recorded)          # raw output -> ctx/<sha>.txt
    raw_by_handle[handle] = recorded
    result = summarize(recorded, handle)  # capped summary + handle
else:
    result = recorded                      # uncompressed replay

# the measurement: the prompt the endpoint actually receives
chars = len(json.dumps(payload, ensure_ascii=False))   # in the mock endpoint
usage = {"prompt_tokens": int(chars / 4.0)}             # 4 chars/token, as Phase 0

# the outcome check: sha256 over every tool call + its arguments
digest = hashlib.sha256(json.dumps(trajectory, ensure_ascii=False).encode()).hexdigest()
```

### Verification output

```
$ python3 -m pytest tests/ -q
.................................................................. [ 28%]
........................................................................ [ 60%]
................................................................. [ 88%]
..........................                                               [100%]
229 passed, 13 subtests passed in 19.67s

$ python3 -m pytest tests/test_headroom_phase1.py -q
12 passed in 0.20s

$ git status --porcelain
?? docs/spikes/headroom-phase1.md
?? tests/test_headroom_phase1.py
```

(The scratch directory `spikes-scratch/` and the temporary venv
`/tmp/hr-phase1-venv` were deleted; `git status` shows only the
deliverable and its test -- no scratch residue, no venv residue, no
harness change.)

