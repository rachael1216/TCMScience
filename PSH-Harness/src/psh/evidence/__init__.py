"""Evidence: claims, sources, and whether a source supports a claim."""

from .claims import (
    Downgrade, DowngradeReason, EffectDirection, LicensedScope, PopulationSpec,
    ScientificClaim, extract_outcome, extract_population,
)
from .scope import ScopeVerdict, check_scope
from .record import (
    EvidenceRecord, RetractionStatus, SourceType, VerifiedSpan, locate_span,
)
from .support import (
    Certainty, Claim, ClaimSupport, ClaimSupportVerifier, Directness, Evidence,
    LexicalSupportVerifier, ModelSupportVerifier, Relationship,
)

__all__ = [
    "Claim", "Evidence", "ClaimSupport", "Certainty", "Relationship", "Directness",
    "ClaimSupportVerifier", "LexicalSupportVerifier", "ModelSupportVerifier",
    "EvidenceRecord", "VerifiedSpan", "SourceType", "RetractionStatus", "locate_span",
    "ScientificClaim", "PopulationSpec", "LicensedScope", "EffectDirection",
    "Downgrade", "DowngradeReason", "extract_population", "extract_outcome",
    "ScopeVerdict", "check_scope",
]
