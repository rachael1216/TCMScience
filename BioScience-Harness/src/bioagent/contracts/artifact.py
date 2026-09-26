"""ResearchArtifact — the envelope every P0 skill must emit, and the gate on it.

Every skill in the stable registry returns a `ResearchArtifact`. One envelope,
one validator, one place where "may this be published?" is decided. A skill
that returns prose instead cannot be validated, cannot be re-run against, and
cannot be scored, which is why the envelope is required rather than encouraged.

The validator is the enforcement half of the whole contract layer. It is
deliberately a **pure function over a finished artifact**: it opens no sockets,
reads no clock, and consults no registry, so the same artifact always produces
the same verdict and a published result can be re-checked by a reviewer years
later.

Violations carry stable codes (`ART101`…) in the same style as PSH's scientific
compiler (`EVIDENCE101`, `FLOW101`), because these strings end up in benchmark
output and in paper tables and must not be renumbered by an edit.

A note on what is *not* here. The validator does not judge whether a claim is
true, well-designed or clinically useful. It judges whether the artifact has
earned the right to be read as a scientific statement: sources pinned, claims
scoped, quotes located, limits stated. Everything else is for humans and for
the benchmark's expert scorers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple

from .candidate_claim import CandidateClaim, check_claim
from .evidence_item import EvidenceItem
from .source_card import SourceCard, canonical_hash, merge_operation_hashes

__all__ = ["ARTIFACT_STATUSES", "ArtifactFile", "ArtifactVerdict", "ResearchArtifact",
           "Violation", "validate_artifact"]

ARTIFACT_STATUSES = ("draft", "validated", "refused", "experimental")

#: Violation codes. Stable strings; never renumbered.
_CODES: Mapping[str, str] = {
    "ART101": "artifact declares no sources",
    "ART102": "a publishable claim rests on a source that is not pinned to a snapshot",
    "ART103": "a publishable claim rests on a source with no identified licence",
    "ART104": "a claim cites evidence id that is not present in the artifact",
    "ART105": "a claim is not supported by its evidence",
    "ART106": "a clinical claim is supported only by computational prediction",
    "ART107": "a declared output file has no content hash",
    "ART108": "composite_version is incomplete",
    "ART109": "a claim cites retracted evidence",
    "ART110": "no limitations are stated",
    "ART111": "an evidence item names a source card that is not in the artifact",
    "ART112": "a claim declares a confidence basis but evidence quality is unassessed",
}

#: Claim-level reason code → artifact violation code. The claim layer numbers
#: its own refusals (`CLM0xx`) so the benchmark can count them; the artifact
#: layer maps them onto its own (`ART1xx`) so a published report has one stable
#: vocabulary. Collapsing them to a single code would lose the distinction
#: between "the evidence is too weak" and "this is a prediction asserted as
#: clinical fact", which is the one the project most needs to report.
_CLAIM_TO_ARTIFACT: Mapping[str, str] = {
    "CLM001": "ART101",
    "CLM002": "ART104",
    "CLM003": "ART109",
    "CLM004": "ART106",
    "CLM005": "ART105",
    "CLM006": "ART105",
    "CLM007": "ART105",
    "CLM008": "ART105",
    "CLM009": "ART105",
    "CLM010": "ART105",
}


class Violation(NamedTuple):
    """One reason an artifact may not be published."""

    code: str
    detail: str
    severity: str = "error"          # error | warning

    def __str__(self) -> str:
        return f"{self.code} ({self.severity}): {self.detail}"


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """A file the run produced, addressed by content."""

    path: str
    sha256: str = ""
    media_type: str = ""
    bytes: int = 0
    description: str = ""

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("ArtifactFile needs a path")
        if self.sha256 and len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64-character hex digest")

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256,
                "media_type": self.media_type, "bytes": self.bytes,
                "description": self.description}


@dataclass(frozen=True, slots=True)
class ResearchArtifact:
    """The complete, self-describing output of one scientific run."""

    id: str
    run_id: str
    skill_id: str
    skill_version: str = ""
    #: The four independently versioned axes (ADR-0002). Every citable result
    #: names all four, so a reader can reconstruct the exact configuration.
    composite_version: Mapping[str, str] = field(default_factory=dict)
    question: str = ""
    created_at: str = ""
    sources: tuple[SourceCard, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    claims: tuple[CandidateClaim, ...] = ()
    outputs: tuple[ArtifactFile, ...] = ()
    #: What the run could not do, and what it assumed. Required to be non-empty:
    #: an artifact with no stated limitations is claiming to have none.
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    #: The authority the run executed under — policy id and the audit chain head,
    #: so the artifact ties back to the kernel's tamper-evident log.
    policy_id: str = ""
    audit_head: str = ""
    #: Free-form provenance from the runtime, and a digest over it.
    provenance: Mapping[str, Any] = field(default_factory=dict)
    status: str = "draft"
    produced_by: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ResearchArtifact needs an id")
        if self.status not in ARTIFACT_STATUSES:
            raise ValueError(f"status {self.status!r} is not one of {ARTIFACT_STATUSES}")
        known = {c.id for c in self.sources}
        for item in self.evidence:
            if item.source_card_id and item.source_card_id not in known:
                raise ValueError(
                    f"evidence {item.id!r} names source card "
                    f"{item.source_card_id!r}, which is not in the artifact")

    # -- indexes -----------------------------------------------------------

    @property
    def evidence_index(self) -> Mapping[str, EvidenceItem]:
        return {e.id: e for e in self.evidence}

    @property
    def source_index(self) -> Mapping[str, SourceCard]:
        return {s.id: s for s in self.sources}

    @property
    def composite_version_string(self) -> str:
        """The single string a paper or leaderboard row cites."""
        keys = ("runtime", "skill", "source", "benchmark")
        parts = [f"{k}={self.composite_version.get(k) or 'unset'}" for k in keys]
        return "|".join(parts)

    @property
    def sources_hash(self) -> str:
        """One digest over the whole source set, so drift is detectable."""
        return merge_operation_hashes(self.sources)

    @property
    def digest(self) -> str:
        """Content digest of the artifact.

        Computed over :meth:`as_dict`, which deliberately does **not** contain
        the digest or the status. `status` is a workflow field, not content — if
        it entered the digest, a refuted-then-fixed artifact would carry a
        different identity for the same science. The digest excludes itself
        because including it is circular: the first version of this property
        recursed until the stack blew, which is why `as_dict` and `digest` are
        now separate layers rather than one calling into the other.
        """
        payload = dict(self.as_dict())
        payload.pop("status", None)
        return canonical_hash(payload)

    def claims_of_kind(self, kind: str) -> tuple[CandidateClaim, ...]:
        return tuple(c for c in self.claims if c.claim_kind == kind)

    def as_dict(self) -> dict[str, Any]:
        """The artifact's fields, with no derived digest. Round-trips exactly."""
        return {"id": self.id, "run_id": self.run_id, "skill_id": self.skill_id,
                "skill_version": self.skill_version,
                "composite_version": dict(self.composite_version),
                "composite_version_string": self.composite_version_string,
                "question": self.question, "created_at": self.created_at,
                "sources": [s.as_dict() for s in self.sources],
                "sources_hash": self.sources_hash,
                "evidence": [e.as_dict() for e in self.evidence],
                "claims": [c.as_dict() for c in self.claims],
                "outputs": [o.as_dict() for o in self.outputs],
                "limitations": list(self.limitations),
                "assumptions": list(self.assumptions),
                "policy_id": self.policy_id, "audit_head": self.audit_head,
                "provenance": dict(self.provenance),
                "status": self.status, "produced_by": self.produced_by,
                "notes": self.notes}

    def document(self) -> dict[str, Any]:
        """The artifact as a publishable document: fields plus their digest.

        Separate from :meth:`as_dict` so that the digest can appear in what is
        written to disk without appearing in what it is computed over.
        """
        return {**self.as_dict(), "digest": self.digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchArtifact":
        return cls(
            id=str(data["id"]), run_id=str(data.get("run_id") or ""),
            skill_id=str(data.get("skill_id") or ""),
            skill_version=str(data.get("skill_version") or ""),
            composite_version=dict(data.get("composite_version") or {}),
            question=str(data.get("question") or ""),
            created_at=str(data.get("created_at") or ""),
            sources=tuple(SourceCard.from_dict(s) for s in data.get("sources") or ()),
            evidence=tuple(EvidenceItem.from_dict(e) for e in data.get("evidence") or ()),
            claims=tuple(CandidateClaim.from_dict(c) for c in data.get("claims") or ()),
            outputs=tuple(ArtifactFile(**o) for o in data.get("outputs") or ()),
            limitations=tuple(data.get("limitations") or ()),
            assumptions=tuple(data.get("assumptions") or ()),
            policy_id=str(data.get("policy_id") or ""),
            audit_head=str(data.get("audit_head") or ""),
            provenance=dict(data.get("provenance") or {}),
            status=str(data.get("status") or "draft"),
            produced_by=str(data.get("produced_by") or ""),
            notes=str(data.get("notes") or ""))


@dataclass(frozen=True, slots=True)
class ArtifactVerdict:
    """May this artifact be published, and if not, exactly why."""

    artifact_id: str
    publishable: bool
    violations: tuple[Violation, ...] = ()
    warnings: tuple[Violation, ...] = ()
    claim_verdicts: tuple[Any, ...] = ()
    #: True when the only blockers are *declarable* — an undeclared
    #: extrapolation — so the fix is to state a limit, not to gather evidence.
    fixable_by_declaration: bool = False

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(v.code for v in self.violations)

    def as_dict(self) -> dict[str, Any]:
        return {"artifact_id": self.artifact_id, "publishable": self.publishable,
                "codes": list(self.codes),
                "violations": [{"code": v.code, "detail": v.detail,
                                "severity": v.severity} for v in self.violations],
                "warnings": [{"code": w.code, "detail": w.detail,
                              "severity": w.severity} for w in self.warnings],
                "claim_verdicts": [v.as_dict() for v in self.claim_verdicts],
                "fixable_by_declaration": self.fixable_by_declaration}

    def explain(self) -> str:
        if self.publishable:
            return f"{self.artifact_id}: publishable" + (
                f" with {len(self.warnings)} warning(s)" if self.warnings else "")
        lines = [f"{self.artifact_id}: NOT publishable"]
        lines += [f"  {v}" for v in self.violations]
        return "\n".join(lines)


def validate_artifact(artifact: ResearchArtifact) -> ArtifactVerdict:
    """Decide whether an artifact may be published. Pure and total.

    Every check runs; nothing short-circuits. An artifact with five problems
    should be fixed once, and a report that revealed only the first would send
    the author round the loop five times.
    """
    errors: list[Violation] = []
    warnings: list[Violation] = []

    # -- the artifact must name its configuration ---------------------------
    missing = [k for k in ("runtime", "skill", "source", "benchmark")
               if not artifact.composite_version.get(k)]
    if missing:
        errors.append(Violation("ART108",
                                f"composite_version is missing {missing}; a result "
                                "that does not name all four axes cannot be reproduced"))

    # -- sources ------------------------------------------------------------
    if not artifact.sources:
        errors.append(Violation("ART101", "artifact declares no sources"))

    # -- claims -------------------------------------------------------------
    index = artifact.evidence_index
    claim_verdicts = []
    for claim in artifact.claims:
        verdict = check_claim(claim, index)
        claim_verdicts.append(verdict)
        if verdict.allowed:
            continue
        for reason in verdict.reasons:
            code = _CLAIM_TO_ARTIFACT.get(reason.code, "ART105")
            errors.append(Violation(code, f"claim {claim.id!r}: {reason.detail}"))

    # Which sources actually carry a publishable claim. Only these are held to
    # the pinning and licence standards — an unpinned source used for
    # background reading is not a defect, and treating it as one would push
    # authors to drop context rather than to pin what matters.
    cited = {sid for c in artifact.claims if c.supports for sid in c.supports}
    cited_cards: set[str] = set()
    for item in artifact.evidence:
        if item.id in cited and item.source_card_id:
            cited_cards.add(item.source_card_id)
    # A claim may also cite evidence with no card (local tool output). Then the
    # claim is held to the pinning standard directly and cannot satisfy it, so
    # say so once rather than silently passing.
    for item in artifact.evidence:
        if item.id in cited and not item.source_card_id:
            warnings.append(Violation(
                "ART111", f"evidence {item.id!r} carries a claim but names no source "
                "card; the claim's provenance cannot be traced to a snapshot",
                "warning"))

    for card_id in sorted(cited_cards):
        card = artifact.source_index.get(card_id)
        if card is None:
            continue
        if not card.pinned:
            errors.append(Violation(
                "ART102", f"source {card_id!r} supports a claim but is not pinned to a "
                "snapshot; its contents could have changed since the run"))
        if not card.licensed:
            errors.append(Violation(
                "ART103", f"source {card_id!r} supports a claim but has no identified "
                "licence; reuse cannot be justified"))

    # -- outputs ------------------------------------------------------------
    for out in artifact.outputs:
        if not out.sha256:
            errors.append(Violation(
                "ART107", f"output {out.path!r} declares no content hash; a file that "
                "cannot be verified is not a reproducible artifact"))

    # -- limitations --------------------------------------------------------
    if not artifact.limitations:
        errors.append(Violation(
            "ART110", "no limitations are stated; an artifact that names none is "
            "claiming to have none"))

    # -- warnings -----------------------------------------------------------
    if not artifact.claims:
        warnings.append(Violation("ART105", "artifact makes no claims; it reports "
                                  "material but asserts nothing", "warning"))
    if artifact.evidence and not any(e.quote_verified for e in artifact.evidence):
        warnings.append(Violation(
            "ART105", "no evidence item has a quote located in its source",
            "warning"))
    for claim in artifact.claims:
        if claim.confidence and not any(
                index.get(s) is not None and index[s].quality is not None
                and index[s].quality.assessed for s in claim.supports):
            warnings.append(Violation(
                "ART112", f"claim {claim.id!r} states confidence "
                f"{claim.confidence} but no supporting evidence has an assessed "
                "quality dimension", "warning"))

    # Fixable by *declaring* a limit rather than by gathering evidence — the
    # distinction that lets a run report "add a limitation and re-submit"
    # instead of "your science is insufficient".
    fixable = bool(errors) and all(v.code == "ART105" for v in errors) and any(
        cv.needs_declaration for cv in claim_verdicts if not cv.allowed)
    return ArtifactVerdict(artifact.id, not errors, tuple(errors), tuple(warnings),
                           tuple(claim_verdicts), fixable_by_declaration=fixable)


RESEARCH_ARTIFACT_SCHEMA: dict[str, Any] = {
    "title": "ResearchArtifact",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "run_id", "skill_id", "composite_version", "sources",
                 "evidence", "claims", "limitations"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "run_id": {"type": "string"}, "skill_id": {"type": "string"},
        "skill_version": {"type": "string"},
        "composite_version": {
            "type": "object",
            "required": ["runtime", "skill", "source", "benchmark"],
            "additionalProperties": {"type": "string"}},
        "question": {"type": "string"}, "created_at": {"type": "string"},
        "sources": {"type": "array", "items": {"$ref": "#/$defs/SourceCard"}},
        "evidence": {"type": "array", "items": {"$ref": "#/$defs/EvidenceItem"}},
        "claims": {"type": "array", "items": {"$ref": "#/$defs/CandidateClaim"}},
        "outputs": {"type": "array", "items": {
            "type": "object", "required": ["path", "sha256"],
            "properties": {"path": {"type": "string"}, "sha256": {"type": "string"},
                           "media_type": {"type": "string"},
                           "bytes": {"type": "integer"},
                           "description": {"type": "string"}}}},
        "limitations": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "policy_id": {"type": "string"}, "audit_head": {"type": "string"},
        "provenance": {"type": "object"},
        "status": {"type": "string", "enum": list(ARTIFACT_STATUSES)},
        "produced_by": {"type": "string"}, "notes": {"type": "string"},
    },
    "description": "The envelope every P0 skill must emit. Validated before release.",
}
