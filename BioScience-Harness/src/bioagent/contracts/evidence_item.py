"""EvidenceItem — one piece of retrieved evidence, with its design and its quote.

This replaces the current model, in which `StudyEvidence` stores an
`EvidenceTier` and carries design, population, comparator and outcome as free
text. The problem with an ordinal tier as the *stored* fact is that it is lossy
in a direction nobody notices:

* `EvidenceTier.PRECLINICAL` covers both an in-vitro assay and an animal study.
  Those differ enormously in what they license — an in-vitro result is about a
  cell line, an animal result is about a whole organism — but as one tier they
  are indistinguishable, and a downstream claim check cannot tell them apart.
* PSH's own `workflow.ir.DESIGNS` already separates `in_vitro` from `animal`.
  Two vocabularies for one fact is how they drift.

So **design is primary and the tier is derived**:

    STUDY_DESIGNS (finer)  ──derive──▶  EvidenceTier (coarser)

`EvidenceItem` stores the design; :func:`tier_for_design` recovers the ranking
the claim-kind table needs. Nothing can be represented at tier level that was
not first represented at design level, so the lossy step happens on read rather
than on write and can be revisited.

The quote is part of the record, not a rendering detail. :meth:`EvidenceItem
.quote_verified` is set only when the excerpt was located inside the retrieved
content by offset, so a claim can point at exact bytes rather than at "the
paper". `artifacts.validator` refuses a publishable claim whose supporting item
has no verified quote — an unverified quote is a paraphrase, and paraphrases
are where citations go wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..tcm.model import CLAIM_KINDS, EvidenceTier

__all__ = ["EMPIRICAL_DESIGNS", "EVIDENCE_TIER_FOR_DESIGN", "IDENTIFIER_TYPES",
           "LOOKUPABLE_IDENTIFIERS", "PREDICTIVE_DESIGNS", "STUDY_DESIGNS", "EvidenceItem",
           "tier_for_design"]

#: Empirical study designs, ordered strongest to weakest. Deliberately the
#: vocabulary PSH's `workflow.ir.DESIGNS` already uses, so the two packages name
#: the same fact the same way; `tests/test_contracts.py` asserts the sets agree.
EMPIRICAL_DESIGNS: tuple[str, ...] = (
    "systematic_review", "randomized_trial", "observational", "case_report",
    "animal", "in_vitro", "expert_consensus", "classical_text",
)

#: Computational designs. These are *not* observations of the world — they are
#: the output of a model, and placing them on the same ladder as an assay would
#: be a category error. They are kept in a separate tuple so the distinction is
#: visible in the vocabulary itself, and so :attr:`EvidenceItem.is_prediction`
#: can be a lookup rather than a heuristic.
#:
#: They are still assigned a tier, because claims must be rankable: everything
#: here sits at ``PRECLINICAL``, which is what lets a network-pharmacology skill
#: make a *mechanism* claim. That tier is a floor for ranking, not an
#: endorsement — :func:`check_claim` still refuses a clinical claim kind
#: supported only by these designs, and mechanism claims resting on them are
#: reported with a caveat.
PREDICTIVE_DESIGNS: tuple[str, ...] = (
    "in_silico", "network_prediction", "docking", "molecular_dynamics",
    "target_prediction", "pathway_enrichment",
)

#: Everything a design field may hold.
STUDY_DESIGNS: tuple[str, ...] = EMPIRICAL_DESIGNS + PREDICTIVE_DESIGNS

#: Design → the coarsest tier that can carry it. Note `animal` and `in_vitro`
#: both land on PRECLINICAL: the tier vocabulary is coarser than the design
#: vocabulary, and that is exactly the direction of information loss this
#: module makes explicit rather than silent. Every predictive design also lands
#: on PRECLINICAL, for the ranking reason stated above.
EVIDENCE_TIER_FOR_DESIGN: Mapping[str, EvidenceTier] = {
    "systematic_review": EvidenceTier.SYSTEMATIC_REVIEW,
    "randomized_trial": EvidenceTier.RANDOMIZED_TRIAL,
    "observational": EvidenceTier.OBSERVATIONAL,
    "case_report": EvidenceTier.CASE_REPORT,
    "animal": EvidenceTier.PRECLINICAL,
    "in_vitro": EvidenceTier.PRECLINICAL,
    "expert_consensus": EvidenceTier.EXPERT_EXPERIENCE,
    "classical_text": EvidenceTier.CLASSICAL_TEXT,
    **{d: EvidenceTier.PRECLINICAL for d in PREDICTIVE_DESIGNS},
}

#: Which identifier a citation uses. Mirrors PSH's `evidence.record.SourceType`
#: for the overlap and adds the TCM-specific registries.
IDENTIFIER_TYPES: tuple[str, ...] = (
    "pmid", "pmcid", "doi", "nct", "chictr", "isrctn", "dataset", "local_artifact",
    "classical_passage", "pharmacopoeia", "registry_record", "user_supplied",
)

#: Identifiers a *clinical* claim must carry. A claim about humans that cannot be
#: looked up is not evidence, it is an assertion.
LOOKUPABLE_IDENTIFIERS = frozenset({"pmid", "pmcid", "doi", "nct", "chictr", "isrctn"})


def tier_for_design(design: str) -> EvidenceTier:
    """The tier a design ranks as. Raises rather than defaulting.

    A default here would be the precise failure mode this module exists to
    avoid: an unrecognised design silently becoming, say, `EXPERT_EXPERIENCE`
    and then being treated as citable for a traditional-use claim.
    """
    try:
        return EVIDENCE_TIER_FOR_DESIGN[design]
    except KeyError:
        raise ValueError(
            f"unknown study design {design!r}; known designs: {STUDY_DESIGNS}") from None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One retrieved piece of evidence, ready to support or refuse a claim."""

    id: str
    #: The study design, which is the primary fact. `tier` is derived from it.
    design: str
    #: Verbatim excerpt relied on. Required: an item with no quote cannot be
    #: checked, and an unchecked item is an assertion with a citation attached.
    quote: str
    citation: str = ""
    title: str = ""
    #: Identifier and its type, e.g. ("38291045", "pmid"). Both or neither: an
    #: identifier with no stated type cannot be resolved, and a lookupable type
    #: with no value is a citation that cannot be followed. Defaulting the type
    #: to a lookupable one would make every un-cited item fail validation for
    #: the wrong reason, so the default is "unspecified".
    identifier: str = ""
    identifier_type: str = ""
    #: Which SourceCard this came through, so the artifact can name the snapshot.
    source_card_id: str = ""
    #: SHA-256 of the full retrieved content the quote was located in.
    content_hash: str = ""
    #: True only when `quote` was located by offset inside that content.
    quote_verified: bool = False
    #: The four quality dimensions. Never collapsed into a grade.
    quality: Any = None
    #: Study descriptors. `population` and `outcome` are the ones claim checking
    #: reads; the others are carried for the report.
    subject: str = ""
    population: str = ""
    condition: str = ""
    comparator: str = ""
    outcome: str = ""
    effect: str = ""
    sample_size: int = 0
    year: int = 0
    #: Retraction state. `unverified` is not `not_retracted` — see note below.
    retracted: str = "unverified"
    retrieved_by: str = ""
    retrieval_run: str = ""
    retrieved_at: float = 0.0
    #: Ids of other items that contradict this one. Conflict is a first-class
    #: field because a body of evidence is rarely unanimous and a system that
    #: cannot represent disagreement will report false confidence.
    conflicts_with: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("EvidenceItem needs an id")
        # Raises on an unknown design rather than coercing.
        tier_for_design(self.design)
        if not self.quote.strip():
            raise ValueError(
                f"evidence {self.id!r} has no quote; an item without an excerpt "
                "cannot be verified against its source")
        if self.identifier_type and self.identifier_type not in IDENTIFIER_TYPES:
            raise ValueError(
                f"identifier_type {self.identifier_type!r} is not one of {IDENTIFIER_TYPES}")
        if self.retracted not in ("not_retracted", "retracted", "expression_of_concern",
                                  "unverified"):
            raise ValueError(f"unknown retraction state {self.retracted!r}")
        if self.quality is not None:
            from .quality import EvidenceQuality
            if not isinstance(self.quality, EvidenceQuality):
                raise TypeError("quality must be an EvidenceQuality")
        if self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")
        if self.identifier and not self.identifier_type:
            raise ValueError(
                f"evidence {self.id!r} has identifier {self.identifier!r} but states no "
                "identifier_type; an identifier of unknown scheme cannot be resolved")
        if self.identifier_type in LOOKUPABLE_IDENTIFIERS and not self.identifier:
            raise ValueError(
                f"identifier_type {self.identifier_type!r} requires an identifier value")

    @property
    def tier(self) -> EvidenceTier:
        """The coarse ranking, derived. Never stored, so it cannot drift."""
        return tier_for_design(self.design)

    @property
    def is_prediction(self) -> bool:
        """Whether this is model output rather than an observation.

        The distinction the whole network-pharmacology contract rests on: a
        predicted target and a measured target are different kinds of fact and
        must stay separable all the way to the artifact.
        """
        return self.design in PREDICTIVE_DESIGNS

    @property
    def usable(self) -> bool:
        """Whether this item may support a claim at all.

        Retracted work is not usable in any form. `unverified` retraction state
        is usable but is reported as a caveat by :meth:`caveats` — refusing it
        outright would make the system unusable offline, while treating it as
        clean would be a lie.
        """
        return self.retracted != "retracted"

    @property
    def crossref_ready(self) -> bool:
        """Whether a reader can look this up from the citation alone."""
        return bool(self.identifier) and self.identifier_type in LOOKUPABLE_IDENTIFIERS

    def caveats(self) -> tuple[str, ...]:
        """What a reader must know before trusting this item. All of it, not one line.

        Every applicable caveat is returned. A summary that picked the most
        severe one would hide the others, and these are not ranked — a
        retracted article and an unverified retraction check are different
        problems, not two points on one scale.
        """
        out: list[str] = []
        if self.retracted == "retracted":
            out.append("RETRACTED: this item may not support any claim")
        elif self.retracted == "expression_of_concern":
            out.append("expression of concern has been issued for this item")
        elif self.retracted == "unverified":
            out.append("retraction status has not been checked")
        if not self.quote_verified:
            out.append("quote was not located in the retrieved content")
        if not self.crossref_ready:
            out.append("no lookupable identifier; the citation cannot be checked")
        if self.quality is not None:
            for name in self.quality.unassessed:
                out.append(f"quality dimension {name} was not assessed")
        if self.conflicts_with:
            out.append(f"conflicts with {len(self.conflicts_with)} other item(s)")
        if self.design in ("animal", "in_vitro"):
            out.append(f"{self.design} evidence does not describe human outcomes")
        return tuple(out)

    def licenses(self, claim_kind: str) -> bool:
        """Whether this item's tier is enough for a claim kind at all.

        This is the *tier* question only, and answering it does not mean the
        claim is licensed — quality, scope and population still have to agree.
        It is a cheap necessary condition, not a sufficient one.
        """
        floor = CLAIM_KINDS.get(claim_kind)
        if floor is None:
            return False
        return self.tier >= floor and self.usable

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "design": self.design, "tier": self.tier.name.lower(),
                "tier_rank": int(self.tier), "quote": self.quote,
                "citation": self.citation, "title": self.title,
                "identifier": self.identifier, "identifier_type": self.identifier_type,
                "source_card_id": self.source_card_id, "content_hash": self.content_hash,
                "quote_verified": self.quote_verified,
                "quality": self.quality.as_dict() if self.quality is not None else None,
                "subject": self.subject, "population": self.population,
                "condition": self.condition, "comparator": self.comparator,
                "outcome": self.outcome, "effect": self.effect,
                "sample_size": self.sample_size, "year": self.year,
                "retracted": self.retracted, "retrieved_by": self.retrieved_by,
                "retrieval_run": self.retrieval_run, "retrieved_at": self.retrieved_at,
                "conflicts_with": list(self.conflicts_with),
                "usable": self.usable, "crossref_ready": self.crossref_ready,
                "caveats": list(self.caveats()), "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceItem":
        from .quality import EvidenceQuality
        quality = data.get("quality")
        return cls(
            id=str(data["id"]), design=str(data["design"]), quote=str(data["quote"]),
            citation=str(data.get("citation") or ""), title=str(data.get("title") or ""),
            identifier=str(data.get("identifier") or ""),
            identifier_type=str(data.get("identifier_type") or ""),
            source_card_id=str(data.get("source_card_id") or ""),
            content_hash=str(data.get("content_hash") or ""),
            quote_verified=bool(data.get("quote_verified")),
            quality=(EvidenceQuality.from_dict(quality) if isinstance(quality, Mapping)
                     else quality),
            subject=str(data.get("subject") or ""),
            population=str(data.get("population") or ""),
            condition=str(data.get("condition") or ""),
            comparator=str(data.get("comparator") or ""),
            outcome=str(data.get("outcome") or ""), effect=str(data.get("effect") or ""),
            sample_size=int(data.get("sample_size") or 0),
            year=int(data.get("year") or 0),
            retracted=str(data.get("retracted") or "unverified"),
            retrieved_by=str(data.get("retrieved_by") or ""),
            retrieval_run=str(data.get("retrieval_run") or ""),
            retrieved_at=float(data.get("retrieved_at") or 0.0),
            conflicts_with=tuple(data.get("conflicts_with") or ()),
            notes=str(data.get("notes") or ""))


def designs_for_tier(tier: EvidenceTier) -> Sequence[str]:
    """The designs a tier covers. The inverse of :func:`tier_for_design`.

    Returned as a sequence because the mapping is many-to-one: PRECLINICAL
    covers two designs, and a caller checking "is this tier actually the design
    I think it is" needs to see both.
    """
    return tuple(d for d, t in EVIDENCE_TIER_FOR_DESIGN.items() if t is tier)


EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "title": "EvidenceItem",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "design", "quote"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "design": {"type": "string", "enum": list(STUDY_DESIGNS)},
        "quote": {"type": "string", "minLength": 1},
        "citation": {"type": "string"},
        "title": {"type": "string"},
        "identifier": {"type": "string"},
        "identifier_type": {"type": "string", "enum": ["", *IDENTIFIER_TYPES]},
        "source_card_id": {"type": "string"},
        "content_hash": {"type": "string"},
        "quote_verified": {"type": "boolean"},
        "quality": {"$ref": "#/$defs/EvidenceQuality"},
        "subject": {"type": "string"}, "population": {"type": "string"},
        "condition": {"type": "string"}, "comparator": {"type": "string"},
        "outcome": {"type": "string"}, "effect": {"type": "string"},
        "sample_size": {"type": "integer", "minimum": 0},
        "year": {"type": "integer", "minimum": 0},
        "retracted": {"type": "string",
                      "enum": ["not_retracted", "retracted",
                               "expression_of_concern", "unverified"]},
        "retrieved_by": {"type": "string"}, "retrieval_run": {"type": "string"},
        "retrieved_at": {"type": "number"},
        "conflicts_with": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "description": "One retrieved source. Design is stored; tier is derived.",
}
