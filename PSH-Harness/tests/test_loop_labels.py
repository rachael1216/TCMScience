"""Labels travel across every edge of the agent loop, or the loop launders.

Found by an adversarial review of the v0.6–v0.9 runtime after it was written. Two of its
findings survived false-positive filtering, and both were reproduced before being fixed:

**Laundering across loop tasks.** The loop recorded the broker's label on every task result
and then never consulted it. The next model task's evidence item was built with the default
label (PUBLIC); the next tool task's payload carried the bare value, which ingress
re-classified from its text — so a count derived from a PHI cohort read INTERNAL. PHI a tool
had returned reached a public provider one hop later, under a PUBLIC verdict, while
``LoopResult.label`` correctly said PHI. The kernel knew and the gate was never told.

**The objective and planner feedback were never classified.** ``Runner.run`` classifies its
request at stage 1 and refuses a projection above the run ceiling before any model call. The
loop and the planner built their turn and instruction items from the objective, from task
objectives, and from evaluator failures that quote task results, all with the default
label, and the broker took a ``ContextProjection``'s label on trust.

The closure has two layers, and the tests below check both. Labels now travel: with
results into the next task's projection and payload, with the objective onto every item
built from it, with the plan onto every task objective a model wrote, with feedback onto
what it quotes, and with a delegation onto the child's objective. And the broker no longer
trusts a projection: it classifies the rendered text and joins, so a projection can be
escalated at that boundary and never trusted downward — and both gateways enforce the run's
ceiling, which only ``Runner._preflight`` had compared before.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import sys
from pathlib import Path

import pytest

import psh.runtime as runtime_pkg
from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, ComponentKind, ComponentManifest, ContextItem, ContextProjection,
    DelegationContract, EgressDenied, ModelProfile, PolicyDenied, RiskTier,
)
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Labeled, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, Checkpoint, CheckpointStore, Criterion, ExecutionGraph, LoopState,
    ModelPlanner, Plan, PlanTask, ResumeRefused, StaticPlanner, TaskKind, TaskState,
    Termination, TestSpec, capture, resume,
)
from psh.runtime.subagent import LocalSubagentBackend

MRN = "04851923"
PHI_TEXT = f"Patient Alice Smith MRN {MRN} admitted with chest pain"
LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
OPEN = LOCAL + (Destination.PUBLIC_REMOTE,)


def policy(**kw):
    params = dict(profile_id="label_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=OPEN, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


def kernel_with(tmp_path, name="k", **kw):
    return TrustedKernel(PSHConfig(state_dir=tmp_path / name).ensure_dirs(),
                         policy=policy(**kw))


@pytest.fixture
def kernel(tmp_path):
    k = kernel_with(tmp_path)
    yield k
    k.close()


@pytest.fixture
def public_model():
    """A public provider that may lawfully see de-identified research data and no more."""
    return ModelProfile(id="gpt-public", provider="public",
                        destination=Destination.PUBLIC_REMOTE,
                        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
                        usd_per_1k_input=0.0, usd_per_1k_output=0.0)


@pytest.fixture
def local_model():
    return ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                        max_label=Sensitivity.PHI, usd_per_1k_input=0.0,
                        usd_per_1k_output=0.0)


class Tool:
    def __init__(self, component_id, result, **manifest_kw):
        params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI, risk_tier=RiskTier.R1_ROUTINE)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.calls: list = []
        self._result = result

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        return self._result


def plan_of(*tasks, objective="do the thing"):
    return Plan(objective=objective, tasks=tuple(tasks),
                completion_criteria=(Criterion(description="done"),), produced_by="test")


def public_decisions(kernel):
    return [(d.allowed, d.label.sensitivity.name) for d in kernel.model_gateway.decisions
            if d.destination is Destination.PUBLIC_REMOTE]


# ============================================ finding 1: laundering across tasks

def test_a_phi_tool_result_does_not_reach_a_public_model_as_evidence(kernel, public_model):
    """The reproduction. Before: goal_satisfied, the prompt carried the MRN, and the gate
    recorded ``allowed=True`` at PUBLIC_REMOTE with label PUBLIC."""
    registry = CapabilityRegistry()
    registry.register(Tool("chart_reader", PHI_TEXT))
    prompts: list[str] = []
    plan = plan_of(
        PlanTask(task_id="read", objective="read the chart", kind=TaskKind.TOOL,
                 component_id="chart_reader", max_label=Sensitivity.PHI),
        PlanTask(task_id="summ", objective="summarise the chart", kind=TaskKind.MODEL,
                 dependencies=("read",), destinations=(Destination.PUBLIC_REMOTE,)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               model=public_model,
                               model_invoke=lambda p: prompts.append(p) or "ok",
                               sleep=lambda s: None)
    result = loop.run("summarise", kernel.policy.envelope())

    assert result.termination is not Termination.GOAL_SATISFIED
    assert prompts == [], "the public provider must never see the tool's result"
    assert not any(ok for ok, _ in public_decisions(kernel))
    assert kernel.model_gateway.decisions == [] or all(
        not d.allowed for d in kernel.model_gateway.decisions)
    assert result.results.get("read") == PHI_TEXT          # the tool itself was fine
    assert result.label.sensitivity is Sensitivity.PHI


def test_a_task_is_refused_rather_than_run_without_its_input(kernel, public_model):
    """The compiler may drop evidence a destination cannot receive. In a loop the upstream
    result IS the task's input, so dropping it would answer a different question."""
    registry = CapabilityRegistry()
    registry.register(Tool("chart_reader", PHI_TEXT))
    plan = plan_of(
        PlanTask(task_id="read", objective="read", kind=TaskKind.TOOL,
                 component_id="chart_reader", max_label=Sensitivity.PHI),
        PlanTask(task_id="summ", objective="summarise", kind=TaskKind.MODEL,
                 dependencies=("read",), destinations=(Destination.PUBLIC_REMOTE,)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               model=public_model, model_invoke=lambda p: "ok",
                               sleep=lambda s: None)
    result = loop.run("summarise", kernel.policy.envelope())
    assert result.state_counts.get("failed", 0) >= 1
    refused = [e for e in kernel.events.records() if e.event_type == "loop_task_refused"]
    assert refused, "the refusal must be an audited task refusal, not a quiet drop"
    assert refused[-1].detail.get("error_type") == "EgressDenied"


def test_a_count_derived_from_phi_carries_phi_to_the_next_tool(kernel):
    """The tool variant. ``{"readmissions": 3}`` contains no identifier; its label does.
    Before: ingress re-classified the bare value as INTERNAL and the uploader ran."""
    registry = CapabilityRegistry()
    registry.register(Tool("cohort_count", {"readmissions": 3, "cohort": 12}))
    uploader = Tool("public_uploader", {"uploaded": True},
                    destinations=(Destination.PUBLIC_REMOTE,),
                    max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    registry.register(uploader)
    plan = plan_of(
        PlanTask(task_id="a", objective="count", kind=TaskKind.TOOL,
                 component_id="cohort_count", payload={"chart": PHI_TEXT},
                 max_label=Sensitivity.PHI),
        PlanTask(task_id="b", objective="upload", kind=TaskKind.TOOL,
                 component_id="public_uploader", dependencies=("a",),
                 destinations=(Destination.PUBLIC_REMOTE,)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               sleep=lambda s: None)
    result = loop.run("count and upload", kernel.policy.envelope())

    assert uploader.calls == [], "a PHI-derived count reached a public destination"
    upload_decisions = [d for d in kernel.tool_gateway.decisions
                        if d.target == "public_uploader"]
    assert upload_decisions and all(not d.allowed for d in upload_decisions)
    assert all(d.label.sensitivity is Sensitivity.PHI for d in upload_decisions), (
        "the gate must rule on the derived label, not on the text")
    assert result.results.get("a") == {"readmissions": 3, "cohort": 12}


def test_a_tool_receives_bare_values_even_though_the_payload_carried_labels(kernel):
    """Carrying labels must not change what a component sees."""
    registry = CapabilityRegistry()
    registry.register(Tool("first", {"n": 1}))
    second = Tool("second", {"ok": True})
    registry.register(second)
    plan = plan_of(
        PlanTask(task_id="a", objective="one", kind=TaskKind.TOOL, component_id="first"),
        PlanTask(task_id="b", objective="two", kind=TaskKind.TOOL, component_id="second",
                 dependencies=("a",)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               sleep=lambda s: None)
    result = loop.run("chain", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert second.calls[0]["upstream"] == {"a": {"n": 1}}
    assert not isinstance(second.calls[0]["upstream"]["a"], Labeled)


def test_a_model_result_is_labelled_with_what_the_model_saw(kernel, local_model):
    """A local model may see PHI. Its answer is then PHI whatever it says."""
    registry = CapabilityRegistry()
    registry.register(Tool("chart_reader", PHI_TEXT))
    plan = plan_of(
        PlanTask(task_id="read", objective="read", kind=TaskKind.TOOL,
                 component_id="chart_reader", max_label=Sensitivity.PHI),
        PlanTask(task_id="summ", objective="summarise", kind=TaskKind.MODEL,
                 dependencies=("read",), destinations=(Destination.LOCAL_MODEL,),
                 max_label=Sensitivity.PHI))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               model=local_model, model_invoke=lambda p: "the patient is stable",
                               sleep=lambda s: None)
    result = loop.run("summarise", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert result.label.sensitivity is Sensitivity.PHI
    # And the broker's own record of the call says PHI, not PUBLIC.
    assert [d.label.sensitivity.name for d in kernel.model_gateway.decisions] == ["PHI"]


# ============================================ finding 2: unclassified objectives

def test_a_phi_objective_never_reaches_a_public_planner(tmp_path, public_model):
    """The reproduction. Before: the planner's prompt carried the MRN and the gate recorded
    ``allowed=True`` at PUBLIC_REMOTE with label PUBLIC, while the classifier said PHI."""
    kernel = kernel_with(tmp_path, max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    served: list[str] = []
    planner = ModelPlanner(kernel, model=public_model,
                           model_invoke=lambda p: served.append(p) or "{}")
    state = LoopState(loop_id="l", objective=f"Summarise the chart of {PHI_TEXT}",
                      envelope=kernel.policy.envelope())
    with pytest.raises(EgressDenied, match="ceiling|PHI"):
        planner.plan(state)
    assert served == []
    assert state.objective_label.sensitivity is Sensitivity.PHI
    assert public_decisions(kernel) and not any(ok for ok, _ in public_decisions(kernel))
    kernel.close()


def test_a_phi_task_objective_never_reaches_a_public_model(tmp_path, public_model):
    """The loop's own instruction item, same defect."""
    kernel = kernel_with(tmp_path, max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    prompts: list[str] = []
    plan = plan_of(PlanTask(task_id="t", objective=f"Summarise: {PHI_TEXT}",
                            kind=TaskKind.MODEL, destinations=(Destination.PUBLIC_REMOTE,)))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), model=public_model,
                               model_invoke=lambda p: prompts.append(p) or "ok",
                               sleep=lambda s: None)
    result = loop.run("x", kernel.policy.envelope())
    assert prompts == []
    assert result.termination is not Termination.GOAL_SATISFIED
    assert public_decisions(kernel) and not any(ok for ok, _ in public_decisions(kernel))
    kernel.close()


def test_feedback_that_quotes_a_phi_result_is_labelled_phi(kernel, public_model):
    """Evaluator failures embed result values. A replan quotes them back to the planner,
    whose model is public here — so the correction must carry the results' label."""
    registry = CapabilityRegistry()
    registry.register(Tool("chart_reader", PHI_TEXT, max_label=Sensitivity.PHI))
    served: list[str] = []
    first = json.dumps({
        "objective": "check the chart", "tasks": [{
            "task_id": "read", "objective": "read the chart", "kind": "tool",
            "component_id": "chart_reader", "max_label": "PHI",
            "acceptance_tests": [{"kind": "output_schema",
                                  "detail": {"schema": {"enum": ["stable", "unstable"]}}}]}],
        "completion_criteria": [{"description": "a verdict exists", "kind": "task"}]})
    planner = ModelPlanner(kernel, model=public_model, registry=registry,
                           model_invoke=lambda p: served.append(p) or first)
    loop = AgentLoopController(kernel, planner=planner, registry=registry,
                               sleep=lambda s: None)
    result = loop.run("check the chart", kernel.policy.envelope())

    assert result.termination is Termination.POLICY_DENIED, result.summary()
    assert len(served) == 1, "the planner ran once; the replan was refused at the gate"
    assert MRN not in served[0]
    refused = [d for d in kernel.model_gateway.decisions if not d.allowed]
    assert refused and refused[-1].label.sensitivity is Sensitivity.PHI


def test_a_plan_a_model_wrote_carries_what_the_planner_saw(kernel, local_model, public_model):
    """The derived case. The objective is PHI; the plan the model returns is clean text;
    a task in it heading for a public provider is still refused, because a plan derived
    from a PHI prompt is PHI whatever it says."""
    served: list[str] = []
    clean_plan = json.dumps({
        "objective": "write up", "tasks": [{
            "task_id": "write", "objective": "write two sentences about study design",
            "kind": "model", "destinations": ["PUBLIC_REMOTE"]}],
        "completion_criteria": [{"description": "text exists", "kind": "task"}]})
    planner = ModelPlanner(kernel, model=local_model,
                           model_invoke=lambda p: served.append(p) or clean_plan)
    state = LoopState(loop_id="l", objective=f"Plan a write-up for {PHI_TEXT}",
                      envelope=kernel.policy.envelope())
    plan = planner.plan(state)
    assert state.plan_label.sensitivity is Sensitivity.PHI

    prompts: list[str] = []
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), model=public_model,
                               model_invoke=lambda p: prompts.append(p) or "ok",
                               sleep=lambda s: None)
    loop_state = LoopState(loop_id="l2", objective=state.objective,
                           envelope=kernel.policy.envelope(), plan_label=state.plan_label)
    result = loop.run(state.objective, kernel.policy.envelope(), resume_from=loop_state)
    assert prompts == []
    assert result.termination is not Termination.GOAL_SATISFIED


def test_a_delegated_objective_carries_the_plan_label_to_the_child(kernel, public_model):
    """A parent hands a child an objective derived from PHI. The child's own classification
    of the clean text says INTERNAL; the contract's projection label says PHI; the child
    starts from the join — so its planner, a public model here, is refused."""
    served: list[str] = []
    child_plan = json.dumps({
        "objective": "greet", "tasks": [{"task_id": "t", "objective": "say hello",
                                         "kind": "model", "destinations": ["PUBLIC_REMOTE"]}],
        "completion_criteria": [{"description": "greeted", "kind": "task"}]})
    backend = LocalSubagentBackend(
        kernel, model=public_model, model_invoke=lambda p: served.append(p) or child_plan,
        planner_factory=lambda c: ModelPlanner(
            kernel, model=public_model, model_invoke=lambda p: served.append(p) or child_plan),
        sleep=lambda s: None)
    envelope = kernel.policy.envelope().restrict(task_id="child")
    contract = DelegationContract(
        task_id="child", objective="write a greeting", envelope=envelope,
        projection=ContextProjection(items=(), label=DataLabel(Sensitivity.PHI)))
    result = backend(contract)
    assert served == []
    assert result.termination == Termination.POLICY_DENIED.value
    assert result.label.sensitivity is Sensitivity.PHI

    # And with no label handed over, the same clean objective plans and runs.
    plain = DelegationContract(task_id="child2", objective="write a greeting",
                               envelope=kernel.policy.envelope().restrict(task_id="child2"))
    result = backend(plain)
    assert result.termination == Termination.GOAL_SATISFIED.value
    assert len(served) == 2                                 # one plan, one task


# ============================================ the kernel no longer trusts a projection

def test_the_broker_reclassifies_a_projection_it_is_handed(kernel, public_model):
    """A projection built from unlabelled items says PUBLIC. The rendered text is what
    will be sent; the broker classifies it and joins. Escalated, never trusted downward."""
    projection = ContextProjection(items=(ContextItem(kind="turn", content=PHI_TEXT),),
                                   label=DataLabel())
    assert projection.label.sensitivity is Sensitivity.PUBLIC
    sent: list[str] = []
    with pytest.raises(EgressDenied):
        kernel.broker.call_model(projection, public_model, kernel.policy.envelope(),
                                 invoke=lambda p: sent.append(p) or "ok")
    assert sent == []
    assert kernel.model_gateway.decisions[-1].label.sensitivity is Sensitivity.PHI


def test_a_correctly_labelled_projection_is_unchanged(kernel, local_model):
    projection = ContextProjection(
        items=(ContextItem(kind="turn", content=PHI_TEXT, label=DataLabel(Sensitivity.PHI)),),
        label=DataLabel(Sensitivity.PHI))
    call = kernel.broker.call_model(projection, local_model, kernel.policy.envelope(),
                                    invoke=lambda p: "fine")
    assert call.label.sensitivity is Sensitivity.PHI
    assert kernel.model_gateway.decisions[-1].allowed


def test_a_model_result_carries_the_label_the_gate_ruled_on(kernel, local_model):
    call = kernel.broker.call_model(Labeled(PHI_TEXT, DataLabel(Sensitivity.PHI)),
                                    local_model, kernel.policy.envelope(),
                                    invoke=lambda p: "stable")
    assert call.label.sensitivity is Sensitivity.PHI


def test_the_model_gateway_enforces_the_run_ceiling(tmp_path, local_model):
    """Only ``Runner._preflight`` compared context with the run's ceiling. The gate does."""
    kernel = kernel_with(tmp_path, allowed_destinations=LOCAL)
    narrow = kernel.policy.envelope(max_label=Sensitivity.INTERNAL)
    with pytest.raises(EgressDenied, match="ceiling"):
        kernel.broker.call_model(
            Labeled("de-identified cohort table", DataLabel(Sensitivity.RESEARCH_DEIDENTIFIED)),
            local_model, narrow, invoke=lambda p: "ok")
    kernel.close()


def test_the_tool_gateway_enforces_the_run_ceiling(tmp_path):
    kernel = kernel_with(tmp_path, allowed_destinations=LOCAL)
    tool = Tool("local", {"ok": True})
    narrow = kernel.policy.envelope(max_label=Sensitivity.INTERNAL)
    with pytest.raises(EgressDenied, match="ceiling"):
        kernel.broker.call_tool(
            tool, Labeled({"q": "x"}, DataLabel(Sensitivity.RESEARCH_DEIDENTIFIED)), narrow)
    assert tool.calls == []
    kernel.close()


# ============================================ checkpoints keep, and obey, the labels

def _loop_with_checkpoints(kernel, tmp_path, tool_result, **policy_kw):
    registry = CapabilityRegistry()
    registry.register(Tool("reader", tool_result))
    plan = plan_of(PlanTask(task_id="read", objective="read", kind=TaskKind.TOOL,
                            component_id="reader", max_label=Sensitivity.PHI))
    store = CheckpointStore(tmp_path / "ckpt")
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               checkpoints=store, sleep=lambda s: None)
    return loop, store


def test_a_checkpoint_stores_labels_and_a_resume_restores_them(kernel, tmp_path):
    loop, store = _loop_with_checkpoints(kernel, tmp_path, PHI_TEXT)
    result = loop.run("read", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.labels["read"]["sensitivity"] == "PHI"
    assert checkpoint.objective_label is not None

    state = resume(checkpoint, kernel)
    assert state.graph.nodes["read"].label.sensitivity is Sensitivity.PHI
    assert state.objective_label is not None


def test_a_restored_result_is_escalated_by_its_text_never_lowered_by_the_file(kernel, tmp_path):
    loop, store = _loop_with_checkpoints(kernel, tmp_path, PHI_TEXT)
    result = loop.run("read", kernel.policy.envelope())
    path = store.path_for(store.latest_for(result.loop_id).checkpoint_id)
    raw = json.loads(path.read_text())
    raw["labels"]["read"] = {"sensitivity": "PUBLIC", "categories": []}
    # An editor who lowers the stored label and recomputes the hash gains nothing: the
    # result's own text is classified again on resume.
    body = {k: v for k, v in raw.items() if k != "hash"}
    from psh.contracts import content_hash
    raw["hash"] = content_hash(body)
    path.write_text(json.dumps(raw))
    state = resume(store.load(raw["checkpoint_id"]), kernel)
    assert state.graph.nodes["read"].label.sensitivity is Sensitivity.PHI


def test_a_result_above_the_persistence_ceiling_is_withheld(kernel):
    plan = plan_of(PlanTask(task_id="t", objective="o", kind=TaskKind.TOOL,
                            component_id="x", max_label=Sensitivity.PHI))
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope(),
                      plan=plan, graph=ExecutionGraph(plan))
    state.graph.mark_running("t", at=1.0)
    state.graph.mark_succeeded("t", PHI_TEXT, at=2.0, label=DataLabel(Sensitivity.PHI))

    withheld = capture(state, ceiling=Sensitivity.INTERNAL)
    assert "t" not in withheld.results
    assert "ceiling" in withheld.withheld["t"]
    assert MRN not in json.dumps(withheld.to_dict())

    kept = capture(state, ceiling=Sensitivity.PHI)
    assert kept.results["t"] == PHI_TEXT

    resumed = resume(withheld, kernel)
    node = resumed.graph.nodes["t"]
    assert node.state is TaskState.RETRYABLE
    assert "withheld" in node.error


def test_an_unlabelled_result_is_withheld_not_assumed_public(kernel):
    plan = plan_of(PlanTask(task_id="t", objective="o", kind=TaskKind.TOOL, component_id="x"))
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope(),
                      plan=plan, graph=ExecutionGraph(plan))
    state.graph.mark_running("t", at=1.0)
    state.graph.mark_succeeded("t", {"n": 1}, at=2.0)                # no label
    assert "t" in capture(state).withheld


def test_a_run_that_may_not_persist_checkpoints_no_results(tmp_path):
    """peer_review removes PERSISTENT so review material is not retained. A loop with a
    checkpoint store under such a run must not retain it either."""
    kernel = kernel_with(tmp_path, allowed_destinations=(
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT))
    loop, store = _loop_with_checkpoints(kernel, tmp_path, {"finding": "clean"})
    result = loop.run("read", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.results == {}
    assert "PERSISTENT" in checkpoint.withheld["read"]
    assert any(e.event_type == "loop_checkpoint_withheld" for e in kernel.events.records())
    kernel.close()


def test_a_checkpoint_without_a_hash_is_refused(kernel, tmp_path):
    loop, store = _loop_with_checkpoints(kernel, tmp_path, {"n": 1})
    result = loop.run("read", kernel.policy.envelope())
    checkpoint = store.latest_for(result.loop_id)
    path = store.path_for(checkpoint.checkpoint_id)
    raw = json.loads(path.read_text())
    del raw["hash"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ResumeRefused, match="no hash"):
        store.load(checkpoint.checkpoint_id)
    with pytest.raises(ResumeRefused, match="no hash"):
        Checkpoint.from_dict(raw)
    assert store.latest_for(result.loop_id) is None      # not listed either


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions")
def test_checkpoint_files_are_owner_only(kernel, tmp_path):
    loop, store = _loop_with_checkpoints(kernel, tmp_path, {"n": 1})
    result = loop.run("read", kernel.policy.envelope())
    path = store.path_for(store.latest_for(result.loop_id).checkpoint_id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ============================================ structural: every item states its label

def test_every_context_item_the_runtime_builds_states_its_label():
    """``ContextItem.label`` defaults to PUBLIC. A call in the runtime that omits it is the
    defect this file exists for, so the omission itself is refused."""
    root = Path(runtime_pkg.__file__).parent
    offenders: list[str] = []
    for source in sorted(root.glob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if name != "ContextItem":
                continue
            if not any(kw.arg == "label" for kw in node.keywords):
                offenders.append(f"{source.name}:{node.lineno}")
    assert offenders == [], f"ContextItem built without a label: {offenders}"


def test_the_loop_still_acts_only_through_the_broker(kernel, local_model):
    """The fix added classification calls; it must not have added a fourth way to act."""
    registry = CapabilityRegistry()
    tool = Tool("summarise", {"ok": True})
    registry.register(tool)
    plan = plan_of(
        PlanTask(task_id="t1", objective="call the model", kind=TaskKind.MODEL,
                 destinations=(Destination.LOCAL_MODEL,)),
        PlanTask(task_id="t2", objective="call the tool", kind=TaskKind.TOOL,
                 component_id="summarise", dependencies=("t1",)),
        PlanTask(task_id="t3", objective="delegate", kind=TaskKind.DELEGATE,
                 dependencies=("t2",)))
    delegated: list = []
    before = kernel.broker.stats()
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                               model=local_model, model_invoke=lambda p: "answer",
                               delegate_backend=lambda c: delegated.append(c) or {"ok": True},
                               sleep=lambda s: None)
    result = loop.run("do the thing", kernel.policy.envelope())
    after = kernel.broker.stats()
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert after["model_calls"] - before["model_calls"] == 1
    assert after["tool_calls"] - before["tool_calls"] == 1
    assert after["delegations"] - before["delegations"] == 1
    assert delegated[0].projection is not None and delegated[0].projection.items == ()
