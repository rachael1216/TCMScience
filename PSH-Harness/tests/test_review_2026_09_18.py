"""Regressions for the 2026-09-18 architecture review — the P0 batch.

The review probed the merged tree with synthetic data and found four P0 defects:

F01  A loop's result never met the release gate. ``Runner.run`` quarantined, verified and
     gated its output; ``AgentLoopController.run`` returned the raw graph, so the same
     policy gave the same sentence two verdicts depending on the entrance.
F02  ``Runner`` re-classified the model's output text alone, dropping the projection's
     label: a count derived from a PHI chart left the run as INTERNAL.
F03  A checkpoint withheld task *results* for a run without ``PERSISTENT`` and wrote the
     objective, the task objectives, the payloads and the error texts in full.
F04  The fallback classifier knew no Chinese cues: 患者张三，住院号：12345678 was INTERNAL,
     which sits below the public-remote ceiling.

Two smaller findings landed with the batch: the loop's bounds check consumed a model call
(F06B), and the schema check accepted a boolean as an integer while a manual criterion
answered "banana" counted as a satisfied goal (F07).

Every scenario below was run against the merged tree first, with the probe under
``scratchpad/review``, and failed there for the reviewer's reason.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, BudgetExhausted, ComponentKind, ComponentManifest, ModelProfile,
    PolicyDenied, RiskTier,
)
from psh.evidence import EvidenceRecord
from psh.kernel import TrustedKernel
from psh.kernel.classify import Classifier
from psh.labels import Destination, Sensitivity, unwrap
from psh.policy import PolicyLattice, PolicySnapshot
from psh.runtime import (
    AgentLoopController, BindingError, CheckpointStore, Criterion, Finalizer, InputBinding,
    LoopLimits, Plan, PlanRejected, PlanTask, PlanValidator, ResearchRunService, ResumeRefused,
    Runner, StaticPlanner, TaskKind, TaskState, Termination, render_deliverable,
    resolve_bindings, resolve_pointer, resume,
)
from psh.runtime.evaluator import check_output_schema

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
PHI_TEXT = "Patient Alice Smith MRN 04851923 admitted with chest pain"
FABRICATED = "Drug X cures every cancer [PMID:99999999]."
SUPPORTED = "Empagliflozin may reduce heart-failure hospitalization (PMID: 34449189)."
ABSTRACT = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction. The "
    "primary outcome occurred in 415 of 2997 patients in the empagliflozin group and in 511 "
    "of 2991 patients in the placebo group (hazard ratio, 0.79; 95% confidence interval, "
    "0.69 to 0.90; P<0.001).")

LOCAL_MODEL = ModelProfile(id="local-8b", provider="local", destination=Destination.LOCAL_MODEL,
                           max_label=Sensitivity.PHI, usd_per_1k_input=0.0,
                           usd_per_1k_output=0.0)


def policy(**kw):
    params = dict(profile_id="review_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=LOCAL, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


def kernel_with(tmp_path, name="k", **kw):
    return TrustedKernel(PSHConfig(state_dir=tmp_path / name).ensure_dirs(), policy=policy(**kw))


class Tool:
    """A component that records what it was asked to do."""

    def __init__(self, component_id="probe", result=None, error=None, **manifest_kw):
        params = dict(id=component_id, name=component_id, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI, risk_tier=RiskTier.R1_ROUTINE)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.calls: list = []
        self._result = result if result is not None else {"ok": True}
        self._error = error

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return self._result


def registry_of(*tools):
    registry = CapabilityRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def loop_for(kernel, plan, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("limits", LoopLimits())
    return AgentLoopController(kernel, planner=StaticPlanner(plan), **kw)


def evidence_plan(*, criteria=(Criterion(description="both tasks ran", kind="task"),)):
    """A plan the strict policy accepts: a tool gathers evidence, a model states the finding."""
    return Plan(
        objective="state the finding", produced_by="test",
        tasks=(PlanTask(task_id="gather", objective="gather evidence", kind=TaskKind.TOOL,
                        component_id="gather", evidence_required=True,
                        output_schema={"type": "object", "required": ["evidence"]}),
               PlanTask(task_id="state", objective="state the finding", kind=TaskKind.MODEL,
                        dependencies=("gather",), destinations=(Destination.LOCAL_MODEL,))),
        completion_criteria=tuple(criteria))


def model_plan(answer_objective="state the finding", *, criteria=("done",), kinds=("manual",)):
    return Plan(
        objective=answer_objective, produced_by="test",
        tasks=(PlanTask(task_id="state", objective=answer_objective, kind=TaskKind.MODEL,
                        destinations=(Destination.LOCAL_MODEL,)),),
        completion_criteria=tuple(Criterion(description=c, kind=k)
                                  for c, k in zip(criteria, kinds)))


def signed_source(kernel):
    record = EvidenceRecord.from_text(identifier="34449189", text=ABSTRACT,
                                      retrieved_by="sable.pubmed_fetch", retrieval_run="r0",
                                      retracted=False)
    return {"34449189": kernel.evidence_signer.sign(record)}


def text_fields(result) -> list[str]:
    """Every string anywhere in a released-result DTO, for "carries no body" checks."""
    out: list[str] = []

    def walk(value):
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple, set)):
            for v in value:
                walk(v)

    walk(dataclasses.asdict(result))
    return out


# ================================================================ F01: one release path

def test_f01_evidence_requirement_strings_no_longer_satisfy_the_validator(tmp_path):
    """Under claim support, a plan needs a task that declares evidence_required."""
    strict = policy(require_citation=True, require_claim_support=True)
    envelope = strict.envelope()
    worded = Plan(objective="summarise", produced_by="test",
                  tasks=(PlanTask(task_id="t1", objective="state the finding",
                                  kind=TaskKind.MODEL, destinations=(Destination.LOCAL_MODEL,)),),
                  completion_criteria=(Criterion(description="done"),),
                  evidence_requirements=("every claim cited",))
    with pytest.raises(PlanRejected) as info:
        PlanValidator().validate(worded, envelope, policy=strict)
    assert "evidence_required" in str(info.value)

    validated = PlanValidator().validate(evidence_plan(), envelope, policy=strict)
    assert validated is not None


def test_f01_a_fabricated_citation_is_refused_by_loop_and_runner_alike(tmp_path):
    """The reviewer's sentence, through both entrances, under one strict policy."""
    kernel = kernel_with(tmp_path, require_citation=True, require_claim_support=True)
    registry = registry_of(Tool("gather", result={"evidence": ["PMID:99999999"]}))
    envelope = kernel.policy.envelope()

    loop = loop_for(kernel, evidence_plan(), registry=registry, model=LOCAL_MODEL,
                    model_invoke=lambda prompt: FABRICATED)
    internal = loop.run("state the finding", envelope)
    # The loop finishes: execution succeeded and the deterministic criterion held. That is
    # the internal verdict, and it is not a release.
    assert internal.termination is Termination.GOAL_SATISFIED, internal.summary()
    assert unwrap(internal.results["state"]) == FABRICATED
    assert render_deliverable(internal) == FABRICATED

    released = Finalizer(kernel).finalize(internal, envelope, sources={})
    assert released.status == "refused" and not released.ok
    assert released.refused_at == "release_gate"
    assert released.refusal_kind == "VerificationFailed"
    assert released.released_output is None
    assert released.claims_checked == 1 and released.claims_unsupported == 1
    assert released.citations == ("99999999",)

    runner = Runner(kernel, model=LOCAL_MODEL, model_invoke=lambda prompt: FABRICATED)
    single = runner.run("state the finding", sources={})
    assert single.status == "refused" and single.released_output is None
    assert (released.status, released.refused_at) == (single.status, single.refused_at)


def test_f01_a_refused_result_carries_counts_and_no_text(tmp_path):
    kernel = kernel_with(tmp_path, require_citation=True, require_claim_support=True)
    registry = registry_of(Tool("gather", result={"evidence": ["PMID:99999999"]}))
    envelope = kernel.policy.envelope()
    internal = loop_for(kernel, evidence_plan(), registry=registry, model=LOCAL_MODEL,
                        model_invoke=lambda prompt: FABRICATED).run("state the finding", envelope)
    released = Finalizer(kernel).finalize(internal, envelope)
    assert released.status == "refused"
    for text in text_fields(released):
        assert "Drug X" not in text and "cures" not in text, text
    assert released.quarantine_ref, "the refused text stays addressable for audit"
    assert kernel.quarantine.refused >= 1 and kernel.quarantine.released == 0


def test_f01_the_service_hands_back_only_released_results(tmp_path):
    kernel = kernel_with(tmp_path, require_citation=True, require_claim_support=True)
    registry = registry_of(Tool("gather", result={"evidence": ["PMID:99999999"]}))
    service = ResearchRunService(kernel, planner=StaticPlanner(evidence_plan()),
                                 registry=registry, model=LOCAL_MODEL,
                                 model_invoke=lambda prompt: FABRICATED, limits=LoopLimits())
    released = service.run("state the finding")
    assert released.status == "refused" and released.released_output is None
    # The internal result exists for diagnostics and is not what the caller received.
    assert service.last_loop_result is not None
    assert service.last_loop_result.termination is Termination.GOAL_SATISFIED
    assert service.finalizer.finalized == 1


def test_f01_a_supported_claim_is_released_and_committed(tmp_path):
    kernel = kernel_with(tmp_path, require_citation=True, require_claim_support=True)
    registry = registry_of(Tool("gather", result={"evidence": ["PMID:34449189"]}))
    envelope = kernel.policy.envelope(project_id="proj-1")
    internal = loop_for(kernel, evidence_plan(), registry=registry, model=LOCAL_MODEL,
                        model_invoke=lambda prompt: SUPPORTED).run("state the finding", envelope)
    assert internal.termination is Termination.GOAL_SATISFIED, internal.summary()

    released = Finalizer(kernel).finalize(internal, envelope, sources=signed_source(kernel),
                                          project_id="proj-1")
    assert released.status == "released" and released.ok, released.summary()
    assert released.released_output == SUPPORTED
    assert released.goal_status == "verified"
    assert released.claims_checked == 1 and released.claims_unsupported == 0
    assert released.committed_claims == 1, "verified claims enter project memory on release"
    assert kernel.quarantine.released == 1 and kernel.quarantine.refused == 0


def test_f01_release_needs_the_user_output_destination(tmp_path):
    """A run that may not reach USER_OUTPUT gets a refusal, not the graph."""
    kernel = kernel_with(tmp_path, allowed_destinations=(
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.PERSISTENT))
    envelope = kernel.policy.envelope()
    internal = loop_for(kernel, model_plan(), model=LOCAL_MODEL,
                        model_invoke=lambda prompt: "TP53 encodes p53.").run("gene", envelope)
    assert internal.termination is Termination.GOAL_SATISFIED, internal.summary()
    released = Finalizer(kernel).finalize(internal, envelope)
    assert released.status == "refused"
    assert released.refused_at == "release_gate"
    assert released.refusal_kind == "EgressDenied"
    assert released.released_output is None


def test_f01_a_loop_that_did_not_finish_releases_nothing(tmp_path):
    kernel = kernel_with(tmp_path)
    registry = registry_of(Tool("broken", error=RuntimeError("the tool died")))
    plan = Plan(objective="run the tool", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="run the tool", kind=TaskKind.TOOL,
                                component_id="broken"),),
                completion_criteria=(Criterion(description="ran", kind="task"),))
    envelope = kernel.policy.envelope()
    internal = loop_for(kernel, plan, registry=registry).run("run the tool", envelope)
    assert internal.termination is not Termination.GOAL_SATISFIED
    released = Finalizer(kernel).finalize(internal, envelope)
    assert released.status == "not_completed" and released.refused_at == "execution"
    assert released.released_output is None
    assert released.refusal_kind == internal.termination.value
    assert kernel.output_gate.checks == 0, "nothing reached the gate"


# ============================================================ F02: labels are inherited

def test_f02_the_gate_judges_the_output_under_the_projection_label(tmp_path):
    """A count derived from a PHI chart is PHI at the gate, whatever its text scans as."""
    kernel = kernel_with(tmp_path)
    runner = Runner(kernel, model=LOCAL_MODEL, model_invoke=lambda prompt: "The cohort count is 17.")
    result = runner.run(f"{PHI_TEXT}. How many patients are in this cohort?")
    assert result.status == "ok", result.error
    assert result.projection.label.sensitivity is Sensitivity.PHI
    # The text alone scans as INTERNAL; the gate must have seen the join.
    assert kernel.classifier.classify_text("The cohort count is 17.").sensitivity \
        is Sensitivity.INTERNAL
    assert kernel.output_gate.verdicts[-1].label.sensitivity is Sensitivity.PHI


def test_f02_the_quarantined_copy_carries_the_inherited_label(tmp_path):
    kernel = kernel_with(tmp_path)
    held = []
    original = kernel.quarantine.hold

    def spy(content, *, label, run_id=""):
        held.append(label)
        return original(content, label=label, run_id=run_id)

    kernel.quarantine.hold = spy
    Runner(kernel, model=LOCAL_MODEL, model_invoke=lambda prompt: "17").run(
        f"{PHI_TEXT}. Count the admissions.")
    assert held and held[-1].sensitivity is Sensitivity.PHI


# ======================================================= F03: checkpoints are durable writes

def no_persist_kernel(tmp_path, name="k"):
    return kernel_with(tmp_path, name, allowed_destinations=(
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT))


def phi_plan(error=None):
    return Plan(objective=PHI_TEXT, produced_by="test",
                tasks=(PlanTask(task_id="t1", objective=f"count admissions for {PHI_TEXT}",
                                kind=TaskKind.TOOL, component_id="count",
                                payload={"mrn": "04851923"}, max_label=Sensitivity.PHI),),
                completion_criteria=(Criterion(description="counted", kind="task"),))


def test_f03_a_run_that_may_not_persist_writes_no_task_text(tmp_path):
    kernel = no_persist_kernel(tmp_path)
    store = CheckpointStore(tmp_path / "ckpt")
    tool = Tool("count", result={"admissions": 3})
    result = loop_for(kernel, phi_plan(), registry=registry_of(tool), checkpoints=store).run(
        PHI_TEXT, kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()

    files = sorted((tmp_path / "ckpt").glob("*.json"))
    assert files, "the loop still checkpoints: progress survives, the words do not"
    for path in files:
        raw = path.read_text()
        for secret in ("04851923", "Alice", "chest pain", "admissions"):
            assert secret not in raw, f"{path.name} carries {secret!r}"

    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.redacted is True
    assert checkpoint.objective == ""
    assert checkpoint.plan["objective"] == "withheld"
    task = checkpoint.plan["tasks"][0]
    assert task["objective"] == "withheld" and task["payload"] == {}
    assert task["task_id"] == "t1" and task["component_id"] == "count"
    assert {"objective", "plan_bodies", "errors", "t1"} <= set(checkpoint.withheld)
    assert checkpoint.results == {}
    assert checkpoint.usage.get("tool_calls") == 1, "consumption is recorded for resume"
    assert checkpoint.intact


def test_f03_a_run_that_may_persist_still_records_its_words(tmp_path):
    kernel = kernel_with(tmp_path)
    store = CheckpointStore(tmp_path / "ckpt")
    plan = Plan(objective="count the cohort", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="count", kind=TaskKind.TOOL,
                                component_id="count", payload={"cohort": "hfpef"}),),
                completion_criteria=(Criterion(description="counted", kind="task"),))
    result = loop_for(kernel, plan, registry=registry_of(Tool("count", result={"n": 3})),
                      checkpoints=store).run("count the cohort", kernel.policy.envelope())
    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.redacted is False
    assert checkpoint.objective == "count the cohort"
    assert checkpoint.plan["tasks"][0]["payload"] == {"cohort": "hfpef"}
    assert checkpoint.results == {"t1": {"n": 3}}


def test_f03_resuming_a_redacted_checkpoint_needs_the_words_again(tmp_path):
    kernel = no_persist_kernel(tmp_path)
    store = CheckpointStore(tmp_path / "ckpt")
    tool = Tool("count", result={"admissions": 3})
    plan = phi_plan()
    result = loop_for(kernel, plan, registry=registry_of(tool), checkpoints=store).run(
        PHI_TEXT, kernel.policy.envelope())
    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.redacted

    with pytest.raises(ResumeRefused, match="objective and the plan"):
        resume(checkpoint, kernel)
    with pytest.raises(ResumeRefused, match="objective and the plan"):
        resume(checkpoint, kernel, objective=PHI_TEXT)

    wrong_shape = Plan(objective=PHI_TEXT, produced_by="test",
                       tasks=(PlanTask(task_id="t1", objective="count", kind=TaskKind.TOOL,
                                       component_id="other"),),
                       completion_criteria=(Criterion(description="counted", kind="task"),))
    with pytest.raises(ResumeRefused, match="shape"):
        resume(checkpoint, kernel, objective=PHI_TEXT, plan=wrong_shape)

    state = resume(checkpoint, kernel, objective=PHI_TEXT, plan=plan)
    assert state.objective == PHI_TEXT
    assert state.graph.nodes["t1"].state is TaskState.RETRYABLE, \
        "a withheld result is unknown here, so the task runs again"
    resumed = loop_for(kernel, plan, registry=registry_of(tool), checkpoints=store).run(
        PHI_TEXT, state.envelope, resume_from=state)
    assert resumed.termination is Termination.GOAL_SATISFIED, resumed.summary()
    assert len(tool.calls) == 2


def test_f03_a_plain_checkpoint_refuses_supplied_words(tmp_path):
    kernel = kernel_with(tmp_path)
    store = CheckpointStore(tmp_path / "ckpt")
    plan = Plan(objective="count", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="count", kind=TaskKind.TOOL,
                                component_id="count"),),
                completion_criteria=(Criterion(description="counted", kind="task"),))
    result = loop_for(kernel, plan, registry=registry_of(Tool("count")), checkpoints=store).run(
        "count", kernel.policy.envelope())
    checkpoint = store.latest_for(result.loop_id)
    assert not checkpoint.redacted
    with pytest.raises(ResumeRefused, match="redacted"):
        resume(checkpoint, kernel, objective="count", plan=plan)


def test_f03_error_text_is_reduced_to_its_class_when_redacted(tmp_path):
    kernel = no_persist_kernel(tmp_path)
    store = CheckpointStore(tmp_path / "ckpt")
    tool = Tool("count", error=RuntimeError(f"lookup failed for {PHI_TEXT}"))
    result = loop_for(kernel, phi_plan(), registry=registry_of(tool), checkpoints=store).run(
        PHI_TEXT, kernel.policy.envelope())
    assert result.termination is not Termination.GOAL_SATISFIED
    for path in (tmp_path / "ckpt").glob("*.json"):
        assert "Alice" not in path.read_text() and "04851923" not in path.read_text()
    checkpoint = store.latest_for(result.loop_id)
    recorded = checkpoint.task_states["t1"]["error"]
    assert recorded and "Alice" not in recorded
    assert recorded.split()[0].rstrip(":") == "RuntimeError"


# ============================================================ F04: Chinese PHI cues

@pytest.fixture
def fallback():
    classifier = Classifier(prefer_sable=False)
    assert not classifier.validated and classifier.detector_name == "psh.fallback"
    return classifier


@pytest.mark.parametrize("text, category", [
    ("患者张三，住院号：12345678，出生日期：1980-01-01", "chinese_record_number"),
    ("患者张三，住院号：12345678，出生日期：1980-01-01", "chinese_patient_name"),
    ("患者张三，住院号：12345678，出生日期：1980-01-01", "chinese_date_of_birth"),
    ("患者姓名：李四，联系电话：13812345678", "chinese_mobile_number"),
    ("身份证号：110101198001011234", "chinese_resident_id"),
    ("门诊号 20230456 王五 男 45岁", "chinese_record_number"),
    ("家庭住址：上海市浦东新区张江路100号", "chinese_address"),
    ("Patient name: 王小明, admitted yesterday", "name_cued_cjk"),
])
def test_f04_chinese_clinical_identifiers_are_phi_under_the_fallback(fallback, text, category):
    verdict = fallback.classify_text(text)
    assert verdict.sensitivity is Sensitivity.PHI, (text, verdict.categories)
    assert category in verdict.categories
    assert not verdict.label.shareable


@pytest.mark.parametrize("text", [
    "患者教育材料应当通俗易懂",
    "本研究纳入120例患者",
    "黄芪注射液治疗心力衰竭的随机对照试验",
    "住院患者跌倒风险评估",
    "剂量为10mg，每日两次，共30天",
])
def test_f04_chinese_research_prose_is_not_flagged(fallback, text):
    verdict = fallback.classify_text(text)
    assert verdict.sensitivity is Sensitivity.INTERNAL, (text, verdict.categories)
    assert verdict.categories == ()


def test_f04_english_cues_still_hold(fallback):
    assert fallback.classify_text(PHI_TEXT).sensitivity is Sensitivity.PHI


def test_f04_a_clinical_origin_floors_the_label_at_phi(fallback):
    """Provenance is a stronger fact than a regex miss."""
    verdict = fallback.classify_text("随访记录：无明显不适", origin="ehr:export")
    assert verdict.sensitivity is Sensitivity.PHI
    assert "clinical_source" in verdict.categories
    assert fallback.classify_text("随访记录：无明显不适").sensitivity is Sensitivity.INTERNAL
    for origin in ("clinical", "ehr:export", "emr_dump", "chart/2024", "patient_record:x"):
        assert Classifier.is_clinical_origin(origin), origin
    for origin in ("public_source", "model_output", "clinically_reviewed", ""):
        assert not Classifier.is_clinical_origin(origin), origin


def test_f04_a_policy_may_require_the_validated_detector(tmp_path, monkeypatch):
    monkeypatch.setattr("psh.kernel.classify.SABLE_AVAILABLE", False)
    config = PSHConfig(state_dir=tmp_path / "strict").ensure_dirs()
    with pytest.raises(PolicyDenied, match="validated PHI detector"):
        TrustedKernel(config, policy=policy(require_validated_classifier=True))
    # Off by default, so existing deployments keep working and say what they run on.
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "lax").ensure_dirs(), policy=policy())
    assert kernel.classifier.detector_name == "psh.fallback"


def test_f04_the_requirement_is_a_lattice_dimension():
    parent = policy(require_validated_classifier=True)
    child = policy(require_validated_classifier=False)
    dimensions = [v.dimension for v in PolicyLattice.violations(child, parent)]
    assert "require_validated_classifier" in dimensions
    assert PolicyLattice.violations(parent, child) == []
    assert parent.as_dict()["require_validated_classifier"] is True


def test_f04_the_fallback_warns_when_phi_may_travel_remotely(tmp_path, monkeypatch):
    monkeypatch.setattr("psh.kernel.classify.SABLE_AVAILABLE", False)
    warnings: list[str] = []
    TrustedKernel(PSHConfig(state_dir=tmp_path / "remote").ensure_dirs(),
                  policy=policy(allowed_destinations=LOCAL + (Destination.PUBLIC_REMOTE,)),
                  on_warning=warnings.append)
    assert any("fallback" in w for w in warnings), warnings

    quiet: list[str] = []
    TrustedKernel(PSHConfig(state_dir=tmp_path / "local").ensure_dirs(), policy=policy(),
                  on_warning=quiet.append)
    assert not any("fallback" in w for w in quiet), quiet


# ======================================================== F06B: a bounds check reserves nothing

def test_f06b_two_tool_steps_run_under_a_single_model_call_ceiling(tmp_path):
    kernel = kernel_with(tmp_path, budget=Budget(max_model_calls=1))
    tool = Tool("t")
    plan = Plan(objective="tools", produced_by="test",
                tasks=(PlanTask(task_id="s0", objective="s0", kind=TaskKind.TOOL, component_id="t"),
                       PlanTask(task_id="s1", objective="s1", kind=TaskKind.TOOL, component_id="t",
                                dependencies=("s0",))),
                completion_criteria=(Criterion(description="both", kind="task"),))
    result = loop_for(kernel, plan, registry=registry_of(tool)).run("tools", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert len(tool.calls) == 2
    assert kernel.broker.stats()["model_calls"] == 0


def test_f06b_peek_raises_at_the_ceiling_without_consuming(tmp_path):
    kernel = kernel_with(tmp_path, budget=Budget(max_model_calls=1))
    envelope = kernel.policy.envelope()
    kernel.budget.peek_model_call(envelope)
    assert kernel.budget.snapshot(envelope).model_calls == 0
    kernel.budget.check_model_call(envelope)
    with pytest.raises(BudgetExhausted):
        kernel.budget.peek_model_call(envelope)
    assert kernel.budget.snapshot(envelope).model_calls == 1


# ============================================================ F07: strict evaluation

@pytest.mark.parametrize("value, schema, ok", [
    (True, {"type": "integer"}, False),
    (1, {"type": "integer"}, True),
    (1.0, {"type": "integer"}, True),
    (1.5, {"type": "integer"}, False),
    (float("nan"), {"type": "number"}, False),
    (-5, {"type": "integer", "minimum": 0}, False),
    (0, {"type": "integer", "minimum": 0}, True),
    (5, {"type": "integer", "exclusiveMaximum": 5}, False),
    (["a"], {"type": "array", "items": {"type": "integer"}}, False),
    ([1, 2], {"type": "array", "items": {"type": "integer"}, "minItems": 2}, True),
    ([1, 1], {"type": "array", "uniqueItems": True}, False),
    ({"a": 1, "b": 2}, {"type": "object", "properties": {"a": {"type": "integer"}},
                        "additionalProperties": False}, False),
    ({"a": 1}, {"type": "object", "required": ["a", "b"]}, False),
    ("x", {"type": "string", "enum": ["y", "z"]}, False),
    ("HGNC:11998", {"type": "string", "pattern": r"^HGNC:\d+$"}, True),
    ("hgnc 11998", {"type": "string", "pattern": r"^HGNC:\d+$"}, False),
    ("", {"type": "string", "minLength": 1}, False),
    (None, {"type": ["string", "null"]}, True),
    (3, {"anyOf": [{"type": "string"}, {"type": "integer"}]}, True),
    (3, {"not": {"type": "integer"}}, False),
])
def test_f07_schema_checks_are_strict(value, schema, ok):
    assert check_output_schema(value, schema).ok is ok


def test_f07_an_unknown_schema_keyword_is_refused_not_ignored():
    verdict = check_output_schema(-1, {"type": "integer", "minimun": 0})
    assert not verdict.ok and "minimun" in verdict.detail


def test_f07_an_unjudged_manual_criterion_is_pending_not_verified(tmp_path):
    """"banana" against "the answer names both identifiers" is not a verified goal."""
    kernel = kernel_with(tmp_path)
    envelope = kernel.policy.envelope()
    plan = model_plan("return the HGNC ID and UniProt accession for TP53",
                      criteria=("the answer names both identifiers",))
    internal = loop_for(kernel, plan, model=LOCAL_MODEL,
                        model_invoke=lambda prompt: "banana").run("ids", envelope)
    assert internal.termination is Termination.GOAL_SATISFIED
    assert internal.goal_status == "pending_manual"
    assert "await a judge" in internal.reason

    lax = Finalizer(kernel).finalize(internal, envelope)
    assert lax.status == "released" and lax.goal_status == "pending_manual"
    assert any("pending_manual" in l for l in lax.limitations)

    strict = Finalizer(kernel).finalize(internal, kernel.policy.envelope(),
                                        require_goal_verification=True)
    assert strict.status == "refused"
    assert strict.refused_at == "goal_verification" and strict.refusal_kind == "GoalUnverified"
    assert strict.released_output is None


def test_f07_a_deterministic_criterion_verifies_the_goal(tmp_path):
    kernel = kernel_with(tmp_path)
    envelope = kernel.policy.envelope()
    plan = model_plan("state the gene", criteria=("the task ran",), kinds=("task",))
    internal = loop_for(kernel, plan, model=LOCAL_MODEL,
                        model_invoke=lambda prompt: "TP53").run("gene", envelope)
    assert internal.goal_status == "verified"
    released = Finalizer(kernel).finalize(internal, envelope)
    assert released.status == "released" and released.limitations == ()


def test_f07_a_strict_policy_refuses_an_unverified_goal_before_the_gate(tmp_path):
    kernel = kernel_with(tmp_path, require_citation=True, require_claim_support=True)
    registry = registry_of(Tool("gather", result={"evidence": ["PMID:34449189"]}))
    envelope = kernel.policy.envelope()
    plan = evidence_plan(criteria=(Criterion(description="a physician agrees"),))
    internal = loop_for(kernel, plan, registry=registry, model=LOCAL_MODEL,
                        model_invoke=lambda prompt: SUPPORTED).run("state the finding", envelope)
    assert internal.termination is Termination.GOAL_SATISFIED, internal.summary()
    assert internal.goal_status == "pending_manual"
    released = Finalizer(kernel).finalize(internal, envelope, sources=signed_source(kernel))
    assert released.status == "refused" and released.refused_at == "goal_verification"
    assert kernel.output_gate.checks == 0, "an unverified goal never reaches the gate"


# ================================================== F06A: a run's ledger includes its tasks

def priced_invoke(cost: float):
    from psh.kernel.results import ModelCallResult, ModelUsage

    def invoke(prompt):
        return ModelCallResult(content="x", model_id="local-8b", provider="local",
                               usage=ModelUsage(input_tokens=1, output_tokens=1,
                                                cost_usd=cost))
    return invoke


def two_model_tasks():
    return Plan(objective="two", produced_by="test",
                tasks=(PlanTask(task_id="a", objective="a", kind=TaskKind.MODEL,
                                destinations=(Destination.LOCAL_MODEL,)),
                       PlanTask(task_id="b", objective="b", kind=TaskKind.MODEL,
                                destinations=(Destination.LOCAL_MODEL,), dependencies=("a",))),
                completion_criteria=(Criterion(description="both", kind="task"),))


def test_f06a_a_task_charges_the_run_that_owns_it(tmp_path):
    """Two $0.60 tasks under a $1.00 run: the run stops, and its ledger says why."""
    kernel = kernel_with(tmp_path, budget=Budget(usd_soft=0.5, usd_hard=1.0))
    envelope = kernel.policy.envelope()
    result = loop_for(kernel, two_model_tasks(), model=LOCAL_MODEL,
                      model_invoke=priced_invoke(0.6)).run("two", envelope)
    assert result.termination is Termination.BUDGET_EXHAUSTED, result.summary()
    spent = kernel.budget.snapshot(envelope)
    assert spent.usd == pytest.approx(1.2)
    assert spent.model_calls == 2
    # The governor-wide figure counts the tree once, through its root.
    assert kernel.budget.snapshot().usd == pytest.approx(1.2)


def test_f06a_a_parent_ceiling_binds_a_child_call(tmp_path):
    kernel = kernel_with(tmp_path, budget=Budget(max_model_calls=1))
    parent = kernel.policy.envelope()
    first = parent.restrict()
    second = parent.restrict()
    kernel.budget.check_model_call(first)
    with pytest.raises(BudgetExhausted, match=parent.run_id):
        kernel.budget.check_model_call(second)
    assert kernel.budget.snapshot(parent).model_calls == 1
    assert kernel.budget.snapshot(second).model_calls == 0
    assert kernel.budget.remaining(second)["model_calls"] == 0, \
        "what a child may spend is bounded by every level above it"
    assert kernel.budget.lineage(second.run_id) == (second.run_id, parent.run_id)


def test_f06a_a_resumed_run_keeps_what_it_had_spent(tmp_path):
    kernel = kernel_with(tmp_path, budget=Budget(max_tool_calls=3))
    envelope = kernel.policy.envelope()
    kernel.budget.restore(envelope, {"tool_calls": 2, "usd": 0.25, "elapsed_minutes": 1.0})
    snap = kernel.budget.snapshot(envelope)
    assert snap.tool_calls == 2 and snap.usd == pytest.approx(0.25)
    assert snap.elapsed >= 59.0, "the clock continues; it does not restart"
    kernel.budget.check_tool_call(envelope)
    with pytest.raises(BudgetExhausted):
        kernel.budget.check_tool_call(envelope)
    # Restoring never lowers a counter the governor already holds.
    kernel.budget.restore(envelope, {"tool_calls": 0})
    assert kernel.budget.snapshot(envelope).tool_calls == 3


def test_f06a_a_checkpoint_records_the_run_tree_usage_and_a_resume_restores_it(tmp_path):
    kernel = kernel_with(tmp_path, budget=Budget(max_tool_calls=2))
    store = CheckpointStore(tmp_path / "ckpt")
    plan = Plan(objective="count", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="count", kind=TaskKind.TOOL,
                                component_id="count"),),
                completion_criteria=(Criterion(description="counted", kind="task"),))
    result = loop_for(kernel, plan, registry=registry_of(Tool("count")), checkpoints=store).run(
        "count", kernel.policy.envelope())
    checkpoint = store.latest_for(result.loop_id)
    assert checkpoint.usage["tool_calls"] == 1

    fresh = kernel_with(tmp_path, "fresh", budget=Budget(max_tool_calls=2))
    state = resume(checkpoint, fresh)
    assert fresh.budget.snapshot(state.envelope).tool_calls == 1


# ================================================================ F05: input bindings

def chain_plan(*, inputs=None, lookup_payload=None):
    inputs = (InputBinding(argument="symbol", source="fetch", pointer="/gene",
                           expected_type="string"),) if inputs is None else inputs
    return Plan(objective="chain", produced_by="test",
                tasks=(PlanTask(task_id="fetch", objective="fetch the gene record",
                                kind=TaskKind.TOOL, component_id="fetch",
                                payload={"note": PHI_TEXT}, max_label=Sensitivity.PHI),
                       PlanTask(task_id="lookup", objective="look the symbol up",
                                kind=TaskKind.TOOL, component_id="lookup",
                                dependencies=("fetch",), max_label=Sensitivity.PHI,
                                payload=({"species": "human"} if lookup_payload is None
                                         else lookup_payload),
                                inputs=inputs)),
                completion_criteria=(Criterion(description="done", kind="task"),))


def test_f05_resolve_bindings_keeps_the_source_label():
    from psh.labels import DataLabel, Labeled, label_of

    task = chain_plan().tasks[1]
    upstream = {"fetch": Labeled({"gene": "TP53", "hgnc_id": "HGNC:11998"},
                                 DataLabel(Sensitivity.PHI))}
    bound = resolve_bindings(task, upstream)
    assert unwrap(bound["symbol"]) == "TP53"
    assert label_of(bound["symbol"]).sensitivity is Sensitivity.PHI, \
        "a value read from a PHI result is PHI, whatever its own text says"


def test_f05_a_bound_argument_reaches_the_component(tmp_path):
    """The reviewer's chain: the second tool receives the gene symbol, not a blob."""
    from psh.labels import label_of

    kernel = kernel_with(tmp_path)
    fetch = Tool("fetch", result={"gene": "TP53", "hgnc_id": "HGNC:11998"})
    lookup = Tool("lookup")
    seen: dict[str, Sensitivity] = {}
    original = kernel.tool_gateway.check

    def spy(payload, manifest, envelope):
        seen[manifest.id] = label_of(payload).sensitivity
        return original(payload, manifest, envelope)

    kernel.tool_gateway.check = spy
    result = loop_for(kernel, chain_plan(), registry=registry_of(fetch, lookup)).run(
        "chain", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    payload = lookup.calls[0]
    assert payload["symbol"] == "TP53"
    assert payload["species"] == "human", "literals and bindings coexist"
    assert "upstream" in payload, "the whole upstream result is still offered"
    assert seen["lookup"] is Sensitivity.PHI, "the gate judged the payload under the join"


def test_f05_a_reference_looking_literal_is_a_plan_error(tmp_path):
    """The probe's plan: "$fetch.gene" is text, and the validator says so before anything runs."""
    kernel = kernel_with(tmp_path)
    plan = chain_plan(inputs=(), lookup_payload={"symbol": "$fetch.gene"})
    with pytest.raises(PlanRejected) as info:
        PlanValidator().validate(plan, kernel.policy.envelope())
    assert "input binding" in str(info.value) and "'fetch'" in str(info.value)
    fetch, lookup = Tool("fetch", result={"gene": "TP53"}), Tool("lookup")
    result = loop_for(kernel, plan, registry=registry_of(fetch, lookup)).run(
        "chain", kernel.policy.envelope())
    assert result.termination is Termination.PLAN_REJECTED
    assert lookup.calls == [] and fetch.calls == []


def test_f05_a_binding_is_checked_against_the_plan():
    with pytest.raises(ValueError, match="not among its dependencies"):
        PlanTask(task_id="lookup", objective="o", kind=TaskKind.TOOL, component_id="c",
                 inputs=(InputBinding(argument="symbol", source="fetch"),))
    with pytest.raises(ValueError, match="own result"):
        PlanTask(task_id="lookup", objective="o", kind=TaskKind.TOOL, component_id="c",
                 dependencies=("lookup",) if False else ("fetch",),
                 inputs=(InputBinding(argument="symbol", source="lookup"),))
    with pytest.raises(ValueError, match="twice"):
        PlanTask(task_id="lookup", objective="o", kind=TaskKind.TOOL, component_id="c",
                 dependencies=("fetch",),
                 inputs=(InputBinding(argument="symbol", source="fetch"),
                         InputBinding(argument="symbol", source="fetch", pointer="/x")))
    with pytest.raises(ValueError, match="pointer"):
        InputBinding(argument="symbol", source="fetch", pointer="gene")
    with pytest.raises(ValueError, match="legal types"):
        InputBinding(argument="symbol", source="fetch", expected_type="str")
    strict = policy()
    plan = chain_plan(lookup_payload={"symbol": "TP53", "species": "human"})
    with pytest.raises(PlanRejected, match="both a payload literal and an input binding"):
        PlanValidator().validate(plan, strict.envelope())


def test_f05_a_pointer_that_finds_nothing_fails_the_task_and_names_the_keys(tmp_path):
    kernel = kernel_with(tmp_path)
    fetch = Tool("fetch", result={"symbol": "TP53"})          # no "gene" field
    lookup = Tool("lookup")
    result = loop_for(kernel, chain_plan(), registry=registry_of(fetch, lookup)).run(
        "chain", kernel.policy.envelope())
    assert result.termination is not Termination.GOAL_SATISFIED
    assert lookup.calls == [], "the component never ran with a missing argument"
    assert any("BindingError" in f and "'/gene'" in f and "'symbol'" in f
               for f in result.failures), result.failures
    failed = [e for e in kernel.events.records() if e.event_type == "loop_task_failed"]
    assert failed and failed[0].detail["error_type"] == "BindingError"


@pytest.mark.parametrize("value, pointer, expected", [
    ({"gene": "TP53"}, "", {"gene": "TP53"}),
    ({"gene": "TP53"}, "/gene", "TP53"),
    ({"hits": [{"id": "A"}, {"id": "B"}]}, "/hits/1/id", "B"),
    ({"hits": [{"id": "A"}, {"id": "B"}]}, "/hits/-1/id", "B"),
    ({"a/b": {"c~d": 1}}, "/a~1b/c~0d", 1),
])
def test_f05_pointer_semantics(value, pointer, expected):
    assert resolve_pointer(value, pointer) == expected


def test_f05_pointer_failures_are_binding_errors():
    with pytest.raises(BindingError, match="out of range"):
        resolve_pointer({"hits": [1]}, "/hits/3")
    with pytest.raises(BindingError, match="not an integer"):
        resolve_pointer({"hits": [1]}, "/hits/x")
    with pytest.raises(BindingError, match="no members"):
        resolve_pointer({"n": 3}, "/n/deeper")
    with pytest.raises(BindingError, match="available keys"):
        resolve_pointer({"n": 3}, "/m")


def test_f05_type_and_cardinality_are_checked():
    from psh.labels import DataLabel, Labeled

    upstream = {"fetch": Labeled({"n": "3", "ids": [1, 2], "mixed": [1, "x"]}, DataLabel())}

    def task(**binding):
        return PlanTask(task_id="t", objective="o", kind=TaskKind.TOOL, component_id="c",
                        dependencies=("fetch",),
                        inputs=(InputBinding(argument="arg", source="fetch", **binding),))

    with pytest.raises(BindingError, match="expected integer"):
        resolve_bindings(task(pointer="/n", expected_type="integer"), upstream)
    with pytest.raises(BindingError, match="expected a list"):
        resolve_bindings(task(pointer="/n", cardinality="many"), upstream)
    with pytest.raises(BindingError, match="not integer"):
        resolve_bindings(task(pointer="/mixed", cardinality="many", expected_type="integer"),
                         upstream)
    ok = resolve_bindings(task(pointer="/ids", cardinality="many", expected_type="integer"),
                          upstream)
    assert unwrap(ok["arg"]) == [1, 2]
    optional = resolve_bindings(task(pointer="/missing", required=False), upstream)
    assert optional == {}, "an optional binding that finds nothing binds nothing"


def test_f05_a_model_task_sees_bound_values_by_name(tmp_path):
    kernel = kernel_with(tmp_path)
    prompts: list[str] = []
    fetch = Tool("fetch", result={"gene": "TP53", "hgnc_id": "HGNC:11998"})
    plan = Plan(objective="describe", produced_by="test",
                tasks=(PlanTask(task_id="fetch", objective="fetch", kind=TaskKind.TOOL,
                                component_id="fetch"),
                       PlanTask(task_id="describe", objective="describe the gene",
                                kind=TaskKind.MODEL, dependencies=("fetch",),
                                destinations=(Destination.LOCAL_MODEL,),
                                inputs=(InputBinding(argument="hgnc", source="fetch",
                                                     pointer="/hgnc_id"),))),
                completion_criteria=(Criterion(description="done", kind="task"),))
    result = loop_for(kernel, plan, registry=registry_of(fetch), model=LOCAL_MODEL,
                      model_invoke=lambda p: prompts.append(p) or "p53").run(
        "describe", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert prompts and "hgnc: HGNC:11998" in prompts[-1]


def test_f05_the_planner_parses_inputs_and_they_survive_a_round_trip():
    from psh.runtime import parse_plan
    from psh.runtime.checkpoint import plan_shape

    text = json.dumps({
        "objective": "chain", "tasks": [
            {"task_id": "fetch", "objective": "fetch", "kind": "tool", "component_id": "fetch"},
            {"task_id": "lookup", "objective": "lookup", "kind": "tool", "component_id": "lookup",
             "dependencies": ["fetch"],
             "inputs": [{"argument": "symbol", "source": "fetch", "pointer": "/gene",
                         "type": "string"}]}],
        "completion_criteria": [{"description": "done", "kind": "task"}]})
    plan = parse_plan(text)
    binding = plan.tasks[1].inputs[0]
    assert (binding.argument, binding.source, binding.pointer, binding.expected_type) == \
        ("symbol", "fetch", "/gene", "string")
    again = Plan.from_dict(plan.to_dict())
    assert again.tasks[1].inputs == plan.tasks[1].inputs
    assert plan_shape(plan.to_dict())[1][4] == (("symbol", "fetch", "/gene"),)
    assert plan_shape(plan.to_dict()) != plan_shape(chain_plan(inputs=()).to_dict())

    from psh.runtime import PlanParseError
    bad = text.replace('"dependencies": ["fetch"],', "")
    with pytest.raises(PlanParseError, match="not among its dependencies"):
        parse_plan(bad)


# ============================================================ F09: the operation ledger

def test_f09_the_ledger_records_every_tool_call_durably(tmp_path):
    from psh.runtime import OperationLedger, OperationState

    kernel = kernel_with(tmp_path)
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    tool = Tool("t")
    plan = Plan(objective="tools", produced_by="test",
                tasks=(PlanTask(task_id="s0", objective="s0", kind=TaskKind.TOOL, component_id="t"),
                       PlanTask(task_id="s1", objective="s1", kind=TaskKind.TOOL, component_id="t",
                                dependencies=("s0",))),
                completion_criteria=(Criterion(description="both", kind="task"),))
    envelope = kernel.policy.envelope()
    result = loop_for(kernel, plan, registry=registry_of(tool), operations=ledger).run(
        "tools", envelope)
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    records = {r.task_id: r for r in ledger.records(envelope.run_id)}
    assert set(records) == {"s0", "s1"}
    assert all(r.state is OperationState.SUCCEEDED and r.attempts == 1 for r in records.values())
    assert records["s0"].key == f"{envelope.run_id}:s0"
    assert ledger.in_doubt(envelope.run_id) == []
    ledger.close()

    reopened = OperationLedger(tmp_path / "ops.sqlite")
    assert len(reopened) == 2, "the ledger outlives the process"
    assert reopened.get(f"{envelope.run_id}:s1").state is OperationState.SUCCEEDED


def side_effect_plan():
    return Plan(objective="submit", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="submit the sample", kind=TaskKind.TOOL,
                                component_id="submit"),),
                completion_criteria=(Criterion(description="submitted", kind="task"),))


def test_f09_a_lost_side_effecting_operation_is_not_replayed(tmp_path):
    """The process died mid-call before the restart: the loop must not submit twice."""
    from psh.runtime import OperationLedger, OperationState

    kernel = kernel_with(tmp_path)
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    submit = Tool("submit", idempotent=False)
    envelope = kernel.policy.envelope()
    # What the ledger held when the previous process was lost: begun, never reported.
    ledger.begin(f"{envelope.run_id}:t1", component_id="submit", run_id=envelope.run_id,
                 task_id="t1", idempotent=False)

    result = loop_for(kernel, side_effect_plan(), registry=registry_of(submit),
                      operations=ledger).run("submit", envelope)
    assert result.termination is not Termination.GOAL_SATISFIED
    assert submit.calls == [], "the side effect was not repeated"
    assert any("OperationUnresolved" in f for f in result.failures), result.failures
    record = ledger.get(f"{envelope.run_id}:t1")
    assert record.state is OperationState.UNKNOWN and record.attempts == 1
    assert [r.task_id for r in ledger.in_doubt(envelope.run_id)] == ["t1"]
    assert any(e.event_type == "loop_task_unresolved" for e in kernel.events.records())


def test_f09_an_idempotent_operation_is_simply_re_run(tmp_path):
    from psh.runtime import OperationLedger, OperationState

    kernel = kernel_with(tmp_path)
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    submit = Tool("submit")                                     # idempotent by default
    envelope = kernel.policy.envelope()
    ledger.begin(f"{envelope.run_id}:t1", component_id="submit", run_id=envelope.run_id,
                 task_id="t1")
    result = loop_for(kernel, side_effect_plan(), registry=registry_of(submit),
                      operations=ledger).run("submit", envelope)
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    record = ledger.get(f"{envelope.run_id}:t1")
    assert record.state is OperationState.SUCCEEDED and record.attempts == 2


def test_f09_a_timeout_leaves_the_operation_in_doubt(tmp_path):
    from psh.contracts import ToolTimeout
    from psh.runtime import OperationLedger, OperationState

    kernel = kernel_with(tmp_path)
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    submit = Tool("submit", idempotent=False, error=ToolTimeout("no reply in 120s"))
    envelope = kernel.policy.envelope()
    first = loop_for(kernel, side_effect_plan(), registry=registry_of(submit),
                     operations=ledger).run("submit", envelope)
    assert first.termination is not Termination.GOAL_SATISFIED
    record = ledger.get(f"{envelope.run_id}:t1")
    assert record.state is OperationState.UNKNOWN and record.error_class == "ToolTimeout"
    assert len(submit.calls) == 1

    # A plain failure may also occur after the side effect: its outcome is unknown.
    kernel2 = kernel_with(tmp_path, "k2")
    broken = Tool("submit", idempotent=False, error=RuntimeError("bad request"))
    envelope2 = kernel2.policy.envelope()
    loop_for(kernel2, side_effect_plan(), registry=registry_of(broken),
             operations=ledger).run("submit", envelope2)
    assert ledger.get(f"{envelope2.run_id}:t1").state is OperationState.UNKNOWN

    # The same envelope, again (a restart): the timed-out submission is not repeated.
    again = loop_for(kernel, side_effect_plan(), registry=registry_of(submit),
                     operations=ledger).run("submit", envelope)
    assert again.termination is not Termination.GOAL_SATISFIED
    assert len(submit.calls) == 1


def test_f09_the_ledger_holds_no_task_text(tmp_path):
    from psh.runtime import OperationLedger

    kernel = kernel_with(tmp_path)
    path = tmp_path / "ops.sqlite"
    ledger = OperationLedger(path)
    tool = Tool("count", result={"admissions": 3, "note": PHI_TEXT})
    result = loop_for(kernel, phi_plan(), registry=registry_of(tool), operations=ledger).run(
        PHI_TEXT, kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    ledger.close()
    raw = path.read_bytes()
    for secret in (b"04851923", b"Alice", b"chest pain", b"admissions"):
        assert secret not in raw
    record = OperationLedger(path).records()[0]
    assert record.result_digest and len(record.result_digest) == 16


def test_f09_the_service_and_the_local_backend_share_the_ledger(tmp_path):
    from psh.runtime import LocalSubagentBackend, OperationLedger

    kernel = kernel_with(tmp_path)
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    tool = Tool("t")
    plan = Plan(objective="tools", produced_by="test",
                tasks=(PlanTask(task_id="s0", objective="s0", kind=TaskKind.TOOL, component_id="t"),),
                completion_criteria=(Criterion(description="ran", kind="task"),))
    service = ResearchRunService(kernel, planner=StaticPlanner(plan), registry=registry_of(tool),
                                 operations=ledger, limits=LoopLimits())
    released = service.run("tools")
    assert released.status == "released", released.summary()
    assert len(ledger) == 1
    backend = LocalSubagentBackend(kernel, planner_factory=lambda c: StaticPlanner(plan),
                                   registry=registry_of(tool), operations=ledger)
    assert backend.operations is ledger


# ======================================================== F10: Chinese retrieval terms

def test_f10_chinese_queries_produce_terms():
    from psh.context import CJK_STOP, terms

    found = terms("黄芪治疗心力衰竭的临床证据")
    assert {"astragalus", "heart", "failure", "clinical", "evidence", "treatment"} <= found
    assert {"黄芪", "心力衰竭", "临床", "证据"} <= found, "Chinese meets Chinese"
    assert "的" not in found and not any("的" in t for t in found)
    assert "苦参" in terms("苦参碱"), "an unknown word degrades to bigrams, not to nothing"
    assert terms("") == set() and "的" in CJK_STOP


def test_f10_a_chinese_query_resolves_an_english_capability(tmp_path):
    from psh.contracts import ComponentManifest

    kernel = kernel_with(tmp_path)
    registry = CapabilityRegistry()
    pubmed = ComponentManifest(id="pubmed_search", name="PubMed search", kind=ComponentKind.TOOL,
                               description="search PubMed for clinical evidence and trials",
                               intents=("clinical evidence",), max_label=Sensitivity.PUBLIC)
    blast = ComponentManifest(id="blast", name="BLAST", kind=ComponentKind.TOOL,
                              description="sequence alignment against a reference database",
                              max_label=Sensitivity.PUBLIC)
    registry.register(pubmed)
    registry.register(blast)
    ranked = registry.resolve("检索黄芪治疗心力衰竭的临床证据", kernel.policy.envelope())
    assert [c.manifest.id for c in ranked][0] == "pubmed_search"
    by_id = {c.manifest.id: c for c in ranked}
    assert by_id["pubmed_search"].relevance > by_id["blast"].relevance
    assert by_id["pubmed_search"].relevance >= 0.35, "the intent matched through the lexicon"


def test_f10_memory_crosses_the_language_boundary_both_ways(tmp_path):
    from psh.context import MemoryRetriever
    from psh.workgraph import NodeKind

    kernel = kernel_with(tmp_path)

    def commit(title, kind=NodeKind.CLAIM):
        return kernel.persistence.commit_node(
            kind=kind, title=title, principal="tester", source_run="run_earlier",
            project_id="proj_a", validation_status="verified")

    commit("astragalus injection reduced heart failure readmission in the 2023 cohort")
    commit("决定：心衰队列的蛋白组采用 DIA 采集", kind=NodeKind.DECISION)
    commit("sequencing depth for the exome panel is 100x", kind=NodeKind.DECISION)
    retriever = MemoryRetriever(kernel.graph)
    envelope = kernel.policy.envelope(project_id="proj_a")

    from_chinese = retriever.retrieve("黄芪治疗心力衰竭", envelope=envelope)
    assert from_chinese and "astragalus" in from_chinese[0].content
    from_english = retriever.retrieve("heart failure proteomics acquisition", envelope=envelope)
    assert from_english and "DIA" in from_english[0].content


# ============================================ F12: outcomes, degraded results, timeouts

def test_f12_tool_outcomes_feed_the_registry_prior(tmp_path):
    kernel = kernel_with(tmp_path)
    good, bad = Tool("good"), Tool("bad", error=RuntimeError("boom"))
    registry = registry_of(good, bad)
    plan = Plan(objective="both", produced_by="test",
                tasks=(PlanTask(task_id="g", objective="g", kind=TaskKind.TOOL, component_id="good"),
                       PlanTask(task_id="b", objective="b", kind=TaskKind.TOOL, component_id="bad")),
                completion_criteria=(Criterion(description="both", kind="task"),))
    loop_for(kernel, plan, registry=registry).run("both", kernel.policy.envelope())
    assert registry.observed_success(good.manifest) == 1.0
    assert registry.observed_success(bad.manifest) < 1.0, "a failure lowered the prior"


def test_f12_a_degraded_result_is_a_value_with_a_caveat_that_reaches_the_release(tmp_path):
    from psh.contracts import DegradedResult

    kernel = kernel_with(tmp_path)
    tool = Tool("count", result=DegradedResult({"n": 3}, "fallback dataset used"))
    plan = Plan(objective="count", produced_by="test",
                tasks=(PlanTask(task_id="t1", objective="count", kind=TaskKind.TOOL,
                                component_id="count"),),
                completion_criteria=(Criterion(description="counted", kind="task"),))
    envelope = kernel.policy.envelope()
    internal = loop_for(kernel, plan, registry=registry_of(tool)).run("count", envelope)
    assert internal.termination is Termination.GOAL_SATISFIED, internal.summary()
    assert unwrap(internal.results["t1"]) == {"n": 3}
    assert internal.caveats == {"t1": ("fallback dataset used",)}
    released = Finalizer(kernel).finalize(internal, envelope)
    assert released.status == "released"
    assert "task t1 ran degraded: fallback dataset used" in released.limitations
    assert any(e.event_type == "loop_task_degraded" for e in kernel.events.records())


def test_f12_a_timeout_is_its_own_failure_class():
    from psh.contracts import ContractViolation, ToolTimeout
    from psh.runtime import RetryPolicy

    assert issubclass(ToolTimeout, ContractViolation)
    assert RetryPolicy().permits(ToolTimeout("late")), "the default retry policy still covers it"
    assert not RetryPolicy(retryable=("ToolTimeout",)).permits(ContractViolation("bad shape"))


# ========================================================= F08: isolation is a report

class Confining:
    """A sandbox backend that claims OS-level confinement, for the wiring tests."""

    os_isolation = True

    def wrap(self, argv, *, workdir, allow_network):
        return list(argv)

    def describe(self):
        return "test sandbox: confines filesystem and sockets"


def test_f08_the_kernel_reports_what_its_isolation_provides(tmp_path):
    kernel = kernel_with(tmp_path)
    report = kernel.isolation_report
    assert report.sandbox == "NoSandbox" and report.os_isolation is False
    assert report.process_isolation and report.clean_environment and report.egress_proxy
    assert not report.filesystem_confined and not report.raw_sockets_confined
    assert not report.sufficient_for_untrusted_code
    assert "NOT confined" in report.describes
    described = kernel.report()
    assert described["isolation"]["report"]["os_isolation"] is False
    assert described["isolation"]["os_isolation_required"] is False
    assert described["classifier_validated"] is kernel.classifier.validated


def test_f08_a_policy_requiring_os_isolation_is_refused_without_a_sandbox(tmp_path):
    with pytest.raises(PolicyDenied, match="OS-level isolation"):
        kernel_with(tmp_path, require_os_isolation=True)


def test_f08_a_confining_backend_satisfies_the_requirement(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "s").ensure_dirs(),
                           policy=policy(require_os_isolation=True), sandbox=Confining())
    report = kernel.isolation_report
    assert report.sandbox == "Confining" and report.os_isolation
    assert report.sufficient_for_untrusted_code
    assert kernel.report()["isolation"]["os_isolation_required"] is True


def test_f08_a_run_policy_cannot_add_a_requirement_the_kernel_cannot_meet(tmp_path):
    kernel = kernel_with(tmp_path)
    strict = policy(require_os_isolation=True)
    # The lattice admits it: turning a requirement on is a narrowing ...
    assert PolicyLattice.violations(strict, policy()) == []
    assert [v.dimension for v in PolicyLattice.violations(policy(), strict)] == \
        ["require_os_isolation"]
    # ... and the kernel refuses it, because it cannot meet it.
    result = Runner(kernel, model=LOCAL_MODEL, model_invoke=lambda p: "ok").run(
        "hello", policy=strict)
    assert result.status == "refused" and "OS-level isolation" in result.error
    with pytest.raises(PolicyDenied, match="OS-level isolation"):
        ResearchRunService(kernel, planner=StaticPlanner(model_plan()), policy=strict)
    assert strict.as_dict()["require_os_isolation"] is True


def test_f08_a_child_process_can_report_a_degraded_result_or_a_timeout():
    from psh.contracts import DegradedResult
    from psh.kernel.isolation import _unwrap_child_result

    wrapped = _unwrap_child_result(
        {"$psh": {"status": "degraded", "reason": "partial page", "value": {"n": 1}}})
    assert isinstance(wrapped, DegradedResult)
    assert wrapped.value == {"n": 1} and wrapped.reason == "partial page"
    assert _unwrap_child_result({"n": 1}) == {"n": 1}
    plain = {"$psh": {"status": "ok", "value": 3}}
    assert _unwrap_child_result(plain) == plain, "only a declared shortfall is unwrapped"
