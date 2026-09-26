# Scientific workflow compiler — first increment

`psh.workflow` adds a versioned scientific contract layer above the existing PSH
`Plan`, validator, execution loop and gateways. It implements a bounded first
increment of the Scientific IR proposal. It does **not** implement the complete
autonomous scientist architecture or establish superiority over another system.

## Run the offline example

From `PSH-Harness`, with Python 3.11 or newer:

```sh
python -m pip install -e ".[test]"
python examples/scientific_workflow.py
python -m pytest -q tests/test_scientific_workflow.py
```

The example uses synthetic metadata. It prints the compiled task order, content
fingerprint, incremental invalidation report, and an expected refusal to use
animal evidence for a clinical efficacy claim. It makes no biomedical assertions,
contacts no provider and executes no research tools.

## Representation and execution

For model-generated programs with bounded compilation/repair, see
[ScientificModelPlanner](SCIENTIFIC_MODEL_PLANNER.md). The existing
ScientificPlanner remains an adapter for caller-supplied programs.

`ScientificProgram(schema_version=1, plan=..., contracts=...)` wraps a `Plan` with
exactly one `TaskContract` per task. Existing plain plans continue to work.
Python construction and `to_dict()` / `from_dict()` provide the Python and JSON
frontends. Unknown scientific fields, versions and enum values are rejected;
unknown fields in embedded plans are also rejected by this frontend. This is a
declarative DAG representation, not an arbitrary Python script interpreter.

Each contract describes a sensitivity floor, explicit effects, a side-effect
class, optional evidence/claim refinements, and optional
[statistical design checks](STATISTICAL_DESIGN.md). Task IDs and provenance references
must be opaque identifiers, not patient names or other sensitive content.

When an explicit `policy` is supplied, the compiler first intersects the run
envelope with `policy.ceiling()` using the shared AuthorityLattice. This prevents
an older, broader envelope from overriding tightened destinations, data ceilings,
budgets or other authority dimensions. Both validation passes and registered
protocol reads use that effective envelope. A broader policy never widens an
already restricted run. Tasks still inside the new limits may compile; tasks
that require revoked authority are refused. Without `policy`, the supplied run
envelope remains the authority boundary. This does not discover policy updates:
callers must supply the current snapshot (or update `ScientificPlanner.policy`).
Normal runtime admission and release checks remain mandatory.

The compiler:

1. Takes an independent JSON snapshot and rejects non-finite/non-JSON values.
2. Runs the existing graph, authority, binding, budget and acceptance checks.
3. Propagates sensitivity in topological order through **all** dependencies.
4. Checks effects against explicit destinations and propagated labels.
5. Rejects unsafe automatic retry declarations and negative resource estimates.
6. Checks evidence design and population/intervention/outcome refinements.
7. Lowers the propagated labels into `PlanTask.input_sensitivity`, validates the
   resulting plan, and returns a `Compilation` with per-task content hashes.

`PlanTask.max_label` remains a ceiling; `input_sensitivity` is a floor. A task
cannot relabel a PHI-derived count as PUBLIC by omitting identifiers from its text.
The floor is joined at model, tool and delegation dispatch, serialized with the
plan, and included when comparing redacted checkpoint shapes. Old serialized
plans default to PUBLIC for this new field. Ordinary runtime ingress can still
raise sensitivity above the compiler's declaration.

To execute through the existing runtime:

```python
from psh.runtime import AgentLoopController
from psh.workflow import ScientificPlanner

loop = AgentLoopController(
    kernel,
    planner=ScientificPlanner(program, registry=registry, policy=kernel.policy),
    registry=registry,
    model=model_profile,
    model_invoke=model_invoke,
)
result = loop.run(program.plan.objective, kernel.policy.envelope())
```

`kernel`, `registry`, `model_profile`, and `model_invoke` are the application's
existing PSH components. The adapter compiles under the actual run envelope and
includes any inherited objective/plan label. The controller still invokes its
normal validator and broker. Evidence declarations do not bypass output release,
quarantine, evidence verification or memory admission.

## Scientific scope checks

The initial conservative support matrix is:

| Claim | Admissible declared designs |
| --- | --- |
| Classical attribution | Classical text |
| Traditional use | Classical text, expert consensus |
| Mechanism | In vitro, animal |
| Association | Observational, randomized trial |
| Clinical efficacy | Randomized trial |
| Safety signal | Case report, observational, randomized trial |

A systematic review inherits the designs of its underlying studies. A review
of animal experiments does not become clinical evidence. Mixed reviews fail when
any declared underlying design cannot support the claim; split the evidence into
appropriate scoped records if necessary. These are software admission rules for
this prototype, not a universal clinical evidence grading guideline.

Every evidence-producing task must supply nonempty provenance references.
Every claim must name direct dependency tasks that declare evidence. Population,
intervention and outcome must match after trimming whitespace and case folding.
There is no fuzzy synonym matching or automatic population generalization.
Evidence IDs and references survive serialization and participate in hashes.

This checks **declared compatibility**, not whether a cited source exists, whether
the source says what the declaration claims, bias, certainty, causality or whether
the planned task actually returns adequate evidence. An RCT declaration is not
proof of efficacy. Runtime evidence verification remains necessary. This layer
does not generate or release scientific prose.

## Effects and retry semantics

Effects map to existing PSH destinations: local read/compute, local model,
trusted/public remote, persistence and user output. Effects must cover exactly the
task's explicit destinations. They do not grant authority, inspect generated code,
restrict raw sockets or attest third-party tool behavior.

Automatic retries are admitted only for declared `pure`, `idempotent` or
`at_least_once` work. `at_most_once`, `non_repeatable` and `compensatable` work
cannot request automatic retries; a compensation declaration alone cannot make a
retry safe. PURE excludes remote calls, persistence and user output. This is a
preflight check: runtime tool manifests and the existing operation ledger still
determine whether an interrupted operation can safely be dispatched again.

## Amendment analysis

`assess_amendment(old, new, envelope, completed=...)` validates both versions
under current authority, compares task contracts/payloads and their recursive
dependency fingerprints, and returns:

- `invalidated`: added or changed tasks and their affected descendants;
- `removed`: IDs present only in the previous program;
- `reusable_candidates`: unchanged, completed PURE tasks.

Changing the research objective, assumptions, acceptance criteria or other
plan-level context invalidates all tasks. Changing only `plan_id` does not.
Provenance changes invalidate downstream findings. Unknown completed IDs fail.
This API does not load results, mutate a running loop, or automatically reuse a
checkpoint. Candidate reuse still needs verified result digests, current policy
and labels, and matching model/tool/environment identity. Fingerprints identify
declared program content, not reproducible execution environments or signatures.

## Coverage and remaining roadmap

The test module includes unsafe scientific trajectories, transitive PHI flow,
arbitrary-chain label monotonicity, unsafe retries, amendment propagation,
serialization, checkpoint label shape, and execution through the real broker.
The repository's existing PSH CI automatically collects these tests.

| Proposal area | State after this increment |
| --- | --- |
| Scientific IR and compiler | Versioned task/evidence/claim contracts; Python/JSON frontend |
| Types, effects, information/evidence flow | Conservative declared-scope checks; runtime sensitivity floors |
| Workflow amendment | Change impact analysis; no live amendment or automatic cache reuse |
| Durable execution | Optional [checkpoint journal](DURABLE_CHECKPOINT_JOURNAL.md) and atomic operation admission; no per-event replay engine |
| Scientific World Model | Typed hypothesis/protocol/observation records and explicit deviations; see [scientific records](SCIENTIFIC_RECORDS.md). Competition remains future work |
| Statistical/causal compiler | Not implemented; resource checks are not statistical validation |
| Dynamic branches, loops, fan-out | Not implemented by this frontend |
| OS sandbox, network broker, secrets | Existing controls retained; no stronger isolation claim |
| PROV/RO-Crate, CWL/WES/TES | Future interoperability work |
| Capability evolution and Research Cockpit | Future work |

Next implementation milestones should add pinned capability/environment manifests
before automatic reuse, evidence-preserving durable replay with fault injection,
and hypothesis competition over the typed scientific records.
Each milestone needs its own runtime integration and trajectory tests. This code
is an original implementation against PSH interfaces; no ZCode source was copied
or reviewed for this increment.
