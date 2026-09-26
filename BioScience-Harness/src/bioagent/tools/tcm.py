"""TCM knowledge tools: look things up, check scope, check compatibility. Offline.

Each function reads the checked-in knowledge base (``bioagent.tcm``) and answers one
question. None of them recommends a treatment: they report what the sources record,
how strong the record is, and whether a stated claim falls within its scope.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..tcm import CLAIM_KINDS, EvidenceTier, default_knowledge
from ..tcm.knowledge import _kind_of

__all__ = ["tcm_lookup", "tcm_herb", "tcm_formula", "tcm_syndrome", "tcm_compatibility",
           "tcm_applicability", "tcm_evidence_tiers", "tcm_classical_search"]


def _kb():
    return default_knowledge()


def _summary(entity: Any) -> dict[str, Any]:
    return {"id": entity.id, "kind": _kind_of(entity), "chinese": getattr(entity, "chinese", ""),
            "pinyin": getattr(entity, "pinyin", "")}


def tcm_lookup(name: str, kind: str = "") -> dict[str, Any]:
    """Resolve a Chinese, pinyin or Latin name to one entity, or list the candidates it
    could mean. Ambiguity is reported, never resolved silently."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if kind and kind not in ("herb", "processed", "formula", "syndrome"):
        raise ValueError("kind must be one of herb, processed, formula, syndrome, or empty")
    resolution = _kb().resolve(name, kind=kind)
    return resolution.as_dict()


def tcm_herb(name: str) -> dict[str, Any]:
    """A herb's nature, flavours, meridians and actions, its processed forms, the formulas
    that contain it, its recorded relations and its safety records."""
    kb = _kb()
    herb = kb.require(name, kind="herb")
    return {
        "herb": herb.as_dict(),
        "processed_forms": [p.as_dict() for p in kb.processed_forms(herb.id)],
        "formulas": [_summary(f) for f in kb.formulas_containing(herb.id)],
        "relations": [r.as_dict() for r in kb.relations_of(herb.id)],
        "passages": [p.as_dict() for p in kb.passages_mentioning(herb.id)],
        "safety": [s.as_dict() for s in kb.safety_for(herb.id)],
    }


def tcm_formula(name: str) -> dict[str, Any]:
    """A formula's ingredients with their 君臣佐使 roles, its source passages, the
    syndromes it is indicated for, its inherited safety records and a compatibility
    check over its ingredients."""
    kb = _kb()
    formula = kb.require(name, kind="formula")
    ingredients = []
    for ingredient in formula.ingredients:
        herb = kb.herbs.get(ingredient.herb_id)
        item = ingredient.as_dict()
        item["chinese"] = herb.chinese if herb is not None else ingredient.herb_id
        if ingredient.processed and ingredient.processed in kb.processed:
            item["processed_chinese"] = kb.processed[ingredient.processed].chinese
        ingredients.append(item)
    return {
        "formula": formula.as_dict(),
        "ingredients": ingredients,
        "indications": [kb.syndromes[s].as_dict() for s in formula.indications
                        if s in kb.syndromes],
        "passages": [p.as_dict() for p in kb.passages_mentioning(formula.id)],
        "relations": [r.as_dict() for r in kb.relations_of(formula.id)],
        "safety": [s.as_dict() for s in kb.safety_for(formula.id)],
        "compatibility": [c.as_dict() for c in kb.check_compatibility(formula.herb_ids)],
    }


def tcm_syndrome(name: str) -> dict[str, Any]:
    """A syndrome (证) with its manifestations, tongue, pulse and treatment principle,
    and the formulas and herbs recorded for it, each with its evidence tier."""
    kb = _kb()
    syndrome = kb.require(name, kind="syndrome")
    return {
        "syndrome": syndrome.as_dict(),
        "formulas": [_summary(f) for f in kb.formulas_for(syndrome.id)],
        "relations": [r.as_dict() for r in kb.relations_of(object_id=syndrome.id)],
        "passages": [p.as_dict() for p in kb.passages_mentioning(syndrome.id)],
    }


def tcm_compatibility(herbs: Sequence[str]) -> dict[str, Any]:
    """Check a combination of herbs (or a formula's ingredients) for recorded
    incompatibilities — 十八反 and other records — and unresolved names."""
    if not isinstance(herbs, (list, tuple)) or not herbs:
        raise ValueError("herbs must be a non-empty list of names")
    kb = _kb()
    resolved: list[dict[str, Any]] = []
    ids: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for name in herbs:
        resolution = kb.resolve(str(name))
        if resolution.found and _kind_of(resolution.entity) in ("herb", "processed"):
            resolved.append(_summary(resolution.entity))
            ids.append(resolution.entity.id)
        elif resolution.found and _kind_of(resolution.entity) == "formula":
            resolved.append(_summary(resolution.entity))
            ids.extend(resolution.entity.herb_ids)
        else:
            unresolved.append({"name": str(name), "candidates": resolution.as_dict()["candidates"]})
    conflicts = kb.check_compatibility(ids)
    return {"resolved": resolved, "unresolved": unresolved,
            "conflicts": [c.as_dict() for c in conflicts],
            "compatible": not conflicts and not unresolved,
            "note": ("every name resolved and no recorded incompatibility was found; absence of "
                     "a record is not evidence of safety") if not conflicts and not unresolved
            else "see conflicts and unresolved names"}


def tcm_applicability(subject: str, object: str, claim_kind: str = "efficacy",
                      population: str = "", condition: str = "") -> dict[str, Any]:
    """Whether the recorded evidence that ``subject`` treats or is indicated for
    ``object`` licenses a claim of ``claim_kind`` (attribution, traditional_use,
    mechanism, safety_signal, association, efficacy, recommendation) for the given
    population and condition. A classical passage licenses an attribution and not an
    efficacy claim; evidence from one population is extrapolated to another."""
    if claim_kind not in CLAIM_KINDS:
        raise ValueError(f"claim_kind must be one of {sorted(CLAIM_KINDS)}")
    kb = _kb()
    subject_entity = kb.require(subject)
    object_entity = kb.require(object)
    verdicts = kb.claims_between(subject_entity.id, object_entity.id, claim_kind=claim_kind,
                                 population=population, condition=condition)
    best = verdicts[0].verdict if verdicts else "unsupported"
    return {
        "subject": _summary(subject_entity), "object": _summary(object_entity),
        "claim_kind": claim_kind, "required_tier": CLAIM_KINDS[claim_kind].name,
        "required_tier_zh": CLAIM_KINDS[claim_kind].chinese,
        "verdict": best, "licensed": best == "within_scope",
        "relations": [v.as_dict() for v in verdicts],
        "note": ("no recorded relation between these entities" if not verdicts else
                 "the strongest applicable relation decides the verdict"),
    }


def tcm_evidence_tiers() -> dict[str, Any]:
    """The evidence tiers in order and the kind of claim each one licenses."""
    return {
        "tiers": [{"tier": t.name, "rank": int(t), "chinese": t.chinese, "clinical": t.clinical,
                   "needs_citation": t.needs_citation} for t in EvidenceTier],
        "claim_kinds": {k: {"required_tier": v.name, "required_tier_zh": v.chinese}
                        for k, v in CLAIM_KINDS.items()},
        "rule": "a claim is licensed only by evidence at or above the tier its kind needs, "
                "and only within the population and condition that evidence covers",
    }


def tcm_classical_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Find classical passages by a phrase or by the entities they mention."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 50:
        raise ValueError("limit must be an integer between 1 and 50")
    kb = _kb()
    hits = kb.search_passages(query)
    resolution = kb.resolve(query)
    if resolution.found:
        for p in kb.passages_mentioning(resolution.entity.id):
            if p not in hits:
                hits.append(p)
    return {"query": query, "count": len(hits),
            "passages": [p.as_dict() for p in hits[:limit]]}
