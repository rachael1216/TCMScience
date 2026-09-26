from dataclasses import asdict, replace
import json

import pytest

from psh.labels import DataLabel, Destination as D, Sensitivity as S
from psh.runtime import ReleasedResult
from psh.workflow import DynamicWorkflow, DynamicResearchRunService, RunEventJournal
from test_planner import kernel, local_profile
from test_dynamic_workflow import stage


def evidence_workflow():
    from psh.capabilities import CapabilityRegistry
    from psh.workflow import WorkflowStage, Effect
    from test_checkpoint import Tool
    from test_scientific_workflow import task, program, contract
    registry = CapabilityRegistry()
    registry.register(Tool("gather", {"evidence": ["PMID:99999999"]}))
    tasks = [task("gather", kind="tool", component_id="gather", evidence_required=True,
        output_schema={"type": "object", "required": ["evidence"]}, max_label=S.RESEARCH_DEIDENTIFIED,
        destinations=(D.LOCAL_COMPUTE,)), task("answer", ("gather",), max_label=S.RESEARCH_DEIDENTIFIED)]
    p = program(tasks, {"gather": contract(effects=(Effect.LOCAL_COMPUTE,)), "answer": contract()})
    p = replace(p, plan=replace(p.plan, plan_id="release_fixture"))
    return DynamicWorkflow((WorkflowStage("a", p),), "a"), registry


def service(kernel, local_profile, journal, responses, **kwargs):
    queue = iter(responses)
    return DynamicResearchRunService(kernel, journal=journal, model=local_profile,
                                    model_invoke=lambda _: next(queue), **kwargs)


def test_only_final_stage_deliverable_is_released(kernel, local_profile, tmp_path):
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = service(kernel, local_profile, journal, ["intermediate-only", "final summary"])
        result = s.run(DynamicWorkflow((stage(next_stage="b"), stage("b")), "a"))
        assert isinstance(result, ReleasedResult) and result.ok
        assert result.released_output == "final summary"
        assert "intermediate-only" not in json.dumps(asdict(result))
        assert not hasattr(result, "stages") and not hasattr(result, "results")
        assert kernel.quarantine.released == 1 and kernel.broker.stats()["model_calls"] == 2


def test_uncited_clinical_claim_is_withheld(kernel, local_profile, tmp_path):
    kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
    flow, registry = evidence_workflow()
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["Drug X cures every cancer."],
                         registry=registry).run(flow)
        assert result.status == "refused" and result.refused_at == "release_gate"
        assert result.released_output is None and not result.citations
        assert "Drug X" not in json.dumps(asdict(result))
        assert kernel.quarantine.released == 0


def test_execution_failure_never_releases_partial_result(kernel, local_profile, tmp_path):
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["partial-private-text"]).run(
            DynamicWorkflow((stage(next_stage="b"), stage("b")), "a"))
        assert result.status == "not_completed" and result.released_output is None
        assert "partial-private-text" not in repr(result)
        assert kernel.quarantine.held == 0


def test_visit_limit_is_not_release_success(kernel, local_profile, tmp_path):
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["partial"]).run(
            DynamicWorkflow((stage(next_stage="a"),), "a", max_visits=1))
        assert not result.ok and result.termination == "max_visits"
        assert kernel.quarantine.held == 0


def test_current_output_destination_is_rechecked_after_execution(kernel, local_profile, tmp_path):
    envelope = kernel.envelope()
    def invoke(_):
        kernel.policy = replace(kernel.policy,
            allowed_destinations=tuple(d for d in kernel.policy.allowed_destinations if d != D.USER_OUTPUT))
        return "do not release me"
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = DynamicResearchRunService(kernel, journal=journal, model=local_profile, model_invoke=invoke)
        result = s.run(DynamicWorkflow((stage(),), "a"), envelope=envelope)
        assert result.status == "refused" and result.released_output is None
        assert kernel.quarantine.released == 0


def test_new_citation_requirement_is_not_lost_to_stale_policy(kernel, local_profile, tmp_path):
    def invoke(_):
        kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
        return "Drug X cures every cancer."
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicResearchRunService(kernel, journal=journal, model=local_profile,
            model_invoke=invoke).run(DynamicWorkflow((stage(),), "a"))
        assert result.status == "refused" and result.released_output is None


def test_original_strict_citation_requirement_survives_policy_widening(kernel, local_profile, tmp_path):
    kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
    flow, registry = evidence_workflow()
    def invoke(_):
        kernel.policy = replace(kernel.policy, require_citation=False, require_claim_support=False)
        return "Drug X cures every cancer."
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = DynamicResearchRunService(kernel, journal=journal, model=local_profile,
            model_invoke=invoke, registry=registry).run(flow)
        assert result.status == "refused"


def test_prior_stage_label_and_goal_are_not_replaced_by_final_stage(kernel, local_profile, tmp_path, monkeypatch):
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = service(kernel, local_profile, journal, ["intermediate", "final"])
        run = s._controller.run
        def unverified(*args, **kwargs):
            raw = run(*args, **kwargs)
            raw.stages[0].goal_status = "unverified"
            return replace(raw, label=DataLabel(S.RESEARCH_DEIDENTIFIED, rationale="private metadata"))
        monkeypatch.setattr(s._controller, "run", unverified)
        result = s.run(DynamicWorkflow((stage(next_stage="b"), stage("b")), "a"))
        assert result.refused_at == "goal_verification"
        assert result.label.sensitivity == S.RESEARCH_DEIDENTIFIED
        assert "private metadata" not in repr(result)


def test_intermediate_caveat_is_retained_only_as_generic_limitation(kernel, local_profile, tmp_path, monkeypatch):
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = service(kernel, local_profile, journal, ["intermediate", "final"])
        run = s._controller.run
        def degraded(*args, **kwargs):
            raw = run(*args, **kwargs)
            raw.stages[0].caveats = {"private-task-id": ("private caveat text",)}
            return raw
        monkeypatch.setattr(s._controller, "run", degraded)
        result = s.run(DynamicWorkflow((stage(next_stage="b"), stage("b")), "a"))
        assert result.ok and result.limitations
        assert "private" not in repr(result)


def test_journal_failure_is_safe_metadata_only(kernel, local_profile, tmp_path, monkeypatch):
    with RunEventJournal(tmp_path / "e.db") as journal:
        def fail(*args, **kwargs):
            raise OSError("private file path")
        monkeypatch.setattr(journal, "append", fail)
        result = service(kernel, local_profile, journal, []).run(DynamicWorkflow((stage(),), "a"))
        assert result.refusal_kind == "OSError" and result.released_output is None
        assert "private file path" not in repr(result)
        assert kernel.broker.stats()["model_calls"] == 0


def test_nonempty_journal_does_not_return_previous_released_output(kernel, local_profile, tmp_path):
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = service(kernel, local_profile, journal, ["first output"])
        flow = DynamicWorkflow((stage(),), "a")
        assert s.run(flow).ok
        result = s.run(flow)
        assert result.refusal_kind == "ReplayRefused" and result.released_output is None
        assert kernel.broker.stats()["model_calls"] == 1


def test_finalizer_exception_withholds_candidate(kernel, local_profile, tmp_path, monkeypatch):
    from psh.workflow.service import Finalizer
    def fail(*args, **kwargs):
        raise OSError("private candidate")
    monkeypatch.setattr(Finalizer, "finalize", fail)
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["private candidate"]).run(
            DynamicWorkflow((stage(),), "a"))
        assert result.refused_at == "finalization" and result.released_output is None
        assert "private candidate" not in repr(result)


def test_fabricated_citation_and_untrusted_source_are_withheld(kernel, local_profile, tmp_path):
    kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
    flow, registry = evidence_workflow()
    fabricated = "Drug X cures every cancer [PMID:99999999]."
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, [fabricated], registry=registry).run(
            flow, sources={"99999999": fabricated})
        assert result.status == "refused" and result.refused_at == "release_gate"
        assert result.released_output is None and result.citations == ()
        assert "99999999" not in repr(result) and "Drug X" not in repr(result)


def test_real_intermediate_sensitive_output_labels_final_summary(kernel, local_profile, tmp_path):
    kernel.policy = replace(kernel.policy, max_data_label=S.PHI)
    kernel.persistence.max_label = S.PHI
    stages = (stage(next_stage="b"), stage("b"))
    stages = tuple(replace(s, program=replace(s.program, plan=replace(s.program.plan,
        tasks=tuple(replace(t, max_label=S.PHI) for t in s.program.plan.tasks)))) for s in stages)
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["MRN: 12345678", "count: 1"]).run(
            DynamicWorkflow(stages, "a"))
        assert result.ok and result.released_output == "count: 1"
        assert result.label.sensitivity == S.PHI and "12345678" not in repr(result)


def test_service_policy_applies_to_execution_after_kernel_policy_widens(kernel, local_profile, tmp_path):
    kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
    with RunEventJournal(tmp_path / "e.db") as journal:
        s = service(kernel, local_profile, journal, ["should not execute"])
        kernel.policy = replace(kernel.policy, require_citation=False, require_claim_support=False)
        result = s.run(DynamicWorkflow((stage(),), "a"))
        assert result.status == "not_completed" and result.refusal_kind == "PlanRejected"
        assert kernel.broker.stats()["model_calls"] == 0


def test_prior_caller_destination_restriction_survives_default_policy(kernel, local_profile, tmp_path):
    envelope = replace(kernel.envelope(), allowed_destinations=frozenset((D.LOCAL_MODEL, D.PERSISTENT)))
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, ["final summary"]).run(
            DynamicWorkflow((stage(),), "a"), envelope=envelope)
        assert result.status == "refused" and result.released_output is None


@pytest.mark.parametrize("signed", [True, False])
def test_source_trust_is_revalidated_not_taken_from_flag(kernel, local_profile, tmp_path, signed):
    from psh.evidence.record import EvidenceRecord
    from test_review_2026_09_18 import SUPPORTED, ABSTRACT
    kernel.policy = replace(kernel.policy, require_citation=True, require_claim_support=True)
    flow, registry = evidence_workflow()
    record = EvidenceRecord.from_text(identifier="34449189", text=ABSTRACT,
        retrieved_by="fixture", retrieval_run="fixture", trusted=True, retracted=False)
    if signed:
        record = kernel.evidence_signer.sign(record)
    with RunEventJournal(tmp_path / "e.db") as journal:
        result = service(kernel, local_profile, journal, [SUPPORTED], registry=registry).run(
            flow, sources={"34449189": record})
        assert result.ok is signed
        assert (result.released_output == SUPPORTED) if signed else result.released_output is None
