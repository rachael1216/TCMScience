"""The typed planner: a model proposes, the validator disposes.

The last placeholder in the pipeline. `_plan()` split the request on full stops and
`validate_plan` recorded "no typed plan to validate", so the loop could iterate only over a
plan its caller had written.

The centrepiece here is ``test_a_planner_that_proposes_an_escalating_plan_is_refused``.
Everything else in this package assumes the model is a component whose output is untrusted,
and a planner is the sharpest case of that: its output is not an answer to be checked but a
*program to be run*. If a model could widen a run by writing a wider plan, every control in
the package would be reachable by asking nicely.
"""

from __future__ import annotations

import json

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, ComponentKind, ComponentManifest, ModelProfile, RiskTier,
)
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, LoopLimits, LoopState, ModelPlanner, PlanParseError, PlanRejected,
    TaskKind, Termination, parse_plan,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


def policy(**kw):
    params = dict(profile_id="planner_bench", allowed_destinations=LOCAL,
                  max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED,
                  autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R2_CONSEQUENTIAL,
                  require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


@pytest.fixture
def local_profile():
    return ModelProfile(id="local-8b", provider="local",
                        destination=Destination.LOCAL_MODEL, max_label=Sensitivity.PHI,
                        usd_per_1k_input=0.0, usd_per_1k_output=0.0)


def plan_json(**overrides):
    body = {
        "objective": "summarise the evidence",
        "tasks": [{"task_id": "search", "objective": "find trials", "kind": "model",
                   "destinations": ["LOCAL_MODEL"]}],
        "completion_criteria": [{"description": "an answer exists", "kind": "task"}],
    }
    body.update(overrides)
    return json.dumps(body)


def planner(kernel, profile, responses, **kw):
    """A planner whose model returns each response in turn."""
    queue = list(responses)
    served: list[str] = []

    def invoke(prompt: str) -> str:
        served.append(prompt)
        return queue.pop(0) if queue else queue_exhausted()

    def queue_exhausted():
        raise AssertionError("the planner asked for more attempts than the test supplied")

    p = ModelPlanner(kernel, model=profile, model_invoke=invoke, **kw)
    p.prompts = served                                   # for assertions
    return p


def state_for(kernel, objective="summarise the evidence", **envelope_kw):
    return LoopState(loop_id="loop_test", objective=objective,
                     envelope=kernel.policy.envelope(**envelope_kw))


# ================================================ the property that matters

def test_a_planner_that_proposes_an_escalating_plan_is_refused(kernel, local_profile):
    """A model cannot widen a run by writing a wider plan.

    The run is local-only and R2. The model asks for a task at ``PUBLIC_REMOTE`` and
    ``R4_KERNEL``. If a plan were trusted because a model produced it, every control in
    this package would be reachable by asking for it.
    """
    escalating = plan_json(tasks=[{
        "task_id": "exfiltrate", "objective": "send it all to a public provider",
        "kind": "model", "max_risk": "R4_KERNEL", "max_label": "PHI",
        "destinations": ["PUBLIC_REMOTE"]}])

    p = planner(kernel, local_profile, [escalating] * 3, max_attempts=3)
    with pytest.raises(PlanRejected) as exc:
        p.plan(state_for(kernel))

    assert "PUBLIC_REMOTE" in str(exc.value) or "destination" in str(exc.value)
    assert len(p.attempts) == 3, "it must stop re-asking, not loop"
    assert all(a.error for a in p.attempts)


def test_the_refusal_is_fed_back_so_the_model_can_correct(kernel, local_profile):
    """A retry that repeats the prompt gets the same answer. Name what was wrong."""
    bad = plan_json(tasks=[{"task_id": "t", "objective": "o", "kind": "model",
                            "destinations": ["PUBLIC_REMOTE"]}])
    p = planner(kernel, local_profile, [bad, plan_json()], max_attempts=2)

    plan = p.plan(state_for(kernel))

    assert plan.tasks[0].task_id == "search"
    assert len(p.prompts) == 2
    assert "refused" in p.prompts[1].lower()
    assert "PUBLIC_REMOTE" in p.prompts[1] or "destination" in p.prompts[1]


def test_correction_is_bounded(kernel, local_profile):
    """Re-asking is worth doing once or twice; forever is a loop, not a correction."""
    p = planner(kernel, local_profile, ["not json at all"] * 2, max_attempts=2)
    with pytest.raises(PlanRejected):
        p.plan(state_for(kernel))
    assert kernel.broker.stats()["model_calls"] == 2


def test_planning_is_a_model_call_like_any_other(kernel, local_profile):
    """The planner holds no provider: its prompt is gated, budgeted and recorded."""
    before = kernel.broker.stats()["model_calls"]
    planner(kernel, local_profile, [plan_json()]).plan(state_for(kernel))
    assert kernel.broker.stats()["model_calls"] == before + 1


def test_a_planner_cannot_reach_a_destination_the_run_forbids(kernel):
    """Even the planner's own call goes through the model gateway."""
    from psh.contracts import EgressDenied

    public = ModelProfile(id="cloud", provider="public",
                          destination=Destination.PUBLIC_REMOTE,
                          max_label=Sensitivity.PHI)
    p = planner(kernel, public, [plan_json()])
    with pytest.raises(EgressDenied):
        p.plan(state_for(kernel))


def test_the_prompt_states_the_ceiling(kernel, local_profile):
    """An avoidable refusal is cheaper than a paid-for one."""
    p = planner(kernel, local_profile, [plan_json()])
    p.plan(state_for(kernel))
    prompt = p.prompts[0]
    for expected in ("risk ceiling", "data ceiling", "destinations", "budget"):
        assert expected in prompt


# ======================================================== parsing is strict

def test_json_is_found_inside_prose_and_fences():
    wrapped = f"Sure, here is the plan:\n```json\n{plan_json()}\n```\nLet me know!"
    assert parse_plan(wrapped).tasks[0].task_id == "search"
    assert parse_plan(f"Plan: {plan_json()} — that should do it").tasks


def test_trailing_prose_with_a_brace_does_not_break_the_parse():
    """Taking the last '}' would swallow this; the parser matches braces instead."""
    text = plan_json() + "\n\nNote: use {curly braces} carefully."
    assert parse_plan(text).tasks[0].task_id == "search"


@pytest.mark.parametrize("text,fragment", [
    ("no json here", "no JSON object"),
    ('{"objective": "x"}', "non-empty 'tasks'"),
    ('{"tasks": [], "objective": "x"}', "non-empty 'tasks'"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o","kind":"magic"}]}', "kind"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o","kind":"tool"}]}',
     "names no component"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o","destinations":["MARS"]}]}',
     "legal destinations"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o","max_risk":"R9"}]}',
     "max_risk"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o",'
     '"dependencies":["a"]}]}', "depends on itself"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":""}]}', "objective"),
    ('{"objective":"x","tasks":[{"task_id":"a","objective":"o"},'
     '{"task_id":"a","objective":"o"}]}', "duplicate"),
])
def test_malformed_plans_are_refused_with_a_usable_message(text, fragment):
    """Each message is what the model sees next, so it names the field, not 'invalid'."""
    with pytest.raises(PlanParseError, match=fragment):
        parse_plan(text)


def test_an_unterminated_object_is_refused():
    with pytest.raises(PlanParseError, match="unterminated"):
        parse_plan('{"objective": "x", "tasks": [{"task_id": "a"')


def test_parsing_does_not_execute_anything(kernel, local_profile):
    """Parsing is pure. A plan that names a tool does not touch the tool."""
    registry = CapabilityRegistry()

    class Probe:
        manifest = ComponentManifest(id="probe", name="probe", kind=ComponentKind.TOOL)

        def __init__(self):
            self.calls = 0

        def invoke(self, payload, envelope):
            self.calls += 1
            return {}

    probe = Probe()
    registry.register(probe)
    parse_plan(plan_json(tasks=[{"task_id": "t", "objective": "o", "kind": "tool",
                                 "component_id": "probe"}]))
    assert probe.calls == 0


# ============================================== the planner drives the loop

def test_the_loop_runs_a_plan_the_model_wrote(kernel, local_profile):
    """End to end: no caller-supplied plan anywhere."""
    registry = CapabilityRegistry()

    class Summariser:
        manifest = ComponentManifest(id="summarise", name="summarise",
                                     kind=ComponentKind.TOOL,
                                     max_label=Sensitivity.PHI)

        def invoke(self, payload, envelope):
            return {"answer": "SGLT2 inhibitors reduce HF hospitalisation"}

    registry.register(Summariser())
    written = plan_json(tasks=[
        {"task_id": "summarise", "objective": "summarise the trials", "kind": "tool",
         "component_id": "summarise",
         "output_schema": {"type": "object", "required": ["answer"]},
         "acceptance_tests": [{"kind": "non_empty"}]}])

    p = planner(kernel, local_profile, [written], registry=registry)
    loop = AgentLoopController(kernel, planner=p, registry=registry, model=local_profile,
                               model_invoke=lambda prompt: "unused",
                               limits=LoopLimits(max_iterations=6))
    result = loop.run("summarise the HFpEF evidence", kernel.policy.envelope())

    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert result.results["summarise"]["answer"].startswith("SGLT2")
    assert result.plan.produced_by.startswith("local-8b")


def test_a_loop_whose_planner_cannot_produce_a_valid_plan_terminates(kernel,
                                                                     local_profile):
    """PlanRejected must stop the loop, not escape it."""
    p = planner(kernel, local_profile, ["garbage"] * 2, max_attempts=2)
    loop = AgentLoopController(kernel, planner=p, model=local_profile,
                               model_invoke=lambda prompt: "unused",
                               limits=LoopLimits(max_iterations=4))
    result = loop.run("do something", kernel.policy.envelope())
    assert result.termination is Termination.PLAN_REJECTED


def test_a_planner_naming_an_unregistered_component_is_refused(kernel, local_profile):
    """The registry is consulted at validation, not discovered at execution."""
    registry = CapabilityRegistry()
    ghost = plan_json(tasks=[{"task_id": "t", "objective": "o", "kind": "tool",
                              "component_id": "does_not_exist"}])
    p = planner(kernel, local_profile, [ghost] * 2, registry=registry, max_attempts=2)
    with pytest.raises(PlanRejected, match="not registered"):
        p.plan(state_for(kernel))
