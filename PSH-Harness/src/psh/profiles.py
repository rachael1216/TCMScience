"""Work-mode profiles: declarative policy bundles, so a user picks a mode not a dozen knobs.

The review's argument is practical rather than architectural. A physician-scientist switching
between reading literature, analysing identifiable cohort data, drafting a manuscript and
reviewing someone else's paper needs a different policy posture for each — and if setting
that posture means configuring ten individual controls, they will not do it, and the strict
settings will never be used.

So each profile states the whole posture at once: which destinations are reachable, the data
ceiling, autonomy, risk tier, whether claim support is enforced, and the budget. Profiles are
authored state in the review's storage model — they belong in the Git workspace and are
readable by agents but writable only by a human.

Two profiles deserve their reasoning stated, because they are the restrictive ones:

* ``clinical_research`` forbids public remote destinations entirely. Not "PHI may not go
  there" — nothing may, because the classifier is a safety net and not a certification, so a
  run touching identifiable data should not depend on the detector being perfect.
* ``peer_review`` forbids *all* network egress. A manuscript under review is confidential to
  the reviewer; sending any part of it to a third-party provider breaches that, whatever the
  provider's retention policy says.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .contracts import Autonomy, Budget, PolicyDenied, RiskTier
from .labels import Destination, Sensitivity
from .licensing import INTEGRATION_MODES, LICENSE_CLASSES

__all__ = ["WorkProfile", "PROFILES", "get_profile", "profile_names"]

if TYPE_CHECKING:  # pragma: no cover
    from .policy import PolicySnapshot


@dataclass(frozen=True, slots=True)
class WorkProfile:
    """One named policy posture."""

    name: str
    description: str
    destinations: tuple[Destination, ...]
    max_label: Sensitivity
    autonomy: Autonomy
    risk: RiskTier
    require_claim_support: bool = True
    require_citation: bool = False
    #: Licence provenance, adopted from BioScience-Harness. Defaults to everything, because
    #: the licence table already refuses the combination that matters; a profile narrows
    #: when the *work* imposes a constraint the licence alone does not.
    integration_modes: tuple[str, ...] = INTEGRATION_MODES
    license_classes: tuple[str, ...] = LICENSE_CLASSES
    budget: Budget = field(default_factory=Budget)
    rationale: str = ""
    notes: tuple[str, ...] = ()

    def freeze(self) -> "PolicySnapshot":
        """Return the immutable policy this profile declares.

        The single supported way to apply a profile. It replaces the split
        ``as_config_kwargs`` / ``as_envelope_kwargs`` pair, which allowed a field to be
        declared on the profile and silently dropped before it reached the component that
        enforces it — ``require_citation`` was declared by the literature profile and never
        arrived at the output gate.
        """
        from .policy import PolicySnapshot

        return PolicySnapshot(
            profile_id=self.name, max_data_label=self.max_label,
            allowed_destinations=tuple(self.destinations),
            require_citation=self.require_citation,
            require_claim_support=self.require_claim_support,
            autonomy=self.autonomy, risk_ceiling=self.risk, budget=self.budget,
            allowed_integration_modes=tuple(self.integration_modes),
            allowed_license_classes=tuple(self.license_classes),
            notes=self.notes)

    def as_config_kwargs(self) -> dict[str, Any]:
        """Deprecated: use ``freeze()``. Retained so v0.1 callers fail loudly, not silently."""
        raise PolicyDenied(
            "as_config_kwargs() is removed in v0.2 because splitting policy across config "
            "and envelope keyword paths let declared fields go unenforced. Use "
            "profile.freeze() and pass policy= to TrustedKernel and Runner.run().")

    def as_envelope_kwargs(self) -> dict[str, Any]:
        """Deprecated: use ``freeze()``."""
        raise PolicyDenied(
            "as_envelope_kwargs() is removed in v0.2. Use profile.freeze() and pass "
            "policy= to Runner.run().")

    @property
    def permits_network(self) -> bool:
        return any(d in (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)
                   for d in self.destinations)


_LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
          Destination.PERSISTENT)
_LOCAL_PLUS_PUBLIC = _LOCAL + (Destination.PUBLIC_REMOTE,)
_LOCAL_PLUS_TRUSTED = _LOCAL + (Destination.TRUSTED_REMOTE,)


PROFILES: Mapping[str, WorkProfile] = {
    "literature": WorkProfile(
        name="literature",
        description="Literature search and synthesis against public sources.",
        destinations=_LOCAL_PLUS_PUBLIC,
        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        autonomy=Autonomy.ACT, risk=RiskTier.R1_ROUTINE,
        require_claim_support=True, require_citation=True,
        budget=Budget(tokens_soft=120_000, tokens_hard=350_000, usd_soft=1.5, usd_hard=6.0),
        rationale=("Public retrieval is the point, so public destinations are open — but the "
                   "data ceiling stops identifiable content from riding along, and citation "
                   "is required because an uncited synthesis is not usable in this mode.")),

    "clinical_research": WorkProfile(
        name="clinical_research",
        description="Work touching identifiable or re-identifiable patient data.",
        destinations=_LOCAL,
        max_label=Sensitivity.PHI,
        autonomy=Autonomy.ACT_WITH_APPROVAL, risk=RiskTier.R3_CLINICAL,
        require_claim_support=True, require_citation=False,
        budget=Budget(tokens_soft=80_000, tokens_hard=200_000, usd_soft=0.0, usd_hard=0.5,
                      max_model_calls=40),
        rationale=("No public destination at all, rather than relying on the classifier to "
                   "catch every identifier. The detector is a safety net, not a "
                   "certification, so a run that touches identifiable data should not "
                   "depend on it being perfect. Cost ceilings are near zero because the "
                   "only permitted models are local."),
        notes=("Approval is required for state-changing tools.",
               "A local model is required; configure one before using this profile.")),

    "data_science": WorkProfile(
        name="data_science",
        description="Omics and statistical analysis with reproducibility emphasis.",
        destinations=_LOCAL + (Destination.PUBLIC_REMOTE,),
        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        autonomy=Autonomy.ACT, risk=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=False,
        budget=Budget(tokens_soft=200_000, tokens_hard=600_000, usd_soft=3.0, usd_hard=12.0,
                      seconds_hard=4 * 3600, max_tool_calls=600),
        rationale=("Long tool-heavy runs need a wider time and call budget. Claim support is "
                   "off because analysis output is numbers and figures rather than cited "
                   "prose — it is re-enabled when those results are written up."),
        notes=("Run mutating tools inside a container; psh does not provide isolation.",)),

    "writing": WorkProfile(
        name="writing",
        description="Manuscript drafting with evidence-aware output checking.",
        destinations=_LOCAL_PLUS_PUBLIC,
        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        autonomy=Autonomy.ACT_WITH_APPROVAL, risk=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=True, require_citation=True,
        budget=Budget(tokens_soft=150_000, tokens_hard=400_000, usd_soft=2.0, usd_hard=8.0),
        rationale=("The strictest claim checking of any profile, because a manuscript is "
                   "where an unsupported citation does lasting damage. Approval is required "
                   "before anything writes to the manuscript files.")),

    "peer_review": WorkProfile(
        name="peer_review",
        description="Reviewing a confidential manuscript.",
        destinations=(Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL,
                      Destination.USER_OUTPUT),
        max_label=Sensitivity.SENSITIVE,
        autonomy=Autonomy.SUGGEST, risk=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=True,
        budget=Budget(tokens_soft=60_000, tokens_hard=150_000, usd_soft=0.0, usd_hard=0.0,
                      max_tool_calls=30),
        rationale=("No network egress of any kind and no persistence. A manuscript under "
                   "review is confidential to the reviewer, so sending any part of it to a "
                   "third-party provider breaches that regardless of retention policy. "
                   "Autonomy is SUGGEST: the reviewer writes the review, not the harness."),
        notes=("PERSISTENT is deliberately absent: review material should not be retained.",)),

    "coding": WorkProfile(
        name="coding",
        description="Software work with sandboxing and diff review.",
        destinations=_LOCAL_PLUS_PUBLIC,
        max_label=Sensitivity.INTERNAL,
        autonomy=Autonomy.ACT_WITH_APPROVAL, risk=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=False,
        budget=Budget(tokens_soft=200_000, tokens_hard=500_000, usd_soft=3.0, usd_hard=10.0,
                      max_tool_calls=400),
        integration_modes=("native", "federated"),
        rationale=("Claim support is irrelevant to code. The data ceiling is INTERNAL so a "
                   "chart note pasted into a debugging session cannot reach a provider. "
                   "Vendoring is off: this is the mode that produces code the group then "
                   "distributes, and copying an upstream implementation into that tree is "
                   "a licensing decision rather than an engineering one. Call it or "
                   "reimplement it; if it must be vendored, that is a deliberate act under "
                   "a profile that says so."),
        notes=("psh has no sandbox; use a container for anything that executes code.",)),

    "learning": WorkProfile(
        name="learning",
        description="Study and spaced-repetition review.",
        destinations=_LOCAL_PLUS_PUBLIC,
        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        autonomy=Autonomy.ACT, risk=RiskTier.R0_TRIVIAL,
        require_claim_support=True,
        budget=Budget(tokens_soft=40_000, tokens_hard=120_000, usd_soft=0.5, usd_hard=2.0,
                      seconds_hard=45 * 60),
        rationale=("A deliberately small time budget. This mode is meant to protect the "
                   "user's attention, so a study session that runs long has failed at its "
                   "purpose even if it is producing output.")),

    "administrative": WorkProfile(
        name="administrative",
        description="Calendar, correspondence and connector work.",
        destinations=_LOCAL_PLUS_TRUSTED,
        max_label=Sensitivity.INTERNAL,
        autonomy=Autonomy.ACT_WITH_APPROVAL, risk=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=False,
        budget=Budget(tokens_soft=40_000, tokens_hard=100_000, usd_soft=0.5, usd_hard=2.0),
        rationale=("Every outbound action needs approval, because an email or calendar write "
                   "is visible to other people and cannot be undone by the harness.")),
}


def profile_names() -> list[str]:
    return sorted(PROFILES)


def get_profile(name: str) -> WorkProfile:
    """Return a profile by name, with a helpful error listing the alternatives."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(
            f"unknown work profile {name!r}; available: {', '.join(profile_names())}"
        ) from None
