# psh — Physician-Scientist Harness

A governed control plane for physician-scientist work. Not a bigger agent: the layer your
agents, tools and harnesses run *through*.

Positioning, stated precisely, because the earlier "Kubernetes for AI agents" framing
promised hard isolation, distributed scheduling and multi-tenancy that this does not have:

> **A policy-enforced control plane for biomedical scientific agents.** Research prototype.

## The design vocabulary learned to name a simulation

> **A change to the kernel's own vocabulary, made for a downstream skill and worth
> understanding on its own.** The clearest example so far of a downstream
> requirement forcing a kernel distinction that should have existed already.

`workflow.ir` previously had one flat `DESIGNS` set, and it had no way to name a
*computational* result. Compiling TCMScience's network-pharmacology skill exposed
the consequence: a docking score or a predicted target relationship had no design
to declare. The nearest available value was `in_vitro` — so a simulation would
have been recorded in the IR as a bench experiment. That is precisely the confusion
the IR exists to prevent, and the IR could not express the distinction needed to
prevent it.

`DESIGNS` is now two named sets:

```python
EVIDENCE_DESIGNS   = {classical_text, expert_consensus, in_vitro, animal,
                      case_report, observational, randomized_trial, systematic_review}
PREDICTIVE_DESIGNS = {in_silico, network_prediction, docking, molecular_dynamics,
                      target_prediction, pathway_enrichment}
DESIGNS            = EVIDENCE_DESIGNS | PREDICTIVE_DESIGNS
```

The split carries weight in `_SUPPORTS`, the claim-support table. Predictive
designs appear in **exactly one row**:

```python
ClaimType.MECHANISTIC: EVIDENCE_DESIGNS - {classical_text, expert_consensus} | PREDICTIVE_DESIGNS
```

A prediction can therefore license a mechanism claim — the entire output of
network pharmacology, and refusing it would make the tool useless — and can never
license `CLINICAL`, `ASSOCIATION` or `SAFETY`. Those are statements about
patients, and a prediction is not an observation of one. `CLINICAL` still accepts
only `randomized_trial`, so there is no row where a prediction and a clinical
claim meet.

This is the kernel enforcing "predictions must not be presented as clinical
facts" through the type system rather than through a prompt instruction — the
difference between a rule the model *should* follow and one it *cannot* break.
958 tests pass unchanged, which is the evidence that widening the vocabulary did
not loosen anything.

---

## v0.5.3 — the third review, closed

A third external review (2026-09-18) probed the merged tree with synthetic data and found
that the governance closed around one `Runner` pass had not closed around the loop.
Twelve findings; `docs/REVIEW_RESPONSE_2026-09-18.md` takes each through reproduction,
fix and test. What changed, in one place:

* **One release path (F01).** `LoopResult` is internal. `Finalizer` takes a loop's
  deliverable through the same quarantine, claim verification, output gate and claim
  commit that `Runner.run` uses, and hands back a `ReleasedResult` whose only text field
  is `released_output`; a refusal carries counts and a category, never the sentence.
  `ResearchRunService` is the application-facing door. A plan-level
  `evidence_requirements` string no longer satisfies a claim-support policy; only a task
  that declares `evidence_required` does, because only that is checked.
* **Labels are inherited (F02).** The runner's quarantined output is labelled with the
  join of the projection, the broker's result and a fresh scan; a count derived from a
  PHI chart is PHI.
* **Checkpoints are durable writes (F03).** A run that may not persist writes the plan's
  shape and none of its words: objective, task text, payloads and error texts are
  withheld, the record says `redacted=True`, and resuming it needs the objective and the
  plan supplied again with the same shape. The record carries the run's consumption.
* **Chinese PHI cues (F04).** The fallback classifier recognises 住院号 / 病案号, 身份证号,
  手机号, 出生日期, 住址 and 患者姓名 forms; a clinical origin floors the label at PHI
  whatever the language; `require_validated_classifier` refuses to start on the fallback.
* **Input bindings (F05).** A step fills an argument from an upstream result by JSON
  pointer, typed and labelled; a payload literal that reads like a reference is a plan
  error that names the binding it should have been.
* **The budget tree (F06).** Consumption is charged to the run and every ancestor; a call
  is refused at any level's ceiling; the loop's bounds check reserves nothing.
* **Strict evaluation (F07).** The schema check refuses a boolean as an integer and an
  unknown keyword; a manual criterion nobody judged is `pending_manual`, not verified,
  and a claim-support policy refuses an unverified goal before the gate. A modal no
  longer becomes a claim's subject.
* **Isolation as a report (F08).** `IsolationReport` states what the runner confines;
  `require_os_isolation` is a lattice requirement the kernel refuses when it cannot meet.
* **An operation ledger (F09).** Every tool call is recorded durably by its idempotency
  key; a side-effecting component whose earlier attempt is in doubt is not re-run. The
  bridge hands the key to BioScience's runtime and never to the entrypoint or the wire.
* **Bilingual retrieval (F10).** Capability resolution and memory recall tokenise
  Chinese through a checked-in lexicon with bigram fallback.
* **Outcomes and shortfalls (F12).** Tool outcomes feed the registry's prior; a degraded
  result carries its caveat to the release as a limitation; a timeout is `ToolTimeout`.

```bash
python -m pytest tests/ -q          # 636 pass
```

## v0.5.1 — gate composition closure

A second review accepted the v0.5 premise and attacked the next layer: what happens where
two controls meet. Seven of its findings were real, two of them P0. They share a shape, and
it is not the v0.5 shape:

> v0.5 — *the mechanism exists and the main path does not call it.*
>
> v0.5.1 — **the mechanism exists, is called, and a second, shorter copy of it was
> hand-written somewhere else.**

| Closed | What was wrong |
| --- | --- |
| **One policy ceiling** (P0) | `Runner.run(policy=…)` minted the run envelope from whatever snapshot the caller passed, never compared to `TrustedKernel.policy`. A kernel forbidding public providers executed a run that reached one and released the output. A per-run policy is now refused unless the kernel's contains it (`clamp_policy=True` meets instead), by a new `PolicyLattice`. |
| **All destinations, not `[0]`** (P0) | `ToolGateway` gated on `manifest.destinations[0]`, so `PHI` + `(LOCAL_COMPUTE, PUBLIC_REMOTE)` was allowed — while `IsolatedExecutor` read the *same* tuple with `any(…)`, saw `PUBLIC_REMOTE` and opened the component's network reach. Every destination is checked; the decision names the most exposed one; approval records list all of them. |
| **Delegation uses the lattice** | `DelegationGateway` hand-checked four of the lattice's fourteen dimensions, so an `R1`/`SUGGEST` parent could delegate `R4_KERNEL`/`ACT`/unrestricted/999× budget and get `allowed=True`. It calls `AuthorityLattice.violations` now. |
| **Policy narrowing is a lattice** | `with_()` was documented as refusing any widening and compared three of twelve dimensions. `SUGGEST→ACT`, `require_isolated_tools True→False` and `tokens_hard 10→999` were all accepted. |
| **The sandbox contains the workdir** | `manifest.id` was validated only for being non-empty and was joined onto the sandbox root, so `id="../../escaped"` put the child's cwd outside it and an absolute id discarded the root. Ids are one path component, and containment is re-checked after resolution. |
| **Timeout kills the process group** | `start_new_session=True` created a group that nothing ever signalled: a grandchild outlived the timeout by seconds and went on writing files. `killpg` is sent now. |
| **Security walkers fail closed** | Past depth 8 the path walker returned `[]`, so nesting a payload deep enough made the filesystem boundary disappear. A walk that hit its cap now refuses, as `labels.walk_values` already did. |
| **Split brain closed** | The run envelope came from one policy while `OutputGate`, `broker.require_isolation` and `PersistenceGateway` came from another. `require_isolated_tools` travels on the envelope; the gate and the store take per-call ceilings that can only tighten. |

Also: `requires_network` validation was a chained comparison that could never be true;
`StuckLoop` counted every run of a `Runner` in one bucket and refused three identical
top-level requests; `min_autonomy` was declared and never read (and defaulted to the
strongest requirement); an isolated component breaking the stdout JSON protocol was
silently accepted. See `docs/V5_1_GATE_COMPOSITION_CLOSURE.md`.

The delegation property test that should have caught the lattice bypass was **vacuous**:
its child widened every dimension at once and `tokens_hard = 10**9` always exceeded the
parent strategy's `100_000`, so the first budget comparison refused every example and no
other dimension was ever the reason for a verdict. It is now one dimension at a time, with
a test asserting the properties actually reach their assertions — which immediately found a
dimension with zero coverage.

## v0.5.1 — a bounded agent loop, through the kernel

The same release adds the component both reviews named as the most valuable next one, under
the rule they both stated: **the loop must not bypass the trusted kernel.**

```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            typed Plan / PlanTask / TestSpec / RetryPolicy
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph — transient, deliberately NOT the WorkGraph
├── evaluator.py       structural / execution / evidence / goal
└── loop.py            AgentLoopController — plan, act, observe, evaluate, bounded
```

`validate_plan` used to record `"placeholder planner: no typed plan to validate"`, which was
honest and was also why the stage could enforce nothing: there is no dimension of
`"summarise the HFpEF evidence"` to compare against a risk ceiling. A plan is typed now and
each task carries the authority it intends to use, so a plan whose fourth step needs a
destination the run forbids is refused *at the plan*, not discovered at step four with three
steps' side effects already committed.

The loop holds no provider, no subprocess and no socket. It acts through exactly three
broker calls, and that is checked three ways: the dispatch has three branches; a test
balances the broker's counters against what the loop did; and a test parses `loop.py` and
fails if it ever imports a transport or a process spawner — because a behavioural test sees
the paths a test took, not the paths that exist, which is exactly how `IsolatedRunner`
shipped in v0.4 while `call_tool` ran everything in process.

Termination is a controller rather than a repeat counter: `goal_satisfied`, `max_iterations`,
`budget_exhausted`, `deadline`, `no_progress`, `max_replans`, `plan_rejected`,
`policy_denied`, `escalated`, `unrecoverable_error` — always named, never inferred. Retries
are per task and are charged to the same budget as the first attempt; a policy *refusal* is
never retried, because it is an answer rather than a fault.

What this was **not**, when it landed: a multi-agent runtime. No supervisor, no worker
pool, no fan-out and no parallelism — `docs/ROADMAP_AGENT_RUNTIME.md` said so in a table
rather than in prose, and the sections below add each of them in turn. Gates first, then
iteration — a loop multiplies whatever the gates get wrong.

### Licence provenance — the first BioScience convergence step

`psh/licensing.py` adds the one dimension where BioScience-Harness's policy model is
stronger than this one: whether a capability's licence permits the **way** it is
integrated. The same licence gives different answers —

```
vendor      copy the upstream implementation in     -> redistribution
native      call an independent equivalent          -> no upstream code at all
federated   invoke upstream in its own process      -> use, not redistribution
```

— so unlicensed code may be invoked and may not be copied, which a single allow/deny per
licence cannot say. A fixed table states what is permissible in principle (the same shape
as `labels.DEFAULT_CEILINGS`), and a profile narrows which classes and modes it permits at
all; `coding` does not permit vendoring, because that is the mode that produces code the
group then distributes.

It is also the test of whether the lattice work paid for itself. Adding a governed
dimension cost naming it in `AuthorityLattice`, `PolicyLattice` and the property test's
dimension list — `restrict`, `with_`, the meet, delegation and `ToolGateway` govern it
without being told.

### A planner that writes the plan

`psh/runtime/planner.py` closes the last placeholder. `ModelPlanner` asks a model for a
typed `Plan`, through `ExecutionBroker` like any other model call, parses it strictly, and
feeds each refusal back so the next attempt can correct — bounded, because re-asking a
model forever is a loop rather than a correction.

The property worth stating: **a plan is untrusted input.** Its output is not an answer to
be checked but a program to be run, so a model that proposes a task holding
`PUBLIC_REMOTE` under a local-only run does not get it — `PlanValidator` refuses the plan
and the refusal becomes the next prompt. If a model could widen a run by writing a wider
plan, every control here would be reachable by asking for it.

### Parallel branches, and schemas the planner can act on

Two properties a frontier runtime has and the v0.5.1 loop did not. An iteration's ready
set is independent by construction — every dependency of each task has already
succeeded — so with `LoopLimits(max_parallel=n)` it runs on a bounded pool of threads.
Nothing about the governance changes: every branch is still one of the three broker
calls, the gates and the budget governor are locked (the counters that tests and audits
read now take a lock too), a refusal in one branch is that branch's failure and its
descendants' block, and a bound one branch hits — an approval nobody can grant, a budget
— surfaces as the loop's termination after the branches already in flight have recorded
their own outcome. Sequential stays the default, because it is the easier behaviour to
reason about.

And the registry's disclosure gained its second level. `manifest_items` shows enough to
*choose* a capability; `schema_items` shows enough to *call* one — a connector's
operations with their arguments and an example payload, a native tool's parameters, or
the property names of a declared JSON schema — for the few candidates that survived
ranking, from the manifest and never from an invocation. `ModelPlanner` puts them in the
planning prompt, whose rules now say what a `payload` is, so a model can write
`{"operation": "symbol", "symbol": "TP53"}` instead of guessing. Each item carries the
manifest's description label, because a schema that arrived from an MCP server or a
catalogue is text this kernel did not write. `tests/test_parallel_and_schemas.py`.

### Memory the run may recall, and delegation the planner is told about

The WorkGraph was already project memory with a governed write side: every node passes
`PersistenceGateway`, a claim is committed only after the release gate has ruled, and a
refused claim survives as a hash and a reason. What was missing was a read side that kept
those properties. `psh/context/memory.py` is it. `MemoryRetriever` reads nodes whose
`validation_status` is `verified` or `system` — a candidate is not trusted memory unless
an operator says so, and a rejected claim is excluded whatever the caller asks — ranks
them lexically against the objective, and returns `ContextItem(kind="memory")` carrying
the label the gateway stored, so a memory of PHI is PHI whatever its text looks like. Two
things are withheld at retrieval rather than left to the compiler: memory above the run's
own ceiling, and memory the model's destination may not receive. The second matters for
the loop, which refuses to run a task whose *upstream result* the destination may not see;
memory is optional context, so it is withheld and the task runs with a quieter prompt,
and the trace says so. Retrieval is scoped to the envelope's project — no project, no
memory — and it is a read: the module holds no path to `add`, `update` or `commit_node`,
and a structural test keeps it that way. `ModelPlanner(memory=…)`,
`AgentLoopController(memory=…)` and `Runner(memory=…)` compile what it returns into
their projections, where the label joins like any other item's. `tests/test_memory.py`.

The roadmap's last yellow cell was that a plan *could* carry `kind="delegate"` tasks while
the planner was never told whether the loop had a backend to run them; a model that
guessed wrong got a `ContractViolation` at dispatch after the rest of the plan had spent
budget. The loop now states the fact on `LoopState.can_delegate`, the authority brief
says either "available: up to N child agent(s)" or "not available in this loop", the
prompt's rules say what a delegated objective must be, and a delegate task without a
backend is a refusal the planner feeds back for correction rather than a crash.
`tests/test_delegation_disclosure.py`.

### Checkpoint and resume, with one rule

`Runner`'s `checkpoint` stage wrote an audit event, so "checkpoint" named a record of
having finished rather than a state a run could continue from. `psh/runtime/checkpoint.py`
is the real thing, and its design is one sentence:

> **A resumed run re-meets its authority against the policy in force *now*.**

Restoring the envelope a run held is the obvious implementation and it is a hole. An
envelope is a grant, and a grant that outlives the policy that issued it is a capability
the kernel never agreed to — that is P0-1 arriving through a file instead of a keyword
argument. So resuming computes `AuthorityLattice.meet(stored, current_ceiling)`: never
wider than it was, never wider than today's policy, with every narrowed dimension named in
an audit event. Unfinished tasks are re-authorised one at a time and the resume is refused
if the policy no longer admits one; finished tasks are history and are not re-checked.

### Compaction: what is left out is summarised and said

The compiler filled its token budget by rank and discarded the rest, reporting a count. A
count tells the *caller* something was omitted and tells the model — the party that has to
answer around the gap — nothing at all. `psh/context/compaction.py` summarises the overflow
instead and shadows it, DeepSeek Harness's shape.

The property that made this a kernel concern rather than a utility:

> **A summary carries the join of the labels it summarises.**

`DEFAULT_SYSTEM_PROMPT` already tells the model "a summary of identifiable content is still
identifiable". That has to hold by construction. Deriving the label from the summary *text*
would mean a summary of PHI whose extract happened to omit the identifiers classified as
`INTERNAL` — measurably, in `test_the_label_comes_from_the_inputs_not_the_summary_text` —
and became permitted at a destination its sources could never reach. Laundering by
accident. So the label comes from the inputs, and a summary that may not reach the
destination is replaced by a bare count, which carries no content.

### Multi-agent: a supervisor that can only narrow

`psh/runtime/supervisor.py` and `subagent.py` add fan-out under one rule:

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

`Supervisor.mint()` turns a request into arguments for `parent.restrict()` — the authority
lattice — and holds no comparison of its own; a structural test parses it and fails if one
appears. Children are child loops under the contract's envelope and return a
`SubagentResult` with no field for a transcript: claims, evidence, artifacts, a summary, and
a label that is the join of everything the child saw. A `WorkerPool` runs them concurrently
over the kernel, which is now locked for it — `BudgetGovernor`'s check-and-increment and the
broker's counters, the latter being the proof nothing bypassed the broker and therefore the
thing that must survive threads. Fan-in is an `AggregatedObservation` in which two children
disagreeing is a recorded `Conflict`, not two paragraphs concatenated.

It found one defect worth naming: `Budget.child()` bounded one child and its docstring
claimed it stopped fan-out multiplying a budget. Ten quarter-children are two and a half
parents. `BudgetLedger` sums fractions per parent, cumulatively.

### Durability: a child that stops answering is given up on, not waited for

Measured first: a child whose tool never returned left the parent's `wait()` blocked
forever, and nothing recorded that it was stuck. Every child now holds a `WorkerLease` that
the loop renews before every task; `reap()` marks a silent child `STALLED`, cancels its
token, and the parent moves on. The thread is leaked knowingly — an in-process call that
never returns cannot be interrupted, and claiming otherwise would be the process-group
defect in another costume.

Leaking it found a second defect: the stalled child mid-append on the shared event store
while `kernel.close()` closed the connection from another thread was a **segmentation
fault**. `EventStore.close()` takes the write lock now, and later appends refuse cleanly.

Idempotency keys — `"<loop run id>:<task id>"`, stable across retries and across a resume
— let a side-effecting component recognise the replay that `resume()` deliberately creates.

### Interoperability: remote tools and agents, on the operator's terms

`psh/protocols/mcp.py` and `a2a.py` admit MCP tools and A2A agents as components. MCP's
own documentation says its annotations are hints, not security guarantees; A2A's samples say
an `AgentCard` is untrusted. Both adapters take the protocols at their word — annotations
may tighten and never loosen, the destination class is the operator's, a card's `url` must
fall inside the operator's allowed hosts, and `input-required` is escalated rather than
answered — and the consequence is that **neither added a gate**. PHI to an MCP tool on a
public server is refused by the same `ToolGateway` check that refuses PHI to a public model;
a remote agent passes `DelegationGateway` for its authority and `ToolGateway` for its data.

It found a defect older than either adapter: `manifest_items` rendered every description
into the model's context with the default `PUBLIC` label, so a server-supplied description
carrying PHI would have reached a public model. Descriptions are classified at ingress now
and the rendered item carries that label.

### Labels travel across the loop — an adversarial review of the runtime

The runtime was reviewed after it was written: candidate findings, an independent
false-positive filter per finding, a cut at confidence 8. Two survived, both reproduced,
both the same shape — the loop went *through* the kernel and handed it the wrong label. A
tool result labelled PHI reached the next model task as PUBLIC evidence, because the label
was recorded on the node and never carried into the prompt; the objective and the
planner's feedback were never classified at all, so a PHI objective reached a public
planner under a PUBLIC verdict while `LoopResult.label` correctly said PHI.

Labels travel now — with results into the next projection and payload, with the objective
onto every item built from it, with a model-authored plan onto every task objective, with
feedback onto what it quotes, with a delegation onto the child. And the kernel no longer
trusts a projection: the broker classifies the rendered text and joins, so a projection can
be escalated at that boundary and never trusted downward, and both gateways enforce the
run's ceiling, which only `Runner._preflight` had compared before. Checkpoints obey the
persistence rules — a result the gateway would refuse is withheld and the task re-runs —
and a record without a hash is refused. `docs/RUNTIME_SECURITY_REVIEW.md` has the report,
the two findings that were filtered out, and what was done about them anyway.

### Convergence: BioScience's capability plane, under this kernel

The seam the roadmap described is built, on the other side of it: `bioagent.psh` in
BioScience-Harness v2.4 admits BioScience components — the 2,567-row catalogue and 56
live-verified public biomedical sources — as components of this kernel, with the gate
dimensions derived conservatively from what each declares, one harness per domain in the
two-level registry, and an isolated mode that runs them in this kernel's child process.
A call crosses both kernels in order and neither can be skipped. This package gained
three data-licence ids in `licensing.py` and nothing else: the dependency points one way.
`BioScience-Harness/docs/V24_PSH_CONVERGENCE.md` has the design, the evidence and the
honest state; `BioScience-Harness/demo_convergence.py` runs a plan through both.

```bash
python -m pytest tests/ -q          # 636 pass
python -m compileall -q src         # clean
```

## v0.5 — enforcement closure

No new subsystems. This release closes the gap an external review measured between what the
package *implements* and what its *executing path* uses. The defects shared one shape — a
control that exists, a main path that does not call it, and a README describing the
control — and each is now closed with a test that drives the main path:

| Closed | What was wrong |
| --- | --- |
| **Policy is a ceiling** | `PolicySnapshot.envelope()` read `kw.pop("risk", self.risk_ceiling)`, so the ceiling applied only to callers who declined to state a value. `R2`/`SUGGEST` policies minted `R4`/`ACT` envelopes on request. Every dimension is now checked against the ceiling by `AuthorityLattice`; widening raises, `clamp=True` narrows instead. |
| **One minting path** | `TrustedKernel.envelope()` never consulted `self.policy` at all — it minted from hard-coded defaults (`PHI`, `ACT_WITH_APPROVAL`, four destinations). It now delegates to the policy, so a `peer_review` kernel cannot hand out a network envelope. |
| **Isolation on the real path** | `IsolatedRunner` shipped in v0.4 and `ExecutionBroker.call_tool` ran every component with `component.invoke(...)` *inside the kernel process* — a tool could read `os.environ` and open its own socket. Components declaring `backend="subprocess"` now run through the runner; the two paths are counted separately, the audit event records which ran, and `require_isolated_tools` refuses the in-process one outright. |
| **Approvals key on the action** | A session approval was keyed on the string `"run shell"`, so approving `git push origin main` for the session also pre-approved `curl … | sh`. The key is now a digest over request kind, component, normalised argv (or payload digest), targets and risk. |
| **Grants cannot unset the boundary** | `build_child_environment` wrote the proxy variables and then applied caller grants over them, so `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` disabled the egress boundary from inside. Kernel-reserved names are applied last and refused as grants. |
| **No DNS rebinding, ports are capabilities** | The proxy validated a hostname and then handed the *name* to `create_connection`, which resolved it again. It now connects to the addresses the decision vetted, refuses names that do not resolve, treats `host` as ports 80/443 (`host:8443`, `host:*` to say otherwise), and derives the forwarded `Host:` from the vetted URI instead of the client's header. |
| **Filesystem containment after resolution** | Path checks were `fnmatch` over the payload's own string, so `/allowed/../secret` and a symlink out of `/allowed` both passed. Paths are now resolved (`..`, symlinks, `~`) and must land inside an allowed root; nested payloads are walked. |
| **External hooks are contained** | A `PreToolUse` command hook received the fully unwrapped payload and ran under a bare `subprocess.run` — PHI reaching a third-party process with unsupervised network access, through a door the broker does not watch. External hooks now run through the same isolated runner (no network by default) and receive a redacted view; in-process hooks are trusted and unchanged. |
| **The output gate is not English-only** | Clinical assertions in Chinese and reported statistics (`AUC`, odds ratio, `p < 0.001`, 敏感性/死亡率) were invisible to the gate, and CJK text split into a single "sentence". Both are recognised now. |

Also: `TrustedKernel.close()` releases the WorkGraph connection as well as the event store;
`hypothesis` is a declared test dependency (the authority property tests silently skipped
without it); `live` tests are deselected by default in `pyproject.toml` rather than by
remembering a flag; AppleDouble `._*` sidecars are gone from the tree and ignored (they
contain NUL bytes and break `python -m compileall`).

Run tests:

```bash
python -m pytest tests/ -q          # `-m live` opts into network tests
python -m compileall -q src         # clean
```

## What is enforced, and what is not

Enforced, with a test that drives the executing path:

* no value reaches a gateway unclassified, and a caller-supplied label is re-validated —
  a compiled projection included, whose rendered text the broker classifies and joins;
* nothing above a run's ceiling reaches a model or a tool, at the gate rather than in one
  caller's preflight;
* no envelope is wider than the policy that minted it, on any of the lattice's dimensions;
* every model call, tool call and delegation passes the broker, which records an event;
* output is quarantined and `released_output` stays `None` unless the release gate passed —
  for a loop's deliverable exactly as for a single pass, through one `Finalizer`;
* a checkpoint of a run that may not persist holds the plan's shape and none of its text;
* a side-effecting tool whose earlier attempt is in doubt is not re-run;
* a policy requirement this process cannot meet (the validated detector, OS isolation)
  refuses the kernel or the run before anything executes;
* a `backend="subprocess"` component cannot read the kernel's environment.

**Not** enforced, stated plainly:

* a `backend="python"` component runs in the kernel process and is confined by nothing.
  This is the honest boundary: `require_isolated_tools=True` is how a policy refuses it.
* the egress proxy governs clients that honour proxy variables. A raw socket bypasses it.
  `SandboxBackend` is the seam for the OS layer (Seatbelt, bubblewrap+seccomp); only
  `NoSandbox` ships, and it says so in `describe()` and in the kernel's `IsolationReport`.
* the audit chain is tamper-evident, not tamper-proof; classification is a safety net, not
  certified de-identification; claim support is lexical; the planner is a placeholder.

## Install

```bash
pip install -e .            # standalone; classification degrades and says so
pip install -e .[test]      # pytest + hypothesis
```

Run tests with `PYTHONPATH=src` (or `PYTHONPATH=../sable_pkg/src:src` for the composed
configuration) — editable installs may not survive a session restart.

## Use

```python
from psh import get_profile
from psh.kernel import TrustedKernel
from psh.runtime import Runner

policy = get_profile("clinical_research").freeze()   # local only, PHI ceiling
kernel = TrustedKernel(policy=policy)
runner = Runner(kernel, model=local_model, model_invoke=my_provider, policy=policy)

result = runner.run("Summarise the HFpEF evidence", sources={"34449189": abstract})
if result.released_output is None:
    print("refused:", result.error, "| quarantined as", result.quarantine_ref)
```

The policy is a ceiling: `runner.run(..., risk=RiskTier.R4_KERNEL)` under this profile is
refused at the `policy_snapshot` stage, not honoured. Ask for less than the profile grants
and you get it; ask for more and you get a `PolicyDenied` naming the dimension.

That applies to the *policy* as well as to the envelope. `runner.run(..., policy=wider)` is
refused, because until v0.5.1 it was the way to mint a run the kernel's own policy forbade:

```python
runner.run("…", policy=broader)              # PolicyDenied, naming each dimension
Runner(kernel, policy=broader, clamp_policy=True)   # narrowed to the meet instead
runner.run("…", policy=policy.with_(autonomy=Autonomy.OBSERVE))   # narrowing: honoured
```

`clinical_research` is deliberately unusable without a local model. That is the profile
working as intended.

## Earlier releases

* **v0.5** — enforcement closure: nine controls moved onto the path that actually
  executes. Policy became a real ceiling at minting, isolation reached `call_tool`,
  approvals keyed on the action, filesystem checks resolved before comparing.
* **v0.4** — six enforcement patterns adopted from Codex (read from source) and Claude Code
  (official docs), attributed per pattern in `PATTERN_ATTRIBUTION.md`: declarative
  self-testing execution policy, approval-to-rule amendments, absolute-deny evaluation
  order, the shared hook JSON protocol, default-deny child environments, a kernel-owned
  egress proxy refusing private ranges even when allowlisted.
* **v0.2** — sixteen externally-found defects closed as invariants: mandatory ingress, deep
  labelling, one authority predicate, release before exposure, classified persistence,
  evidence provenance, real token/cost accounting.

## Scope

Research prototype. Not clinical-safe, not production-ready.

`docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the review items this release closes, each with
its reproduction, and the ones it deliberately leaves open.
`docs/V5_ENFORCEMENT_CLOSURE.md` — the previous round.
`docs/ROADMAP_AGENT_RUNTIME.md` — what this is *not* yet: a governed agent runtime. The
planner is a placeholder, `Runner.run` is a single pass rather than a loop, and delegation
is a primitive rather than an orchestrator. That roadmap is deliberately separate from the
enforcement work, because adding a loop before the gates compose correctly multiplies
whatever the gates get wrong.

MIT licensed.
