"""Traditional Chinese medicine knowledge: a typed model and a scoped knowledge base.

    from bioagent.tcm import default_knowledge, EvidenceTier
    kb = default_knowledge()
    kb.resolve("参").ambiguous                      # 人参 or 丹参: the caller decides
    kb.claims_between("formula.guizhitang", "syndrome.taiyang_zhongfeng",
                      claim_kind="efficacy")[0].verdict   # "unsupported": a text is not a trial
    kb.check_compatibility(["herb.gancao", "herb.gansui"])   # 十八反

See ``model.py`` for the entities and ``knowledge.py`` for the questions.
"""

from .knowledge import (
    Applicability, Conflict, Resolution, TCMKnowledgeBase, default_knowledge, seed,
)
from .model import (
    CLAIM_KINDS, PREDICATES, ROLES, SAFETY_KINDS, ActionRelation, ClassicalPassage,
    EvidenceTier, Formula, Herb, Ingredient, ProcessedHerb, SafetyRecord, StudyEvidence,
    Syndrome,
)

__all__ = [
    "TCMKnowledgeBase", "Resolution", "Applicability", "Conflict", "default_knowledge", "seed",
    "EvidenceTier", "CLAIM_KINDS", "ROLES", "PREDICATES", "SAFETY_KINDS",
    "Herb", "ProcessedHerb", "Ingredient", "Formula", "Syndrome", "ClassicalPassage",
    "StudyEvidence", "ActionRelation", "SafetyRecord",
]
