# Bounded dynamic workflows and stage-event replay

Applications should use `psh.workflow.DynamicResearchRunService` to execute
caller-authored graphs of `ScientificProgram` stages and obtain gated output.
`DynamicWorkflowController` and `DynamicResult` are internal execution interfaces,
retained as imports for compatibility. This is a bounded dynamic increment, not
the complete ZCode dynamic-workflow engine or a replacement for task checkpoints.

## Execution model

- `WorkflowStage` contains a scientific program and either `next_stage` or a
  `Branch`. `None` is the terminal edge.
- `Branch` reads a task result in the just-completed stage, optionally through
  the existing runtime JSON-pointer resolver. A type-strict JSON-scalar equality
  selects `if_true` or `if_false`. No `eval`, model-authored Python or routing
  callback is executed. Missing paths fail routing instead of silently choosing.
  Model results are strings; JSON text is **not** implicitly parsed into objects.
- Cycles are explicit. `max_visits` bounds total stage executions (1–1024),
  including repeats. A graph holds at most 256 stages. Each stage also obeys
  `LoopLimits`; implicit replanning is disabled. Cancellation is cooperative.
- Each stage remains an ordinary DAG: existing bounded independent-task
  concurrency and dependency joins work via `LoopLimits(max_parallel=...)`.
  Dynamic map expansion, live graph amendment and
  model generation of dynamic graphs are not implemented by this adapter.
- Stage inputs can pass selected fields from the immediately preceding successful
  visit into tool arguments. They do not read an arbitrary historical stage or
  inject context into model/delegate tasks; see the binding example below.
- The input graph is JSON-snapshotted before execution. All declared stages,
  even unselected ones, pass scientific preflight before dispatch. Selected
  stages compile again against current policy. Registered-protocol checks use
  the supplied `scientific_ledger`, as with `ScientificPlanner`.
- All visits retain the original envelope's run ID, budget and deadline. Tool
  operation keys gain a workflow-instance/visit namespace, so two intentionally
  repeated stage visits do not collide in `OperationLedger`. A repeated visit is
  a **new invocation**, not a retry or cache hit. Cyclic tool stages now also pass
  the repeat-safety checks below. Existing task retry restrictions remain.

## Cycle admission and changing capability facts

Before any stage dispatch or journal start, a Tarjan strongly-connected-component
pass identifies every declared cycle, including self-loops and both branch edges.
Declared unreachable cycles are included, even when `max_visits=1`; the pass is
deliberately conservative and does not try to prove predicates impossible.

For each tool task in a cyclic stage:

- `NON_REPEATABLE`, `AT_MOST_ONCE` and `COMPENSATABLE` contracts are rejected
  (`REPEAT101`). No compensation executor or per-visit approval exception exists.
- The registry manifest **and** the currently invocable component must report
  exactly `idempotent=True` (`REPEAT102`). A planner's PURE/IDEMPOTENT declaration
  is insufficient. Missing or merely truthy facts do not grant permission.
- `PURE`, `IDEMPOTENT` or `AT_LEAST_ONCE` contracts may pass only with that trusted
  fact and all existing scientific compilation, authority and budget checks.

Delegation in cycles is rejected (`REPEAT103`): this controller has no repeat-safety
attestation for a delegated workflow. Ordinary model stages remain available in
bounded cycles under model budget/egress gates; this does not declare model calls
pure, reproducible or free of provider-side effects.

Checks run again before every cyclic stage visit. If capability facts no longer
permit repetition, the controller writes `ended(reason="repeat_refused")` before
starting that visit. During a cyclic stage, the existing loop also requires the
actual component's idempotency fact at each tool dispatch, so a downgrade between
two tasks cannot authorize the later tool just because preflight passed earlier.
A within-stage refusal produces the ordinary `stage_failed` outcome. These checks
assume trusted host-controlled registries; they are not an atomic registry version
pin or protection against malicious concurrent component replacement.

Nonrepeatable tools in acyclic stages still execute once normally. This pass does
not detect manually unrolled duplicate operations in separate acyclic stages or
different runs, and it does not prove a trusted manifest truthful. Idempotency
alone does not prove purity, clinical suitability or safety for different payloads.
There is no bypass flag for unsafe cyclic tools in this release. Future approved
repetition requires explicit host authority, bounded repeat policy and durable
effect tracking, not an extra model-authored field.

New `repeat_refused` events require updated readers; older readers fail closed on
the unknown reason. Existing accepted history and workflow fingerprints are not
rewritten. Some previously accepted cyclic programs are now intentionally refused.

## Usage

Here `screen_program` and `review_program` are previously constructed
`ScientificProgram` objects; `screen_program` includes a task named `decision`.

```python
from psh.workflow import (
    Branch, WorkflowStage, DynamicWorkflow, DynamicResearchRunService,
    RunEventJournal,
)

workflow = DynamicWorkflow(
    stages=(
        WorkflowStage("screen", screen_program,
            branch=Branch("decision", "review", "review", None)),
        WorkflowStage("review", review_program),
    ),
    entry="screen",
    max_visits=4,
)

# Use a NEW database for each execution. The path is chosen by the application,
# never by model-generated program content.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    service = DynamicResearchRunService(
        kernel, journal=journal, registry=registry,
        scientific_ledger=scientific_ledger,
        model=local_model_profile, model_invoke=invoke_local_model,
        operations=operation_ledger,
    )
    result = service.run(workflow, envelope=envelope, sources=authorized_sources)
    if result.ok:
        print(result.released_output)  # only this field carries released text
    anchor = journal.anchor()  # retain separately for suffix-loss detection

# Inspection after reopening: this does NOT invoke any model or tool.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    state = journal.replay(expected_anchor=anchor,
                           fingerprint=workflow.fingerprint)
    print(state.phase, state.visits, state.reason, state.in_doubt)
```

`authorized_sources` is a caller-supplied mapping of citation identifiers to
retrieved evidence records; `{}` is valid when no evidence is supplied. This service
revalidates records and passes only trusted records to the finalizer. Bare text and
unsigned/tampered records cannot serve as release evidence through this entry.
This is stricter than the shared output gate's legacy provenance-caveat allowance.

## Application release boundary

`DynamicResearchRunService.run()` returns `ReleasedResult`, never `DynamicResult`
or stage result dictionaries. It runs the existing Finalizer: quarantine, evidence
verification, OutputGate, release and optional verified-claim persistence. Only
the final visited stage's terminal tasks are rendered as the candidate. There is
no extra model synthesis call; authors must explicitly provide a final reporting
stage when earlier findings need synthesis.

All visited stages must complete and have verified goal status before release.
The candidate inherits sensitivity from the whole observed execution, even if
intermediate payloads are omitted. Intermediate caveats become generic limitations
without raw task IDs or warning text. Service policy is intersected with current
kernel policy both for execution and for release; the original envelope is never
widened. A journal that says `completed` describes **execution**, not publication.
The existing quarantine/release audit records the separate publication outcome.

Failures return structural refusal metadata and no output; refused citation IDs
and raw exception messages are withheld. The service exposes no `last_result`
cache that could accidentally return a previous run's body. Metadata labels retain
sensitivity without caller-supplied rationale text. A result whose status is not
`released` must not be treated as a deliverable.

This is a safer application API, not a Python sandbox: trusted host code can still
import the low-level controller or inspect private objects and is responsible for
not publishing them. The existing finalizer's quarantine storage, evidence checker,
claim extraction and persistence semantics are reused, not redesigned here. There
is no automatic resume, additional scientific truth attestation or exactly-once
release/commit transaction.

Persistence must be authorized by the original envelope **and** current policy.
The store's ceiling is also enforced. The controller conservatively propagates
declared and classified workflow sensitivity, registered-protocol sensitivity,
caller `input_label`, and all completed stage result labels. Routing indices
are derived information, not automatically public metadata. If a newly sensitive
result cannot be persisted, the controller stops without writing a branch or
dispatching another stage; the journal may deliberately remain `running`.
The journal adapter itself is a trusted low-level storage port, like the existing
checkpoint adapter, not a public persistence gateway. Its readers require
filesystem access appropriate to the run. There is no encryption/key management.

## Stage-to-stage inputs

`StageInput` pairs a target tool task ID with the runtime's existing `InputBinding`.
Here `fetch_program` produces task `fetch`, whose result contains `hits`; the
`analyze` tool task in `analysis_program` receives those values as `records`:

```python
from psh.runtime import InputBinding
from psh.workflow import StageInput

workflow = DynamicWorkflow(
    stages=(
        WorkflowStage("fetch_stage", fetch_program, next_stage="analysis_stage"),
        WorkflowStage("analysis_stage", analysis_program, inputs=(
            StageInput("analyze", InputBinding(
                argument="records", source="fetch", pointer="/hits",
                expected_type="object", cardinality="many",
            )),
        )),
    ),
    entry="fetch_stage",
)
```

The source task is resolved in the **immediately preceding successful visit**,
not the target DAG. The same task ID may appear in both stages. On a self-loop,
the second visit consumes the first visit's result, not the initial seed forever.
An entry stage cannot declare inputs because it has no preceding visit. Every
incoming predecessor must contain each declared source task; unknown sources,
duplicate target arguments, literal/intra-stage-input conflicts and runtime
reserved arguments (`upstream`, `_psh_*`) are rejected during graph construction.

All inputs for a stage resolve before its intent event or any target task runs.
Pointers, types and cardinality use the same resolver as intra-stage bindings:
`cardinality="many"` checks each element against `expected_type`. Optional missing
paths (`required=False`) omit the argument; present values of the wrong type
still fail. JSON text is not automatically parsed. Only JSON-compatible selected
values are copied into payloads; nonfinite numbers, non-string object keys and
custom objects are refused, rather than stringified. A copied payload never
shares mutable nested values with the previous stage's result.

The instantiated scientific program is compiled again before dispatch. Its
known sensitivity floor includes all previously observed data, even if the
selected field is only an innocuous-looking count. Selecting a smaller field
is not declassification. Tool invocation still uses the broker and current
policy, the shared run budget and operation ledger.

Binding failure records `ended(reason="binding_failed")` while the next stage
is still `ready`; that stage has not started and does not increase visit count.
No values, field names or raw binding exceptions are copied into the event log.
Readers from before this increment reject the new termination reason; upgrade
readers before reading new histories. Workflows without inputs keep their
previous serialized shape and fingerprint. Histories do not contain bound
payloads and still cannot restore intermediate results or automatically resume.

## Replay semantics and failure boundaries

Events are `started`, `stage_started`, `stage_finished`, `routed`, `ended`.
The pure reducer validates schema, ordering, stage indices, visit counts and
terminal conditions. Replay rebuilds stage control state, not task outputs or
scientific evidence. Only structural indices/status and a workflow fingerprint
are persisted; no prompt, result, branch comparison literal or error text.

SQLite WAL/FULL commits each event atomically. `BEGIN IMMEDIATE` plus an expected
sequence prevents concurrent writers from advancing the same state. The hash
chain detects altered/interior-deleted events; an external anchor detects suffix
loss. Hashes are not signatures and cannot defeat full database rewriting.
Every read/append verifies history, currently O(events), appropriate to the
bounded first implementation rather than a high-throughput streaming journal.

The intent event commits **before** a stage runs. If the process dies after that
commit but before stage completion commits, replay reports `in_doubt=True`.
It does not guess whether a tool or model ran. Failure to write an event stops
execution; unlike best-effort audit logging, it never silently falls back to RAM.
A completed stage whose route was not recorded remains in `routing`.

`run()` rejects every nonempty journal, including completed histories. There is
no automatic resume/redispatch from these events, no persisted result cache, no
exactly-once guarantee, and no atomic transaction with external tool effects.
Use the existing operation ledger for effect investigation and the existing
governed checkpoint APIs for their separate task-level recovery workflow.
This journal is authoritative for **stage scheduling history only**, not a
universal replacement for audit logs, task checkpoints or operation records.

`DynamicResult.ok` means all visited stages met their loop completion criteria
and a terminal edge was reached. It does not certify scientific truth or clinical
validity. Per-stage goal status and caveats remain on `result.stages`.

## Validation

`tests/test_dynamic_workflow.py` covers both branch choices, conditional and
bounded cycles, preflight rejection, persistent reopen/replay, crash windows,
current-policy and sensitivity checks, shared budget exhaustion, distinct
repeatable operation identities, cancellation, caller mutation isolation,
invalid transitions, stale writers, corruption, suffix anchors and fail-closed
storage errors. Tests use synthetic model callbacks and tools, not live providers.
`tests/test_stage_inputs.py` adds real brokered argument delivery, type/cardinality
and optional-path rules, cyclic latest-visit dataflow, payload-copy isolation,
graph validation, sensitive-source label propagation and replay of binding
failures. These are synthetic execution tests, not scientific-validity benchmarks.
`tests/test_cycle_safety.py` covers SCC topology, self/multi-stage/unreachable
cycles, the 256-stage bound, trusted fact and contract refusals, safe cycles,
acyclic nonrepeatable execution, changing facts between/within visits and refusal
replay. The original nonrepeatable-cycle test was replaced by a repeatable-key
test; unsafe cyclic execution is now tested as a refusal, not a supported path.
`tests/test_dynamic_service.py` covers terminal-only publication, inherited labels,
goal/caveat aggregation, current/original policy restrictions, clinical and fabricated
citation refusals, incomplete runs, storage/finalizer failures and stale-output
prevention. Synthetic callbacks are used rather than live model providers.
