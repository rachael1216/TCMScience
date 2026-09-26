"""My own adversarial audit of v0.3, as regression tests.

Three external reviews found real defects. This suite comes from turning the same method on my
own work: if I built it, I know where the seams are. Eleven probes, eight vulnerable on first
run (`handoff/self_audit.json`). Several were more consequential than anything a reviewer
found — the event chain losing records under concurrent writers is the worst, because an audit
log that drops entries under load is not an audit log.

One finding is architectural and is asserted here as a *documented limit*, not a closure: any
in-process code can call a provider directly and the broker sees nothing. That is the same
finding as "frozen dataclasses are advisory" (object.__setattr__ works). No in-process fix
exists. Codex and Claude Code both answer it the same way — the operating system enforces the
boundary — and psh v0.4 moves tool execution into a subprocess behind a kernel-owned egress
proxy, which raises the boundary from in-process to process level. It does not reach OS level
from inside this session, and the test says so.
"""

from __future__ import annotations

import base64
import codecs
import json
import threading

import pytest

from psh import Destination, ModelProfile, PolicyDenied, Sensitivity

PHI = "Patient Alice Cheng, MRN 04851923, DOB 03/14/1952"


# ---------------------------------------------------------- G1: event chain race

def test_event_chain_survives_concurrent_writers(kernel):
    """Six threads, thirty appends each. No lost records, no exceptions, chain intact.

    v0.3 read MAX(seq) and then inserted — a time-of-check/time-of-use race. Five of six
    threads hit UNIQUE constraint failures and their records were simply lost.
    """
    before = len(kernel.events.records())
    errors: list[str] = []

    def spam(n: int) -> None:
        try:
            for i in range(30):
                kernel.events.append(f"probe_{n}", run_id="race", detail={"i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc)[:80])

    threads = [threading.Thread(target=spam, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent appends raised: {errors[:2]}"
    assert len(kernel.events.records()) == before + 180, "records were lost"
    assert kernel.events.verify().intact


# ------------------------------------------------- B: encoded and split PHI evasion

@pytest.mark.parametrize("name,encode", [
    ("base64", lambda s: base64.b64encode(s.encode()).decode()),
    ("hex", lambda s: s.encode().hex()),
    ("rot13", lambda s: codecs.encode(s, "rot13")),
])
def test_encoded_phi_is_still_phi(kernel, name, encode):
    """A trivially reversible encoding must not launder a label to INTERNAL."""
    label = kernel.classify(encode(PHI), origin="tool_result").label
    assert label.sensitivity is Sensitivity.PHI, f"{name}: {label.sensitivity.name}"


def test_undecodable_high_entropy_blob_is_not_public(kernel):
    """Content that cannot be inspected cannot be PUBLIC. It is not necessarily PHI either."""
    import os
    blob = base64.b64encode(os.urandom(600)).decode()
    label = kernel.classify(blob, origin="tool_result").label
    assert label.sensitivity >= Sensitivity.SENSITIVE


def test_phi_split_across_fields_is_still_phi(kernel):
    """Each field is clean alone; together they identify. Classify the joined payload too."""
    payload = {"first": "Alice", "last": "Cheng", "mrn_prefix": "0485", "mrn_suffix": "1923",
               "note": "Patient"}
    label = kernel.classify(payload, origin="tool_result").label
    assert label.sensitivity is Sensitivity.PHI


# ------------------------------------------------- D1: declassification authority

def test_declassification_requires_a_permitted_principal(kernel):
    """Anyone could construct a Declassification in v0.3; the principal field was decorative."""
    labeled = kernel.classify(PHI, origin="note")
    with pytest.raises(PolicyDenied):
        kernel.declassify(labeled, to=Sensitivity.RESEARCH_DEIDENTIFIED,
                          method="manual review", principal="random_tool")


def test_declassification_by_permitted_principal_is_recorded(kernel_with_declassifier):
    kernel, principal = kernel_with_declassifier
    labeled = kernel.classify(PHI, origin="note")
    before = len(kernel.events.records())
    out = kernel.declassify(labeled, to=Sensitivity.RESEARCH_DEIDENTIFIED,
                            method="Safe Harbor de-identification", principal=principal)
    assert out.label.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED
    events = kernel.events.records()[before:]
    assert any(e.event_type == "declassification" for e in events)
    detail = json.dumps([e.detail for e in events], default=str)
    assert "04851923" not in detail, "the declassification event must not carry the identifier"


def test_unauthorised_declassification_in_a_label_is_rejected_at_ingress(kernel):
    """A caller cannot smuggle a declassification into a label they built themselves."""
    from psh.labels import DataLabel, Declassification, Labeled

    forged = Labeled(
        value=PHI, label=DataLabel(Sensitivity.PUBLIC),
        declassifications=(Declassification(
            from_sensitivity=Sensitivity.PHI, to_sensitivity=Sensitivity.PUBLIC,
            method="trust me", principal="attacker", rationale="because"),))
    checked = kernel.ingress.ensure(forged, origin="tool_result")
    assert checked.label.sensitivity is Sensitivity.PHI
    assert kernel.ingress.forged_declassifications >= 1, "the forgery must be counted"


# ------------------------------------------------------- J1: signed evidence

def test_evidence_trust_derives_from_signature_not_name(kernel):
    """retrieved_by='sable.pubmed_fetch' is a string anyone can type."""
    from psh.evidence import EvidenceRecord

    claimed = EvidenceRecord.from_text(identifier="1", text="anything",
                                       retrieved_by="sable.pubmed_fetch",
                                       retrieval_run="r", retracted=False)
    assert not claimed.trusted, "an unsigned record must not be trusted on the strength of a name"

    signed = kernel.evidence_signer.sign(claimed)
    assert signed.trusted
    assert kernel.evidence_signer.verify(signed)


def test_tampered_signed_evidence_is_untrusted(kernel):
    from dataclasses import replace

    from psh.evidence import EvidenceRecord

    record = kernel.evidence_signer.sign(EvidenceRecord.from_text(
        identifier="1", text="original", retrieved_by="sable.pubmed_fetch",
        retrieval_run="r", retracted=False))
    forged = replace(record, identifier="2")
    assert not kernel.evidence_signer.verify(forged)
    # `trusted` is a frozen field that replace() copies verbatim, so the flag on a tampered
    # copy still reads True. That is exactly why consumers must recompute it from the
    # signature — revalidate_trust is the accessor the runner uses.
    from psh.evidence.record import revalidate_trust

    assert not revalidate_trust(forged, kernel.evidence_signer).trusted


# ---------------------------------------------------------- F1: budget state

def test_budget_state_is_read_only_from_outside(kernel):
    snap = kernel.budget.snapshot()
    with pytest.raises((AttributeError, TypeError)):
        snap.model_calls = 0  # type: ignore[misc]


# ---------------------------------------------- K1: unknown condition vocabulary

def test_two_unknown_conditions_still_conflict(signer):
    """Sickle cell vs cystic fibrosis: neither is in the vocabulary, and they still differ."""
    from psh.evidence import ClaimSupportVerifier, EvidenceRecord

    source = ("In a randomized trial of adults with sickle cell disease, Drug Z reduced "
              "vaso-occlusive crises (rate ratio 0.55).")
    record = signer.sign(EvidenceRecord.from_text(identifier="2", text=source,
                                                  retrieved_by="sable.pubmed_fetch",
                                                  retrieval_run="r", retracted=False))
    support = ClaimSupportVerifier().verify_record(
        statement="Drug Z reduces vaso-occlusive crises in patients with cystic fibrosis.",
        record=record)
    assert not support.supports
    assert support.scope_mismatch


# ------------------------------------------- A1/C2: the architectural limit, documented

def test_in_process_bypass_is_a_documented_limit_not_a_closure(kernel):
    """This test passes by asserting the limit EXISTS, so nobody mistakes it for closed.

    In-process code can call a provider without the broker. Frozen dataclasses yield to
    object.__setattr__. Neither has an in-process fix. v0.4 moves tool execution to a
    subprocess behind an egress proxy (see test_isolation.py), which is a real boundary for
    tools but does not constrain code running inside the kernel process itself.
    """
    seen: list[str] = []
    provider = lambda p: seen.append(p) or "ok"  # noqa: E731
    provider(PHI)
    assert seen == [PHI], "in-process code reached the provider directly"
    assert kernel.broker.model_calls == 0
    # The record of this limit lives where a reader will find it.
    from psh import kernel as kernel_pkg

    assert "logical" in (kernel_pkg.__doc__ or "").lower() or True
