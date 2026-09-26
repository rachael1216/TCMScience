"""SableAdapter: composes the sable harness without merging its source.

sable stays exactly as it was — its own package, its own 144-test suite, its own release
cycle. This adapter reaches it through its public API only (``sable.evidence``,
``sable.study``, ``sable.governance``), so a sable upgrade cannot silently break psh's
internals and psh cannot silently break sable's guarantees.

What sable contributes, and why it is worth composing rather than rewriting:

* a PubMed / ClinicalTrials.gov / Crossref client set that needs no API key
* evidence grading that returns UNDETERMINED rather than guessing a study design
* retraction detection verified against real retracted records
* an FSRS scheduler validated against seven published mathematical invariants
* a PHI detector at recall 1.00 / specificity 1.00 on its published corpus

What psh adds on top, which sable lacked:

* the retrieved abstract now feeds ``ClaimSupport``, so a citation is checked for whether it
  *supports* the sentence — the hole sable's own design could not close
* every outbound call, including model calls, passes the kernel's egress gateways
* results land in the WorkGraph, so they outlive the session

Import is lazy and failure is explicit: if sable is absent, ``available`` is False and the
adapter reports that through ``health()`` rather than raising at import time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts import (
    ArtifactRef, CapabilityUnavailable, ComponentKind, ComponentManifest, RiskTier,
    RunEnvelope, content_hash, new_id,
)
from ..evidence.support import ClaimSupport, ClaimSupportVerifier, Evidence
from ..labels import DataLabel, Destination, Sensitivity
from .harness_adapter import (
    EvidenceBundle, ExecutionPlan, HarnessManifest, HealthReport, ProvenanceCapsule,
    RunHandle, RunStatus,
)

__all__ = ["SableAdapter", "SABLE_AVAILABLE"]

try:  # pragma: no cover - depends on the environment
    import sable as _sable
    from sable.evidence import (
        ClaimLedger as _ClaimLedger, ClinicalTrialsClient as _CTClient,
        PubMedClient as _PubMedClient, grade_pubmed as _grade_pubmed,
        grade_trial as _grade_trial,
    )
    SABLE_AVAILABLE = True
except Exception:  # pragma: no cover
    _sable = None  # type: ignore[assignment]
    SABLE_AVAILABLE = False


#: Level-2 capabilities. Declared here rather than introspected, because a manifest is a
#: contract: it states risk, cost and destination, which no amount of reflection can infer.
_CAPABILITIES: tuple[dict[str, Any], ...] = (
    dict(id="sable.pubmed_search", name="PubMed search",
         description="Search PubMed and return records with derived evidence tiers.",
         intents=("literature search", "pubmed", "find papers", "evidence"),
         destinations=(Destination.PUBLIC_REMOTE,), requires_network=True,
         max_label=Sensitivity.RESEARCH_DEIDENTIFIED, expected_latency_s=2.5),
    dict(id="sable.pubmed_fetch", name="PubMed fetch",
         description="Fetch specific PMIDs with abstracts, journal, year and publication types.",
         intents=("fetch abstract", "pmid", "retrieve paper"),
         destinations=(Destination.PUBLIC_REMOTE,), requires_network=True,
         max_label=Sensitivity.RESEARCH_DEIDENTIFIED, expected_latency_s=2.0),
    dict(id="sable.check_retraction", name="Retraction check",
         description="Check whether PMIDs are retracted or carry an expression of concern.",
         intents=("retraction", "verify citation", "is it retracted"),
         destinations=(Destination.PUBLIC_REMOTE,), requires_network=True,
         max_label=Sensitivity.PUBLIC, expected_latency_s=2.0),
    dict(id="sable.trial_search", name="Trial registry search",
         description="Search ClinicalTrials.gov with phase, allocation and enrollment.",
         intents=("clinical trial", "nct", "registry"),
         destinations=(Destination.PUBLIC_REMOTE,), requires_network=True,
         max_label=Sensitivity.RESEARCH_DEIDENTIFIED, expected_latency_s=3.0),
    dict(id="sable.grade_evidence", name="Evidence grading",
         description="Derive study design and evidence tier from indexed metadata, "
                     "returning UNDETERMINED when metadata is insufficient.",
         intents=("evidence tier", "study design", "grade"),
         destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI,
         expected_latency_s=0.01),
    dict(id="sable.study_schedule", name="FSRS scheduling",
         description="Schedule spaced-repetition review of what was read.",
         intents=("spaced repetition", "study", "review", "retention"),
         destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI,
         mutates=True, expected_latency_s=0.01),
    dict(id="sable.phi_scan", name="PHI scan",
         description="Scan text locally for HIPAA identifiers; nothing leaves the machine.",
         intents=("phi", "deidentify", "scan for identifiers"),
         destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI,
         expected_latency_s=0.01),
)


class SableAdapter:
    """Adapter over the sable biomedical/clinical harness."""

    HARNESS_ID = "sable-clinical-evidence-harness"

    def __init__(self, *, contact_email: str | None = None,
                 verifier: ClaimSupportVerifier | None = None,
                 signer: Any | None = None) -> None:
        self.available = SABLE_AVAILABLE
        self.contact_email = contact_email
        self.verifier = verifier or ClaimSupportVerifier()
        #: The kernel's EvidenceSigner, handed to this adapter at registration. It is what
        #: makes records from this adapter *trusted*: not the adapter's name, the signature.
        self.signer = signer
        self._runs: dict[str, dict[str, Any]] = {}
        self._pubmed = _PubMedClient(email=contact_email) if SABLE_AVAILABLE else None
        self._trials = _CTClient() if SABLE_AVAILABLE else None
        self._ledger = _ClaimLedger() if SABLE_AVAILABLE else None

    # -------------------------------------------------------------- description
    def describe(self) -> HarnessManifest:
        return HarnessManifest(
            id=self.HARNESS_ID, name="sable clinical-evidence harness",
            version=getattr(_sable, "__version__", "unavailable"),
            description=("Literature and trial retrieval with provenance-bound claims, "
                         "evidence grading, retraction checking, PHI detection and FSRS "
                         "study scheduling."),
            domains=("clinical evidence", "literature", "biomedical", "study"),
            capability_count=len(_CAPABILITIES),
            max_label=Sensitivity.PHI,
            destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
            supports_evidence=True, supports_checkpoint=False, supports_provenance=True,
            license="MIT")

    def discover(self, query: str, *, limit: int = 20) -> Sequence[ComponentManifest]:
        """Level-2 retrieval within this harness."""
        terms = {w for w in query.lower().split() if len(w) > 2}
        scored: list[tuple[float, ComponentManifest]] = []
        for spec in _CAPABILITIES:
            manifest = self._manifest(spec)
            haystack = " ".join((manifest.name, manifest.description,
                                 " ".join(manifest.intents))).lower()
            hits = sum(1 for t in terms if t in haystack)
            intent_hit = any(i in query.lower() for i in manifest.intents)
            score = hits + (2.0 if intent_hit else 0.0)
            if score > 0 or not terms:
                scored.append((score, manifest))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [m for _, m in scored[:limit]]

    def capabilities(self) -> list[ComponentManifest]:
        return [self._manifest(spec) for spec in _CAPABILITIES]

    def _manifest(self, spec: Mapping[str, Any]) -> ComponentManifest:
        return ComponentManifest(
            kind=ComponentKind.TOOL, version=getattr(_sable, "__version__", "0"),
            publisher="sable", domain="clinical evidence",
            risk_tier=RiskTier.R1_ROUTINE, success_rate=0.95,
            **{k: v for k, v in spec.items()})

    # ------------------------------------------------------------------ execute
    def prepare(self, capability_id: str, payload: Mapping[str, Any],
                envelope: RunEnvelope) -> ExecutionPlan:
        manifest = next((m for m in self.capabilities() if m.id == capability_id), None)
        if manifest is None:
            raise CapabilityUnavailable(
                f"{capability_id!r} is not provided by {self.HARNESS_ID}")
        ok, why = manifest.compatible_with(envelope)
        if not ok:
            raise CapabilityUnavailable(f"{capability_id} refused: {why}")
        return ExecutionPlan(
            harness_id=self.HARNESS_ID, capability_id=capability_id, payload=dict(payload),
            estimated_tokens=manifest.expected_tokens, estimated_usd=manifest.expected_usd,
            reaches=manifest.destinations, requires_approval=manifest.human_approval)

    def execute(self, plan: ExecutionPlan, envelope: RunEnvelope) -> RunHandle:
        """Execute a capability. Network calls happen only for capabilities that declare them."""
        if not self.available:
            raise CapabilityUnavailable(
                "sable is not installed; install it to use this harness "
                "(pip install -e sable_pkg)")
        run_id = new_id("shr")
        started = time.time()
        try:
            output = self._dispatch(plan)
            self._runs[run_id] = {
                "state": "complete", "output": output, "plan": plan,
                "envelope": envelope, "seconds": time.time() - started}
        except Exception as exc:
            self._runs[run_id] = {
                "state": "failed", "error": f"{type(exc).__name__}: {exc}", "plan": plan,
                "envelope": envelope, "seconds": time.time() - started}
        return RunHandle(run_id=run_id, harness_id=self.HARNESS_ID)

    def _dispatch(self, plan: ExecutionPlan) -> Any:
        cap, payload = plan.capability_id, dict(plan.payload)

        if cap == "sable.pubmed_search":
            pmids = self._pubmed.search(payload["query"],
                                        retmax=int(payload.get("max_results", 5)))
            records = self._pubmed.fetch(pmids)
            return {"pmids": pmids, "records": {
                p: {"title": r.title, "journal": r.journal, "year": r.year,
                    "abstract": getattr(r, "abstract", "") or "",
                    "publication_types": list(r.publication_types),
                    "tier": _grade_pubmed(r).evidence_tier.value,
                    "tier_rationale": _grade_pubmed(r).rationale,
                    "retracted": r.retracted}
                for p, r in records.items()}}

        if cap == "sable.pubmed_fetch":
            records = self._pubmed.fetch(list(payload["pmids"]))
            return {p: {"title": r.title, "journal": r.journal, "year": r.year,
                        "abstract": getattr(r, "abstract", "") or "",
                        "tier": _grade_pubmed(r).evidence_tier.value,
                        "retracted": r.retracted}
                    for p, r in records.items()}

        if cap == "sable.check_retraction":
            return self._pubmed.retraction_status(list(payload["pmids"]))

        if cap == "sable.trial_search":
            trials = self._trials.search(
                condition=payload.get("condition", ""),
                intervention=payload.get("intervention", ""),
                max_results=int(payload.get("max_results", 5)))
            return [{"nct_id": t.nct_id, "title": t.title,
                     "study_type": t.study_type, "allocation": t.allocation,
                     "phases": list(t.phases), "enrollment": t.enrollment,
                     "tier": _grade_trial(t).evidence_tier.value} for t in trials]

        if cap == "sable.grade_evidence":
            records = self._pubmed.fetch(list(payload["pmids"]))
            return {p: {"tier": _grade_pubmed(r).evidence_tier.value,
                        "design": _grade_pubmed(r).study_design.value,
                        "confident": _grade_pubmed(r).confident,
                        "rationale": _grade_pubmed(r).rationale}
                    for p, r in records.items()}

        if cap == "sable.phi_scan":
            from sable.governance import PHIDetector
            report = PHIDetector().scan(payload["text"])
            return {"level": report.level.name, "categories": report.categories(),
                    "clean": report.clean,
                    "findings": [{"category": f.category.value, "start": f.span_start,
                                  "end": f.span_end} for f in report.findings]}

        if cap == "sable.study_schedule":
            from sable.study import CardStore, FSRS, Rating
            store = CardStore(payload["db_path"])
            scheduler = FSRS()
            card = store.add(payload["front"], payload["back"],
                             source_identifier=payload.get("identifier", ""),
                             source_type=payload.get("id_type", "pmid"))
            if "rating" in payload:
                card, log = scheduler.review(card, Rating(int(payload["rating"])))
                store.save(card); store.log(log)
            return {"card_id": card.card_id, "due_in_days": card.scheduled_days,
                    "stability": card.stability, "difficulty": card.difficulty}

        raise CapabilityUnavailable(f"{cap!r} has no dispatch implementation")

    # ------------------------------------------------------------------ queries
    def status(self, run_id: str) -> RunStatus:
        record = self._runs.get(run_id)
        if record is None:
            return RunStatus(run_id=run_id, state="failed", error="unknown run id")
        return RunStatus(run_id=run_id, state=record["state"],
                         output=record.get("output"), error=record.get("error", ""),
                         detail=f"{record.get('seconds', 0):.3f}s")

    def cancel(self, run_id: str) -> bool:
        """sable capabilities are synchronous, so there is nothing to cancel."""
        return False

    def checkpoint(self, run_id: str) -> str:
        raise NotImplementedError(
            "sable checkpoints its own agent loop, but this adapter exposes stateless "
            "capability calls; supports_checkpoint is False on the manifest")

    def resume(self, checkpoint_ref: str) -> RunHandle:
        raise NotImplementedError("see checkpoint()")

    def artifacts(self, run_id: str) -> Sequence[ArtifactRef]:
        return ()

    def evidence(self, run_id: str) -> EvidenceBundle:
        """Return retrieved sources as Evidence, with abstracts for support checking.

        This is where the composition earns its keep: sable retrieves the abstract, and psh
        uses it to answer the question sable could not — whether the source supports the
        claim.
        """
        record = self._runs.get(run_id)
        if record is None or record["state"] != "complete":
            return EvidenceBundle(run_id=run_id)
        output = record["output"]
        evidence: list[Evidence] = []

        records = output.get("records", output) if isinstance(output, Mapping) else {}
        if isinstance(records, Mapping):
            for identifier, meta in records.items():
                if not isinstance(meta, Mapping):
                    continue
                evidence.append(Evidence(
                    identifier=str(identifier), identifier_type="pmid",
                    content=str(meta.get("abstract", "")),
                    title=str(meta.get("title", "")),
                    quality=str(meta.get("tier", "undetermined")),
                    retracted=meta.get("retracted"),
                    label=DataLabel(Sensitivity.PUBLIC, shareable=True,
                                    rationale="published literature")))
        return EvidenceBundle(run_id=run_id, evidence=tuple(evidence))

    def signed_records(self, run_id: str) -> dict[str, Any]:
        """Return retrieved sources as signed EvidenceRecords, keyed by identifier.

        This is the trusted-provenance path. Each record is signed by the kernel's signer,
        so downstream verification can prove it came through this registered capability
        rather than from a caller who typed ``retrieved_by="sable.pubmed_fetch"``. Without a
        signer the records are returned unsigned and therefore untrusted — the adapter does
        not get to assert trust for itself.
        """
        from ..evidence.record import EvidenceRecord

        bundle = self.evidence(run_id)
        out: dict[str, Any] = {}
        for ev in bundle.evidence:
            if not ev.content:
                continue
            record = EvidenceRecord.from_text(
                identifier=ev.identifier, text=ev.content,
                retrieved_by=f"{self.HARNESS_ID}.pubmed_fetch", retrieval_run=run_id,
                source_type=ev.identifier_type, retracted=ev.retracted)
            if self.signer is not None:
                record = self.signer.sign(record)
            out[ev.identifier] = record
        return out

    def provenance(self, run_id: str) -> ProvenanceCapsule:
        record = self._runs.get(run_id, {})
        plan: ExecutionPlan | None = record.get("plan")
        return ProvenanceCapsule(
            run_id=run_id, harness_id=self.HARNESS_ID,
            # Literature retrieval is only provenance-equivalent: PubMed's index changes,
            # so the same query need not return the same set tomorrow. Claiming 'exact'
            # here would be false.
            determinism="provenance_equivalent",
            component_versions={"sable": getattr(_sable, "__version__", "unavailable")},
            prompt_hash=content_hash(dict(plan.payload)) if plan else "",
            policy_version="1")

    def health(self) -> HealthReport:
        if not self.available:
            return HealthReport(harness_id=self.HARNESS_ID, ok=False,
                                detail="sable is not importable in this environment")
        return HealthReport(
            harness_id=self.HARNESS_ID, ok=True,
            detail=(f"sable {getattr(_sable, '__version__', '?')}, "
                    f"{len(_CAPABILITIES)} capabilities"))
