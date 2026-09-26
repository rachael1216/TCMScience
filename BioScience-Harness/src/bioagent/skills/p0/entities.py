"""`normalize-tcm-entities` — resolve names to corpus entities, preserving ambiguity.

The refusal this skill is built around: when a name is ambiguous, return the
*candidates* and do not choose. 「姜」 is 生姜 (fresh ginger, 微温, releases the
exterior) or 干姜 (dried ginger, 热, warms the interior and restores yang) —
different drugs with different indications from the same plant. 「甘草」 has
processed forms that are likewise different preparations. A resolver that picked
one would be wrong roughly half the time and confident every time, and nothing
downstream could tell that a choice had been made.

`TCMKnowledgeBase.resolve` already returns `entity=None` with a populated
`candidates` tuple for exactly these cases; this skill maps that faithfully
rather than picking the first. See `p0/common.py` for the shared artifact
assembly the other three skills also use.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...contracts import CandidateClaim, EvidenceItem, ResearchArtifact, require_declared
from ...tcm import knowledge as tcm_knowledge
from ...tcm.model import EvidenceTier, Herb, ProcessedHerb
from .common import (SEED_SOURCE_ID, SKILL_VERSIONS, _now, _quality_for, artifact,
                     json_file, seed_source_card)

__all__ = ["normalize_tcm_entities"]


# ---------------------------------------------------------------------------
# E4-A  normalize-tcm-entities
# ---------------------------------------------------------------------------


def normalize_tcm_entities(names: Sequence[str], *, run_id: str = "") -> ResearchArtifact:
    """Resolve TCM names to corpus entities, preserving ambiguity.

    Returns, per input name, either a resolved entity or the *set* of candidates
    it could mean. The distinction the plan asks for — "ambiguous names are not
    silently merged" — is structural here: `Resolution` carries `candidates`
    separately from `entity`, and this function maps that faithfully rather than
    picking the first.
    """
    kb = tcm_knowledge.default_knowledge()
    source = seed_source_card()

    rows: list[dict[str, Any]] = []
    evidence: list[EvidenceItem] = []
    limitations: list[str] = [
        "resolution is against the shipped seed corpus only; a name absent here may "
        "still be a real drug or formula",
        "no external identifier (InChIKey, UniProt, HGNC) is assigned, because the "
        "seed corpus carries none; identifiers require a connector run",
    ]

    for index, name in enumerate(names):
        raw = str(name).strip()
        if not raw:
            continue
        resolution = kb.resolve(raw)
        candidates = [getattr(c, "id", str(c)) for c in resolution.candidates]
        resolved = resolution.entity
        row: dict[str, Any] = {
            "query": raw,
            "status": "ambiguous" if (candidates and resolved is None) else
                      ("resolved" if resolved is not None else "unresolved"),
            "entity_id": getattr(resolved, "id", None),
            "entity_kind": _kind_of(resolved),
            "candidates": candidates,
            "chinese": getattr(resolved, "chinese", "") if resolved else "",
        }

        # A processed form is a *different* substance from its crude drug. When
        # both resolve, say which one is in play rather than collapsing them.
        if isinstance(resolved, ProcessedHerb):
            parent = kb.entity(resolved.herb_id)
            row["processed_from"] = resolved.herb_id
            row["processing_method"] = resolved.method
            row["effect_change"] = resolved.effect_change
            row["parent_herb"] = getattr(parent, "chinese", "")
            limitations.append(
                f"{raw}: resolved to the processed form {resolved.id!r}; its "
                f"properties differ from the crude drug {resolved.herb_id!r}")
        elif isinstance(resolved, Herb):
            forms = kb.processed_forms(resolved.id)
            row["processed_forms"] = [p.id for p in forms]
            if forms:
                # A crude-drug name that has processed forms is a genuine
                # ambiguity: the same query means different preparations
                # depending on context, and the artifact must not choose.
                row["status"] = "resolved_with_processed_forms"
                row["candidates"] = [resolved.id, *[p.id for p in forms]]

        rows.append(row)

        if resolved is not None:
            evidence.append(EvidenceItem(
                id=f"resolve.{len(evidence)}", design="classical_text",
                quote=_quote_for(resolved),
                citation=f"TCMScience TCM seed corpus, entry {getattr(resolved,'id','')}",
                identifier=str(getattr(resolved, "id", "")),
                identifier_type="pharmacopoeia",
                source_card_id=SEED_SOURCE_ID, quote_verified=True,
                subject=getattr(resolved, "chinese", raw),
                quality=_quality_for(EvidenceTier.CLASSICAL_TEXT,
                                     assessed_by="normalize-tcm-entities"),
                retrieved_by="normalize-tcm-entities", retrieval_run=run_id))

    ambiguous = [r["query"] for r in rows if r["status"] in ("ambiguous",
                                                             "resolved_with_processed_forms")]
    if ambiguous:
        limitations.append(
            f"ambiguous for {len(ambiguous)} input(s): {ambiguous}; the artifact "
            "returns candidates instead of choosing, because choosing would be "
            "wrong roughly half the time with no signal that it had happened")

    payload = {"queries": rows, "corpus": source.as_dict()}
    out, _ = json_file("entities.json", payload,
                       description="resolved entities and retained ambiguity")

    # A claim is made only when something actually resolved. With no evidence
    # there is nothing to cite, and a claim citing nothing is refused by
    # `validate_artifact` as ART101 — correctly. A run that resolved nothing has
    # an empty result, which is a legitimate answer and not a claim.
    claims: list[CandidateClaim] = []
    if rows and evidence:
        resolved_count = sum(1 for r in rows if r["entity_id"])
        claims.append(CandidateClaim(
            id="normalization.summary",
            text=(f"{resolved_count} of {len(rows)} queried names resolve to an entity "
                  f"in the seed corpus; {len(ambiguous)} carry unresolved ambiguity"),
            claim_kind="attribution", subject="; ".join(r["query"] for r in rows[:5]),
            predicate="recorded_in", object=SEED_SOURCE_ID,
            supports=(evidence[0].id,),
            asserted_population="seed corpus", supported_population="seed corpus",
            asserted_outcome="entity resolution", supported_outcome="entity resolution",
            confidence=1.0,
            confidence_basis="counted directly from the resolution results above",
            falsified_by="a corpus entry that resolves differently from the table above",
            produced_by="normalize-tcm-entities"))
        # An attribution claim over a corpus is one of the few things this skill
        # can license, so it is the one it makes. `require_declared` is the
        # strict door: this skill would rather fail than publish an undeclared
        # extrapolation.
        for claim in claims:
            require_declared(claim)

    return artifact(
        id="normalize-tcm-entities", run_id=run_id,
        skill_id="normalize-tcm-entities", skill_version=SKILL_VERSIONS[
            "normalize-tcm-entities"],
        question=f"resolve {len(names)} TCM name(s) to corpus entities",
        sources=(source,), evidence=evidence, claims=claims, outputs=(out,),
        limitations=limitations,
        assumptions=("the seed corpus is the intended reference for this query",),
        created_at=_now(),
        provenance={"queries": list(names), "output_file": "entities.json"})


def _kind_of(entity: Any) -> str:
    from ...tcm.model import (ActionRelation, ClassicalPassage, Formula, Herb, Ingredient,
                             ProcessedHerb, SafetyRecord, StudyEvidence, Syndrome)
    for cls in (Herb, ProcessedHerb, Formula, Syndrome, ClassicalPassage,
                StudyEvidence, ActionRelation, SafetyRecord):
        if isinstance(entity, cls):
            return cls.__name__
    return type(entity).__name__ if entity is not None else ""


def _quote_for(entity: Any) -> str:
    """A verbatim excerpt supporting the resolution.

    Built from the entity's own fields, not from prose written for the occasion:
    the quote has to be checkable against the corpus entry, and a sentence this
    module composed could not be.
    """
    parts = [f"id={getattr(entity, 'id', '')}"]
    for field in ("chinese", "pinyin", "latin", "source", "method", "category"):
        value = getattr(entity, field, "")
        if value:
            parts.append(f"{field}={value}")
    aliases = getattr(entity, "aliases", ())
    if aliases:
        parts.append(f"aliases={','.join(aliases)}")
    return "; ".join(parts)
