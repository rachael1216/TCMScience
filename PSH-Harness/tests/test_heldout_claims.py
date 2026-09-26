"""Held-out claim classes: error modes the generated benchmark does not produce.

The generated benchmark reaches 29/29, which is evidence of internal consistency rather than
of capability — its perturbations and the verifier's checks were written by the same author,
so it measures the failure modes that were imagined. These cases were written *after* the
verifier, deliberately outside the perturbation grammar, and on first run **5 of 6 failed**.

They are kept as tests because they document where the boundary actually is. Four were fixed;
one known false refusal is asserted as-is, so that "fixing" it by loosening the numeric check
fails here rather than silently trading a false refusal for false accepts.
"""

from __future__ import annotations

import pytest

SOURCE = ("In a randomized trial of patients with HFpEF, empagliflozin reduced "
          "hospitalization for heart failure (hazard ratio 0.79; 95% CI 0.69 to 0.90).")


@pytest.fixture
def verify(signer):
    from psh.evidence import ClaimSupportVerifier, EvidenceRecord

    record = signer.sign(EvidenceRecord.from_text(
        identifier="34449189", text=SOURCE, retrieved_by="sable.pubmed_fetch",
        retrieval_run="heldout", retracted=False))
    verifier = ClaimSupportVerifier()
    return lambda statement: verifier.verify_record(statement=statement, record=record)


# ------------------------------------------------------- normative overreach

def test_guideline_leap_is_not_supported(verify):
    """A single efficacy trial does not establish standard of care.

    Scored 0.75 SUPPORT before this was fixed, on two shared terms. The sentence names no
    measured outcome, so an outcome-based gate excluded it from checking altogether — which
    inverted the priority, since a recommendation is the assertion most in need of evidence.
    """
    support = verify("Empagliflozin is first-line therapy for HFpEF.")
    assert not support.supports
    assert support.structured.normative


def test_recommendation_appended_to_a_true_finding_is_not_supported(verify):
    """The dangerous form: a supported finding carrying a recommendation.

    It inherits the finding's lexical overlap, so it scored 0.85 — higher than many honest
    claims. Evidence licenses what was observed, never what should be done.
    """
    support = verify("Empagliflozin reduced hospitalization for heart failure in HFpEF, "
                     "and should be offered to all such patients.")
    assert not support.supports


def test_class_generalisation_from_one_drug_is_not_supported(verify):
    """A trial of one agent does not license a claim about its whole class."""
    assert not verify(
        "SGLT2 inhibitors reduce hospitalization for heart failure in HFpEF.").supports


# ------------------------------------------------- parsing must not cause false refusals

def test_passive_voice_is_still_supported(verify):
    """"Hospitalization was reduced by X" is the same claim as "X reduced hospitalization".

    Refused before the fix, because the outcome noun heading the clause was taken as the
    subject and then reported as a subject mismatch — a false refusal produced entirely by
    shallow parsing.
    """
    support = verify(
        "Hospitalization for heart failure was reduced by empagliflozin in HFpEF.")
    assert support.supports, support.rationale


def test_causal_phrasing_from_an_rct_is_still_supported(verify):
    """"X causes reduced Y" is legitimate from a randomised trial.

    Refused before the fix because "causes" was captured as the claim's subject.
    """
    assert verify("Empagliflozin causes reduced hospitalization in HFpEF.").supports


# --------------------------------------------- a known, deliberate false refusal

def test_derived_percentage_is_refused_and_this_is_deliberate(verify):
    """1 - 0.79 = 21% is arithmetically right, and the verifier still refuses it.

    Asserted as-is rather than fixed. The numeric check requires a claimed value to appear in
    the source, which is what catches magnitude alteration — the class where a claim is
    otherwise word-for-word correct. Accepting derived arithmetic would mean accepting
    *some* transformations of source numbers, and the verifier cannot distinguish a correct
    derivation from an invented one without doing the arithmetic itself.

    So this is a false refusal in the safe direction, and it is documented rather than
    silently traded away. If a future version teaches the verifier to derive relative risk
    reduction, this test should change to assert support — but only alongside a magnitude
    test proving the fabricated variant is still caught.
    """
    support = verify(
        "Empagliflozin reduced hospitalization for heart failure in HFpEF by 21%.")
    assert not support.supports
    assert "no such value appears" in support.rationale

    # The guard this refusal buys: a fabricated magnitude is still caught.
    assert not verify(
        "Empagliflozin reduced hospitalization with a hazard ratio of 0.20.").supports
