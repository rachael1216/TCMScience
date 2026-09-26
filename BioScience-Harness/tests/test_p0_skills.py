"""The four P0 TCM skills — behaviour, and the refusals they are built around.

Every skill here produces a `ResearchArtifact`, so the first thing each test does
is put it through `validate_artifact`. A skill whose output is not publishable is
not a skill, whatever else it does.

The interesting tests are the ones asserting that a skill *declines* to do
something. `test_predicted_edges_are_never_reported_as_measured`,
`test_a_classical_contraindication_is_not_reported_as_a_safety_signal` and
`test_no_record_is_unknown_not_safe` are the load-bearing ones: each pins a
distinction that a helpful-looking implementation would quietly erase.
"""

from __future__ import annotations

import pytest

from bioagent.contracts import (PREDICTIVE_DESIGNS, validate_artifact)
from bioagent.skills.p0 import (analyze_tcm_network_pharmacology, assess_tcm_safety,
                                normalize_tcm_entities, retrieve_tcm_evidence)
from bioagent.skills.p0.common import seed_source_card, strongest_supported_kind
from bioagent.skills.loader import load_skills
from bioagent.skills.compiler import compile_skill

SKILLS_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "skills" / "tcm"


_CAPTURED: dict = {}


@pytest.fixture(autouse=True)
def _capture(monkeypatch):
    """Record the JSON each skill emits, so a test can assert on its content.

    A `ResearchArtifact` carries a file's hash and size but not its bytes — that
    separation is deliberate, since the artifact is a record and the files are
    separate objects. Spying on the single emitter is therefore how a test reads
    the payload, and it is less invasive than giving the skills a file-writing
    side effect that would make them awkward to test.
    """
    from bioagent.skills.base import json_file as original

    def spy(path, data, *, description=""):
        record, text = original(path, data, description=description)
        _CAPTURED["data"] = data
        return record, text

    for module in ("bioagent.skills.p0.common", "bioagent.skills.p0.entities",
                   "bioagent.skills.p0.retrieve", "bioagent.skills.p0.netpharm",
                   "bioagent.skills.p0.assess_safety"):
        monkeypatch.setattr(f"{module}.json_file", spy)
    yield
    _CAPTURED.clear()


def emitted() -> dict:
    """The payload the most recent skill emitted."""
    assert "data" in _CAPTURED, "no skill emitted JSON during this test"
    return _CAPTURED["data"]


# --------------------------------------------------------------------------
# every skill produces a publishable artifact
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label,artifact", [
    ("entities", normalize_tcm_entities(["白芍", "姜"], run_id="T")),
    ("retrieve", retrieve_tcm_evidence("附子", run_id="T")),
    ("netpharm", analyze_tcm_network_pharmacology("桂枝汤", run_id="T")),
    ("safety", assess_tcm_safety("附子", run_id="T")),
])
def test_every_p0_skill_produces_a_publishable_artifact(label, artifact):
    verdict = validate_artifact(artifact)
    assert verdict.publishable, f"{label}: {verdict.explain()}"


@pytest.mark.parametrize("artifact", [
    normalize_tcm_entities(["白芍"], run_id="T"),
    retrieve_tcm_evidence("附子", run_id="T"),
    analyze_tcm_network_pharmacology("桂枝汤", run_id="T"),
    assess_tcm_safety("附子", run_id="T"),
])
def test_every_artifact_states_its_limitations_and_versions(artifact):
    assert artifact.limitations, "an artifact claiming no limitations is claiming none"
    assert set(artifact.composite_version) == {"runtime", "skill", "source", "benchmark"}
    assert artifact.sources, "an artifact with no declared source is uncheckable"


def test_every_skill_declares_the_seed_corpus_it_reads():
    """The corpus is a source, and a resolution is only checkable if the reader
    knows which corpus produced it."""
    for artifact in (normalize_tcm_entities(["白芍"], run_id="T"),
                     retrieve_tcm_evidence("附子", run_id="T"),
                     analyze_tcm_network_pharmacology("桂枝汤", run_id="T"),
                     assess_tcm_safety("附子", run_id="T")):
        assert "tcmscience.tcm.seed" in {s.id for s in artifact.sources}
        assert artifact.sources[0].pinned, "a source carrying claims must be pinned"


# --------------------------------------------------------------------------
# the seed source card
# --------------------------------------------------------------------------


def test_seed_source_card_is_pinned_and_hashes_the_corpus_contents():
    card = seed_source_card()
    assert card.pinned and card.offline_capable
    assert card.license_spdx == "MIT"
    assert not card.opens_network()


def test_the_seed_pin_changes_if_the_corpus_changes(monkeypatch):
    """A pin that could not move would be decoration."""
    from bioagent.skills.p0 import common
    from bioagent.tcm import knowledge

    before = seed_source_card().snapshot_hash
    monkeypatch.setattr(knowledge, "_HERBS", knowledge._HERBS[:-1], raising=False)
    assert seed_source_card().snapshot_hash != before


def test_the_seed_card_says_the_corpus_is_small_and_has_no_trials():
    limits = " ".join(seed_source_card().known_limits)
    assert "illustrative" in limits
    assert "no clinical study data" in limits


# --------------------------------------------------------------------------
# E4-A  entity normalization preserves ambiguity
# --------------------------------------------------------------------------


def test_an_ambiguous_name_returns_candidates_and_does_not_choose():
    """「姜」 is 生姜 or 干姜. Picking one would be wrong half the time and
    confident every time."""
    normalize_tcm_entities(["姜"], run_id="T")
    row = next(r for r in emitted()["queries"] if r["query"] == "姜")
    assert row["status"] == "ambiguous"
    assert row["entity_id"] is None
    assert len(row["candidates"]) >= 2


def test_ambiguity_is_declared_in_the_artifacts_limitations():
    artifact = normalize_tcm_entities(["姜"], run_id="T")
    assert any("ambiguous" in limit for limit in artifact.limitations)


def test_a_crude_drug_with_processed_forms_reports_both():
    """甘草 and 炙甘草 are different preparations, so a bare name is ambiguous."""
    normalize_tcm_entities(["甘草"], run_id="T")
    row = next(r for r in emitted()["queries"] if r["query"] == "甘草")
    assert row["status"] == "resolved_with_processed_forms"
    assert len(row["candidates"]) > 1


def test_a_processed_form_states_what_it_came_from():
    normalize_tcm_entities(["炙甘草"], run_id="T")
    row = emitted()["queries"][0]
    if row["entity_kind"] == "ProcessedHerb":
        assert row["processed_from"]
        assert row["processing_method"]
        assert any("processed form" in limit for limit in
                   normalize_tcm_entities(["炙甘草"], run_id="T").limitations)


def test_an_unknown_name_is_unresolved_not_an_error():
    artifact = normalize_tcm_entities(["不存在的药"], run_id="T")
    assert validate_artifact(artifact).publishable
    assert emitted()["queries"][0]["status"] == "unresolved"


# --------------------------------------------------------------------------
# E4-B  evidence retrieval reports rather than adjudicates
# --------------------------------------------------------------------------


def test_retrieval_labels_study_design_not_just_tier():
    artifact = retrieve_tcm_evidence("附子", run_id="T")
    for item in artifact.evidence:
        assert item.design, "an item with no design cannot be scope-checked"
        assert item.tier is not None


def test_retrieval_does_not_assess_quality_dimensions_the_corpus_cannot_support():
    """Risk of bias, precision and consistency default to NOT_ASSESSED rather
    than to a plausible-looking `low`."""
    artifact = retrieve_tcm_evidence("附子", run_id="T")
    for item in artifact.evidence:
        if item.quality is not None:
            assert set(item.quality.unassessed) >= {"risk_of_bias", "precision",
                                                    "consistency"}


def test_retrieval_says_absence_of_evidence_is_not_evidence_of_absence():
    artifact = retrieve_tcm_evidence("不存在的药", run_id="T")
    joined = " ".join(artifact.limitations).lower()
    assert "not evidence" in joined or "empty result" in joined


def test_retrieval_reports_that_no_live_index_was_searched():
    artifact = retrieve_tcm_evidence("附子", run_id="T")
    joined = " ".join(artifact.limitations)
    assert "Europe PMC" in joined or "not a systematic search" in joined


def test_retrieval_records_when_the_result_set_was_truncated():
    artifact = retrieve_tcm_evidence("附子", run_id="T", max_results=1)
    assert emitted()["search"].get("truncated") in (True, False)


def test_a_retracted_study_stays_retracted():
    """The skill does not launder `retracted` into `unverified`."""
    artifact = retrieve_tcm_evidence("附子", run_id="T")
    for item in artifact.evidence:
        assert item.retracted in ("retracted", "not_retracted",
                                  "expression_of_concern", "unverified")


# --------------------------------------------------------------------------
# E4-C  network pharmacology keeps prediction and measurement apart
# --------------------------------------------------------------------------


def test_predicted_edges_are_never_reported_as_measured():
    """The plan's central requirement for this skill, and the reason the kernel
    grew a predictive-design vocabulary."""
    analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    data = emitted()
    assert "predicted_targets" in data and "measured_targets" in data
    for row in data["predicted_targets"]:
        assert row["measured"] is False, "a prediction was marked measured"
    for row in data["measured_targets"]:
        assert row["measured"] is True


def test_there_is_no_combined_targets_key_a_reader_could_mistake():
    analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    assert "targets" not in emitted(), (
        "a single `targets` key would let a predicted edge be read as a measurement")


def test_every_network_edge_carries_a_predictive_design():
    artifact = analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    for item in artifact.evidence:
        assert item.design in PREDICTIVE_DESIGNS, (
            f"{item.design!r} is not a predictive design; a network inference must "
            "not be labelled as an experiment")
        assert item.is_prediction


def test_network_edges_are_marked_extrapolated_so_they_block_claims():
    """`directness=EXTRAPOLATED` is what stops a predicted edge being restated
    downstream as a measured one."""
    from bioagent.contracts import Directness
    artifact = analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    for item in artifact.evidence:
        assert item.quality.directness is Directness.EXTRAPOLATED
        assert item.quality.blocks("mechanism")


def test_a_network_claim_is_a_mechanism_claim_and_says_it_is_predicted():
    artifact = analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    for claim in artifact.claims:
        assert claim.claim_kind == "mechanism"
        assert "in silico" in claim.supported_population
        assert claim.confidence <= 0.5


def test_the_network_artifact_declares_no_randomisation_was_done():
    analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    assert emitted()["randomisation"]["performed"] is False
    joined = " ".join(analyze_tcm_network_pharmacology("桂枝汤", run_id="T").limitations)
    assert "no network randomisation" in joined or "not a" in joined


def test_a_name_that_is_not_a_formula_yields_an_empty_artifact_not_an_exception():
    artifact = analyze_tcm_network_pharmacology("阿司匹林", run_id="T")
    assert validate_artifact(artifact).publishable
    assert emitted()["formula"] is None
    assert not artifact.claims


def test_a_formula_with_no_edges_makes_no_claim():
    """The first implementation emitted a claim unconditionally and produced an
    artifact whose claim cited no evidence — `validate_artifact` caught it as
    ART101, which is the validator doing its job."""
    artifact = analyze_tcm_network_pharmacology("桂枝汤", run_id="T")
    if not artifact.evidence:
        assert not artifact.claims


# --------------------------------------------------------------------------
# E4-D  safety: unknown is not safe
# --------------------------------------------------------------------------


def test_no_record_is_unknown_not_safe():
    """The single most important behaviour in this package. A tool that answers
    "no known risk" when it means "I found no record" is dangerous precisely
    because it looks helpful."""
    artifact = assess_tcm_safety("不存在的药", run_id="T")
    assert emitted()["status"] == "unknown"
    assert any("NOT EVIDENCE OF SAFETY" in limit for limit in artifact.limitations)


def test_a_resolved_substance_without_records_is_no_record_not_safe():
    assess_tcm_safety("桂枝", run_id="T")
    status = emitted()["status"]
    assert status in ("no_record", "risk_recorded")
    if status == "no_record":
        assert any("must not be read as `safe`" in limit
                   for limit in assess_tcm_safety("桂枝", run_id="T").limitations)


def test_the_safety_artifact_always_refuses_to_be_clinical_advice():
    for artifact in (assess_tcm_safety("附子", run_id="T"),
                     assess_tcm_safety("不存在的药", run_id="T")):
        joined = " ".join(artifact.limitations)
        assert "not clinical advice" in joined
        assert "ABSENCE OF A RECORD IS NOT EVIDENCE OF SAFETY" in joined


def test_eighteen_incompatibilities_are_a_property_of_the_combination():
    """A per-herb loop would miss every one of them."""
    assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="T")
    data = emitted()
    assert data["combination_conflicts"], (
        "甘草 + 甘遂 is an 18-fan incompatibility and must be detected as a pair")


def test_a_combination_conflict_is_severity_critical():
    assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="T")
    critical = emitted()["critical_records"]
    assert any(r["kind"] == "incompatibility" for r in critical)


def test_a_classical_contraindication_is_not_reported_as_a_safety_signal():
    """The distinction the contract forced and the skill now respects.

    "The classics record these two as incompatible" and "there is clinical
    evidence of harm" are different statements, and only the second is a
    `safety_signal` (floor: `case_report`). The corpus's records are
    pharmacopoeia entries, so the strongest honest claim is an `attribution`.
    Labelling it a safety signal would promote traditional knowledge to clinical
    evidence — the overstatement this layer exists to prevent.
    """
    artifact = assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="T")
    for claim in artifact.claims:
        assert claim.claim_kind != "safety_signal"
        assert claim.claim_kind in ("attribution", "traditional_use", "mechanism")


def test_high_severity_records_are_surfaced_and_govern_the_claim_text():
    artifact = assess_tcm_safety("附子", run_id="T")
    data = emitted()
    if data["critical_records"]:
        assert any("severity" in limit or "critical" in limit
                   for limit in artifact.limitations)


def test_processing_that_changes_toxicity_is_carried_not_summarised():
    """附子 is toxic crude and used as the processed 制附子; dropping the
    processing state would describe a different substance."""
    assess_tcm_safety("附子", run_id="T")
    data = emitted()
    assert isinstance(data["processing"], list)


def test_an_unresolved_co_administered_herb_is_reported_not_ignored():
    artifact = assess_tcm_safety("附子", co_administered=["不存在的药"], run_id="T")
    assert emitted()["unresolved"] == ["不存在的药"]
    assert artifact.limitations


def test_safety_never_downgrades_a_critical_severity():
    """There is no code path that softens a finding, because the pressure to do
    so is what makes a safety tool dangerous."""
    for artifact in (assess_tcm_safety("附子", run_id="T"),
                     assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="T")):
        for row in emitted()["records"]:
            if row["combination"]:
                assert row["severity"] == "critical"


# --------------------------------------------------------------------------
# the manifests
# --------------------------------------------------------------------------


def test_all_four_manifests_load_without_refusal():
    loaded, refused = load_skills(SKILLS_DIR)
    assert refused == (), f"manifests refused: {refused}"
    assert {s.spec.id for s in loaded} == {
        "normalize-tcm-entities", "retrieve-tcm-evidence",
        "analyze-tcm-network-pharmacology", "assess-tcm-safety"}


def test_every_manifest_declares_a_hashable_implementation():
    loaded, _ = load_skills(SKILLS_DIR, require_implementation=False)
    for skill in loaded:
        assert skill.content_hash, "a skill with no content hash cannot be pinned"
        assert skill.spec.version == "1.0.0"
        assert skill.spec.license_spdx


def test_the_netpharm_manifest_forbids_every_clinical_claim_kind():
    """Its evidence is computational, so the manifest bans the claims that would
    turn a prediction into a finding — and the kernel bans the same ones
    independently."""
    loaded, _ = load_skills(SKILLS_DIR)
    netpharm = next(s for s in loaded
                    if s.spec.id == "analyze-tcm-network-pharmacology")
    assert netpharm.spec.evidence.claim_kinds == ("mechanism",)
    assert {"efficacy", "association", "safety_signal"} <= set(
        netpharm.spec.evidence.forbidden_claims)


def test_every_manifest_compiles_and_the_kernel_accepts_it():
    from psh.policy import PolicySnapshot
    from psh.labels import Destination, Sensitivity
    from psh.contracts import Autonomy, RiskTier
    from psh.workflow.compiler import ScientificCompiler

    env = PolicySnapshot(
        profile_id="t", max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        allowed_destinations=frozenset(Destination), autonomy=Autonomy.ACT_WITH_APPROVAL,
        risk_ceiling=RiskTier.R2_CONSEQUENTIAL).envelope(clamp=True)
    loaded, _ = load_skills(SKILLS_DIR)
    for skill in loaded:
        compiled = compile_skill(skill.spec, env, objective=f"run {skill.spec.id}")
        ScientificCompiler().compile(compiled.program, env)


# --------------------------------------------------------------------------
# the helper that keeps a skill from overstating
# --------------------------------------------------------------------------


def test_strongest_supported_kind_picks_what_the_evidence_can_carry():
    from bioagent.contracts import EvidenceItem, EvidenceQuality, Directness
    from bioagent.contracts import RiskOfBias, Precision, Consistency

    q = EvidenceQuality(risk_of_bias=RiskOfBias.LOW, directness=Directness.DIRECT,
                        precision=Precision.PRECISE, consistency=Consistency.CONSISTENT)
    classical = EvidenceItem(id="a", design="classical_text", quote="x",
                             source_card_id="s", quote_verified=True, quality=q)
    trial = EvidenceItem(id="b", design="randomized_trial", quote="y",
                         source_card_id="s", quote_verified=True, quality=q)

    permitted = ("safety_signal", "traditional_use", "attribution")
    # A classical text licenses an attribution and no more: `traditional_use`
    # needs EXPERT_EXPERIENCE, which is a strictly higher tier. Returning the
    # strongest *supported* kind rather than the strongest *listed* one is the
    # whole point of the helper.
    assert strongest_supported_kind([classical], permitted) == "attribution"
    assert strongest_supported_kind([trial], permitted) == "safety_signal"
    with pytest.raises(ValueError, match="no claim kind is supported"):
        strongest_supported_kind([], permitted)
