from dataclasses import replace
import json

import pytest

from psh.contracts import BudgetExhausted, EgressDenied, ModelProfile
from psh.labels import DataLabel, Destination as D, Sensitivity as S
from psh.runtime import AgentLoopController, LoopLimits, PlanParseError, PlanRejected
from psh.workflow import ScientificModelPlanner, parse_scientific_program
from test_planner import kernel, local_profile, state_for, plan_json
from test_scientific_workflow import contract, evidence_program, program, task


def valid():
    return program([task("a", max_label=S.RESEARCH_DEIDENTIFIED)]).to_dict()


def planner(kernel, local_profile, responses, **kwargs):
    prompts = []
    queue = iter(responses)

    def invoke(prompt):
        prompts.append(prompt)
        return next(queue)

    result = ScientificModelPlanner(kernel, model=local_profile, model_invoke=invoke, **kwargs)
    result.prompts = prompts
    return result


def test_brokered_scientific_proposal_roundtrip(kernel, local_profile):
    p = planner(kernel, local_profile, [json.dumps(valid())])
    result = p.plan(state_for(kernel))
    assert result.tasks[0].task_id == "a"
    assert p.last_program.to_dict() == valid_with_identity(p.last_program.to_dict())
    assert p.last_compilation.plan == result
    assert kernel.broker.stats()["model_calls"] == 1
    assert p.attempts[0].error == ""


def valid_with_identity(value):
    # Program IDs are generated per construction; the content is otherwise stable.
    expected = valid()
    expected["plan"]["plan_id"] = value["plan"]["plan_id"]
    return expected


@pytest.mark.parametrize("bad", ["not JSON", "[]", "null", "{}", plan_json(),
    '{"schema_version":1,"schema_version":2}',
    '{"schema_version":NaN}', '{"schema_version":1e999}',
    '```json\n{}\n```', '{} {}', '{"schema_version":true,"plan":{},"contracts":{}}'])
def test_strict_response_parser(bad):
    with pytest.raises(PlanParseError, match="SCIENTIFIC_SCHEMA"):
        parse_scientific_program(bad)


def test_duplicate_nested_fields_and_size_limit():
    text = json.dumps(valid()).replace('"effects": ["local_model"]',
                                     '"effects": [], "effects": ["local_model"]')
    with pytest.raises(PlanParseError):
        parse_scientific_program(text)
    with pytest.raises(PlanParseError):
        parse_scientific_program(json.dumps(valid()), max_chars=10)


def test_invalid_then_valid_repair_is_budgeted(kernel, local_profile):
    p = planner(kernel, local_profile, [plan_json(), json.dumps(valid())])
    p.plan(state_for(kernel))
    assert len(p.prompts) == 2
    assert "SCIENTIFIC_SCHEMA" in p.prompts[1]
    assert p.attempts[0].error and not p.attempts[1].error
    assert kernel.broker.stats()["model_calls"] == 2


def test_scientific_evidence_refusal_is_repaired(kernel, local_profile):
    bad = evidence_program(design="animal")
    bad = replace(bad, plan=replace(bad.plan, tasks=tuple(
        replace(t, max_label=S.RESEARCH_DEIDENTIFIED) for t in bad.plan.tasks)))
    p = planner(kernel, local_profile, [json.dumps(bad.to_dict()), json.dumps(valid())])
    p.plan(state_for(kernel))
    assert "EVIDENCE103" in p.attempts[0].error
    assert "EVIDENCE103" in p.prompts[1]


def test_no_plain_plan_fallback_and_bound(kernel, local_profile):
    p = planner(kernel, local_profile, [plan_json()] * 2, max_attempts=2)
    with pytest.raises(PlanRejected, match="exhausted 2"):
        p.plan(state_for(kernel))
    assert len(p.prompts) == 2
    assert p.last_program is None and p.last_compilation is None


def test_error_details_do_not_echo_model_text(kernel, local_profile):
    bad = valid()
    bad["private-untrusted-token"] = "do something else"
    p = planner(kernel, local_profile, [json.dumps(bad), json.dumps(valid())])
    p.plan(state_for(kernel))
    assert "private-untrusted-token" not in p.prompts[1]
    assert "private-untrusted-token" not in repr(p.attempts)


def test_existing_plan_label_propagates(kernel, local_profile):
    state = state_for(kernel)
    state.plan_label = DataLabel(S.RESEARCH_DEIDENTIFIED)
    p = planner(kernel, local_profile, [json.dumps(valid())])
    result = p.plan(state)
    assert result.tasks[0].input_sensitivity == S.RESEARCH_DEIDENTIFIED
    assert state.plan_label.sensitivity >= S.RESEARCH_DEIDENTIFIED


def test_current_policy_checked_before_provider(kernel, local_profile):
    state = state_for(kernel)
    kernel.policy = replace(kernel.policy, allowed_destinations=(D.LOCAL_COMPUTE,))
    p = planner(kernel, local_profile, [json.dumps(valid())])
    with pytest.raises(EgressDenied):
        p.plan(state)
    assert p.prompts == []


def test_phi_objective_not_sent_to_public_model(kernel):
    kernel.policy = replace(kernel.policy, allowed_destinations=tuple(D), max_data_label=S.PHI)
    profile = ModelProfile(id="public-test", provider="public", destination=D.PUBLIC_REMOTE,
                           max_label=S.PHI)
    state = state_for(kernel)
    state.objective_label = DataLabel(S.PHI)
    p = planner(kernel, profile, [json.dumps(valid())])
    with pytest.raises(EgressDenied):
        p.plan(state)
    assert p.prompts == []


def test_repair_stops_at_run_budget(kernel, local_profile):
    state = state_for(kernel, budget=replace(kernel.policy.budget, max_model_calls=1))
    p = planner(kernel, local_profile, ["bad", json.dumps(valid())])
    with pytest.raises(BudgetExhausted):
        p.plan(state)
    assert len(p.prompts) == 1


@pytest.mark.parametrize("count", [0, -1, True, 1.5, 9])
def test_invalid_attempt_bound(kernel, local_profile, count):
    with pytest.raises(ValueError):
        planner(kernel, local_profile, [], max_attempts=count)


def test_provider_failure_is_not_ir_repair(kernel, local_profile):
    def broken(prompt):
        raise RuntimeError("provider unavailable")
    p = ScientificModelPlanner(kernel, model=local_profile, model_invoke=broken)
    with pytest.raises(RuntimeError):
        p.plan(state_for(kernel))
    assert not p.attempts


def test_real_loop_accepts_compiled_model_proposal(kernel, local_profile):
    p = planner(kernel, local_profile, [json.dumps(valid())])
    loop = AgentLoopController(kernel, planner=p, model=local_profile,
                               model_invoke=lambda prompt: "synthetic result",
                               limits=LoopLimits(max_iterations=3))
    result = loop.run("inspect synthetic material", kernel.policy.envelope())
    assert p.last_program is not None
    assert result.ok, result.reason
    assert result.results == {"a": "synthetic result"}


def test_failed_replan_clears_previous_inspection_state(kernel, local_profile):
    p = planner(kernel, local_profile, [json.dumps(valid()), "bad"], max_attempts=1)
    p.plan(state_for(kernel))
    with pytest.raises(PlanRejected):
        p.plan(state_for(kernel))
    assert p.last_program is None and p.last_compilation is None
    assert len(p.attempts) == 1 and p.attempts[0].error


def test_exhausted_proposals_never_execute_tasks(kernel, local_profile):
    calls = []
    p = planner(kernel, local_profile, [plan_json()], max_attempts=1)
    loop = AgentLoopController(kernel, planner=p, model=local_profile,
                               model_invoke=lambda prompt: calls.append(prompt))
    result = loop.run("inspect material", kernel.policy.envelope())
    assert not result.ok
    assert calls == [] and result.results == {}


def test_labels_from_refused_output_survive_repair(kernel, local_profile, monkeypatch):
    import psh.workflow.model_planner as module
    kernel.policy = replace(kernel.policy, max_data_label=S.PHI)
    safe = program([task("a", max_label=S.PHI)]).to_dict()
    monkeypatch.setattr(module, "classify_with", lambda *args, **kw: DataLabel(S.PHI))
    p = planner(kernel, local_profile, ["bad", json.dumps(safe)])
    state = state_for(kernel)
    result = p.plan(state)
    assert result.tasks[0].input_sensitivity == S.PHI
    assert state.plan_label.sensitivity == S.PHI
