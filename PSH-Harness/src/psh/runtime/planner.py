"""A planner that emits a typed plan, through the broker, and corrects itself.

``StaticPlanner`` takes the plan from its caller. That is honest and it is not planning, so
this is the component that closes the last placeholder in the pipeline: `_plan()` split the
request on full stops, `validate_plan` had nothing typed to validate, and the loop could
iterate only over a plan someone else had written.

Three properties are load-bearing, and each is a rule this package has already paid for
once:

**It is a model call like any other.** The planner holds no provider. It asks
``ExecutionBroker.call_model``, so the planning prompt is classified, gated on the model's
destination, counted against the run's budget and recorded as an event. A planner that
reached a provider directly would be a second egress path — the defect closed in v0.2 for
the claim verifier, which "took a raw callable that reached a provider directly".

**Its output is untrusted input.** A plan is text a model produced, so nothing it says is
believed: it is parsed into typed objects that refuse malformed values, and then
``PlanValidator`` rules on it against the run envelope. A model that proposes a task
holding ``PUBLIC_REMOTE`` under a local-only run does not get it — the plan is refused,
and the refusal is fed back as the next attempt's input. This is the property worth
testing directly, and ``test_planner.py`` does: **an escalating plan is refused, not
obeyed.**

**Correction is bounded.** Re-asking a model that produced an invalid plan is worth doing;
re-asking it forever is not. Attempts are capped, each one costs the same budget as the
first, and the failure mode is a refusal naming what the model kept getting wrong rather
than a loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts import (
    Autonomy, ContextItem, ContractViolation, PolicyDenied, RiskTier, RunEnvelope,
)
from ..labels import DataLabel, Destination, Sensitivity
from .loop import LoopState, Planner, classify_with, graph_label, objective_label_for
from .plan import InputBinding, Criterion, Plan, PlanTask, RetryPolicy, TaskKind, TestSpec
from .plan_validator import PlanRejected, PlanValidator

__all__ = ["ModelPlanner", "PlanParseError", "parse_plan", "PLANNER_SYSTEM_PROMPT"]


class PlanParseError(ContractViolation):
    """The model's output was not a plan this runtime can execute."""


PLANNER_SYSTEM_PROMPT = """\
You are the planner inside a governed research harness. Return **one JSON object and
nothing else** — no prose, no explanation, no code fence.

{
  "objective": "restatement of the goal",
  "assumptions": ["what you are taking as given"],
  "tasks": [
    {
      "task_id": "short_snake_case_id",
      "objective": "what this step achieves",
      "kind": "model" | "tool" | "delegate",
      "component_id": "required when kind is tool",
      "payload": {"argument": "value — exactly the arguments the capability's payload schema names"},
      "dependencies": ["task_id of any step that must finish first"],
      "inputs": [{"argument": "payload argument this fills", "source": "task_id it reads",
                  "pointer": "/field/in/that/result", "type": "string" | "integer" |
                  "number" | "boolean" | "array" | "object", "cardinality": "one" | "many"}],
      "max_risk": "R0_TRIVIAL" | "R1_ROUTINE" | "R2_CONSEQUENTIAL" | "R3_CLINICAL",
      "max_label": "PUBLIC" | "INTERNAL" | "RESEARCH_DEIDENTIFIED" | "SENSITIVE" | "PHI",
      "destinations": ["LOCAL_COMPUTE" | "LOCAL_MODEL" | "USER_OUTPUT" | "PERSISTENT" |
                       "TRUSTED_REMOTE" | "PUBLIC_REMOTE"],
      "evidence_required": true | false,
      "output_schema": {"type": "object", "required": ["..."]},
      "acceptance_tests": [{"kind": "non_empty" | "citations_present" | "output_schema"}]
    }
  ],
  "completion_criteria": [{"description": "...", "kind": "task"|"evidence"|"artifact"|"manual"}]
}

Rules that are enforced, so a plan breaking them is refused rather than run:

* The authority you request per task is a **ceiling request**, not a grant. Ask for the
  least that will do. Asking for more than the run holds gets the whole plan refused.
* Dependencies must form a DAG over ids in this plan.
* Every plan needs at least one completion criterion, or there is no definition of done.
* A `tool` task must name a `component_id` from the capabilities listed below. Do not
  invent one.
* A `tool` task's `payload` holds the arguments its capability's payload schema (listed
  below for the best matches) names, and nothing else. A connector needs `"operation"`
  plus that operation's arguments; a tool with named parameters needs those parameters.
  A payload the schema does not describe fails at the component, not silently.
* A step that needs a value another step produced declares it in `inputs`: the argument
  it fills, the producing task (which must also be in `dependencies`) and a JSON pointer
  into that task's result (`""` for the whole result, `/gene` for a field, `/hits/0/id`
  for a list element). The runtime resolves bindings; it never resolves payload text, so
  writing `"$fetch.gene"` in a payload sends the string `$fetch.gene` to the tool.
* Budget estimates are checked for feasibility before anything runs.
* A `delegate` task is allowed only when the authority brief says delegation is
  available. It hands a self-contained sub-objective to a child agent that sees nothing
  but that objective and works under a narrower budget, so its `objective` must stand on
  its own. Never delegate the whole objective, and never rely on delegation for a step a
  `model` or `tool` task can do.
"""


# ------------------------------------------------------------------- parsing

_ENUMS: Mapping[str, Mapping[str, Any]] = {
    "max_risk": {t.name: t for t in RiskTier},
    "max_label": {s.name: s for s in Sensitivity},
    "autonomy": {a.name: a for a in Autonomy} | {a.value: a for a in Autonomy},
}
_DESTINATIONS = {d.name: d for d in Destination}


def _extract_json(text: str) -> dict[str, Any]:
    """Find the plan object in whatever the model wrapped it in.

    Models put JSON inside fences and prose however firmly they are asked not to. Being
    strict about the envelope buys nothing — the *contents* are what must be strict — so
    this is tolerant about where the object is and unforgiving about what is in it.
    """
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    if start == -1:
        raise PlanParseError("the planner returned no JSON object at all")
    # Scan for the matching close brace rather than taking the last one: trailing prose
    # containing a brace would otherwise swallow the parse.
    depth, in_string, escaped = 0, False, False
    for index, char in enumerate(stripped[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = stripped[start:index + 1]
                break
    else:
        raise PlanParseError("the planner's JSON object is unterminated")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"the planner's JSON does not parse: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PlanParseError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _enum(field_name: str, value: Any, default: Any) -> Any:
    if value is None:
        return default
    table = _ENUMS[field_name]
    key = str(value).strip()
    if key not in table:
        raise PlanParseError(
            f"{field_name}={value!r} is not one of {sorted(table)}")
    return table[key]


def parse_plan(text: str, *, produced_by: str = "model") -> Plan:
    """Turn a planner's output into a typed ``Plan``, refusing anything malformed.

    Every ``raise`` here is a message the planner will see on its next attempt, so they
    name the field and the legal values rather than saying the plan was invalid.
    """
    raw = _extract_json(text)
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise PlanParseError("a plan needs a non-empty 'tasks' array")

    tasks: list[PlanTask] = []
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, Mapping):
            raise PlanParseError(f"tasks[{index}] is not an object")
        destinations = item.get("destinations") or ()
        try:
            resolved = tuple(_DESTINATIONS[str(d).strip()] for d in destinations)
        except KeyError as exc:
            raise PlanParseError(
                f"tasks[{index}].destinations names {exc.args[0]!r}; legal destinations "
                f"are {sorted(_DESTINATIONS)}") from exc
        tests = item.get("acceptance_tests") or ()
        try:
            specs = tuple(
                TestSpec(kind=str(t["kind"]), detail=dict(t.get("detail") or {}))
                if isinstance(t, Mapping) else TestSpec(kind=str(t))
                for t in tests)
        except KeyError as exc:
            raise PlanParseError(
                f"tasks[{index}].acceptance_tests entries need a 'kind': {exc}") from exc
        try:
            tasks.append(PlanTask(
                task_id=str(item.get("task_id") or f"task_{index + 1}"),
                objective=str(item.get("objective") or ""),
                kind=str(item.get("kind") or TaskKind.MODEL),
                dependencies=tuple(str(d) for d in (item.get("dependencies") or ())),
                component_id=str(item.get("component_id") or ""),
                capability_requirements=tuple(
                    str(c) for c in (item.get("capability_requirements") or ())),
                payload=dict(item.get("payload") or {}),
                max_risk=_enum("max_risk", item.get("max_risk"), RiskTier.R1_ROUTINE),
                max_label=_enum("max_label", item.get("max_label"),
                                Sensitivity.RESEARCH_DEIDENTIFIED),
                destinations=resolved,
                autonomy=_enum("autonomy", item.get("autonomy"), Autonomy.SUGGEST),
                estimated_tokens=int(item.get("estimated_tokens") or 0),
                estimated_usd=float(item.get("estimated_usd") or 0.0),
                estimated_seconds=float(item.get("estimated_seconds") or 0.0),
                output_schema=dict(item.get("output_schema") or {}),
                acceptance_tests=specs,
                evidence_required=bool(item.get("evidence_required")),
                inputs=tuple(
                    InputBinding.from_dict(b) if isinstance(b, Mapping)
                    else InputBinding(argument=str(b), source="")
                    for b in (item.get("inputs") or ())),
                retry=RetryPolicy(max_attempts=max(1, int(item.get("max_attempts") or 1))),
            ))
        except (ValueError, TypeError) as exc:
            # PlanTask's own validation: self-dependency, unknown kind, tool without a
            # component, evidence without a schema. Its messages already name the task.
            raise PlanParseError(str(exc)) from exc

    criteria_raw = raw.get("completion_criteria") or ()
    criteria: list[Criterion] = []
    for item in criteria_raw:
        if isinstance(item, Mapping):
            criteria.append(Criterion(description=str(item.get("description") or ""),
                                      kind=str(item.get("kind") or "manual")))
        elif str(item).strip():
            criteria.append(Criterion(description=str(item)))

    try:
        return Plan(
            objective=str(raw.get("objective") or "").strip() or "unstated objective",
            tasks=tuple(tasks),
            assumptions=tuple(str(a) for a in (raw.get("assumptions") or ())),
            completion_criteria=tuple(criteria),
            evidence_requirements=tuple(
                str(e) for e in (raw.get("evidence_requirements") or ())),
            produced_by=produced_by)
    except ValueError as exc:
        raise PlanParseError(str(exc)) from exc


# ------------------------------------------------------------------- planner

@dataclass
class PlanAttempt:
    """One planning attempt, kept so a refusal can show what the model kept doing."""

    attempt: int
    error: str = ""
    plan_id: str = ""


class ModelPlanner(Planner):
    """Asks a model for a typed plan, through the broker, and corrects it once or twice."""

    def __init__(self, kernel: Any, *, model: Any,
                 model_invoke: Callable[[str], str],
                 registry: Any = None, validator: PlanValidator | None = None,
                 max_attempts: int = 3, system_prompt: str = "",
                 schema_candidates: int = 6, memory: Any = None,
                 memory_items: int = 6) -> None:
        self.kernel = kernel
        self.model = model
        self.model_invoke = model_invoke
        #: Optional ``MemoryRetriever``. What earlier runs verified is compiled into the
        #: planning prompt as labelled memory items — retrieved under the run's ceiling
        #: and withheld for this model's destination, so a memory the gate would refuse
        #: never makes the planning call refusable. Off unless a retriever is given.
        self.memory = memory
        self.memory_items = memory_items
        #: How many of the ranked capabilities have their payload schema disclosed. The
        #: summaries let the model choose; the schemas let it call. Both come from the
        #: registry's manifests and nothing else.
        self.schema_candidates = schema_candidates
        self.registry = registry
        #: The planner validates so it can *correct*; the loop validates again so the
        #: result is enforced. The second check is the authoritative one — a planner that
        #: was also the enforcement point could be replaced by a laxer planner.
        self.validator = validator or PlanValidator(registry=registry)
        if max_attempts < 1:
            raise ValueError("a planner needs at least one attempt")
        self.max_attempts = max_attempts
        self.system_prompt = system_prompt or PLANNER_SYSTEM_PROMPT
        self.attempts: list[PlanAttempt] = []

    # ------------------------------------------------------------------ public
    def plan(self, state: LoopState, *, feedback: Any = None) -> Plan:
        """Return a validated ``Plan`` or raise. Never returns something unexecutable.

        Everything quoted back to the model is labelled with what it quotes. Evaluator
        failures embed task results and component errors, so their label is the join of
        the graph's; a refused attempt is quoted from the model's own reply, so its label
        is what that reply was given. The accepted plan's label — what the model was shown
        joined with what it wrote — is recorded on the state, and the loop labels every
        task objective with it, because a task objective is derived from this prompt.
        """
        errors: list[str] = []
        errors_label = DataLabel()
        if feedback is not None and getattr(feedback, "failures", ()):
            errors.append("The previous plan completed but did not satisfy its criteria: "
                          + "; ".join(list(feedback.failures)[:5]))
            errors_label = errors_label.merged_with(graph_label(state.graph))

        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            projection = self._projection(state, errors, errors_label)
            call = self.kernel.broker.call_model(projection, self.model, state.envelope,
                                                 invoke=self.model_invoke)
            text = getattr(call, "content", str(call))
            shown = getattr(call, "label", None) or projection.label
            produced = shown.merged_with(
                classify_with(self.kernel, text, origin="planner_output"))
            try:
                candidate = parse_plan(text, produced_by=f"{self.model.id}#{attempt}")
                self.validator.validate(candidate, state.envelope,
                                        policy=getattr(self.kernel, "policy", None))
                delegating = [t.task_id for t in candidate.tasks if t.kind == TaskKind.DELEGATE]
                if delegating and not state.can_delegate:
                    # The validator rules on authority; whether a delegate task has
                    # anywhere to go is a fact about this loop, which the state carries.
                    # Refusing here, as a correction, is cheaper than a ContractViolation
                    # at dispatch after the rest of the plan has run.
                    raise PlanRejected(
                        f"task(s) {delegating} delegate, and this loop has no delegate "
                        "backend: delegation is not available, use model or tool tasks")
            except (PlanParseError, PlanRejected) as exc:
                last = exc
                message = str(exc)
                errors.append(f"Attempt {attempt} was refused: {message}")
                errors_label = errors_label.merged_with(produced)
                self.attempts.append(PlanAttempt(attempt=attempt, error=message))
                self._audit(state, attempt=attempt, outcome="refused",
                            error_type=type(exc).__name__)
                continue
            self.attempts.append(PlanAttempt(attempt=attempt, plan_id=candidate.plan_id))
            self._audit(state, attempt=attempt, outcome="accepted",
                        tasks=len(candidate.tasks))
            state.plan_label = produced
            return candidate

        raise PlanRejected(
            f"the planner produced {self.max_attempts} plan(s) that could not be executed "
            f"under run {state.envelope.run_id}; last refusal: {last}",
            getattr(last, "violations", ()))

    # ----------------------------------------------------------------- context
    def _projection(self, state: LoopState, errors: Sequence[str],
                    errors_label: DataLabel | None = None) -> Any:
        """Compile the planning prompt. Its own projection, like any other worker's.

        The turn item carries the objective's classification and the correction item
        carries the label of what it quotes. Both kinds are load-bearing — the compiler
        never drops them — so the projection's label is what the gate rules on, and a PHI
        objective heading for a public provider is refused rather than sent as PUBLIC.
        """
        from ..context import ContextCompiler

        # Static text from this module: PUBLIC by construction, and said so. A structural
        # test refuses any ContextItem built in the runtime without a stated label.
        items = [ContextItem(kind="instruction", content=self.system_prompt,
                             label=DataLabel()),
                 ContextItem(kind="instruction", content=self._authority_brief(state),
                             label=DataLabel())]
        if self.registry is not None:
            candidates = self.registry.resolve(state.objective, state.envelope, limit=12)
            items += self.registry.manifest_items(candidates)
            schema_items = getattr(self.registry, "schema_items", None)
            if schema_items is not None and self.schema_candidates > 0:
                items += schema_items(candidates, limit=self.schema_candidates)
        if self.memory is not None and self.memory_items > 0:
            # Each item carries the label its node was stored with; the retriever has
            # already withheld what exceeds the run's ceiling or this destination.
            items += self.memory.retrieve(state.objective, envelope=state.envelope,
                                          destination=self.model.destination,
                                          limit=self.memory_items)
        if errors:
            # The correction. Naming what was wrong is the difference between a retry and
            # a second identical answer.
            items.append(ContextItem(
                kind="instruction",
                content=("Previous attempts were refused. Fix these and return a corrected "
                         "plan:\n- " + "\n- ".join(errors[-4:])),
                label=errors_label or DataLabel()))
        items.append(ContextItem(kind="turn", content=state.objective,
                                 label=objective_label_for(state, self.kernel)))
        return ContextCompiler().compile(
            items=items, envelope=state.envelope, destination=self.model.destination,
            token_budget=state.envelope.budget.tokens_soft, query=state.objective)

    @staticmethod
    def _authority_brief(state: LoopState) -> str:
        """Tell the planner the ceiling, so a refusable plan is avoidable rather than routine.

        The validator refuses an escalating plan whether or not the planner was told, so
        this is an efficiency, not a control. Withholding it would mean paying for a
        refused attempt every time the model guessed.
        """
        envelope = state.envelope
        children = envelope.budget.max_delegations
        if getattr(state, "can_delegate", False) and children > 0:
            delegation = (f"available: up to {children} child agent(s) via kind=\"delegate\", "
                          "each under a narrower slice of this budget")
        else:
            delegation = "not available in this loop; do not emit kind=\"delegate\" tasks"
        return (
            "This run's authority — plan within it:\n"
            f"- risk ceiling: {envelope.risk.name}\n"
            f"- data ceiling: {envelope.max_label.sensitivity.name}\n"
            f"- destinations: {sorted(d.name for d in envelope.allowed_destinations)}\n"
            f"- autonomy: {envelope.autonomy.value}\n"
            f"- budget: {envelope.budget.tokens_hard} tokens, "
            f"${envelope.budget.usd_hard:.2f}, {envelope.budget.max_model_calls} model "
            f"call(s), {envelope.budget.max_tool_calls} tool call(s)\n"
            f"- delegation: {delegation}")

    def _audit(self, state: LoopState, **detail: Any) -> None:
        audit = getattr(self.kernel, "audit", None)
        if audit is not None:
            audit("plan_proposed", run_id=state.envelope.run_id,
                  detail={"loop_id": state.loop_id, **detail})
