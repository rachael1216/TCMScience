"""Durability: leases, heartbeats and idempotency.

Measured before it was built: a child whose tool never returned left the parent's
``wait()`` blocked forever, and nothing anywhere recorded that the child was stuck. The
default ``dispatch(wait=True)`` passed no timeout, so a single dead socket inside one child
hung the whole fan-out. Cooperative cancellation cannot help — the child is inside a call
it will never come back from — so the parent needs a signal that does not depend on the
child's cooperation. The absence of a heartbeat is that signal.

Idempotency is the other half of "resume after a crash". ``resume()`` deliberately turns a
task that was RUNNING when the process died into RETRYABLE, because what it did is unknown.
A component that had already performed its side effect would perform it again unless it can
recognise that it has seen this exact piece of work before.
"""

from __future__ import annotations

import threading
import time

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import Autonomy, ComponentKind, ComponentManifest, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    KEY_FIELD, AgentLoopController, CancellationPolicy, CheckpointStore, ChildState,
    Criterion, DelegationRequest, IdempotencyLedger, LeaseRegistry, LocalSubagentBackend,
    LoopLimits, Plan, PlanTask, Reducer, RetryPolicy, StaticPlanner, Supervisor, TaskKind,
    Termination, WorkerLease, key_of, resume,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
        policy=PolicySnapshot(profile_id="dur_bench", allowed_destinations=LOCAL,
                              max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
                              risk_ceiling=RiskTier.R2_CONSEQUENTIAL,
                              require_claim_support=False))
    yield k
    k.close()


class Tool:
    def __init__(self, cid, *, block=None, delay=0.0, fail_times=0, ledger=None):
        self.manifest = ComponentManifest(id=cid, name=cid, kind=ComponentKind.TOOL,
                                          max_label=Sensitivity.PHI)
        self.block, self.delay, self.fail_times, self.ledger = block, delay, fail_times, ledger
        self.calls = 0
        self.keys: list[str] = []
        self.side_effects = 0
        self.entered = threading.Event()

    def invoke(self, payload, envelope):
        self.calls += 1
        self.keys.append(key_of(payload))
        self.entered.set()
        if self.block is not None:
            self.block.wait()
        if self.delay:
            time.sleep(self.delay)
        if self.calls <= self.fail_times:
            raise RuntimeError("transient")
        if self.ledger is not None:
            key = key_of(payload)
            if self.ledger.seen(key):
                return self.ledger.recall(key)
            self.side_effects += 1
            return self.ledger.remember(key, {"effect": self.side_effects})
        return {"ok": True}


def plan_for(component_id, steps=1, retry=None):
    tasks = []
    for i in range(steps):
        tasks.append(PlanTask(task_id=f"s{i}", objective=f"step {i}", kind=TaskKind.TOOL,
                              component_id=component_id,
                              dependencies=(f"s{i - 1}",) if i else (),
                              retry=retry or RetryPolicy()))
    return Plan(objective=component_id, produced_by="test", tasks=tuple(tasks),
                completion_criteria=(Criterion(description="ran", kind="task"),))


def supervisor_for(kernel, tools, *, steps=1, lease_ttl_s=0.3, max_concurrency=4):
    registry = CapabilityRegistry()
    for tool in tools.values():
        registry.register(tool)
    backend = LocalSubagentBackend(
        kernel, planner_factory=lambda c: StaticPlanner(plan_for(c.objective, steps)),
        registry=registry, limits=LoopLimits(max_iterations=steps + 3),
        sleep=lambda s: None)
    return Supervisor(kernel, backend, max_concurrency=max_concurrency,
                      lease_ttl_s=lease_ttl_s)


def request(objective, **kw):
    return DelegationRequest(objective=objective, **{"budget_fraction": 0.2, **kw})


# ============================================================ the hung child

def test_a_hung_child_is_detected_within_its_lease(kernel):
    """The demonstration that motivated this, now terminating."""
    never = threading.Event()
    tools = {"hung": Tool("hung", block=never), "fine": Tool("fine")}
    supervisor = supervisor_for(kernel, tools, lease_ttl_s=0.3)

    t0 = time.time()
    children = supervisor.dispatch([request("hung"), request("fine")],
                                   kernel.policy.envelope())          # wait=True, no timeout
    elapsed = time.time() - t0

    states = {c.contract.objective: c.state for c in children}
    assert states["fine"] is ChildState.COMPLETED
    assert states["hung"] is ChildState.STALLED
    assert elapsed < 3.0, f"the parent waited {elapsed:.1f}s on a child that will never answer"
    hung = next(c for c in children if c.contract.objective == "hung")
    assert "no heartbeat" in hung.error
    never.set()
    supervisor.close()


def test_a_stalled_child_is_told_to_stop_if_it_ever_wakes(kernel):
    """Reaping cancels the token, so a child that finally returns does not carry on."""
    gate = threading.Event()
    tool = Tool("late", block=gate)
    supervisor = supervisor_for(kernel, {"late": tool}, steps=3, lease_ttl_s=0.2)
    (child,) = supervisor.dispatch([request("late")], kernel.policy.envelope())
    assert child.state is ChildState.STALLED
    assert child.token.cancelled

    gate.set()                                     # the tool finally returns
    child._future.result(timeout=5)                # let the thread finish
    assert child.state is ChildState.STALLED, "a stall is not rewritten by a late return"
    assert child.result is not None
    assert child.result.termination == Termination.CANCELLED.value
    assert tool.calls == 1, "the child went on to further steps after being given up on"
    supervisor.close()


def test_a_slow_but_live_child_keeps_its_lease(kernel):
    """Beating per task means a long plan of short steps is never mistaken for a hang.

    The work (8 × 0.2 s) exceeds the lease (1.0 s), so a child that beat only when it
    started would be reaped; per-task beats are what keep it alive. The margin between
    beats is deliberately wide: between two heartbeats the child writes four to five
    audit events, each a separate ``synchronous=FULL`` sqlite commit, and on a loaded CI
    runner's disk the earlier 0.3 s lease left about 50 ms per fsync — the child was
    reaped while waiting on the audit log, not while hung.
    """
    tool = Tool("slow", delay=0.2)
    supervisor = supervisor_for(kernel, {"slow": tool}, steps=8, lease_ttl_s=1.0)
    (child,) = supervisor.dispatch([request("slow")], kernel.policy.envelope())
    assert child.state is ChildState.COMPLETED, child.error
    assert tool.calls == 8
    assert child.lease.age_s > 1.0, "the work must outlast the lease for the test to mean anything"
    supervisor.close()


def test_a_stalled_child_is_recorded_in_the_audit_chain(kernel):
    never = threading.Event()
    supervisor = supervisor_for(kernel, {"hung": Tool("hung", block=never)}, lease_ttl_s=0.2)
    supervisor.dispatch([request("hung")], kernel.policy.envelope())
    kinds = [e.event_type for e in kernel.events.records()]
    assert "child_stalled" in kinds
    assert kernel.events.verify().intact
    never.set()
    supervisor.close()


def test_fan_in_reports_stalled_children_by_id(kernel):
    never = threading.Event()
    tools = {"hung": Tool("hung", block=never), "fine": Tool("fine")}
    supervisor = supervisor_for(kernel, tools, lease_ttl_s=0.2)
    children = supervisor.dispatch([request("hung"), request("fine")],
                                   kernel.policy.envelope())
    aggregate = supervisor.reduce(children)
    hung = next(c for c in children if c.contract.objective == "hung")
    assert aggregate.stalled == (hung.id,)
    assert "1 stalled" in aggregate.summary()
    never.set()
    supervisor.close()


def test_a_lease_is_released_when_a_child_finishes(kernel):
    supervisor = supervisor_for(kernel, {"a": Tool("a")})
    supervisor.dispatch([request("a")], kernel.policy.envelope())
    assert len(supervisor.leases) == 0
    supervisor.close()


def test_the_loop_beats_before_every_task_not_only_per_iteration(kernel):
    beats = {"n": 0}
    registry = CapabilityRegistry()
    registry.register(Tool("a"))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan_for("a", steps=5)),
                               registry=registry, limits=LoopLimits(max_iterations=8),
                               heartbeat=lambda: beats.__setitem__("n", beats["n"] + 1),
                               sleep=lambda s: None)
    result = loop.run("a", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED
    assert beats["n"] >= 5 + result.iterations


def test_a_failing_heartbeat_does_not_end_the_work(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("a"))

    def broken():
        raise OSError("the registry is gone")

    loop = AgentLoopController(kernel, planner=StaticPlanner(plan_for("a")),
                               registry=registry, heartbeat=broken, sleep=lambda s: None)
    assert loop.run("a", kernel.policy.envelope()).termination is Termination.GOAL_SATISFIED


# ============================================================== the registry

def test_a_lease_expires_only_after_silence_of_its_ttl():
    lease = WorkerLease(child_id="c", ttl_s=1.0, issued_at=100.0, last_beat=100.0)
    assert not lease.expired(now=100.9)
    assert lease.expired(now=101.1)
    lease.beat(now=101.0)
    assert not lease.expired(now=101.9)
    assert lease.beats == 1


def test_the_registry_is_thread_safe():
    registry = LeaseRegistry()
    for i in range(8):
        registry.issue(f"c{i}", ttl_s=10)
    errors: list[str] = []

    def hammer(n):
        try:
            for _ in range(500):
                registry.beat(f"c{n % 8}")
                registry.expired()
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(registry) == 8


def test_a_lease_needs_a_positive_ttl(kernel):
    with pytest.raises(ValueError):
        LeaseRegistry().issue("c", ttl_s=0)
    with pytest.raises(ValueError):
        Supervisor(kernel, lambda c, **k: None, lease_ttl_s=-1)


# ============================================================== idempotency

def test_a_tool_receives_the_same_key_on_every_attempt(kernel):
    tool = Tool("flaky", fail_times=2)
    registry = CapabilityRegistry()
    registry.register(tool)
    plan = plan_for("flaky", retry=RetryPolicy(max_attempts=3))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               limits=LoopLimits(max_iterations=6), sleep=lambda s: None)
    result = loop.run("flaky", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED
    assert len(tool.keys) == 3
    assert len(set(tool.keys)) == 1, tool.keys
    assert tool.keys[0].endswith(":s0")


def test_the_key_is_stable_across_a_resume(kernel, tmp_path):
    """The case idempotency exists for: RUNNING at the crash, RETRYABLE after it."""
    from psh.runtime import ExecutionGraph, LoopState, capture

    ledger = IdempotencyLedger()
    tool = Tool("effect", ledger=ledger)
    registry = CapabilityRegistry()
    registry.register(tool)
    plan = plan_for("effect", steps=2)

    # First life: step 0 runs, the process dies while step 1 is RUNNING (after its side
    # effect happened, which is exactly the situation the key is for).
    first = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                                limits=LoopLimits(max_iterations=1), sleep=lambda s: None)
    first.run("effect", kernel.policy.envelope())
    assert tool.side_effects == 1

    state = LoopState(loop_id="l", objective="effect", envelope=kernel.policy.envelope())
    state.plan = plan
    state.graph = ExecutionGraph(plan)
    state.graph.mark_running("s0", at=1.0)
    state.graph.mark_succeeded("s0", {"effect": 1}, at=2.0, label=DataLabel())
    state.graph.mark_running("s1", at=3.0)             # ...and then the process ended
    # Pretend the side effect of s1 already landed under the key the loop will present.
    ledger.remember(f"{state.envelope.run_id}:s1", {"effect": "already done"})

    resumed = resume(capture(state, policy=kernel.policy), kernel)
    assert resumed.envelope.run_id == state.envelope.run_id, \
        "resume() must preserve the run id, or every key changes"

    second = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                                 limits=LoopLimits(max_iterations=4), sleep=lambda s: None)
    result = second.run("effect", kernel.policy.envelope(), resume_from=resumed)

    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert result.results["s1"] == {"effect": "already done"}, \
        "the replayed task performed its side effect a second time"
    assert ledger.replays == 1


def test_the_ledger_only_answers_for_keys_it_was_told_about():
    ledger = IdempotencyLedger()
    assert not ledger.seen("")
    assert not ledger.seen("never")
    ledger.remember("", {"ignored": True})
    assert len(ledger) == 0, "an empty key is 'no key', not a key"
    ledger.remember("k", {"n": 1})
    assert ledger.seen("k") and ledger.recall("k") == {"n": 1}


def test_key_of_is_empty_outside_a_loop():
    assert key_of({"q": "x"}) == ""
    assert key_of(None) == ""
    assert key_of({KEY_FIELD: "run:task"}) == "run:task"


# ================================================ closing a shared event store

def test_closing_the_event_store_while_a_thread_appends_does_not_crash(tmp_path):
    """Measured as a segmentation fault: a stalled child mid-append, its kernel closed.

    The store is shared between threads by design. That makes closing a concurrency
    question: sqlite's C extension does not survive one thread closing a connection
    another is inside, and the result is a crash, not an exception. close() takes the
    write lock now, and appends after it refuse cleanly.
    """
    from psh.kernel.events import EventStore

    store = EventStore(tmp_path / "events.db")
    stop = threading.Event()
    outcomes: list[str] = []

    def writer():
        while not stop.is_set():
            try:
                store.append("probe", run_id="r")
                outcomes.append("ok")
            except RuntimeError as exc:
                outcomes.append("refused" if "closed" in str(exc) else f"other:{exc}")
                return
            except Exception as exc:  # noqa: BLE001
                outcomes.append(f"crashed:{type(exc).__name__}")
                return

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    store.close()                                   # while they are still appending
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert all(not t.is_alive() for t in threads)
    assert "ok" in outcomes, "the writers never got going"
    assert not [o for o in outcomes if o.startswith(("other", "crashed"))], outcomes
    store.close()                                   # idempotent


def test_a_stalled_child_outliving_its_kernel_fails_quietly(kernel):
    """The original crash, end to end: reap, close the kernel, then let the child wake."""
    gate = threading.Event()
    tool = Tool("late", block=gate)
    supervisor = supervisor_for(kernel, {"late": tool}, lease_ttl_s=0.2)
    (child,) = supervisor.dispatch([request("late")], kernel.policy.envelope())
    assert child.state is ChildState.STALLED

    kernel.close()                                  # the parent is done with it
    gate.set()                                      # ...and only now does the child return
    child._future.result(timeout=5)                 # no crash, no unhandled error
    assert child.state is ChildState.STALLED
    supervisor.close()
