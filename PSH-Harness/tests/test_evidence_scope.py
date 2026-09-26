"""The evidence-model failures, as permanent regression tests.

All of these were reproduced against psh v0.2 before being written. A reviewer identified
two, and direct execution showed both are worse than described:

**Population extrapolation.** A trial in middle-aged European adults was reported as
supporting claims about elderly Asian women, children under five, pregnant patients and
dialysis-dependent patients — at confidence 0.65-0.75, against 0.95 for the one population
the trial actually enrolled. Every unlicensed population scored in the same band as the
licensed one, because the verifier has no notion of population at all.

**Subject swap across two real papers.** Given "P1NP predicts fracture" and "CTX predicts
mortality", the fused claim "P1NP predicts all-cause mortality" was reported as supported by
the CTX paper. Lexical overlap on the outcome carried the verdict while the subject differed.

The diagnosis that shapes these tests: this is a **data-model** gap, not a matching-quality
gap. The v0.2 claim is a free-text string, so there is no subject to compare and no
population to compare. Adding a stronger matcher — an entailment model, say — over a claim
with no structured subject inherits exactly the same blindness. So the fix is structure
first, and these tests assert on structure.
"""

from __future__ import annotations

import pytest

from psh import Sensitivity

# ------------------------------------------------------------------ source material

#: A trial with a narrow, explicitly stated enrolment. What it licenses is a claim about
#: middle-aged European adults, and nothing wider.
EUROPEAN_TRIAL = (
    "In this randomized, double-blind trial of middle-aged European adults (mean age 52 "
    "years, 94% of European ancestry, n=2418), Drug A improved overall survival compared "
    "with placebo (hazard ratio 0.78; 95% CI 0.66 to 0.92; P=0.003).")

#: Two real findings about two different analytes. Fusing them is the hallucination that
#: matters, because every individual term is present in a genuine source.
P1NP_FRACTURE = (
    "Serum P1NP concentration independently predicted incident vertebral fracture in "
    "postmenopausal women (hazard ratio 1.41 per SD; 95% CI 1.18 to 1.69).")
CTX_MORTALITY = (
    "Serum CTX concentration was associated with all-cause mortality in the same cohort "
    "(hazard ratio 1.33 per SD; 95% CI 1.10 to 1.61).")

#: A surrogate-outcome trial: bone mineral density is not fracture.
BMD_SURROGATE = (
    "Among postmenopausal women with osteoporosis, teriparatide increased lumbar spine bone "
    "mineral density by 9.7% over 18 months compared with placebo (P<0.001).")


@pytest.fixture
def scoped_record(signer):
    """Build a SIGNED EvidenceRecord carrying the licensed scope of its source.

    Signed, because that is what a registered retriever produces. v0.3 tests got trust for
    free by naming the retriever; that free trust was the defect, so the tests now do what
    the real path does.
    """
    from psh.evidence import EvidenceRecord

    def build(identifier: str, text: str, **kw):
        return signer.sign(EvidenceRecord.from_text(
            identifier=identifier, text=text, retrieved_by="sable.pubmed_fetch",
            retrieval_run="test", retracted=False, **kw))

    return build


# ------------------------------------------------- 1-2: the claim has structure at all

def test_1_claim_carries_structured_fields():
    """A claim is subject/predicate/object plus population and outcome, not a string.

    This is the test the whole fix rests on. v0.2's ``ClaimSupport.claim`` was a sentence,
    so "which entity is this about?" and "which population?" were unanswerable questions and
    therefore unasked ones.
    """
    from psh.evidence import ScientificClaim

    claim = ScientificClaim.parse(
        "Drug A improves overall survival in elderly Asian women.")
    assert claim.subject, "the claim's subject must be identified"
    assert "drug a" in claim.subject.lower()
    assert claim.outcome, "the claim's outcome must be identified"
    # Outcomes canonicalise: survival and mortality are the same construct measured in
    # opposite directions, so comparing them as distinct strings would make a mortality
    # source fail to license a survival claim about the identical finding.
    assert claim.outcome == "mortality"
    assert claim.population is not None
    assert claim.population.descriptors, "population qualifiers must be captured"
    assert claim.text  # the display form is preserved


def test_2_unextractable_fields_are_unknown_not_guessed():
    """A field that cannot be established is UNKNOWN, never inferred.

    The same rule the evidence tiers follow: a plausible-looking guess is worse than an
    admitted gap, because a downstream check treats a guessed field as established.
    """
    from psh.evidence import ScientificClaim

    vague = ScientificClaim.parse("The results were encouraging.")
    assert not vague.is_clinical_claim, "a non-claim must not be treated as one"

    partial = ScientificClaim.parse("Drug A improves survival.")
    assert partial.subject
    assert partial.population.is_unstated, \
        "an unstated population must be marked unstated, not treated as universal"


# ------------------------------------------------- 3: the source declares what it licenses

def test_3_evidence_record_carries_its_licensed_scope(scoped_record):
    """A source records the population it enrolled and the outcomes it measured."""
    record = scoped_record("11111111", EUROPEAN_TRIAL)
    scope = record.licensed_scope
    assert scope is not None
    assert scope.population.descriptors, "the enrolled population must be captured"
    joined = " ".join(scope.population.descriptors).lower()
    assert "european" in joined or "middle-aged" in joined
    assert "mortality" in scope.outcomes, scope.outcomes
    assert scope.design, "the study design must be recorded"


# --------------------------------- 4: population extrapolation cannot pass verification

@pytest.mark.parametrize("statement", [
    "Drug A improves survival in elderly Asian women.",
    "Drug A improves survival in children under 5.",
    "Drug A improves survival in pregnant patients.",
    "Drug A improves survival in dialysis-dependent patients.",
])
def test_4_population_outside_the_studied_one_is_not_supported(scoped_record, statement):
    """The headline failure. Each of these scored 0.65-0.75 SUPPORT in v0.2."""
    from psh.evidence import Relationship
    from psh.kernel import TrustedKernel

    record = scoped_record("11111111", EUROPEAN_TRIAL)
    verifier = TrustedKernel.__new__(TrustedKernel)  # verifier only; no state dir needed
    from psh.evidence import ClaimSupportVerifier

    support = ClaimSupportVerifier().verify_record(statement=statement, record=record)
    assert not support.supports, f"unlicensed population reported as supported: {statement}"
    assert support.relationship in (Relationship.PARTIAL, Relationship.UNKNOWN)
    assert support.scope_mismatch, "the mismatch must be reported, not silently discounted"
    assert "population" in support.rationale.lower()


def test_5_the_studied_population_is_still_supported(scoped_record):
    """The dual. A fix that refuses everything closes nothing, because it gets switched off."""
    from psh.evidence import ClaimSupportVerifier

    support = ClaimSupportVerifier().verify_record(
        statement="Drug A improves overall survival in middle-aged European adults.",
        record=scoped_record("11111111", EUROPEAN_TRIAL))
    assert support.supports, f"the licensed claim was refused: {support.rationale}"
    assert not support.scope_mismatch


def test_6_unstated_claim_population_inherits_the_source(scoped_record):
    """An unqualified claim is read as being about the studied population, not all humans.

    The alternative — treating an unstated population as universal — would refuse almost
    every real sentence a clinician writes, since prose usually leaves the population
    implicit. Inheriting is both the charitable and the accurate reading.
    """
    from psh.evidence import ClaimSupportVerifier

    support = ClaimSupportVerifier().verify_record(
        statement="Drug A improves overall survival.",
        record=scoped_record("11111111", EUROPEAN_TRIAL))
    assert support.supports
    assert support.directness.value in ("direct", "indirect")


# ------------------------------------------- 7: subject swap across two real sources

def test_7_subject_swap_between_real_papers_is_caught(scoped_record):
    """"P1NP predicts mortality" from a P1NP/fracture paper and a CTX/mortality paper."""
    from psh.evidence import ClaimSupportVerifier

    verifier = ClaimSupportVerifier()
    fused = "P1NP predicts all-cause mortality."

    against_ctx = verifier.verify_record(statement=fused,
                                         record=scoped_record("33333333", CTX_MORTALITY))
    assert not against_ctx.supports, \
        "the CTX paper must not support a claim about P1NP"
    assert against_ctx.subject_mismatch, "the subject mismatch must be named"

    against_p1np = verifier.verify_record(statement=fused,
                                          record=scoped_record("22222222", P1NP_FRACTURE))
    assert not against_p1np.supports, \
        "the P1NP paper reports fracture, not mortality"


def test_8_each_source_still_supports_its_own_finding(scoped_record):
    """Neither guard may block the claim its source genuinely licenses."""
    from psh.evidence import ClaimSupportVerifier

    verifier = ClaimSupportVerifier()
    a = verifier.verify_record(
        statement="P1NP predicts incident vertebral fracture in postmenopausal women.",
        record=scoped_record("22222222", P1NP_FRACTURE))
    b = verifier.verify_record(
        statement="CTX is associated with all-cause mortality.",
        record=scoped_record("33333333", CTX_MORTALITY))
    assert a.supports, a.rationale
    assert b.supports, b.rationale


# ------------------------------------------------------- 9: certainty downgrades

def test_9_surrogate_outcome_downgrades_certainty(scoped_record):
    """Bone density is not fracture. GRADE calls this indirectness."""
    from psh.evidence import ClaimSupportVerifier, Directness

    support = ClaimSupportVerifier().verify_record(
        statement="Teriparatide reduces fracture in postmenopausal osteoporosis.",
        record=scoped_record("44444444", BMD_SURROGATE))
    assert not support.supports, "a surrogate outcome cannot license a clinical outcome"
    assert support.directness is not Directness.DIRECT
    assert support.downgrades, "the applied downgrade must be recorded"


def test_10_downgrade_reasons_are_enumerated(scoped_record):
    """Every downgrade names itself, so a reader can see which one applied."""
    from psh.evidence import ClaimSupportVerifier

    support = ClaimSupportVerifier().verify_record(
        statement="Drug A improves survival in children under 5.",
        record=scoped_record("11111111", EUROPEAN_TRIAL))
    assert support.downgrades
    reasons = {d.reason for d in support.downgrades}
    assert any("population" in r for r in reasons), reasons


# ------------------------------------------------- 11: the graph records what is licensed

def test_11_graph_records_licensed_scope_and_extrapolation(tmp_path, scoped_record):
    """A why-query must show where a conclusion exceeds its sources."""
    from psh.evidence import ClaimSupportVerifier
    from psh.workgraph import EdgeKind, NodeKind, WorkGraph

    graph = WorkGraph(tmp_path / "g.db")
    record = scoped_record("11111111", EUROPEAN_TRIAL)
    support = ClaimSupportVerifier().verify_record(
        statement="Drug A improves survival in elderly Asian women.", record=record)

    evidence = graph.add(NodeKind.EVIDENCE, "PMID 11111111",
                         licensed_population="; ".join(record.licensed_scope.population.descriptors))
    claim = graph.add(NodeKind.CLAIM, "Drug A improves survival in elderly Asian women")
    graph.link(evidence, claim, EdgeKind.EXTRAPOLATED_TO
               if support.scope_mismatch else EdgeKind.SUPPORTS,
               note=support.rationale[:200])

    edges = graph.neighbours(evidence.id, direction="out")
    assert edges
    kinds = {kind for kind, _ in edges}
    assert EdgeKind.EXTRAPOLATED_TO in kinds, \
        f"an out-of-scope link must be recorded as extrapolation, not support: {kinds}"
    assert EdgeKind.SUPPORTS not in kinds
