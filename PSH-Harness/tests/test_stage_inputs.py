from dataclasses import replace
import json

import pytest

from psh.capabilities import CapabilityRegistry
from psh.labels import Destination as D, Sensitivity as S, unwrap_deep
from psh.runtime import InputBinding
from psh.workflow import (Branch, StageInput, WorkflowStage, DynamicWorkflow,
    DynamicWorkflowController, RunEventJournal, Effect)
from test_checkpoint import Tool
from test_planner import kernel
from test_scientific_workflow import contract, program, task


def tool_stage(name, component, *, inputs=(), next_stage=None, branch=None, payload=None):
    p = program([task("answer", kind="tool", component_id=component,
        max_label=S.RESEARCH_DEIDENTIFIED, destinations=(D.LOCAL_COMPUTE,), payload=payload or {})],
        {"answer": contract(effects=(Effect.LOCAL_COMPUTE,))})
    # Random hexadecimal transport IDs can accidentally match identifier rules.
    # This synthetic fixture tests input flow, not probabilistic classification.
    p = replace(p, plan=replace(p.plan, plan_id="stage_fixture"))
    return WorkflowStage(name, p, next_stage, branch, inputs)


def binding(*, target="answer", argument="value", source="answer", pointer="/value", **kwargs):
    return StageInput(target, InputBinding(argument, source, pointer, **kwargs))


def workflow(item=None):
    return DynamicWorkflow((tool_stage("source", "source", next_stage="target"),
        tool_stage("target", "target", inputs=(item or binding(),))), "source")


def execute(kernel, tmp_path, source, *, flow=None, target=None):
    registry = CapabilityRegistry()
    producer = Tool("source", source)
    consumer = target or Tool("target")
    registry.register(producer)
    registry.register(consumer)
    with RunEventJournal(tmp_path / "events.db") as journal:
        result = DynamicWorkflowController(kernel, journal=journal, registry=registry).run(
            flow or workflow(), kernel.envelope())
        assert journal.replay() == result.state
        events = journal.events()
    return result, producer, consumer, events


def test_actual_tool_argument_and_no_payload_in_events(kernel, tmp_path):
    result, producer, consumer, events = execute(kernel, tmp_path, {"value": "selected-data", "hidden": "not-selected"})
    assert result.ok and len(producer.calls) == len(consumer.calls) == 1
    payload = unwrap_deep(consumer.calls[0])
    assert payload["value"] == "selected-data" and "hidden" not in payload
    assert "selected-data" not in json.dumps(events)
    assert "not-selected" not in json.dumps(events)


@pytest.mark.parametrize("value,expected", [(7, "integer"), (3.5, "number"),
    (True, "boolean"), ("text", "string"), ([1, 2], "array"), ({"x": 1}, "object")])
def test_bound_types(kernel, tmp_path, value, expected):
    result, _, consumer, _ = execute(kernel, tmp_path, {"value": value},
        flow=workflow(binding(expected_type=expected)))
    assert result.ok and unwrap_deep(consumer.calls[0])["value"] == value


@pytest.mark.parametrize("source,item", [({"value": True}, binding(expected_type="integer")),
    ({"value": "7"}, binding(expected_type="number")),
    ({"missing": 1}, binding()),
    ({"value": [1, "x"]}, binding(expected_type="integer", cardinality="many")),
    ({"value": 1}, binding(cardinality="many")),
    ({"value": float("nan")}, binding()),
    ({"value": float("inf")}, binding())])
def test_bad_bindings_stop_before_target_intent_or_dispatch(kernel, tmp_path, source, item):
    result, _, consumer, events = execute(kernel, tmp_path, source, flow=workflow(item))
    assert result.state.reason == "binding_failed" and not result.ok
    assert not consumer.calls
    assert result.state.visits == 1
    assert len([e for e in events if e["kind"] == "stage_started"]) == 1


def test_optional_missing_argument_is_omitted(kernel, tmp_path):
    result, _, consumer, _ = execute(kernel, tmp_path, {"other": 1},
        flow=workflow(binding(required=False)))
    assert result.ok and "value" not in unwrap_deep(consumer.calls[0])


def test_nested_pointer_and_many(kernel, tmp_path):
    result, _, consumer, _ = execute(kernel, tmp_path, {"a/b": {"~hits": [2, 3]}},
        flow=workflow(binding(pointer="/a~1b/~0hits", expected_type="integer", cardinality="many")))
    assert result.ok and unwrap_deep(consumer.calls[0])["value"] == [2, 3]


def test_cycle_reads_immediately_previous_visit(kernel, tmp_path):
    from psh.workflow import SideEffect
    class Increment(Tool):
        def invoke(self, payload, envelope):
            super().invoke(payload, envelope)
            return {"value": unwrap_deep(payload)["value"] + 1}
    consumer = Increment("target")
    flow = DynamicWorkflow((tool_stage("source", "source", next_stage="target"),
        tool_stage("target", "target", inputs=(binding(expected_type="integer"),),
                   branch=Branch("answer", 3, None, "target", "/value"))), "source")
    repeated = flow.stages[1]
    repeated = replace(repeated, program=replace(repeated.program, contracts={
        "answer": replace(repeated.program.contracts["answer"], side_effect=SideEffect.IDEMPOTENT)}))
    flow = replace(flow, stages=(flow.stages[0], repeated))
    result, _, consumer, _ = execute(kernel, tmp_path, {"value": 0}, flow=flow, target=consumer)
    assert result.ok and result.state.visits == 4
    assert [unwrap_deep(p)["value"] for p in consumer.calls] == [0, 1, 2]


def test_payload_copy_isolation(kernel, tmp_path):
    class Mutator(Tool):
        def invoke(self, payload, envelope):
            super().invoke(payload, envelope)
            unwrap_deep(payload)["value"]["items"].append(99)
            return {"ok": True}
    original = {"value": {"items": [1]}}
    result, _, _, _ = execute(kernel, tmp_path, original, target=Mutator("target"))
    assert result.ok and original == {"value": {"items": [1]}}
    assert result.stages[0].results["answer"] == original


@pytest.mark.parametrize("argument", ["upstream", "_psh_idempotency_key", "_psh_future"])
def test_reserved_arguments_are_refused(argument):
    with pytest.raises(ValueError, match="reserved"):
        binding(argument=argument)


def test_static_binding_validation():
    with pytest.raises(ValueError, match="entry"):
        DynamicWorkflow((tool_stage("source", "source", inputs=(binding(),)),), "source")
    with pytest.raises(ValueError, match="predecessor"):
        workflow(binding(source="absent"))
    with pytest.raises(ValueError, match="tool"):
        workflow(binding(target="absent"))
    source, target = workflow().stages
    with pytest.raises(ValueError, match="duplicate"):
        DynamicWorkflow((source, replace(target, inputs=(binding(), binding()))), "source")
    with pytest.raises(ValueError, match="conflict"):
        DynamicWorkflow((source, tool_stage("target", "target", inputs=(binding(),),
                                           payload={"value": "literal"})), "source")


def test_model_target_is_not_silently_ignored():
    source, target = workflow().stages
    model = replace(target, program=program([task("answer")]))
    with pytest.raises(ValueError, match="tool"):
        DynamicWorkflow((source, model), "source")


def test_source_required_in_all_incoming_branches():
    source, target = workflow().stages
    other = replace(source, stage_id="other", program=program([task("different")]))
    with pytest.raises(ValueError, match="every predecessor"):
        DynamicWorkflow((source, other, target), "source")


def test_stage_fingerprint_preserves_legacy_and_tracks_input_changes():
    from psh.workflow.ir import digest
    legacy = DynamicWorkflow((tool_stage("source", "source"),), "source")
    body = legacy.to_dict()
    body["stages"][0]["program"]["plan"].pop("plan_id")
    assert "inputs" not in body["stages"][0]
    assert legacy.fingerprint == digest(body)
    flow = workflow()
    assert flow.fingerprint == flow.snapshot().fingerprint
    assert flow.fingerprint != workflow(binding(pointer="/other")).fingerprint


def test_sensitive_bound_field_cannot_lower_output_label(kernel, tmp_path):
    kernel.policy = replace(kernel.policy, max_data_label=S.PHI)
    kernel.persistence.max_label = S.PHI
    flow = workflow()
    first = flow.stages[0]
    first = replace(first, program=replace(first.program, plan=replace(first.program.plan,
        tasks=tuple(replace(t, max_label=S.PHI) for t in first.program.plan.tasks))))
    flow = replace(flow, stages=(first, flow.stages[1]))
    result, _, consumer, _ = execute(kernel, tmp_path,
        {"value": 1, "private": "MRN: 12345678"}, flow=flow)
    assert result.state.reason == "stage_failed" and not consumer.calls
    assert result.label.sensitivity >= S.PHI


def test_binding_failure_replay_is_only_valid_after_a_routed_visit():
    from psh.workflow.events import replay, ReplayRefused
    from test_dynamic_workflow import history
    events = history()[:3] + [dict(kind="routed", target=1), dict(kind="ended", reason="binding_failed")]
    assert replay(events).reason == "binding_failed"
    with pytest.raises(ReplayRefused):
        replay([events[0], events[-1]])


@pytest.mark.parametrize("value", [{1: "not-a-string-key"}, {"x": {1, 2}}, (1, 2), object()])
def test_non_json_values_do_not_get_coerced(value):
    from psh.workflow.dynamic import _copy_inputs
    from psh.runtime.bindings import BindingError
    with pytest.raises(BindingError):
        _copy_inputs({"value": value})


def test_all_bindings_resolve_before_any_target_task_executes(kernel, tmp_path):
    flow = workflow()
    target = flow.stages[1]
    first = target.program.plan.tasks[0]
    second = replace(first, task_id="second")
    p = program([first, second], {"answer": contract(effects=(Effect.LOCAL_COMPUTE,)),
                                 "second": contract(effects=(Effect.LOCAL_COMPUTE,))})
    target = replace(target, program=p, inputs=(binding(), binding(target="second", pointer="/missing")))
    flow = replace(flow, stages=(flow.stages[0], target))
    result, _, consumer, _ = execute(kernel, tmp_path, {"value": 1}, flow=flow)
    assert result.state.reason == "binding_failed" and not consumer.calls


def test_conflict_with_intra_stage_binding():
    source, target = workflow().stages
    first = replace(target.program.plan.tasks[0], task_id="first")
    second = replace(target.program.plan.tasks[0], dependencies=("first",),
                     inputs=(InputBinding("value", "first"),))
    p = program([first, second], {"first": contract(effects=(Effect.LOCAL_COMPUTE,)),
                                 "answer": contract(effects=(Effect.LOCAL_COMPUTE,))})
    with pytest.raises(ValueError, match="conflict"):
        DynamicWorkflow((source, replace(target, program=p)), "source")


def test_bound_payload_does_not_alias_previous_result(kernel, tmp_path):
    from psh.labels import DataLabel
    from psh.runtime import LoopResult, Termination
    previous = LoopResult("loop", "run", Termination.GOAL_SATISFIED,
                          results={"answer": {"value": {"items": [1]}}})
    with RunEventJournal(tmp_path / "e.db") as journal:
        c = DynamicWorkflowController(kernel, journal=journal)
        bound = c._bind_stage(workflow().stages[1], previous, DataLabel())
        bound.plan.tasks[0].payload["value"]["items"].append(2)
        assert previous.results["answer"]["value"]["items"] == [1]
