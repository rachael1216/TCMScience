"""`retrieve-tcm-evidence` — find evidence and report it without adjudicating it.

The plan asks this skill for six things: Europe PMC/PubMed/HERB retrieval,
ClinicalTrials.gov/ChiCTR, study-design identification, citation link validation,
a conflict table, and risk-of-bias kept *separate* from certainty of evidence.
The last one is the design commitment. "How likely is this study to be wrong"
and "how sure are we overall" are different questions with different answers,
and the standard failure is to fold them into one grade that then reads as a
verdict. Nothing here produces a verdict.

Three refusals this skill is built around:

**It does not answer the question.** It returns evidence with its scope attached
— population, outcome, design, retraction state — and leaves the inference to a
consumer. A retrieval step that summarised would be making claims nobody could
attribute to a source.

**It does not report "no evidence" as "evidence of no effect".** A search that
found nothing is reported as an empty result set with the search recorded, which
is a different statement and a much weaker one.

**It does not treat an unchecked retraction as a clean record.** The corpus
carries `retracted` and it stays whatever it is; the three-state model
(`not_retracted` / `retracted` / `unverified`) exists precisely so that "nobody
checked" is representable and visible.

Offline by default. The seed corpus is what ships; a connector-backed run would
add `europepmc`/`pubmed` SourceCards, and the shape of the artifact would not
change — which is the point of declaring sources rather than inlining them.
"""

from __future__ import annotations

from typing import Any, Sequence

from ...contracts import (CandidateClaim, EvidenceItem, EvidenceQuality, RiskOfBias,
                          require_declared)
from ...tcm import knowledge as tcm_knowledge
from ...tcm.model import CLAIM_KINDS, EvidenceTier, StudyEvidence
from .common import (SEED_SOURCE_ID, SKILL_VERSIONS, _now, _quality_for, artifact,
                     json_file, seed_source_card)

__all__ = ["retrieve_tcm_evidence"]

#: Design label per evidence tier. The corpus records a tier; the contract layer
#: stores a *design*. `PRECLINICAL` is ambiguous between `animal` and `in_vitro`,
#: and the corpus does not say which, so it maps to the weaker of the two rather
#: than guessing the stronger — declaring an animal study when it might be a cell
#: line would overstate what the evidence licenses.
_DESIGN_FOR_TIER = {
    EvidenceTier.CLASSICAL_TEXT: "classical_text",
    EvidenceTier.EXPERT_EXPERIENCE: "expert_consensus",
    EvidenceTier.PRECLINICAL: "in_vitro",
    EvidenceTier.CASE_REPORT: "case_report",
    EvidenceTier.OBSERVATIONAL: "observational",
    EvidenceTier.RANDOMIZED_TRIAL: "randomized_trial",
    EvidenceTier.SYSTEMATIC_REVIEW: "systematic_review",
}


def retrieve_tcm_evidence(subject: str, *, claim_kind: str = "efficacy",
                          run_id: str = "", max_results: int = 20) -> Any:
    """Gather evidence about ``subject`` and report it with its limits.

    ``max_results`` is applied and *recorded*: a truncated result set that does
    not say it was truncated is indistinguishable from a complete one, and the
    difference decides whether a synthesis is honest.
    """
    kb = tcm_knowledge.default_knowledge()
    source = seed_source_card()

    resolutions = kb.resolve(subject)
    entity = resolutions.entity
    subject_id = getattr(entity, "id", "")

    evidence: list[EvidenceItem] = []
    conflict_rows: list[dict[str, Any]] = []
    coverage: list[str] = []

    # --- study records attached to this subject ---------------------------
    studies: list[StudyEvidence] = []
    for relation in (kb.relations_of(subject_id) if subject_id else []):
        studies.extend(e for e in kb.evidence(relation.evidence_ids)
                       if isinstance(e, StudyEvidence))
    for safety in (kb.safety_for(subject_id) if subject_id else []):
        studies.extend(e for e in kb.evidence(safety.evidence_ids)
                       if isinstance(e, StudyEvidence))
    # Deduplicate by id, preserving order: a study cited by two relations is one
    # study, and counting it twice would inflate the apparent evidence base.
    seen: set[str] = set()
    unique_studies = [s for s in studies if not (s.id in seen or seen.add(s.id))]

    truncated = len(unique_studies) > max_results
    for study in unique_studies[:max_results]:
        evidence.append(_item_from_study(study, run_id=run_id, index=len(evidence)))
    if truncated:
        coverage.append(
            f"result set truncated at {max_results}; {len(unique_studies)} records "
            "were available")

    # --- classical passages that mention the subject ----------------------
    for passage in (kb.passages_mentioning(subject_id) if subject_id else [])[:max_results]:
        evidence.append(EvidenceItem(
            id=f"passage.{passage.id}", design="classical_text", quote=passage.text,
            citation=f"{passage.source}" + (f", {passage.chapter}" if passage.chapter else ""),
            title=f"{passage.source} — {passage.chapter}" if passage.chapter else passage.source,
            identifier=passage.id, identifier_type="classical_passage",
            source_card_id=SEED_SOURCE_ID, quote_verified=True,
            subject=getattr(entity, "chinese", subject),
            outcome="",  # a classical passage states a use, not a measured outcome
            quality=_quality_for(EvidenceTier.CLASSICAL_TEXT,
                                 assessed_by="retrieve-tcm-evidence"),
            retrieved_by="retrieve-tcm-evidence", retrieval_run=run_id))

    # --- conflicting evidence --------------------------------------------
    # The plan asks for a conflict table. Conflict here means two records
    # disagreeing in *direction* about the same subject, which is the only kind
    # this corpus can detect without interpreting free text.
    by_direction: dict[str, list[str]] = {}
    for study in unique_studies:
        key = "positive" if study.effect and not study.effect.startswith(("no ", "无")) \
            else "null_or_negative"
        by_direction.setdefault(key, []).append(study.id)
    if len(by_direction) > 1:
        conflict_rows.append({
            "subject": subject, "kind": "direction_disagreement",
            "positive": by_direction.get("positive", []),
            "null_or_negative": by_direction.get("null_or_negative", []),
            "note": "records point in different directions; no synthesis is offered here",
        })

    # --- citations and their checkability --------------------------------
    unciteable = [e.id for e in evidence if not e.crossref_ready]

    payload = {
        "query": {"subject": subject, "claim_kind": claim_kind,
                  "max_results": max_results},
        "entity": {"id": subject_id, "chinese": getattr(entity, "chinese", "")},
        "evidence": [e.as_dict() for e in evidence],
        "conflicts": conflict_rows,
        "search": {"sources_searched": [SEED_SOURCE_ID], "truncated": truncated,
                   "records_available": len(unique_studies)},
    }
    out, _ = json_file("evidence.json", payload,
                       description="retrieved evidence with scope and limits")

    limitations: list[str] = [
        "only the shipped seed corpus was searched; no live literature index "
        "(Europe PMC, PubMed) or trial registry (ClinicalTrials.gov, ChiCTR) was "
        "queried, so this is not a systematic search",
        "absence of a record in this corpus is not absence of evidence; the corpus "
        "is illustrative and small",
        "risk of bias, precision and consistency are NOT ASSESSED — the corpus "
        "carries no information on which to judge them, and a default of 'low' "
        "would be an invented finding",
    ]
    if unciteable:
        limitations.append(
            f"{len(unciteable)} item(s) have no lookupable identifier and cannot be "
            f"verified from the citation alone: {unciteable[:5]}")
    if truncated:
        limitations.append(
            f"the result set was truncated at {max_results}; this is a partial view")
    if not evidence:
        limitations.append(
            "no evidence was found. This is an empty result set, NOT evidence that "
            "no effect exists — the corpus is small and the search was not systematic")

    # --- claims ------------------------------------------------------------
    claims: list[CandidateClaim] = []
    if evidence:
        strongest = max(evidence, key=lambda e: e.tier)
        # The claim this skill is entitled to make is about *the search*, not
        # about the subject. Claiming anything about efficacy would require a
        # design the corpus does not contain, and `check_claim` would refuse it.
        claims.append(CandidateClaim(
            id="retrieval.coverage",
            text=(f"{len(evidence)} record(s) about {subject} were retrieved from the "
                  f"seed corpus; the strongest design available is "
                  f"{strongest.tier.name.lower()}"),
            claim_kind="attribution", subject=subject, predicate="recorded_in",
            object=SEED_SOURCE_ID, supports=(strongest.id,),
            asserted_population="seed corpus", supported_population="seed corpus",
            asserted_outcome="retrieval coverage", supported_outcome="retrieval coverage",
            confidence=1.0, confidence_basis="counted directly from the search above",
            direction="unclear",
            falsified_by="a corpus record about this subject that the search missed",
            produced_by="retrieve-tcm-evidence"))
        for claim in claims:
            require_declared(claim)

    return artifact(
        id="retrieve-tcm-evidence", run_id=run_id,
        skill_id="retrieve-tcm-evidence",
        skill_version=SKILL_VERSIONS["retrieve-tcm-evidence"],
        question=f"what evidence exists about {subject}?",
        sources=(source,), evidence=evidence, claims=claims, outputs=(out,),
        limitations=limitations,
        assumptions=("the seed corpus is an adequate stand-in for a literature search "
                     "in this configuration; it is not",),
        created_at=_now(),
        provenance={"subject": subject, "claim_kind": claim_kind,
                    "records": len(evidence), "conflicts": len(conflict_rows)})


def _item_from_study(study: StudyEvidence, *, run_id: str, index: int) -> EvidenceItem:
    """One corpus study as an `EvidenceItem`, keeping every caveat it carries."""
    design = _DESIGN_FOR_TIER.get(study.tier, "expert_consensus")
    quote = _study_quote(study)

    # A retracted study is marked as such; the corpus is the authority on
    # retraction, and this skill does not launder it into `unverified`.
    retracted = "retracted" if getattr(study, "retracted", False) else "not_retracted"

    identifier, identifier_type = _identifier_of(study)

    return EvidenceItem(
        id=f"study.{study.id}", design=design, quote=quote,
        citation=study.citation or study.id,
        title=getattr(study, "condition", "") or study.id,
        identifier=identifier, identifier_type=identifier_type,
        source_card_id=SEED_SOURCE_ID, quote_verified=True,
        quality=_quality_for(study.tier, assessed_by="retrieve-tcm-evidence"),
        subject=study.subject_id, population=getattr(study, "population", ""),
        condition=getattr(study, "condition", ""),
        comparator=getattr(study, "comparator", ""),
        outcome=getattr(study, "outcome", ""), effect=getattr(study, "effect", ""),
        sample_size=int(getattr(study, "sample_size", 0) or 0),
        year=int(getattr(study, "year", 0) or 0),
        retracted=retracted,
        retrieved_by="retrieve-tcm-evidence", retrieval_run=run_id,
        notes=f"corpus tier {study.tier.name.lower()}; design inferred as {design}")


def _identifier_of(study: StudyEvidence) -> tuple[str, str]:
    """The best identifier the record carries, with its scheme."""
    for attr, kind in (("pmid", "pmid"), ("doi", "doi"), ("registry_id", "nct")):
        value = getattr(study, attr, "")
        if value:
            return str(value), kind
    return study.id, "pharmacopoeia"


def _study_quote(study: StudyEvidence) -> str:
    """A verbatim rendering of the record's own fields.

    Assembled from the stored fields so it is checkable against the corpus entry.
    Prose composed for the occasion could not be verified, and the contract
    requires a quote that can be located in its source.
    """
    parts = []
    for field in ("design", "condition", "population", "comparator", "outcome",
                  "effect"):
        value = getattr(study, field, "")
        if value:
            parts.append(f"{field}={value}")
    if getattr(study, "sample_size", 0):
        parts.append(f"sample_size={study.sample_size}")
    return "; ".join(parts) or f"id={study.id}"
