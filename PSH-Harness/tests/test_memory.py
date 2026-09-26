"""Governed memory retrieval: what a run may recall is what the gateway let in, labelled.

``kernel/persistence.py`` made the WorkGraph a governed sink so that mis-labelled content
could not become "a standing invitation to include PHI in a future prompt". This is the
read side of that promise. The tests check that only validated knowledge is recalled,
that a rejected claim is never recalled, that every item carries its stored label, that
the run's ceiling and the destination both withhold, that withholding memory never turns
into a refusal, and that retrieval writes nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import psh.context.memory as memory_module
from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.context import MemoryRetriever
from psh.contracts import Autonomy, ModelProfile, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, Criterion, LoopLimits, ModelPlanner, Plan, PlanTask, Runner,
    StaticPlanner, TaskKind, Termination,
)
from psh.workgraph import NodeKind

MRN = "04851923"
PHI_CLAIM = f"Patient Alice Smith MRN {MRN} responded to the DIA proteomics protocol"
LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
OPEN = LOCAL + (Destination.PUBLIC_REMOTE,)


def policy(**kw):
    params = dict(profile_id="memory_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=OPEN, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


@pytest.fixture
def local_model():
    return ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                        max_label=Sensitivity.PHI, usd_per_1k_input=0.0, usd_per_1k_output=0.0)


@pytest.fixture
def public_model():
    return ModelProfile(id="gpt-public", provider="public",
                        destination=Destination.PUBLIC_REMOTE,
                        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
                        usd_per_1k_input=0.0, usd_per_1k_output=0.0)


def commit(kernel, title, *, kind=NodeKind.CLAIM, status="verified", project="proj_a",
           body="", ref=""):
    return kernel.persistence.commit_node(
        kind=kind, title=title, body=body, ref=ref, principal="tester",
        source_run="run_earlier", project_id=project, validation_status=status)


def envelope_for(kernel, project="proj_a", **kw):
    return kernel.policy.envelope(project_id=project, **kw)


def plan_of(*tasks):
    return Plan(objective="answer from memory", tasks=tuple(tasks),
                completion_criteria=(Criterion(description="done"),), produced_by="test")


# ================================================================= the read side

def test_verified_memory_is_recalled_with_the_label_it_was_stored_under(kernel):
    node = commit(kernel, PHI_CLAIM)
    assert node.label.sensitivity is Sensitivity.PHI, "the gateway classified it at write"

    items = MemoryRetriever(kernel.graph).retrieve("DIA proteomics protocol",
                                                   envelope=envelope_for(kernel))
    assert len(items) == 1
    item = items[0]
    assert item.kind == "memory" and item.source_ref == node.id
    assert item.label.sensitivity is Sensitivity.PHI
    assert MRN in item.content


def test_a_rejected_claim_is_never_recalled(kernel):
    """The release gate refused it, so it lives on as a hash and a reason — and even a
    retriever told to recall rejected claims gets nothing, because there is no text."""
    kernel.persistence.commit_rejected(statement="ivermectin cures the proteomics cohort",
                                       reason="unsupported", principal="tester",
                                       source_run="run_earlier", project_id="proj_a")
    permissive = MemoryRetriever(kernel.graph, kinds=list(NodeKind), statuses=["rejected", "verified"])
    assert permissive.retrieve("ivermectin cohort", envelope=envelope_for(kernel)) == []
    assert NodeKind.REJECTED_CLAIM not in permissive.kinds and "rejected" not in permissive.statuses


def test_candidates_are_not_trusted_memory_unless_an_operator_says_so(kernel):
    commit(kernel, "candidate: the cohort prefers DIA acquisition", status="candidate")
    default = MemoryRetriever(kernel.graph)
    assert default.retrieve("cohort DIA acquisition", envelope=envelope_for(kernel)) == []
    assert default.last_trace.excluded_status == 1

    opted_in = MemoryRetriever(kernel.graph, statuses=["candidate", "verified"])
    assert len(opted_in.retrieve("cohort DIA acquisition", envelope=envelope_for(kernel))) == 1


def test_memory_above_the_run_ceiling_is_withheld_at_retrieval(kernel):
    commit(kernel, PHI_CLAIM)
    narrow = kernel.policy.envelope(project_id="proj_a").restrict(
        max_label=DataLabel(Sensitivity.RESEARCH_DEIDENTIFIED))
    retriever = MemoryRetriever(kernel.graph)
    assert retriever.retrieve("DIA proteomics", envelope=narrow) == []
    assert retriever.last_trace.withheld_ceiling == 1


def test_memory_the_destination_may_not_receive_is_withheld_not_sent(kernel):
    commit(kernel, PHI_CLAIM)
    retriever = MemoryRetriever(kernel.graph)
    assert retriever.retrieve("DIA proteomics", envelope=envelope_for(kernel),
                              destination=Destination.PUBLIC_REMOTE) == []
    assert retriever.last_trace.withheld_destination == 1
    assert len(retriever.retrieve("DIA proteomics", envelope=envelope_for(kernel),
                                  destination=Destination.LOCAL_MODEL)) == 1


def test_memory_is_scoped_to_the_project(kernel):
    commit(kernel, "decision: use DIA for the proteomics cohort", kind=NodeKind.DECISION,
           project="proj_a")
    retriever = MemoryRetriever(kernel.graph)
    assert retriever.retrieve("DIA proteomics", envelope=envelope_for(kernel, "proj_b")) == []
    assert len(retriever.retrieve("DIA proteomics", envelope=envelope_for(kernel, "proj_a"))) == 1


def test_no_project_means_no_memory_unless_cross_project_is_allowed(kernel):
    commit(kernel, "decision: use DIA", kind=NodeKind.DECISION, project="proj_a")
    scoped = MemoryRetriever(kernel.graph)
    assert scoped.retrieve("DIA", envelope=kernel.policy.envelope()) == []
    assert scoped.last_trace.no_project
    assert len(MemoryRetriever(kernel.graph, cross_project=True).retrieve(
        "DIA", envelope=kernel.policy.envelope())) == 1


def test_ranking_is_relevance_first_and_deterministic(kernel):
    commit(kernel, "decision: sequencing depth for the exome panel", kind=NodeKind.DECISION)
    commit(kernel, "decision: use DIA acquisition for the proteomics cohort",
           kind=NodeKind.DECISION)
    retriever = MemoryRetriever(kernel.graph)
    first = retriever.retrieve("proteomics acquisition", envelope=envelope_for(kernel))
    second = retriever.retrieve("proteomics acquisition", envelope=envelope_for(kernel))
    assert [i.source_ref for i in first] == [i.source_ref for i in second]
    assert "DIA acquisition" in first[0].content and first[0].score > first[1].score


def test_retrieval_reads_and_never_writes(kernel):
    commit(kernel, "decision: use DIA", kind=NodeKind.DECISION)
    before = kernel.graph.stats()
    commits = kernel.persistence.commits
    MemoryRetriever(kernel.graph).retrieve("DIA", envelope=envelope_for(kernel))
    assert kernel.graph.stats() == before and kernel.persistence.commits == commits

    tree = ast.parse(Path(memory_module.__file__).read_text())
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"add", "update", "link", "commit_node", "commit_raw", "execute"}, calls


# ============================================================ compiled into runs

def test_the_planner_sees_verified_memory_and_the_plan_carries_its_label(kernel, local_model):
    commit(kernel, "decision: use DIA acquisition for the proteomics cohort",
           kind=NodeKind.DECISION)
    served = []

    def invoke(prompt):
        served.append(prompt)
        return ('{"objective": "o", "tasks": [{"task_id": "t", "objective": "summarise", '
                '"kind": "model", "destinations": ["LOCAL_MODEL"]}], '
                '"completion_criteria": [{"description": "done", "kind": "task"}]}')

    planner = ModelPlanner(kernel, model=local_model, model_invoke=invoke,
                           memory=MemoryRetriever(kernel.graph))
    from psh.runtime import LoopState
    state = LoopState(loop_id="loop_m", objective="plan the proteomics acquisition",
                      envelope=envelope_for(kernel))
    planner.plan(state)
    assert "use DIA acquisition" in served[0]
    assert state.plan_label.sensitivity >= Sensitivity.INTERNAL


def test_a_loop_task_recalls_memory_and_the_result_label_joins_it(kernel, local_model):
    commit(kernel, PHI_CLAIM)
    served = []
    plan = plan_of(PlanTask(task_id="t1", objective="summarise the proteomics protocol result",
                            kind=TaskKind.MODEL, destinations=(Destination.LOCAL_MODEL,),
                            max_label=Sensitivity.PHI))
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), model=local_model,
                               model_invoke=lambda p: served.append(p) or "summary",
                               limits=LoopLimits(), sleep=lambda s: None,
                               memory=MemoryRetriever(kernel.graph))
    result = loop.run("do it", envelope_for(kernel))
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert MRN in served[0]
    assert result.label.sensitivity is Sensitivity.PHI


def test_withheld_memory_is_a_quieter_prompt_not_a_refusal(kernel, public_model):
    """Contrast with an upstream *result* the destination may not receive, which the loop
    refuses to run without. Memory is optional context: it is withheld and the task runs."""
    commit(kernel, PHI_CLAIM)
    served = []
    # The validator refuses a PHI ceiling at a public destination outright, so inside a
    # loop the task's ceiling withholds this memory before the destination gets to; the
    # destination path on its own is tested above at the retriever. Either way the memory
    # is withheld, the prompt is quieter, and the task runs.
    plan = plan_of(PlanTask(task_id="t1", objective="summarise the proteomics protocol result",
                            kind=TaskKind.MODEL, destinations=(Destination.PUBLIC_REMOTE,)))
    retriever = MemoryRetriever(kernel.graph)
    loop = AgentLoopController(kernel, planner=StaticPlanner(plan), model=public_model,
                               model_invoke=lambda p: served.append(p) or "summary",
                               limits=LoopLimits(), sleep=lambda s: None, memory=retriever)
    result = loop.run("do it", envelope_for(kernel))
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert MRN not in served[0]
    trace = retriever.last_trace
    assert trace.withheld_ceiling + trace.withheld_destination == 1 and trace.returned == 0


def test_the_runner_compiles_prior_memory_into_its_projection(kernel, local_model):
    runner = Runner(kernel, registry=CapabilityRegistry(), model=local_model,
                    model_invoke=lambda prompt: "An answer.",
                    memory=MemoryRetriever(kernel.graph))
    project = runner._default_project()
    kernel.persistence.commit_node(
        kind=NodeKind.KNOWLEDGE, title="the proteomics cohort uses DIA acquisition",
        principal="tester", source_run="run_earlier", project_id=project,
        validation_status="verified")
    result = runner.run("what acquisition does the proteomics cohort use?",
                        project_id=project)
    assert result.status == "ok", result.error
    memories = [i for i in result.projection.items if i.kind == "memory"]
    assert len(memories) == 1 and "DIA acquisition" in memories[0].content
