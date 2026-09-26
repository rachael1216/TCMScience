"""Supervisor, worker pool and reducer: fan-out that cannot widen and fan-in that records.

The multi-agent layer, built under the rule the roadmap states and every framework
surveyed gets wrong in one direction or another:

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

    Supervisor --propose--> TrustedKernel --authorize--> Worker

The CrewAI / AutoGen shape has a manager that grants its workers tools and budget. Here the
supervisor holds no way to construct a ``RunEnvelope`` at all: it turns a
``DelegationRequest`` into keyword arguments for ``parent.restrict()``, which is the
authority lattice, and a request for more than the parent holds raises ``PolicyDenied``
from inside that call. The supervisor is not trusted; it is *contained*, which is a
stronger property and a cheaper one.

Three further things this module is for:

**Fan-out cannot multiply the budget.** ``Budget.child(0.25)`` bounds one child, and its
docstring says that "stops a delegation tree from multiplying a budget by fanning out". It
does not: ten siblings at a quarter each are two and a half parents, and nothing summed
them. ``BudgetLedger`` does, and it is cumulative over the parent's life because a
finished child *spent* its slice — releasing the fraction would let a parent spawn
children forever, one at a time.

**Fan-in is typed, and disagreement is a recorded state.** For research work,
``"\\n".join(worker_outputs)`` is not a reducer. ``AggregatedObservation`` carries facts
with who asserted them, evidence references, *conflicts*, and what is unresolved, so two
children disagreeing is a thing the parent can act on rather than two paragraphs of prose
concatenated. Conflicts are detected with the existing ``ClaimSupportVerifier`` — lexical
polarity and overlap — not with a new model of what contradiction means.

**The result's label is the join.** The same rule as compaction and the subagent: an
aggregate of what several children saw is labelled at least as high as any of them.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contracts import (
    Autonomy, BudgetExhausted, DelegationContract, PolicyDenied, RiskTier, RunEnvelope,
)
from ..labels import DataLabel, Destination, Sensitivity, combine
from .subagent import (
    CancellationPolicy, CancellationToken, ChildRun, ChildState, LeaseRegistry,
    SubagentResult,
)

__all__ = ["Supervisor", "DelegationRequest", "BudgetLedger", "WorkerPool", "Reducer",
           "AggregatedObservation", "Fact", "Conflict"]


# ------------------------------------------------------------------ the ledger

class BudgetLedger:
    """Σ(child budget fractions) ≤ 1 per parent run, over the parent's whole life."""

    def __init__(self) -> None:
        self._allocated: dict[str, float] = {}
        self._lock = threading.Lock()

    def reserve(self, parent_run_id: str, fraction: float) -> None:
        if not 0.0 < fraction <= 1.0:
            raise ValueError("a child's budget fraction must be in (0, 1]")
        with self._lock:
            held = self._allocated.get(parent_run_id, 0.0)
            if held + fraction > 1.0 + 1e-9:
                raise BudgetExhausted(
                    f"run {parent_run_id} has already delegated {held:.0%} of its budget; "
                    f"a further {fraction:.0%} would make its children worth more than the "
                    "parent, which is what a per-child fraction alone did not prevent")
            self._allocated[parent_run_id] = held + fraction

    def allocated(self, parent_run_id: str) -> float:
        with self._lock:
            return self._allocated.get(parent_run_id, 0.0)


# ----------------------------------------------------------------- the request

@dataclass(frozen=True, slots=True)
class DelegationRequest:
    """What a supervisor may say about a child. Every field is a *narrowing* of the parent.

    There is no field for a capability, destination, label or risk the parent lacks,
    because there is no way to express one: each value becomes an argument to
    ``restrict()``, and ``restrict`` refuses anything wider than the parent.
    """

    objective: str
    capabilities: tuple[str, ...] = ()
    max_label: Sensitivity | None = None
    max_risk: RiskTier | None = None
    autonomy: Autonomy | None = None
    destinations: tuple[Destination, ...] | None = None
    budget_fraction: float = 0.25
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    evidence_required: bool = False
    cancellation: CancellationPolicy = CancellationPolicy.CASCADE

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("a delegation request needs an objective")


# ----------------------------------------------------------------- the pool

class WorkerPool:
    """Bounded concurrency over a kernel that is now safe to share between threads.

    Threads rather than processes, because the whole point is that every worker goes
    through the *same* ``ExecutionBroker`` — the counters, the budget governor and the
    event chain are the proof that nothing bypassed it, and a process per worker would
    give each its own copy. Bounded, because concurrency is a resource the parent's policy
    should own; unbounded fan-out is the budget-multiplication defect in another form.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("a pool needs at least one worker")
        self.max_concurrency = max_concurrency
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[], Any]) -> Future:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.max_concurrency, thread_name_prefix="psh-worker")
            return self._executor.submit(fn)

    def shutdown(self, *, wait: bool) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=wait)


# ------------------------------------------------------------------ fan-in

@dataclass(frozen=True, slots=True)
class Fact:
    statement: str
    asserted_by: tuple[str, ...]
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Conflict:
    left: Fact
    right: Fact
    rationale: str


@dataclass(frozen=True, slots=True)
class AggregatedObservation:
    """What a set of children found, with disagreement as a first-class field."""

    facts: tuple[Fact, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    #: Children whose lease expired. Their work is absent from this aggregate, and saying
    #: so is the difference between "three children found two facts" and "three children
    #: found two facts, and one of them never answered".
    stalled: tuple[str, ...] = ()
    label: DataLabel = field(default_factory=DataLabel)
    children: int = 0

    def summary(self) -> str:
        return (f"{self.children} child(ren): {len(self.facts)} fact(s), "
                f"{len(self.conflicts)} conflict(s), {len(self.evidence)} evidence ref(s), "
                f"{len(self.unresolved)} unresolved, {len(self.failed)} failed, "
                f"{len(self.cancelled)} cancelled, {len(self.stalled)} stalled; "
                f"label={self.label.sensitivity.name}")


_WS = re.compile(r"\s+")


def _normalise(statement: str) -> str:
    return _WS.sub(" ", statement.strip().lower().rstrip("."))


class Reducer:
    """Fold children's results into one ``AggregatedObservation``."""

    def __init__(self, verifier: Any = None) -> None:
        if verifier is None:
            from ..evidence.support import ClaimSupportVerifier
            verifier = ClaimSupportVerifier()
        self.verifier = verifier

    def reduce(self, children: Sequence[ChildRun]) -> AggregatedObservation:
        from ..evidence.support import Relationship

        completed = [c for c in children if c.state is ChildState.COMPLETED and c.result]
        failed = tuple(c.id for c in children if c.state is ChildState.FAILED)
        cancelled = tuple(c.id for c in children
                          if c.state in (ChildState.CANCELLED, ChildState.ORPHANED))
        stalled = tuple(c.id for c in children if c.state is ChildState.STALLED)

        # Facts, deduplicated on a normalised statement, remembering who said each.
        by_key: dict[str, dict[str, Any]] = {}
        evidence_all: list[str] = []
        for child in completed:
            result = child.result
            evidence_all.extend(result.evidence)
            for claim in result.claims:
                key = _normalise(claim)
                if not key:
                    continue
                entry = by_key.setdefault(key, {"statement": claim.strip(),
                                                "asserted_by": [], "evidence": []})
                entry["asserted_by"].append(child.id)
                entry["evidence"].extend(result.evidence)
        facts = tuple(Fact(statement=e["statement"],
                           asserted_by=tuple(dict.fromkeys(e["asserted_by"])),
                           evidence=tuple(dict.fromkeys(e["evidence"])))
                      for e in by_key.values())

        # Conflicts: two facts, from different children, that the verifier says
        # contradict. Pairwise is quadratic and n is the number of distinct claims a
        # handful of children returned, which is small; it is also the honest scope of a
        # lexical check, so it is not something to optimise before it is something to
        # replace.
        conflicts: list[Conflict] = []
        for i, left in enumerate(facts):
            for right in facts[i + 1:]:
                if set(left.asserted_by) == set(right.asserted_by):
                    continue
                verdict = self.verifier.verify(
                    statement=left.statement, identifier=right.asserted_by[0],
                    source_text=right.statement)
                if verdict.relationship is Relationship.CONTRADICT:
                    conflicts.append(Conflict(left, right, verdict.rationale))

        unresolved = tuple(f.statement for f in facts if not f.evidence)
        labels = [c.result.label for c in completed]
        return AggregatedObservation(
            facts=facts, conflicts=tuple(conflicts),
            evidence=tuple(dict.fromkeys(evidence_all)), unresolved=unresolved,
            failed=failed, cancelled=cancelled, stalled=stalled,
            label=combine(*labels) if labels else DataLabel(), children=len(children))


# ---------------------------------------------------------------- supervisor

class Supervisor:
    """Proposes delegations; the kernel authorises them; a pool runs them."""

    def __init__(self, kernel: Any, backend: Callable[..., SubagentResult], *,
                 max_concurrency: int = 4, ledger: BudgetLedger | None = None,
                 reducer: Reducer | None = None, lease_ttl_s: float = 60.0) -> None:
        self.kernel = kernel
        self.backend = backend
        self.pool = WorkerPool(max_concurrency)
        self.ledger = ledger or BudgetLedger()
        self.reducer = reducer or Reducer()
        #: How long a child may go without a heartbeat before the parent stops waiting for
        #: it. A child beats at every bounds check and before every task, so this is a
        #: bound on one tool call, not on the child's whole run. Measured before it was
        #: built: without it, a tool that never returned left ``wait()`` blocked forever
        #: and nothing recorded that the child was stuck.
        if lease_ttl_s <= 0:
            raise ValueError("lease_ttl_s must be positive")
        self.lease_ttl_s = lease_ttl_s
        self.leases = LeaseRegistry()
        self.dispatched = 0

    # ------------------------------------------------------------------ mint
    def mint(self, request: DelegationRequest, parent: RunEnvelope) -> DelegationContract:
        """Turn a request into a contract. The envelope comes from ``restrict``, not us.

        Every field of the request is passed as a narrowing argument. This method contains
        no comparison against the parent because it must not: the comparison is the
        lattice's, and a second one here would be the DelegationGateway defect over again.
        """
        narrowing: dict[str, Any] = {
            "budget": parent.budget.child(request.budget_fraction)}
        if request.capabilities:
            narrowing["allowed_capabilities"] = tuple(request.capabilities)
        if request.max_label is not None:
            narrowing["max_label"] = DataLabel(request.max_label)
        if request.max_risk is not None:
            narrowing["risk"] = request.max_risk
        if request.autonomy is not None:
            narrowing["autonomy"] = request.autonomy
        if request.destinations is not None:
            narrowing["allowed_destinations"] = frozenset(request.destinations)
        child = parent.restrict(**narrowing)          # raises PolicyDenied if wider
        return DelegationContract(
            task_id=parent.task_id, objective=request.objective, envelope=child,
            output_schema=dict(request.output_schema),
            evidence_required=request.evidence_required, backend="local_agent")

    # -------------------------------------------------------------- dispatch
    def dispatch(self, requests: Iterable[DelegationRequest], parent: RunEnvelope, *,
                 wait: bool = True) -> list[ChildRun]:
        """Reserve budget, mint, and run every request through the broker.

        Reservation happens for the whole batch before anything runs, so a batch that
        would over-allocate is refused as a batch rather than half-started.
        """
        requests = list(requests)
        for request in requests:
            self.ledger.reserve(parent.run_id, request.budget_fraction)

        children: list[ChildRun] = []
        for request in requests:
            contract = self.mint(request, parent)
            child = ChildRun(contract=contract, policy=request.cancellation,
                             state=ChildState.QUEUED)
            child.lease = self.leases.issue(child.id, self.lease_ttl_s)
            children.append(child)
        for child in children:
            child._future = self.pool.submit(lambda c=child: self._run(c, parent))  # type: ignore[attr-defined]
            self.dispatched += 1
        if wait:
            self.wait(children)
        return children

    def _run(self, child: ChildRun, parent: RunEnvelope) -> None:
        if child.token.cancelled:                       # cancelled while queued
            child.state = ChildState.CANCELLED
            child.error = child.token.reason
            return
        child.state = ChildState.RUNNING
        child.started_at = time.time()
        self.leases.beat(child.id)
        try:
            # THE door. The gateway rules on the contract, the governor counts the
            # delegation, the event is recorded — from a worker thread, on a kernel that
            # is now locked for exactly this.
            result = self.kernel.broker.delegate(
                child.contract, parent,
                lambda contract: self.backend(contract, token=child.token,
                                              heartbeat=lambda: self.leases.beat(child.id)))
            child.result = result
            if child.state in (ChildState.ORPHANED, ChildState.STALLED):
                pass                       # disowned or given up on: result kept, state not
            elif result.termination == "cancelled":
                child.state = ChildState.CANCELLED
            else:
                child.state = ChildState.COMPLETED
        except Exception as exc:  # noqa: BLE001 - one child's fault is not the parent's end
            child.error = f"{type(exc).__name__}: {exc}"
            if child.state not in (ChildState.ORPHANED, ChildState.STALLED):
                child.state = ChildState.FAILED
        finally:
            child.finished_at = time.time()
            self.leases.release(child.id)

    def reap(self, children: Sequence[ChildRun]) -> list[ChildRun]:
        """Give up on children whose lease has expired. Returns the ones newly stalled.

        Nothing here stops the child's thread — a tool that never returns cannot be
        interrupted from outside, and pretending otherwise is how "killed" came to be
        written about a process group nobody signalled. What it does: records the fact,
        cancels the child's token so it stops if it ever wakes, and lets the parent stop
        waiting. The thread is leaked knowingly rather than waited for forever.
        """
        stalled: list[ChildRun] = []
        for child in children:
            if child.state is not ChildState.RUNNING or child.lease is None:
                continue
            if not child.lease.expired():
                continue
            child.state = ChildState.STALLED
            child.error = (f"no heartbeat for {child.lease.ttl_s:g}s; the parent has "
                           "stopped waiting for this child")
            child.token.cancel("lease expired")
            self.leases.release(child.id)
            stalled.append(child)
            audit = getattr(self.kernel, "audit", None)
            if audit is not None:
                audit("child_stalled", run_id=child.contract.envelope.run_id,
                      detail={"child_id": child.id, "ttl_s": child.lease.ttl_s,
                              "beats": child.lease.beats})
        return stalled

    def wait(self, children: Sequence[ChildRun], timeout: float | None = None,
             poll_s: float = 0.05) -> None:
        """Wait for every child that is ours, reaping the ones that stop answering.

        Polls rather than joining each future outright: a join with no timeout is exactly
        how a hung child held the parent forever. Each pass reaps expired leases, and the
        wait ends when every child is finished, stalled or orphaned — or the caller's own
        timeout passes.
        """
        deadline = None if timeout is None else time.time() + timeout
        while True:
            self.reap(children)
            pending = [c for c in children
                       if not c.done and getattr(c, "_future", None) is not None]
            if not pending:
                return
            if deadline is not None and time.time() >= deadline:
                return
            step = poll_s if deadline is None else min(poll_s, max(0.0, deadline - time.time()))
            for child in pending:
                try:
                    child._future.result(timeout=step)  # type: ignore[attr-defined]
                    break                              # one finished; re-evaluate the rest
                except Exception:  # noqa: BLE001 - timeout, or an error _run recorded
                    continue

    def cancel(self, children: Sequence[ChildRun], reason: str = "parent cancelled") -> None:
        """Apply each child's cancellation policy. Then wait for the ones that stop."""
        for child in children:
            future = getattr(child, "_future", None)
            if child.policy is CancellationPolicy.CASCADE and future is not None \
                    and future.cancel():
                child.state = ChildState.CANCELLED         # never started
                child.error = reason
                continue
            child.request_cancel(reason)
        self.wait([c for c in children
                   if c.policy is not CancellationPolicy.DETACH])

    # ---------------------------------------------------------------- reduce
    def reduce(self, children: Sequence[ChildRun]) -> AggregatedObservation:
        return self.reducer.reduce(children)

    def close(self) -> None:
        self.pool.shutdown(wait=False)
