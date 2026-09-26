from dataclasses import replace

import pytest

from psh.capabilities import CapabilityRegistry
from psh.labels import Destination as D, Sensitivity as S
from psh.runtime import AgentLoopController, LoopLimits, StaticPlanner, PlanRejected, OperationLedger
from psh.runtime.plan import RetryPolicy
from psh.workflow import (ScientificCompiler, ScientificPlanner, SideEffect, Effect,
                          DynamicWorkflow, WorkflowStage, DynamicWorkflowController, RunEventJournal)
from test_planner import kernel
from test_checkpoint import Tool
from test_scientific_workflow import task, contract, program


def setup_case(*, idempotent=False, effect=SideEffect.PURE, attempts=2):
    class SideEffectTool(Tool):
        def invoke(self, payload, envelope):
            self.calls.append(payload)
            if len(self.calls) == 1:
                raise OSError("effect may already have happened")
            return {"ok": True}
    tool = SideEffectTool("submit", idempotent=idempotent)
    registry = CapabilityRegistry()
    registry.register(tool)
    p = program([task("submit", kind="tool", component_id="submit", max_label=S.RESEARCH_DEIDENTIFIED,
        destinations=(D.LOCAL_COMPUTE,), retry=RetryPolicy(max_attempts=attempts))],
        {"submit": contract(effects=(Effect.LOCAL_COMPUTE,), side_effect=effect)})
    p = replace(p, plan=replace(p.plan, plan_id="retry_fixture"))
    return tool, registry, p


@pytest.mark.parametrize("effect", [SideEffect.PURE, SideEffect.IDEMPOTENT])
def test_false_repeat_safe_claim_is_rejected_even_without_retry(kernel, effect):
    tool, registry, p = setup_case(effect=effect, attempts=1)
    with pytest.raises(PlanRejected, match="EFFECT106"):
        ScientificCompiler(registry).compile(p, kernel.envelope(), policy=kernel.policy)
    assert not tool.calls


def test_at_least_once_does_not_authorize_nonidempotent_retry(kernel):
    _, registry, p = setup_case(effect=SideEffect.AT_LEAST_ONCE)
    with pytest.raises(PlanRejected, match="RETRY102"):
        ScientificCompiler(registry).compile(p, kernel.envelope())


def test_no_registry_cannot_attest_repeat_safety(kernel):
    _, _, p = setup_case()
    with pytest.raises(PlanRejected, match="EFFECT106"):
        ScientificCompiler().compile(p, kernel.envelope())


@pytest.mark.parametrize("ledger_enabled", [False, True])
def test_plain_loop_never_retries_nonidempotent_tool(kernel, tmp_path, ledger_enabled):
    tool, registry, p = setup_case()
    ledger = OperationLedger(tmp_path / "operations.db") if ledger_enabled else None
    try:
        result = AgentLoopController(kernel, planner=StaticPlanner(p.plan), registry=registry,
            operations=ledger, limits=LoopLimits(max_replans=0)).run("submit once", kernel.envelope())
        assert not result.ok and len(tool.calls) == 1
        if ledger is not None:
            assert len(ledger.in_doubt()) == 1
    finally:
        if ledger is not None:
            ledger.close()


def test_scientific_planner_blocks_review_counterexample_before_dispatch(kernel):
    tool, registry, p = setup_case()
    result = AgentLoopController(kernel, planner=ScientificPlanner(p, registry=registry),
        registry=registry).run("submit", kernel.envelope())
    assert not result.ok and not tool.calls


def test_dynamic_preflight_blocks_review_counterexample(kernel, tmp_path):
    tool, registry, p = setup_case()
    with RunEventJournal(tmp_path / "events.db") as journal:
        with pytest.raises(PlanRejected, match="EFFECT106"):
            DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
                DynamicWorkflow((WorkflowStage("submit", p),), "submit"), kernel.envelope())
        assert not tool.calls and not journal.events()


@pytest.mark.parametrize("ledger_enabled", [False, True])
def test_trusted_idempotent_tool_still_retries(kernel, tmp_path, ledger_enabled):
    tool, registry, p = setup_case(idempotent=True, effect=SideEffect.IDEMPOTENT)
    ledger = OperationLedger(tmp_path / "operations.db") if ledger_enabled else None
    try:
        result = AgentLoopController(kernel, planner=ScientificPlanner(p, registry=registry),
            registry=registry, operations=ledger).run("safe retry", kernel.envelope())
        assert result.ok and len(tool.calls) == 2
    finally:
        if ledger is not None:
            ledger.close()


def test_manifest_fact_participates_in_task_fingerprint(kernel):
    tool, registry, p = setup_case(effect=SideEffect.NON_REPEATABLE, attempts=1)
    before = ScientificCompiler(registry).compile(p, kernel.envelope())
    tool.manifest = replace(tool.manifest, idempotent=True)
    registry.register(tool)
    after = ScientificCompiler(registry).compile(p, kernel.envelope())
    assert before.task_fingerprints != after.task_fingerprints


def test_same_controller_cannot_repeat_claim_after_failure_or_manifest_upgrade(kernel):
    tool, registry, p = setup_case()
    envelope = kernel.envelope()
    c = AgentLoopController(kernel, planner=StaticPlanner(p.plan), registry=registry,
                            limits=LoopLimits(max_replans=0))
    assert not c.run("first attempt", envelope).ok
    tool.manifest = replace(tool.manifest, idempotent=True)
    registry.register(tool)
    assert not c.run("same operation", envelope).ok
    assert len(tool.calls) == 1


@pytest.mark.parametrize("untrusted_flag", [1, "true"])
def test_truthy_nonboolean_fact_does_not_enable_retry(kernel, untrusted_flag):
    tool, registry, p = setup_case(idempotent=untrusted_flag)
    with pytest.raises(PlanRejected, match="EFFECT106"):
        ScientificCompiler(registry).compile(p, kernel.envelope())
    result = AgentLoopController(kernel, planner=StaticPlanner(p.plan), registry=registry,
        limits=LoopLimits(max_replans=0)).run("one attempt", kernel.envelope())
    assert not result.ok and len(tool.calls) == 1


def test_manifest_downgrade_blocks_already_scheduled_retry(kernel):
    tool, registry, p = setup_case(idempotent=True)
    invoke = tool.invoke
    def change_before_failure(payload, envelope):
        tool.manifest = replace(tool.manifest, idempotent=False)
        return invoke(payload, envelope)
    tool.invoke = change_before_failure
    result = AgentLoopController(kernel, planner=StaticPlanner(p.plan), registry=registry,
        limits=LoopLimits(max_replans=0)).run("changed facts", kernel.envelope())
    assert not result.ok and len(tool.calls) == 1
