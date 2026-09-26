"""Multi-agent: fan-out that cannot widen, fan-in that records, cancellation that is stated.

The rule under which all of this was built, and the reason it lives in PSH rather than
being adopted from a framework:

    Supervisor --propose--> TrustedKernel --authorize--> Worker

The supervisor holds no way to construct a ``RunEnvelope``. It turns a request into
arguments for ``parent.restrict()`` — the lattice — so asking for more than the parent has
raises from inside the kernel, not from a comparison the supervisor was trusted to make.
``test_a_supervisor_cannot_widen_a_child`` is that rule; everything else here is the
consequences of taking it seriously under concurrency.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, BudgetExhausted, ComponentKind, ComponentManifest, PolicyDenied,
    RiskTier, RunEnvelope,
)
from psh.kernel import TrustedKernel
from psh.kernel.authority import AuthorityLattice
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AggregatedObservation, BudgetLedger, CancellationPolicy, ChildRun, ChildState,
    Criterion, DelegationRequest, LocalSubagentBackend, LoopLimits, Plan, PlanTask,
    Reducer, StaticPlanner, SubagentResult, Supervisor, TaskKind, Termination,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
        policy=PolicySnapshot(profile_id="sup_bench", allowed_destinations=LOCAL,
                              max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
                              risk_ceiling=RiskTier.R2_CONSEQUENTIAL,
                              require_claim_support=False))
    yield k
    k.close()


class Tool:
    """Records which threads invoked it and can block, fail or return a fixed result."""

    def __init__(self, cid, result=None, *, fail=False, block=None, delay=0.0):
        self.manifest = ComponentManifest(id=cid, name=cid, kind=ComponentKind.TOOL,
                                          max_label=Sensitivity.PHI)
        self.result = result if result is not None else {"ok": True}
        self.fail, self.block, self.delay = fail, block, delay
        self.calls = 0
        self.threads: set[int] = set()
        self.payloads: list = []
        self._lock = threading.Lock()

    def invoke(self, payload, envelope):
        with self._lock:
            self.calls += 1
            self.threads.add(threading.get_ident())
            self.payloads.append(payload)
        if self.block is not None:
            self.block.wait()
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("this tool is broken")
        return self.result


def one_tool_plan(component_id, steps=1):
    tasks = []
    for i in range(steps):
        tasks.append(PlanTask(task_id=f"step{i}", objective=f"step {i}", kind=TaskKind.TOOL,
                              component_id=component_id,
                              dependencies=(f"step{i - 1}",) if i else ()))
    return Plan(objective=component_id, produced_by="test", tasks=tuple(tasks),
                completion_criteria=(Criterion(description="ran", kind="task"),))


def make(kernel, tools, *, steps=1, max_concurrency=4, limits=None):
    """A supervisor whose children each run a plan calling the tool named by the objective."""
    registry = CapabilityRegistry()
    for tool in tools.values():
        registry.register(tool)
    backend = LocalSubagentBackend(
        kernel, planner_factory=lambda c: StaticPlanner(one_tool_plan(c.objective, steps)),
        registry=registry, limits=limits or LoopLimits(max_iterations=steps + 3),
        sleep=lambda s: None)
    return Supervisor(kernel, backend, max_concurrency=max_concurrency)


def request(objective, **kw):
    return DelegationRequest(objective=objective, **{"budget_fraction": 0.2, **kw})


# ================================================================ THE rule

def test_a_supervisor_cannot_widen_a_child(kernel):
    """Asking for more than the parent holds raises from inside restrict(), not from us."""
    supervisor = make(kernel, {"a": Tool("a")})
    parent = kernel.policy.envelope(risk=RiskTier.R1_ROUTINE)

    with pytest.raises(PolicyDenied, match="risk"):
        supervisor.mint(request("a", max_risk=RiskTier.R2_CONSEQUENTIAL), parent)
    with pytest.raises(PolicyDenied, match="destinations"):
        supervisor.mint(request("a", destinations=(Destination.PUBLIC_REMOTE,)), parent)
    with pytest.raises(PolicyDenied, match="max_label"):
        supervisor.mint(request("a", max_label=Sensitivity.SECRET),
                        kernel.policy.envelope(max_label=Sensitivity.INTERNAL))
    supervisor.close()


def test_the_supervisor_holds_no_authority_comparison_of_its_own():
    """No third opinion: mint() contains no check against the parent. restrict() does."""
    import ast
    import inspect
    import textwrap

    from psh.runtime import supervisor as module

    source = textwrap.dedent(inspect.getsource(module.Supervisor.mint))
    tree = ast.parse(source)

    def mentions_parent(node: ast.AST) -> bool:
        return any(isinstance(n, ast.Name) and n.id == "parent" for n in ast.walk(node))

    # ``x is not None`` guards are not authority checks. A comparison that reads the
    # parent is: it would be the supervisor deciding what the parent permits.
    against_parent = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Compare) and mentions_parent(n)]
    assert against_parent == [], (
        "mint() compares against the parent; authority containment belongs to the lattice, "
        "and a second comparison here is the DelegationGateway defect over again")
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "restrict" in calls, "the envelope must come from restrict(), i.e. the lattice"


def test_every_child_holds_authority_within_its_parent(kernel):
    supervisor = make(kernel, {n: Tool(n) for n in "abc"})
    parent = kernel.policy.envelope()
    children = supervisor.dispatch([request(n, max_risk=RiskTier.R1_ROUTINE)
                                    for n in "abc"], parent)
    for child in children:
        assert AuthorityLattice.is_subset(child.contract.envelope, parent)
        assert child.contract.envelope.parent_run_id == parent.run_id
        assert child.contract.envelope.risk is RiskTier.R1_ROUTINE
    supervisor.close()


def test_the_delegation_gateway_rules_on_every_child(kernel):
    """Fan-out goes through the same door, once per child, from worker threads."""
    supervisor = make(kernel, {n: Tool(n) for n in "abcd"})
    before = kernel.delegation_gateway.checks
    supervisor.dispatch([request(n) for n in "abcd"], kernel.policy.envelope())
    assert kernel.delegation_gateway.checks == before + 4
    supervisor.close()


# ======================================================= budget under fan-out

def test_budget_child_alone_lets_siblings_outspend_the_parent():
    """The defect, measured: what the docstring promised and did not deliver."""
    parent = Budget(tokens_hard=100_000)
    ten_children = sum(parent.child(0.25).tokens_hard for _ in range(10))
    assert ten_children == 250_000, "ten quarter-children are two and a half parents"


def test_fan_out_cannot_multiply_the_budget(kernel):
    supervisor = make(kernel, {n: Tool(n) for n in "abcde"})
    parent = kernel.policy.envelope()
    with pytest.raises(BudgetExhausted, match="worth more than the parent"):
        supervisor.dispatch([request(n, budget_fraction=0.25) for n in "abcde"], parent)
    supervisor.close()


def test_the_ledger_is_cumulative_over_the_parents_life(kernel):
    """A finished child spent its slice. Releasing it would allow children forever."""
    supervisor = make(kernel, {n: Tool(n) for n in "abcde"})
    parent = kernel.policy.envelope()
    first = supervisor.dispatch([request(n, budget_fraction=0.25) for n in "abcd"], parent)
    assert all(c.state is ChildState.COMPLETED for c in first)
    assert supervisor.ledger.allocated(parent.run_id) == pytest.approx(1.0)
    with pytest.raises(BudgetExhausted):
        supervisor.dispatch([request("e", budget_fraction=0.1)], parent)
    supervisor.close()


def test_an_over_allocating_batch_is_refused_whole_not_half_started(kernel):
    tools = {n: Tool(n) for n in "abcde"}
    supervisor = make(kernel, tools)
    with pytest.raises(BudgetExhausted):
        supervisor.dispatch([request(n, budget_fraction=0.3) for n in "abcde"],
                            kernel.policy.envelope())
    assert all(t.calls == 0 for t in tools.values()), "part of a refused batch ran"
    supervisor.close()


def test_the_ledger_refuses_a_nonsensical_fraction():
    ledger = BudgetLedger()
    with pytest.raises(ValueError):
        ledger.reserve("run", 0.0)
    with pytest.raises(ValueError):
        ledger.reserve("run", 1.5)


# ============================================================ concurrency

def test_children_actually_run_concurrently(kernel):
    """Real fan-out: every child must reach a barrier at once, or the barrier times out.

    A serial pool would run child 1 to completion before starting child 2, and child 1's
    tool would wait at the barrier forever; the timeout breaks it and the child fails.
    """
    barrier = threading.Barrier(3, timeout=5)
    tools = {n: Tool(n, block=barrier) for n in "abc"}
    supervisor = make(kernel, tools, max_concurrency=3)
    children = supervisor.dispatch([request(n) for n in "abc"], kernel.policy.envelope())
    assert [c.state for c in children] == [ChildState.COMPLETED] * 3, \
        [c.error for c in children]
    assert len({t for tool in tools.values() for t in tool.threads}) == 3
    supervisor.close()


def test_concurrency_is_bounded(kernel):
    """Four children, two workers: never more than two in flight."""
    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class Counting(Tool):
        def invoke(self, payload, envelope):
            with lock:
                in_flight["now"] += 1
                in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
            try:
                time.sleep(0.05)
                return {"ok": True}
            finally:
                with lock:
                    in_flight["now"] -= 1

    supervisor = make(kernel, {n: Counting(n) for n in "abcd"}, max_concurrency=2)
    supervisor.dispatch([request(n) for n in "abcd"], kernel.policy.envelope())
    assert in_flight["peak"] == 2, in_flight
    supervisor.close()


def test_the_broker_counters_balance_across_threads(kernel):
    """The counters are the proof nothing bypassed the broker; they must survive threads."""
    supervisor = make(kernel, {n: Tool(n) for n in "abcdef"}, steps=3, max_concurrency=6)
    before = kernel.broker.stats()
    supervisor.dispatch([request(n, budget_fraction=0.15) for n in "abcdef"],
                        kernel.policy.envelope())
    after = kernel.broker.stats()
    assert after["delegations"] - before["delegations"] == 6
    assert after["tool_calls"] - before["tool_calls"] == 18
    assert kernel.events.verify().intact
    supervisor.close()


def test_the_budget_ceiling_holds_under_concurrency():
    """Check-and-increment is one atomic step now, whatever the scheduler does."""
    from psh.kernel.budget import BudgetGovernor

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        governor = BudgetGovernor(Budget(max_model_calls=40))
        envelope = RunEnvelope(budget=Budget(max_model_calls=40))
        passed = []
        barrier = threading.Barrier(32)

        def worker():
            barrier.wait()
            for _ in range(8):
                try:
                    governor.check_model_call(envelope)
                    passed.append(1)
                except BudgetExhausted:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(previous)
    assert len(passed) == 40


# ====================================================== what a child returns

def test_a_child_returns_a_summary_not_a_transcript(kernel):
    """The parent gets claims and handles. It does not get what the child worked from."""
    marker = "SECRET-PAYLOAD-MRN-4472213"
    tool = Tool("a", {"claims": ["The cohort was assembled in 2019."],
                      "evidence": ["PMID 1"]})
    registry = CapabilityRegistry()
    registry.register(tool)
    plan = Plan(objective="a", produced_by="test",
                tasks=(PlanTask(task_id="t", objective="o", kind=TaskKind.TOOL,
                                component_id="a", payload={"note": marker}),),
                completion_criteria=(Criterion(description="ran", kind="task"),))
    backend = LocalSubagentBackend(kernel, planner_factory=lambda c: StaticPlanner(plan),
                                   registry=registry, sleep=lambda s: None)
    supervisor = Supervisor(kernel, backend)
    (child,) = supervisor.dispatch([request("a")], kernel.policy.envelope())

    assert child.state is ChildState.COMPLETED
    result = child.result
    assert isinstance(result, SubagentResult)
    assert tool.payloads[0]["note"] == marker, "the child did see it"
    assert marker not in result.summary, "...and the parent must not"
    assert not any(hasattr(result, f) for f in ("transcript", "prompts", "payloads"))
    assert result.claims == ("The cohort was assembled in 2019.",)
    assert result.evidence == ("PMID 1",)
    supervisor.close()


def test_a_childs_label_is_the_join_of_what_it_saw(kernel):
    """A child that handled PHI hands back PHI, however innocuous its summary reads."""
    tool = Tool("a", {"claims": ["Outcome recorded."],
                      "note": "Patient John Doe, MRN 4472213, seen 2024-03-02"})
    supervisor = make(kernel, {"a": tool})
    (child,) = supervisor.dispatch([request("a")], kernel.policy.envelope())
    assert child.state is ChildState.COMPLETED, child.error
    assert child.result.label.sensitivity is Sensitivity.PHI
    supervisor.close()


def test_a_failed_child_does_not_take_the_parent_down(kernel):
    tools = {"a": Tool("a"), "b": Tool("b", fail=True), "c": Tool("c")}
    supervisor = make(kernel, tools, limits=LoopLimits(max_iterations=2, max_replans=0))
    children = supervisor.dispatch([request(n) for n in "abc"], kernel.policy.envelope())
    states = {c.contract.objective: c.state for c in children}
    assert states["a"] is ChildState.COMPLETED and states["c"] is ChildState.COMPLETED
    # The broken tool makes the child's loop ESCALATE (its plan cannot be satisfied); the
    # child *completed* as a delegation and reports its own termination honestly.
    assert states["b"] is ChildState.COMPLETED
    assert children[1].result.termination != Termination.GOAL_SATISFIED.value
    assert children[1].result.failures
    supervisor.close()


def test_a_child_may_delegate_only_through_the_same_door(kernel):
    """Nested delegation: grandchild ⊆ child ⊆ parent, and the broker counts both."""
    leaf = Tool("leaf", {"claims": ["leaf ran"]})
    registry = CapabilityRegistry()
    registry.register(leaf)

    def planner_for(contract):
        if contract.objective == "middle":
            plan = Plan(objective="middle", produced_by="test",
                        tasks=(PlanTask(task_id="sub", objective="leaf",
                                        kind=TaskKind.DELEGATE),),
                        completion_criteria=(Criterion(description="ran", kind="task"),))
        else:
            plan = one_tool_plan("leaf")
        return StaticPlanner(plan)

    backend = LocalSubagentBackend(kernel, planner_factory=planner_for, registry=registry,
                                   limits=LoopLimits(max_iterations=4), sleep=lambda s: None)
    supervisor = Supervisor(kernel, backend)
    parent = kernel.policy.envelope(budget=Budget(max_delegations=4))
    before = kernel.broker.stats()["delegations"]
    (child,) = supervisor.dispatch([request("middle", budget_fraction=0.5)], parent)

    assert child.state is ChildState.COMPLETED, child.error
    assert kernel.broker.stats()["delegations"] - before == 2
    assert leaf.calls == 1
    supervisor.close()


# ============================================================== fan-in

def _child(cid, claims, evidence=(), label=Sensitivity.PUBLIC, state=ChildState.COMPLETED):
    from psh.contracts import DelegationContract
    contract = DelegationContract(task_id="t", objective=cid, envelope=RunEnvelope())
    result = SubagentResult(child_run_id=cid, summary="s", termination="goal_satisfied",
                            claims=tuple(claims), evidence=tuple(evidence),
                            label=DataLabel(label))
    return ChildRun(contract=contract, state=state, result=result, id=cid)


def test_fan_in_records_a_disagreement_as_state():
    """Two children disagree. That is a Conflict, not two paragraphs concatenated."""
    agg = Reducer().reduce([
        _child("a", ["Empagliflozin reduces hospitalisation."], ["PMID 1"]),
        _child("b", ["Empagliflozin does not reduce hospitalisation."], ["PMID 2"])])
    assert len(agg.conflicts) == 1
    conflict = agg.conflicts[0]
    assert {conflict.left.asserted_by[0], conflict.right.asserted_by[0]} == {"a", "b"}
    assert "polarity" in conflict.rationale


def test_fan_in_deduplicates_and_attributes():
    agg = Reducer().reduce([
        _child("a", ["The cohort was assembled in 2019."], ["PMID 1"]),
        _child("b", ["the cohort was assembled in 2019"], ["PMID 2"]),
        _child("c", ["A different finding entirely."])])
    statements = {f.statement for f in agg.facts}
    assert len(agg.facts) == 2
    shared = next(f for f in agg.facts if "2019" in f.statement)
    assert set(shared.asserted_by) == {"a", "b"}
    assert set(shared.evidence) == {"PMID 1", "PMID 2"}
    assert agg.conflicts == ()


def test_fan_in_names_what_has_no_evidence():
    agg = Reducer().reduce([_child("a", ["Supported."], ["PMID 1"]),
                            _child("b", ["Asserted without a source."])])
    assert agg.unresolved == ("Asserted without a source.",)


def test_fan_in_label_is_the_join():
    agg = Reducer().reduce([_child("a", ["x"], label=Sensitivity.PUBLIC),
                            _child("b", ["y"], label=Sensitivity.PHI)])
    assert agg.label.sensitivity is Sensitivity.PHI


def test_fan_in_reports_failed_and_cancelled_children_by_id():
    agg = Reducer().reduce([_child("a", ["x"]),
                            _child("b", [], state=ChildState.FAILED),
                            _child("c", [], state=ChildState.CANCELLED)])
    assert agg.failed == ("b",) and agg.cancelled == ("c",)
    assert agg.children == 3
    assert "1 failed" in agg.summary() and "1 cancelled" in agg.summary()


# ========================================================== cancellation

def test_cascade_stops_running_children(kernel):
    """A five-step child told to stop after step one ends CANCELLED with steps unrun."""
    started = threading.Event()
    tool = Tool("slow", delay=0.05)
    original = tool.invoke

    def invoke(payload, envelope):
        started.set()
        return original(payload, envelope)

    tool.invoke = invoke
    supervisor = make(kernel, {"slow": tool}, steps=5)
    (child,) = supervisor.dispatch([request("slow", cancellation=CancellationPolicy.CASCADE)],
                                   kernel.policy.envelope(), wait=False)
    assert started.wait(5)
    supervisor.cancel([child], reason="operator stopped the run")

    assert child.state is ChildState.CANCELLED
    assert child.result is not None
    assert child.result.termination == Termination.CANCELLED.value
    assert tool.calls < 5, "the child kept going after being told to stop"
    supervisor.close()


def test_wait_lets_children_finish(kernel):
    tool = Tool("slow", delay=0.01)
    supervisor = make(kernel, {"slow": tool}, steps=3)
    (child,) = supervisor.dispatch([request("slow", cancellation=CancellationPolicy.WAIT)],
                                   kernel.policy.envelope(), wait=False)
    supervisor.cancel([child])
    assert child.state is ChildState.COMPLETED
    assert tool.calls == 3
    supervisor.close()


def test_detach_orphans_without_waiting(kernel):
    """A *running* child, detached, is disowned and keeps running; the parent does not wait.

    The child must be inside the tool before it is cancelled, and the test then waits on
    its future rather than sleeping. The earlier version cancelled straight after
    ``dispatch(wait=False)`` and asserted the tool had run 0.1 s after ``close()``; on a
    loaded CI runner the child was still writing the audit events on its way to the tool
    (each a synchronous sqlite commit) and the orphan was reported as never having run.
    Entering the tool first also pins the state: ``_run`` sets RUNNING when it starts, so
    an orphaning applied before that would be overwritten and end as COMPLETED.
    """
    release = threading.Event()
    tool = Tool("slow", block=release)
    supervisor = make(kernel, {"slow": tool}, steps=1)
    (child,) = supervisor.dispatch([request("slow", cancellation=CancellationPolicy.DETACH)],
                                   kernel.policy.envelope(), wait=False)
    deadline = time.time() + 10
    while tool.calls == 0 and time.time() < deadline:
        time.sleep(0.005)
    assert tool.calls == 1, "the child never reached the tool"

    t0 = time.time()
    supervisor.cancel([child])
    assert time.time() - t0 < 1.0, "cancel() waited for a detached child"
    assert child.state is ChildState.ORPHANED
    release.set()                                  # let the orphan finish
    supervisor.wait([child])                       # orphans are skipped: returns at once
    child._future.result(timeout=10)               # the orphan ran to completion on its own
    assert child.state is ChildState.ORPHANED, "a completed orphan keeps its state"
    assert child.result is not None
    supervisor.close()


def test_a_queued_child_cancelled_before_starting_never_runs(kernel):
    gate = threading.Event()
    tools = {"gate": Tool("gate", block=gate), "never": Tool("never")}
    supervisor = make(kernel, tools, max_concurrency=1)      # 'never' queues behind 'gate'
    children = supervisor.dispatch([request("gate"), request("never")],
                                   kernel.policy.envelope(), wait=False)
    supervisor.cancel([children[1]])
    gate.set()
    supervisor.wait(children)
    assert children[1].state is ChildState.CANCELLED
    assert tools["never"].calls == 0
    supervisor.close()


def test_a_supervisor_records_the_delegation_in_the_audit_chain(kernel):
    supervisor = make(kernel, {"a": Tool("a")})
    supervisor.dispatch([request("a")], kernel.policy.envelope())
    kinds = [e.event_type for e in kernel.events.records()]
    assert "delegation" in kinds
    assert kernel.events.verify().intact
    supervisor.close()
