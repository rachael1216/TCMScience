# Model-proposed scientific programs

`ScientificModelPlanner` closes one gap between a research question and the
existing scientific compiler. Unlike `ScientificPlanner`, which takes a program
from its caller, it asks a model to propose a ScientificProgram and repairs
rejected proposals within a fixed attempt bound. It returns only a compiled Plan
to the existing AgentLoopController. It has no ordinary-Plan fallback.

```python
from psh.workflow import ScientificModelPlanner
from psh.runtime import AgentLoopController

planner = ScientificModelPlanner(
    kernel,
    model=planning_profile,
    model_invoke=planning_invoke,
    registry=capability_registry,
    scientific_ledger=scientific_ledger,  # optional unless bindings are used
    max_attempts=3,
)
loop = AgentLoopController(
    kernel, planner=planner, registry=capability_registry,
    model=execution_profile, model_invoke=execution_invoke,
)
result = loop.run(research_question, kernel.policy.envelope())
```

The names above are application-provided objects, not a ready-to-run provider
configuration. Every planning request uses ExecutionBroker.call_model, including
the supplied callback: normal model egress, labels, accounting and audit apply.
The planner does not own or call a provider directly. No live provider is needed
for the synthetic regression tests.

## Admission and repair

1. Join objective, prior-plan and (when applicable) prior-result labels.
2. Refresh authority against the kernel's current policy before each request.
3. Build context through the existing ModelPlanner projection pipeline, including
   authorized registry schemas and optional labelled memory.
4. Parse a single JSON object with schema_version, plan and contracts. Reject
   duplicate keys, nonfinite numbers, trailing prose/fences, excessive character
   counts and malformed scientific wire values. The existing ScientificProgram
   parser/constructors define field semantics; some legacy Plan scalar coercions
   remain, so this is not an independently generated JSON Schema validator.
5. Compile under current policy and the joined input/output sensitivity. Reject
   delegation if the loop has no delegate backend.
6. Repair using diagnostic families/codes only, without copying raw rejected
   model text, task IDs or exception messages into correction prompts.

Attempts default to three, may be configured from one to eight, and share the
run's model-call/token/cost limits. Provider, budget and egress failures propagate;
they are not treated as correctable Scientific IR errors. The response parser
defaults to 262,144 characters, but this does not limit provider-side generation
or memory already allocated by the model gateway. Configure provider output and
run budgets as well.

`last_program` and `last_compilation` support in-memory inspection after success.
They are cleared at the start of each planning call, including a failed replan.
They may contain sensitive material: persistence/export still needs normal
governance. `attempts` is scoped to the current planning call; refusal entries
contain safe diagnostic codes rather than raw model replies. Instances are not
intended to be shared by concurrently running loops.

## What this does not establish

- Scientific declarations remain untrusted proposals. Evidence existence,
  execution conformance and scientific validity are not established by parsing.
- This adapter requires the compiler on its own path, but plain Plan users of
  AgentLoopController remain compatible. A separate mandatory ScientificRunMode
  for all entry points has not been implemented.
- Repair can propose a different plan; it is not approval of a changed registered
  protocol, proof of semantic goal equivalence, or authorization to weaken study
  requirements. Applications should supply required bindings/constraints and
  review proposed programs before consequential execution.
- Dynamic branches, loops, fan-out, live amendment, per-event execution replay,
  hypothesis tournaments and autonomous experimental feedback are not added.
- Tests use deterministic response callbacks and the real broker/loop. They do
  not measure live-model scientific quality or compare against another product.

Run `python -m pytest -q tests/test_scientific_model_planner.py` for regression tests.
