"""Evidence quality as separate dimensions, never folded into one label.

`tcm.model.EvidenceTier` answers *what kind of source is this* — a classical
passage, a case report, a randomised trial. That is a rank, and it is the right
shape for the question "may this source license this claim kind?".

It is the wrong shape for "how good is this evidence?", and collapsing the two
is the defect this module exists to prevent. A large, well-conducted,
pre-registered observational cohort can be stronger evidence for an association
than a small randomised trial with high attrition; a systematic review of two
small studies is not automatically better than one large one. A single ordinal
label forces one of those judgements to be lost, and the loss is invisible
because the surviving number looks precise.

So quality is reported as **named dimensions with no default aggregation**:

* ``risk_of_bias`` — how the study could be wrong, on the standard ladder
* ``directness`` — whether the measured thing is the claimed thing
* ``precision`` — whether the estimate is tight enough to act on
* ``consistency`` — agreement with other evidence on the same question

There is deliberately no ``score``, no ``grade`` and no ``total``. Consumers
that want a summary must state which dimensions they are trading off and why;
:meth:`EvidenceQuality.weakest` exists to make that argument explicit rather
than to hide it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Mapping

__all__ = ["Consistency", "Directness", "EvidenceQuality", "NotAssessed",
           "Precision", "RiskOfBias", "UNKNOWN"]

#: Marker for a dimension nobody has judged yet. Absence of an assessment is
#: recorded as an assessment that did not happen — never as a good result.
UNKNOWN = "unknown"


class NotAssessed(ValueError):
    """A dimension was consumed as if it had been judged when it had not."""


class RiskOfBias(IntEnum):
    """Cochrane-style risk of bias, ordered least to most risk."""

    LOW = 0
    SOME_CONCERNS = 1
    HIGH = 2
    CRITICAL = 3
    NOT_ASSESSED = 4

    @property
    def chinese(self) -> str:
        return {"low": "低偏倚风险", "some_concerns": "存在一定问题",
                "high": "高偏倚风险", "critical": "严重偏倚风险",
                "not_assessed": "未评估"}[self.name.lower()]

    @property
    def blocks_efficacy_claim(self) -> bool:
        """Whether this level alone rules out a clinical efficacy claim."""
        return self >= RiskOfBias.HIGH


class Directness(IntEnum):
    """Whether the evidence measures the outcome the claim is about.

    ``PARTIAL`` is a surrogate outcome for the claimed one — a biomarker
    standing in for a clinical endpoint. ``EXTRAPOLATED`` crosses a boundary the
    evidence does not cover: another population, another species, a
    computational prediction standing in for a measurement. Extrapolation is
    not a defect in the study; it is a defect in the *claim*, which is why the
    compiler reads this dimension and the others do not gate claims.
    """

    DIRECT = 0
    PARTIAL = 1
    EXTRAPOLATED = 2
    NOT_ASSESSED = 3

    @property
    def chinese(self) -> str:
        return {"direct": "直接证据", "partial": "替代终点",
                "extrapolated": "外推", "not_assessed": "未评估"}[self.name.lower()]


class Precision(IntEnum):
    """Whether the estimate is tight enough to support the claim's strength."""

    PRECISE = 0
    IMPRECISE = 1
    NOT_ASSESSED = 2

    @property
    def chinese(self) -> str:
        return {"precise": "精确", "imprecise": "不精确",
                "not_assessed": "未评估"}[self.name.lower()]


class Consistency(IntEnum):
    """Agreement with other evidence addressing the same question."""

    CONSISTENT = 0
    INCONSISTENT = 1
    SINGLE_STUDY = 2
    NOT_ASSESSED = 3

    @property
    def chinese(self) -> str:
        return {"consistent": "一致", "inconsistent": "不一致",
                "single_study": "单一研究", "not_assessed": "未评估"}[self.name.lower()]


_DIMENSIONS: Mapping[str, type[IntEnum]] = {
    "risk_of_bias": RiskOfBias, "directness": Directness,
    "precision": Precision, "consistency": Consistency,
}


@dataclass(frozen=True, slots=True)
class EvidenceQuality:
    """Four independent judgements about one piece of evidence.

    Every dimension defaults to ``NOT_ASSESSED`` and stays there until somebody
    judges it. Defaulting to a *value* rather than to *absent* is how a quality
    summary starts lying, so it is not offered.
    """

    risk_of_bias: RiskOfBias = RiskOfBias.NOT_ASSESSED
    directness: Directness = Directness.NOT_ASSESSED
    precision: Precision = Precision.NOT_ASSESSED
    consistency: Consistency = Consistency.NOT_ASSESSED
    #: Free-text rationale per dimension, keyed by dimension name. A judgement
    #: without a stated reason is not reviewable, so prose is not optional in
    #: practice even though the type does not force it.
    rationale: Mapping[str, str] = field(default_factory=dict)
    assessed_by: str = ""
    assessment_tool: str = ""

    def __post_init__(self) -> None:
        for name, enum in _DIMENSIONS.items():
            value = getattr(self, name)
            if not isinstance(value, enum):
                raise TypeError(f"{name} must be a {enum.__name__}, got {value!r}")
        unknown = set(self.rationale) - set(_DIMENSIONS)
        if unknown:
            raise ValueError(f"rationale names unknown dimensions: {sorted(unknown)}")

    @property
    def assessed(self) -> tuple[str, ...]:
        """Names of the dimensions somebody actually judged."""
        return tuple(n for n, e in _DIMENSIONS.items()
                     if getattr(self, n) is not e.NOT_ASSESSED)

    @property
    def unassessed(self) -> tuple[str, ...]:
        return tuple(n for n, e in _DIMENSIONS.items()
                     if getattr(self, n) is e.NOT_ASSESSED)

    @property
    def complete(self) -> bool:
        return not self.unassessed

    def weakest(self) -> tuple[str, IntEnum]:
        """The dimension furthest from ideal, and its name.

        This is the only summary the module offers, and it is deliberately a
        *pointer* rather than a number: it says where to look, not how good the
        evidence is overall. Ties break in declaration order so the answer is
        deterministic and does not depend on dict iteration.
        """
        if not self.assessed:
            raise NotAssessed("no dimension has been assessed")
        worst_name = max(self.assessed, key=lambda n: getattr(self, n).value)
        return worst_name, getattr(self, worst_name)

    def blocks(self, claim_kind: str) -> str:
        """Why this quality cannot support ``claim_kind``, or "" if it can.

        Only two dimensions can refuse a claim on their own, and both refusals
        are structural rather than a matter of degree:

        * ``directness is EXTRAPOLATED`` — the evidence is about something else.
        * ``risk_of_bias >= HIGH`` for a *clinical* claim kind — the estimate is
          too unreliable to be a clinical statement.

        Nothing else blocks. Imprecise, inconsistent or single-study evidence is
        still evidence; it lowers the strength of the claim, which is what
        :meth:`describe` reports, not whether the claim may be made at all.
        """
        if self.directness is Directness.EXTRAPOLATED:
            return "extrapolated evidence cannot support any claim about the target"
        if claim_kind in ("efficacy", "recommendation") and \
                self.risk_of_bias.blocks_efficacy_claim:
            return (f"{self.risk_of_bias.name.lower()} risk of bias cannot support "
                    f"a {claim_kind} claim")
        return ""

    def describe(self) -> str:
        if not self.assessed:
            return "quality not assessed"
        parts = [f"{n.replace('_', ' ')}={getattr(self, n).name.lower()}"
                 for n in self.assessed]
        if self.unassessed:
            parts.append(f"unassessed={','.join(self.unassessed)}")
        return "; ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {"risk_of_bias": self.risk_of_bias.name.lower(),
                "directness": self.directness.name.lower(),
                "precision": self.precision.name.lower(),
                "consistency": self.consistency.name.lower(),
                "assessed": list(self.assessed), "unassessed": list(self.unassessed),
                "rationale": dict(self.rationale),
                "assessed_by": self.assessed_by,
                "assessment_tool": self.assessment_tool,
                "describe": self.describe()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceQuality":
        def dim(name: str) -> Any:
            raw = data.get(name, UNKNOWN)
            if raw is None or str(raw).lower() in ("", UNKNOWN):
                return _DIMENSIONS[name].NOT_ASSESSED
            return _DIMENSIONS[name][str(raw).upper()]
        return cls(risk_of_bias=dim("risk_of_bias"), directness=dim("directness"),
                   precision=dim("precision"), consistency=dim("consistency"),
                   rationale=dict(data.get("rationale") or {}),
                   assessed_by=str(data.get("assessed_by") or ""),
                   assessment_tool=str(data.get("assessment_tool") or ""))


#: JSON Schema for the four dimensions, for consumers that validate documents
#: rather than construct objects (the Arena data build, the compiler's schema
#: export). Kept beside the type so the two cannot drift silently — the enum
#: members are read out of the classes above rather than retyped.
QUALITY_SCHEMA: dict[str, Any] = {
    "title": "EvidenceQuality",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        name: {"type": "string", "enum": [m.name.lower() for m in enum],
               "default": "not_assessed"}
        for name, enum in _DIMENSIONS.items()
    } | {
        "rationale": {"type": "object", "additionalProperties": {"type": "string"}},
        "assessed_by": {"type": "string", "default": ""},
        "assessment_tool": {"type": "string", "default": ""},
    },
    "description": "Four separate judgements. Deliberately no aggregate score.",
}
