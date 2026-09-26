from dataclasses import replace

import pytest

from psh.capabilities import CapabilityRegistry
from psh.labels import Destination as D, Sensitivity as S
from psh.runtime import PlanRejected
from psh.workflow import (Branch, DynamicWorkflow, WorkflowStage, DynamicWorkflowController,
                          RunEventJournal, SideEffect, Effect)
from psh.workflow.cycles import cyclic_stages
from test_checkpoint import Tool
from test_planner import kernel
from test_scientific_workflow import task, program, contract


def tool_stage(name="a", *, next_stage=None, branch=None, effect=SideEffect.IDEMPOTENT):
    p = program([task("work", kind="tool", component_id=name,
        max_label=S.RESEARCH_DEIDENTIFIED, destinations=(D.LOCAL_COMPUTE,))],
        {"work": contract(effects=(Effect.LOCAL_COMPUTE,), side_effect=effect)})
    p = replace(p, plan=replace(p.plan, plan_id="cycle_fixture"))
    return WorkflowStage(name, p, next_stage, branch)


@pytest.mark.parametrize("effect", [SideEffect.NON_REPEATABLE, SideEffect.AT_MOST_ONCE,
                                     SideEffect.COMPENSATABLE])
def test_contract_refuses_cycle_before_any_dispatch(kernel, tmp_path, effect):
    tool = Tool("a", idempotent=True)
    registry = CapabilityRegistry()
    registry.register(tool)
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected) as exc:
            DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
                DynamicWorkflow((tool_stage(next_stage="a", effect=effect),), "a"), kernel.envelope())
        assert "REPEAT101" in {v.family for v in exc.value.violations}
        assert not tool.calls and not journal.events()


@pytest.mark.parametrize("fact", [False, None, "true", 1])
def test_registry_fact_cannot_be_overridden_by_cycle_declaration(kernel, tmp_path, fact):
    tool = Tool("a", idempotent=fact)
    registry = CapabilityRegistry()
    registry.register(tool)
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected) as exc:
            DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
                DynamicWorkflow((tool_stage(next_stage="a"),), "a"), kernel.envelope())
        assert "REPEAT102" in {v.family for v in exc.value.violations}
        assert not tool.calls and not journal.events()


@pytest.mark.parametrize("effect", [SideEffect.PURE, SideEffect.IDEMPOTENT, SideEffect.AT_LEAST_ONCE])
def test_repeat_safe_cycle_still_runs_to_bound(kernel, tmp_path, effect):
    tool = Tool("a", idempotent=True)
    registry = CapabilityRegistry()
    registry.register(tool)
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
            DynamicWorkflow((tool_stage(next_stage="a", effect=effect),), "a", max_visits=3),
            kernel.envelope())
        assert result.state.reason == "max_visits" and len(tool.calls) == 3


def test_acyclic_nonrepeatable_stage_is_still_allowed(kernel, tmp_path):
    tool = Tool("a", idempotent=False)
    registry = CapabilityRegistry()
    registry.register(tool)
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
            DynamicWorkflow((tool_stage(effect=SideEffect.NON_REPEATABLE),), "a"), kernel.envelope())
        assert result.ok and len(tool.calls) == 1


def test_scc_members_not_ancestors_or_descendants():
    flow = DynamicWorkflow((tool_stage("entry", next_stage="a"),
        tool_stage("a", next_stage="b"),
        tool_stage("b", branch=Branch("work", True, "a", "end")),
        tool_stage("end"), tool_stage("unreachable", next_stage="unreachable")), "entry")
    assert cyclic_stages(flow) == frozenset({"a", "b", "unreachable"})


def test_diamond_is_not_a_cycle():
    flow = DynamicWorkflow((tool_stage("entry", branch=Branch("work", True, "a", "b")),
        tool_stage("a", next_stage="end"), tool_stage("b", next_stage="end"),
        tool_stage("end")), "entry")
    assert cyclic_stages(flow) == frozenset()


def test_maximum_size_scc_pass():
    flow = DynamicWorkflow(tuple(tool_stage(str(i), next_stage=str((i + 1) % 256))
                                 for i in range(256)), "0")
    assert len(cyclic_stages(flow)) == 256


def test_multistage_cycle_refuses_nonrepeatable_member(kernel, tmp_path):
    registry = CapabilityRegistry()
    a, b = Tool("a"), Tool("b", idempotent=False)
    registry.register(a)
    registry.register(b)
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected):
            DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
                DynamicWorkflow((tool_stage("a", next_stage="b"),
                    tool_stage("b", next_stage="a", effect=SideEffect.NON_REPEATABLE)), "a"),
                kernel.envelope())
        assert not a.calls and not b.calls and not journal.events()


def test_unreachable_cycle_is_checked_even_with_one_visit_budget(kernel, tmp_path):
    registry = CapabilityRegistry()
    a, b = Tool("a", idempotent=False), Tool("b", idempotent=False)
    registry.register(a)
    registry.register(b)
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected):
            DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
                DynamicWorkflow((tool_stage("a", effect=SideEffect.NON_REPEATABLE),
                    tool_stage("b", next_stage="b", effect=SideEffect.NON_REPEATABLE)), "a", max_visits=1),
                kernel.envelope())
        assert not a.calls and not b.calls


def test_fact_downgrade_between_visits_is_replayed_without_second_dispatch(kernel, tmp_path):
    class Downgrade(Tool):
        def invoke(self, payload, envelope):
            value = super().invoke(payload, envelope)
            self.manifest = replace(self.manifest, idempotent=False)
            return value
    tool = Downgrade("a", idempotent=True)
    registry = CapabilityRegistry()
    registry.register(tool)
    path = tmp_path / "e.db"
    with RunEventJournal(path) as journal:
        result = DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
            DynamicWorkflow((tool_stage(next_stage="a"),), "a"), kernel.envelope())
        assert result.state.reason == "repeat_refused" and result.state.visits == 1
        assert len(tool.calls) == 1
    with RunEventJournal(path) as journal:
        assert journal.replay() == result.state


def test_fact_downgrade_within_stage_is_checked_at_tool_dispatch(kernel, tmp_path):
    second = Tool("second", idempotent=True)
    class DowngradeOther(Tool):
        def invoke(self, payload, envelope):
            value = super().invoke(payload, envelope)
            second.manifest = replace(second.manifest, idempotent=False)
            return value
    first = DowngradeOther("a", idempotent=True)
    registry = CapabilityRegistry()
    registry.register(first)
    registry.register(second)
    original = tool_stage(next_stage="a")
    tasks = original.program.plan.tasks + (replace(original.program.plan.tasks[0],
        task_id="second", component_id="second", dependencies=("work",)),)
    p = replace(original.program, plan=replace(original.program.plan, tasks=tasks),
                contracts={**original.program.contracts, "second": original.program.contracts["work"]})
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
            DynamicWorkflow((replace(original, program=p),), "a"), kernel.envelope())
        assert result.state.reason == "stage_failed"
        assert len(first.calls) == 1 and not second.calls


def test_cycle_delegation_requires_attestation(kernel, tmp_path):
    p = program([task("delegate", kind="delegate", max_label=S.RESEARCH_DEIDENTIFIED)])
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected) as exc:
            DynamicWorkflowController(kernel, journal=journal).run(
                DynamicWorkflow((WorkflowStage("a", p, "a"),), "a"), kernel.envelope())
        assert "REPEAT103" in {v.family for v in exc.value.violations}


def test_repeat_refusal_event_only_valid_at_ready_nonterminal_state():
    from psh.workflow.events import replay, ReplayRefused
    from test_dynamic_workflow import history
    event = dict(kind="ended", reason="repeat_refused")
    assert replay([history()[0], event]).reason == "repeat_refused"
    for prefix in (history()[:2], history()[:3], history()[:4], history()):
        with pytest.raises(ReplayRefused):
            replay(prefix + [event])
