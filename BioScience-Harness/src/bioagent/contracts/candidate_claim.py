"""CandidateClaim — an assertion that must state its own limits before it may be made.

A claim is the unit that can be wrong in a way that matters. Everything upstream
of it (retrieval, extraction, modelling) produces material; a claim is where
that material is turned into a statement about the world. Two rules follow, and
both are structural rather than advisory.

**Rule one: the extrapolation boundary is declared, not inferred.**
A claim names the population and outcome it *asserts* and the population and
outcome its evidence *covers*. When the asserted population is wider than the
covered one, that is extrapolation, and it must appear in
:attr:`CandidateClaim.declared_extrapolations` with a reason. An undeclared gap
raises :class:`Overclaim` at construction. This is the difference between
"this formula reduced X in a mouse model, and we are extrapolating to humans"
and "this formula reduces X" — the first is science, the second is a misquote.

**Rule two: computational prediction cannot become clinical fact.**
The plan singles this out as a *severe* overclaim, and it is the characteristic
failure of network pharmacology: a predicted herb–target edge, propagated
through a PPI network, becomes "the mechanism by which the formula treats the
disease". :data:`PREDICTIVE_DESIGNS` names the designs that are inference rather
than measurement, and a clinical claim kind supported only by those is refused
with :class:`PredictionAsFact` — a distinct error, so it can be counted
separately on the benchmark's Claim Calibration axis instead of being averaged
in with ordinary mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple

from ..tcm.model import CLAIM_KINDS, EvidenceTier
#: The predictive designs are defined once, beside the evidence vocabulary they
#: belong to. Re-exported here because claim checking is what consumes them.
from .evidence_item import PREDICTIVE_DESIGNS

__all__ = ["CLAIM_DIRECTIONS", "CLAIM_REASONS", "CLINICAL_CLAIM_KINDS",
           "PREDICTIVE_DESIGNS", "CandidateClaim", "ClaimVerdict", "Overclaim",
           "PredictionAsFact", "Reason", "check_claim", "require_declared"]

#: Which way the claimed effect points. `no_difference` is a real finding and
#: must be representable; a system that can only state positives will report
#: them whether or not they exist.
CLAIM_DIRECTIONS = ("increase", "decrease", "no_difference", "mixed", "unclear")

#: Claim kinds that assert something about humans. These are the ones a
#: prediction may never support.
CLINICAL_CLAIM_KINDS = frozenset({"efficacy", "association", "safety_signal",
                                  "recommendation"})

_PREDICTIVE: frozenset[str] = frozenset(PREDICTIVE_DESIGNS)


class Overclaim(ValueError):
    """A claim asserts more than its evidence covers, and did not say so."""


class PredictionAsFact(Overclaim):
    """A clinical claim is supported only by computational prediction.

    A subclass of :class:`Overclaim` so existing handlers catch it, and a
    distinct type so the benchmark can count it separately: this is the failure
    mode TCMScience exists to make visible, and it should not be averaged into
    a general overclaim rate.
    """


class Reason(NamedTuple):
    """One thing wrong with a claim, with a stable code.

    Carried as a pair rather than as a bare string because the *kind* of
    problem decides how it is counted. The benchmark reports prediction-as-fact
    separately from ordinary overclaiming, and the artifact validator maps each
    code onto a distinct violation — neither is possible if every refusal
    arrives as prose and the caller pattern-matches on it.
    """

    code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


#: Claim-level reason codes. Stable; never renumbered.
CLAIM_REASONS: Mapping[str, str] = {
    "CLM001": "claim cites no evidence",
    "CLM002": "a cited evidence item is not present",
    "CLM003": "all supporting evidence is retracted",
    "CLM004": "a clinical claim rests only on computational prediction",
    "CLM005": "evidence tier is below the floor for this claim kind",
    "CLM006": "evidence quality blocks this claim",
    "CLM007": "a normative claim does not use claim_kind 'recommendation'",
    "CLM008": "cross-species extrapolation is not declared",
    "CLM009": "an extrapolation beyond the evidence is not declared",
    "CLM010": "a supporting quote was not located in its source",
}


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """Whether a claim may be made, and every reason it may not."""

    claim_id: str
    allowed: bool
    reasons: tuple[Reason, ...] = ()
    #: True when the only thing wrong is an undeclared extrapolation — the claim
    #: is fixable by declaring its limit rather than by changing its evidence.
    needs_declaration: bool = False
    #: True when the claim is a prediction asserted as clinical fact.
    prediction_as_fact: bool = False
    #: The weakest evidence tier actually supporting the claim.
    weakest_tier: str = ""
    #: Caveats inherited from the evidence. Present even when `allowed`.
    caveats: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(r.code for r in self.reasons)

    @property
    def reason_text(self) -> tuple[str, ...]:
        """The details alone, for a human-facing message."""
        return tuple(r.detail for r in self.reasons)

    def as_dict(self) -> dict[str, Any]:
        return {"claim_id": self.claim_id, "allowed": self.allowed,
                "codes": list(self.codes),
                "reasons": [{"code": r.code, "detail": r.detail} for r in self.reasons],
                "needs_declaration": self.needs_declaration,
                "prediction_as_fact": self.prediction_as_fact,
                "weakest_tier": self.weakest_tier,
                "caveats": list(self.caveats)}


@dataclass(frozen=True, slots=True)
class CandidateClaim:
    """A structured assertion with its evidence, scope and declared limits."""

    id: str
    text: str
    claim_kind: str
    #: Structured subject–predicate–object. Free text alone cannot be checked
    #: against a source, and two readers will disagree about what it asserts.
    subject: str = ""
    predicate: str = ""
    object: str = ""
    #: Ids of the `EvidenceItem`s relied on. Empty is allowed at construction so
    #: a claim can be drafted before retrieval; it is refused by `check_claim`
    #: and by the artifact validator.
    supports: tuple[str, ...] = ()
    #: The population and outcome the claim is *about*.
    asserted_population: str = ""
    asserted_outcome: str = ""
    #: The population and outcome the evidence actually *covers*.
    supported_population: str = ""
    supported_outcome: str = ""
    direction: str = "unclear"
    #: A quantified effect where one exists, e.g. "MD -1.8 (95% CI -2.6 to -1.0)".
    magnitude: str = ""
    #: Limits the author knows about and is stating. Each is a surface string
    #: plus a reason; an extrapolation without a reason is not a declaration.
    declared_extrapolations: Mapping[str, str] = field(default_factory=dict)
    #: How sure the system is, and *on what basis*. A confidence with no stated
    #: basis is a number with no meaning; `confidence_basis` is what makes it
    #: checkable, and it is required whenever `confidence` is set.
    confidence: float = 0.0
    confidence_basis: str = ""
    #: True when the claim is deliberately softened ("may", "suggests").
    hedged: bool = False
    #: True when the claim says what *should* be done. Normative claims need a
    #: recommendation-grade evidence base; nothing else can carry them.
    normative: bool = False
    #: Why this claim matters, and what would falsify it. Optional but strongly
    #: expected for a hypothesis claim; absence is reported as a caveat.
    rationale: str = ""
    falsified_by: str = ""
    produced_by: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("CandidateClaim needs an id")
        if not self.text.strip():
            raise ValueError(f"claim {self.id!r} has no text")
        if self.claim_kind not in CLAIM_KINDS:
            raise ValueError(
                f"claim_kind {self.claim_kind!r} is not one of {sorted(CLAIM_KINDS)}")
        if self.direction not in CLAIM_DIRECTIONS:
            raise ValueError(
                f"direction {self.direction!r} is not one of {CLAIM_DIRECTIONS}")
        if self.confidence and not self.confidence_basis.strip():
            raise ValueError(
                f"claim {self.id!r} states confidence {self.confidence} with no "
                "confidence_basis; a confidence without a stated basis cannot be "
                "reviewed")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        # An overclaiming claim is *constructible*. Refusing it here would make
        # the failure unrepresentable, and a system that cannot represent an
        # overclaim cannot measure one — which would gut the Claim Calibration
        # axis this project is built to report on. The refusal happens in
        # `check_claim` (CLM009) and `validate_artifact` (ART105), where it is
        # counted. A skill that would rather not emit one at all can call
        # `require_declared` and get the exception.

    @property
    def tier_floor(self) -> EvidenceTier:
        """The minimum evidence tier this claim kind requires."""
        return CLAIM_KINDS[self.claim_kind]

    @property
    def clinical(self) -> bool:
        return self.claim_kind in CLINICAL_CLAIM_KINDS

    def undeclared_extrapolations(self) -> tuple[str, ...]:
        """Scopes asserted but neither covered nor declared.

        Compares the asserted population and outcome against the supported ones
        by containment, not equality: a claim about "adults with hypertension"
        is covered by evidence about "adults with hypertension and type 2
        diabetes", but not by evidence about "healthy adults".
        """
        gaps: list[str] = []
        for asserted, covered, label in (
                (self.asserted_population, self.supported_population, "population"),
                (self.asserted_outcome, self.supported_outcome, "outcome")):
            if not asserted:
                continue
            if not covered:
                gaps.append(f"{label}:{asserted}")
                continue
            if not _covers(covered, asserted):
                gaps.append(f"{label}:{asserted}")
        return tuple(g for g in gaps if g not in self.declared_extrapolations)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "claim_kind": self.claim_kind,
                "subject": self.subject, "predicate": self.predicate,
                "object": self.object, "supports": list(self.supports),
                "asserted_population": self.asserted_population,
                "asserted_outcome": self.asserted_outcome,
                "supported_population": self.supported_population,
                "supported_outcome": self.supported_outcome,
                "direction": self.direction, "magnitude": self.magnitude,
                "declared_extrapolations": dict(self.declared_extrapolations),
                "confidence": self.confidence,
                "confidence_basis": self.confidence_basis,
                "hedged": self.hedged, "normative": self.normative,
                "rationale": self.rationale, "falsified_by": self.falsified_by,
                "produced_by": self.produced_by,
                "tier_floor": self.tier_floor.name.lower(),
                "clinical": self.clinical}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateClaim":
        return cls(
            id=str(data["id"]), text=str(data["text"]),
            claim_kind=str(data.get("claim_kind") or "mechanism"),
            subject=str(data.get("subject") or ""),
            predicate=str(data.get("predicate") or ""),
            object=str(data.get("object") or ""),
            supports=tuple(data.get("supports") or ()),
            asserted_population=str(data.get("asserted_population") or ""),
            asserted_outcome=str(data.get("asserted_outcome") or ""),
            supported_population=str(data.get("supported_population") or ""),
            supported_outcome=str(data.get("supported_outcome") or ""),
            direction=str(data.get("direction") or "unclear"),
            magnitude=str(data.get("magnitude") or ""),
            declared_extrapolations=dict(data.get("declared_extrapolations") or {}),
            confidence=float(data.get("confidence") or 0.0),
            confidence_basis=str(data.get("confidence_basis") or ""),
            hedged=bool(data.get("hedged")), normative=bool(data.get("normative")),
            rationale=str(data.get("rationale") or ""),
            falsified_by=str(data.get("falsified_by") or ""),
            produced_by=str(data.get("produced_by") or ""))


def _covers(covered: str, asserted: str) -> bool:
    """Whether `covered` is at least as broad as `asserted`.

    Deliberately conservative and purely lexical: it recognises only the two
    cases it can decide honestly — exact agreement, and one being a
    comma-or-and-separated superset of the other. Anything subtler is reported
    as a gap and must be declared. Under-reporting coverage forces a human to
    state an extrapolation; over-reporting it would silently license one.
    """
    c, a = _norm(covered), _norm(asserted)
    if not a or not c:
        return False
    if c == a:
        return True
    c_parts = {p.strip() for p in _split(c) if p.strip()}
    a_parts = {p.strip() for p in _split(a) if p.strip()}
    if a_parts and a_parts <= c_parts:
        return True
    # A narrower phrase inside a broader one, e.g. "adults" in "adults with
    # hypertension". Only accepted when the asserted text is itself a whole
    # token sequence of the covered text.
    return f" {a} " in f" {c} "


def _norm(text: str) -> str:
    return " ".join(str(text).lower().replace("；", ";").replace("，", ",").split())


def _split(text: str) -> list[str]:
    out: list[str] = []
    for chunk in text.replace(";", ",").split(","):
        out.append(chunk)
        out.extend(chunk.split(" and "))
    return out


def require_declared(claim: CandidateClaim) -> None:
    """Refuse an overclaiming claim outright, for callers that want that.

    The validator reports overclaims rather than refusing to build them, so that
    the benchmark can count them. A skill emitting a claim into the trusted
    record usually wants the stricter behaviour — it would rather fail than
    publish an undeclared extrapolation — and this is that door.
    """
    gaps = claim.undeclared_extrapolations()
    if gaps:
        raise Overclaim(
            f"claim {claim.id!r} asserts {list(gaps)} beyond its evidence and does "
            "not declare it; add each to declared_extrapolations with a reason")


def check_claim(claim: CandidateClaim, evidence: Mapping[str, Any]) -> ClaimVerdict:
    """Whether ``claim`` may be made, given an id → :class:`EvidenceItem` index.

    Checks run cheapest first and **every** failure is collected rather than
    short-circuiting, because a claim with three problems should be fixed once,
    not three times. Reasons are ordered by severity: a prediction-as-fact
    refusal first, then tier and quality refusals, then hygiene.
    """
    if not claim.supports:
        return ClaimVerdict(claim.id, False,
                            (Reason("CLM001", "claim cites no evidence"),))

    reasons: list[Reason] = []
    items = []
    for sid in claim.supports:
        item = evidence.get(sid)
        if item is None:
            reasons.append(Reason("CLM002", f"supporting evidence {sid!r} is not present"))
        else:
            items.append(item)
    if not items:
        return ClaimVerdict(claim.id, False, tuple(reasons) or (
            Reason("CLM002", "no resolvable supporting evidence"),))

    usable = [i for i in items if i.usable]
    if not usable:
        return ClaimVerdict(claim.id, False, tuple(reasons) + (
            Reason("CLM003", "all supporting evidence is retracted"),))

    # A claim is only as strong as the evidence it *rests* on. A retracted item
    # that is cited alongside sound ones does not support the claim, but it does
    # not sink it either — it is reported as a caveat below.
    tiers = [i.tier for i in usable]
    weakest = min(usable, key=lambda i: i.tier)
    designs = {i.design for i in usable}

    prediction_as_fact = False
    if claim.clinical and designs <= _PREDICTIVE:
        prediction_as_fact = True
        reasons.append(Reason("CLM004", (
            f"{claim.claim_kind} claim about humans is supported only by "
            f"computational prediction ({', '.join(sorted(designs))}); predicted "
            "relationships are not clinical findings")))

    if min(tiers) < claim.tier_floor:
        reasons.append(Reason("CLM005", (
            f"{claim.claim_kind} requires at least {claim.tier_floor.name.lower()} "
            f"evidence; the strongest available is {max(tiers).name.lower()}")))

    # Quality can refuse a claim on its own only in the two structural cases
    # documented on EvidenceQuality.blocks — extrapolation, and high bias for a
    # clinical claim.
    for item in usable:
        if item.quality is not None:
            why = item.quality.blocks(claim.claim_kind)
            if why:
                reasons.append(Reason("CLM006", f"evidence {item.id!r}: {why}"))

    if claim.normative and claim.claim_kind != "recommendation":
        reasons.append(Reason("CLM007", (
            "a normative claim must use claim_kind 'recommendation', not "
            f"{claim.claim_kind!r}")))

    if claim.clinical and not claim.declared_extrapolations:
        for item in usable:
            if item.design in ("animal", "in_vitro"):
                reasons.append(Reason("CLM008", (
                    f"evidence {item.id!r} is {item.design}; a {claim.claim_kind} "
                    "claim in humans extrapolates across species and must say so")))

    for gap in claim.undeclared_extrapolations():
        reasons.append(Reason("CLM009", f"undeclared extrapolation {gap!r}"))

    # Only cited items matter here. An artifact may legitimately hold background
    # evidence that no claim rests on, and refusing the claim because that
    # background item was not offset-verified would push authors to delete
    # context rather than to verify what matters.
    unverified = [i.id for i in usable if not i.quote_verified]
    if unverified:
        reasons.append(Reason("CLM010", (
            f"supporting evidence {unverified} has no quote located in its source; "
            "an unverified excerpt cannot support a claim")))

    caveats: list[str] = []
    for item in items:
        caveats.extend(f"{item.id}: {c}" for c in item.caveats())
    for item in items:
        if not item.usable and item.tier >= claim.tier_floor:
            caveats.append(f"{item.id}: retracted, and carries no weight for this claim")
    if not claim.falsified_by:
        caveats.append("no falsification condition stated")

    declarable = {"CLM008", "CLM009"}
    codes = {r.code for r in reasons}
    needs_declaration = bool(codes) and codes <= declarable
    return ClaimVerdict(claim.id, not reasons, tuple(reasons),
                        needs_declaration=needs_declaration,
                        prediction_as_fact=prediction_as_fact,
                        weakest_tier=weakest.tier.name.lower(),
                        caveats=tuple(caveats))


CANDIDATE_CLAIM_SCHEMA: dict[str, Any] = {
    "title": "CandidateClaim",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "text", "claim_kind"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1},
        "claim_kind": {"type": "string", "enum": sorted(CLAIM_KINDS)},
        "subject": {"type": "string"}, "predicate": {"type": "string"},
        "object": {"type": "string"},
        "supports": {"type": "array", "items": {"type": "string"}},
        "asserted_population": {"type": "string"},
        "asserted_outcome": {"type": "string"},
        "supported_population": {"type": "string"},
        "supported_outcome": {"type": "string"},
        "direction": {"type": "string", "enum": list(CLAIM_DIRECTIONS)},
        "magnitude": {"type": "string"},
        "declared_extrapolations": {"type": "object",
                                    "additionalProperties": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence_basis": {"type": "string"},
        "hedged": {"type": "boolean"}, "normative": {"type": "boolean"},
        "rationale": {"type": "string"}, "falsified_by": {"type": "string"},
        "produced_by": {"type": "string"},
    },
    "description": "An assertion that must declare its extrapolation boundary.",
}
