"""A subagent is a child loop that returns a summary, not its transcript.

The delegation primitive existed — ``DelegationContract``, ``DelegationGateway``,
``Runner.delegate`` — and the backend it handed the contract to was a bare callable. This
is the backend: a **child** ``AgentLoopController`` running under the contract's envelope,
with its own plan, its own projections and its own termination, and reporting back a
``SubagentResult``.

Two design decisions are borrowed and one is this package's own.

**A child has its own context and returns a summary** (Grok Build's subagent model). The
parent never receives the child's prompts, tool payloads or intermediate results. It gets
claims, evidence references, artifact references and a short summary. That is the same
argument ``ContextProjection`` already makes — compiled context belongs to one worker —
applied one level up: a parent that accumulated every child's raw output would be the
shared transcript the runtime was built to avoid, and a PHI tool result inside one child
would land in the parent's next prompt.

**Cancellation is cooperative and its policy is explicit** (Temporal's distinction between
cancelling the task that started a child and cancelling the child). ``WAIT`` lets children
finish, ``CASCADE`` tells them to stop, ``DETACH`` disowns them. A research run is the case
where the distinction matters: a parent being cancelled should not always mean killing an
analysis that has been running for five hours.

**The result's label is the join of everything the child saw.** This is not borrowed; it
is the rule compaction and the loop already follow, and it is what stops delegation from
being a laundering path. A child that read PHI hands back a PHI-labelled summary however
innocuous the summary text is.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..contracts import DelegationContract, new_id
from ..labels import DataLabel, Sensitivity, combine
from .loop import AgentLoopController, LoopLimits, LoopResult, Planner, Termination

__all__ = ["CancellationPolicy", "CancellationToken", "ChildState", "ChildRun",
           "SubagentResult", "LocalSubagentBackend", "WorkerLease", "LeaseRegistry"]


class CancellationPolicy(str, Enum):
    """What a parent's cancellation means for its children. Stated, never assumed."""

    WAIT = "wait"          # let running children finish; start no new ones
    CASCADE = "cascade"    # tell running children to stop at their next check
    DETACH = "detach"      # stop tracking them; they run to their own conclusion


class ChildState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"          # detached: still running, no longer ours
    #: Its lease expired: no heartbeat for longer than the lease allows. The thread may
    #: still exist — a tool that never returns cannot be interrupted from outside — but the
    #: parent stops waiting for it and its token is cancelled so it stops if it ever wakes.
    STALLED = "stalled"


_DONE = frozenset({ChildState.COMPLETED, ChildState.FAILED, ChildState.CANCELLED,
                   ChildState.ORPHANED, ChildState.STALLED})


# ------------------------------------------------------------------ leases

@dataclass
class WorkerLease:
    """Proof of life. A child holds one and renews it by heartbeating; silence expires it.

    The problem this solves was measured before it was built: a child whose tool never
    returned left the parent's ``wait()`` blocked forever, and nothing anywhere recorded
    that the child was stuck. Cooperative cancellation cannot help — the child is inside a
    call it will never come back from — so the parent needs a signal that does not depend
    on the child's cooperation. Absence of a heartbeat is that signal.
    """

    child_id: str
    ttl_s: float
    issued_at: float = field(default_factory=time.monotonic)
    last_beat: float = field(default_factory=time.monotonic)
    beats: int = 0

    def beat(self, now: float | None = None) -> None:
        self.last_beat = time.monotonic() if now is None else now
        self.beats += 1

    def expired(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return (now - self.last_beat) > self.ttl_s

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.issued_at


class LeaseRegistry:
    """Thread-safe table of live leases. The supervisor's view of who is still alive."""

    def __init__(self) -> None:
        self._leases: dict[str, WorkerLease] = {}
        self._lock = threading.Lock()

    def issue(self, child_id: str, ttl_s: float) -> WorkerLease:
        if ttl_s <= 0:
            raise ValueError("a lease needs a positive ttl")
        lease = WorkerLease(child_id=child_id, ttl_s=ttl_s)
        with self._lock:
            self._leases[child_id] = lease
        return lease

    def beat(self, child_id: str) -> None:
        with self._lock:
            lease = self._leases.get(child_id)
        if lease is not None:
            lease.beat()

    def expired(self, now: float | None = None) -> list[WorkerLease]:
        with self._lock:
            leases = list(self._leases.values())
        return [lease for lease in leases if lease.expired(now)]

    def release(self, child_id: str) -> None:
        with self._lock:
            self._leases.pop(child_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._leases)


class CancellationToken:
    """A flag a loop polls between iterations. Linkable, so a cascade is one call.

    Cooperative by design. Stopping a thread from outside is not something Python can do
    safely, and it would be the wrong thing anyway: a tool call that has started should
    finish or time out on its own terms rather than be abandoned half-way through a write.
    """

    def __init__(self, parent: "CancellationToken | None" = None) -> None:
        self._event = threading.Event()
        self.reason = ""
        self._children: list[CancellationToken] = []
        self._lock = threading.Lock()
        if parent is not None:
            parent._link(self)

    def _link(self, child: "CancellationToken") -> None:
        with self._lock:
            self._children.append(child)
            if self._event.is_set():
                child.cancel(self.reason)

    def cancel(self, reason: str = "") -> None:
        with self._lock:
            if not self._event.is_set():
                self.reason = reason or "cancelled"
                self._event.set()
            children = list(self._children)
        for child in children:
            child.cancel(self.reason)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def child(self) -> "CancellationToken":
        """A token that is cancelled whenever this one is."""
        return CancellationToken(parent=self)


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """What a child hands back. Deliberately has no field for a transcript."""

    child_run_id: str
    summary: str
    termination: str
    claims: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    label: DataLabel = field(default_factory=DataLabel)
    iterations: int = 0
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.termination == Termination.GOAL_SATISFIED.value


@dataclass
class ChildRun:
    """One delegated child, its lifecycle, and how a parent's cancellation reaches it."""

    contract: DelegationContract
    policy: CancellationPolicy = CancellationPolicy.CASCADE
    token: CancellationToken = field(default_factory=CancellationToken)
    state: ChildState = ChildState.CREATED
    result: SubagentResult | None = None
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    lease: WorkerLease | None = None
    id: str = field(default_factory=lambda: new_id("child"))

    @property
    def done(self) -> bool:
        return self.state in _DONE

    def request_cancel(self, reason: str = "") -> None:
        """Apply this child's policy. Only CASCADE actually signals the child."""
        if self.done:
            return
        if self.policy is CancellationPolicy.CASCADE:
            self.state = ChildState.CANCEL_REQUESTED
            self.token.cancel(reason or "parent cancelled")
        elif self.policy is CancellationPolicy.DETACH:
            self.state = ChildState.ORPHANED
        # WAIT: nothing to signal; the parent simply waits.


# ------------------------------------------------------------- summarising

def _collect(results: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    out: list[str] = []
    for value in results.values():
        if isinstance(value, Mapping):
            for key in keys:
                found = value.get(key)
                if isinstance(found, (list, tuple)):
                    out.extend(str(f) for f in found)
                elif isinstance(found, str) and found.strip():
                    out.append(found)
    return tuple(dict.fromkeys(out))


def summarise_loop(result: LoopResult, *, max_chars: int = 1200) -> SubagentResult:
    """Reduce a child's ``LoopResult`` to what its parent is allowed to see.

    Extractive, like compaction: each task contributes one line of its result. The
    parent gets the shape of what happened and typed handles (claims, evidence, artifacts)
    it can reason over — not the material the child worked from.
    """
    lines: list[str] = [f"{result.termination.value}: {result.reason}"]
    used = len(lines[0])
    for task_id, value in result.results.items():
        text = " ".join(str(value).split())
        line = f"- {task_id}: {text[:200]}"
        if used + len(line) > max_chars:
            lines.append(f"- ... {len(result.results) - (len(lines) - 1)} more task(s)")
            break
        lines.append(line)
        used += len(line)
    return SubagentResult(
        child_run_id=result.run_id, summary="\n".join(lines),
        termination=result.termination.value,
        claims=_collect(result.results, "claims", "claim"),
        evidence=_collect(result.results, "evidence", "citations"),
        artifacts=_collect(result.results, "artifacts", "artifact"),
        label=result.label if result.label is not None else DataLabel(),
        iterations=result.iterations, failures=tuple(result.failures))


# ---------------------------------------------------------------- backend

class LocalSubagentBackend:
    """Runs a child loop for a contract. The callable ``ExecutionBroker.delegate`` expects.

    It holds the kernel and the means to build a planner, and nothing else: the child's
    authority comes from ``contract.envelope``, which the supervisor minted through
    ``restrict`` and the delegation gateway has already ruled on by the time this runs.
    """

    def __init__(self, kernel: Any, *,
                 planner_factory: Callable[[DelegationContract], Planner],
                 registry: Any = None, model: Any = None,
                 model_invoke: Callable[[str], str] | None = None,
                 limits: LoopLimits | None = None, evaluator: Any = None,
                 sleep: Callable[[float], None] = time.sleep,
                 operations: Any = None) -> None:
        self.kernel = kernel
        self.planner_factory = planner_factory
        #: The operation ledger, shared with every child: a side effect a child performs
        #: is recorded where a restart of the whole tree can find it.
        self.operations = operations
        self.registry = registry
        self.model = model
        self.model_invoke = model_invoke
        self.limits = limits or LoopLimits(max_iterations=6, max_replans=1)
        self.evaluator = evaluator
        self._sleep = sleep
        self.children_run = 0

    def __call__(self, contract: DelegationContract,
                 token: CancellationToken | None = None,
                 heartbeat: Callable[[], None] | None = None) -> SubagentResult:
        self.children_run += 1
        loop = AgentLoopController(
            self.kernel, planner=self.planner_factory(contract), registry=self.registry,
            model=self.model, model_invoke=self.model_invoke, limits=self.limits,
            evaluator=self.evaluator, cancellation=token, heartbeat=heartbeat,
            sleep=self._sleep, operations=self.operations,
            # A child may delegate further only through the same door. Passing ourselves
            # keeps the tree governed at every level; the budget's child() fractions and
            # max_delegations bound its depth.
            delegate_backend=self)
        # The contract's projection label is what the parent knows about the objective
        # that its text does not show; the child joins it with its own classification.
        handed = contract.projection.label if contract.projection is not None else None
        result = loop.run(contract.objective, contract.envelope, objective_label=handed)
        return summarise_loop(result)
