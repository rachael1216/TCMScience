"""Compiler trajectories: reject extrapolation/egress before tools execute."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from hypothesis import given, strategies as st

from psh.config import PSHConfig
from psh.contracts import Autonomy, ModelProfile
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination as D, Sensitivity as S
from psh.policy import PolicySnapshot
from psh.runtime import AgentLoopController, Criterion, Plan, PlanRejected, PlanTask, Termination
from psh.runtime.plan import RetryPolicy
from psh.workflow import (
    ClaimSpec, ClaimType, Effect, EvidenceSpec, ScientificCompiler, ScientificPlanner,
    ScientificProgram, SideEffect, TaskContract, assess_amendment,
)


def policy():
    return PolicySnapshot(profile_id="science-test", allowed_destinations=tuple(D),
                          max_data_label=S.PHI, autonomy=Autonomy.ACT,
                          require_claim_support=False)


def task(tid, dependencies=(), **kw):
    return PlanTask(task_id=tid, objective=f"research {tid}", dependencies=dependencies,
                    destinations=kw.pop("destinations", (D.LOCAL_MODEL,)),
                    max_label=kw.pop("max_label", S.PHI), **kw)


def contract(**kw):
    return TaskContract(sensitivity=kw.pop("sensitivity", S.PUBLIC),
                        effects=kw.pop("effects", (Effect.LOCAL_MODEL,)), **kw)


def program(tasks=None, contracts=None):
    tasks = tuple(tasks or [task("a")])
    return ScientificProgram(Plan("evaluate evidence", tasks=tasks,
                                   completion_criteria=(Criterion("done", "task"),)),
                             contracts or {t.task_id: contract() for t in tasks})


def compile_program(p):
    return ScientificCompiler().compile(p, policy().envelope(), policy=policy())


def rejected(p, code):
    with pytest.raises(PlanRejected) as exc:
        compile_program(p)
    assert code in {v.family for v in exc.value.violations}, str(exc.value)


def evidence_program(design="randomized_trial", kind=ClaimType.CLINICAL,
                     evidence_kw=None, claim_kw=None):
    ev = dict(design=design, population="adult cohort", intervention="intervention A",
              outcome="prespecified endpoint", provenance=("fixture:source-1",))
    ev.update(evidence_kw or {})
    claim = dict(kind=kind, population="adult cohort", intervention="intervention A",
                 outcome="prespecified endpoint", evidence_from=("source",))
    claim.update(claim_kw or {})
    return program([task("source"), task("claim", ("source",))], {
        "source": contract(evidence=EvidenceSpec(**ev)),
        "claim": contract(claim=ClaimSpec(**claim)),
    })


def test_json_roundtrip_and_plan_identity_independence():
    p = evidence_program()
    restored = ScientificProgram.from_dict(json.loads(json.dumps(p.to_dict())))
    assert restored == p
    assert compile_program(p).fingerprint == compile_program(
        replace(p, plan=replace(p.plan, plan_id="another-run"))).fingerprint


@pytest.mark.parametrize("design", ["animal", "in_vitro", "classical_text", "observational"])
def test_nonclinical_design_cannot_become_clinical_efficacy(design):
    rejected(evidence_program(design), "EVIDENCE103")


@pytest.mark.parametrize("field", ["population", "intervention", "outcome"])
def test_scope_refinement_mismatch_is_not_support(field):
    rejected(evidence_program(claim_kw={field: "different"}), "EVIDENCE104")


def test_review_of_animal_studies_cannot_launder_evidence():
    rejected(evidence_program("systematic_review", evidence_kw={
        "underlying_designs": ("animal",)}), "EVIDENCE103")
    compile_program(evidence_program("systematic_review", evidence_kw={
        "underlying_designs": ("randomized_trial",)}))


def test_classical_attribution_is_supported_by_classical_source():
    compile_program(evidence_program("classical_text", ClaimType.CLASSICAL))


def test_missing_evidence_and_non_dependency_are_rejected():
    p = evidence_program()
    rejected(replace(p, contracts={**p.contracts, "source": contract()}), "EVIDENCE102")
    claim = replace(p.contracts["claim"].claim, evidence_from=("unknown",))
    rejected(replace(p, contracts={**p.contracts, "claim": contract(claim=claim)}), "EVIDENCE101")


def test_transitive_phi_flow_cannot_be_renamed_public():
    p = program([task("source"), task("summary", ("source",)),
                 task("publish", ("summary",), destinations=(D.PUBLIC_REMOTE,),
                      max_label=S.PUBLIC)], {
        "source": contract(sensitivity=S.PHI), "summary": contract(),
        "publish": contract(effects=(Effect.PUBLIC_REMOTE,)),
    })
    rejected(p, "FLOW102")
    rejected(p, "FLOW101")


@given(st.lists(st.sampled_from(list(S)[:-1]), min_size=1, max_size=8))
def test_label_join_is_monotone_across_arbitrary_chain(labels):
    tasks = [task(str(i), (str(i-1),) if i else ()) for i in range(len(labels))]
    p = program(tasks, {str(i): contract(sensitivity=label) for i, label in enumerate(labels)})
    result = compile_program(p)
    assert [result.sensitivities[str(i)] for i in range(len(labels))] == [
        max(labels[:i+1]) for i in range(len(labels))]


def test_effects_must_match_destinations_and_be_explicit():
    rejected(program(contracts={"a": contract(effects=(Effect.PUBLIC_REMOTE,))}), "EFFECT103")
    rejected(program(contracts={"a": contract(effects=())}), "EFFECT102")
    rejected(program([task("a", destinations=())]), "EFFECT101")


@pytest.mark.parametrize("side_effect", [SideEffect.AT_MOST_ONCE, SideEffect.NON_REPEATABLE,
                                        SideEffect.COMPENSATABLE])
def test_unsafe_retry_is_rejected(side_effect):
    rejected(program([task("a", retry=RetryPolicy(max_attempts=2))],
                     {"a": contract(side_effect=side_effect)}), "RETRY101")


def test_pure_cannot_mean_remote_publish():
    rejected(program([task("a", destinations=(D.PUBLIC_REMOTE,), max_label=S.PUBLIC)],
                     {"a": contract(effects=(Effect.PUBLIC_REMOTE,), side_effect=SideEffect.PURE)}),
             "EFFECT105")


def test_authority_is_still_validated_by_existing_kernel_rules():
    p = program([task("a", destinations=(D.PUBLIC_REMOTE,), max_label=S.PUBLIC)],
                {"a": contract(effects=(Effect.PUBLIC_REMOTE,))})
    local = replace(policy(), allowed_destinations=(D.LOCAL_MODEL,))
    with pytest.raises(PlanRejected):
        ScientificCompiler().compile(p, local.envelope())


def test_missing_dependencies_and_cycles_still_rejected():
    rejected(program([task("a", ("missing",))]), "graph")
    rejected(program([task("a", ("b",)), task("b", ("a",))]), "graph")


def test_amendment_invalidates_downstream_and_preserves_independent_candidate():
    p = program([task("raw"), task("analysis", ("raw",)), task("finding", ("analysis",)),
                 task("independent")])
    p = replace(p, contracts={k: contract(side_effect=SideEffect.PURE) for k in p.contracts})
    changed = replace(p.plan.tasks[1], payload={"threshold": 0.01})
    new = replace(p, plan=replace(p.plan, tasks=(p.plan.tasks[0], changed, *p.plan.tasks[2:])))
    delta = assess_amendment(p, new, policy().envelope(), completed=p.contracts)
    assert set(delta.invalidated) == {"analysis", "finding"}
    assert set(delta.reusable_candidates) == {"raw", "independent"}


def test_changed_scientific_contract_invalidates_its_dependents():
    p = evidence_program()
    new = replace(p, contracts={**p.contracts, "source": replace(p.contracts["source"],
        evidence=replace(p.contracts["source"].evidence, provenance=("fixture:source-2",)))})
    assert set(assess_amendment(p, new, policy().envelope()).invalidated) == {"source", "claim"}


def test_changed_objective_invalidates_everything_and_removals_are_reported():
    p = program([task("a"), task("b")])
    new = replace(p, plan=replace(p.plan, objective="new question", tasks=(p.plan.tasks[0],)),
                  contracts={"a": p.contracts["a"]})
    delta = assess_amendment(p, new, policy().envelope(), completed=("a", "b"))
    assert delta.invalidated == ("a",)
    assert delta.removed == ("b",)
    assert delta.reusable_candidates == ()


def test_effectful_results_are_never_reuse_candidates():
    p = program()
    assert not assess_amendment(p, p, policy().envelope(), completed=("a",)).reusable_candidates
    with pytest.raises(ValueError, match="unknown task"):
        assess_amendment(p, p, policy().envelope(), completed=("unknown",))


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_unknown_schema_versions_are_rejected(version):
    with pytest.raises(ValueError):
        ScientificProgram.from_dict({**program().to_dict(), "schema_version": version})


def test_contract_typos_and_unknown_enums_do_not_silently_disappear():
    with pytest.raises(TypeError):
        TaskContract.from_dict({"efects": ["public_remote"]})
    with pytest.raises(ValueError):
        TaskContract.from_dict({"effects": ["publish_anywhere"]})
    with pytest.raises(ValueError):
        replace(program(), contracts={})
    with pytest.raises(ValueError):
        EvidenceSpec("systematic_review", "p", "i", "o", ("ref",))


def test_nonfinite_payload_is_rejected():
    with pytest.raises(ValueError):
        compile_program(program([task("a", payload={"x": float("nan")})]))


def test_negative_resource_estimates_cannot_offset_other_tasks():
    rejected(program([task("a", estimated_usd=-10)]), "RESOURCE101")


def test_sensitive_objective_blocks_public_model_before_execution():
    p = program([task("a", destinations=(D.PUBLIC_REMOTE,), max_label=S.PUBLIC)],
                {"a": contract(effects=(Effect.PUBLIC_REMOTE,))})
    with pytest.raises(PlanRejected, match="FLOW102"):
        ScientificPlanner(p).plan(SimpleNamespace(envelope=policy().envelope(),
                                                  objective_label=DataLabel(S.PHI)))


def test_unknown_plan_fields_and_string_arrays_are_rejected():
    body = program().to_dict()
    body["plan"]["tasks"][0]["max_lable"] = "PHI"
    with pytest.raises(ValueError, match="unknown PlanTask"):
        ScientificProgram.from_dict(body)
    body = evidence_program().to_dict()
    body["contracts"]["source"]["evidence"]["provenance"] = "not-an-array"
    with pytest.raises(ValueError, match="array"):
        ScientificProgram.from_dict(body)


def test_planner_snapshots_nested_payloads():
    data = {"nested": {"values": [1]}}
    p = program([task("a", payload=data)])
    planner = ScientificPlanner(p)
    data["nested"]["values"].append(2)
    result = planner.plan(SimpleNamespace(envelope=policy().envelope()))
    assert result.tasks[0].payload["nested"]["values"] == [1]


def test_scientific_planner_executes_through_existing_broker(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "kernel").ensure_dirs(), policy=policy())
    try:
        p = program(contracts={"a": contract(sensitivity=S.PHI)})
        model = ModelProfile(id="fixture", provider="local", destination=D.LOCAL_MODEL,
                             max_label=S.PHI)
        loop = AgentLoopController(kernel, planner=ScientificPlanner(p, policy=kernel.policy),
                                   model=model, model_invoke=lambda _: "analysis complete")
        result = loop.run(p.plan.objective, kernel.policy.envelope())
        assert result.termination == Termination.GOAL_SATISFIED, result.summary()
        assert kernel.broker.stats()["model_calls"] == 1
        assert result.label.sensitivity == S.PHI
        assert Plan.from_dict(result.plan.to_dict()).tasks[0].input_sensitivity == S.PHI
    finally:
        kernel.close()


def test_compiled_label_survives_tool_dispatch(tmp_path):
    from psh.capabilities import CapabilityRegistry
    from psh.contracts import ComponentKind, ComponentManifest

    class LocalTool:
        manifest = ComponentManifest(id="count", name="count", kind=ComponentKind.TOOL,
                                     max_label=S.PHI)

        def invoke(self, payload, envelope):
            return {"count": 7}

    registry = CapabilityRegistry()
    registry.register(LocalTool())
    p = program([task("a", kind="tool", component_id="count", destinations=(D.LOCAL_COMPUTE,))],
                {"a": contract(sensitivity=S.PHI, effects=(Effect.LOCAL_COMPUTE,))})
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "kernel").ensure_dirs(), policy=policy())
    try:
        loop = AgentLoopController(kernel, planner=ScientificPlanner(p, registry=registry),
                                   registry=registry)
        result = loop.run(p.plan.objective, kernel.policy.envelope())
        assert result.termination == Termination.GOAL_SATISFIED, result.summary()
        assert result.label.sensitivity == S.PHI
    finally:
        kernel.close()


def test_checkpoint_shape_cannot_lower_known_sensitivity():
    from psh.runtime.checkpoint import plan_shape
    p = compile_program(program(contracts={"a": contract(sensitivity=S.PHI)})).plan
    body = p.to_dict()
    body["tasks"][0]["input_sensitivity"] = "PUBLIC"
    assert plan_shape(body) != plan_shape(p.to_dict())


def test_bad_scientific_plan_stops_before_first_model_call(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "kernel").ensure_dirs(), policy=policy())
    try:
        p = evidence_program("animal")
        calls = []
        loop = AgentLoopController(kernel, planner=ScientificPlanner(p),
                                   model_invoke=lambda prompt: calls.append(prompt) or "answer")
        result = loop.run(p.plan.objective, kernel.policy.envelope())
        assert result.termination == Termination.PLAN_REJECTED
        assert calls == []
        assert kernel.broker.stats()["model_calls"] == 0
    finally:
        kernel.close()
