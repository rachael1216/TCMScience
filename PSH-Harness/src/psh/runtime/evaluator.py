"""Four evaluators, three of them deterministic.

The review's structure, and the reason for it: a single "LLM critic: looks good?" is both
the least reliable check and the one that gets asked first. So the layers are ordered by
how much they can be trusted, and the model-shaped question is asked last and only about
what cannot be settled mechanically.

    StructuralEvaluator   does the output match the schema the task promised?
    ExecutionEvaluator    did the tasks actually succeed?
    EvidenceEvaluator     are the claims supported by evidence the run holds?
    GoalEvaluator         is the objective met?

The first three are pure functions of the loop's state. Only the fourth may consult a
model — and when it does, it goes through the broker like any other model call, because an
evaluator that reaches a provider directly is a second egress path and the package has
closed that hole twice already.

``acceptance_tests`` gets teeth here. ``DelegationContract.acceptance_tests`` was
``tuple[str, ...]`` with no execution path at all; a string cannot be dispatched, so the
field could only ever be documentation. The checkers below are keyed by name, and
``PlanValidator`` refuses a plan naming a checker that does not exist — because a test
skipped for being unrecognised reads exactly like a test that passed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .execgraph import ExecutionGraph, TaskState
from .plan import Criterion, Plan, PlanTask, TestSpec

__all__ = ["Evaluator", "Verdict", "ACCEPTANCE_CHECKS", "CheckResult",
           "check_output_schema"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the loop should do next, and why.

    ``done``, ``retry`` and ``replan`` are deliberately not mutually exclusive as fields
    but are resolved in that order by the loop, so a verdict that is both satisfied and
    retryable finishes. The alternative — an enum — cannot carry "the goal is met *and*
    two optional tasks failed", which is the ordinary case at the end of a research run.
    """

    done: bool = False
    retry: bool = False
    replan: bool = False
    escalate: bool = False
    reason: str = ""
    failures: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()
    #: ``verified`` — every criterion was decided by a deterministic check or the goal
    #: checker; ``pending_manual`` — a ``manual`` criterion exists and nothing judged it;
    #: ``unverified`` — a criterion is unmet. ``done`` says the tasks ran and the checks
    #: that could run passed. A reviewer showed a task asked for two identifiers, answered
    #: "banana", and finished ``goal_satisfied`` under a manual criterion: execution
    #: succeeded, the goal was never verified, and the two must not be one word.
    goal_status: str = "unverified"
    pending: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("a verdict must state its reason")


# --------------------------------------------------------------- schema checking

def check_output_schema(value: Any, schema: Mapping[str, Any]) -> CheckResult:
    """A strict JSON-Schema subset: what it understands it enforces, and what it does not
    understand it refuses.

    The first version was "deliberately small" and ignored unknown keywords, which failed
    open in four ways a reviewer measured: ``True`` passed as an integer (bool is an int
    subclass), ``-5`` passed ``minimum: 0``, ``["a"]`` passed ``items: {type: integer}``
    and an extra key passed ``additionalProperties: false``. A schema is the contract a
    task promised; a checker that accepts what the contract forbids is not checking it.

    Supported: type (single or list), enum, const, required, properties,
    additionalProperties (bool or schema), items, minItems, maxItems, uniqueItems,
    minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf, minLength,
    maxLength, pattern, anyOf, oneOf, allOf, not. Annotation keywords are ignored.
    Anything else is a refusal, so a promise the checker cannot keep is visible.
    """
    if not schema:
        return CheckResult(True, "no schema declared")
    problem = _schema_problem(value, schema, path="$")
    return CheckResult(problem is None, problem or "matches schema")


_ANNOTATIONS = frozenset({"title", "description", "default", "examples", "$schema", "$id",
                          "$comment", "deprecated", "readOnly", "writeOnly", "format"})
_KEYWORDS = frozenset({"type", "enum", "const", "required", "properties",
                       "additionalProperties", "items", "minItems", "maxItems",
                       "uniqueItems", "minimum", "maximum", "exclusiveMinimum",
                       "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern",
                       "anyOf", "oneOf", "allOf", "not"})


def _is_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer()
                                          and math.isfinite(value))
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "null":
        return value is None
    return False


def _schema_problem(value: Any, schema: Any, *, path: str) -> str | None:
    """The first violation as text, or None. Refuses schema keywords it cannot enforce."""
    if schema is True:
        return None
    if schema is False:
        return f"{path}: schema forbids any value"
    if not isinstance(schema, Mapping):
        return f"{path}: schema must be an object, got {type(schema).__name__}"
    unknown = sorted(k for k in schema if k not in _KEYWORDS and k not in _ANNOTATIONS)
    if unknown:
        return f"{path}: unsupported schema keyword(s) {unknown}; refusing rather than ignoring"

    expected = schema.get("type")
    if expected is not None:
        options = [expected] if isinstance(expected, str) else list(expected)
        if not any(_is_type(value, opt) for opt in options):
            got = "boolean" if isinstance(value, bool) else type(value).__name__
            return f"{path}: expected {'/'.join(options)}, got {got}"

    if "const" in schema and value != schema["const"]:
        return f"{path}: {value!r} is not the constant {schema['const']!r}"
    allowed = schema.get("enum")
    if allowed is not None and (value not in allowed or isinstance(value, bool) and not any(
            isinstance(a, bool) and a is value for a in allowed)):
        return f"{path}: {value!r} is not one of {list(allowed)}"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: {value} is below the minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: {value} is above the maximum {schema['maximum']}"
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return f"{path}: {value} is not above {schema['exclusiveMinimum']}"
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return f"{path}: {value} is not below {schema['exclusiveMaximum']}"
        if "multipleOf" in schema and schema["multipleOf"] and \
                (value / schema["multipleOf"]) % 1 not in (0, 0.0):
            return f"{path}: {value} is not a multiple of {schema['multipleOf']}"

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path}: shorter than {schema['minLength']} characters"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path}: longer than {schema['maxLength']} characters"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return f"{path}: does not match pattern {schema['pattern']!r}"

    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path}: fewer than {schema['minItems']} items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path}: more than {schema['maxItems']} items"
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                if item in seen:
                    return f"{path}: items are not unique"
                seen.append(item)
        if "items" in schema:
            for index, item in enumerate(value):
                problem = _schema_problem(item, schema["items"], path=f"{path}[{index}]")
                if problem:
                    return problem

    if isinstance(value, Mapping):
        missing = [k for k in schema.get("required", ()) if k not in value]
        if missing:
            return f"{path}: missing required key(s): {missing}"
        properties = schema.get("properties") or {}
        for key, sub in properties.items():
            if key in value:
                problem = _schema_problem(value[key], sub, path=f"{path}.{key}")
                if problem:
                    return problem
        extra = schema.get("additionalProperties", True)
        if extra is not True:
            for key in value:
                if key in properties:
                    continue
                if extra is False:
                    return f"{path}: key {key!r} is not allowed by additionalProperties: false"
                problem = _schema_problem(value[key], extra, path=f"{path}.{key}")
                if problem:
                    return problem

    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            outcomes = [_schema_problem(value, sub, path=path) for sub in schema[keyword]]
            matched = sum(1 for o in outcomes if o is None)
            if keyword == "anyOf" and matched == 0:
                return f"{path}: matches none of the anyOf alternatives"
            if keyword == "oneOf" and matched != 1:
                return f"{path}: matches {matched} oneOf alternatives, expected exactly one"
    for sub in schema.get("allOf", ()):
        problem = _schema_problem(value, sub, path=path)
        if problem:
            return problem
    if "not" in schema and _schema_problem(value, schema["not"], path=path) is None:
        return f"{path}: matches a schema it must not match"
    return None


# ------------------------------------------------------------- acceptance checks

def _check_non_empty(result: Any, spec: TestSpec, ctx: Mapping[str, Any]) -> CheckResult:
    text = result if isinstance(result, str) else str(result or "")
    return CheckResult(bool(text.strip()), "empty result" if not text.strip() else "ok")


def _check_schema(result: Any, spec: TestSpec, ctx: Mapping[str, Any]) -> CheckResult:
    return check_output_schema(result, spec.detail.get("schema") or ctx.get("schema") or {})


def _check_contains(result: Any, spec: TestSpec, ctx: Mapping[str, Any]) -> CheckResult:
    needles = spec.detail.get("any_of") or ()
    text = (result if isinstance(result, str) else str(result or "")).lower()
    hit = [n for n in needles if str(n).lower() in text]
    return CheckResult(bool(hit) if needles else True,
                       f"none of {list(needles)} present" if not hit else f"found {hit}")


def _check_citations_present(result: Any, spec: TestSpec,
                             ctx: Mapping[str, Any]) -> CheckResult:
    import re
    text = result if isinstance(result, str) else str(result or "")
    found = re.findall(r"\b(?:PMID|NCT|DOI)[:\s]*([\w./-]+)", text, re.I)
    return CheckResult(bool(found), f"{len(found)} identifier(s)")


def _check_max_length(result: Any, spec: TestSpec, ctx: Mapping[str, Any]) -> CheckResult:
    limit = int(spec.detail.get("chars", 0) or 0)
    text = result if isinstance(result, str) else str(result or "")
    return CheckResult(not limit or len(text) <= limit,
                       f"{len(text)} chars against a limit of {limit}")


#: The dispatch table ``PlanValidator`` validates against. Adding a checker here is what
#: makes a new ``TestSpec.kind`` legal — the two cannot drift, because the validator reads
#: this dict rather than a copy of its keys.
ACCEPTANCE_CHECKS: dict[str, Callable[[Any, TestSpec, Mapping[str, Any]], CheckResult]] = {
    "non_empty": _check_non_empty,
    "output_schema": _check_schema,
    "contains": _check_contains,
    "citations_present": _check_citations_present,
    "max_length": _check_max_length,
}


# -------------------------------------------------------------------- evaluator

class Evaluator:
    """The four layers, applied in order of how far they can be trusted."""

    def __init__(self, *, goal_checker: Callable[[Plan, Mapping[str, Any]], CheckResult]
                 | None = None) -> None:
        #: Optional. The one layer that may need a model; supplied by the caller so this
        #: module never holds a provider and the loop can pass one that routes through the
        #: broker. Absent, the goal layer falls back to the deterministic criteria below.
        self.goal_checker = goal_checker
        self.evaluations = 0

    # ------------------------------------------------------------------ layers
    def structural(self, graph: ExecutionGraph) -> list[str]:
        """Schema and acceptance tests, per succeeded task."""
        failures: list[str] = []
        for node in graph.succeeded:
            task = node.task
            schema_result = check_output_schema(node.result, task.output_schema)
            if not schema_result.ok:
                failures.append(f"{task.task_id}: output schema — {schema_result.detail}")
            for spec in task.acceptance_tests:
                check = ACCEPTANCE_CHECKS.get(spec.kind)
                if check is None:
                    # Unreachable via a validated plan; kept because reaching it silently
                    # would mean an unrun test counted as a passed one.
                    failures.append(
                        f"{task.task_id}: acceptance test {spec.kind!r} has no checker")
                    continue
                result = check(node.result, spec, {"schema": task.output_schema})
                if not result.ok:
                    failures.append(f"{task.task_id}: {spec.kind} — {result.detail}")
        return failures

    def execution(self, graph: ExecutionGraph) -> list[str]:
        return [f"{n.id}: {n.state.value} — {n.error[:120]}"
                for n in graph.nodes.values()
                if n.state in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED)]

    def evidence(self, graph: ExecutionGraph, supports: Sequence[Any] = ()) -> list[str]:
        """Evidence requirements, using the run's existing ClaimSupport records."""
        failures: list[str] = []
        requiring = [n for n in graph.succeeded if n.task.evidence_required]
        for node in requiring:
            result = node.result
            has_evidence = (isinstance(result, Mapping)
                            and bool(result.get("evidence") or result.get("citations")))
            if not has_evidence:
                failures.append(
                    f"{node.id}: declared evidence_required and returned none")
        failures += [f"unsupported claim: {s.claim[:100]}" for s in supports
                     if getattr(s, "supports", True) is False]
        return failures

    def goal(self, plan: Plan, graph: ExecutionGraph) -> list[str]:
        """Which completion criteria are not met. See ``goal_report`` for what is pending."""
        return self.goal_report(plan, graph)[0]

    def goal_report(self, plan: Plan, graph: ExecutionGraph) -> tuple[list[str], list[str]]:
        """``(unmet, pending)``: criteria that failed, and criteria nobody has judged.

        The deterministic kinds are decided here. ``manual`` is the honest name for "a
        human or a model has to judge this": it is not counted as unmet when every task
        succeeded — the loop may stop — but it is reported as *pending*, so a release can
        say the objective was never verified rather than that it was.
        """
        results = graph.results()
        unmet: list[str] = []
        pending: list[str] = []
        for criterion in plan.completion_criteria:
            if criterion.kind == "task":
                if not all(n.state is TaskState.SUCCEEDED for n in graph.nodes.values()):
                    unmet.append(criterion.description)
            elif criterion.kind in ("evidence", "claim"):
                if not any(isinstance(r, Mapping) and (r.get("evidence") or
                                                       r.get("citations"))
                           for r in results.values()):
                    unmet.append(criterion.description)
            elif criterion.kind == "artifact":
                if not any(isinstance(r, Mapping) and r.get("artifact")
                           for r in results.values()):
                    unmet.append(criterion.description)
            else:                                  # manual
                if graph.failed or not results:
                    unmet.append(criterion.description)
                elif self.goal_checker is None:
                    pending.append(criterion.description)
        if self.goal_checker is not None and not unmet:
            verdict = self.goal_checker(plan, results)
            if not verdict.ok:
                unmet.append(verdict.detail or "the goal checker was not satisfied")
        return unmet, pending

    # ------------------------------------------------------------------ verdict
    def evaluate(self, plan: Plan, graph: ExecutionGraph, *,
                 supports: Sequence[Any] = (), replans_left: int = 0) -> Verdict:
        """Combine the four layers into one decision."""
        self.evaluations += 1
        execution = self.execution(graph)
        structural = self.structural(graph)
        evidence = self.evidence(graph, supports)
        unmet, pending = self.goal_report(plan, graph)
        failures = tuple(execution + structural + evidence)
        goal_status = ("unverified" if unmet or failures else
                       "pending_manual" if pending else "verified")

        retryable = [n for n in graph.nodes.values() if n.state is TaskState.RETRYABLE]
        if retryable:
            return Verdict(retry=True, reason=f"{len(retryable)} task(s) may be retried",
                           failures=failures)

        if not graph.complete:
            return Verdict(reason="tasks remain to be executed", failures=failures)

        if not failures and not unmet:
            return Verdict(done=True, goal_status=goal_status, pending=tuple(pending),
                           reason=("every criterion is met and no task failed"
                                   if not pending else
                                   f"every task succeeded; {len(pending)} manual "
                                   "criterion(a) await a judge"))

        # A structural or evidence failure on a completed graph is a planning problem, not
        # an execution one: running the same tasks again produces the same output. Replan
        # while replans remain, then escalate rather than spin.
        if replans_left > 0:
            return Verdict(replan=True, failures=failures, unmet_criteria=tuple(unmet),
                           reason=(f"{len(failures)} failure(s) and {len(unmet)} unmet "
                                   "criterion(a) on a completed plan"))
        return Verdict(escalate=True, failures=failures, unmet_criteria=tuple(unmet),
                       reason=("no replans remain and the plan did not satisfy its "
                               f"criteria: {len(failures)} failure(s), {len(unmet)} unmet"))
