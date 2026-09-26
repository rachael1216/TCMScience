"""The scientific contracts, and the gate that decides what may be published.

The tests that matter most here are the refusal tests. A validator that accepts
good artifacts is not evidence of much; the claim being made is that specific,
named failure modes are *structurally* impossible to publish, so each of those
failure modes has a test that constructs it and asserts the exact code returned.

`test_prediction_cannot_become_clinical_fact` is the one to read first: it is
the characteristic error of network pharmacology and the reason this contract
layer exists.
"""

from __future__ import annotations

import pytest

from bioagent.contracts import (CandidateClaim, Consistency, Directness,
                                EvidenceItem, EvidenceQuality, NotAssessed,
                                Overclaim, Precision, ResearchArtifact, RiskOfBias,
                                SourceCard, check_claim, designs_for_tier,
                                require_declared, schema_bundle, tier_for_design,
                                validate_artifact)
from bioagent.contracts.artifact import Violation
from bioagent.contracts.evidence_item import (EMPIRICAL_DESIGNS, PREDICTIVE_DESIGNS,
                                              STUDY_DESIGNS)
from bioagent.tcm.model import EvidenceTier

SNAPSHOT = "a" * 64

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def card(id: str = "herb", **kw) -> SourceCard:
    """A well-formed, pinned, licensed source — the happy path."""
    base = dict(id=id, name="HERB 2.0", kind="database", access_method="http_api",
                allowed_hosts=("herb.ac.cn",), license_spdx="CC-BY-4.0",
                snapshot_hash=SNAPSHOT, snapshot_at="2026-09-01T00:00:00Z")
    base.update(kw)
    return SourceCard(**base)


def unpinned_card() -> SourceCard:
    """A local import: no hosts, no snapshot. Legal to construct, refused when
    it is asked to carry a claim."""
    return SourceCard(id="etcm", name="ETCM 2.0", kind="database",
                      access_method="local_file")


def quality(**kw) -> EvidenceQuality:
    base = dict(risk_of_bias=RiskOfBias.LOW, directness=Directness.DIRECT,
                precision=Precision.PRECISE, consistency=Consistency.CONSISTENT)
    base.update(kw)
    return EvidenceQuality(**base)


def item(id: str = "e1", design: str = "randomized_trial", card_id: str = "herb",
         **kw) -> EvidenceItem:
    base = dict(id=id, design=design, quote="the verbatim excerpt relied on",
                source_card_id=card_id, quote_verified=True, quality=quality())
    base.update(kw)
    return EvidenceItem(**base)


def claim(id: str = "c1", kind: str = "efficacy", supports: tuple = ("e1",),
          **kw) -> CandidateClaim:
    base = dict(id=id, text="the asserted statement", claim_kind=kind,
                subject="Qinghao", predicate="reduces", object="fever",
                supports=supports, asserted_population="adults with malaria",
                supported_population="adults with malaria",
                asserted_outcome="fever clearance",
                supported_outcome="fever clearance",
                confidence=0.7, confidence_basis="one RCT, n=120",
                falsified_by="a larger null trial")
    base.update(kw)
    return CandidateClaim(**base)


def artifact(claims=(), evidence=(), sources=(card(),), limitations=("only two "
              "databases were searched, both Chinese-language",), **kw) -> ResearchArtifact:
    base = dict(id="A1", run_id="R1", skill_id="analyze-tcm-network-pharmacology",
                skill_version="1.0.0",
                composite_version={"runtime": "0.5.3", "skill": "1.0.0",
                                   "source": "sources-2026-09", "benchmark": "season-1"},
                sources=sources, evidence=evidence, claims=claims,
                limitations=limitations)
    base.update(kw)
    return ResearchArtifact(**base)


# --------------------------------------------------------------------------
# vocabulary: design is primary, tier is derived
# --------------------------------------------------------------------------


def test_design_vocabulary_splits_what_the_tier_vocabulary_collapses():
    """The defect this layer exists to fix.

    `EvidenceTier.PRECLINICAL` cannot distinguish an assay from an animal study,
    but the two license very different claims. Storing the design and deriving
    the tier keeps both, and makes the loss of information happen on read.
    """
    assert tier_for_design("in_vitro") is EvidenceTier.PRECLINICAL
    assert tier_for_design("animal") is EvidenceTier.PRECLINICAL
    assert {"animal", "in_vitro"} <= set(designs_for_tier(EvidenceTier.PRECLINICAL))
    assert "in_vitro" not in designs_for_tier(EvidenceTier.CASE_REPORT)
    # The split is the point: one tier, two designs that mean different things.
    assert designs_for_tier(EvidenceTier.PRECLINICAL) != ("animal",)
    assert len(designs_for_tier(EvidenceTier.PRECLINICAL)) > 1


def test_design_vocabulary_matches_psh():
    """Two vocabularies for one fact is how they drift.

    PSH's `workflow.ir.DESIGNS` is the kernel-side name; if this package invents
    its own spelling, a compiled task contract and a research artifact will
    disagree about what an "animal study" is.

    The two sets are equal in *membership* but split differently: PSH names the
    empirical half `EVIDENCE_DESIGNS` and the computational half
    `PREDICTIVE_DESIGNS`, because its claim-support table has to keep them apart.
    This package uses the same split. Asserting both in one place is what stops
    one side gaining a design the other never learns about.
    """
    psh_ir = pytest.importorskip("psh.workflow.ir", reason="PSH not importable")
    assert set(psh_ir.DESIGNS) == set(STUDY_DESIGNS)
    assert set(psh_ir.EVIDENCE_DESIGNS) == set(EMPIRICAL_DESIGNS)
    assert set(psh_ir.PREDICTIVE_DESIGNS) == set(PREDICTIVE_DESIGNS)


def test_predictive_designs_are_separate_from_empirical_ones():
    assert not set(PREDICTIVE_DESIGNS) & set(EMPIRICAL_DESIGNS)
    assert set(STUDY_DESIGNS) == set(EMPIRICAL_DESIGNS) | set(PREDICTIVE_DESIGNS)
    assert item(design="docking").is_prediction
    assert not item(design="in_vitro").is_prediction


def test_unknown_design_is_refused_not_defaulted():
    """A default here is the failure mode: an unrecognised design quietly
    ranking as something citable."""
    with pytest.raises(ValueError, match="unknown study design"):
        tier_for_design("vibes")
    with pytest.raises(ValueError, match="unknown study design"):
        item(design="vibes")


# --------------------------------------------------------------------------
# quality: dimensions, never a score
# --------------------------------------------------------------------------


def test_quality_starts_unassessed_and_has_no_aggregate():
    q = EvidenceQuality()
    assert q.assessed == ()
    assert not q.complete
    assert not hasattr(q, "score") and not hasattr(q, "grade")
    with pytest.raises(NotAssessed):
        q.weakest()


def test_quality_partial_assessment_is_reported_not_hidden():
    q = EvidenceQuality(risk_of_bias=RiskOfBias.LOW)
    assert q.assessed == ("risk_of_bias",)
    assert set(q.unassessed) == {"directness", "precision", "consistency"}
    name, value = q.weakest()
    assert (name, value) == ("risk_of_bias", RiskOfBias.LOW)


def test_quality_weakest_points_at_the_worst_dimension():
    q = EvidenceQuality(risk_of_bias=RiskOfBias.LOW, directness=Directness.EXTRAPOLATED,
                        precision=Precision.PRECISE)
    name, _ = q.weakest()
    assert name == "directness"


@pytest.mark.parametrize("bias,kind,blocked", [
    (RiskOfBias.LOW, "efficacy", False),
    (RiskOfBias.SOME_CONCERNS, "efficacy", False),
    (RiskOfBias.HIGH, "efficacy", True),
    (RiskOfBias.CRITICAL, "recommendation", True),
    # Risk of bias gates clinical claims only. High bias in a mechanism study is
    # a weakness to report, not a refusal — the study still says something about
    # a mechanism.
    (RiskOfBias.HIGH, "mechanism", False),
])
def test_risk_of_bias_blocks_only_clinical_claims(bias, kind, blocked):
    q = EvidenceQuality(risk_of_bias=bias, directness=Directness.DIRECT)
    assert bool(q.blocks(kind)) is blocked


def test_extrapolation_blocks_any_claim():
    q = EvidenceQuality(directness=Directness.EXTRAPOLATED)
    assert q.blocks("mechanism")
    assert q.blocks("attribution")


def test_quality_rejects_a_rationale_for_a_dimension_that_does_not_exist():
    with pytest.raises(ValueError, match="unknown dimensions"):
        EvidenceQuality(rationale={"vibes": "felt right"})


# --------------------------------------------------------------------------
# source card: pinned by content
# --------------------------------------------------------------------------


def test_snapshot_hash_and_date_must_travel_together():
    with pytest.raises(ValueError, match="must be set together"):
        card(snapshot_at="")
    with pytest.raises(ValueError, match="must be set together"):
        card(snapshot_hash="")


def test_a_network_source_must_declare_its_hosts():
    with pytest.raises(ValueError, match="must declare allowed_hosts"):
        SourceCard(id="x", name="X", kind="database", access_method="http_api")


def test_local_source_needs_no_hosts_and_is_not_pinned():
    c = unpinned_card()
    assert not c.opens_network()
    assert not c.pinned
    assert not c.licensed


def test_empty_licence_is_honest_not_permissive():
    """A blank SPDX id is 'no grant identified'. Anything that reads it as
    permission inverts the whole licence argument."""
    assert card(license_spdx="").licensed is False
    assert card(license_spdx="CC-BY-4.0").licensed is True


# --------------------------------------------------------------------------
# evidence item
# --------------------------------------------------------------------------


def test_evidence_requires_a_quote():
    with pytest.raises(ValueError, match="has no quote"):
        EvidenceItem(id="e", design="animal", quote="   ")


def test_identifier_and_its_scheme_must_agree():
    with pytest.raises(ValueError, match="states no identifier_type"):
        item(identifier="38291045", identifier_type="")
    with pytest.raises(ValueError, match="requires an identifier value"):
        item(identifier="", identifier_type="pmid")


def test_retracted_evidence_is_unusable_and_says_so():
    e = item(retracted="retracted")
    assert not e.usable
    assert any("RETRACTED" in c for c in e.caveats())


def test_unchecked_retraction_is_a_caveat_not_a_clean_bill():
    """`unverified` is not `not_retracted`. Reporting them the same way would
    claim a check that never happened."""
    e = item(retracted="unverified")
    assert e.usable
    assert any("has not been checked" in c for c in e.caveats())


def test_caveats_are_not_summarised_to_one_line():
    e = item(retracted="expression_of_concern", quote_verified=False)
    caveats = e.caveats()
    assert len(caveats) >= 2
    assert any("concern" in c for c in caveats)
    assert any("not located" in c for c in caveats)


def test_animal_evidence_carries_a_species_caveat():
    assert any("human outcomes" in c for c in item(design="animal").caveats())


def test_tier_is_derived_and_licences_only_what_it_should():
    assert item(design="randomized_trial").licenses("efficacy")
    assert not item(design="animal").licenses("efficacy")
    assert item(design="animal").licenses("mechanism")


# --------------------------------------------------------------------------
# candidate claim: the extrapolation boundary
# --------------------------------------------------------------------------


def test_an_overclaiming_claim_is_constructible_so_it_can_be_measured():
    """The refusal lives in `check_claim`, not in `__post_init__`.

    A system that could not build an overclaiming claim could not detect one
    either, and the Claim Calibration axis would have nothing to score. So the
    object is buildable and the *verdict* is what refuses it.
    """
    c = claim(asserted_population="adults with hypertension", supported_population="mice")
    assert c.undeclared_extrapolations() == ("population:adults with hypertension",)
    v = check_claim(c, {"e1": item()})
    assert not v.allowed
    assert "CLM009" in v.codes


def test_require_declared_is_the_strict_door_for_skills():
    """A skill writing into the trusted record wants to fail rather than
    publish an undeclared extrapolation; this is the entry point for that."""
    c = claim(asserted_population="adults with hypertension", supported_population="mice")
    with pytest.raises(Overclaim, match="does not declare it"):
        require_declared(c)
    require_declared(claim(asserted_population="adults with hypertension",
                           supported_population="mice",
                           declared_extrapolations={
                               "population:adults with hypertension": "mice only"}))


def test_declaring_the_extrapolation_with_a_reason_is_accepted():
    c = claim(asserted_population="adults with hypertension", supported_population="mice",
              declared_extrapolations={
                  "population:adults with hypertension": "efficacy shown only in mice"})
    assert c.undeclared_extrapolations() == ()


def test_a_broader_covered_population_covers_a_narrower_assertion():
    """'adults with hypertension and T2DM' licenses a claim about 'adults with
    hypertension'; the reverse does not hold."""
    c = claim(asserted_population="adults with hypertension",
              supported_population="adults with hypertension and type 2 diabetes")
    assert c.undeclared_extrapolations() == ()
    c2 = claim(asserted_population="adults with hypertension and type 2 diabetes",
               supported_population="adults with hypertension")
    assert c2.undeclared_extrapolations() == (
        "population:adults with hypertension and type 2 diabetes",)


def test_confidence_without_a_basis_is_refused():
    """A number nobody can review is worse than no number, because it looks
    like evidence."""
    with pytest.raises(ValueError, match="no\\s+confidence_basis"):
        claim(confidence=0.8, confidence_basis="")
    with pytest.raises(ValueError, match="confidence must be in"):
        claim(confidence=1.4, confidence_basis="vibes")


def test_unknown_claim_kind_is_refused():
    with pytest.raises(ValueError, match="is not one of"):
        claim(kind="cures")


# --------------------------------------------------------------------------
# check_claim: refusal codes
# --------------------------------------------------------------------------


def test_claim_with_no_evidence_is_refused():
    v = check_claim(claim(supports=()), {})
    assert not v.allowed
    assert v.codes == ("CLM001",)


def test_missing_support_is_reported_by_id():
    v = check_claim(claim(supports=("ghost",)), {})
    assert v.codes == ("CLM002",)
    assert "ghost" in v.reason_text[0]


def test_prediction_cannot_become_clinical_fact():
    """The failure this project exists to make impossible.

    A predicted target relationship, propagated through a network, asserted as
    efficacy in humans. The refusal is a distinct code so the benchmark can
    count it on its own axis rather than averaging it into a general error rate.
    """
    e = EvidenceItem(id="e1", design="network_prediction", quote="predicted overlap",
                     source_card_id="herb", quote_verified=True)
    v = check_claim(claim(kind="efficacy", supports=("e1",)), {"e1": e})
    assert not v.allowed
    assert "CLM004" in v.codes
    assert v.prediction_as_fact


def test_every_prediction_design_is_caught_the_same_way():
    for design in PREDICTIVE_DESIGNS:
        e = EvidenceItem(id="e1", design=design, quote="model output",
                         source_card_id="herb", quote_verified=True)
        v = check_claim(claim(kind="association", supports=("e1",)), {"e1": e})
        assert v.prediction_as_fact, f"{design} slipped through as clinical evidence"


def test_prediction_may_still_support_a_mechanism_claim():
    """Network pharmacology's actual job. Refusing this would make the skill
    useless; the point is that it cannot go further than a mechanism."""
    e = EvidenceItem(id="e1", design="docking", quote="docking score -8.2 kcal/mol",
                     source_card_id="herb", quote_verified=True)
    v = check_claim(claim(kind="mechanism", subject="berberine", predicate="targets",
                          object="TNF", supports=("e1",)), {"e1": e})
    assert v.allowed, v.reason_text


def test_efficacy_from_animal_evidence_is_refused_on_tier_not_on_species():
    """Both problems exist here; the tier floor is the one that cannot be
    declared away, so `needs_declaration` must be False."""
    e = item(design="animal")
    v = check_claim(claim(kind="efficacy", supports=("e1",)), {"e1": e})
    assert not v.allowed
    assert "CLM005" in v.codes
    assert not v.needs_declaration


def test_an_undeclared_extrapolation_alone_is_fixable_by_declaration():
    """The one case where the fix is to state a limit rather than gather data —
    so a run can say 'declare this and re-submit' instead of 'insufficient'.

    A mechanism claim is used deliberately: it needs only preclinical evidence,
    so the animal item clears the tier floor and the *only* thing wrong is the
    undeclared gap. With an efficacy claim the tier floor would also fail, and
    `needs_declaration` would correctly be False.
    """
    e = item(design="animal")
    c = claim(kind="mechanism", asserted_population="humans",
              supported_population="mice", declared_extrapolations={},
              subject="berberine", predicate="targets", object="TNF")
    v = check_claim(c, {"e1": e})
    assert not v.allowed
    assert v.codes == ("CLM009",), v.reason_text
    assert v.needs_declaration


def test_retracted_evidence_alone_refuses_the_claim():
    v = check_claim(claim(supports=("e1",)), {"e1": item(retracted="retracted")})
    assert v.codes == ("CLM003",)


def test_a_retracted_item_alongside_sound_ones_is_a_caveat_not_a_refusal():
    """Deleting a retracted citation is right; treating its presence as fatal
    would push authors to hide the fact that they checked."""
    e1 = item(id="e1", identifier="38291045", identifier_type="pmid")
    e2 = item(id="e2", retracted="retracted")
    v = check_claim(claim(supports=("e1", "e2")), {"e1": e1, "e2": e2})
    assert v.allowed
    assert any("retracted" in c for c in v.caveats)


def test_unverified_quote_refuses_the_claim():
    v = check_claim(claim(supports=("e1",)), {"e1": item(quote_verified=False)})
    assert "CLM010" in v.codes


def test_background_evidence_without_a_quote_does_not_refuse_the_claim():
    """Only cited items are held to the quote standard. Otherwise authors would
    drop context to satisfy the validator."""
    e1 = item(id="e1", identifier="1", identifier_type="pmid")
    e2 = item(id="e2", quote_verified=False)
    v = check_claim(claim(supports=("e1",)), {"e1": e1, "e2": e2})
    assert v.allowed


def test_normative_claim_must_use_the_recommendation_kind():
    v = check_claim(claim(kind="efficacy", normative=True), {"e1": item()})
    assert "CLM007" in v.codes


# --------------------------------------------------------------------------
# validate_artifact: the gate
# --------------------------------------------------------------------------


def test_a_sound_artifact_publishes_cleanly():
    v = validate_artifact(artifact(
        claims=(claim(supports=("e1",)),),
        evidence=(item(id="e1", identifier="38291045", identifier_type="pmid"),)))
    assert v.publishable, v.explain()
    assert v.codes == ()
    assert v.warnings == ()


def test_prediction_as_clinical_fact_is_refused_with_its_own_code():
    e = EvidenceItem(id="e1", design="network_prediction", quote="predicted overlap",
                     source_card_id="herb", quote_verified=True)
    v = validate_artifact(artifact(claims=(claim(supports=("e1",)),), evidence=(e,)))
    assert not v.publishable
    assert "ART106" in v.codes


def test_an_unpinned_source_cannot_carry_a_claim():
    v = validate_artifact(artifact(
        claims=(claim(supports=("e1",)),),
        evidence=(item(id="e1", card_id="etcm", identifier="1", identifier_type="pmid"),),
        sources=(unpinned_card(),)))
    assert not v.publishable
    assert "ART102" in v.codes
    assert "ART103" in v.codes


def test_an_unpinned_source_is_fine_when_no_claim_rests_on_it():
    """Background material does not have to be pinned; only the evidence a
    claim rests on does. Holding everything to the standard would make the
    requirement unaffordable and therefore ignored."""
    v = validate_artifact(artifact(
        claims=(),
        evidence=(item(id="e1", card_id="etcm"),),
        sources=(unpinned_card(),)))
    assert "ART102" not in v.codes


def test_an_artifact_with_no_sources_is_refused():
    v = validate_artifact(artifact(sources=()))
    assert "ART101" in v.codes


def test_an_artifact_with_no_limitations_is_refused():
    """"No limitations stated" reads as "no limitations exist", which is never
    true and is the claim most likely to be wrong."""
    v = validate_artifact(artifact(limitations=()))
    assert "ART110" in v.codes


def test_a_missing_version_axis_is_refused():
    v = validate_artifact(artifact(
        composite_version={"runtime": "0.5.3", "skill": "1.0.0"}))
    assert "ART108" in v.codes


def test_an_output_without_a_hash_is_refused():
    from bioagent.contracts import ArtifactFile
    v = validate_artifact(artifact(outputs=(ArtifactFile(path="network.graphml"),)))
    assert "ART107" in v.codes


def test_evidence_naming_an_absent_source_card_is_a_construction_error():
    """This one cannot even be built, which is stronger than validating it."""
    with pytest.raises(ValueError, match="not in the artifact"):
        artifact(evidence=(item(id="e1", card_id="ghost"),))


def test_every_violation_is_reported_not_just_the_first():
    v = validate_artifact(ResearchArtifact(
        id="A9", run_id="R", skill_id="sk", composite_version={},
        sources=(), evidence=(), claims=(), limitations=()))
    assert {"ART101", "ART108", "ART110"} <= set(v.codes)


def test_verdict_explains_itself():
    v = validate_artifact(artifact(limitations=()))
    assert "NOT publishable" in v.explain()
    assert "ART110" in v.explain()


# --------------------------------------------------------------------------
# provenance: digests and round-trips
# --------------------------------------------------------------------------


def test_artifact_digest_ignores_the_mutable_status():
    """`status` is a workflow field, not content. If it entered the digest, a
    refuted-then-fixed artifact would have a different identity for the same
    science."""
    a = artifact(claims=(claim(supports=("e1",)),), evidence=(item(id="e1"),))
    b = ResearchArtifact(**{**{f: getattr(a, f) for f in
                              ("id", "run_id", "skill_id", "skill_version",
                               "composite_version", "sources", "evidence", "claims",
                               "limitations")}, "status": "validated"})
    assert a.digest == b.digest


def test_round_trip_through_dict_preserves_the_artifact():
    a = artifact(claims=(claim(supports=("e1",)),), evidence=(item(id="e1"),))
    assert ResearchArtifact.from_dict(a.as_dict()).digest == a.digest


def test_the_published_document_carries_the_digest_but_the_digest_does_not_cover_it():
    """`as_dict` round-trips and excludes the digest; `document` adds it. If the
    digest covered itself the computation would not terminate."""
    a = artifact(claims=(claim(supports=("e1",)),), evidence=(item(id="e1"),))
    assert "digest" not in a.as_dict()
    assert a.document()["digest"] == a.digest
    assert ResearchArtifact.from_dict(a.as_dict()).digest == a.digest


def test_composite_version_string_names_all_four_axes():
    a = artifact()
    assert a.composite_version_string == (
        "runtime=0.5.3|skill=1.0.0|source=sources-2026-09|benchmark=season-1")


def test_sources_hash_changes_when_a_snapshot_moves():
    """The whole point of ADR-0002: an external database moving is visible."""
    a = artifact(sources=(card(),))
    b = artifact(sources=(card(snapshot_hash="b" * 64),))
    assert a.sources_hash != b.sources_hash


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


def test_schema_bundle_carries_every_contract():
    bundle = schema_bundle()
    assert set(bundle["$defs"]) == {
        "SourceCard", "EvidenceQuality", "EvidenceItem", "CandidateClaim",
        "ResearchArtifact"}


@pytest.mark.parametrize("name", ["SourceCard", "EvidenceItem", "CandidateClaim",
                                  "ResearchArtifact"])
def test_a_contract_schema_is_self_describing(name):
    from bioagent.contracts import SCHEMAS
    schema = SCHEMAS[name]
    assert schema.get("description")
    assert schema.get("properties")
    assert schema.get("type") == "object"


def test_the_artifact_schema_requires_all_four_version_axes():
    from bioagent.contracts import SCHEMAS
    required = SCHEMAS["ResearchArtifact"]["properties"]["composite_version"]["required"]
    assert set(required) == {"runtime", "skill", "source", "benchmark"}
