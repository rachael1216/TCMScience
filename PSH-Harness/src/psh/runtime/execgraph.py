"""The executable DAG for one loop, kept separate from the WorkGraph.

The review's point, and it is the right one: ``WorkGraph`` is a persistent scientific
knowledge graph — Project, Task, Run, Claim, Evidence, Artifact, Decision — and an
execution DAG is a transient scheduling structure whose nodes go RUNNING and FAILED and get
retried. Putting the second inside the first would mean a retry rewrites provenance, and
provenance that changes when a job is rerun is not provenance.

So they are different objects with different lifetimes. The loop writes *outcomes* to the
WorkGraph through the persistence gateway, as ``Runner`` already does; the states below
never leave memory except as audit events.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence, Iterable, Mapping

from .plan import Plan, PlanTask

__all__ = ["ExecutionGraph", "TaskState", "TaskNode"]


class TaskState(str, Enum):
    """A task's lifecycle. Explicit, because "not done" hides four different situations."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"              # terminal: retries are exhausted or the error is not retryable
    RETRYABLE = "retryable"        # failed, but another attempt is permitted
    BLOCKED = "blocked"            # an upstream task failed terminally
    CANCELLED = "cancelled"


_TERMINAL = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.BLOCKED,
                       TaskState.CANCELLED})


@dataclass
class TaskNode:
    """One task's runtime state. Mutable by design; the plan it came from is not."""

    task: PlanTask
    state: TaskState = TaskState.PENDING
    attempts: int = 0
    result: Any = None
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    #: The label the broker attached to this task's result. Kept so anything built from
    #: the results — a child's summary, a fan-in — carries the join rather than starting
    #: from PUBLIC.
    label: Any = None
    #: What the component said it fell short on. A degraded result is still a result;
    #: the caveat travels to the loop's result and to the release as a limitation.
    caveats: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.task.task_id

    @property
    def done(self) -> bool:
        return self.state in _TERMINAL


class ExecutionGraph:
    """Dependency-ordered task states for one plan."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.nodes: dict[str, TaskNode] = {t.task_id: TaskNode(task=t) for t in plan.tasks}
        # Independent branches may run on threads. Every transition and every read that
        # spans nodes takes this lock, so a failure blocking its descendants cannot
        # interleave with a sibling recording its success.
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- queries
    def ready(self) -> list[TaskNode]:
        """Tasks whose dependencies have all succeeded and which are not yet finished.

        A retryable task is ready again — that is what distinguishes RETRYABLE from FAILED,
        and why "not done" was not enough to express.
        """
        with self._lock:
            out: list[TaskNode] = []
            for node in self.nodes.values():
                if node.state in (TaskState.RUNNING, *_TERMINAL):
                    continue
                if all(self.nodes[d].state is TaskState.SUCCEEDED
                       for d in node.task.dependencies if d in self.nodes):
                    out.append(node)
            return out

    @property
    def complete(self) -> bool:
        return all(n.done for n in self.nodes.values())

    @property
    def succeeded(self) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.state is TaskState.SUCCEEDED]

    @property
    def failed(self) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.state is TaskState.FAILED]

    def results(self) -> dict[str, Any]:
        return {n.id: n.result for n in self.succeeded}

    def labeled_results(self) -> dict[str, Any]:
        """Results wrapped with the label the broker gave them.

        ``results()`` is the bare values, which is what a caller reporting on a loop wants.
        Anything *built on* a result — the next task's prompt, the next tool's payload —
        must start from the label, or the join is lost at every edge of the graph and a
        tool result labelled PHI reaches the next model call as PUBLIC evidence.
        """
        from ..labels import Labeled

        with self._lock:
            return {n.id: (Labeled(value=n.result, label=n.label) if n.label is not None
                           else n.result)
                    for n in self.succeeded}

    # ---------------------------------------------------------------- mutation
    def mark_running(self, task_id: str, *, at: float) -> None:
        with self._lock:
            node = self.nodes[task_id]
            node.state = TaskState.RUNNING
            node.attempts += 1
            node.started_at = at

    def mark_succeeded(self, task_id: str, result: Any, *, at: float,
                       label: Any = None, caveats: Sequence[str] = ()) -> None:
        """Record a result and, with it, the label the broker gave it.

        A node whose result has no label is one nothing can safely be built on: it is not
        checkpointed (``capture`` withholds it) and the loop classifies before it gets
        here. The label is a parameter so the two are set together.
        """
        with self._lock:
            node = self.nodes[task_id]
            node.state = TaskState.SUCCEEDED
            node.result = result
            node.error = ""
            node.finished_at = at
            node.caveats = tuple(caveats)
            if label is not None:
                node.label = label

    def mark_failed(self, task_id: str, error: str, *, at: float,
                    retryable: bool = False) -> None:
        with self._lock:
            node = self.nodes[task_id]
            node.state = TaskState.RETRYABLE if retryable else TaskState.FAILED
            node.error = error
            node.finished_at = at
            if not retryable:
                self.block_descendants(task_id)

    def block_descendants(self, task_id: str) -> list[str]:
        """Mark everything downstream of a terminal failure BLOCKED.

        Without this a dependent task sits PENDING forever and the loop terminates on
        iteration exhaustion, reporting "ran out of iterations" for what is actually "its
        dependency failed". The termination reason a loop gives is the only explanation
        anyone gets, so it has to name the real cause.
        """
        with self._lock:
            blocked: list[str] = []
            changed = True
            while changed:
                changed = False
                for node in self.nodes.values():
                    if node.done or node.state is TaskState.RUNNING:
                        continue
                    upstream = [self.nodes[d] for d in node.task.dependencies
                                if d in self.nodes]
                    if any(u.state in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED)
                           for u in upstream):
                        node.state = TaskState.BLOCKED
                        node.error = node.error or f"an upstream task failed ({task_id})"
                        blocked.append(node.id)
                        changed = True
            return blocked

    def cancel_all(self, reason: str) -> int:
        with self._lock:
            cancelled = 0
            for node in self.nodes.values():
                if not node.done:
                    node.state = TaskState.CANCELLED
                    node.error = reason
                    cancelled += 1
            return cancelled

    # ------------------------------------------------------------------ report
    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.state.value] = counts.get(node.state.value, 0) + 1
        return counts

    def progress_digest(self) -> str:
        """A digest of what has actually been achieved, for stuck detection.

        Hashing the *request* — which is what ``Runner._preflight`` did — answers "have I
        been asked this before", which is a different question from "am I getting
        anywhere". A loop that re-asks the same question after making progress is working;
        a loop that produces the identical state twice is not.
        """
        from ..contracts import content_hash
        return content_hash({
            "states": {n.id: n.state.value for n in self.nodes.values()},
            "attempts": {n.id: n.attempts for n in self.nodes.values()},
            "results": {n.id: content_hash(n.result) for n in self.succeeded},
        })[:32]

    def summary(self) -> str:
        return f"{len(self.nodes)} task(s): {self.state_counts()}"
