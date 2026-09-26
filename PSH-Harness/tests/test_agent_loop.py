"""The bounded agent loop, and the invariant that it cannot route around the kernel.

Two things are being asserted here, and the second is the important one.

The first is ordinary: the loop plans, executes in dependency order, retries what its
policy says is retryable, replans when a completed plan did not satisfy its criteria, and
stops for a named reason every time.

The second is the reason this component was worth building inside PSH rather than adopting
one. Adding a loop to an agent multiplies whatever its gates get wrong — every defect the
v0.5.1 review found would have fired once per iteration instead of once per run. So the
loop is allowed exactly three ways to cause anything to happen, all of them broker calls,
and ``test_the_loop_cannot_act_except_through_the_broker`` counts them.
"""

from __future__ import annotations

import pytest

from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, ComponentKind, ComponentManifest, ContractViolation, ModelProfile,
    PolicyDenied, RiskTier,
)
from psh.capabilities import CapabilityRegistry
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, Criterion, Evaluator, ExecutionGraph, LoopLimits, Plan,
    PlanRejected, PlanTask, PlanValidator, StaticPlanner, TaskKind, TaskState, Termination,
    TestSpec, task_envelope,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


def policy(**kw):
    params = dict(profile_id="loop_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=LOCAL, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


@pytest.fixture
def local_profile():
    return ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                        max_label=Sensitivity.PHI, usd_per_1k_input=0.0,
                        usd_per_1k_output=0.0)


class Tool:
    """A component that records what it was asked to do."""

    def __init__(self, component_id="probe", result=None, fail_times=0, **manifest_kw):
        params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI, risk_tier=RiskTier.R1_ROUTINE)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.calls: list = []
        self._result = result if result is not None else {"ok": True}
        self._fail_times = fail_times

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        if len(self.calls) <= self._fail_times:
            raise ContractViolation("transient")
        return self._result


def plan_of(*tasks, criteria=("done",), objective="do the thing"):
    return Plan(objective=objective, tasks=tuple(tasks),
                completion_criteria=tuple(Criterion(description=c) for c in criteria),
                produced_by="test")


def controller(kernel, plan, *, registry=None, model=None, invoke=None, limits=None,
               delegate_backend=None, sleeps=None):
    return AgentLoopController(
        kernel, planner=StaticPlanner(plan), registry=registry, model=model,
        model_invoke=invoke, limits=limits or LoopLimits(),
        delegate_backend=delegate_backend,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


# ================================================== the invariant that matters

def test_the_loop_cannot_act_except_through_the_broker(kernel, local_profile):
    """Every action the loop took is accounted for by the broker's own counters.

    The counters are the detection mechanism for "something executed by another route" —
    ``BrokerBypass`` exists for exactly this. If the loop ever grows a fourth way to make
    something happen, the arithmetic below stops balancing.
    """
    registry = CapabilityRegistry()
    tool = Tool("summarise")
    registry.register(tool)
    delegated = []

    plan = plan_of(
        PlanTask(task_id="t1", objective="call the model", kind=TaskKind.MODEL,
                 destinations=(Destination.LOCAL_MODEL,)),
        PlanTask(task_id="t2", objective="call the tool", kind=TaskKind.TOOL,
                 component_id="summarise", dependencies=("t1",)),
        PlanTask(task_id="t3", objective="delegate", kind=TaskKind.DELEGATE,
                 dependencies=("t2",)))

    before = kernel.broker.stats()
    loop = controller(kernel, plan, registry=registry, model=local_profile,
                      invoke=lambda prompt: "a model answer",
                      delegate_backend=lambda c: delegated.append(c) or {"ok": True})
    result = loop.run("do the thing", kernel.policy.envelope())
    after = kernel.broker.stats()

    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert after["model_calls"] - before["model_calls"] == 1
    assert after["tool_calls"] - before["tool_calls"] == 1
    assert after["delegations"] - before["delegations"] == 1
    assert len(tool.calls) == 1 and len(delegated) == 1


def test_a_task_the_gates_refuse_does_not_execute(kernel, local_profile):
    """A refusal inside the loop is a task failure, not a bypass and not a crash."""
    registry = CapabilityRegistry()
    # A component whose ceiling is below what the task hands it.
    tool = Tool("narrow", max_label=Sensitivity.PUBLIC)
    registry.register(tool)

    plan = plan_of(PlanTask(task_id="t1", objective="send PHI to a public-only tool",
                            kind=TaskKind.TOOL, component_id="narrow",
                            max_label=Sensitivity.PHI,
                            payload={"note": "MRN 4472213 John Doe"}))
    loop = controller(kernel, plan, registry=registry, model=local_profile,
                      invoke=lambda p: "x")
    result = loop.run("do it", kernel.policy.envelope())

    assert result.termination is not Termination.GOAL_SATISFIED
    assert tool.calls == [], "a refused task reached the component anyway"
    assert result.state_counts.get("failed") == 1


def test_a_model_task_without_a_model_is_a_contract_violation_not_a_direct_call(kernel):
    """There is no fallback path. A loop with no model cannot make a model call."""
    plan = plan_of(PlanTask(task_id="t1", objective="think", kind=TaskKind.MODEL,
                            destinations=(Destination.LOCAL_MODEL,)))
    result = controller(kernel, plan).run("x", kernel.policy.envelope())
    assert result.termination is Termination.ESCALATED
    assert kernel.broker.stats()["model_calls"] == 0


# ==================================================== authority, one level down

def test_each_task_runs_under_an_envelope_narrower_than_the_run(kernel):
    """Task authority is derived with ``restrict``, so it is the lattice, not a copy."""
    from psh.kernel.authority import AuthorityLattice

    parent = kernel.policy.envelope()
    task = PlanTask(task_id="t", objective="x", max_risk=RiskTier.R0_TRIVIAL,
                    max_label=Sensitivity.PUBLIC, autonomy=Autonomy.OBSERVE,
                    destinations=(Destination.LOCAL_COMPUTE,))
    child = task_envelope(task, parent)

    assert AuthorityLattice.is_subset(child, parent)
    assert child.risk is RiskTier.R0_TRIVIAL
    assert child.max_label.sensitivity is Sensitivity.PUBLIC
    assert child.allowed_destinations == frozenset({Destination.LOCAL_COMPUTE})


def test_a_task_asking_for_more_authority_than_the_run_is_refused_at_the_plan(kernel):
    """Refused at validation, before any task executes — not discovered at step four."""
    narrow = kernel.policy.envelope(risk=RiskTier.R0_TRIVIAL)
    plan = plan_of(PlanTask(task_id="ok", objective="fine", max_risk=RiskTier.R0_TRIVIAL),
                   PlanTask(task_id="greedy", objective="escalate",
                            max_risk=RiskTier.R0_TRIVIAL,
                            destinations=(Destination.PUBLIC_REMOTE,)))
    with pytest.raises(PlanRejected) as exc:
        PlanValidator().validate(plan, narrow)
    assert any(v.task_id == "greedy" for v in exc.value.violations)


def test_a_rejected_plan_terminates_the_loop_without_executing_anything(kernel):
    registry = CapabilityRegistry()
    tool = Tool("t")
    registry.register(tool)
    plan = plan_of(PlanTask(task_id="a", objective="x", kind=TaskKind.TOOL,
                            component_id="t",
                            destinations=(Destination.PUBLIC_REMOTE,)))
    result = controller(kernel, plan, registry=registry).run("x", kernel.policy.envelope())
    assert result.termination is Termination.PLAN_REJECTED
    assert tool.calls == []


# ================================================================ plan validation

def test_a_cycle_is_reported_by_name(kernel):
    plan = plan_of(PlanTask(task_id="a", objective="a", dependencies=("b",)),
                   PlanTask(task_id="b", objective="b", dependencies=("a",)))
    with pytest.raises(PlanRejected) as exc:
        PlanValidator().validate(plan, kernel.policy.envelope())
    detail = " ".join(v.detail for v in exc.value.violations)
    assert "cycle" in detail and "'a'" in detail and "'b'" in detail


def test_a_dangling_dependency_is_reported(kernel):
    plan = plan_of(PlanTask(task_id="a", objective="a", dependencies=("ghost",)))
    with pytest.raises(PlanRejected, match="ghost"):
        PlanValidator().validate(plan, kernel.policy.envelope())


def test_a_plan_that_cannot_fit_the_budget_is_refused_before_it_starts(kernel):
    envelope = kernel.policy.envelope(budget=Budget(tokens_hard=100, usd_hard=0.5))
    plan = plan_of(PlanTask(task_id="a", objective="a", estimated_tokens=10_000,
                            estimated_usd=9.0))
    with pytest.raises(PlanRejected) as exc:
        PlanValidator().validate(plan, envelope)
    families = {v.family for v in exc.value.violations}
    assert "budget" in families


def test_budget_feasibility_uses_the_critical_path_not_the_sum(kernel):
    """Independent tasks do not add wall-clock; charging them for it refuses valid plans."""
    envelope = kernel.policy.envelope(budget=Budget(seconds_hard=100))
    parallel = plan_of(PlanTask(task_id="a", objective="a", estimated_seconds=60),
                       PlanTask(task_id="b", objective="b", estimated_seconds=60))
    PlanValidator().validate(parallel, envelope)          # 120s summed, 60s critical path

    chained = plan_of(PlanTask(task_id="a", objective="a", estimated_seconds=60),
                      PlanTask(task_id="b", objective="b", estimated_seconds=60,
                               dependencies=("a",)))
    with pytest.raises(PlanRejected, match="critical path"):
        PlanValidator().validate(chained, envelope)


def test_an_unknown_acceptance_test_kind_is_a_plan_error(kernel):
    """A test that is skipped for being unrecognised reads exactly like a passed one."""
    plan = plan_of(PlanTask(task_id="a", objective="a",
                            acceptance_tests=(TestSpec(kind="vibes"),)))
    with pytest.raises(PlanRejected, match="vibes"):
        PlanValidator().validate(plan, kernel.policy.envelope())


def test_a_plan_with_no_completion_criteria_is_refused(kernel):
    plan = Plan(objective="x", tasks=(PlanTask(task_id="a", objective="a"),))
    with pytest.raises(PlanRejected, match="definition of done"):
        PlanValidator().validate(plan, kernel.policy.envelope())


def test_a_policy_requiring_claim_support_needs_a_plan_that_produces_evidence(kernel):
    strict = policy(require_claim_support=True)
    plan = plan_of(PlanTask(task_id="a", objective="a"))
    with pytest.raises(PlanRejected, match="evidence"):
        PlanValidator().validate(plan, strict.envelope(), policy=strict)


# ====================================================================== bounds

def test_the_loop_stops_at_its_iteration_ceiling(kernel, local_profile):
    """A plan that never completes must still terminate, and say which bound it hit."""
    never_ready = plan_of(
        PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL,
                 destinations=(Destination.LOCAL_MODEL,)),
        PlanTask(task_id="b", objective="b", dependencies=("a",),
                 kind=TaskKind.MODEL, destinations=(Destination.LOCAL_MODEL,)))

    class NeverSucceeds(Evaluator):
        def evaluate(self, plan, graph, *, supports=(), replans_left=0):
            from psh.runtime import Verdict
            return Verdict(reason="pretending work remains")

    loop = AgentLoopController(kernel, planner=StaticPlanner(never_ready),
                              model=local_profile, model_invoke=lambda p: "x",
                              evaluator=NeverSucceeds(),
                              limits=LoopLimits(max_iterations=3, max_no_progress=0))
    result = loop.run("spin", kernel.policy.envelope())
    assert result.termination is Termination.MAX_ITERATIONS
    assert result.iterations == 3


def test_the_loop_stops_when_it_stops_making_progress(kernel, local_profile):
    """The digest is over achieved state, so re-asking after progress is not 'stuck'."""
    class Stalled(Evaluator):
        def evaluate(self, plan, graph, *, supports=(), replans_left=0):
            from psh.runtime import Verdict
            return Verdict(reason="work remains")

    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL,
                            destinations=(Destination.LOCAL_MODEL,)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), model=local_profile,
                              model_invoke=lambda p: "same", evaluator=Stalled(),
                              limits=LoopLimits(max_iterations=20, max_no_progress=2))
    result = loop.run("spin", kernel.policy.envelope())
    assert result.termination is Termination.NO_PROGRESS
    assert result.iterations < 20


def test_the_loop_stops_on_a_passed_deadline(kernel, local_profile):
    import time as _time
    envelope = kernel.policy.envelope(deadline=_time.time() - 1)
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL,
                            destinations=(Destination.LOCAL_MODEL,)))
    result = controller(kernel, plan, model=local_profile, invoke=lambda p: "x").run(
        "x", envelope)
    assert result.termination is Termination.DEADLINE


def test_a_plan_needing_more_calls_than_the_budget_allows_is_refused(kernel,
                                                                     local_profile):
    """Feasibility is decided at the plan, so an impossible plan costs nothing to reject."""
    envelope = kernel.policy.envelope(budget=Budget(max_model_calls=1))
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL),
                   PlanTask(task_id="b", objective="b", kind=TaskKind.MODEL,
                            dependencies=("a",)))
    result = controller(kernel, plan, model=local_profile,
                        invoke=lambda p: "x").run("x", envelope)
    assert result.termination is Termination.PLAN_REJECTED
    assert kernel.broker.stats()["model_calls"] == 0


def test_the_loop_stops_when_the_budget_is_exhausted_at_runtime(kernel, local_profile):
    """Estimates pass validation; real usage then exceeds the ceiling mid-loop.

    The plan declares no token estimate, so the feasibility check has nothing to object
    to — which is the ordinary case, and the reason a runtime ceiling is still needed on
    top of a static one.
    """
    envelope = kernel.policy.envelope(budget=Budget(tokens_hard=40, max_model_calls=8))
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL),
                   PlanTask(task_id="b", objective="b", kind=TaskKind.MODEL,
                            dependencies=("a",)))
    result = controller(kernel, plan, model=local_profile,
                        invoke=lambda p: "x" * 4000).run("x", envelope)
    assert result.termination is Termination.BUDGET_EXHAUSTED
    assert kernel.broker.stats()["model_calls"] == 1, \
        "the second call must not have been made after the ceiling was reached"


def test_every_termination_reason_is_named_never_inferred(kernel):
    """The enum is the contract: a loop that stops must say which bound stopped it."""
    assert Termination.RUNNING.value == "running"
    reasons = {t for t in Termination} - {Termination.RUNNING}
    assert len(reasons) == 11
    assert Termination.CANCELLED in reasons, \
        "cancellation is a named termination, not an exception thrown across threads"


# ===================================================================== retries

def test_a_retryable_failure_is_retried_within_its_policy(kernel):
    from psh.runtime import RetryPolicy

    registry = CapabilityRegistry()
    tool = Tool("flaky", fail_times=2)
    registry.register(tool)
    sleeps: list = []
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="flaky",
                            retry=RetryPolicy(max_attempts=3, initial_delay_s=0.5)))
    result = controller(kernel, plan, registry=registry, sleeps=sleeps).run(
        "x", kernel.policy.envelope())

    assert result.termination is Termination.GOAL_SATISFIED
    assert len(tool.calls) == 3, "the tool should have been attempted three times"
    assert sleeps == [0.5, 1.0], f"exponential backoff was not applied: {sleeps}"


def test_retries_stop_at_max_attempts(kernel):
    from psh.runtime import RetryPolicy

    registry = CapabilityRegistry()
    tool = Tool("always_fails", fail_times=99)
    registry.register(tool)
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="always_fails",
                            retry=RetryPolicy(max_attempts=2)))
    result = controller(kernel, plan, registry=registry,
                        limits=LoopLimits(max_iterations=6, max_replans=0)).run(
        "x", kernel.policy.envelope())
    assert result.termination is not Termination.GOAL_SATISFIED
    assert len(tool.calls) == 2, "a replan must not silently reset the attempt counter"


def test_a_refusal_is_never_retried(kernel):
    """A policy refusal is an answer, not a transient fault; re-asking burns budget."""
    from psh.runtime import RetryPolicy

    registry = CapabilityRegistry()
    tool = Tool("narrow", max_label=Sensitivity.PUBLIC)
    registry.register(tool)
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="narrow", max_label=Sensitivity.PHI,
                            payload={"note": "MRN 4472213 John Doe"},
                            retry=RetryPolicy(max_attempts=5)))
    controller(kernel, plan, registry=registry).run("x", kernel.policy.envelope())
    assert tool.calls == []


# ===================================================================== replans

def test_a_completed_plan_that_misses_its_criteria_triggers_a_replan(kernel):
    registry = CapabilityRegistry()
    good = Tool("good", result={"evidence": ["PMID 1"]})
    bad = Tool("bad", result={})
    registry.register(good)
    registry.register(bad)

    attempts = {"n": 0}

    def planner_fn(state, feedback):
        attempts["n"] += 1
        component = "bad" if attempts["n"] == 1 else "good"
        return Plan(objective="o", produced_by="test",
                    tasks=(PlanTask(task_id="a", objective="gather", kind=TaskKind.TOOL,
                                    component_id=component, evidence_required=True,
                                    output_schema={"type": "object"}),),
                    completion_criteria=(Criterion(description="evidence gathered",
                                                   kind="evidence"),))

    loop = AgentLoopController(kernel, planner=StaticPlanner(planner_fn), registry=registry,
                               limits=LoopLimits(max_iterations=8, max_replans=2))
    result = loop.run("gather evidence", kernel.policy.envelope())

    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert result.replans == 1
    assert attempts["n"] == 2


def test_replans_are_bounded(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("bad", result={}))
    plan = Plan(objective="o", produced_by="test",
                tasks=(PlanTask(task_id="a", objective="gather", kind=TaskKind.TOOL,
                                component_id="bad", evidence_required=True,
                                output_schema={"type": "object"}),),
                completion_criteria=(Criterion(description="evidence", kind="evidence"),))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               limits=LoopLimits(max_iterations=20, max_replans=1,
                                                 max_no_progress=0))
    result = loop.run("x", kernel.policy.envelope())
    assert result.termination is Termination.ESCALATED
    assert result.replans <= 1


# ================================================================== evaluation

def test_a_task_whose_output_breaks_its_schema_fails_structurally(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("wrong", result={"unexpected": 1}))
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="wrong",
                            output_schema={"type": "object", "required": ["answer"]}))
    result = controller(kernel, plan, registry=registry,
                        limits=LoopLimits(max_iterations=4, max_replans=0)).run(
        "x", kernel.policy.envelope())
    assert result.termination is Termination.ESCALATED
    assert any("output schema" in f for f in result.failures), result.failures


def test_acceptance_tests_actually_run(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("terse", result="short"))
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="terse",
                            acceptance_tests=(TestSpec(kind="citations_present"),)))
    result = controller(kernel, plan, registry=registry,
                        limits=LoopLimits(max_iterations=4, max_replans=0)).run(
        "x", kernel.policy.envelope())
    assert any("citations_present" in f for f in result.failures), result.failures


def test_a_blocked_downstream_task_is_named_as_blocked_not_as_out_of_iterations(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("fails", fail_times=99))
    registry.register(Tool("never_runs"))
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="fails"),
                   PlanTask(task_id="b", objective="b", kind=TaskKind.TOOL,
                            component_id="never_runs", dependencies=("a",)))
    result = controller(kernel, plan, registry=registry,
                        limits=LoopLimits(max_iterations=6, max_replans=0)).run(
        "x", kernel.policy.envelope())
    assert result.state_counts.get("blocked") == 1
    assert any("blocked" in f for f in result.failures), result.failures


def test_tasks_execute_in_dependency_order(kernel):
    registry = CapabilityRegistry()
    order: list[str] = []

    class Ordered(Tool):
        def invoke(self, payload, envelope):
            order.append(self.manifest.id)
            return {"ok": True}

    for name in ("first", "second", "third"):
        registry.register(Ordered(name))
    plan = plan_of(
        PlanTask(task_id="c", objective="c", kind=TaskKind.TOOL, component_id="third",
                 dependencies=("b",)),
        PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL, component_id="first"),
        PlanTask(task_id="b", objective="b", kind=TaskKind.TOOL, component_id="second",
                 dependencies=("a",)))
    result = controller(kernel, plan, registry=registry).run("x", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED
    assert order == ["first", "second", "third"]


def test_a_task_receives_its_dependencies_results(kernel):
    registry = CapabilityRegistry()
    registry.register(Tool("producer", result={"value": 42}))
    consumer = Tool("consumer")
    registry.register(consumer)
    plan = plan_of(PlanTask(task_id="a", objective="a", kind=TaskKind.TOOL,
                            component_id="producer"),
                   PlanTask(task_id="b", objective="b", kind=TaskKind.TOOL,
                            component_id="consumer", dependencies=("a",)))
    controller(kernel, plan, registry=registry).run("x", kernel.policy.envelope())
    assert consumer.calls and "upstream" in consumer.calls[0]


def test_each_task_gets_its_own_projection_not_a_shared_transcript(kernel, local_profile):
    """A shared history is how a PHI tool result reaches a later step's prompt."""
    prompts: list[str] = []
    plan = plan_of(PlanTask(task_id="a", objective="FIRST OBJECTIVE", kind=TaskKind.MODEL,
                            destinations=(Destination.LOCAL_MODEL,)),
                   PlanTask(task_id="b", objective="SECOND OBJECTIVE", kind=TaskKind.MODEL,
                            destinations=(Destination.LOCAL_MODEL,),
                            dependencies=("a",)))
    controller(kernel, plan, model=local_profile,
               invoke=lambda p: prompts.append(p) or "answer").run(
        "x", kernel.policy.envelope())
    assert len(prompts) == 2
    assert "FIRST OBJECTIVE" in prompts[0] and "SECOND OBJECTIVE" not in prompts[0]


# ============================ the loop's containment, checked structurally

def test_the_loop_module_holds_no_route_to_the_outside_world():
    """Counting broker calls proves what a loop *did*; this proves what it *can* do.

    A behavioural test sees the paths the test exercised. The concern here is a path that
    exists and no test happens to take — which is precisely how ``IsolatedRunner`` shipped
    in v0.4 while ``call_tool`` ran everything in process. So this reads the module: if the
    loop ever imports a transport or a process spawner, it fails here, before anyone has to
    notice the counters stopped balancing.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "src" / "psh" / "runtime" / "loop.py"
    tree = ast.parse(source.read_text())

    forbidden = {"subprocess", "socket", "http", "urllib", "requests", "httpx", "ssl",
                 "asyncio", "multiprocessing", "os"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    leaks = imported & forbidden
    assert not leaks, (
        f"{source.name} imports {sorted(leaks)}. The loop reaches the world through "
        "ExecutionBroker and nothing else; an import of a transport or a process spawner "
        "is a second execution path whether or not any test takes it.")


def test_the_loop_dispatches_to_exactly_three_broker_methods():
    """The three routes are enumerated in one place, so a fourth is visible."""
    import ast
    import textwrap
    import inspect

    from psh.runtime import loop as loop_module

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(loop_module.AgentLoopController._dispatch)))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "broker"}
    assert called == {"call_model", "call_tool", "delegate"}, called


# ================== a task's authority: self-imposed ceilings vs required reach

def test_a_task_ceiling_meets_the_run_rather_than_being_refused(kernel):
    """``max_risk`` / ``max_label`` / ``autonomy`` are ceilings the task sets on itself.

    Their names say so — "this task incurs at most R2", "handles at most PHI" — so the
    effective value is the lower of the task's and the run's. Refusing instead would mean a
    task carrying the *default* ``max_label`` could not run under a narrower policy: a
    default that is not the weakest statement of its field, which is the
    ``min_autonomy = ACT`` mistake in another costume.

    This was inconsistent when first written: ``risk`` was met with ``min()`` while
    ``max_label`` and ``autonomy`` were passed through unclamped, so the ceiling applied on
    one dimension and refused on the next.
    """
    narrow = kernel.policy.envelope(risk=RiskTier.R0_TRIVIAL,
                                    max_label=Sensitivity.PUBLIC,
                                    autonomy=Autonomy.OBSERVE)
    greedy = PlanTask(task_id="t", objective="o", max_risk=RiskTier.R3_CLINICAL,
                      max_label=Sensitivity.PHI, autonomy=Autonomy.ACT)

    child = task_envelope(greedy, narrow)

    assert child.risk is RiskTier.R0_TRIVIAL
    assert child.max_label.sensitivity is Sensitivity.PUBLIC
    assert child.autonomy is Autonomy.OBSERVE


def test_a_task_that_needs_reach_the_run_lacks_is_refused(kernel):
    """``destinations`` and ``capability_requirements`` are requirements, not ceilings.

    A task naming ``PUBLIC_REMOTE`` is saying it must get there. Silently narrowing that to
    nothing would hand it an envelope forbidding the very call it exists to make, so it is
    refused at the plan instead.
    """
    local_only = kernel.policy.envelope()
    reaching = PlanTask(task_id="t", objective="o",
                        destinations=(Destination.PUBLIC_REMOTE,))
    with pytest.raises(PolicyDenied, match="destinations"):
        task_envelope(reaching, local_only)


def test_the_two_kinds_are_not_confused_in_one_task(kernel):
    """A task may state a ceiling below the run's and a reach the run holds."""
    envelope = kernel.policy.envelope()
    task = PlanTask(task_id="t", objective="o", max_label=Sensitivity.PUBLIC,
                    max_risk=RiskTier.R0_TRIVIAL,
                    destinations=(Destination.LOCAL_MODEL,))
    child = task_envelope(task, envelope)
    assert child.max_label.sensitivity is Sensitivity.PUBLIC
    assert child.allowed_destinations == frozenset({Destination.LOCAL_MODEL})
