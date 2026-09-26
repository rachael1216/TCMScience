"""A typed plan, so ``validate_plan`` has something to validate.

``Runner._plan()`` split the request on full stops and returned a list of strings, and the
``validate_plan`` stage recorded ``"placeholder planner: no typed plan to validate"``. That
was honest, and it is also the reason the stage could not enforce anything: a list of
sentences has no dimension a policy can be checked against. There is nothing to compare to
a risk ceiling in ``"summarise the HFpEF evidence"``.

So a plan is a typed object whose every step carries the authority it intends to use. That
is what makes the next module possible: ``PlanValidator`` can refuse a plan *before* any of
it runs, on the same lattice the envelope uses, rather than discovering at step four that
step four was never permitted.

The shape follows the review's list, with one deliberate difference. The review proposed
``capability_requirements: list[str]`` and ``max_risk``/``max_label`` per task; this adds
``kind`` and makes it exhaustive — ``model``, ``tool`` or ``delegate`` — because the loop
has to route each task to one of exactly three broker methods, and a plan that does not say
which is a plan whose execution requires guessing. A field the executor has to infer is a
field the validator cannot check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts import Autonomy, RiskTier, new_id
from ..labels import Destination, Sensitivity

__all__ = ["Plan", "PlanTask", "TaskKind", "TestSpec", "Criterion", "RetryPolicy",
           "RetryBudget", "InputBinding"]


class TaskKind(str):
    """The three things a plan step can be. Not an Enum only so it renders plainly."""

    MODEL = "model"
    TOOL = "tool"
    DELEGATE = "delegate"


_KINDS = frozenset({TaskKind.MODEL, TaskKind.TOOL, TaskKind.DELEGATE})


@dataclass(frozen=True, slots=True)
class TestSpec:
    """One acceptance test, named so a runner can dispatch it without interpreting prose.

    ``acceptance_tests`` was ``tuple[str, ...]`` on ``DelegationContract`` and nothing ever
    executed it. A string cannot be dispatched, so the field could only ever be
    documentation. ``kind`` is matched against the checker table in ``evaluator.py``; an
    unknown kind is a plan validation error rather than a silently skipped test, because a
    test that is skipped when it is not understood is worse than no test at all.
    """

    #: Not a pytest class. The name collides with pytest's ``Test*`` collection rule, and
    #: a warning on every run is the kind of noise people learn to scroll past.
    __test__ = False

    kind: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("an acceptance test must name its kind")


_BINDING_TYPES = frozenset({"", "string", "integer", "number", "boolean", "array", "object"})
_CARDINALITIES = frozenset({"one", "many"})
_POINTER = re.compile(r"^(?:/[^/]*)*$")


@dataclass(frozen=True, slots=True)
class InputBinding:
    """One argument of a task, filled from a value an upstream task produced.

    The loop handed a dependent tool its upstream results under one payload key,
    ``upstream`` — every dependency's whole result — and left the arguments the component
    actually takes to the planner's literals. The 2026-09-18 review showed the
    consequence: a plan writing ``"symbol": "$fetch.gene"`` executed with the literal
    string as the symbol, and the BioScience bridge, whose entrypoints take named keyword
    arguments, dropped ``upstream`` because no argument is called that. Data flowed
    between steps only as a blob no step could read.

    A binding names the argument, the task that produces the value, where in that task's
    result the value sits (a JSON pointer, RFC 6901: ``/gene``, ``/hits/0/id``, ``""`` for
    the whole result), what type the argument expects and whether it takes one value or a
    list. The validator checks it against the plan before anything runs; the loop
    resolves it at dispatch, and the resolved value keeps the upstream result's label so
    the join is not lost at the edge. A pointer is structure, never data: do not encode
    an identifier in one.
    """

    argument: str
    source: str
    pointer: str = ""
    expected_type: str = ""
    cardinality: str = "one"
    unit: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        if not self.argument.strip():
            raise ValueError("an input binding must name the argument it fills")
        if not self.source.strip():
            raise ValueError(
                f"input binding {self.argument!r} must name the task it reads from")
        if self.pointer and not _POINTER.match(self.pointer):
            raise ValueError(
                f"input binding {self.argument!r} has pointer {self.pointer!r}; a pointer "
                "is empty (the whole result) or a JSON pointer such as /gene or /hits/0/id")
        if self.expected_type not in _BINDING_TYPES:
            raise ValueError(
                f"input binding {self.argument!r} expects type {self.expected_type!r}; "
                f"legal types are {sorted(t for t in _BINDING_TYPES if t)} or none")
        if self.cardinality not in _CARDINALITIES:
            raise ValueError(
                f"input binding {self.argument!r} has cardinality {self.cardinality!r}; "
                "it is 'one' or 'many'")

    def to_dict(self) -> dict[str, Any]:
        return {"argument": self.argument, "source": self.source, "pointer": self.pointer,
                "expected_type": self.expected_type, "cardinality": self.cardinality,
                "unit": self.unit, "required": self.required}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InputBinding":
        return cls(
            argument=str(data.get("argument") or ""), source=str(data.get("source") or ""),
            pointer=str(data.get("pointer") or ""),
            expected_type=str(data.get("expected_type") or data.get("type") or ""),
            cardinality=str(data.get("cardinality") or "one"),
            unit=str(data.get("unit") or ""), required=bool(data.get("required", True)))


@dataclass(frozen=True, slots=True)
class Criterion:
    """One condition that must hold for the plan's objective to be considered met."""

    description: str
    kind: str = "manual"          # evidence | artifact | claim | task | manual

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("a completion criterion must describe what it requires")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """When and how a failed task is retried.

    ``PSHConfig.max_retries = 2`` existed and the runtime never read it, so retry was a
    configuration field with no behaviour. Retries also are not free: each attempt consumes
    tokens, dollars and wall-clock from the same envelope, so the loop charges them to the
    same ``BudgetGovernor`` as the first attempt rather than treating a retry as exempt.
    """

    max_attempts: int = 1
    initial_delay_s: float = 0.0
    factor: float = 2.0
    max_delay_s: float = 30.0
    #: Exception type *names*, so a policy can be declared without importing the classes.
    retryable: tuple[str, ...] = ("ContractViolation", "TimeoutError", "OSError")

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.factor < 1.0:
            raise ValueError("an exponential backoff factor below 1 shrinks the delay")

    def delay_for(self, attempt: int) -> float:
        """Delay before ``attempt`` (1-based). Deterministic: no jitter.

        Jitter exists to decorrelate many clients hitting one service. This runtime is
        single-threaded and its tests assert on timing, so jitter here would buy nothing and
        cost reproducibility. It belongs with the parallel scheduler, and is noted there.
        """
        if attempt <= 1 or self.initial_delay_s <= 0:
            return 0.0
        return min(self.initial_delay_s * (self.factor ** (attempt - 2)), self.max_delay_s)

    def permits(self, exc: BaseException) -> bool:
        names = {type(exc).__name__, *(c.__name__ for c in type(exc).__mro__)}
        return bool(names & set(self.retryable))


@dataclass(frozen=True, slots=True)
class RetryBudget:
    """Separate attempt counts per failure class, rather than one number for everything.

    A validation failure and a transport failure are not the same event and should not
    share a counter: retrying a schema violation three times runs the same model three
    times to get the same malformed answer, while retrying a dropped connection once is
    almost always right. One ``max_retries`` cannot express that.
    """

    transport: int = 3
    provider: int = 2
    tool: int = 2
    validation: int = 2
    subagent: int = 1
    replan: int = 2


@dataclass(frozen=True, slots=True)
class PlanTask:
    """One step, carrying the authority it intends to use.

    Every authority field is a *request*, not a grant. ``PlanValidator`` checks each against
    the run envelope and the loop derives the task's envelope with ``envelope.restrict()``,
    so a task cannot execute with authority the run does not hold even if the planner asked
    for it — the same containment rule as everywhere else, applied one level further down.
    """

    task_id: str
    objective: str
    kind: str = TaskKind.MODEL
    dependencies: tuple[str, ...] = ()
    #: For ``kind="tool"``: the component to invoke. For the others, advisory.
    component_id: str = ""
    capability_requirements: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    # authority requested
    max_risk: RiskTier = RiskTier.R1_ROUTINE
    max_label: Sensitivity = Sensitivity.RESEARCH_DEIDENTIFIED
    #: Empty means "whatever the run permits" — the task narrows nothing on this dimension.
    #: It is NOT ``(LOCAL_COMPUTE,)``: the default kind is a model call, and a default pair
    #: of "model call" and "local compute only" would make every task that never thought
    #: about destinations mint an envelope forbidding the call it exists to make. That is
    #: the ``min_autonomy = ACT`` mistake — a default that is the strongest statement of a
    #: field rather than the weakest — and it is worth not repeating.
    destinations: tuple[Destination, ...] = ()
    autonomy: Autonomy = Autonomy.SUGGEST
    # economics, for feasibility rather than for billing
    estimated_tokens: int = 0
    estimated_usd: float = 0.0
    estimated_seconds: float = 0.0
    # the executable contract
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    acceptance_tests: tuple[TestSpec, ...] = ()
    evidence_required: bool = False
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    #: Arguments filled from upstream results. Each names a task in ``dependencies``.
    inputs: tuple[InputBinding, ...] = ()
    #: Known data sensitivity floor, preserved through compilation and checkpoints.
    #: Unlike max_label (a ceiling), this can only raise the runtime ingress label.
    input_sensitivity: Sensitivity = Sensitivity.PUBLIC

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("a plan task requires an id")
        if not isinstance(self.input_sensitivity, Sensitivity):
            raise ValueError("input_sensitivity must be a Sensitivity")
        if not self.objective.strip():
            raise ValueError(f"plan task {self.task_id!r} requires an objective")
        if self.kind not in _KINDS:
            raise ValueError(
                f"plan task {self.task_id!r} has kind {self.kind!r}; the loop routes a task "
                f"to one of {sorted(_KINDS)} and cannot infer a fourth")
        if self.kind == TaskKind.TOOL and not self.component_id:
            raise ValueError(
                f"plan task {self.task_id!r} is a tool call and names no component")
        if self.task_id in self.dependencies:
            raise ValueError(f"plan task {self.task_id!r} depends on itself")
        if self.evidence_required and not self.output_schema:
            # The same rule DelegationContract already states: an evidence requirement
            # implies a structured return, because there is nothing to check evidence
            # against in free text.
            raise ValueError(
                f"plan task {self.task_id!r} requires evidence, so it needs an "
                "output_schema to return it in")
        seen_arguments: set[str] = set()
        for binding in self.inputs:
            if binding.source == self.task_id:
                raise ValueError(
                    f"plan task {self.task_id!r} binds {binding.argument!r} to its own result")
            if binding.source not in self.dependencies:
                raise ValueError(
                    f"plan task {self.task_id!r} binds {binding.argument!r} to task "
                    f"{binding.source!r}, which is not among its dependencies "
                    f"{list(self.dependencies)}; a value can only be read from a task "
                    "that is guaranteed to have finished first")
            if binding.argument in seen_arguments:
                raise ValueError(
                    f"plan task {self.task_id!r} binds argument {binding.argument!r} twice")
            seen_arguments.add(binding.argument)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "objective": self.objective, "kind": self.kind,
            "dependencies": list(self.dependencies), "component_id": self.component_id,
            "capability_requirements": list(self.capability_requirements),
            "payload": dict(self.payload),
            "max_risk": self.max_risk.name, "max_label": self.max_label.name,
            "destinations": [d.name for d in self.destinations],
            "autonomy": self.autonomy.value,
            "estimated_tokens": self.estimated_tokens,
            "estimated_usd": self.estimated_usd,
            "estimated_seconds": self.estimated_seconds,
            "output_schema": dict(self.output_schema),
            "acceptance_tests": [{"kind": t.kind, "detail": dict(t.detail)}
                                 for t in self.acceptance_tests],
            "evidence_required": self.evidence_required,
            "inputs": [b.to_dict() for b in self.inputs],
            "input_sensitivity": self.input_sensitivity.name,
            "retry": {"max_attempts": self.retry.max_attempts,
                      "initial_delay_s": self.retry.initial_delay_s,
                      "factor": self.retry.factor,
                      "max_delay_s": self.retry.max_delay_s,
                      "retryable": list(self.retry.retryable)},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanTask":
        retry = data.get("retry") or {}
        return cls(
            task_id=str(data["task_id"]), objective=str(data.get("objective") or ""),
            kind=str(data.get("kind") or TaskKind.MODEL),
            dependencies=tuple(data.get("dependencies") or ()),
            component_id=str(data.get("component_id") or ""),
            capability_requirements=tuple(data.get("capability_requirements") or ()),
            payload=dict(data.get("payload") or {}),
            max_risk=RiskTier[data.get("max_risk", RiskTier.R1_ROUTINE.name)],
            max_label=Sensitivity[data.get("max_label",
                                           Sensitivity.RESEARCH_DEIDENTIFIED.name)],
            destinations=tuple(Destination[d] for d in data.get("destinations") or ()),
            autonomy=Autonomy(data.get("autonomy", Autonomy.SUGGEST.value)),
            estimated_tokens=int(data.get("estimated_tokens") or 0),
            estimated_usd=float(data.get("estimated_usd") or 0.0),
            estimated_seconds=float(data.get("estimated_seconds") or 0.0),
            output_schema=dict(data.get("output_schema") or {}),
            acceptance_tests=tuple(
                TestSpec(kind=t["kind"], detail=dict(t.get("detail") or {}))
                for t in data.get("acceptance_tests") or ()),
            evidence_required=bool(data.get("evidence_required")),
            inputs=tuple(InputBinding.from_dict(b) for b in data.get("inputs") or ()),
            input_sensitivity=Sensitivity[data.get("input_sensitivity", "PUBLIC")],
            retry=RetryPolicy(
                max_attempts=int(retry.get("max_attempts", 1)),
                initial_delay_s=float(retry.get("initial_delay_s", 0.0)),
                factor=float(retry.get("factor", 2.0)),
                max_delay_s=float(retry.get("max_delay_s", 30.0)),
                retryable=tuple(retry.get("retryable")
                                or RetryPolicy().retryable)))


@dataclass(frozen=True, slots=True)
class Plan:
    """A typed, validatable plan for one objective."""

    objective: str
    tasks: tuple[PlanTask, ...] = ()
    assumptions: tuple[str, ...] = ()
    completion_criteria: tuple[Criterion, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    plan_id: str = field(default_factory=lambda: new_id("plan"))
    produced_by: str = "unknown"

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("a plan requires an objective")
        seen: set[str] = set()
        for task in self.tasks:
            if task.task_id in seen:
                raise ValueError(f"duplicate plan task id {task.task_id!r}")
            seen.add(task.task_id)

    def task(self, task_id: str) -> PlanTask | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    @property
    def estimated_tokens(self) -> int:
        return sum(t.estimated_tokens for t in self.tasks)

    @property
    def estimated_usd(self) -> float:
        return sum(t.estimated_usd for t in self.tasks)

    def summary(self) -> str:
        kinds: dict[str, int] = {}
        for task in self.tasks:
            kinds[task.kind] = kinds.get(task.kind, 0) + 1
        return (f"{len(self.tasks)} task(s) {kinds}, ~{self.estimated_tokens} tokens, "
                f"~${self.estimated_usd:.2f}, {len(self.completion_criteria)} criteria")

    # ------------------------------------------------------------ persistence
    #
    # A plan has to survive a checkpoint, and round-tripping it through the same
    # constructors that validate it is what stops a checkpoint from becoming a way to
    # introduce a plan that ``PlanTask.__post_init__`` would have refused. ``from_dict``
    # is not a deserialiser that trusts its input; it is the constructor with a different
    # argument shape.

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id, "objective": self.objective,
            "produced_by": self.produced_by,
            "assumptions": list(self.assumptions),
            "evidence_requirements": list(self.evidence_requirements),
            "completion_criteria": [{"description": c.description, "kind": c.kind}
                                    for c in self.completion_criteria],
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Plan":
        return cls(
            objective=str(data.get("objective") or ""),
            tasks=tuple(PlanTask.from_dict(t) for t in data.get("tasks") or ()),
            assumptions=tuple(data.get("assumptions") or ()),
            completion_criteria=tuple(
                Criterion(description=c["description"], kind=c.get("kind", "manual"))
                for c in data.get("completion_criteria") or ()),
            evidence_requirements=tuple(data.get("evidence_requirements") or ()),
            plan_id=str(data.get("plan_id") or new_id("plan")),
            produced_by=str(data.get("produced_by") or "unknown"))
