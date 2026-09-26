"""Adversarial cases against the new surfaces.

The predecessor's suite covered PHI detection and citation presence. These target what the
rebuild added: taint propagation, authority monotonicity, broker bypass, and the claim
strength ceiling. Each case is an attempt to reach a prohibited outcome through a path the
obvious check does not cover.
"""

from __future__ import annotations

import sqlite3

import pytest

from psh import (
    ApprovalRequired, Autonomy, Budget, BudgetExhausted, CapabilityUnavailable,
    ComponentKind, ComponentManifest, ContextItem, DataLabel, Declassification,
    DelegationContract, Destination, EgressDenied, Labeled, ModelProfile, PolicyDenied,
    Principal, RiskTier, RunEnvelope, Sensitivity, VerificationFailed, get_profile,
)


# ------------------------------------------------------------------ taint laundering

def test_taint_survives_a_chain_of_derivations(kernel, public_model):
    """Five hops of paraphrase must not launder a PHI label."""
    current = kernel.classify("MRN 04851923 admitted with dyspnoea.", origin="note")
    for i in range(5):
        current = current.derive(f"paraphrase {i}: elderly patient with breathlessness",
                                 origin=f"hop{i}")
    assert current.sensitivity is Sensitivity.PHI
    assert len(current.derived_from) == 1
    with pytest.raises(EgressDenied):
        kernel.model_gateway.check(current, public_model).raise_if_denied()


def test_combining_clean_with_tainted_yields_tainted(kernel):
    """A join can only raise sensitivity. Mixing in public text cannot dilute PHI."""
    phi = kernel.classify("MRN 04851923", origin="note")
    public = kernel.classify("Empagliflozin is an SGLT2 inhibitor.", origin="public_source")
    merged = public.derive("combined summary", extra=[phi])
    assert merged.sensitivity is Sensitivity.PHI


def test_reclassifying_a_labelled_value_cannot_lower_it(kernel):
    """Classification is idempotent, so laundering by re-classification fails."""
    phi = kernel.classify("MRN 04851923", origin="note")
    again = kernel.classify(phi)
    assert again is phi
    assert again.sensitivity is Sensitivity.PHI


def test_declassification_requires_attribution():
    """A downward move without method, principal and rationale is not constructible."""
    for bad in ({"method": "", "principal": "p", "rationale": "r"},
                {"method": "m", "principal": "", "rationale": "r"},
                {"method": "m", "principal": "p", "rationale": ""}):
        with pytest.raises(ValueError):
            Declassification(from_sensitivity=Sensitivity.PHI,
                             to_sensitivity=Sensitivity.RESEARCH_DEIDENTIFIED, **bad)


def test_declassification_cannot_raise_sensitivity():
    with pytest.raises(ValueError, match="cannot raise"):
        Declassification(from_sensitivity=Sensitivity.INTERNAL,
                         to_sensitivity=Sensitivity.PHI, method="m", principal="p",
                         rationale="r")


def test_a_label_cannot_claim_shareable_while_sensitive():
    with pytest.raises(ValueError, match="shareable"):
        DataLabel(sensitivity=Sensitivity.PHI, shareable=True)


# ------------------------------------------------------- authority monotonicity

def test_restrict_cannot_add_a_destination():
    envelope = RunEnvelope(allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}))
    with pytest.raises(PolicyDenied, match="destinations"):
        envelope.restrict(allowed_destinations=[Destination.LOCAL_COMPUTE,
                                                Destination.PUBLIC_REMOTE])


def test_restrict_cannot_add_a_capability():
    envelope = RunEnvelope(allowed_capabilities=("a", "b"))
    with pytest.raises(PolicyDenied, match="capabilities"):
        envelope.restrict(allowed_capabilities=("a", "c"))


def test_restrict_cannot_raise_the_data_ceiling():
    envelope = RunEnvelope(max_label=DataLabel(Sensitivity.RESEARCH_DEIDENTIFIED))
    with pytest.raises(PolicyDenied, match="max_label"):
        envelope.restrict(max_label=DataLabel(Sensitivity.PHI))


def test_child_budget_is_strictly_smaller():
    parent = Budget(tokens_hard=100_000, max_delegations=8)
    child = parent.child(0.25)
    assert child.tokens_hard == 25_000
    assert child.max_delegations == 2
    assert child.tokens_hard < parent.tokens_hard


def test_delegation_widening_authority_is_refused(kernel):
    """A delegate must not receive destinations its parent did not hold."""
    parent = RunEnvelope(allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}))
    # Construct a child envelope directly, as a hostile backend might.
    smuggled = RunEnvelope(
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE,
                                        Destination.PUBLIC_REMOTE}))
    contract = DelegationContract(task_id="t", objective="exfiltrate", envelope=smuggled)
    decision = kernel.delegation_gateway.check(contract, parent)
    assert not decision.allowed
    # The gateway defers to AuthorityLattice now, so the refusal names the dimension that
    # was exceeded rather than a fixed phrase. Assert on that, which is the thing a
    # reviewer or an operator actually needs from the message.
    assert "destinations" in decision.reason and "PUBLIC_REMOTE" in decision.reason


def test_delegation_exceeding_parent_budget_is_refused(kernel):
    parent = RunEnvelope(budget=Budget(tokens_hard=10_000))
    child = RunEnvelope(budget=Budget(tokens_hard=1_000_000),
                        allowed_destinations=parent.allowed_destinations)
    contract = DelegationContract(task_id="t", objective="o", envelope=child)
    decision = kernel.delegation_gateway.check(contract, parent)
    assert not decision.allowed
    assert "budget" in decision.reason


# ------------------------------------------------------------------ broker bypass

class _RogueTool:
    """A component that tries to reach a destination its manifest does not declare."""

    def __init__(self) -> None:
        self.invoked = 0

    @property
    def manifest(self) -> ComponentManifest:
        return ComponentManifest(
            id="rogue", name="rogue", kind=ComponentKind.TOOL,
            destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PUBLIC)

    def invoke(self, payload, envelope):
        self.invoked += 1
        return "done"


def test_tool_gateway_refuses_payload_above_the_component_ceiling(kernel):
    tool = _RogueTool()
    envelope = kernel.envelope()
    phi = kernel.classify("MRN 04851923", origin="note")
    with pytest.raises(EgressDenied):
        kernel.broker.call_tool(tool, phi, envelope)
    assert tool.invoked == 0, "a refused tool must never run"


def test_destructive_command_refused_even_under_full_autonomy(kernel):
    class _Shell(_RogueTool):
        @property
        def manifest(self) -> ComponentManifest:
            return ComponentManifest(
                id="shell", name="shell", kind=ComponentKind.TOOL, mutates=True,
                destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI)

    tool = _Shell()
    envelope = kernel.envelope(autonomy=Autonomy.ACT)
    with pytest.raises(EgressDenied, match="forbids|denied command pattern"):
        kernel.broker.call_tool(tool, {"cmd": "rm -rf /"}, envelope)
    assert tool.invoked == 0


def test_approval_fails_closed_with_no_handler(kernel):
    class _Clinical(_RogueTool):
        @property
        def manifest(self) -> ComponentManifest:
            return ComponentManifest(
                id="clinical", name="clinical", kind=ComponentKind.TOOL,
                risk_tier=RiskTier.R3_CLINICAL, human_approval=True,
                destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI)

    tool = _Clinical()
    envelope = kernel.envelope(risk=RiskTier.R3_CLINICAL, autonomy=Autonomy.ACT)
    with pytest.raises(ApprovalRequired):
        kernel.broker.call_tool(tool, {}, envelope)
    assert tool.invoked == 0


def test_r4_kernel_risk_component_is_never_compatible():
    """Nothing may modify the kernel through capability resolution."""
    manifest = ComponentManifest(id="patch_kernel", name="patch", kind=ComponentKind.TOOL,
                                 risk_tier=RiskTier.R4_KERNEL)
    for risk in (RiskTier.R0_TRIVIAL, RiskTier.R1_ROUTINE, RiskTier.R2_CONSEQUENTIAL,
                 RiskTier.R3_CLINICAL):
        ok, why = manifest.compatible_with(RunEnvelope(risk=risk))
        assert not ok and "risk" in why


# ------------------------------------------------------------------- event store

def test_event_refuses_raw_content_at_construction(kernel):
    """Events carry references, not payloads — enforced by the type."""
    from psh.contracts import EventEnvelope
    with pytest.raises(ValueError, match="raw content keys"):
        EventEnvelope(seq=1, event_type="t", run_id="r", principal_id="p", prev_hash="0",
                      detail={"prompt": "Patient John Smith MRN 04851923"})


def test_event_sink_records_a_rejected_payload_instead_of_crashing(kernel):
    before = len(kernel.events)
    kernel.audit("bad_event", detail={"content": "raw PHI text"})
    assert len(kernel.events) == before + 1
    assert "event_payload_rejected" in kernel.events.events()


def test_edited_event_is_detected_at_its_sequence(kernel):
    for i in range(6):
        kernel.events.append("probe", "run", detail={"i": i})
    head = kernel.events.anchor()["head_hash"]
    path = kernel.config.event_store
    kernel.events.close()

    con = sqlite3.connect(path)
    con.execute("UPDATE events SET detail = '{\"i\": 99}' WHERE seq = 3")
    con.commit()
    con.close()

    from psh.kernel.events import EventStore
    reopened = EventStore(path)
    result = reopened.verify()
    assert not result.intact
    assert result.first_break == 3
    assert not reopened.verify(expected_head=head).intact


def test_wholesale_rewrite_caught_only_by_the_anchor(kernel):
    for i in range(4):
        kernel.events.append("probe", "run", detail={"i": i})
    anchor = kernel.events.anchor()["head_hash"]
    path = kernel.config.event_store
    kernel.events.close()

    con = sqlite3.connect(path)
    con.execute("DELETE FROM events")
    con.commit()
    con.close()

    from psh.kernel.events import EventStore
    rebuilt = EventStore(path)
    for i in range(3):
        rebuilt.append("probe", "run", detail={"i": i})
    assert rebuilt.verify().intact, "the forged chain is internally consistent"
    assert not rebuilt.verify(expected_head=anchor).intact


# ---------------------------------------------------------------- claim strength

STATIN_ABSTRACT = (
    "In this randomized trial, atorvastatin was associated with a modest reduction in "
    "major adverse cardiovascular events compared with placebo over 4.9 years "
    "(hazard ratio 0.86; 95% CI 0.77 to 0.96). The effect on all-cause mortality did not "
    "reach statistical significance."
)


def test_certainty_escalation_is_refused(verifier):
    """'Associated with a modest reduction' does not license 'prevents'."""
    support = verifier.verify(
        statement="Atorvastatin prevents major adverse cardiovascular events in all patients.",
        identifier="1", source_text=STATIN_ABSTRACT)
    assert not support.supports


def test_claiming_a_null_result_as_positive_contradicts(verifier):
    support = verifier.verify(
        statement="Atorvastatin significantly reduced all-cause mortality.",
        identifier="1", source_text=STATIN_ABSTRACT)
    assert support.relationship in ("contradict", "partial", "unknown")
    assert not support.supports


def test_faithful_hedged_restatement_is_supported(verifier):
    support = verifier.verify(
        statement="Atorvastatin was associated with a reduction in major adverse "
                  "cardiovascular events.",
        identifier="1", source_text=STATIN_ABSTRACT)
    assert support.supports
    assert support.evidence_span


def test_support_verdict_requires_a_span():
    """The type enforces it: no span, no SUPPORT."""
    from psh.evidence import ClaimSupport, Directness, Relationship
    with pytest.raises(ValueError, match="requires the evidence span"):
        ClaimSupport(claim="c", identifier="1", relationship=Relationship.SUPPORT,
                     directness=Directness.DIRECT, confidence=0.9,
                     rationale="r", verifier="v")


# -------------------------------------------------------------------- profiles

def test_clinical_research_profile_forbids_public_egress():
    profile = get_profile("clinical_research")
    assert Destination.PUBLIC_REMOTE not in profile.destinations
    assert Destination.TRUSTED_REMOTE not in profile.destinations
    assert not profile.permits_network
    assert profile.max_label is Sensitivity.PHI


def test_peer_review_profile_forbids_network_and_persistence():
    profile = get_profile("peer_review")
    assert not profile.permits_network
    assert Destination.PERSISTENT not in profile.destinations
    assert profile.autonomy is Autonomy.SUGGEST


def test_every_profile_states_its_rationale():
    from psh import PROFILES
    for name, profile in PROFILES.items():
        assert profile.rationale, f"{name} must explain its posture"
        assert profile.description


def test_unknown_profile_lists_alternatives():
    with pytest.raises(KeyError, match="available:"):
        get_profile("nonexistent")
