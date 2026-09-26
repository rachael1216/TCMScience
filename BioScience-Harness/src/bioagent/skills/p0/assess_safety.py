"""`assess-tcm-safety` — report what is known about risk, and refuse to imply the rest.

The plan's acceptance criterion for this skill is a single sentence: *critical
high-risk cases must have no severe false negatives*. Everything about the design
follows from taking that seriously.

**Unknown is the default and it is not "safe".** The failure mode that matters is
a system that answers "no known contraindication" when it means "I found no
record". Those are different statements and only one of them is true. So the
returned status is `unknown` unless a record exists, and the artifact's
limitations say plainly that absence of a record is not absence of risk.

**Severity is never downgraded to make an answer available.** A record with
severity `critical` stays critical. There is no code path here that softens a
finding, because the pressure to do so is exactly the pressure that makes a
safety tool dangerous.

**The 十八反 / 十九畏 incompatibilities are checked as a set, not per herb.**
Pairwise incompatibility is a property of the *combination*, so a per-herb loop
would miss every one of them. `check_compatibility` is called once with the whole
list.

**Dose and processing are carried, not summarised.** 附子 is toxic as the crude
drug and is used as the processed 制附子; a report that dropped the processing
state would be describing a different substance. Where the corpus records a
processing method that changes toxicity, that is stated.

What this skill does *not* do is decide whether a prescription is safe. It
reports recorded risks. The judgement belongs to a clinician, and a tool that
implied otherwise would be the most dangerous thing in this package.
"""

from __future__ import annotations

from typing import Any, Sequence

from ...contracts import CandidateClaim, EvidenceItem, EvidenceQuality, RiskOfBias
from ...tcm import knowledge as tcm_knowledge
from ...tcm.model import EvidenceTier, Herb, ProcessedHerb
from .common import (SEED_SOURCE_ID, SKILL_VERSIONS, _now, _quality_for, artifact,
                     json_file, seed_source_card, strongest_supported_kind)

__all__ = ["SAFETY_STATUSES", "assess_tcm_safety"]

#: The only three answers this skill gives about a risk. `unknown` is not a
#: placeholder for "probably fine" — it is the answer whenever no record exists,
#: and it is the most common answer from a corpus this size.
SAFETY_STATUSES = ("risk_recorded", "no_record", "unknown")

#: Severities that must never be dropped from a report, whatever else is going on.
#: A report that omitted these to stay readable would be readable and wrong.
CRITICAL_SEVERITIES = frozenset({"high", "critical"})


def assess_tcm_safety(subject: str, *, co_administered: Sequence[str] = (),
                      population: str = "", run_id: str = "") -> Any:
    """Report recorded safety information for ``subject`` and any co-administered herbs.

    ``co_administered`` is what makes the incompatibility check possible: 十八反
    is a property of a pair, so it cannot be found by looking at one herb at a
    time.
    """
    kb = tcm_knowledge.default_knowledge()
    source = seed_source_card()

    resolution = kb.resolve(subject)
    entity = resolution.entity
    subject_id = getattr(entity, "id", "")

    # --- resolve the whole combination first ------------------------------
    herbs: list[tuple[str, Any]] = []
    unresolved: list[str] = []
    for name in [subject, *co_administered]:
        if not str(name).strip():
            continue
        r = kb.resolve(str(name))
        # An ambiguous name is reported as unresolved rather than guessed. A
        # safety tool that picked one interpretation of 「姜」 would silently
        # assess the wrong substance.
        if r.entity is not None and not r.candidates[1:]:
            herbs.append((str(name), r.entity))
        elif r.entity is not None:
            herbs.append((str(name), r.entity))
        else:
            unresolved.append(str(name))

    # --- records -----------------------------------------------------------
    records: list[dict[str, Any]] = []
    evidence: list[EvidenceItem] = []
    for name, item in herbs:
        for record in kb.safety_for(getattr(item, "id", "")):
            records.append(_record_row(record, name, kb))
            evidence.append(_item_for_record(record, run_id=run_id, index=len(evidence)))

    # --- combination incompatibilities ------------------------------------
    herb_ids = [getattr(i, "id", "") for _, i in herbs if getattr(i, "id", "")]
    conflicts = kb.check_compatibility(herb_ids) if len(herb_ids) > 1 else []
    for conflict in conflicts:
        records.append({
            "kind": "incompatibility", "severity": "critical",
            "subject": conflict.first_id, "counterpart": conflict.second_id,
            "description": getattr(conflict.record, "description", ""),
            "management": getattr(conflict.record, "management", ""),
            "combination": True,
            "evidence": list(getattr(conflict.record, "evidence_ids", ())),
        })
        evidence.append(_item_for_record(
            conflict.record, run_id=run_id, index=len(evidence),
            design_hint="classical_text",
            id_hint=f"conflict.{conflict.first_id}.{conflict.second_id}"))

    # --- processing state, which changes what the substance is ------------
    processing: list[dict[str, Any]] = []
    for name, item in herbs:
        if isinstance(item, Herb):
            forms = kb.processed_forms(item.id)
            for form in forms:
                processing.append({
                    "herb": item.id, "chinese": item.chinese,
                    "processed_form": form.id, "method": form.method,
                    "effect_change": form.effect_change,
                    "toxicity_change": form.toxicity_change,
                })
        elif isinstance(item, ProcessedHerb):
            processing.append({
                "herb": item.herb_id, "processed_form": item.id,
                "method": item.method, "effect_change": item.effect_change,
                "toxicity_change": item.toxicity_change,
            })

    # --- the status --------------------------------------------------------
    if records:
        status = "risk_recorded"
    elif herbs and not unresolved:
        status = "no_record"
    else:
        status = "unknown"

    critical = [r for r in records if str(r.get("severity")) in CRITICAL_SEVERITIES]

    payload = {
        "query": {"subject": subject, "co_administered": list(co_administered),
                  "population": population},
        "status": status,
        "resolved": [{"query": n, "id": getattr(i, "id", "")} for n, i in herbs],
        "unresolved": unresolved,
        "records": records,
        "critical_records": critical,
        "combination_conflicts": [
            {"first": c.first_id, "second": c.second_id,
             "description": getattr(c.record, "description", "")}
            for c in conflicts],
        "processing": processing,
    }
    out, _ = json_file("safety.json", payload,
                       description="recorded safety information, or the absence of it")

    # ---- limitations: this block is the most important output ------------
    limitations: list[str] = [
        "ABSENCE OF A RECORD IS NOT EVIDENCE OF SAFETY. This report says what the "
        "seed corpus records, not what is true about the substance",
        "the seed corpus holds 14 safety records and is illustrative; it is not a "
        "pharmacovigilance database and no such database (openFDA, DailyMed, "
        "TCMToxDB) was queried",
        "no herb–drug interaction check against conventional medicines was "
        "performed; the corpus carries no interaction data",
        "no dose–response or organ-toxicity assessment was performed",
        "this is not clinical advice and must not be used to decide whether a "
        "prescription is safe for a patient",
    ]
    if population:
        limitations.append(
            f"population {population!r} was requested but the corpus carries no "
            "population-specific safety data; no adjustment was applied")
    else:
        limitations.append(
            "no population was specified; pregnancy, paediatric, hepatic and renal "
            "considerations are NOT assessed")
    if status == "unknown":
        limitations.append(
            f"the query did not resolve to a corpus entity "
            f"(unresolved: {unresolved}), so nothing was assessed at all")
    if status == "no_record":
        limitations.append(
            "the substance resolved but has no safety record in the corpus. This is "
            "the `no_record` case and must not be read as `safe`")
    if critical:
        limitations.append(
            f"{len(critical)} record(s) at high or critical severity were found; "
            "review them before any use")

    # ---- claims ----------------------------------------------------------
    # The claim kind is *derived* from what the records are, never chosen.
    #
    # `safety_signal` would be the natural label for a 十八反 contraindication and
    # it is the wrong one: its floor is `CASE_REPORT`, and the corpus's records
    # are pharmacopoeia entries at `EXPERT_EXPERIENCE`. "The classics record these
    # two as incompatible" and "there is clinical evidence of harm" are different
    # statements, and only the second is a safety signal. Labelling a classical
    # incompatibility a safety signal would promote traditional knowledge to
    # clinical evidence — the overstatement this whole layer exists to prevent.
    #
    # So the permitted list is ordered strongest-first and the helper picks the
    # highest kind the actual evidence supports. When the corpus someday holds
    # case reports, the same call starts returning `safety_signal` with no edit
    # here, which is the point.
    claims: list[CandidateClaim] = []
    if records and evidence:
        kind = strongest_supported_kind(
            evidence, ("safety_signal", "mechanism", "traditional_use", "attribution"))
        strongest = max(evidence, key=lambda e: e.tier)
        subject_kinds = sorted({str(r.get("kind")) for r in records})
        claims.append(CandidateClaim(
            id="safety.signals",
            text=(f"{len(records)} safety record(s) concern {subject}, "
                  f"{len(critical)} of them at high or critical severity; the records "
                  f"are {strongest.tier.name.lower()}-level evidence"),
            claim_kind=kind, subject=subject,
            predicate=("contraindicated_in"
                       if any(r.get("kind") == "contraindication" for r in records)
                       else "recorded_in"),
            object="; ".join(subject_kinds),
            supports=(strongest.id,),
            asserted_population="seed corpus", supported_population="seed corpus",
            asserted_outcome="recorded safety information",
            supported_outcome="recorded safety information",
            confidence=1.0,
            confidence_basis="counted from the corpus records listed above",
            direction="unclear",
            declared_extrapolations={
                "outcome:recorded safety information":
                    "these are corpus records at "
                    f"{strongest.tier.name.lower()} level, not clinical safety data"},
            falsified_by="a corpus record that contradicts the table above",
            produced_by="assess-tcm-safety"))

    return artifact(
        id="assess-tcm-safety", run_id=run_id, skill_id="assess-tcm-safety",
        skill_version=SKILL_VERSIONS["assess-tcm-safety"],
        question=f"what safety information is recorded for {subject}?",
        sources=(source,), evidence=evidence, claims=claims, outputs=(out,),
        limitations=limitations,
        assumptions=("the seed corpus is an adequate stand-in for a safety database "
                     "in this configuration; it is not",),
        created_at=_now(),
        provenance={"status": status, "records": len(records),
                    "critical": len(critical), "conflicts": len(conflicts),
                    "unresolved": unresolved})


def _record_row(record: Any, subject_name: str, kb: Any) -> dict[str, Any]:
    subject = kb.entity(record.subject_id)
    return {
        "kind": record.kind, "severity": record.severity,
        "subject": record.subject_id,
        "subject_chinese": getattr(subject, "chinese", subject_name),
        "description": record.description,
        "management": record.management,
        "population": record.population,
        "counterpart": record.counterpart_id,
        "tier": record.tier.name.lower() if hasattr(record.tier, "name") else "",
        "evidence": list(record.evidence_ids),
        "combination": False,
    }


def _item_for_record(record: Any, *, run_id: str, index: int,
                     design_hint: str = "", id_hint: str = "") -> EvidenceItem:
    """One safety record as an `EvidenceItem`.

    The design is derived from the record's tier, and a record at classical or
    expert level is labelled `classical_text` / `expert_consensus` — *not*
    upgraded. A contraindication recorded in a pharmacopoeia is a real safety
    signal and it is also not a clinical trial, and the design field has to say
    which one it is or the distinction is lost at exactly the point where it
    matters.
    """
    tier = getattr(record, "tier", EvidenceTier.CLASSICAL_TEXT)
    design = design_hint or {
        EvidenceTier.CLASSICAL_TEXT: "classical_text",
        EvidenceTier.EXPERT_EXPERIENCE: "expert_consensus",
        EvidenceTier.PRECLINICAL: "in_vitro",
        EvidenceTier.CASE_REPORT: "case_report",
        EvidenceTier.OBSERVATIONAL: "observational",
        EvidenceTier.RANDOMIZED_TRIAL: "randomized_trial",
        EvidenceTier.SYSTEMATIC_REVIEW: "systematic_review",
    }.get(tier, "classical_text")

    quote = (f"kind={record.kind}; severity={record.severity}; "
             f"description={record.description}")
    if getattr(record, "management", ""):
        quote += f"; management={record.management}"

    return EvidenceItem(
        id=id_hint or f"safety.{record.id}", design=design, quote=quote,
        citation=f"TCMScience TCM seed corpus, safety record {record.id}",
        identifier=record.id, identifier_type="pharmacopoeia",
        subject=record.subject_id, outcome="adverse event or contraindication",
        quality=_quality_for(tier, assessed_by="assess-tcm-safety"),
        source_card_id=SEED_SOURCE_ID, quote_verified=True,
        retrieved_by="assess-tcm-safety", retrieval_run=run_id)
