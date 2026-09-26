from dataclasses import replace
import json
import sqlite3

import pytest

from psh.contracts import PolicyDenied
from psh.labels import DataLabel, Destination as D, Sensitivity as S
from psh.runtime import PlanRejected, LoopLimits
from psh.workflow import (Branch, WorkflowStage, DynamicWorkflow, DynamicWorkflowController,
                          RunEventJournal, ReplayRefused, replay)
from test_planner import kernel, local_profile
from test_scientific_workflow import program, task


def stage(name="a", **kwargs):
    return WorkflowStage(name, program([task("answer", max_label=S.RESEARCH_DEIDENTIFIED)]),
                         **kwargs)


def controller(kernel, local_profile, journal, responses, **kwargs):
    queue = iter(responses)
    return DynamicWorkflowController(kernel, journal=journal, model=local_profile,
                                     model_invoke=lambda _: next(queue), **kwargs)


def start(**kwargs):
    return dict(kind="started", fingerprint="a" * 64, entry=0, stages=2,
                max_visits=3, **kwargs)


def history():
    return [start(), dict(kind="stage_started", stage=0, visit=1),
            dict(kind="stage_finished", ok=True), dict(kind="routed", target=None),
            dict(kind="ended", reason="completed")]


@pytest.mark.parametrize("answer,target", [("yes", 1), ("no", 2)])
def test_branch_executes_only_selected_stage_and_replays(kernel, local_profile, tmp_path, answer, target):
    workflow = DynamicWorkflow((stage(branch=Branch("answer", "yes", "b", "c")),
                                stage("b"), stage("c")), "a")
    path = tmp_path / "events.db"
    with RunEventJournal(path) as journal:
        result = controller(kernel, local_profile, journal, [answer, "done"]).run(
            workflow, kernel.envelope())
        assert result.ok and len(result.stages) == 2
        assert kernel.broker.stats()["model_calls"] == 2
        assert [e["stage"] for e in journal.events() if e["kind"] == "stage_started"] == [0, target]
        anchor = journal.anchor()
        assert journal.replay(fingerprint=workflow.fingerprint) == result.state
        assert replay(journal.events()) == result.state
        # Result payloads and human-authored stage IDs are never serialized.
        assert "done" not in json.dumps(journal.events())
    with RunEventJournal(path) as journal:
        assert journal.replay(expected_anchor=anchor) == result.state
        with pytest.raises(ReplayRefused, match="nonempty"):
            controller(kernel, local_profile, journal, []).run(workflow, kernel.envelope())
        assert kernel.broker.stats()["model_calls"] == 2


def test_bounded_cycle_and_shared_run_id(kernel, local_profile, tmp_path):
    workflow = DynamicWorkflow((stage(next_stage="a"),), "a", max_visits=3)
    with RunEventJournal(tmp_path / "e.db") as journal:
        envelope = kernel.envelope()
        result = controller(kernel, local_profile, journal, ["x"] * 3).run(workflow, envelope)
        assert not result.ok and result.state.reason == "max_visits"
        assert result.state.visits == 3
        assert {r.run_id for r in result.stages} == {envelope.run_id}
        assert kernel.broker.stats()["model_calls"] == 3


def test_conditional_cycle_exits(kernel, local_profile, tmp_path):
    workflow = DynamicWorkflow((stage(branch=Branch("answer", "again", "a", None)),), "a")
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = controller(kernel, local_profile, journal, ["again", "stop"]).run(workflow, kernel.envelope())
        assert result.ok and result.state.visits == 2


def test_missing_pointer_is_route_failure(kernel, local_profile, tmp_path):
    workflow = DynamicWorkflow((stage(branch=Branch("answer", True, None, None, "/missing")),), "a")
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = controller(kernel, local_profile, journal, ["plain string"]).run(workflow, kernel.envelope())
        assert result.state.reason == "route_failed"


def test_invalid_unselected_branch_is_rejected_before_dispatch(kernel, local_profile, tmp_path):
    bad = stage("b")
    bad = replace(bad, program=replace(bad.program, plan=replace(bad.program.plan,
        tasks=(replace(bad.program.plan.tasks[0], destinations=(D.PUBLIC_REMOTE,)),))))
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PlanRejected):
            controller(kernel, local_profile, journal, []).run(
                DynamicWorkflow((stage(), bad), "a"), kernel.envelope())
        assert not journal.events()
        assert kernel.broker.stats()["model_calls"] == 0


def test_persistence_refusal_before_dispatch(kernel, local_profile, tmp_path):
    envelope = replace(kernel.envelope(), allowed_destinations=frozenset((D.LOCAL_MODEL,)))
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PolicyDenied, match="persistence"):
            controller(kernel, local_profile, journal, []).run(DynamicWorkflow((stage(),), "a"), envelope)
        assert not journal.events()


def test_store_ceiling_checked(kernel, local_profile, tmp_path):
    kernel.persistence.max_label = S.PUBLIC
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PolicyDenied, match="persistence"):
            controller(kernel, local_profile, journal, []).run(DynamicWorkflow((stage(),), "a"),
                kernel.envelope(), input_label=DataLabel(S.RESEARCH_DEIDENTIFIED))


def test_current_policy_change_stops_next_dispatch(kernel, local_profile, tmp_path):
    calls = []
    def invoke(_):
        calls.append(1)
        kernel.policy = replace(kernel.policy, allowed_destinations=(D.LOCAL_COMPUTE, D.PERSISTENT))
        return "done"
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, model=local_profile,
            model_invoke=invoke).run(DynamicWorkflow((stage(next_stage="b"), stage("b")), "a"),
                                    kernel.envelope())
        assert result.state.reason == "stage_failed"
        assert len(calls) == 1


def test_sensitive_output_does_not_leak_branch_and_remains_in_doubt(kernel, local_profile, tmp_path):
    with RunEventJournal(tmp_path / "e.db") as journal:
        with pytest.raises(PolicyDenied):
            controller(kernel, local_profile, journal, ["MRN: 12345678"]).run(
                DynamicWorkflow((stage(),), "a"), kernel.envelope())
        assert journal.replay().in_doubt
        assert "12345678" not in json.dumps(journal.events())


def test_crash_after_intent_replay_never_calls_provider(kernel, local_profile, tmp_path):
    def crash(_):
        raise KeyboardInterrupt()
    with RunEventJournal(tmp_path / "e.db") as journal:
        c = DynamicWorkflowController(kernel, journal=journal, model=local_profile, model_invoke=crash)
        with pytest.raises(KeyboardInterrupt):
            c.run(DynamicWorkflow((stage(),), "a"), kernel.envelope())
        assert journal.replay().in_doubt
        assert journal.replay().visits == 1


@pytest.mark.parametrize("change", [lambda e: e.update(kind="unknown"),
    lambda e: e.update(extra=True), lambda e: e.update(max_visits=True),
    lambda e: e.update(entry=2), lambda e: e.update(fingerprint="bad")])
def test_replay_rejects_invalid_start(change):
    event = start()
    change(event)
    with pytest.raises(ReplayRefused):
        replay([event])


@pytest.mark.parametrize("events", [history()[1:], history() + [history()[-1]],
    [start(), dict(kind="stage_finished", ok=True)],
    [start(), dict(kind="stage_started", stage=1, visit=1)],
    [start(), dict(kind="stage_started", stage=0, visit=2)],
    [start(), dict(kind="ended", reason="completed")],
    history()[:2] + [dict(kind="routed", target=0)],
    history()[:3] + [dict(kind="routed", target=2)],
    history()[:2] + [dict(kind="stage_finished", ok=1)]])
def test_replay_rejects_invalid_transitions(events):
    with pytest.raises(ReplayRefused):
        replay(events)


def test_journal_cas_tamper_anchor_and_rollback(tmp_path):
    path = tmp_path / "e.db"
    with RunEventJournal(path) as journal, RunEventJournal(path) as writer:
        journal.append(start(), expected_sequence=0)
        with pytest.raises(ReplayRefused, match="stale"):
            writer.append(history()[1], expected_sequence=0)
        with pytest.raises(ReplayRefused):
            writer.append(history()[2], expected_sequence=1)
        assert journal.replay().sequence == 1
        writer.append(history()[1], expected_sequence=1)
        anchor = writer.anchor()
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM workflow_events WHERE seq=2")
        with pytest.raises(ReplayRefused, match="anchor"):
            journal.replay(expected_anchor=anchor)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE workflow_events SET body='{}' WHERE seq=1")
        with pytest.raises(ReplayRefused, match="integrity"):
            journal.replay()


def test_fingerprint_and_snapshot():
    workflow = DynamicWorkflow((stage(),), "a")
    assert workflow.fingerprint == workflow.snapshot().fingerprint
    with pytest.raises(ReplayRefused, match="fingerprint"):
        replay(history(), fingerprint=workflow.fingerprint)
    assert workflow.fingerprint != replace(workflow, max_visits=2).fingerprint


@pytest.mark.parametrize("bound", [0, -1, 1025, True, 1.5])
def test_workflow_bounds(bound):
    with pytest.raises(ValueError):
        DynamicWorkflow((stage(),), "a", max_visits=bound)


def test_bad_graph():
    with pytest.raises(ValueError):
        DynamicWorkflow((stage(next_stage="absent"),), "a")
    with pytest.raises(ValueError):
        DynamicWorkflow((stage(), stage()), "a")
    with pytest.raises(ValueError):
        DynamicWorkflow((stage(branch=Branch("absent", True, None, None)),), "a")


def test_journal_failure_is_fail_closed(kernel, local_profile, tmp_path, monkeypatch):
    with RunEventJournal(tmp_path / "e.db") as journal:
        def fail(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(journal, "append", fail)
        with pytest.raises(OSError):
            controller(kernel, local_profile, journal, []).run(DynamicWorkflow((stage(),), "a"),
                                                               kernel.envelope())
        assert kernel.broker.stats()["model_calls"] == 0


def test_budget_is_not_reset_by_stage_visits(kernel, local_profile, tmp_path):
    from psh.runtime import Termination
    envelope = kernel.envelope()
    envelope = replace(envelope, budget=replace(envelope.budget, max_model_calls=2))
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = controller(kernel, local_profile, journal, ["x"] * 4).run(
            DynamicWorkflow((stage(next_stage="a"),), "a", max_visits=4), envelope)
        assert result.state.reason == "stage_failed"
        assert result.stages[-1].termination == Termination.BUDGET_EXHAUSTED
        assert kernel.broker.stats()["model_calls"] == 2


def test_repeatable_tool_visits_get_distinct_operation_keys(kernel, tmp_path):
    from psh.capabilities import CapabilityRegistry
    from psh.runtime import OperationLedger
    from psh.workflow import Effect, SideEffect
    from test_checkpoint import Tool
    from test_scientific_workflow import contract
    tool = Tool("probe", idempotent=True)
    registry = CapabilityRegistry()
    registry.register(tool)
    p = program([task("answer", kind="tool", component_id="probe",
                     max_label=S.RESEARCH_DEIDENTIFIED, destinations=(D.LOCAL_COMPUTE,))],
                {"answer": contract(effects=(Effect.LOCAL_COMPUTE,), side_effect=SideEffect.IDEMPOTENT)})
    ledger = OperationLedger(tmp_path / "ops.db")
    try:
        with RunEventJournal(tmp_path / "e.db") as journal:
            result = DynamicWorkflowController(kernel, journal=journal, registry=registry,
                operations=ledger).run(DynamicWorkflow((WorkflowStage("a", p, "a"),),
                                                     "a", max_visits=2), kernel.envelope())
            assert result.state.reason == "max_visits"
            assert len(tool.calls) == 2
            records = ledger.records()
            assert len({r.key for r in records}) == 2
            assert len({r.run_id for r in records}) == 1
            assert all(r.attempts == 1 for r in records)
    finally:
        ledger.close()


def test_cancellation_between_stages(kernel, local_profile, tmp_path):
    from types import SimpleNamespace
    token = SimpleNamespace(cancelled=False)
    def invoke(_):
        token.cancelled = True
        return "done"
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, model=local_profile,
            model_invoke=invoke, cancellation=token).run(
                DynamicWorkflow((stage(next_stage="a"),), "a"), kernel.envelope())
        assert result.state.reason == "cancelled" and result.state.visits == 1


def test_mutating_caller_program_does_not_change_future_stages(kernel, local_profile, tmp_path):
    second = stage("b")
    original = second.program.plan.tasks[0].payload
    def invoke(_):
        original["mutated"] = "MRN: 12345678"
        return "done"
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, model=local_profile,
            model_invoke=invoke).run(DynamicWorkflow((stage(next_stage="b"), second), "a"),
                                    kernel.envelope())
        assert result.ok
        assert not result.stages[-1].plan.tasks[0].payload


def test_finished_stage_before_route_is_not_in_doubt():
    state = replay(history()[:3])
    assert state.phase == "routing" and not state.in_doubt


def test_branch_comparison_rejects_nonfinite_and_structured_literals():
    for value in (float("nan"), float("inf"), {}, []):
        with pytest.raises(ValueError):
            Branch("answer", value, None, None)


def test_control_flow_label_prevents_sensitive_next_stage_egress(kernel, local_profile, tmp_path):
    from psh.workflow import Effect
    from test_scientific_workflow import contract
    kernel.policy = replace(kernel.policy, max_data_label=S.PHI, allowed_destinations=tuple(D))
    kernel.persistence.max_label = S.PHI
    first = WorkflowStage("local", program([task("answer")]), next_stage="remote")
    remote = WorkflowStage("remote", program([task("answer", max_label=S.RESEARCH_DEIDENTIFIED,
                                                  destinations=(D.PUBLIC_REMOTE,))],
        {"answer": contract(effects=(Effect.PUBLIC_REMOTE,))}))
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = controller(kernel, local_profile, journal, ["MRN: 12345678"]).run(
            DynamicWorkflow((first, remote), "local"), kernel.envelope())
        assert result.state.reason == "stage_failed"
        assert result.label.sensitivity >= S.PHI
        assert kernel.broker.stats()["model_calls"] == 1
        assert "12345678" not in json.dumps(journal.events())


def test_partial_database_transaction_is_not_replayed(tmp_path):
    path = tmp_path / "e.db"
    with RunEventJournal(path):
        pass
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO workflow_events VALUES (1, '{}', '', '')")
    conn.close()
    with RunEventJournal(path) as journal:
        assert journal.replay().phase == "empty"
