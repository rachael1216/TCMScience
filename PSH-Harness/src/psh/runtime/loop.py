"""AgentLoopController: a bounded plan/act/observe/evaluate loop that cannot bypass the kernel.

``Runner.run`` is a straight line — classify, compile, **one** model call, verify, release.
That is a governed execution of a single step, not an agent. This adds the loop, and the
whole design question is the one the review puts most sharply:

    AgentLoopController                     AgentLoop
             |                                 |-- calls a provider directly
        ExecutionBroker                        |-- spawns a subprocess
             |                                 |-- opens a socket
        TrustedKernel                          '-- spawns an agent

The second shape is what most agent runtimes are, and adopting it would discard everything
this package is for. So the controller holds **no** provider, no ``subprocess``, no socket
and no registry of raw callables. It holds a kernel, and every action it takes is one of
exactly three broker calls:

    TaskKind.MODEL    -> kernel.broker.call_model
    TaskKind.TOOL     -> kernel.broker.call_tool
    TaskKind.DELEGATE -> kernel.broker.delegate

``test_loop.py`` asserts that by counting: the broker's counters must account for every
action a loop performed. A loop that found a fourth way to do something fails that test.

**Boundedness is the other half.** The review is right that "loop termination" in v0.5 was
a repeated-request counter and not a controller. Every one of these stops the loop, and the
reason is reported rather than inferred:

    goal_satisfied      max_iterations     budget_exhausted    deadline
    no_progress         max_replans        plan_rejected       policy_denied
    escalated           unrecoverable_error

A loop that can only end by succeeding is not bounded, so the default limits are finite and
the terminal state always names which one it hit.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..contracts import (
    ApprovalRequired, BudgetExhausted, ContractViolation, EgressDenied, OperationUnresolved,
    PolicyDenied, RunEnvelope, ToolTimeout, new_id,
)
from ..labels import DataLabel, Destination, Labeled, combine, label_of, unwrap
from .bindings import resolve_bindings
from .evaluator import Evaluator, Verdict
from .execgraph import ExecutionGraph, TaskState
from .operations import OperationState
from .plan import Plan, PlanTask, RetryBudget, TaskKind
from .plan_validator import PlanRejected, PlanValidator, ValidatedPlan, task_envelope

__all__ = ["AgentLoopController", "LoopState", "LoopResult", "Termination", "LoopLimits",
           "Planner", "StaticPlanner", "classify_with", "graph_label", "objective_label_for"]


class Termination(str, Enum):
    """Why a loop stopped. Never inferred from the absence of something else."""

    GOAL_SATISFIED = "goal_satisfied"
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE = "deadline"
    NO_PROGRESS = "no_progress"
    MAX_REPLANS = "max_replans"
    PLAN_REJECTED = "plan_rejected"
    POLICY_DENIED = "policy_denied"
    ESCALATED = "escalated"
    UNRECOVERABLE_ERROR = "unrecoverable_error"
    #: Asked to stop by whoever holds the cancellation token — a supervisor cascading a
    #: parent's cancellation, or an operator. Cooperative: checked between iterations,
    #: never mid-call, so a task that has started finishes or times out on its own terms.
    CANCELLED = "cancelled"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class LoopLimits:
    """Every bound in one object, so "is this loop bounded?" has one place to look."""

    max_iterations: int = 12
    max_replans: int = 2
    #: Consecutive iterations producing an identical progress digest before stopping. The
    #: digest is over *achieved state*, not over the request — a loop that re-asks the same
    #: question after making progress is working, and v0.5's request-hash detector could
    #: not tell those apart.
    max_no_progress: int = 2
    retries: RetryBudget = field(default_factory=RetryBudget)
    #: How many *independent* ready tasks may execute at once. 1 is sequential, which
    #: is the default because it is the easier behaviour to reason about; above 1 the
    #: ready set of each iteration runs on a bounded thread pool. Nothing about the
    #: governance changes: every branch is still a broker call, the gates and the budget
    #: governor are locked, and a refusal in one branch is that branch's failure.
    max_parallel: int = 1

    def __post_init__(self) -> None:
        for name in ("max_iterations", "max_replans", "max_no_progress"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.max_iterations < 1:
            raise ValueError("a loop with no iterations cannot do anything")
        if self.max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")


@dataclass
class LoopState:
    """Everything one loop knows. Serialisable, so a checkpoint is a copy of this.

    Checkpointing is not implemented in this release — ``Runner``'s ``checkpoint`` stage is
    still an audit event — but the state a checkpoint would have to carry is collected here
    rather than spread across local variables, which is the part that is hard to retrofit.
    """

    loop_id: str
    objective: str
    envelope: RunEnvelope
    iteration: int = 0
    replans: int = 0
    no_progress: int = 0
    plan: Plan | None = None
    validated: ValidatedPlan | None = None
    graph: ExecutionGraph | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    digests: list[str] = field(default_factory=list)
    termination: Termination = Termination.RUNNING
    detail: str = ""
    #: The objective's classification, computed once at ingress (``objective_label_for``)
    #: and carried onto every context item built from the objective. ``Runner.run``
    #: classifies its request at stage 1 and labels the turn item with the result; the
    #: loop and the planner built their turn and instruction items with the default label,
    #: which is PUBLIC, and a PHI objective reached a public provider under that verdict.
    objective_label: Any = None
    #: The label of the plan in force: for a model-authored plan, the join of everything
    #: the planner's model was shown and the classification of what it wrote. A task
    #: objective is a derived value — derived from that prompt — and carries this label.
    #: ``None`` for a plan the caller wrote (``StaticPlanner``), whose task text is
    #: classified on its own.
    plan_label: Any = None
    #: Whether this loop holds a delegate backend. Set by the controller before planning
    #: and not checkpointed: it is a fact about the loop that runs the state, not about
    #: the state, so a resumed loop states its own. The planner reads it to tell the model
    #: whether ``kind="delegate"`` tasks have anywhere to go.
    can_delegate: bool = False

    def observe(self, **fields: Any) -> None:
        self.observations.append({"iteration": self.iteration, **fields})

    def envelope_for(self, task_id: str) -> RunEnvelope:
        """The authority one task executes under.

        Prefers the envelope the validator already built, and computes it with the same
        ``task_envelope`` function when there is none — a resumed state has a plan and a
        graph but no ``ValidatedPlan``, and dereferencing that was an ``AttributeError``
        reported as ``unrecoverable_error``, which is a true statement about the loop and a
        useless one about the cause.

        The fallback is not a second construction path: it calls the one function the
        validator calls, so the value is identical either way.
        """
        if self.validated is not None:
            return self.validated.envelope_for(task_id)
        if self.plan is None:
            raise ContractViolation(
                f"no plan is loaded, so task {task_id!r} has no envelope to execute under")
        task = self.plan.task(task_id)
        if task is None:
            raise ContractViolation(f"task {task_id!r} is not in the loaded plan")
        return task_envelope(task, self.envelope)


@dataclass
class LoopResult:
    """What a loop produced and why it stopped."""

    loop_id: str
    run_id: str
    termination: Termination
    reason: str = ""
    results: dict[str, Any] = field(default_factory=dict)
    iterations: int = 0
    replans: int = 0
    failures: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()
    plan: Plan | None = None
    state_counts: dict[str, int] = field(default_factory=dict)
    broker_stats: dict[str, int] = field(default_factory=dict)

    #: The join of every task result's label. What a child hands back to a parent must be
    #: labelled at least as high as anything the child saw, or delegation launders.
    label: Any = None
    #: ``verified`` when every completion criterion was decided by a deterministic check
    #: or a goal checker; ``pending_manual`` when a criterion is ``manual`` and nobody has
    #: judged it; ``unverified`` otherwise. ``GOAL_SATISFIED`` says the tasks ran and the
    #: deterministic criteria held — this says whether the objective was actually judged
    #: met, which is a different claim and the one a release should rest on.
    goal_status: str = "unverified"
    #: Per task, what its component said it fell short on (a degraded result). Carried
    #: to the release as limitations; a value with a caveat is not a value without one.
    caveats: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.termination is Termination.GOAL_SATISFIED

    def summary(self) -> str:
        return (f"loop {self.loop_id[:12]} {self.termination.value} after "
                f"{self.iterations} iteration(s), {self.replans} replan(s): {self.reason}")


class Planner:
    """What the loop needs from a planner. A protocol, not a base class to inherit."""

    def plan(self, state: LoopState, *, feedback: Verdict | None = None) -> Plan:
        raise NotImplementedError


class StaticPlanner(Planner):
    """Returns a plan it was given. The honest default, and what the tests use.

    A model-backed planner belongs behind the broker like every other model call, and
    writing one that produces a *typed* plan reliably is a real piece of work rather than a
    detail of this module. Shipping a placeholder that splits on full stops and calling it a
    planner is how ``Runner._plan`` ended up being described as one, so this says what it
    is: the plan comes from the caller.
    """

    def __init__(self, plan: Plan | Callable[[LoopState, Verdict | None], Plan]) -> None:
        self._plan = plan
        self.calls = 0

    def plan(self, state: LoopState, *, feedback: Verdict | None = None) -> Plan:
        self.calls += 1
        if callable(self._plan):
            return self._plan(state, feedback)
        return self._plan


def classify_with(kernel: Any, value: Any, *, origin: str = "") -> DataLabel:
    """Classify a value through the kernel's classifier. PUBLIC only when it says so."""
    classify = getattr(kernel, "classify", None)
    if classify is None:                                     # pragma: no cover - test doubles
        return DataLabel()
    return classify(value, origin=origin).label


def objective_label_for(state: LoopState, kernel: Any) -> DataLabel:
    """The objective's label, classified once and remembered on the state."""
    if state.objective_label is None:
        state.objective_label = classify_with(kernel, state.objective, origin="loop_objective")
    return state.objective_label


def graph_label(graph: ExecutionGraph | None) -> DataLabel:
    """The join of every labelled task result in a graph; PUBLIC when there is none."""
    if graph is None:
        return DataLabel()
    return combine(*[n.label for n in graph.nodes.values()
                     if getattr(n, "label", None) is not None])


class AgentLoopController:
    """Drives plan -> act -> observe -> evaluate, through the kernel, within bounds."""

    def __init__(self, kernel: Any, *, planner: Planner,
                 registry: Any = None, model: Any = None,
                 model_invoke: Callable[[str], str] | None = None,
                 evaluator: Evaluator | None = None,
                 validator: PlanValidator | None = None,
                 limits: LoopLimits | None = None,
                 delegate_backend: Callable[[Any], Any] | None = None,
                 checkpoints: Any = None,
                 cancellation: Any = None,
                 heartbeat: Callable[[], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 memory: Any = None, memory_items: int = 4,
                 operations: Any = None, operation_namespace: str = "",
                 require_idempotent_tools: bool = False) -> None:
        self.kernel = kernel
        self.planner = planner
        self.registry = registry
        #: Optional ``OperationLedger``: every tool call is recorded by its idempotency
        #: key before it runs and after it reports, so a restart knows which
        #: side-effecting calls are in doubt. Without one the loop behaves as before —
        #: and a restart of a side-effecting plan has nothing to consult.
        self.operations = operations
        # Dynamic visits share the run budget but must not share operation keys.
        self.operation_namespace = operation_namespace
        self.require_idempotent_tools = require_idempotent_tools
        # Local protection survives replans on this controller, not process crashes.
        # Durable recovery still requires an OperationLedger.
        self._nonrepeatable_operations: set[str] = set()
        self._operation_lock = threading.Lock()
        #: Optional ``MemoryRetriever``: verified project memory relevant to a task's
        #: objective is compiled into that task's projection, labelled as stored. A
        #: retriever reads; the loop still cannot write to the graph.
        self.memory = memory
        self.memory_items = memory_items
        #: The model profile and invoke callable for ``TaskKind.MODEL`` tasks. Held so they
        #: can be handed to ``broker.call_model``; never called from here.
        self.model = model
        self.model_invoke = model_invoke
        self.evaluator = evaluator or Evaluator()
        self.validator = validator or PlanValidator(registry=registry)
        self.limits = limits or LoopLimits()
        self.delegate_backend = delegate_backend
        #: Optional ``CheckpointStore``. When present the loop snapshots after every
        #: iteration, so a crash costs one iteration rather than the run. Resuming is
        #: deliberately NOT automatic — see ``checkpoint.resume``, which re-meets the
        #: stored authority against the policy in force and may refuse.
        self.checkpoints = checkpoints
        #: A ``CancellationToken`` (or anything with a truthy ``cancelled``). The loop
        #: polls it at the same point it checks every other bound, so cancellation is a
        #: termination reason like the rest rather than an exception thrown across threads.
        self.cancellation = cancellation
        #: Proof of life for whoever is waiting on this loop. Called at every bounds check
        #: and before every task, so a long plan beats per task rather than per iteration.
        #: It cannot fire *during* a call that never returns — that is the point: its
        #: absence is how a supervisor learns a child is stuck inside one.
        self.heartbeat = heartbeat
        self._sleep = sleep

    # ------------------------------------------------------------------- public
    def run(self, objective: str, envelope: RunEnvelope, *,
            supports: Sequence[Any] = (),
            resume_from: LoopState | None = None,
            objective_label: Any = None) -> LoopResult:
        """Execute the loop until one of the termination conditions holds.

        ``resume_from`` continues a state rebuilt by ``checkpoint.resume``, which has
        already re-authorised it. This method does not rebuild it itself, because doing so
        would put a second authority-restoring path beside the one that performs the meet.

        ``objective_label`` is what the caller knows about the objective that its text does
        not show — a parent handing a child an objective derived from PHI. It is joined
        with the classification of the text and can only raise it.
        """
        state = resume_from or LoopState(loop_id=new_id("loop"), objective=objective,
                                         envelope=envelope)
        state.can_delegate = self.delegate_backend is not None
        # Classify at ingress, before anything is built from the objective. The same stage
        # Runner.run has, for the same reason.
        label = objective_label_for(state, self.kernel)
        if objective_label is not None:
            state.objective_label = label.merged_with(objective_label)
        self._audit("loop_started" if resume_from is None else "loop_continued", state,
                    objective_len=len(objective),
                    max_iterations=self.limits.max_iterations)
        feedback: Verdict | None = None

        try:
            while True:
                stop = self._check_bounds(state)
                if stop is not None:
                    return self._finish(state, stop, state.detail)

                state.iteration += 1

                if state.graph is None or state.graph.complete and feedback is not None \
                        and feedback.replan:
                    if not self._replan(state, feedback):
                        return self._finish(state, Termination.MAX_REPLANS,
                                            "no replans remain")
                    feedback = None

                ready = state.graph.ready()
                if ready:
                    self._execute_ready(state, ready)
                elif not state.graph.complete:
                    # Nothing ready and nothing finished: every remaining task is blocked.
                    state.graph.block_descendants("")

                self._record_progress(state)
                self._checkpoint(state)

                verdict = self.evaluator.evaluate(
                    state.plan, state.graph, supports=supports,
                    replans_left=self.limits.max_replans - state.replans)
                state.observe(verdict=verdict.reason,
                              states=state.graph.state_counts())
                self._audit("loop_iteration", state, verdict=verdict.reason[:120],
                            states=state.graph.state_counts())

                if verdict.done:
                    return self._finish(state, Termination.GOAL_SATISFIED, verdict.reason,
                                        verdict)
                if verdict.escalate:
                    return self._finish(state, Termination.ESCALATED, verdict.reason,
                                        verdict)
                if verdict.replan:
                    feedback = verdict
                    continue
                # retry or "tasks remain": loop round and let the bounds check run again.
                feedback = None

        except (BudgetExhausted,) as exc:
            return self._finish(state, Termination.BUDGET_EXHAUSTED, str(exc))
        except PlanRejected as exc:
            return self._finish(state, Termination.PLAN_REJECTED, str(exc))
        except (EgressDenied, PolicyDenied, ApprovalRequired) as exc:
            return self._finish(state, Termination.POLICY_DENIED,
                                f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a loop must terminate, not propagate
            return self._finish(state, Termination.UNRECOVERABLE_ERROR,
                                f"{type(exc).__name__}: {exc}")

    # -------------------------------------------------------------------- bounds
    def _check_bounds(self, state: LoopState) -> Termination | None:
        """Every reason to stop that is not a verdict. Checked before work, not after."""
        self._beat()
        if self.cancellation is not None and getattr(self.cancellation, "cancelled", False):
            state.detail = (getattr(self.cancellation, "reason", "") or
                            "cancelled by the holder of this loop's cancellation token")
            return Termination.CANCELLED
        if state.iteration >= self.limits.max_iterations:
            state.detail = (f"reached the {self.limits.max_iterations}-iteration ceiling; "
                            "stopping rather than continuing indefinitely")
            return Termination.MAX_ITERATIONS
        if state.envelope.expired:
            state.detail = "the run's deadline passed"
            return Termination.DEADLINE
        if state.no_progress >= self.limits.max_no_progress > 0:
            state.detail = (f"{state.no_progress} consecutive iterations produced an "
                            "identical execution state; the loop is not advancing")
            return Termination.NO_PROGRESS
        # Budget is checked by the governor at each broker call and raises; this is the
        # cheap pre-check so a loop that is already out of budget does not start an
        # iteration to discover it.
        try:
            self.kernel.budget.peek_model_call(state.envelope)
        except BudgetExhausted as exc:
            state.detail = str(exc)
            return Termination.BUDGET_EXHAUSTED
        return None

    def _record_progress(self, state: LoopState) -> None:
        digest = state.graph.progress_digest()
        if state.digests and digest == state.digests[-1]:
            state.no_progress += 1
        else:
            state.no_progress = 0
        state.digests.append(digest)

    # --------------------------------------------------------------------- plan
    def _replan(self, state: LoopState, feedback: Verdict | None) -> bool:
        """Produce and validate a plan. Returns False when no replan remains."""
        if state.plan is not None:
            if state.replans >= self.limits.max_replans:
                return False
            state.replans += 1

        plan = self.planner.plan(state, feedback=feedback)
        # Validation is not optional and not advisory. This is the stage ``Runner`` recorded
        # as "placeholder planner: no typed plan to validate"; it validates now, and a plan
        # that fails raises PlanRejected, which terminates the loop rather than executing a
        # plan known to be inadmissible.
        validated = self.validator.validate(plan, state.envelope,
                                            policy=getattr(self.kernel, "policy", None))
        state.plan = plan
        state.validated = validated
        state.graph = ExecutionGraph(plan)
        self._audit("loop_plan", state, plan_id=plan.plan_id, tasks=len(plan.tasks),
                    replan=state.replans)
        return True

    # ----------------------------------------------------------------- dispatch
    def _execute_ready(self, state: LoopState, ready: Sequence[Any]) -> None:
        """Run this iteration's ready set: in order, or on a bounded pool of threads.

        The ready set is independent by construction — every dependency of each task has
        already succeeded — so branches never read each other's results mid-flight; each
        takes its snapshot of ``labeled_results()`` when it starts. Results are collected
        in submission order, so a bound that one branch hit (budget, approval) surfaces
        deterministically, after the branches already in flight have recorded their own
        outcome on the graph.
        """
        workers = min(self.limits.max_parallel, len(ready))
        if workers <= 1:
            for node in ready:
                self._execute_task(state, node.task)
            return
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="psh-branch") as pool:
            futures = [pool.submit(self._execute_task, state, node.task) for node in ready]
            first_error: BaseException | None = None
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below, in order
                    first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def _execute_task(self, state: LoopState, task: PlanTask) -> None:
        """Run one task through the broker, with its own envelope and retry policy."""
        graph = state.graph
        envelope = state.envelope_for(task.task_id)
        attempt = graph.nodes[task.task_id].attempts + 1
        delay = task.retry.delay_for(attempt)
        if delay:
            self._sleep(delay)

        graph.mark_running(task.task_id, at=time.time())
        self._beat()
        identity = (f"{self.operation_namespace}:{task.task_id}"
                    if self.operation_namespace else task.task_id)
        key = f"{state.envelope.run_id}:{identity}"
        ledger = self.operations if task.kind == TaskKind.TOOL else None
        repeat_safe = True
        if task.kind == TaskKind.TOOL:
            try:
                manifest = getattr(self._component(task.component_id), "manifest", None)
            except ContractViolation:
                manifest = None  # Dispatch reports unavailable components normally.
            repeat_safe = getattr(manifest, "idempotent", None) is True
            with self._operation_lock:
                refused = (key in self._nonrepeatable_operations
                           or (attempt > 1 and not repeat_safe)
                           or (self.require_idempotent_tools and not repeat_safe))
                if not repeat_safe:
                    self._nonrepeatable_operations.add(key)
            if refused:
                graph.mark_failed(task.task_id, "OperationUnresolved: unsafe repeated tool operation",
                                  at=time.time(), retryable=False)
                self._audit("loop_task_unresolved", state, task_id=task.task_id,
                            component_id=task.component_id, operation=key)
                return
        if ledger is not None:
            try:
                self._guard_operation(state, task, key)
            except OperationUnresolved as exc:
                graph.mark_failed(task.task_id, f"{type(exc).__name__}: {exc}",
                                  at=time.time(), retryable=False)
                self._audit("loop_task_unresolved", state, task_id=task.task_id,
                            component_id=task.component_id, operation=key)
                return
        try:
            result = self._dispatch(task, envelope, graph.labeled_results(),
                                    idempotency_key=key, plan_label=state.plan_label)
        except (BudgetExhausted, ApprovalRequired):
            if ledger is not None:
                ledger.fail(key, "not_started")    # raised before the component ran
            raise                                  # bounds, not task failures
        except (EgressDenied, PolicyDenied) as exc:
            # A refusal is a fact about authority, not a transient fault. Retrying it would
            # re-ask a question already answered and burn budget doing it.
            if ledger is not None:
                ledger.fail(key, type(exc).__name__)
            graph.mark_failed(task.task_id, f"{type(exc).__name__}: {exc}",
                              at=time.time(), retryable=False)
            self._audit("loop_task_refused", state, task_id=task.task_id,
                        error_type=type(exc).__name__)
            return
        except Exception as exc:  # noqa: BLE001 - one task's fault is not the loop's end
            retryable = repeat_safe and (attempt < task.retry.max_attempts) and task.retry.permits(exc)
            if ledger is not None:
                if isinstance(exc, ToolTimeout) or not ledger.get(key).idempotent:
                    # A non-idempotent component may have changed the world before
                    # raising any exception, not just a timeout. Do not guess that it
                    # did nothing and silently repeat the side effect.
                    ledger.mark_unknown(key, type(exc).__name__)
                else:
                    ledger.fail(key, type(exc).__name__)
            self._record_outcome(task, ok=False)
            graph.mark_failed(task.task_id, f"{type(exc).__name__}: {exc}",
                              at=time.time(), retryable=retryable)
            self._audit("loop_task_failed", state, task_id=task.task_id,
                        error_type=type(exc).__name__, attempt=attempt,
                        retryable=retryable)
            return

        value, label, caveats = result
        if ledger is not None:
            ledger.succeed(key, value)
        self._record_outcome(task, ok=True)
        if label is None:
            # A result with no label is not PUBLIC; it is unclassified. Classify it, so no
            # node in the graph carries None into the joins built on it.
            label = classify_with(self.kernel, value, origin=f"task:{task.task_id}")
        graph.mark_succeeded(task.task_id, value, at=time.time(), label=label,
                             caveats=caveats)
        if caveats:
            self._audit("loop_task_degraded", state, task_id=task.task_id,
                        caveats=len(caveats))
        state.observe(task_id=task.task_id, kind=task.kind, attempt=attempt)

    def _guard_operation(self, state: LoopState, task: PlanTask, key: str) -> None:
        """Consult the operation ledger before a tool runs; refuse an unsafe replay.

        A component declaring ``idempotent=False`` is not re-run when the ledger says an
        earlier attempt of this very operation started and never reported, or succeeded
        without its result reaching this run (a redacted checkpoint withholds results, a
        replan rebuilds the graph). "Did it run?" has no answer here, and a retry policy
        is not the place to guess: the task fails and the loop escalates. Idempotent
        components run again, and the ledger records that they did.
        """
        ledger = self.operations
        try:
            manifest = getattr(self._component(task.component_id), "manifest", None)
        except ContractViolation:
            manifest = None                        # _dispatch raises it as a task failure
        idempotent = getattr(manifest, "idempotent", None) is True
        prior = ledger.get(key)
        if prior is not None and not idempotent and prior.state in (
                OperationState.RUNNING, OperationState.UNKNOWN, OperationState.SUCCEEDED):
            if prior.state is OperationState.RUNNING:
                ledger.mark_unknown(key, "lost")
            what = ("succeeded and its result did not reach this run"
                    if prior.state is OperationState.SUCCEEDED
                    else "started and never reported")
            raise OperationUnresolved(
                f"task {task.task_id!r}: an earlier attempt of side-effecting component "
                f"{task.component_id!r} {what} (operation {key}, attempt {prior.attempts}); "
                "re-running it is not safe, so the task fails until the operation is "
                "reconciled")
        ledger.begin(key, component_id=task.component_id, run_id=state.envelope.run_id,
                     task_id=task.task_id, idempotent=idempotent)

    def _record_outcome(self, task: PlanTask, *, ok: bool) -> None:
        """Feed the registry's success prior with what actually happened.

        ``CapabilityRegistry.record_outcome`` existed and nothing called it, so the
        observed-success term in ranking was the manifest's own claim forever.
        """
        if task.kind != TaskKind.TOOL or self.registry is None:
            return
        record = getattr(self.registry, "record_outcome", None)
        if record is not None:
            record(task.component_id, ok)

    def _dispatch(self, task: PlanTask, envelope: RunEnvelope,
                  upstream: Mapping[str, Any], *, idempotency_key: str = "",
                  plan_label: Any = None) -> tuple[Any, Any, tuple[str, ...]]:
        """The only three ways this loop can cause anything to happen.

        Returns ``(value, label)``. The label used to be discarded here — the broker hands
        back a labelled result and the loop unwrapped it to the bare value — which meant a
        child agent's summary could not carry the join of what it had seen, and a summary
        that does not carry its sources' labels is the laundering path compaction closes.

        ``upstream`` is ``graph.labeled_results()``: each dependency's value wrapped in the
        label the broker gave it. The label was recorded on the node and then not consulted
        when the next task's input was built, so every edge of the graph was a laundering
        step — the evidence item for a model task said PUBLIC whatever the tool had
        returned, and a tool payload was re-classified from its text alone, which sees an
        identifier but not a count derived from a PHI cohort. The label travels now: onto
        the evidence item, into the payload where ingress joins it, and into the result.

        ``idempotency_key`` is ``"<loop run id>:<task id>"``: the same value on every
        attempt of a task and, because ``resume()`` preserves the run id through the meet,
        the same value after a crash and restart. A tool with side effects can use it to
        recognise a replay — the case ``resume()`` creates deliberately when it turns a task
        that was RUNNING into RETRYABLE, since what that task did is not known.
        """
        broker = self.kernel.broker

        if task.kind == TaskKind.MODEL:
            if self.model is None or self.model_invoke is None:
                raise ContractViolation(
                    f"task {task.task_id!r} is a model call and this loop has no model "
                    "configured; a loop may not reach a provider by any other route")
            projection = self._projection(task, envelope, upstream, plan_label=plan_label)
            call = broker.call_model(projection, self.model, envelope,
                                     invoke=self.model_invoke)
            content = getattr(call, "content", call)
            # A model's output is at least as sensitive as the context it was shown, and
            # at least as sensitive as what it says. The broker's result carries the label
            # the gate ruled on; join it with a classification of the reply.
            shown = getattr(call, "label", None) or getattr(projection, "label", DataLabel())
            label = shown.merged_with(
                classify_with(self.kernel, content, origin=f"task:{task.task_id}"))
            return content, label, ()

        if task.kind == TaskKind.TOOL:
            component = self._component(task.component_id)
            payload = dict(task.payload)
            # Bound inputs: each is a Labeled value read from an upstream result by
            # pointer, so the argument the component actually takes carries that result's
            # label into ingress and the join. The ``upstream`` blob below stays for
            # components that read it; the bridge drops it, which is why bindings exist.
            payload.update(resolve_bindings(task, upstream))
            if task.dependencies:
                # Labeled values, not bare ones: ingress walks the payload and joins every
                # label it finds, so the derived label survives where a lexical scan of the
                # text would not see it.
                payload["upstream"] = {d: upstream.get(d) for d in task.dependencies}
            if idempotency_key:
                payload["_psh_idempotency_key"] = idempotency_key
            labeled_payload = (Labeled(payload, DataLabel(task.input_sensitivity))
                               if task.input_sensitivity else payload)
            result = broker.call_tool(component, labeled_payload, envelope)
            caveats = tuple(getattr(result, "warnings", ()) or ())
            return getattr(result, "value", result), getattr(result, "label", None), caveats

        if task.kind == TaskKind.DELEGATE:
            if self.delegate_backend is None:
                raise ContractViolation(
                    f"task {task.task_id!r} delegates and this loop has no delegate "
                    "backend; delegation must go somewhere the broker can gate")
            from ..contracts import ContextProjection, DelegationContract
            # What is handed over is the objective, and its label is what the delegation
            # gateway compares with the child's ceiling. A projection with no items and
            # this label says exactly that; the child joins it with its own classification
            # of the text at its ingress.
            handed = self._instruction_label(task, plan_label)
            contract = DelegationContract(
                task_id=task.task_id, objective=task.objective,
                envelope=envelope, output_schema=dict(task.output_schema),
                acceptance_tests=tuple(t.kind for t in task.acceptance_tests),
                evidence_required=task.evidence_required, backend="local_agent",
                projection=ContextProjection(items=(), label=handed, run_id=envelope.run_id))
            result = broker.delegate(contract, envelope, self.delegate_backend)
            label = getattr(result, "label", None)
            if label is None:
                label = classify_with(self.kernel, result, origin=f"task:{task.task_id}")
            return result, handed.merged_with(label), ()

        raise ContractViolation(f"unroutable task kind {task.kind!r}")

    def _instruction_label(self, task: PlanTask, plan_label: Any) -> DataLabel:
        """The label of a task's objective text: the plan's label joined with its own."""
        own = classify_with(self.kernel, task.objective, origin=f"task:{task.task_id}")
        if task.input_sensitivity:
            own = own.merged_with(DataLabel(task.input_sensitivity))
        return own if plan_label is None else own.merged_with(plan_label)

    def _projection(self, task: PlanTask, envelope: RunEnvelope,
                    upstream: Mapping[str, Any], *, plan_label: Any = None) -> Any:
        """Compile this task's own context. One projection per task, not a transcript.

        ``ContextProjection``'s docstring already says compiled context belongs to exactly
        one worker. A loop is where that stops being a style preference: accumulating every
        step's output into a shared history is how a tool result labelled PHI ends up in the
        prompt of a later step heading somewhere it may not go.

        Every item is labelled. The instruction carries the plan's label joined with its
        own classification; each evidence item carries the label its result was given by
        the broker. And an evidence item the compiler would drop for this destination is a
        refusal, not a quieter prompt: the upstream result *is* the task's input, and a task
        that ran without it would succeed at answering a different question.
        """
        from ..context import ContextCompiler
        from ..contracts import ContextItem

        destination = (self.model.destination if self.model is not None
                       else Destination.LOCAL_MODEL)
        items = [ContextItem(kind="instruction", content=task.objective,
                             label=self._instruction_label(task, plan_label))]
        for dependency in task.dependencies:
            item = upstream.get(dependency)
            value = unwrap(item)
            if value is not None:
                items.append(ContextItem(kind="evidence", content=str(value)[:4000],
                                         source_ref=dependency, label=label_of(item)))
        for argument, bound in resolve_bindings(task, upstream).items():
            # A bound input is the value the step asked for by name, labelled as its
            # source was. It sits beside the whole upstream result, not instead of it.
            items.append(ContextItem(kind="evidence",
                                     content=f"{argument}: {str(unwrap(bound))[:2000]}",
                                     source_ref=f"binding:{argument}", label=label_of(bound)))
        if self.memory is not None and self.memory_items > 0:
            # Withheld at retrieval for this destination and the run's ceiling, so the
            # compiler's policy count below still means exactly "an upstream result was
            # dropped" — memory the task may not see is a quieter prompt, not a refusal.
            items += self.memory.retrieve(task.objective, envelope=envelope,
                                          destination=destination, limit=self.memory_items)
        compiler = ContextCompiler()
        projection = compiler.compile(items=items, envelope=envelope,
                                      destination=destination,
                                      token_budget=envelope.budget.tokens_soft,
                                      query=task.objective)
        dropped = getattr(compiler.last_trace, "dropped_policy", 0)
        if dropped:
            withheld = combine(*[label_of(upstream.get(d)) for d in task.dependencies])
            raise EgressDenied(
                f"task {task.task_id!r} depends on results classified "
                f"{withheld.sensitivity.name} that may not reach {destination.name}; "
                "refusing rather than running the task without its input",
                label=withheld, destination=destination)
        return projection

    def _component(self, component_id: str) -> Any:
        if self.registry is None:
            raise ContractViolation(
                f"task names component {component_id!r} and this loop has no registry")
        component = self.registry.component(component_id)
        if component is None:
            raise ContractViolation(f"component {component_id!r} is not invocable")
        return component

    # ------------------------------------------------------------------- finish
    def _finish(self, state: LoopState, termination: Termination, reason: str,
                verdict: Verdict | None = None) -> LoopResult:
        state.termination = termination
        if state.graph is not None and termination is not Termination.GOAL_SATISFIED:
            state.graph.cancel_all(f"loop terminated: {termination.value}")
        result = LoopResult(
            loop_id=state.loop_id, run_id=state.envelope.run_id, termination=termination,
            reason=reason, iterations=state.iteration, replans=state.replans,
            results=state.graph.results() if state.graph else {},
            failures=verdict.failures if verdict else (),
            unmet_criteria=verdict.unmet_criteria if verdict else (),
            plan=state.plan,
            state_counts=state.graph.state_counts() if state.graph else {},
            broker_stats=self.kernel.broker.stats(),
            label=_result_label(state),
            goal_status=getattr(verdict, "goal_status", "unverified") if verdict else "unverified",
            caveats=({n.id: n.caveats for n in state.graph.nodes.values() if n.caveats}
                     if state.graph else {}))
        self._audit("loop_finished", state, termination=termination.value,
                    iterations=state.iteration, replans=state.replans)
        return result

    def _beat(self) -> None:
        if self.heartbeat is None:
            return
        try:
            self.heartbeat()
        except Exception:  # noqa: BLE001 - a failed heartbeat must not end the work
            pass

    def _checkpoint(self, state: LoopState) -> None:
        """Snapshot after each iteration, if a store is configured.

        Best-effort by design: a checkpoint that cannot be written must not end a run that
        is otherwise fine. The failure is recorded, so "we have no checkpoints" is visible
        rather than inferred from their absence.
        """
        if self.checkpoints is None or state.plan is None:
            return
        try:
            from .checkpoint import capture

            # A checkpoint is a durable write and obeys the persistence rules like any
            # other: results above the store's ceiling, or that may not reach PERSISTENT,
            # or of a run that may not persist at all, are withheld and the task re-runs
            # on resume. The rest of the record is still written.
            persistence = getattr(self.kernel, "persistence", None)
            ceiling = getattr(persistence, "max_label", None)
            checkpoint = capture(
                state, policy=getattr(self.kernel, "policy", None), ceiling=ceiling,
                allow_results=state.envelope.permits_destination(Destination.PERSISTENT),
                kernel=self.kernel)
            self.checkpoints.save(checkpoint)
            if checkpoint.withheld:
                self._audit("loop_checkpoint_withheld", state,
                            tasks=sorted(checkpoint.withheld))
        except Exception as exc:  # noqa: BLE001 - a failed snapshot is not a failed run
            self._audit("loop_checkpoint_failed", state, error_type=type(exc).__name__)

    def _audit(self, event: str, state: LoopState, **detail: Any) -> None:
        audit = getattr(self.kernel, "audit", None)
        if audit is None:
            return
        try:
            audit(event, run_id=state.envelope.run_id, detail={"loop_id": state.loop_id,
                                                               "iteration": state.iteration,
                                                               **detail})
        except Exception:  # noqa: BLE001
            # A child given up on by its parent may wake after the kernel has closed. Its
            # last audit write then fails, and that failure must end quietly rather than
            # propagate out of a thread nobody is waiting for. The store refuses cleanly
            # now; this is the other half of the same fix.
            pass


def _join_labels(graph: ExecutionGraph) -> Any:
    """The join of every labelled task result in a graph, or None if none were labelled."""
    labels = [n.label for n in graph.nodes.values() if getattr(n, "label", None) is not None]
    return combine(*labels) if labels else None


def _result_label(state: LoopState) -> Any:
    """What a loop hands back is labelled at least as high as anything it saw.

    That is the task results' join AND the objective's label: a child asked about a PHI
    chart answers about that chart, and its answer is derived from the question whatever
    its tasks returned. ``None`` only when nothing was labelled at all.
    """
    labels = [label for label in (
        _join_labels(state.graph) if state.graph else None, state.objective_label)
        if label is not None]
    return combine(*labels) if labels else None
