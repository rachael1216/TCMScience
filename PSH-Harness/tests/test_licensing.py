"""Licence and integration provenance as a governed policy dimension.

Adopted from BioScience-Harness, which is the one place its policy model is stronger than
this package's. The question it answers is not "is this licence acceptable" but "is it
acceptable **for this integration mode**", because the same licence gives different
answers:

    vendor      copy the upstream implementation in    -> redistribution
    native      call an independent equivalent         -> no upstream code at all
    federated   invoke upstream in its own process     -> use, not redistribution

Unlicensed code may be invoked and may not be copied. A single allow/deny per licence
cannot say that, which is why this is a table plus two subset dimensions rather than a flag.

These tests also stand as the evidence for a claim made in
``docs/ROADMAP_AGENT_RUNTIME.md``: that adding a governed dimension to this package should
now be cheap. Adding it cost naming it in ``AuthorityLattice.violations``/``meet``,
``PolicyLattice``, and the property test's dimension list. Everything else —
``restrict``, delegation, ``with_``, the meet, ``ToolGateway`` — governs it without being
told, and that is what the tests below check.
"""

from __future__ import annotations

import pytest

from psh.contracts import (
    Autonomy, ComponentKind, ComponentManifest, PolicyDenied, RiskTier, RunEnvelope,
)
from psh.kernel.authority import AuthorityLattice
from psh.kernel.egress import DelegationGateway, ToolGateway
from psh.contracts import DelegationContract
from psh.labels import Destination, Sensitivity
from psh.licensing import (
    INTEGRATION_MODES, LICENSE_CLASSES, LicenseDecision, classify_license,
    license_ruling, normalise_mode,
)
from psh.policy import PolicyLattice, PolicySnapshot


# ==================================================================== the table

@pytest.mark.parametrize("spdx,mode,expected", [
    ("MIT", "vendor", LicenseDecision.ALLOW),
    ("Apache-2.0", "vendor", LicenseDecision.ALLOW),
    ("GPL-3.0", "vendor", LicenseDecision.PREFER_ALTERNATIVE),
    ("GPL-3.0", "federated", LicenseDecision.ALLOW),
    ("GPL-3.0", "native", LicenseDecision.ALLOW),
    ("NOASSERTION", "vendor", LicenseDecision.DENY),
    ("", "vendor", LicenseDecision.DENY),
    ("", "federated", LicenseDecision.ALLOW),
    ("PROPRIETARY", "vendor", LicenseDecision.DENY),
])
def test_the_rule_depends_on_the_mode_not_only_the_licence(spdx, mode, expected):
    assert license_ruling(spdx, mode).decision is expected


def test_an_unrecognised_licence_is_unlicensed_not_permissive():
    """Failing closed here is the point: a typo must not read as a grant."""
    assert classify_license("Weird-Lab-Licence-1.0") == "none"
    assert not license_ruling("Weird-Lab-Licence-1.0", "vendor").allowed
    # ...and it is still invocable, because using it is not copying it.
    assert license_ruling("Weird-Lab-Licence-1.0", "federated").allowed


def test_an_unknown_integration_mode_is_refused_rather_than_ignored():
    ruling = license_ruling("MIT", "sneak-it-in")
    assert not ruling.allowed and ruling.rule == "license.default_deny"


def test_the_bioscience_vocabulary_is_accepted():
    """``adapter-only`` is BioScience's name for federated; the alias is kept."""
    assert normalise_mode("adapter-only") == "federated"
    assert normalise_mode("adapter_only") == "federated"
    assert license_ruling("MIT", "adapter-only").mode == "federated"


# ============================================================== manifests

def test_a_manifest_that_never_considered_licensing_is_still_usable():
    """The default is the weakest claim, so silence does not assert a right to copy."""
    plain = ComponentManifest(id="plain", name="p", kind=ComponentKind.TOOL)
    assert plain.integration_mode == "federated"
    assert plain.compatible_with(RunEnvelope())[0]


def test_a_manifest_with_an_invalid_mode_does_not_construct():
    with pytest.raises(ValueError, match="integration_mode"):
        ComponentManifest(id="b", name="b", kind=ComponentKind.TOOL,
                          integration_mode="borrow")


def test_vendored_unlicensed_code_is_refused_at_the_gate():
    """The enforcement point is the gate every tool call already passes."""
    manifest = ComponentManifest(id="vendored", name="v", kind=ComponentKind.TOOL,
                                 license_spdx="NOASSERTION", integration_mode="vendor")
    decision = ToolGateway().check({"q": "x"}, manifest, RunEnvelope(autonomy=Autonomy.ACT))
    assert not decision.allowed
    assert "does not permit vendor use" in decision.reason


def test_the_same_component_invoked_rather_than_copied_is_allowed():
    federated = ComponentManifest(id="upstream", name="u", kind=ComponentKind.TOOL,
                                  license_spdx="NOASSERTION",
                                  integration_mode="federated")
    assert ToolGateway().check({"q": "x"}, federated,
                               RunEnvelope(autonomy=Autonomy.ACT)).allowed


# =============================================================== the policy axis

def test_a_profile_may_forbid_vendoring_outright():
    """A group that distributes its pipelines narrows the mode set and stops copying."""
    policy = PolicySnapshot(profile_id="redistributes",
                            allowed_integration_modes=("native", "federated"))
    envelope = policy.envelope()
    mit_vendored = ComponentManifest(id="m", name="m", kind=ComponentKind.TOOL,
                                     license_spdx="MIT", integration_mode="vendor")
    ok, why = mit_vendored.compatible_with(envelope)
    assert not ok and "integrated as 'vendor'" in why


def test_a_profile_may_forbid_a_whole_licence_class():
    policy = PolicySnapshot(profile_id="no_copyleft",
                            allowed_license_classes=("permissive",))
    gpl = ComponentManifest(id="g", name="g", kind=ComponentKind.TOOL,
                            license_spdx="GPL-3.0", integration_mode="federated")
    ok, why = gpl.compatible_with(policy.envelope())
    assert not ok and "copyleft" in why


def test_the_policy_is_a_ceiling_on_this_dimension_too():
    narrow = PolicySnapshot(profile_id="narrow",
                            allowed_integration_modes=("federated",))
    with pytest.raises(PolicyDenied, match="allowed_integration_modes"):
        narrow.with_(allowed_integration_modes=INTEGRATION_MODES)
    with pytest.raises(PolicyDenied, match="allowed_integration_modes"):
        narrow.envelope(allowed_integration_modes=("vendor", "federated"))


def test_policy_meet_intersects_the_licence_dimensions():
    a = PolicySnapshot(profile_id="a", allowed_integration_modes=("native", "federated"),
                       allowed_license_classes=("permissive", "none"))
    b = PolicySnapshot(profile_id="b", allowed_integration_modes=INTEGRATION_MODES,
                       allowed_license_classes=LICENSE_CLASSES)
    met = PolicyLattice.meet(b, a)
    assert set(met.allowed_integration_modes) == {"native", "federated"}
    assert set(met.allowed_license_classes) == {"permissive", "none"}
    assert PolicyLattice.is_narrower(met, a) and PolicyLattice.is_narrower(met, b)


# ================================================== governed without new logic

def test_a_delegate_cannot_re_enable_a_mode_its_parent_gave_up():
    """The point of one lattice: the delegation gateway needed no change for this."""
    parent = RunEnvelope(allowed_integration_modes=("native", "federated"))
    child = RunEnvelope(allowed_integration_modes=INTEGRATION_MODES)
    decision = DelegationGateway().check(
        DelegationContract(task_id="t", objective="x", envelope=child), parent)
    assert not decision.allowed
    assert "allowed_integration_modes" in decision.reason


def test_restrict_refuses_the_same_widening():
    parent = RunEnvelope(allowed_license_classes=("permissive",))
    with pytest.raises(PolicyDenied, match="allowed_license_classes"):
        parent.restrict(allowed_license_classes=("permissive", "copyleft"))


def test_restrict_still_permits_narrowing():
    parent = RunEnvelope(allowed_integration_modes=INTEGRATION_MODES)
    child = parent.restrict(allowed_integration_modes=("federated",))
    assert child.allowed_integration_modes == ("federated",)
    assert AuthorityLattice.is_subset(child, parent)


def test_meet_intersects_on_the_envelope_too():
    parent = RunEnvelope(allowed_integration_modes=("native", "federated"))
    requested = RunEnvelope(allowed_integration_modes=INTEGRATION_MODES)
    assert set(AuthorityLattice.meet(requested, parent).allowed_integration_modes) == \
        {"native", "federated"}


def test_the_dimension_appears_in_the_policy_record():
    """It must be visible in the audit record, or a refusal cannot be explained later."""
    record = PolicySnapshot(profile_id="p",
                            allowed_integration_modes=("federated",)).as_dict()
    assert record["integration_modes"] == ["federated"]
    assert "license_classes" in record


# ============================= the freeze-drops-a-field defect, generically

def test_every_profile_field_reaches_the_snapshot():
    """``freeze()`` must carry every field a profile declares.

    This is the third appearance of one defect. v0.1 split policy across
    ``as_config_kwargs`` / ``as_envelope_kwargs`` and the literature profile's
    ``require_citation`` never reached the output gate. ``freeze()`` was written to fix it
    — and when the licence dimensions were added, ``freeze()`` did not carry them either,
    so a profile could declare a licence posture that no gate would ever see.

    The pattern is not "someone forgot": it is that the mapping is written by hand and
    nothing checks it is total. So this checks it. A new ``WorkProfile`` field must be
    mapped onto the snapshot or named here as deliberately not policy.
    """
    from dataclasses import fields

    from psh.profiles import WorkProfile

    #: Fields that are documentation about the profile, not policy the kernel enforces.
    NOT_POLICY = {"name", "description", "rationale", "destinations", "max_label",
                  "autonomy", "risk"}

    profile = WorkProfile(
        name="probe", description="d",
        destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PUBLIC,
        autonomy=Autonomy.OBSERVE, risk=RiskTier.R0_TRIVIAL,
        integration_modes=("federated",), license_classes=("permissive",),
        require_citation=True, require_claim_support=True,
        notes=("a note",))
    snapshot = profile.freeze()

    # The renamed-but-carried fields, checked by value rather than by name.
    assert snapshot.max_data_label is profile.max_label
    assert snapshot.allowed_destinations == profile.destinations
    assert snapshot.autonomy is profile.autonomy
    assert snapshot.risk_ceiling is profile.risk

    unmapped = []
    for field_ in fields(WorkProfile):
        if field_.name in NOT_POLICY:
            continue
        declared = getattr(profile, field_.name)
        candidates = [getattr(snapshot, name) for name in dir(snapshot)
                      if not name.startswith("_")]
        if declared not in [c for c in candidates if not callable(c)]:
            unmapped.append(field_.name)
    assert not unmapped, (
        f"these WorkProfile fields do not reach PolicySnapshot: {unmapped}. A field a "
        "profile declares and the kernel never sees is the defect freeze() exists to "
        "prevent.")


def test_the_coding_profile_does_not_permit_vendoring():
    """A worked example, not just a mechanism."""
    from psh import get_profile

    policy = get_profile("coding").freeze()
    assert set(policy.allowed_integration_modes) == {"native", "federated"}
    vendored = ComponentManifest(id="lib", name="lib", kind=ComponentKind.TOOL,
                                 license_spdx="MIT", integration_mode="vendor")
    ok, why = vendored.compatible_with(policy.envelope())
    assert not ok and "vendor" in why
