"""The scientific data contracts every skill emits and every consumer validates.

Four objects, one direction of dependency:

    SourceCard     where material came from, pinned and licensed
        │
        ▼
    EvidenceItem   one retrieved source, design-stated, quote-verified
        │
        ▼
    CandidateClaim an assertion over that evidence, with its limits declared
        │
        ▼
    ResearchArtifact  the envelope holding all of the above, gated before release

`validate_artifact` is the only entry point a publisher needs, and it is a pure
function — no clock, no network, no registry — so a published artifact can be
re-checked by a reviewer with nothing but the file.

Why dataclasses rather than a validation library: this package's only runtime
dependencies are pandas and pyarrow, its existing code is frozen dataclasses
throughout, and its tests run on three Python versions in CI. The
contracts are enforced in `__post_init__` and exposed as plain JSON Schema
documents via :data:`SCHEMAS`, so a document-level consumer gets the same
guarantees without the package taking on a dependency.
"""

from __future__ import annotations

from typing import Any, Mapping

from .artifact import (ARTIFACT_STATUSES, RESEARCH_ARTIFACT_SCHEMA, ArtifactFile,
                       ArtifactVerdict, ResearchArtifact, Violation, validate_artifact)
from .candidate_claim import (CANDIDATE_CLAIM_SCHEMA, CLAIM_DIRECTIONS, CLAIM_REASONS,
                              CLINICAL_CLAIM_KINDS, PREDICTIVE_DESIGNS, CandidateClaim,
                              ClaimVerdict, Overclaim, PredictionAsFact, Reason, check_claim,
                              require_declared)
from .evidence_item import (EVIDENCE_ITEM_SCHEMA, EVIDENCE_TIER_FOR_DESIGN,
                            IDENTIFIER_TYPES, STUDY_DESIGNS, EvidenceItem,
                            designs_for_tier, tier_for_design)
from .quality import (QUALITY_SCHEMA, Consistency, Directness, EvidenceQuality,
                      NotAssessed, Precision, RiskOfBias)
from .source_card import (ACCESS_METHODS, SOURCE_CARD_SCHEMA, SOURCE_KINDS, SourceCard,
                          canonical_hash, merge_operation_hashes)

__all__ = [
    # source_card
    "ACCESS_METHODS", "SOURCE_KINDS", "SourceCard", "canonical_hash",
    "merge_operation_hashes",
    # evidence_item
    "EVIDENCE_TIER_FOR_DESIGN", "IDENTIFIER_TYPES", "STUDY_DESIGNS", "EvidenceItem",
    "designs_for_tier", "tier_for_design",
    # quality
    "Consistency", "Directness", "EvidenceQuality", "NotAssessed", "Precision",
    "RiskOfBias",
    # candidate_claim
    "CLAIM_DIRECTIONS", "CLAIM_REASONS", "CLINICAL_CLAIM_KINDS", "PREDICTIVE_DESIGNS",
    "CandidateClaim", "ClaimVerdict", "Overclaim", "PredictionAsFact", "Reason",
    "check_claim", "require_declared",
    # artifact
    "ARTIFACT_STATUSES", "ArtifactFile", "ArtifactVerdict", "ResearchArtifact",
    "Violation", "validate_artifact",
    # schema bundle
    "SCHEMAS", "schema_bundle",
]

#: Every contract's JSON Schema, keyed by title. Consumers that validate
#: documents (the Arena data build, an external skill author, a CI check on a
#: submitted artifact) read this rather than importing the classes.
SCHEMAS: Mapping[str, dict[str, Any]] = {
    "SourceCard": SOURCE_CARD_SCHEMA,
    "EvidenceQuality": QUALITY_SCHEMA,
    "EvidenceItem": EVIDENCE_ITEM_SCHEMA,
    "CandidateClaim": CANDIDATE_CLAIM_SCHEMA,
    "ResearchArtifact": RESEARCH_ARTIFACT_SCHEMA,
}


def schema_bundle(version: str = "1.0.0") -> dict[str, Any]:
    """One self-contained JSON Schema document with every contract as a ``$defs``.

    Emitted to ``registry/contracts-<version>.schema.json`` by
    ``scripts/export_contracts.py`` so a skill author outside this repository can
    validate an artifact without installing the package.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://tcmscience.org/schemas/contracts-{version}.json",
        "title": "TCMScience scientific contracts",
        "version": version,
        "description": ("SourceCard / EvidenceItem / CandidateClaim / ResearchArtifact. "
                        "Every P0 skill emits a ResearchArtifact; the validator "
                        "refuses to publish one whose claims outrun its evidence."),
        "$defs": {name: schema for name, schema in SCHEMAS.items()},
        "oneOf": [{"$ref": f"#/$defs/{name}"} for name in SCHEMAS],
    }
