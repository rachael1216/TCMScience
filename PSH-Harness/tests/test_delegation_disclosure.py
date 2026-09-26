"""The planner is told whether it may delegate, and a plan that delegates into nothing is
corrected rather than crashed.

The roadmap's last yellow cell: a plan *could* carry ``kind="delegate"`` tasks, and the
planner was never told whether the loop had a backend to run them. A model that guessed
wrong got a ``ContractViolation`` at dispatch, after the rest of the plan had already
spent budget. Now the loop states the fact on the state, the authority brief passes it
on, and the planner treats a delegate task without a backend as a refusal to correct.
"""

from __future__ import annotations

import json

import pytest

from psh.config import PSHConfig
from psh.contracts import Autonomy, ModelProfile, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, LoopLimits, LoopState, ModelPlanner, PlanRejected, Termination,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=PolicySnapshot(
        profile_id="delegation_bench", allowed_destinations=LOCAL,
        max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED, autonomy=Autonomy.ACT,
        risk_ceiling=RiskTier.R2_CONSEQUENTIAL, require_claim_support=False))
    yield k
    k.close()


@pytest.fixture
def local_model():
    return ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                        max_label=Sensitivity.PHI, usd_per_1k_input=0.0, usd_per_1k_output=0.0)


def plan_json(*tasks):
    return json.dumps({"objective": "o", "tasks": list(tasks),
                       "completion_criteria": [{"description": "done", "kind": "task"}]})


MODEL_TASK = {"task_id": "think", "objective": "summarise", "kind": "model",
              "destinations": ["LOCAL_MODEL"]}
DELEGATE_TASK = {"task_id": "child", "objective": "survey the literature on DIA", "kind": "delegate"}


def planner_with(kernel, model, responses):
    queue = list(responses)
    served = []

    def invoke(prompt):
        served.append(prompt)
        return queue.pop(0)

    p = ModelPlanner(kernel, model=model, model_invoke=invoke, max_attempts=len(responses))
    p.prompts = served
    return p


def test_the_brief_says_whether_delegation_is_available(kernel, local_model):
    state = LoopState(loop_id="l", objective="o", envelope=kernel.policy.envelope())
    assert 'not available in this loop; do not emit kind="delegate"' in \
        ModelPlanner._authority_brief(state)
    state.can_delegate = True
    brief = ModelPlanner._authority_brief(state)
    assert "available: up to" in brief and 'kind="delegate"' in brief


def test_the_loop_states_its_own_capability_on_the_state(kernel, local_model):
    seen = {}

    def invoke(prompt):
        seen["prompt"] = prompt
        return plan_json(MODEL_TASK)

    planner = ModelPlanner(kernel, model=local_model, model_invoke=invoke)
    without = AgentLoopController(kernel, planner=planner, model=local_model,
                                  model_invoke=lambda p: "answer", limits=LoopLimits(),
                                  sleep=lambda s: None)
    assert without.run("o", kernel.policy.envelope()).termination is Termination.GOAL_SATISFIED
    assert "not available in this loop" in seen["prompt"]

    with_backend = AgentLoopController(kernel, planner=planner, model=local_model,
                                       model_invoke=lambda p: "answer", limits=LoopLimits(),
                                       sleep=lambda s: None,
                                       delegate_backend=lambda contract: {"ok": True})
    assert with_backend.run("o", kernel.policy.envelope()).termination is Termination.GOAL_SATISFIED
    assert "available: up to" in seen["prompt"]


def test_a_delegate_task_without_a_backend_is_refused_and_corrected(kernel, local_model):
    p = planner_with(kernel, local_model, [plan_json(MODEL_TASK, DELEGATE_TASK), plan_json(MODEL_TASK)])
    state = LoopState(loop_id="l", objective="o", envelope=kernel.policy.envelope())
    plan = p.plan(state)
    assert [t.task_id for t in plan.tasks] == ["think"]
    assert len(p.attempts) == 2 and "delegation is not available" in p.attempts[0].error
    assert "delegation is not available" in p.prompts[1], "the refusal is fed back"


def test_a_delegate_task_that_is_never_corrected_ends_as_a_refusal(kernel, local_model):
    p = planner_with(kernel, local_model, [plan_json(DELEGATE_TASK)] * 2)
    with pytest.raises(PlanRejected, match="delegation is not available"):
        p.plan(LoopState(loop_id="l", objective="o", envelope=kernel.policy.envelope()))


def test_a_planned_delegation_runs_through_the_broker(kernel, local_model):
    delegated = []
    planner = ModelPlanner(kernel, model=local_model,
                           model_invoke=lambda p: plan_json(MODEL_TASK, DELEGATE_TASK))
    loop = AgentLoopController(kernel, planner=planner, model=local_model,
                               model_invoke=lambda p: "answer", limits=LoopLimits(),
                               sleep=lambda s: None,
                               delegate_backend=lambda c: delegated.append(c) or {"ok": True})
    before = kernel.broker.stats()
    result = loop.run("o", kernel.policy.envelope())
    after = kernel.broker.stats()
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert len(delegated) == 1 and delegated[0].objective == "survey the literature on DIA"
    assert after["delegations"] - before["delegations"] == 1
