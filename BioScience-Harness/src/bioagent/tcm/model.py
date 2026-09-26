"""Typed traditional Chinese medicine knowledge.

The 2026-09-18 review's F11: the harness could fetch TCM datasets and search TCM
literature, and had no representation of what a TCM claim *is*. A formula is not a
compound; a herb is not its processed form; 桂枝汤主之 in the Shanghan Lun is an
attribution, not a trial result; and "黄芪治疗心衰" can be true as a classical action, an
observational association or a randomised efficacy claim, which are three different
claims with three different evidence requirements. Free text carries none of those
distinctions, so nothing downstream could check them.

This module is the vocabulary. Each entity is a frozen dataclass with the fields the
distinctions need: a herb with its nature, flavours, meridians and actions; a processed
form that changes them; a formula with its ingredients and their 君臣佐使 roles; a
syndrome (证) with its manifestations and treatment principle; a classical passage that
can be cited for an attribution; a study that can be cited for a clinical claim; a
relation between two entities carrying the tier of evidence behind it; and a safety
record. ``EvidenceTier`` orders the tiers and says which kind of claim each one
licenses. The knowledge base (``knowledge.py``) holds the entities and answers scope
questions over them.

Nothing here is a clinical recommendation. It is a model of what the sources say and how
strong the saying is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Mapping

__all__ = ["EvidenceTier", "CLAIM_KINDS", "Herb", "ProcessedHerb", "Ingredient", "Formula",
           "Syndrome", "ClassicalPassage", "StudyEvidence", "ActionRelation", "SafetyRecord",
           "ROLES", "PREDICATES", "SAFETY_KINDS"]


class EvidenceTier(IntEnum):
    """How strong the evidence behind a relation is, ordered for comparison.

    The order is *clinical* strength. A classical text is the weakest clinical evidence
    and the strongest provenance for an attribution — which is why the two are separate
    claim kinds rather than points on one scale.
    """

    CLASSICAL_TEXT = 1       # 经典本草、方书、医籍记载
    EXPERT_EXPERIENCE = 2    # 名医经验、教材、专家共识、药典
    PRECLINICAL = 3          # 体外、动物、网络药理学、分子对接
    CASE_REPORT = 4          # 病例报告、病例系列
    OBSERVATIONAL = 5        # 队列、病例对照、横断面、真实世界
    RANDOMIZED_TRIAL = 6     # 随机对照试验
    SYSTEMATIC_REVIEW = 7    # 系统评价 / 荟萃分析（基于 RCT）

    @property
    def chinese(self) -> str:
        return _TIER_ZH[self]

    @property
    def clinical(self) -> bool:
        """Evidence from people in a clinical setting, as opposed to texts or models."""
        return self >= EvidenceTier.CASE_REPORT

    @property
    def needs_citation(self) -> bool:
        """Every tier above expert experience is a published study and must cite one."""
        return self >= EvidenceTier.PRECLINICAL


_TIER_ZH = {
    EvidenceTier.CLASSICAL_TEXT: "经典文献记载", EvidenceTier.EXPERT_EXPERIENCE: "名医经验/专家共识",
    EvidenceTier.PRECLINICAL: "临床前研究", EvidenceTier.CASE_REPORT: "病例报告/病例系列",
    EvidenceTier.OBSERVATIONAL: "观察性研究", EvidenceTier.RANDOMIZED_TRIAL: "随机对照试验",
    EvidenceTier.SYSTEMATIC_REVIEW: "系统评价/荟萃分析",
}

#: The kind of claim -> the weakest tier that licenses it. An attribution ("the Shanghan
#: Lun prescribes it for ...") needs only the text; an efficacy claim needs a trial.
CLAIM_KINDS: Mapping[str, EvidenceTier] = {
    "attribution": EvidenceTier.CLASSICAL_TEXT,      # 记载/主治：文献这样说
    "traditional_use": EvidenceTier.EXPERT_EXPERIENCE,  # 传统应用/教材功效
    "mechanism": EvidenceTier.PRECLINICAL,           # 机制/靶点
    "safety_signal": EvidenceTier.CASE_REPORT,       # 安全性信号
    "association": EvidenceTier.OBSERVATIONAL,       # 相关性
    "efficacy": EvidenceTier.RANDOMIZED_TRIAL,       # 疗效
    "recommendation": EvidenceTier.SYSTEMATIC_REVIEW,  # 推荐
}

ROLES: tuple[str, ...] = ("君", "臣", "佐", "使")
PREDICATES: frozenset[str] = frozenset({
    "treats", "indicated_for", "contraindicated_in", "potentiates", "antagonises",
    "incompatible_with", "contains", "processed_from", "targets", "recorded_in",
})
SAFETY_KINDS: frozenset[str] = frozenset({
    "toxicity", "interaction", "contraindication", "adverse_event", "incompatibility",
})
_SEVERITIES = frozenset({"low", "moderate", "high", "critical"})


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True, slots=True)
class Herb:
    """A crude drug (药材) as the materia medica describe it."""

    id: str
    chinese: str
    pinyin: str
    latin: str = ""
    species: tuple[str, ...] = ()
    part: str = ""
    nature: str = ""                     # 四气：寒 热 温 凉 平（及 微温 等）
    flavours: tuple[str, ...] = ()       # 五味：辛 甘 酸 苦 咸 淡 涩
    meridians: tuple[str, ...] = ()      # 归经
    actions: tuple[str, ...] = ()        # 功效
    aliases: tuple[str, ...] = ()
    toxicity: str = "无毒"               # 药典标注：无毒 / 有小毒 / 有毒 / 有大毒
    source: str = ""                     # 首载文献

    def __post_init__(self) -> None:
        if not self.id or not self.chinese:
            raise ValueError("a herb needs an id and its Chinese name")
        for name in ("species", "flavours", "meridians", "actions", "aliases"):
            object.__setattr__(self, name, _tuple(getattr(self, name)))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            n for n in (self.chinese, self.pinyin, self.latin, *self.aliases) if n))

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "chinese": self.chinese, "pinyin": self.pinyin,
                "latin": self.latin, "species": list(self.species), "part": self.part,
                "nature": self.nature, "flavours": list(self.flavours),
                "meridians": list(self.meridians), "actions": list(self.actions),
                "aliases": list(self.aliases), "toxicity": self.toxicity,
                "source": self.source}


@dataclass(frozen=True, slots=True)
class ProcessedHerb:
    """A processed form (炮制品). Processing changes nature, potency or toxicity."""

    id: str
    herb_id: str
    method: str                          # 蜜炙 / 姜制 / 胆巴制 ...
    chinese: str
    effect_change: str = ""
    toxicity_change: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not (self.id and self.herb_id and self.method and self.chinese):
            raise ValueError("a processed herb needs an id, the base herb, the method and a name")

    @property
    def names(self) -> tuple[str, ...]:
        return (self.chinese,)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "herb_id": self.herb_id, "method": self.method,
                "chinese": self.chinese, "effect_change": self.effect_change,
                "toxicity_change": self.toxicity_change, "notes": self.notes}


@dataclass(frozen=True, slots=True)
class Ingredient:
    """One herb in a formula, with its 君臣佐使 role and the classical dose."""

    herb_id: str
    role: str
    dose: str = ""                       # as the source states it (两 / 枚 / g)
    processed: str = ""                  # id of the processed form, when one is meant

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"ingredient {self.herb_id!r}: role {self.role!r} is not one of {ROLES}")

    def as_dict(self) -> dict[str, Any]:
        return {"herb_id": self.herb_id, "role": self.role, "dose": self.dose,
                "processed": self.processed}


@dataclass(frozen=True, slots=True)
class Formula:
    """A prescription (方剂): named ingredients in roles, from a named source."""

    id: str
    chinese: str
    pinyin: str
    source: str
    ingredients: tuple[Ingredient, ...]
    actions: tuple[str, ...] = ()        # 功用
    indications: tuple[str, ...] = ()    # syndrome ids
    dosage_form: str = "汤"
    contraindications: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not (self.id and self.chinese and self.source):
            raise ValueError("a formula needs an id, its Chinese name and its source")
        if not self.ingredients:
            raise ValueError(f"formula {self.chinese} lists no ingredients")
        if not any(i.role == "君" for i in self.ingredients):
            raise ValueError(f"formula {self.chinese} names no sovereign (君) ingredient")
        for name in ("actions", "indications", "contraindications", "aliases"):
            object.__setattr__(self, name, _tuple(getattr(self, name)))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(n for n in (self.chinese, self.pinyin, *self.aliases) if n))

    @property
    def herb_ids(self) -> tuple[str, ...]:
        return tuple(i.herb_id for i in self.ingredients)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "chinese": self.chinese, "pinyin": self.pinyin,
                "source": self.source, "ingredients": [i.as_dict() for i in self.ingredients],
                "actions": list(self.actions), "indications": list(self.indications),
                "dosage_form": self.dosage_form,
                "contraindications": list(self.contraindications),
                "aliases": list(self.aliases), "notes": self.notes}


@dataclass(frozen=True, slots=True)
class Syndrome:
    """A pattern (证), the unit TCM treatment is indicated for."""

    id: str
    chinese: str
    pinyin: str
    english: str = ""
    category: str = ""                   # 脏腑辨证 / 六经辨证 / 气血津液辨证 / 八纲 ...
    manifestations: tuple[str, ...] = ()
    tongue: str = ""
    pulse: str = ""
    treatment_principle: str = ""       # 治法
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.id and self.chinese):
            raise ValueError("a syndrome needs an id and its Chinese name")
        for name in ("manifestations", "aliases"):
            object.__setattr__(self, name, _tuple(getattr(self, name)))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            n for n in (self.chinese, self.pinyin, self.english, *self.aliases) if n))

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "chinese": self.chinese, "pinyin": self.pinyin,
                "english": self.english, "category": self.category,
                "manifestations": list(self.manifestations), "tongue": self.tongue,
                "pulse": self.pulse, "treatment_principle": self.treatment_principle,
                "aliases": list(self.aliases)}


@dataclass(frozen=True, slots=True)
class ClassicalPassage:
    """A quotable passage from a classical text: what an attribution cites."""

    id: str
    source: str                          # 伤寒论 / 神农本草经 / ...
    text: str
    chapter: str = ""
    era: str = ""
    translation: str = ""
    mentions: tuple[str, ...] = ()       # entity ids the passage is about

    def __post_init__(self) -> None:
        if not (self.id and self.source and self.text):
            raise ValueError("a passage needs an id, its source and its text")
        object.__setattr__(self, "mentions", _tuple(self.mentions))

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source": self.source, "chapter": self.chapter,
                "era": self.era, "text": self.text, "translation": self.translation,
                "mentions": list(self.mentions)}


@dataclass(frozen=True, slots=True)
class StudyEvidence:
    """A study that can be cited for a clinical or preclinical claim.

    A tier at or above PRECLINICAL is a published study and must carry a PMID, a DOI or
    a registry id: a knowledge base must not hold a "randomised trial" that nobody can
    look up. The two lowest tiers cite a text or a consensus document instead.
    """

    id: str
    tier: EvidenceTier
    subject_id: str                      # herb / processed / formula id
    design: str = ""
    condition: str = ""                  # biomedical condition studied
    population: str = ""                 # who was studied
    comparator: str = ""
    outcome: str = ""
    effect: str = ""
    sample_size: int | None = None
    year: int | None = None
    pmid: str = ""
    doi: str = ""
    registry_id: str = ""
    citation: str = ""                   # for CLASSICAL_TEXT / EXPERT_EXPERIENCE
    retracted: bool | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        tier = EvidenceTier(self.tier)
        object.__setattr__(self, "tier", tier)
        if not (self.id and self.subject_id):
            raise ValueError("a study needs an id and the entity it studied")
        if tier.needs_citation and not (self.pmid or self.doi or self.registry_id):
            raise ValueError(
                f"study {self.id!r} claims tier {tier.name} and cites no PMID, DOI or "
                "registry id; a study nobody can look up is not evidence")
        if not tier.needs_citation and not self.citation:
            raise ValueError(
                f"study {self.id!r} at tier {tier.name} must name the text or consensus "
                "document it comes from")

    @property
    def usable(self) -> bool:
        return self.retracted is not True

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tier": self.tier.name, "tier_zh": self.tier.chinese,
                "subject_id": self.subject_id, "design": self.design,
                "condition": self.condition, "population": self.population,
                "comparator": self.comparator, "outcome": self.outcome, "effect": self.effect,
                "sample_size": self.sample_size, "year": self.year, "pmid": self.pmid,
                "doi": self.doi, "registry_id": self.registry_id, "citation": self.citation,
                "retracted": self.retracted, "notes": self.notes}


@dataclass(frozen=True, slots=True)
class ActionRelation:
    """``subject predicate object`` with the tier of evidence behind it and its scope."""

    id: str
    subject_id: str
    predicate: str
    object_id: str
    tier: EvidenceTier
    evidence_ids: tuple[str, ...] = ()   # passage or study ids
    population: str = ""                 # the population the evidence covers, if stated
    condition: str = ""                  # the biomedical condition, if stated
    notes: str = ""

    def __post_init__(self) -> None:
        if self.predicate not in PREDICATES:
            raise ValueError(
                f"relation {self.id!r}: predicate {self.predicate!r} is not one of "
                f"{sorted(PREDICATES)}")
        object.__setattr__(self, "tier", EvidenceTier(self.tier))
        object.__setattr__(self, "evidence_ids", _tuple(self.evidence_ids))
        if not self.evidence_ids:
            raise ValueError(f"relation {self.id!r} cites no evidence")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "subject_id": self.subject_id, "predicate": self.predicate,
                "object_id": self.object_id, "tier": self.tier.name,
                "tier_zh": self.tier.chinese, "evidence_ids": list(self.evidence_ids),
                "population": self.population, "condition": self.condition,
                "notes": self.notes}


@dataclass(frozen=True, slots=True)
class SafetyRecord:
    """Toxicity, an interaction, a contraindication, an adverse event or 十八反/十九畏."""

    id: str
    subject_id: str
    kind: str
    description: str
    severity: str = "moderate"
    tier: EvidenceTier = EvidenceTier.EXPERT_EXPERIENCE
    evidence_ids: tuple[str, ...] = ()
    population: str = ""
    counterpart_id: str = ""             # the other party of an interaction/incompatibility
    management: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SAFETY_KINDS:
            raise ValueError(f"safety record {self.id!r}: kind {self.kind!r} is not one of "
                             f"{sorted(SAFETY_KINDS)}")
        if self.severity not in _SEVERITIES:
            raise ValueError(f"safety record {self.id!r}: severity {self.severity!r} is not "
                             f"one of {sorted(_SEVERITIES)}")
        object.__setattr__(self, "tier", EvidenceTier(self.tier))
        object.__setattr__(self, "evidence_ids", _tuple(self.evidence_ids))
        if not self.description.strip():
            raise ValueError(f"safety record {self.id!r} has no description")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "subject_id": self.subject_id, "kind": self.kind,
                "description": self.description, "severity": self.severity,
                "tier": self.tier.name, "tier_zh": self.tier.chinese,
                "evidence_ids": list(self.evidence_ids), "population": self.population,
                "counterpart_id": self.counterpart_id, "management": self.management}
