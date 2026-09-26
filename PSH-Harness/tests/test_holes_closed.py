"""The two demonstrated defects, as permanent regression tests.

Both of these FAIL against the predecessor design (sable) and must PASS against psh.
They were written before the fixes, from a live demonstration rather than from
speculation, and they are the definition of success for this rebuild.

Provenance of each:

HOLE 1 — model egress. sable gated tool arguments only. A local tool marked
``reads_phi=True`` read a chart note into the transcript; the next model call carried the
MRN to the provider. Measured: ``tool_calls_blocked=0``, ``model calls carrying the MRN=1``.
The predecessor's own test suite documented this as a *feature* ("the boundary gates
egress, not utility"), which is why it survived review.

HOLE 2 — claim support. sable verified that a citation existed, was retrieved, and was
not retracted. It never checked whether the source supported the sentence. Measured:
three fabricated conclusions attached to real, retrieved, un-retracted PMID 34449189 all
returned ``allow``.
"""

from __future__ import annotations

import pytest

from psh.contracts import EgressDenied, RunEnvelope, VerificationFailed
from psh.labels import Destination, Labeled, Sensitivity


# --------------------------------------------------------------------------- hole 1

PHI_NOTE = "Patient: John Smith, MRN 04851923, DOB 03/14/1952. Started empagliflozin."


def test_phi_note_cannot_reach_a_public_model_provider(kernel, public_model):
    """The exact leak: PHI in *context*, not in a tool argument, heading for a provider."""
    labeled = kernel.classify(PHI_NOTE, origin="read_note")
    assert labeled.sensitivity is Sensitivity.PHI, "classifier must see the MRN"

    with pytest.raises(EgressDenied) as excinfo:
        kernel.model_gateway.check(labeled, public_model).raise_if_denied()

    assert excinfo.value.destination is Destination.PUBLIC_REMOTE
    # The refusal must not quote the identifier it was protecting.
    assert "04851923" not in str(excinfo.value)
    assert "John Smith" not in str(excinfo.value)


def test_summary_of_a_phi_note_is_still_phi(kernel, public_model):
    """Taint laundering: the derived text contains no identifier, yet remains PHI.

    This is the case string scanning cannot catch and is the reason classification is
    attached to data rather than to call sites.
    """
    note = kernel.classify(PHI_NOTE, origin="read_note")
    summary = note.derive("Elderly male with heart failure, started on an SGLT2 inhibitor.",
                          origin="summarise")

    assert "04851923" not in summary.value, "the derived text is genuinely identifier-free"
    assert summary.sensitivity is Sensitivity.PHI, "but it is still PHI by derivation"
    assert note.id in summary.derived_from

    with pytest.raises(EgressDenied):
        kernel.model_gateway.check(summary, public_model).raise_if_denied()


def test_the_same_note_may_reach_a_local_model(kernel, local_model):
    """The boundary must gate egress without destroying local utility."""
    note = kernel.classify(PHI_NOTE, origin="read_note")
    decision = kernel.model_gateway.check(note, local_model)
    assert decision.allowed


def test_declassified_note_may_egress_and_records_who_did_it(kernel_with_declassifier,
                                                              public_model):
    """Declassification is the only path downward, and it is always attributable.

    v0.3's version of this test passed with principal "dr-user" against a kernel whose
    policy named nobody — which was the defect my own audit later found. The principal must
    now be one the policy permits, so the test uses a kernel that permits one.
    """
    kernel, principal = kernel_with_declassifier
    note = kernel.classify(PHI_NOTE, origin="read_note")
    deidentified = kernel.declassify(
        note, to=Sensitivity.RESEARCH_DEIDENTIFIED, method="safe_harbor_redaction",
        principal=principal, rationale="18 identifiers removed and verified")
    assert kernel.model_gateway.check(deidentified, public_model).allowed
    assert deidentified.declassifications[0].principal == principal


def test_no_model_call_bypasses_the_gateway(kernel, public_model):
    """Every model call routes through the gateway, counted, with no side door."""
    before = kernel.model_gateway.checks
    clean = kernel.classify("empagliflozin heart failure randomized", origin="query")
    kernel.model_gateway.check(clean, public_model)
    assert kernel.model_gateway.checks == before + 1


# --------------------------------------------------------------------------- hole 2

#: A real abstract fragment for PMID 34449189 (EMPEROR-Preserved). Used offline so the
#: test does not depend on the network; the live variant is marked ``live``.
EMPEROR_ABSTRACT = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization "
    "for heart failure in patients with heart failure and a preserved ejection fraction, "
    "irrespective of the presence or absence of diabetes. Empagliflozin 10 mg once daily "
    "was compared with placebo in 5988 patients. The primary outcome occurred in 415 of "
    "2997 patients in the empagliflozin group and in 511 of 2991 patients in the placebo "
    "group (hazard ratio, 0.79; 95% confidence interval, 0.69 to 0.90; P<0.001). "
    "The effect was mainly related to a lower risk of hospitalization for heart failure."
)

FABRICATED = [
    "Empagliflozin cures type 2 diabetes and eliminates the need for insulin.",
    "Empagliflozin is contraindicated in all patients over 65.",
    "Empagliflozin reduced all-cause mortality by 87%.",
]


@pytest.mark.parametrize("statement", FABRICATED)
def test_fabricated_conclusion_is_not_supported_by_a_real_citation(verifier, statement):
    """The three sentences sable allowed. Each must now fail support verification."""
    support = verifier.verify(statement=statement, identifier="34449189",
                              source_text=EMPEROR_ABSTRACT)
    assert not support.supports, f"must not pass: {statement!r} ({support.relationship})"
    assert support.relationship in ("contradict", "unknown", "partial")
    assert support.verifier, "the verdict must record which verifier produced it"


def test_a_faithful_claim_is_supported():
    """Specificity matters as much as sensitivity: real conclusions must pass."""
    from psh.evidence import ClaimSupportVerifier
    support = ClaimSupportVerifier().verify(
        statement=("Empagliflozin reduced cardiovascular death or heart-failure "
                   "hospitalization in HFpEF."),
        identifier="34449189", source_text=EMPEROR_ABSTRACT)
    assert support.supports
    assert support.relationship == "support"
    assert support.evidence_span, "a supporting verdict must point at the span it used"


def test_claim_may_not_be_stronger_than_its_evidence(verifier):
    """A hedged finding restated as certainty overstates the source."""
    support = verifier.verify(
        statement="Empagliflozin always prevents heart-failure hospitalization in every patient.",
        identifier="34449189", source_text=EMPEROR_ABSTRACT)
    assert not support.supports
    assert "strength" in support.rationale.lower() or support.relationship != "support"


def test_numeric_claim_not_present_in_source_is_refused(verifier):
    """87% appears nowhere in the abstract; a number must be found to be supported."""
    support = verifier.verify(statement="Mortality fell by 87%.", identifier="34449189",
                              source_text=EMPEROR_ABSTRACT)
    assert not support.supports
    assert "87" in support.rationale


def test_verifier_refuses_rather_than_guesses_without_source_text(verifier):
    """No abstract means unknown, never 'supported'."""
    support = verifier.verify(statement="Empagliflozin reduces hospitalization.",
                              identifier="34449189", source_text=None)
    assert not support.supports
    assert support.relationship == "unknown"


def test_final_output_gate_blocks_unsupported_clinical_claims(kernel):
    """End to end: an unsupported clinical sentence cannot reach the user."""
    envelope = RunEnvelope()
    with pytest.raises(VerificationFailed) as excinfo:
        kernel.output_gate.check(
            "Empagliflozin cures type 2 diabetes (PMID: 34449189).",
            envelope=envelope, sources={"34449189": EMPEROR_ABSTRACT})
    assert "34449189" in str(excinfo.value)


def test_broker_enforces_rather_than_merely_inspecting(kernel, public_model):
    """The gate's check() reports; the broker raises. Production code takes the broker.

    Keeping both is deliberate — pre-flight inspection needs a non-raising form — but the
    broker must never be the inspecting one, or the whole gate becomes advisory.
    """
    from psh.contracts import Destination as _D  # noqa: F401  (import shape check)
    note = kernel.classify(PHI_NOTE, origin="read_note")
    envelope = kernel.envelope(allowed_destinations=[Destination.PUBLIC_REMOTE,
                                                    Destination.USER_OUTPUT])
    reached = []
    with pytest.raises(EgressDenied):
        kernel.broker.call_model(note, public_model, envelope,
                                 invoke=lambda prompt: reached.append(prompt))
    assert reached == [], "the provider callable must never be invoked on a refusal"
    assert kernel.broker.model_calls == 0
    assert kernel.broker.refusals == 1


# ------------------------------------------------- silent-success regression (found here)

def test_compiler_must_not_silently_drop_the_users_question(kernel):
    """A PHI question bound for a public model must REFUSE, not answer a redacted version.

    Found while testing the trusted path: the compiler's policy filter applied to every
    item kind, so a PHI-bearing user turn heading for a public provider was simply removed
    and the run completed 13/13 stages against a clean-but-meaningless context. Answering a
    different question than the one asked is a worse failure than refusing, so the
    instruction block and the current turn are now never dropped.
    """
    from psh.contracts import ModelProfile
    from psh.runtime import Runner

    public = ModelProfile(id="frontier", provider="cloud",
                          destination=Destination.PUBLIC_REMOTE,
                          max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    invoked = []
    runner = Runner(kernel, model=public, model_invoke=lambda p: invoked.append(p) or "x")
    result = runner.run("Summarise the course for Mrs. Alice Cheng, MRN 04851923.",
                        allowed_destinations=[Destination.PUBLIC_REMOTE,
                                              Destination.USER_OUTPUT])
    assert result.status == "refused", "must refuse rather than answer a redacted question"
    # v0.2 refuses earlier than v0.1 did. Classification now runs before the envelope is
    # minted, so a PHI request under a public-only policy is refused while the policy is
    # being applied rather than at execute. Refusing sooner is the improvement; what the
    # test guards is that the question is never silently dropped and completed anyway.
    assert result.refused_at in ("policy_snapshot", "preflight", "execute")
    assert result.status == "refused"
    assert result.released_output is None
    assert invoked == [], "the provider must never be reached"

    # The silent-drop guarantee is a property of the compiler, so assert it there. The run
    # above now refuses before reaching compilation, which is a stronger outcome but would
    # otherwise leave the original defect untested.
    from psh.context import ContextCompiler
    from psh.contracts import ContextItem

    compiler = ContextCompiler()
    question = "Summarise the course for Mrs. Alice Cheng, MRN 04851923."
    projection = compiler.compile(
        items=[ContextItem(kind="instruction", content="You are an assistant."),
               ContextItem(kind="turn", content=question,
                           label=kernel.classify(question).label)],
        envelope=kernel.envelope(), destination=Destination.PUBLIC_REMOTE,
        token_budget=4000, query=question)
    assert any(i.kind == "turn" for i in projection.items), \
        "the user's question must remain in the projection so the gateway can judge it"
    assert projection.label.sensitivity is Sensitivity.PHI
