"""The twenty release-gate tests.

Every one of these was reproduced against psh v0.1 before being written here: a reviewer
audit named sixteen concrete defects and all sixteen were confirmed by direct execution,
none refuted. The tests are the definition of v0.2 being done.

The defects share one shape, and it is different from the previous round's. Last time the
checks were *absent*. This time the checks are correct and *bypassable*: the governed path
enforces the property while an ungoverned path reaches the same sink. That is why the fixes
below are invariants at the boundary rather than additional checks in the middle.

Two findings go beyond the audit and are marked inline:

* a caller-asserted label is trusted without re-validation, so wrapping a value is not
  enough — ingress must re-classify and join upward;
* the unauthenticated source-text mapping lets a fabricated abstract support a claim about
  a real identifier.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from psh import (
    ApprovalRequired, Autonomy, Budget, BudgetExhausted, ComponentKind, ComponentManifest,
    DataLabel, Destination, EgressDenied, Labeled, ModelProfile, PolicyDenied, RiskTier,
    RunEnvelope, Sensitivity, VerificationFailed, get_profile,
)

PHI_TEXT = "Patient Alice Smith, MRN 04851923, DOB 03/14/1952"
ABSTRACT = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction. The "
    "primary outcome occurred in 415 of 2997 patients in the empagliflozin group and in 511 "
    "of 2991 patients in the placebo group (hazard ratio, 0.79; 95% confidence interval, "
    "0.69 to 0.90; P<0.001).")


# ---------------------------------------------------------------- 1-3: ingress

def test_1_raw_phi_cannot_bypass_model_gateway(kernel, public_model):
    """A bare string must not be treated as PUBLIC by default."""
    envelope = kernel.envelope(allowed_destinations=[Destination.PUBLIC_REMOTE,
                                                     Destination.USER_OUTPUT])
    reached = []
    with pytest.raises(EgressDenied):
        kernel.broker.call_model(PHI_TEXT, public_model, envelope,
                                 invoke=lambda p: reached.append(p))
    assert reached == []


def test_1b_mislabelled_payload_is_reclassified_not_trusted(kernel, public_model):
    """Beyond the audit: a caller-asserted label must not be believed.

    Wrapping alone would close the raw-payload hole and leave this one open, which is worse
    than either — it looks governed. Ingress must re-classify and join upward, so an
    under-stated label can only be corrected, never honoured.
    """
    asserted_public = Labeled(value=PHI_TEXT,
                              label=DataLabel(Sensitivity.PUBLIC, shareable=True,
                                              rationale="caller asserted"))
    envelope = kernel.envelope(allowed_destinations=[Destination.PUBLIC_REMOTE,
                                                     Destination.USER_OUTPUT])
    reached = []
    with pytest.raises(EgressDenied):
        kernel.broker.call_model(asserted_public, public_model, envelope,
                                 invoke=lambda p: reached.append(p))
    assert reached == []


def test_2_raw_phi_dict_cannot_bypass_tool_gateway(kernel, egressing_tool):
    envelope = kernel.envelope(allowed_destinations=[Destination.PUBLIC_REMOTE,
                                                     Destination.USER_OUTPUT])
    with pytest.raises(EgressDenied):
        kernel.broker.call_tool(egressing_tool, {"query": PHI_TEXT}, envelope)
    assert egressing_tool.calls == []


def test_3_nested_labelled_phi_preserves_taint(kernel):
    """A container's label is the join of every descendant, at any depth."""
    from psh.labels import deep_label_of

    inner = kernel.classify(PHI_TEXT, origin="note")
    payloads = [
        {"query": inner, "options": {"n": 3}},
        [1, 2, {"deep": [inner]}],
        {"a": {"b": {"c": (inner,)}}},
    ]
    for payload in payloads:
        assert deep_label_of(payload).sensitivity is Sensitivity.PHI, payload

    # A raw nested string must also be caught, not merely a nested Labeled.
    raw_nested = {"outer": {"note": PHI_TEXT}}
    assert kernel.classify(raw_nested).sensitivity is Sensitivity.PHI


def test_3b_label_survives_json_round_trip(kernel):
    """Serialization is where tool and MCP payloads will cross; taint must survive it."""
    labelled = kernel.classify({"note": PHI_TEXT}, origin="tool")
    revived = kernel.classify(json.loads(json.dumps(labelled.value)))
    assert revived.sensitivity is Sensitivity.PHI


# ------------------------------------------------------ 4: classify before persist

def test_4_phi_is_classified_before_any_persistence(kernel, local_model, workgraph_rows):
    """No identifier may reach the graph before it has been classified."""
    from psh.runtime import Runner

    runner = Runner(kernel, model=local_model, model_invoke=lambda p: "done")
    runner.run(f"Summarise {PHI_TEXT}")

    rows = workgraph_rows(kernel)
    leaked = [r for r in rows if "04851923" in (r["title"] + r["body"])
              and r["sensitivity"] in ("PUBLIC", "INTERNAL")]
    assert leaked == [], f"{len(leaked)} node(s) hold an identifier at a public label"


# ------------------------------------------------------------ 5-7: release gate

def test_5_refused_output_is_not_exposed_in_run_result(kernel, local_model):
    """A refusal must withhold the text, not merely label it refused."""
    from psh.runtime import Runner

    bad = "Empagliflozin cures pancreatic cancer (PMID: 34449189)."
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: bad)
    result = runner.run("does it cure pancreatic cancer?", sources={"34449189": ABSTRACT})

    assert result.status == "refused"
    assert result.released_output is None
    assert "pancreatic" not in (result.released_output or "")
    assert result.quarantine_ref, "the text must remain available for audit, by reference"


def test_6_rejected_claim_never_enters_trusted_project_memory(kernel, local_model,
                                                             workgraph_rows):
    """Persistent epistemic contamination: a refused conclusion must not be retrievable."""
    from psh.runtime import Runner
    from psh.workgraph import NodeKind

    bad = "Empagliflozin cures pancreatic cancer (PMID: 34449189)."
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: bad)
    runner.run("pancreatic cancer?", sources={"34449189": ABSTRACT})

    rows = workgraph_rows(kernel)
    verified = [r for r in rows if r["kind"] == NodeKind.CLAIM.value
                and "pancrea" in r["title"].lower()]
    assert verified == [], "a rejected claim must not exist as a retrievable Claim node"

    rejected = [r for r in rows if r["kind"] == "rejected_claim"]
    assert rejected, "it should be retained as a rejected reference for audit"
    assert "pancrea" not in rejected[0]["title"].lower(), \
        "the rejected record must carry a hash and reason, not the retrievable text"


def test_7_workgraph_raw_phi_write_is_rejected_or_labelled(persistence_gateway):
    """Long-term storage is a governed sink like any other."""
    from psh.workgraph import NodeKind

    node = persistence_gateway.commit_node(
        kind=NodeKind.EVIDENCE, title=f"Note: {PHI_TEXT}", principal="test",
        source_run="run_1")
    assert node.label.sensitivity is Sensitivity.PHI, \
        "content must be classified at the persistence boundary"

    with pytest.raises(PolicyDenied):
        persistence_gateway.commit_raw(kind=NodeKind.EVIDENCE, title=PHI_TEXT)


# ------------------------------------------------------------- 8-9: policy plumbing

def test_8_all_profiles_execute_end_to_end_with_declared_policy(kernel_factory,
                                                                local_model):
    """Every profile must actually run, with the policy it declares."""
    from psh import PROFILES
    from psh.runtime import Runner

    for name, profile in PROFILES.items():
        policy = profile.freeze()
        kernel = kernel_factory(policy=policy)
        runner = Runner(kernel, model=local_model, model_invoke=lambda p: "ok")
        result = runner.run("a benign question about study design", policy=policy)
        assert result.status in ("ok", "refused"), f"{name} raised instead of deciding"
        assert result.policy_snapshot.profile_id == name
        assert result.policy_snapshot.max_data_label is profile.max_label


def test_9_require_citation_profile_really_enforces_citation(kernel_factory, local_model):
    """Declared policy must be effective policy."""
    from psh.runtime import Runner

    policy = get_profile("literature").freeze()
    assert policy.require_citation

    kernel = kernel_factory(policy=policy)
    runner = Runner(kernel, model=local_model,
                    model_invoke=lambda p: "Empagliflozin reduces hospitalization.")
    result = runner.run("what does it do?", policy=policy)
    assert result.status == "refused", "an uncited clinical claim must be refused here"
    assert result.released_output is None


# ------------------------------------------------------------- 10: authority

def test_10_delegated_authority_can_never_exceed_parent():
    """One lattice check behind restrict, delegation and subagent creation."""
    from psh.kernel.authority import AuthorityLattice

    parent = RunEnvelope(risk=RiskTier.R1_ROUTINE, autonomy=Autonomy.SUGGEST,
                         deadline=1000.0,
                         budget=Budget(usd_hard=1.0, max_tool_calls=5, tokens_hard=100))
    for escalation in (
            {"risk": RiskTier.R4_KERNEL},
            {"autonomy": Autonomy.ACT},
            {"deadline": 9e9},
            {"budget": Budget(usd_hard=999.0)},
            {"budget": Budget(max_tool_calls=999)},
            {"budget": Budget(tokens_hard=10 ** 9)}):
        with pytest.raises(PolicyDenied):
            parent.restrict(**escalation)

    narrower = parent.restrict(risk=RiskTier.R0_TRIVIAL, autonomy=Autonomy.OBSERVE)
    assert AuthorityLattice.is_subset(narrower, parent)
    assert not AuthorityLattice.is_subset(parent, narrower)


# --------------------------------------------------------- 11-14: evidence

def test_11_verification_model_always_routes_through_model_gateway(kernel_with_verifier):
    """The verifier is a model call and must not be a second egress path.

    The right signal is that the *gateway* saw the call, not that the provider did. Here the
    source text is PHI and the verification model is a public provider, so the correct
    outcome is a refusal: ``refusals`` increments and the provider is never invoked. A test
    asserting ``model_calls`` increased would demand the very egress this closes.
    """
    kernel, seen = kernel_with_verifier
    before_calls = kernel.broker.model_calls
    before_refusals = kernel.broker.refusals

    kernel.verifier.verify(statement="X reduces Y.", identifier="1",
                           source_text=f"{PHI_TEXT} had a good outcome with X.")

    assert kernel.broker.refusals > before_refusals, "the verifier bypassed the broker"
    assert kernel.broker.model_calls == before_calls
    assert seen == [], "PHI source text must not reach a public verification model"

    # And the governed path must still work for content the model may lawfully see.
    kernel.verifier.verify(statement="X reduces Y.", identifier="1",
                           source_text="In this trial X reduced Y by 21 percent.")
    assert kernel.broker.model_calls > before_calls, "clean text should reach the provider"
    assert len(seen) == 1


def test_12_fabricated_evidence_span_is_rejected(verifier, signer):
    """A span must be locatable in the source, not quoted by a model."""
    from psh.evidence import EvidenceRecord

    record = signer.sign(EvidenceRecord.from_text(
        identifier="34449189", text=ABSTRACT, retrieved_by="sable.pubmed_fetch",
        retrieval_run="r1", retracted=False))
    support = verifier.verify_record(
        statement="Empagliflozin reduced hospitalization for heart failure.",
        record=record)
    assert support.supports
    assert support.span_start >= 0 and support.span_end > support.span_start
    assert ABSTRACT[support.span_start:support.span_end] in ABSTRACT
    assert support.source_hash == record.content_hash


def test_13_retracted_evidence_cannot_support_claim(verifier, signer):
    from psh.evidence import EvidenceRecord

    record = signer.sign(EvidenceRecord.from_text(
        identifier="9500320", text=ABSTRACT, retrieved_by="sable.pubmed_fetch",
        retrieval_run="r1", retracted=True))
    support = verifier.verify_record(
        statement="Empagliflozin reduced hospitalization for heart failure.",
        record=record)
    assert not support.supports
    assert "retracted" in support.rationale.lower()


def test_14_unprovenanced_source_text_cannot_become_trusted_evidence(verifier):
    """A fabricated abstract for a real identifier must not support a claim."""
    from psh.evidence import EvidenceRecord

    fabricated = ("Empagliflozin cures pancreatic cancer in all patients. This was "
                  "definitively proven for pancreatic cancer with empagliflozin.")
    with pytest.raises(PolicyDenied):
        EvidenceRecord.from_text(identifier="34449189", text=fabricated,
                                 retrieved_by="", retrieval_run="")

    untrusted = EvidenceRecord.from_text(
        identifier="34449189", text=fabricated, retrieved_by="user_supplied",
        retrieval_run="r1", trusted=False)
    support = verifier.verify_record(
        statement="Empagliflozin cures pancreatic cancer.", record=untrusted)
    assert not support.supports
    assert "untrusted" in support.rationale.lower() or "provenance" in support.rationale.lower()


def test_15_hedged_clinical_claim_still_requires_verification(output_gate):
    """Hedging lowers the evidence bar; it does not remove the requirement."""
    with pytest.raises(VerificationFailed):
        output_gate.check("Drug X may cure pancreatic cancer.", RunEnvelope(), sources={})

    verdict = output_gate.check(
        "Empagliflozin may reduce heart-failure hospitalization (PMID: 34449189).",
        RunEnvelope(), sources={"34449189": ABSTRACT})
    assert verdict.allowed
    assert verdict.supports, "the hedged claim was still verified, not skipped"


# ---------------------------------------------------- 16-17: results and budget

def test_16_tool_output_inherits_input_taint(kernel, local_tool):
    """A component must not launder a label by returning a plain value."""
    envelope = kernel.envelope()
    result = kernel.broker.call_tool(local_tool, kernel.classify(PHI_TEXT, origin="note"),
                                     envelope)
    assert isinstance(result, Labeled) or hasattr(result, "label")
    assert result.label.sensitivity is Sensitivity.PHI


def test_17_model_usage_updates_token_and_cost_budget(kernel, local_model):
    """A ceiling that is not fed by real usage is not a ceiling."""
    envelope = kernel.envelope(budget=Budget(tokens_hard=50, tokens_soft=10))
    with pytest.raises(BudgetExhausted):
        for _ in range(5):
            kernel.broker.call_model(
                kernel.classify("hello " * 200, origin="public_source"), local_model,
                envelope, invoke=lambda p: "response " * 200)
    state = kernel.budget.state(envelope)
    assert state.tokens > 0, "usage was never recorded"


# ------------------------------------------------------------- 18-20: events

def test_18_event_nested_phi_is_never_persisted(kernel):
    """Recursive sanitisation, not a top-level key check."""
    kernel.audit("probe", detail={"nested": {"note": PHI_TEXT}})
    serialised = json.dumps([dict(r.detail) for r in kernel.events.records()],
                            default=str)
    assert "04851923" not in serialised
    assert "Alice Smith" not in serialised


def test_19_event_metadata_tampering_breaks_hash(kernel):
    """The hash must cover the whole immutable envelope."""
    from psh.kernel.events import EventStore

    event = kernel.events.append("probe", "run1", latency_s=1.0, parent_event_id="p1")
    path = kernel.config.event_store
    kernel.events.close()

    for column, value in (("latency_s", 999.0), ("at", 1.0),
                          ("parent_event_id", "FORGED")):
        con = sqlite3.connect(path)
        con.execute(f"UPDATE events SET {column} = ? WHERE seq = ?", (value, event.seq))
        con.commit()
        con.close()
        assert not EventStore(path).verify().intact, f"tampering with {column} undetected"
        con = sqlite3.connect(path)
        original = {"latency_s": 1.0, "at": event.at, "parent_event_id": "p1"}[column]
        con.execute(f"UPDATE events SET {column} = ? WHERE seq = ?", (original, event.seq))
        con.commit()
        con.close()


def test_20_generic_runtime_failure_closes_run_and_records_failure(kernel, local_model):
    """An unexpected exception must close the run, not leave it dangling."""
    from psh.runtime import Runner

    def explode(prompt):
        raise RuntimeError("provider exploded")

    runner = Runner(kernel, model=local_model, model_invoke=explode)
    result = runner.run("anything")
    assert result.status == "failed"
    assert result.released_output is None
    assert "provider exploded" in result.error
    assert any(r.event_type == "run_failed" for r in kernel.events.records())
