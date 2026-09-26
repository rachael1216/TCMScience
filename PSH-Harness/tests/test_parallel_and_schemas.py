"""Two frontier-runtime properties, each governed the same way as the sequential path.

**Parallel branches.** An iteration's ready set is independent by construction, so it may
run on a bounded pool of threads. The governance does not change: every branch is a
broker call, the gates and the budget governor are locked, a refusal in one branch is that
branch's failure and no other's, and a bound one branch hits surfaces deterministically.

**Level-2 disclosure.** Summaries let a model choose a capability; schemas let it call
one. The registry renders the payload schema of the few candidates that survived ranking,
from the manifest and nothing else, and the planner sees them.
"""

from __future__ import annotations

import threading
import time

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, ComponentKind, ComponentManifest, ContextItem, ModelProfile, RiskTier,
)
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Labeled, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, Criterion, LoopLimits, LoopState, ModelPlanner, Plan, PlanTask,
    StaticPlanner, TaskKind, TaskState, Termination,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


def policy(**kw):
    params = dict(profile_id="parallel_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=LOCAL, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


class SlowTool:
    """Records how many calls are in flight at once, so parallelism is measured."""

    def __init__(self, component_id, delay=0.15, **manifest_kw):
        params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI, risk_tier=RiskTier.R1_ROUTINE)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.delay = delay
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        #: Seconds during which two or more calls were in flight at once: the direct
        #: measure of parallelism, independent of anything the kernel does around a call.
        self.overlap_s = 0.0
        self._overlap_started = None
        self._lock = threading.Lock()

    def invoke(self, payload, envelope):
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.calls += 1
            if self.in_flight == 2:
                self._overlap_started = time.perf_counter()
        try:
            time.sleep(self.delay)
            return {"echo": payload.get("n"), "thread": threading.current_thread().name}
        finally:
            with self._lock:
                if self.in_flight == 2 and self._overlap_started is not None:
                    self.overlap_s += time.perf_counter() - self._overlap_started
                    self._overlap_started = None
                self.in_flight -= 1


def plan_of(*tasks):
    return Plan(objective="fan out", tasks=tuple(tasks),
                completion_criteria=(Criterion(description="done"),), produced_by="test")


def fan_out(tool_id, n):
    return plan_of(*[PlanTask(task_id=f"t{i}", objective=f"call {i}", kind=TaskKind.TOOL,
                              component_id=tool_id, payload={"n": i}) for i in range(n)])


# ================================================================= parallel branches

def test_independent_tasks_run_concurrently_and_the_broker_still_counts_every_one(kernel):
    """Parallelism is measured inside the tool, not by the loop's wall-clock time.

    An earlier version also required the whole batch to finish in under six delays. That
    bound measured the runner's disk as much as the loop: every branch writes its audit
    events as synchronous sqlite commits, serialised by the event store, and on a loaded
    CI runner those alone exceeded it while the branches were demonstrably overlapping.
    The tool records how long two or more calls were in flight together; with four
    workers and six 0.15 s calls that is well above half a delay whenever branches run
    concurrently, and zero when they do not.
    """
    registry = CapabilityRegistry()
    tool = SlowTool("slow")
    registry.register(tool)
    before = kernel.broker.stats()
    loop = AgentLoopController(kernel, planner=StaticPlanner(fan_out("slow", 6)),
                               registry=registry, limits=LoopLimits(max_parallel=4),
                               sleep=lambda s: None)
    result = loop.run("fan out", kernel.policy.envelope())

    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert tool.peak >= 2, "branches never overlapped"
    assert tool.overlap_s >= 0.5 * tool.delay, \
        f"branches overlapped for only {tool.overlap_s:.3f}s of {6 * tool.delay:.2f}s of work"
    assert result.results == {f"t{i}": result.results[f"t{i}"] for i in range(6)}
    assert [result.results[f"t{i}"]["echo"] for i in range(6)] == list(range(6))
    after = kernel.broker.stats()
    assert after["tool_calls"] - before["tool_calls"] == 6
    assert after["tool_gate_checks"] - before["tool_gate_checks"] == 6
    assert tool.calls == 6
    assert result.label is not None


def test_sequential_is_the_default_and_the_bound_is_validated(kernel):
    assert LoopLimits().max_parallel == 1
    with pytest.raises(ValueError, match="max_parallel"):
        LoopLimits(max_parallel=0)
    registry = CapabilityRegistry()
    tool = SlowTool("slow", delay=0.02)
    registry.register(tool)
    loop = AgentLoopController(kernel, planner=StaticPlanner(fan_out("slow", 3)),
                               registry=registry, sleep=lambda s: None)
    result = loop.run("fan out", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED
    assert tool.peak == 1


def test_a_refusal_in_one_branch_is_that_branchs_failure_only(kernel):
    """PHI to a PUBLIC-only tool is refused in its branch; its siblings complete."""
    registry = CapabilityRegistry()
    open_tool = SlowTool("open", delay=0.05)
    narrow_tool = SlowTool("narrow", delay=0.05, max_label=Sensitivity.PUBLIC)
    registry.register(open_tool)
    registry.register(narrow_tool)
    plan = plan_of(
        PlanTask(task_id="a", objective="fine", kind=TaskKind.TOOL, component_id="open",
                 payload={"n": 1}),
        PlanTask(task_id="b", objective="refused", kind=TaskKind.TOOL, component_id="narrow",
                 payload={"n": 2, "note": "Patient Alice Smith MRN 04851923"},
                 max_label=Sensitivity.PHI),
        PlanTask(task_id="c", objective="fine too", kind=TaskKind.TOOL, component_id="open",
                 payload={"n": 3}),
        PlanTask(task_id="d", objective="downstream of the refusal", kind=TaskKind.TOOL,
                 component_id="open", dependencies=("b",), payload={"n": 4}))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               limits=LoopLimits(max_parallel=3, max_replans=0),
                               sleep=lambda s: None)
    result = loop.run("mixed", kernel.policy.envelope())
    counts = result.state_counts
    assert counts.get("succeeded") == 2 and counts.get("failed") == 1
    assert counts.get("blocked", 0) + counts.get("cancelled", 0) == 1
    assert set(result.results) == {"a", "c"}
    assert narrow_tool.calls == 0, "the refused branch reached its component"
    assert result.termination is Termination.ESCALATED


def test_a_bound_hit_in_one_branch_ends_the_loop_deterministically(kernel):
    """An approval demanded in one branch (no handler is wired, so it cannot be granted)
    surfaces as the loop's termination after the sibling branches already in flight have
    recorded their own outcomes; the gated component itself is never reached."""
    registry = CapabilityRegistry()
    tool = SlowTool("slow", delay=0.05)
    gated = SlowTool("gated", delay=0.05, human_approval=True)
    registry.register(tool)
    registry.register(gated)
    plan = plan_of(
        PlanTask(task_id="a", objective="fine", kind=TaskKind.TOOL, component_id="slow",
                 payload={"n": 1}),
        PlanTask(task_id="b", objective="needs approval", kind=TaskKind.TOOL,
                 component_id="gated", payload={"n": 2}),
        PlanTask(task_id="c", objective="fine", kind=TaskKind.TOOL, component_id="slow",
                 payload={"n": 3}))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               limits=LoopLimits(max_parallel=3), sleep=lambda s: None)
    result = loop.run("mixed", kernel.policy.envelope())
    assert result.termination is Termination.POLICY_DENIED, result.summary()
    assert "ApprovalRequired" in result.reason
    assert gated.calls == 0
    assert tool.calls == 2 and set(result.results) == {"a", "c"}


def test_block_descendants_never_blocks_a_running_sibling():
    """A failure's descendants are blocked; a sibling still running is left to finish."""
    from psh.runtime import ExecutionGraph

    plan = plan_of(
        PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL, component_id="x"),
        PlanTask(task_id="b", objective="b", kind=TaskKind.TOOL, component_id="x"),
        PlanTask(task_id="c", objective="c", kind=TaskKind.TOOL, component_id="x",
                 dependencies=("a",)))
    graph = ExecutionGraph(plan)
    graph.mark_running("a", at=1.0)
    graph.mark_running("b", at=1.0)
    graph.mark_failed("a", "boom", at=2.0, retryable=False)
    assert graph.nodes["c"].state is TaskState.BLOCKED
    assert graph.nodes["b"].state is TaskState.RUNNING
    graph.mark_succeeded("b", {"ok": True}, at=3.0, label=DataLabel())
    assert graph.nodes["b"].state is TaskState.SUCCEEDED


# ================================================================ level-2 disclosure

def manifest(component_id, **kw):
    params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                  description=f"{component_id} does things", max_label=Sensitivity.PHI)
    params.update(kw)
    return ComponentManifest(**params)


class Stub:
    def __init__(self, m):
        self.manifest = m

    def invoke(self, payload, envelope):
        return {"ok": True}


def test_schema_items_render_operations_parameters_and_properties(kernel):
    registry = CapabilityRegistry()
    registry.register(Stub(manifest("conn", input_schema={
        "type": "object", "required": ["operation"],
        "operations": {"symbol": {"description": "Gene by symbol", "args": ["symbol"],
                                  "example": {"symbol": "TP53"}}}})))
    registry.register(Stub(manifest("native", input_schema={
        "parameters": [{"name": "weight_kg", "required": True},
                       {"name": "height_cm", "required": True}],
        "example": {"weight_kg": 70, "height_cm": 175}})))
    registry.register(Stub(manifest("json", input_schema={
        "type": "object", "required": ["q"], "properties": {"q": {}, "limit": {}}})))
    registry.register(Stub(manifest("bare")))
    candidates = registry.resolve("conn native json bare things", kernel.policy.envelope(),
                                  limit=10)
    items = registry.schema_items(candidates, limit=10)
    text = {i.source_ref: i.content for i in items}
    assert '"operation": "symbol"' in text["conn"] and "symbol(symbol)" in text["conn"]
    assert "weight_kg, height_cm" in text["native"] and '"weight_kg": 70' in text["native"]
    assert "q, limit?" in text["json"]
    assert "no schema declared" in text["bare"]
    assert all(i.kind == "manifest" for i in items)


def test_schema_items_carry_the_description_label_and_respect_the_limit(kernel):
    registry = CapabilityRegistry()
    for i in range(4):
        registry.register(Stub(manifest(f"tool{i}", provenance={"description_sensitivity": "PHI"},
                                        input_schema={"properties": {"x": {}}})))
    candidates = registry.resolve("tool0 tool1 tool2 tool3", kernel.policy.envelope(), limit=10)
    items = registry.schema_items(candidates, limit=2)
    assert len(items) == 2
    assert all(i.label.sensitivity is Sensitivity.PHI for i in items)


def test_the_planner_discloses_schemas_for_the_best_matches(kernel):
    registry = CapabilityRegistry()
    registry.register(Stub(manifest("public.connector.hgnc", description="HGNC gene records",
                                    input_schema={"operations": {"symbol": {
                                        "description": "Gene by symbol", "args": ["symbol"],
                                        "example": {"symbol": "TP53"}}}})))
    served: list[str] = []
    plan_json = ('{"objective": "x", "tasks": [{"task_id": "t", "objective": "look up TP53", '
                 '"kind": "tool", "component_id": "public.connector.hgnc", '
                 '"payload": {"operation": "symbol", "symbol": "TP53"}}], '
                 '"completion_criteria": [{"description": "found", "kind": "task"}]}')
    local = ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                         max_label=Sensitivity.PHI, usd_per_1k_input=0.0, usd_per_1k_output=0.0)
    planner = ModelPlanner(kernel, model=local, registry=registry,
                           model_invoke=lambda p: served.append(p) or plan_json)
    plan = planner.plan(LoopState(loop_id="l", objective="find the HGNC record for TP53",
                                  envelope=kernel.policy.envelope()))
    assert plan.tasks[0].payload == {"operation": "symbol", "symbol": "TP53"}
    prompt = served[0]
    assert "public.connector.hgnc payload:" in prompt and "symbol(symbol)" in prompt
    assert "payload schema" in prompt                      # the rule that explains it

    quiet = ModelPlanner(kernel, model=local, registry=registry, schema_candidates=0,
                         model_invoke=lambda p: served.append(p) or plan_json)
    quiet.plan(LoopState(loop_id="l2", objective="find the HGNC record for TP53",
                         envelope=kernel.policy.envelope()))
    assert "symbol(symbol)" not in served[-1]
