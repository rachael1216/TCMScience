# From control plane to governed agent runtime

Two reviews asked a question the enforcement work does not answer: **how much of an agent
runtime is actually here?** Their conclusion was blunt and correct —

> PSH-Harness is a *Governed Agent Control Plane + Execution Broker*, not a multi-agent
> runtime. It is "single governed execution + a delegation primitive", not "a recursive
> multi-agent loop".

This document is the honest map, what v0.5.1 added against it, and the order to build the
rest in. It is deliberately separate from `V5_1_GATE_COMPOSITION_CLOSURE.md`, because the
sequencing matters: **a loop multiplies whatever the gates get wrong.** Every defect the
second review found would have fired once per iteration rather than once per run. Gates
first, then iteration.

## 1. Where it actually stands

Measured against the executing path, not the README.

| Mechanism | v0.5 | v0.5.1 | Note |
| --- | --- | --- | --- |
| Single governed execution pipeline | ✅ | ✅ | `Runner.run`, 16 stages |
| Typed plan | ❌ placeholder | ✅ | `runtime/plan.py` |
| Model-backed planner | ❌ | ✅ | `runtime/planner.py`, validator-driven correction |
| Plan validation | ❌ `"no typed plan to validate"` | ✅ | `runtime/plan_validator.py`, five families |
| Plan/act/observe/evaluate loop | ❌ | ✅ | `runtime/loop.py`, bounded |
| Tool-use loop integrated with the runtime | ❌ | ✅ | `TaskKind.TOOL` through the broker |
| Iterative replanning | ❌ | ✅ | bounded by `max_replans` |
| Loop termination controller | ❌ repeat counter | ✅ | ten named `Termination` reasons |
| Retry / backoff | ❌ config only | ✅ | `RetryPolicy` per task, charged to the budget |
| Layered evaluator | ❌ | ✅ | structural / execution / evidence / goal |
| Acceptance tests executed | ❌ declarative | ✅ | `ACCEPTANCE_CHECKS`, unknown kind = plan error |
| Output-schema validation | ❌ declarative | ✅ | `check_output_schema` |
| Delegation contract | ✅ | ✅ | `DelegationContract` |
| Child authority narrowing | ✅/🟡 | ✅ | gateway now defers to `AuthorityLattice` |
| Automatic subagent selection | ❌ | ✅ | the planner is told whether delegation is available and for how many children (`LoopState.can_delegate`, the authority brief); a delegate task with nowhere to go is corrected, not crashed |
| Fan-out / fan-in | ❌ | ✅ | `runtime/supervisor.py`: `WorkerPool`, `Reducer` → `AggregatedObservation` |
| Supervisor / worker pool | ❌ | ✅ | proposes only; `restrict()` authorises; Σ child fractions ≤ 1 |
| Heartbeat / lease | ❌ | ✅ | `WorkerLease` / `LeaseRegistry`; a silent child is `STALLED`, not waited for |
| Idempotency keys | ❌ | ✅ | `runtime/idempotency.py`; stable across retries and resume |
| Cancellation propagation | 🟡 interface | ✅ | `WAIT` / `CASCADE` / `DETACH`, cooperative token |
| Checkpoint / resume | 🟡 audit event | ✅ | `runtime/checkpoint.py`, authority re-met on resume |
| Parallel execution | ❌ | ✅ | supervisor: threads over one locked kernel, bounded by `max_concurrency`; loop: independent ready tasks on a bounded pool, `LoopLimits(max_parallel)` |
| Progressive disclosure of tool schemas | ❌ | ✅ | `CapabilityRegistry.schema_items`: summaries to choose, schemas to call, for the ranked few |
| A2A / MCP | ❌ | ✅ | `protocols/mcp.py`, `protocols/a2a.py`; no SDK, no new gate |
| Context compaction | ❌ | ✅ | `context/compaction.py`, label is the join of the sources |
| Project memory recall | ❌ | ✅ | `context/memory.py`: verified nodes only, labels carried, withheld above the run ceiling or for the destination, read-only by structural test |
| Persistent WorkGraph, provenance, quarantine, audit | ✅ | ✅ | the package's strongest layer |

So the honest summary is now: **a bounded, governed agent loop with governed, durable
fan-out, and remote tools and agents admitted on the operator's terms.** Children run
concurrently through one locked kernel, a supervisor that can only narrow, a reducer that
records disagreement, leases so a child that stops answering is given up on, and MCP/A2A
adapters that add no execution path the gates do not already cover — and, since v0.5.2,
project memory that a later run recalls under the same gateway's labels, and a planner
that is told when it may delegate. Still absent: distributed workers and A2A polling for
long-running remote tasks.

## 2. What v0.5.1 added, and the one rule it was built under

```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            Plan, PlanTask, TestSpec, Criterion, RetryPolicy, RetryBudget
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph, TaskState — transient, NOT the WorkGraph
├── evaluator.py       four layers, three deterministic
└── loop.py            AgentLoopController, LoopState, Termination
```

The rule, which the review states and which is the whole reason this was built here rather
than adopted from elsewhere:

> **The LoopController must never bypass the TrustedKernel.**

```
        AgentLoopController                 what most agent runtimes are
                 |                                     |
          ExecutionBroker                              |-- calls a provider
                 |                                     |-- spawns a subprocess
          TrustedKernel                                |-- opens a socket
                 |                                     '-- spawns an agent
    model / tool / delegate
```

It is enforced three ways, not asserted once:

1. `_dispatch` has exactly three branches, each a broker call.
2. `test_the_loop_cannot_act_except_through_the_broker` balances the broker's counters
   against what the loop did.
3. `test_the_loop_module_holds_no_route_to_the_outside_world` parses `loop.py` and fails if
   it ever imports `subprocess`, `socket`, `urllib`, `asyncio`, `os` or a HTTP client — a
   behavioural test sees the paths a test took; this sees the paths that *exist*. That
   distinction is exactly how `IsolatedRunner` shipped in v0.4 while `call_tool` ran
   everything in process.

Three design decisions worth stating because they differ from the review's sketch:

**`ExecutionGraph` is not the `WorkGraph`.** The review recommended separating them and it
is right: a scheduling structure whose nodes go `RUNNING` and `FAILED` and get retried
cannot be the same object as a provenance record, or a retry rewrites history. Outcomes
reach the WorkGraph through the persistence gateway; task states never leave memory except
as audit events and, when a checkpoint store is wired, as checkpoints written under the
persistence rules (`docs/RUNTIME_SECURITY_REVIEW.md`).

**Task authority is `restrict()`, not a new comparison.** `task_envelope()` narrows the run
envelope with the existing lattice, and the *validator returns the envelopes the loop then
uses*. Recomputing them at execution time would put a second construction path beside the
validated one — the precise shape of defect this release spent its time closing.

**Three deterministic evaluator layers before the model-shaped one.** A single "LLM critic:
looks good?" is both the least reliable check and the one that gets asked first. Schema,
execution state and evidence are pure functions of loop state; only the goal layer may
consult a model, and when it does it goes through the broker.

## 3. What to build next, in order

The ordering is by engineering dependency, not by appeal. In particular **do not start with
MCP or A2A**: both are adapters onto a runtime, and adapting an incomplete runtime means
building the adapter twice.

### v0.6 — finish the single-agent runtime

* **A real planner.** ~~`StaticPlanner` takes the plan from the caller, which is honest but
  is not planning.~~ **Done** (`psh/runtime/planner.py`). `ModelPlanner` emits a typed
  `Plan` through the broker, with PydanticAI-style validator-driven correction: each
  refusal is fed back as the next attempt's input, bounded by `max_attempts`. The load-
  bearing test is that an *escalating* plan is refused rather than obeyed — a planner's
  output is a program, not an answer, so trusting it because a model produced it would make
  every control in the package reachable by asking.
* **Context compaction.** ~~Absent entirely.~~ **Done** (`psh/context/compaction.py`),
  in DeepSeek Harness's shape — summarise and shadow rather than truncate. It landed in the
  compiler rather than in a session, because this runtime has no shared transcript to
  compact: `ContextProjection` belongs to one worker, so the place the loss actually
  occurred was the compiler silently discarding over-budget items and reporting a count.

  The property that made it a kernel concern: **a summary carries the join of the labels it
  summarises.** Deriving it from the summary text instead would let a summary of PHI whose
  extract omits the identifiers classify as `INTERNAL` — measured, not hypothesised — and
  reach a destination its sources could not. The default summariser is extractive and
  deterministic, because compression on the critical path must not itself require egress.
* **Checkpoint / resume for real.** ~~`LoopState` was written to be the thing a checkpoint
  copies.~~ **Done** (`psh/runtime/checkpoint.py`). The load-bearing rule held: a resumed
  run computes `AuthorityLattice.meet(stored, current_ceiling)`, so it is never wider than
  it was *or* than the policy in force, and every narrowed dimension is audited. Restoring
  the stored envelope would have been P0-1 through a file rather than a keyword argument —
  the same defect's second vector, which is the argument for the meet living in one place
  and every entry point going through it. Checkpoints are content-hashed and refused if
  they do not verify; unfinished tasks are re-authorised individually and finished ones are
  not re-checked.

  Building it surfaced a defect in `task_envelope`: `risk` was met with `min()` while
  `max_label` and `autonomy` were passed through unclamped, so a ceiling applied on one
  dimension and refused on the next. The distinction is now explicit — `max_risk`,
  `max_label` and `autonomy` are *self-imposed ceilings* and meet the run's;
  `destinations` and `capability_requirements` are *required reach* and are refused if the
  run does not hold them.

### v0.7 — multi-agent — **done** (`runtime/subagent.py`, `runtime/supervisor.py`)

`Supervisor`, `WorkerPool`, fan-out/fan-in, `Reducer`, child lifecycle and cancellation
propagation, under the one rule:

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

```
Supervisor --propose--> TrustedKernel --authorize--> Worker
```

The supervisor holds no way to construct a `RunEnvelope`. `mint()` turns a request into
arguments for `parent.restrict()` — the lattice — and a request for more than the parent
holds raises from inside that call. A structural test parses `mint()` and fails if it ever
compares anything against the parent: the supervisor is *contained*, not trusted, which is
the stronger property and the cheaper one. Children are `AgentLoopController`s under the
contract's envelope, returning a `SubagentResult` — claims, evidence, artifacts, a summary,
and a label that is the join of everything the child saw. There is no field for a
transcript (Grok Build's model, and `ContextProjection`'s argument applied one level up).

Three things it surfaced:

* **`Budget.child()` never bounded siblings.** Its docstring says it "stops a delegation
  tree from multiplying a budget by fanning out". Ten quarter-children are two and a half
  parents, measured. `BudgetLedger` sums fractions per parent run, cumulatively — a finished
  child spent its slice, so releasing it would allow children forever, one at a time.
* **The kernel was not safe to share between threads.** `BudgetGovernor`'s checks are
  compare-then-increment; forty trials at the tightest GIL switch interval could not race
  them, and a ceiling that holds because of scheduler timing is not a ceiling. Locked, along
  with the broker's counters — which are the proof nothing bypassed the broker, and would be
  worthless under concurrency exactly when they matter.
* **Cancellation needs a policy, not a flag.** `WAIT` / `CASCADE` / `DETACH`, per child, on
  a cooperative token the loop polls at the same point it checks every other bound. A parent
  being cancelled does not always mean killing the five-hour analysis.

Fan-in is `AggregatedObservation`: facts with who asserted them, evidence, *conflicts*
(detected with the existing `ClaimSupportVerifier` — polarity and overlap, not a new model
of contradiction), what is unresolved, and a label that is the join. Two children
disagreeing is a recorded state.

### v0.8 — durability — **done** (`runtime/subagent.py`, `runtime/idempotency.py`)

Checkpoint/resume landed in v0.6 and cancellation policy in v0.7, so what remained was
leases and idempotency, and both were built after measuring the defect they close.

**A hung child was invisible.** A child whose tool never returned — a dead socket, a
deadlocked driver — left the parent's `wait()` blocked forever, and `dispatch(wait=True)`
passed no timeout. Nothing anywhere recorded that the child was stuck. Cooperative
cancellation cannot help; the child is inside a call it will never come back from. So the
signal is the *absence* of a heartbeat: every child holds a `WorkerLease`, the loop beats at
every bounds check and before every task (per task, so a long plan of short steps is never
mistaken for a hang), and `reap()` marks a silent child `STALLED`, cancels its token so it
stops if it ever wakes, and lets the parent proceed. The thread is leaked knowingly. What is
*not* claimed: that the thread was stopped. An in-process tool that never returns cannot be
interrupted from outside, and saying "killed" about it would be the process-group defect in
another costume.

**Leaking that thread crashed the process.** The first end-to-end test segfaulted: the
stalled child, mid-append on the shared event store, while the parent's `kernel.close()`
closed the sqlite connection from another thread. `check_same_thread=False` permits
sharing; it does not make closing safe. `EventStore.close()` takes the write lock now —
so it waits for a writer in flight and makes every later one fail cleanly — and a late
child's final audit write ends quietly. The regression test reproduces the segfault 3/3
against the old `close()`.

**Idempotency keys** are `"<loop run id>:<task id>"`, the same on every attempt and —
because `resume()` preserves the run id through the meet — the same after a crash. That is
the case they exist for: `resume()` turns a task that was RUNNING into RETRYABLE because
what it did is unknown, and a component with side effects would do them again unless it
recognises the key. The loop cannot decide replay semantics for a component, so
`IdempotencyLedger` is something a component *uses*, not something the kernel imposes.

### v0.9 — interoperability — **done** (`protocols/mcp.py`, `protocols/a2a.py`)

`MCPToolAdapter` for agent↔tool, `A2AAgentAdapter` for agent↔agent. Both under one rule:

```
MCP server / remote AgentCard
        |
     adapter
        |
  IngressClassifier        <- external metadata is untrusted input
        |
  ComponentManifest / RemoteAgent
        |
  ToolGateway / DelegationGateway
        |
    TrustedKernel
```

MCP's own documentation says its tool annotations are **hints, not security guarantees**,
and A2A's samples say an `AgentCard` and everything a remote agent returns are untrusted.
Both adapters take the protocols at their word, and the consequence is the property worth
stating: **neither added a gate.** An MCP tool is a `ComponentManifest` whose destination
is the operator's, so PHI to it is refused by the same `ToolGateway` check that refuses PHI
to a public model. A remote agent is a component *and* a delegation, so it passes
`DelegationGateway` for its authority and `ToolGateway` for its data — two gates built for
local work, and the remote case is where they are most obviously necessary.

The rules, per adapter:

* **MCP.** Annotations may tighten and never loosen: `destructiveHint` raises the risk and
  requires approval; `readOnlyHint` changes nothing, because a server that can lie about
  being read-only can lie. The only way to relax a default is an operator `override`,
  recorded as the operator's decision. The destination class is stated at adapter
  construction, never inferred. An `isError` reply is a `ContractViolation`, not a result
  with an odd shape. Runtime bookkeeping (`_psh_*` keys) is stripped before the wire.
* **A2A.** The card grants nothing: `skills` become retrieval metadata, `capabilities`
  are recorded as claims, and the `url` must fall inside the operator's `allowed_hosts` or
  the card is refused. `input-required` is escalated, never answered — a remote agent asking
  a question is asking a human. A non-terminal reply is not a result. The reply's label is
  the join of what was sent and what came back, classified.
* **Neither imports an SDK.** Each takes a transport callable and the protocol's own JSON
  shapes, so the official client, a stdio subprocess and a test double share one seam and
  the trust boundary lives here rather than inside a library's session object.

It surfaced one defect that predates both adapters. `CapabilityRegistry.manifest_items`
rendered every description into the model's context with the **default `PUBLIC` label**,
and the compiler's destination filter reads the label — so a description containing PHI
would have been compiled into a public model's context. Harmless while every description
was written by an operator; not harmless the moment one arrives from a server. Adapters now
classify descriptions at ingress and the registry labels the rendered item accordingly;
the regression test fails against the unpatched registry.

What is *not* claimed: A2A polling. The transport is synchronous and a task that comes back
`working` is escalated rather than waited on.

## 4. What to borrow, and what not to

The reviews' survey, reduced to the decisions:

| Take from | What | Why not wholesale |
| --- | --- | --- |
| LangGraph | checkpointer, thread-scoped state vs. cross-thread store, `Send` map-reduce | its state model has no authority dimension |
| Google ADK | workflow graph, fan-out/fan-in, nested workflows | same |
| PydanticAI | typed output + validator + retry, *layered* retry budgets | `max_retries` as one number is the thing to avoid |
| Microsoft Agent Framework | `Handoff`, `Concurrent`, Magentic manager | manager-grants-tools is the anti-pattern here |
| OpenAI Agents SDK | the loop shape, sessions, handoff input filters | — |
| Temporal / Prefect | durable scheduling, cancellation semantics, task lifecycle | heavyweight for a research prototype; take the semantics |
| DeepSeek Harness | pluginised agent loop, durable session event log, **compaction** | — |
| Grok Build | subagent = independent child session returning a *summary*; worktree isolation; folder trust | — |
| Codex | execpolicy `allow/prompt/forbidden`, approval→amendment, sandbox/exec seams | already adopted; see `PATTERN_ATTRIBUTION.md` |

Two of these are worth calling out as directly applicable rather than aspirational:

**Grok's subagent model.** A child gets its own context window and returns a *summary*, not
its transcript. That is the same argument `ContextProjection` already makes — compiled
context belongs to one worker — applied to delegation, and it is what stops a parent's
window from accumulating every child's raw output. `DelegationContract` should return
`SubagentResult(summary, artifacts, claims, evidence)`.

**Grok's folder trust.** Repository-local hooks, skills and instructions go through a trust
gate. This package governs runtime authority well and has no notion of *where a
configuration came from*. A `SourceTrust` axis — `SYSTEM` / `USER` / `TRUSTED_PROJECT` /
`UNTRUSTED_PROJECT` / `REMOTE` — would stop a cloned research repository's `AGENTS.md` from
influencing policy. That matters as much for shared data repositories as for code.

And one to decline: **do not adopt another framework's runtime wholesale.** The reviews'
own conclusion is the right one — loop and multi-agent orchestration are commoditising,
while "a child's authority cannot float up, sensitive data cannot cross a boundary, a
scientific claim needs evidence, and the whole run is recoverable and auditable" is not.
That is the part worth keeping, and it is the part a drop-in runtime would replace.

## 5. Convergence with BioScience-Harness

The two packages are complementary rather than competing:

> **PSH** — can the agent do this? &nbsp;&nbsp;**BioScience** — what can the agent actually do?

The recommended shape, and the thing *not* to do:

```
             Agent Runtime  (loop, planner, supervisor, workers)
                    |  proposes
         PSH TrustedKernel  (authority, labels, budget, approval, audit, evidence)
                    |  authorizes
    BioScience Capability Plane  (registry, resolver, backends, acquisition, evolution)
```

Do **not** merge the two `Runtime`s, the two `Policy` objects or the two `Manifest`s. Keep
PSH's kernel as the immutable base; keep BioScience's registry, resolver, backends,
acquisition and evolution pipeline as the capability layer; write the orchestration layer
once, on top. Concretely:

* **One policy, PSH's.** BioScience's genuinely distinct contribution there is not its
  permission model but its **licence and integration provenance** (`MIT`, `Apache-2.0`,
  `NOASSERTION`, `GPL`, vendor / native / federated). That belongs as a *new dimension* of
  the PSH policy — and `PolicyLattice` is now the place to add it, which is most of why it
  exists.

  **Done** (`psh/licensing.py`). It is the first convergence step and it was deliberately
  taken first, because it is also the test of whether the lattice work paid for itself.
  The dimension asks "is this licence acceptable **for this integration mode**", which a
  single allow/deny per licence cannot express: unlicensed code may be *invoked* and may
  not be *copied*. So it is a fixed table — the same shape as `labels.DEFAULT_CEILINGS` —
  plus two subset dimensions a profile narrows.

  The cost of adding it is the number worth recording. Naming it in
  `AuthorityLattice.violations`/`meet`, in `PolicyLattice`, and in the property test's
  dimension list. That is all. `restrict`, `with_`, the meet, delegation and `ToolGateway`
  govern it without being told, because each defers to the one predicate; the
  anti-vacuity test confirmed both new dimensions immediately got real coverage. Before
  v0.5.1 the same change would have meant editing four hand-written comparisons and
  hoping they agreed.

  It did surface one thing: `WorkProfile.freeze()` did not carry the new fields, so a
  profile could declare a licence posture no gate would see. That is the *third* appearance
  of one defect — v0.1 dropped `require_citation` between the profile and the output gate,
  and `freeze()` exists because of it. A hand-written mapping that nothing checks is total
  will keep doing this, so `test_every_profile_field_reaches_the_snapshot` now checks it.
* **Both audit stores, not one.** PSH's hash chain is security truth; BioScience's causal
  event DAG is scientific provenance. They answer different questions and should not be
  flattened into one table.
* **Self-evolution, quarantined.** BioScience's propose → smoke → benchmark → regression →
  promote pipeline is the most forward-looking thing in either package, and under PSH it
  needs one hard boundary: an evolution agent may write `components/`, `skills/`,
  `prompts/`, `planners/` and may **never** write `kernel/`, `authority/`, the policy
  evaluator, the audit chain, the release gate or the sandbox. A component that can modify
  the thing enforcing policy is not governed by it — which is `contracts.py`'s opening
  argument, applied to the agent that edits the repository.

  **Done** (`bioagent.evolution.boundary`). `EvolutionPipeline.submit()` has a `boundary`
  stage that quarantines a proposal whose entrypoint, source path or declared writes land
  in the trusted plane, before its smoke test runs.

* **The bridge itself** — **done** (`bioagent.psh`, BioScience v2.4; design in
  `BioScience-Harness/docs/V24_PSH_CONVERGENCE.md`). A BioScience manifest becomes a PSH
  manifest with the gate dimensions derived conservatively; a call crosses both kernels
  in order; one harness per domain sits at the top of this package's two-level registry;
  `isolate=True` runs components in this kernel's child process. The dependency points one
  way — `bioagent.psh` imports `psh`, never the reverse, and never `psh.kernel` — and this
  package gained nothing but three data-licence ids in `licensing.py`.

BioScience's test suite was reported as 133 passed / 3 failed / 6 skipped; the
container-validation ordering bug was closed in its v2.3.1 before convergence started,
and the BioScience unit tier stands at 543 passed / 2 skipped with the bridge, native-toolkit and dataset tests included.

## 6. Naming the honest state

v0.5.1 is:

> a **policy-enforced control plane** for biomedical scientific agents, with a **bounded,
> governed single-agent loop** on top of it.

It is not a multi-agent operating system, and the row in §1 that reads "Supervisor / worker
pool ❌" is the reason. The target the reviews describe — *typed planner → task graph →
loop controller → supervisor → delegation contract → worker pool → observation → evaluator
→ retry/replan → fan-in → evidence verification → release* — is reachable from here, and
§3 is the order to reach it in. What should not happen is the reverse: claiming the
destination while the middle rows are empty. That is the failure mode both reviews were
written to catch, and it has now cost two releases to correct.

## 7. v0.5.3 — after the third review

The 2026-09-18 review measured the loop against the single pass and found the loop
short: its result never met the release gate, its checkpoints wrote what its envelope
forbade, its tasks could not pass a value to each other except as a blob, its budget did
not see its own tasks, its evaluation accepted "banana", and its retrieval could not read
Chinese. `docs/REVIEW_RESPONSE_2026-09-18.md` is the finding-by-finding record.

The order of work was the review's: the P0 batch (one release path, inherited labels,
redacted checkpoints, Chinese PHI cues), then the execution-closure batch (input
bindings, the budget tree, strict evaluation, the operation ledger, bilingual terms),
then the foundations (the isolation report, the TCM knowledge layer, the doctor). Each
batch landed with its probe reproduced first and its regression tests second.

What §6 said still holds, with one line added: the runtime is a governed loop *whose
deliverable is judged by the same policy as a single pass*, and whose restarts know what
they did. It is still not a multi-tenant system and still ships no OS sandbox.
