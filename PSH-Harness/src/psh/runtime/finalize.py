"""One release path for everything a run hands back.

``Runner.run`` held its candidate output in quarantine, verified its cited claims,
put it through the output gate and only then committed verified claims to project
memory. The agent loop did none of that: ``LoopResult.results`` was the raw graph, and a
reviewer showed the loop returning "Drug X cures every cancer." as ``goal_satisfied``
under a policy requiring citations and claim support, with the gate never consulted —
while the same sentence through ``Runner`` was refused at ``release_gate``. The same
policy, two entrances, two outcomes.

This module is the one entrance. ``LoopResult`` is an *internal* object — a parent loop,
a supervisor or a resume path may read it — and ``ReleasedResult`` is what leaves the
system: only ``released_output`` carries text, and only after the gate has ruled. A
refused result carries counts and a category, never the sentence that was refused.

The evidence helpers below are shared with ``Runner`` rather than duplicated: the single
pass and the loop verify claims with the same code, so the two cannot drift apart again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts import (
    ApprovalRequired, EgressDenied, PolicyDenied, RunEnvelope, VerificationFailed,
)
from ..evidence.support import ClaimSupport
from ..labels import DataLabel, Destination, Labeled, combine, unwrap
from ..workgraph import EdgeKind, NodeKind
from .loop import LoopResult, Termination

__all__ = ["ReleasedResult", "Finalizer", "render_deliverable", "ingest_evidence",
           "verify_claims", "commit_claims"]


@dataclass(frozen=True, slots=True)
class ReleasedResult:
    """What an application may show. No field but ``released_output`` holds run text."""

    status: str                                   # released | refused | not_completed
    run_id: str
    loop_id: str = ""
    released_output: str | None = None
    label: DataLabel = field(default_factory=DataLabel)
    termination: str = ""
    goal_status: str = "unverified"
    refused_at: str = ""                          # execution | goal_verification | release_gate
    refusal_kind: str = ""                        # the exception class, never its text
    citations: tuple[str, ...] = ()
    claims_checked: int = 0
    claims_unsupported: int = 0
    claims_uncited: int = 0
    committed_claims: int = 0
    limitations: tuple[str, ...] = ()
    quarantine_ref: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "released"

    def summary(self) -> str:
        head = f"{self.status} ({self.termination or 'n/a'}, goal {self.goal_status})"
        if self.status != "released":
            head += f" at {self.refused_at}: {self.refusal_kind}"
        return head + (f"; {len(self.limitations)} limitation(s)" if self.limitations else "")


# ------------------------------------------------------------- shared helpers

def ingest_evidence(kernel: Any, sources: Mapping[str, Any] | None,
                    envelope: RunEnvelope) -> dict[str, Any]:
    """Turn supplied sources into EvidenceRecords, preserving provenance.

    A caller may pass either a record (retrieved through a trusted capability) or bare
    text. Bare text is accepted but marked untrusted, so it cannot on its own establish
    support — the fabricated-abstract path a reviewer identified.
    """
    from ..evidence.record import EvidenceRecord, revalidate_trust

    records: dict[str, Any] = {}
    for identifier, source in (sources or {}).items():
        if isinstance(source, EvidenceRecord):
            # A record's `trusted` flag is data the caller set. Recompute it from the
            # signature: an unsigned or tampered record enters the run untrusted no
            # matter what its flag says.
            records[identifier] = revalidate_trust(source, kernel.evidence_signer)
            continue
        records[identifier] = EvidenceRecord.from_text(
            identifier=identifier, text=str(source), retrieved_by="caller_supplied",
            retrieval_run=envelope.run_id, trusted=False, retracted=None)
    return records


def verify_claims(kernel: Any, output: str, records: Mapping[str, Any]) -> list[ClaimSupport]:
    """Verify every cited clinical claim against its record. Commits nothing."""
    gate = kernel.output_gate
    supports: list[ClaimSupport] = []
    for sentence in gate._sentences(output):
        if not gate.is_clinical(sentence):
            continue
        identifiers = gate._identifiers(sentence)
        statement = gate._strip_citations(sentence)
        for identifier in identifiers:
            record = records.get(identifier)
            if record is None:
                supports.append(kernel.verifier.verify(
                    statement=statement, identifier=identifier, source_text=None))
                continue
            supports.append(kernel.verifier.verify_record(statement=statement, record=record))
    return supports


#: Support relationship -> WorkGraph edge, shared with the runner.
_EDGE_FOR = {
    "supports": EdgeKind.SUPPORTS, "partially_supports": EdgeKind.PARTIALLY_SUPPORTS,
    "contradicts": EdgeKind.CONTRADICTS, "unresolved": EdgeKind.UNRESOLVED,
}


def commit_claims(kernel: Any, graph: Any, supports: Sequence[ClaimSupport], *,
                  project_id: str, run_node_id: str, envelope: RunEnvelope) -> int:
    """Commit verified claims to project memory. Runs only after a release has passed."""
    committed = 0
    for support in supports:
        if not support.supports:
            continue
        claim_node = kernel.persistence.commit_node(
            kind=NodeKind.CLAIM, title=support.claim[:140],
            principal=envelope.principal.id, source_run=envelope.run_id,
            project_id=project_id, validation_status="verified",
            max_label=envelope.max_label.sensitivity)
        evidence_node = kernel.persistence.commit_node(
            kind=NodeKind.EVIDENCE, title=support.identifier,
            body=support.evidence_span[:200], principal=envelope.principal.id,
            source_run=envelope.run_id, project_id=project_id,
            ref=support.identifier, validation_status="verified",
            max_label=envelope.max_label.sensitivity,
            relationship=support.relationship.value, confidence=support.confidence,
            span_start=support.span_start, span_end=support.span_end)
        graph.link(run_node_id, evidence_node, EdgeKind.YIELDED)
        graph.link(evidence_node, claim_node,
                   _EDGE_FOR.get(support.edge_kind, EdgeKind.UNRESOLVED),
                   note=support.rationale[:160])
        committed += 1
    return committed


def render_deliverable(result: LoopResult) -> str:
    """The text a loop hands back: the results of its terminal tasks, in plan order.

    Terminal tasks are the ones nothing depends on — the plan's outputs. Intermediate
    results stay internal; a parent that needs them reads the ``LoopResult``.
    """
    plan = result.plan
    order = [t.task_id for t in plan.tasks] if plan is not None else list(result.results)
    depended_on = {d for t in (plan.tasks if plan is not None else ()) for d in t.dependencies}
    terminal = [tid for tid in order if tid not in depended_on and tid in result.results] \
        or [tid for tid in order if tid in result.results]
    parts = []
    for task_id in terminal:
        value = unwrap(result.results[task_id])
        parts.append(value if isinstance(value, str)
                     else json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))
    return "\n\n".join(parts)


# -------------------------------------------------------------------- finalizer

class Finalizer:
    """Quarantine, verify, gate, release, commit — for a loop's result.

    The same sequence ``Runner.run`` performs in its stages 10 to 14, over the same
    kernel objects, so a loop's deliverable is judged by the policy exactly as a single
    pass's is. ``policy`` is the run's policy; its ``require_citation`` and
    ``require_claim_support`` are passed to the gate, which can only tighten.
    """

    def __init__(self, kernel: Any, *, policy: Any = None) -> None:
        self.kernel = kernel
        self.policy = policy if policy is not None else getattr(kernel, "policy", None)
        self.finalized = 0

    def finalize(self, result: LoopResult, envelope: RunEnvelope, *,
                 sources: Mapping[str, Any] | None = None, output: str | None = None,
                 project_id: str = "",
                 require_goal_verification: bool | None = None) -> ReleasedResult:
        self.finalized += 1
        policy = self.policy
        require_support = bool(getattr(policy, "require_claim_support", False))
        require_citation = bool(getattr(policy, "require_citation", False))
        require_goal = (require_support if require_goal_verification is None
                        else require_goal_verification)
        base = dict(run_id=envelope.run_id, loop_id=result.loop_id,
                    termination=result.termination.value, goal_status=result.goal_status)

        if result.termination is not Termination.GOAL_SATISFIED:
            self._audit(envelope, "not_completed", reason=result.termination.value)
            return ReleasedResult(status="not_completed", refused_at="execution",
                                  refusal_kind=result.termination.value,
                                  limitations=(f"the loop ended {result.termination.value}",),
                                  **base)

        candidate = output if output is not None else render_deliverable(result)
        # The deliverable's label: the join of everything the loop saw (its result label)
        # and a fresh scan of the rendered text. Never the scan alone.
        fresh = self.kernel.ingress.ensure(candidate, origin="loop_output").label
        label = combine(fresh, result.label or DataLabel())
        ref = self.kernel.quarantine.hold(candidate, label=label, run_id=envelope.run_id)
        limitations: list[str] = []
        if result.goal_status != "verified":
            limitations.append(f"goal verification {result.goal_status}")
        for task_id, caveats in sorted((getattr(result, "caveats", None) or {}).items()):
            for caveat in caveats:
                limitations.append(f"task {task_id} ran degraded: {caveat}")

        if require_goal and result.goal_status != "verified":
            self.kernel.quarantine.refuse(ref, f"goal {result.goal_status}",
                                          run_id=envelope.run_id)
            self._audit(envelope, "refused", stage="goal_verification",
                        goal_status=result.goal_status)
            return ReleasedResult(status="refused", refused_at="goal_verification",
                                  refusal_kind="GoalUnverified", label=label,
                                  quarantine_ref=ref.ref, limitations=tuple(limitations),
                                  **base)

        records = ingest_evidence(self.kernel, sources, envelope)
        supports = verify_claims(self.kernel, candidate, records)
        citations = tuple(dict.fromkeys(s.identifier for s in supports))
        unsupported = sum(1 for s in supports if s.supports is False)
        try:
            verdict = self.kernel.output_gate.check(
                Labeled(candidate, label), envelope, sources=records,
                require_citation=require_citation, require_support=require_support)
        except (EgressDenied, VerificationFailed, PolicyDenied, ApprovalRequired) as exc:
            self.kernel.quarantine.refuse(ref, type(exc).__name__, run_id=envelope.run_id)
            last = self.kernel.output_gate.verdicts[-1] if self.kernel.output_gate.verdicts else None
            self._audit(envelope, "refused", stage="release_gate",
                        kind=type(exc).__name__)
            return ReleasedResult(
                status="refused", refused_at="release_gate", refusal_kind=type(exc).__name__,
                label=label, citations=citations, claims_checked=len(supports),
                claims_unsupported=len(last.unsupported) if last else unsupported,
                claims_uncited=len(last.uncited) if last else 0,
                quarantine_ref=ref.ref, limitations=tuple(limitations), **base)

        released = self.kernel.quarantine.release(ref, run_id=envelope.run_id)
        committed = 0
        if project_id and envelope.permits_destination(Destination.PERSISTENT):
            run_node = self.kernel.persistence.commit_node(
                kind=NodeKind.RUN, title=f"loop {result.loop_id[:12]}",
                principal=envelope.principal.id, source_run=envelope.run_id,
                project_id=project_id, status="complete", ref=envelope.run_id,
                validation_status="system", max_label=envelope.max_label.sensitivity)
            committed = commit_claims(self.kernel, self.kernel.graph, supports,
                                      project_id=project_id, run_node_id=run_node.id,
                                      envelope=envelope)
        if verdict.uncited:
            limitations.append(f"{len(verdict.uncited)} clinical sentence(s) carry no citation")
        self._audit(envelope, "released", claims=len(supports), committed=committed)
        return ReleasedResult(
            status="released", released_output=released, label=label,
            citations=citations, claims_checked=len(supports),
            claims_unsupported=len(verdict.unsupported), claims_uncited=len(verdict.uncited),
            committed_claims=committed, limitations=tuple(limitations),
            quarantine_ref=ref.ref, **base)

    def _audit(self, envelope: RunEnvelope, outcome: str, **detail: Any) -> None:
        audit = getattr(self.kernel, "audit", None)
        if audit is not None:
            audit("loop_release", run_id=envelope.run_id, detail={"outcome": outcome, **detail})
