"""Refuse a plan before any of it runs.

The argument for validating a plan as a whole, rather than checking each step as it comes
up, is the one that motivates the authority lattice generally: a check made at the point of
use happens after the earlier steps have already executed. A plan whose fourth step needs a
public destination the run does not permit should be refused at the plan, not discovered at
step four with three steps' worth of side effects already committed and budget already
spent.

Five families of check, in the order a failure is cheapest to explain:

    GraphValidity      cycles, missing dependencies, unreachable tasks
    AuthorityValidity  every task's requested authority against the run envelope
    DataFlowValidity   labels against destinations, and against the run's ceiling
    BudgetFeasibility  the sum of the estimates against the envelope's budget
    ScientificValidity completion criteria, evidence requirements, dispatchable tests

The authority family does **not** re-implement containment. It builds the envelope each
task would execute under, using ``RunEnvelope.restrict()`` — which is the lattice — and
reports the ``PolicyDenied`` that raises. So the validator agrees with the executor by
construction: the thing it validates is the very thing the loop will later build.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import Autonomy, PolicyDenied, RiskTier, RunEnvelope
from ..labels import DataLabel, Destination, Sensitivity
from .plan import Plan, PlanTask, TaskKind

__all__ = ["PlanValidator", "PlanViolation", "ValidatedPlan", "PlanRejected"]


class PlanRejected(PolicyDenied):
    """A plan was refused before execution. Carries every violation, not just the first."""

    def __init__(self, message: str, violations: Sequence["PlanViolation"] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)


@dataclass(frozen=True, slots=True)
class PlanViolation:
    """One reason a plan may not execute."""

    family: str                    # graph | authority | dataflow | budget | scientific
    task_id: str
    detail: str

    def __str__(self) -> str:
        where = f" [{self.task_id}]" if self.task_id else ""
        return f"{self.family}{where}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    """A plan that passed, plus the per-task envelopes the loop will execute under.

    The envelopes are returned rather than recomputed later. Recomputing them would put a
    second construction path beside the validated one, which is exactly the shape of defect
    this package keeps closing — the validator would be attesting to envelopes that are not
    the ones used.
    """

    plan: Plan
    envelopes: Mapping[str, RunEnvelope]
    order: tuple[str, ...]

    def envelope_for(self, task_id: str) -> RunEnvelope:
        return self.envelopes[task_id]


class PlanValidator:
    """Decides whether a typed plan may execute under a given run envelope."""

    def __init__(self, registry: Any = None,
                 known_test_kinds: Iterable[str] | None = None) -> None:
        #: Optional. When present, a task naming a component that is not registered, or one
        #: the run's authority excludes, is a plan error rather than a runtime surprise.
        self.registry = registry
        from .evaluator import ACCEPTANCE_CHECKS
        self.known_test_kinds = frozenset(known_test_kinds
                                          if known_test_kinds is not None
                                          else ACCEPTANCE_CHECKS)
        self.validations = 0

    # ------------------------------------------------------------------ public
    def validate(self, plan: Plan, envelope: RunEnvelope, *,
                 policy: Any = None) -> ValidatedPlan:
        """Return a ``ValidatedPlan`` or raise ``PlanRejected`` listing every violation."""
        self.validations += 1
        violations: list[PlanViolation] = []

        order = self._graph_violations(plan, violations)
        envelopes = self._authority_violations(plan, envelope, violations)
        self._dataflow_violations(plan, envelope, violations)
        self._budget_violations(plan, envelope, violations)
        self._scientific_violations(plan, policy, violations)

        if violations:
            raise PlanRejected(
                f"plan {plan.plan_id} is not executable under run {envelope.run_id}: "
                + "; ".join(str(v) for v in violations[:8])
                + (f" (+{len(violations) - 8} more)" if len(violations) > 8 else ""),
                violations)
        return ValidatedPlan(plan=plan, envelopes=envelopes, order=order)

    # ------------------------------------------------------------------ graph
    def _graph_violations(self, plan: Plan,
                          out: list[PlanViolation]) -> tuple[str, ...]:
        """Topologically order the plan, reporting cycles and dangling dependencies."""
        ids = {t.task_id for t in plan.tasks}
        for task in plan.tasks:
            for dependency in task.dependencies:
                if dependency not in ids:
                    out.append(PlanViolation(
                        "graph", task.task_id,
                        f"depends on {dependency!r}, which is not in this plan"))

        # Kahn's algorithm. Anything left when the queue empties is in a cycle, which is
        # reported by name rather than as "a cycle exists" — a planner needs to know which.
        pending = {t.task_id: [d for d in t.dependencies if d in ids] for t in plan.tasks}
        ordered: list[str] = []
        ready = [tid for tid, deps in pending.items() if not deps]
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for tid, deps in pending.items():
                if current in deps:
                    deps.remove(current)
                    if not deps and tid not in ordered and tid not in ready:
                        ready.append(tid)
        stuck = sorted(set(pending) - set(ordered))
        if stuck:
            out.append(PlanViolation(
                "graph", "", f"these tasks are in a dependency cycle: {stuck}"))
        if not plan.tasks:
            out.append(PlanViolation("graph", "", "a plan with no tasks cannot satisfy "
                                                  "its objective"))
        return tuple(ordered)

    # -------------------------------------------------------------- authority
    def _authority_violations(self, plan: Plan, envelope: RunEnvelope,
                              out: list[PlanViolation]) -> dict[str, RunEnvelope]:
        """Build each task's envelope with ``restrict``, which IS the lattice."""
        envelopes: dict[str, RunEnvelope] = {}
        for task in plan.tasks:
            try:
                envelopes[task.task_id] = task_envelope(task, envelope)
            except PolicyDenied as exc:
                out.append(PlanViolation("authority", task.task_id, str(exc)))
            if self.registry is not None and task.kind == TaskKind.TOOL:
                manifest = _manifest_for(self.registry, task.component_id)
                if manifest is None:
                    out.append(PlanViolation(
                        "authority", task.task_id,
                        f"component {task.component_id!r} is not registered"))
                    continue
                ok, why = manifest.compatible_with(envelopes.get(task.task_id, envelope))
                if not ok:
                    out.append(PlanViolation("authority", task.task_id, why))
        return envelopes

    # --------------------------------------------------------------- dataflow
    #: Where a model can actually be reached. A model task that names none of these has
    #: declared a reach that cannot contain the call it is going to make.
    _MODEL_DESTINATIONS = (Destination.LOCAL_MODEL, Destination.TRUSTED_REMOTE,
                           Destination.PUBLIC_REMOTE)

    def _dataflow_violations(self, plan: Plan, envelope: RunEnvelope,
                             out: list[PlanViolation]) -> None:
        for task in plan.tasks:
            if (task.kind == TaskKind.MODEL and task.destinations
                    and not any(d in self._MODEL_DESTINATIONS
                                for d in task.destinations)):
                # Caught here rather than at execution. The task's envelope is built by
                # narrowing the run to ``task.destinations``, so a model task declaring
                # only LOCAL_COMPUTE mints an envelope that forbids the very call the task
                # exists to make — and the model gateway then refuses it, correctly, at
                # step N with N-1 steps already executed. It is a plan error, so it is
                # reported as one, before anything runs.
                out.append(PlanViolation(
                    "dataflow", task.task_id,
                    f"is a model call but declares destinations "
                    f"{[d.name for d in task.destinations]}; name the destination the "
                    f"model runs at (one of "
                    f"{[d.name for d in self._MODEL_DESTINATIONS]})"))
            label = DataLabel(task.max_label)
            for destination in task.destinations:
                if not envelope.permits_destination(destination):
                    out.append(PlanViolation(
                        "dataflow", task.task_id,
                        f"destination {destination.name} is not permitted by this run"))
                elif not label.permits(destination):
                    out.append(PlanViolation(
                        "dataflow", task.task_id,
                        f"{task.max_label.name} data may not reach {destination.name}"))
            self._binding_violations(plan, task, out)

    #: A payload literal that looks like a reference to another task's result. Nothing
    #: resolves it: the component receives the string. The 2026-09-18 review ran a plan
    #: whose payload said ``"symbol": "$fetch.gene"`` and the tool looked up ``$fetch.gene``.
    _REFERENCE = re.compile(r"^\$\{?([A-Za-z_][\w-]*)(?:[./][\w-]+)*\}?$")

    def _binding_violations(self, plan: Plan, task: PlanTask,
                            out: list[PlanViolation]) -> None:
        ids = {t.task_id for t in plan.tasks}
        for binding in task.inputs:
            if binding.source not in ids:
                out.append(PlanViolation(
                    "dataflow", task.task_id,
                    f"input {binding.argument!r} is bound to task {binding.source!r}, "
                    "which is not in this plan"))
            if binding.argument in task.payload:
                out.append(PlanViolation(
                    "dataflow", task.task_id,
                    f"argument {binding.argument!r} is both a payload literal and an input "
                    "binding; a value has one source"))
        for key, value in task.payload.items():
            if isinstance(value, str):
                match = self._REFERENCE.match(value.strip())
                if match:
                    out.append(PlanViolation(
                        "dataflow", task.task_id,
                        f"payload argument {key!r} is the literal string {value!r}, which "
                        f"reads like a reference to task {match.group(1)!r}; a payload is "
                        "never resolved, so declare an input binding instead: "
                        f"inputs=[{{argument: {key!r}, source: {match.group(1)!r}, "
                        "pointer: '/<field>'}]"))

    # ----------------------------------------------------------------- budget
    def _budget_violations(self, plan: Plan, envelope: RunEnvelope,
                           out: list[PlanViolation]) -> None:
        """Feasibility, not accounting: a plan that cannot fit should not start."""
        budget = envelope.budget
        if plan.estimated_tokens > budget.tokens_hard:
            out.append(PlanViolation(
                "budget", "", f"plan estimates {plan.estimated_tokens} tokens against a "
                              f"hard ceiling of {budget.tokens_hard}"))
        if plan.estimated_usd > budget.usd_hard:
            out.append(PlanViolation(
                "budget", "", f"plan estimates ${plan.estimated_usd:.2f} against a hard "
                              f"ceiling of ${budget.usd_hard:.2f}"))

        # The critical path, not the sum: independent tasks do not add wall-clock even
        # though this runtime executes them one at a time, and charging a plan for a
        # parallelism it is permitted to have would refuse plans that are in fact feasible.
        critical = _critical_path_seconds(plan)
        if critical > budget.seconds_hard:
            out.append(PlanViolation(
                "budget", "", f"the plan's critical path is {critical:.0f}s against a hard "
                              f"ceiling of {budget.seconds_hard:.0f}s"))

        model_tasks = sum(1 for t in plan.tasks if t.kind == TaskKind.MODEL)
        tool_tasks = sum(1 for t in plan.tasks if t.kind == TaskKind.TOOL)
        delegations = sum(1 for t in plan.tasks if t.kind == TaskKind.DELEGATE)
        for count, ceiling, what in ((model_tasks, budget.max_model_calls, "model calls"),
                                     (tool_tasks, budget.max_tool_calls, "tool calls"),
                                     (delegations, budget.max_delegations, "delegations")):
            if count > ceiling:
                out.append(PlanViolation(
                    "budget", "", f"plan needs {count} {what} against a ceiling of {ceiling}"))

    # ------------------------------------------------------------- scientific
    def _scientific_violations(self, plan: Plan, policy: Any,
                               out: list[PlanViolation]) -> None:
        if not plan.completion_criteria:
            out.append(PlanViolation(
                "scientific", "",
                "a plan with no completion criteria has no definition of done, so the "
                "loop could only terminate on exhaustion"))
        for task in plan.tasks:
            for test in task.acceptance_tests:
                if test.kind not in self.known_test_kinds:
                    # Not a warning. A test whose kind is unknown would be silently skipped,
                    # and a skipped acceptance test reads as a passed one.
                    out.append(PlanViolation(
                        "scientific", task.task_id,
                        f"acceptance test kind {test.kind!r} has no checker; known kinds "
                        f"are {sorted(self.known_test_kinds)}"))
        if policy is not None and getattr(policy, "require_claim_support", False):
            # ``evidence_requirements`` are strings the planner wrote; they describe an
            # intention and nothing executes them. Under a policy that requires claim
            # support, only a task that declares ``evidence_required`` — which the
            # evaluator checks — counts as producing evidence. Accepting the strings let
            # a plan satisfy the strictest policy by wording alone.
            if not any(t.evidence_required for t in plan.tasks):
                out.append(PlanViolation(
                    "scientific", "",
                    "this policy requires claim support but no task in the plan declares "
                    "evidence_required, so nothing the plan returns could be supported; "
                    "plan-level evidence_requirements are documentation, not a check"))


# ------------------------------------------------------------------- helpers

def task_envelope(task: PlanTask, parent: RunEnvelope) -> RunEnvelope:
    """The envelope a task executes under: the parent, narrowed by the task's declaration.

    One function, used by the validator, the loop and the resume path, so what was
    validated is what runs. ``restrict`` raises ``PolicyDenied`` when the result would
    exceed the run, which is the whole check — there is no separate comparison here to
    drift from the lattice.

    A task's authority fields are two different kinds of statement, and conflating them is
    how this function was first written:

    *Self-imposed ceilings* — ``max_risk``, ``max_label``, ``autonomy``. The names say so:
    "this task incurs at most R2", "handles at most PHI". A ceiling meets the run's, and
    the lower of the two wins. Refusing instead would mean a task declaring the default
    ``max_label=RESEARCH_DEIDENTIFIED`` could not run under an ``INTERNAL`` policy — a
    default that is not the weakest statement of its field, which is the
    ``min_autonomy = ACT`` mistake in another costume.

    *Required reach* — ``destinations``, ``capability_requirements``. A task naming
    ``PUBLIC_REMOTE`` is saying it must get there. Silently narrowing that to nothing would
    hand the task an envelope forbidding the very call it exists to make, so a reach the
    run does not hold is refused, loudly, at the plan.

    ``risk`` was already met with ``min()`` here while the other two ceilings were passed
    through unclamped, so a ceiling applied on one dimension and refused on the next.
    """
    from ..contracts import _autonomy_rank

    if task.input_sensitivity > min(task.max_label, parent.max_label.sensitivity):
        raise PolicyDenied("known input sensitivity exceeds the task/run ceiling")
    return parent.restrict(
        task_id=task.task_id,
        # Ceilings: the lower of the task's and the run's.
        risk=min(task.max_risk, parent.risk),
        max_label=(DataLabel(task.max_label)
                   if task.max_label <= parent.max_label.sensitivity
                   else parent.max_label),
        autonomy=(task.autonomy
                  if _autonomy_rank(task.autonomy) >= _autonomy_rank(parent.autonomy)
                  else parent.autonomy),
        # Reach: refused if the run does not hold it. An undeclared destination set
        # inherits the run's rather than narrowing to nothing — a task that states
        # destinations narrows; one that does not, does not.
        allowed_destinations=(frozenset(task.destinations) if task.destinations
                              else parent.allowed_destinations),
        allowed_capabilities=(tuple(task.capability_requirements)
                              or parent.allowed_capabilities),
        budget=parent.budget.child(fraction=1.0))


def _manifest_for(registry: Any, component_id: str) -> Any:
    for getter in ("manifest_for", "manifest"):
        fn = getattr(registry, getter, None)
        if callable(fn):
            try:
                found = fn(component_id)
                if found is not None:
                    return found
            except Exception:  # noqa: BLE001 - registries differ; fall through
                pass
    component = None
    fn = getattr(registry, "component", None)
    if callable(fn):
        try:
            component = fn(component_id)
        except Exception:  # noqa: BLE001
            component = None
    return getattr(component, "manifest", None)


def _critical_path_seconds(plan: Plan) -> float:
    """Longest dependency chain by estimated seconds. Cycles are reported elsewhere."""
    by_id = {t.task_id: t for t in plan.tasks}
    memo: dict[str, float] = {}

    def cost(task_id: str, seen: frozenset[str]) -> float:
        if task_id in seen:                      # a cycle; the graph family reports it
            return 0.0
        if task_id in memo:
            return memo[task_id]
        task = by_id.get(task_id)
        if task is None:
            return 0.0
        upstream = max((cost(d, seen | {task_id}) for d in task.dependencies), default=0.0)
        memo[task_id] = upstream + task.estimated_seconds
        return memo[task_id]

    return max((cost(t.task_id, frozenset()) for t in plan.tasks), default=0.0)
