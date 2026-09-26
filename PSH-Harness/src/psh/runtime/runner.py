"""The single trusted execution path.

The review's second "spine": every model call, tool call, delegation, code execution and
nested harness invocation passes through one control plane. Not most of them — all of them,
because a governed path with one exception is an ungoverned path.

The sequence, from the review's final section, implemented here as one method:

    request -> WorkGraph bind -> classify -> RunEnvelope -> plan
            -> domain/harness retrieval -> capability resolution
            -> context compilation -> policy preflight
            -> execution via the broker -> checkpoint + event
            -> artifact capture -> evidence/claim graph
            -> verification -> final output gate -> user
            -> validated memory commit -> provenance update

``Runner.run`` executes that sequence and refuses to skip stages. The stages that would be
tempting to make optional — classification, the output gate — are the two that closed the
demonstrated holes, so they are unconditional.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..capabilities import CapabilityRegistry, Candidate
from ..config import PSHConfig
from ..context import ContextCompiler
from ..contracts import (
    ApprovalRequired, Autonomy, BudgetExhausted, CapabilityUnavailable, ContextItem,
    ContextProjection, DelegationContract, EgressDenied, ModelProfile, PolicyDenied,
    Principal, RiskTier, RunEnvelope, VerificationFailed, new_id,
)
from ..evidence.support import Claim, ClaimSupport, Evidence
from ..kernel import TrustedKernel
from ..labels import DataLabel, Destination, Labeled, Sensitivity, combine
from ..workgraph import EdgeKind, NodeKind, WorkGraph

#: Support relationship -> WorkGraph edge. Four states, so "no evidence supports this" is
#: recorded distinctly from "evidence refutes this".
_EDGE_FOR = {
    "supports": EdgeKind.SUPPORTS,
    "partially_supports": EdgeKind.PARTIALLY_SUPPORTS,
    "contradicts": EdgeKind.CONTRADICTS,
    "unresolved": EdgeKind.UNRESOLVED,
}

__all__ = ["Runner", "RunResult", "StageRecord", "StuckLoop", "STAGES"]

#: The frozen stage order. Two orderings here are kernel invariants rather than choices:
#: classification precedes every persistence, and release precedes every trusted-memory
#: commit. v0.1 violated both — it wrote the raw request into the WorkGraph before
#: classifying it, and recorded model-produced claims before the gate had judged them.
STAGES: tuple[str, ...] = (
    "ingest", "classify", "policy_snapshot", "bind", "plan", "validate_plan", "resolve",
    "compile_context", "preflight", "execute", "capture_to_quarantine", "ingest_evidence",
    "verify", "release_gate", "commit_validated_state", "checkpoint")


class StuckLoop(PolicyDenied):
    """The same capability was invoked with identical arguments beyond the threshold."""


@dataclass(frozen=True, slots=True)
class StageRecord:
    """One stage of the trusted path, with its timing and outcome."""

    stage: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunResult:
    """Everything one run produced, including why it stopped.

    ``released_output`` is the only field carrying text a caller may use, and it stays
    ``None`` unless the release gate passed. v0.1 exposed ``output`` regardless of the
    verdict, so a caller reading the natural field received exactly the text the gate had
    refused. The refused text remains addressable through ``quarantine_ref`` for audit.
    """

    run_id: str
    status: str = "ok"
    released_output: str | None = None
    quarantine_ref: str = ""
    stages: list[StageRecord] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    projection: ContextProjection | None = None
    supports: list[ClaimSupport] = field(default_factory=list)
    node_ids: dict[str, str] = field(default_factory=dict)
    policy_snapshot: Any = None
    usage: Any = None
    error: str = ""
    refused_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def output(self) -> str | None:
        """Alias for ``released_output``.

        Kept so v0.1 callers keep working, but it returns ``None`` on a refusal rather than
        the refused text — the compatibility is in the name, not in the unsafe behaviour.
        """
        return self.released_output

    def stage(self, name: str) -> StageRecord | None:
        for record in self.stages:
            if record.stage == name:
                return record
        return None

    def summary(self) -> str:
        done = sum(1 for s in self.stages if s.ok)
        return (f"run {self.run_id[:12]} {self.status}: {done}/{len(self.stages)} stages"
                + (f", refused at {self.refused_at}" if self.refused_at else "")
                + (f" — {self.error[:120]}" if self.error else ""))


class Runner:
    """Drives the trusted path. The only supported way to execute work."""

    #: The canonical stage order. Recorded on every run so a skipped stage is visible.
    STAGES = ("bind", "classify", "envelope", "plan", "resolve", "compile", "preflight",
              "execute", "capture", "evidence", "verify", "output_gate", "commit")

    def __init__(self, kernel: TrustedKernel, *, registry: CapabilityRegistry | None = None,
                 graph: WorkGraph | None = None, compiler: ContextCompiler | None = None,
                 model: ModelProfile | None = None,
                 model_invoke: Callable[[str], str] | None = None,
                 policy: Any = None, system_prompt: str = "",
                 clamp_policy: bool = False, memory: Any = None) -> None:
        self.kernel = kernel
        #: Optional ``MemoryRetriever``. When present, what earlier runs of this project
        #: verified is retrieved into stage 7 under the run's ceiling, labelled as stored.
        #: Items passed to ``run(memory=...)`` are still accepted alongside it.
        self.memory = memory
        #: When True a per-run policy wider than the kernel's is silently narrowed to the
        #: meet instead of refused. Refusal is the default for the same reason it is the
        #: default in ``PolicySnapshot.envelope``: a caller who states an authority it must
        #: not have has made a mistake worth surfacing, not a request worth quietly
        #: reinterpreting.
        self.clamp_policy = clamp_policy
        self.policy = self._effective_policy(policy)
        self.config = kernel.config
        self.registry = registry or CapabilityRegistry()
        self.graph = graph or WorkGraph(self.config.index_store)
        self.compiler = compiler or ContextCompiler()
        self.model = model
        self.model_invoke = model_invoke
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        #: Repeat counts for loop detection, keyed by (loop scope, request digest). Bounded
        #: so a long-lived runner cannot grow it without limit.
        self._signatures: dict[tuple[str, str], int] = {}

    #: Most entries a runner keeps for loop detection before discarding the oldest. A
    #: detector that grows without bound is a leak; one that forgets everything is not a
    #: detector.
    MAX_TRACKED_SIGNATURES = 4096

    def _effective_policy(self, requested: Any) -> Any:
        """The policy this run actually executes under: never wider than the kernel's.

        ``Runner.run(policy=...)`` used to take whatever snapshot the caller passed and mint
        the run envelope from it, with no containment check against ``kernel.policy``. A
        kernel built with a local-only, ``SUGGEST``, ``R1`` policy therefore executed a run
        whose envelope named ``PUBLIC_REMOTE``, ``ACT`` and ``R3``, and every gateway
        honoured it — because a gateway checks the envelope it is handed, and this envelope
        had been minted by a policy the kernel never agreed to.

        The same call also produced a split brain: the envelope came from the caller's
        policy while the output gate, the broker's isolation requirement and the
        persistence ceiling came from the kernel's, so ``result.policy_snapshot`` described
        a policy that had not governed most of the run. Meeting the two removes the second
        problem along with the first — the effective policy is contained by the kernel's on
        every dimension, so a gate reading either one cannot be reading a wider rule than
        the run was minted under.
        """
        from ..policy import PolicyLattice

        ceiling = self.kernel.policy
        if requested is None or requested is ceiling:
            return ceiling
        effective = (PolicyLattice.meet(requested, ceiling) if self.clamp_policy
                     else PolicyLattice.enforce(requested, ceiling, operation="run policy"))
        # Contained by the kernel's policy is necessary, not sufficient: a run may add a
        # requirement (the validated classifier, OS isolation) the kernel cannot meet.
        check = getattr(self.kernel, "check_requirements", None)
        if check is not None:
            check(effective)
        return effective

    # --------------------------------------------------------------------- run
    def run(self, request: str, *, policy: Any = None, project_id: str = "",
            task_title: str = "", risk: RiskTier | None = None,
            autonomy: Autonomy | None = None,
            allowed_destinations: Sequence[Destination] | None = None,
            memory: Iterable[ContextItem] = (), sources: Mapping[str, Any] | None = None,
            max_capabilities: int = 8, loop_scope: str = "") -> RunResult:
        """Execute one request through all sixteen stages of the trusted path.

        Two orderings are invariants, not preferences:

        * **classification precedes persistence** — nothing reaches the WorkGraph before it
          has been classified, so an identifier cannot be registered as public;
        * **release precedes trusted commit** — no model-produced claim enters project
          memory before the gate has judged it, so a refused conclusion cannot be retrieved
          into a later prompt.

        ``policy`` is a ``PolicySnapshot``. It replaces the v0.1 pair of config and envelope
        keyword paths, which let a declared field go unenforced.
        """
        stages: list[StageRecord] = []
        result = RunResult(run_id="", stages=stages)
        quarantine_ref = None

        def stage(name: str):
            started = time.perf_counter()
            class _Ctx:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, exc_type, exc, tb):
                    stages.append(StageRecord(
                        stage=name, ok=exc_type is None,
                        detail=getattr(self_inner, "detail", ""),
                        seconds=round(time.perf_counter() - started, 5)))
                    return False
            return _Ctx()

        try:
            # 0. INGEST — the raw request enters the system and is wrapped, nothing more.
            with stage("ingest") as ctx:
                # Not ``policy or self.policy``: a per-run policy is an authority claim and
                # is admitted only if the kernel's policy contains it.
                policy = self.policy if policy is None else self._effective_policy(policy)
                ctx.detail = f"{len(request)} chars, profile={policy.profile_id}"

            # 1. CLASSIFY — before anything is stored, anywhere.
            with stage("classify") as ctx:
                labeled_request = self.kernel.ingress.ensure(request, origin="user_request")
                ctx.detail = labeled_request.sensitivity.name

            # 2. POLICY_SNAPSHOT — frozen for the whole run.
            with stage("policy_snapshot") as ctx:
                envelope = policy.envelope(
                    task_id="", project_id=project_id,
                    **({"risk": risk} if risk is not None else {}),
                    **({"autonomy": autonomy} if autonomy is not None else {}))
                if allowed_destinations is not None:
                    envelope = envelope.restrict(allowed_destinations=allowed_destinations)
                result.run_id = envelope.run_id
                result.policy_snapshot = policy
                self.kernel.audit("run_created", run_id=envelope.run_id,
                                  detail=policy.as_dict())
                ctx.detail = policy.summary()

            # 3. BIND — now safe: the label is known, so the write is classified.
            with stage("bind") as ctx:
                project_id = project_id or self._default_project()
                # ``max_label`` is the RUN's ceiling, not the store's. The persistence
                # gateway is built once from the kernel's policy, so a run under a narrower
                # policy would otherwise write at the kernel's ceiling.
                ceiling = envelope.max_label.sensitivity
                task = self.kernel.persistence.commit_node(
                    kind=NodeKind.TASK, title=task_title or request[:110],
                    body="", principal=envelope.principal.id, source_run=envelope.run_id,
                    project_id=project_id, status="in_progress",
                    validation_status="system", max_label=ceiling)
                run_node = self.kernel.persistence.commit_node(
                    kind=NodeKind.RUN, title=f"run {envelope.run_id[:12]}",
                    principal=envelope.principal.id, source_run=envelope.run_id,
                    project_id=project_id, status="running", ref=envelope.run_id,
                    validation_status="system", max_label=ceiling)
                self.graph.link(project_id, task, EdgeKind.HAS)
                self.graph.link(task, run_node, EdgeKind.HAS)
                # The envelope was minted before the task existed, so its ``task_id`` was
                # the empty string for the whole run. Everything keyed on it — loop
                # detection above all — therefore shared one bucket across unrelated runs.
                # Bind the real id now that there is one; ``run_id`` is preserved, so every
                # reference already taken stays valid.
                envelope = replace(envelope, task_id=task.id)
                result.node_ids = {"task": task.id, "run": run_node.id}
                ctx.detail = f"task={task.id[:12]} label={task.label.sensitivity.name}"

            # 4-5. PLAN and VALIDATE_PLAN
            with stage("plan") as ctx:
                plan = self._plan(request, labeled_request)
                ctx.detail = f"{len(plan)} step(s)"
            with stage("validate_plan") as ctx:
                ctx.detail = "placeholder planner: no typed plan to validate"

            # 6. RESOLVE
            with stage("resolve") as ctx:
                candidates = self.registry.resolve(request, envelope, limit=max_capabilities)
                result.candidates = candidates
                ctx.detail = (f"{len(candidates)} of "
                              f"{self.registry.last_trace.considered} considered")

            # 7. COMPILE_CONTEXT
            with stage("compile_context") as ctx:
                destination = (self.model.destination if self.model
                               else Destination.LOCAL_MODEL)
                items = [ContextItem(kind="instruction", content=self.system_prompt,
                                     label=DataLabel())]      # static text: PUBLIC
                items += list(memory)
                if self.memory is not None:
                    items += self.memory.retrieve(request, envelope=envelope,
                                                  project_id=project_id,
                                                  destination=destination)
                items += self.registry.manifest_items(candidates)
                items.append(ContextItem(kind="turn", content=request,
                                         label=labeled_request.label))
                projection = self.compiler.compile(
                    items=items, envelope=envelope, destination=destination,
                    token_budget=envelope.budget.tokens_soft, query=request)
                result.projection = projection
                ctx.detail = projection.summary()

            # 8. PREFLIGHT
            with stage("preflight") as ctx:
                self._preflight(request, projection, envelope, loop_scope=loop_scope)
                ctx.detail = f"label={projection.label.sensitivity.name}"

            # 9. EXECUTE
            with stage("execute") as ctx:
                call = self._execute(projection, envelope)
                candidate_output = call.content if hasattr(call, "content") else str(call)
                result.usage = getattr(call, "usage", None)
                ctx.detail = f"{len(candidate_output)} chars"

            # 10. CAPTURE_TO_QUARANTINE — the output is held, not returned.
            with stage("capture_to_quarantine") as ctx:
                # The label of a model's output is the join of three things: what the
                # model was shown (the projection), what the broker recorded on the result,
                # and a fresh scan of the text. Re-classifying the text alone let a count
                # derived from a PHI chart leave as INTERNAL — derivation never lowers a
                # label, and the join is where that rule is enforced.
                fresh = self.kernel.ingress.ensure(candidate_output, origin="model_output")
                inherited = combine(projection.label,
                                    getattr(call, "label", None) or DataLabel())
                labeled_output = Labeled(candidate_output,
                                         fresh.label.merged_with(inherited))
                quarantine_ref = self.kernel.quarantine.hold(
                    candidate_output, label=labeled_output.label, run_id=envelope.run_id)
                result.quarantine_ref = quarantine_ref.ref
                ctx.detail = f"held {quarantine_ref.size_bytes}B as {quarantine_ref.ref}"

            # 11. INGEST_EVIDENCE — build records; nothing is committed to memory yet.
            with stage("ingest_evidence") as ctx:
                records = self._ingest_evidence(sources or {}, envelope)
                ctx.detail = f"{len(records)} evidence record(s)"

            # 12. VERIFY — and record rejections now, because the release gate may raise
            # before the commit stage is reached. A rejected claim must be registered as a
            # hash-and-reason reference whether or not the run goes on to release.
            with stage("verify") as ctx:
                supports = self._verify_claims(candidate_output, records)
                result.supports = supports
                unsupported = [s for s in supports if not s.supports]
                for support in unsupported:
                    self.kernel.persistence.commit_rejected(
                        statement=support.claim, reason=support.rationale,
                        principal=envelope.principal.id, source_run=envelope.run_id,
                        project_id=project_id)
                ctx.detail = f"{len(supports)} checked, {len(unsupported)} unsupported"

            # 13. RELEASE_GATE — the only path by which output becomes readable.
            with stage("release_gate") as ctx:
                verdict = self.kernel.output_gate.check(
                    labeled_output, envelope, sources=records,
                    require_citation=policy.require_citation,
                    require_support=policy.require_claim_support)
                result.released_output = self.kernel.quarantine.release(
                    quarantine_ref, run_id=envelope.run_id)
                ctx.detail = verdict.reason[:110]

            # 14. COMMIT_VALIDATED_STATE — only verified knowledge enters project memory.
            with stage("commit_validated_state") as ctx:
                committed = self._commit_claims(supports, project_id, run_node.id, envelope)
                self.kernel.persistence.update_node(
                    run_node.id, principal=envelope.principal.id,
                    source_run=envelope.run_id, status="complete")
                ctx.detail = f"{committed} verified claim(s) committed"

            # 15. CHECKPOINT
            with stage("checkpoint") as ctx:
                self.kernel.audit("run_completed", run_id=envelope.run_id,
                                  tokens=getattr(result.usage, "total_tokens", 0),
                                  detail={"stages": len(stages)})
                ctx.detail = "run recorded"

            result.status = "ok"
            return result

        except (EgressDenied, VerificationFailed, BudgetExhausted, ApprovalRequired,
                CapabilityUnavailable, StuckLoop, PolicyDenied) as exc:
            return self._close(result, stages, exc, status="refused",
                               quarantine_ref=quarantine_ref)
        except Exception as exc:  # noqa: BLE001 - a runtime fault must close the run
            # A generic failure must close the run and be recorded, not propagate and leave
            # the WorkGraph showing a run that never ended.
            return self._close(result, stages, exc, status="failed",
                               quarantine_ref=quarantine_ref)

    def _close(self, result: RunResult, stages: list[StageRecord], exc: BaseException, *,
               status: str, quarantine_ref: Any = None) -> RunResult:
        """Close a run that did not reach release. No output is ever released here."""
        result.status = status
        result.error = f"{type(exc).__name__}: {exc}"
        result.refused_at = stages[-1].stage if stages else "ingest"
        result.released_output = None
        if quarantine_ref is not None:
            self.kernel.quarantine.refuse(quarantine_ref, result.error,
                                          run_id=result.run_id)
        if result.node_ids.get("run"):
            try:
                self.graph.update(result.node_ids["run"], status=status)
            except Exception:  # pragma: no cover - best effort
                pass
        self.kernel.audit("run_refused" if status == "refused" else "run_failed",
                          run_id=result.run_id or "",
                          detail={"refused_at": result.refused_at,
                                  "error_type": type(exc).__name__,
                                  "error": str(exc)[:200]})
        return result

    # ------------------------------------------------------------------ stages
    def _default_project(self) -> str:
        existing = self.graph.nodes(kind=NodeKind.PROJECT, limit=1)
        if existing:
            return existing[0].project_id
        return self.graph.project("default").project_id

    def _plan(self, request: str, labeled: Labeled) -> list[str]:
        """Minimal planner: one step per sentence of intent.

        Deliberately simple. A planner good enough to decompose research work needs a model
        and belongs behind the same gateway as any other model call; this placeholder keeps
        the path complete and honest about what it is.
        """
        return [s.strip() for s in request.split(".") if s.strip()][:6] or [request]

    def _preflight(self, request: str, projection: ContextProjection,
                   envelope: RunEnvelope, *, loop_scope: str = "") -> None:
        """Check the run is permissible before anything executes."""
        self._check_not_looping(request, envelope, loop_scope=loop_scope)
        if envelope.expired:
            raise BudgetExhausted("run deadline passed before execution")
        if projection.label.sensitivity > envelope.max_label.sensitivity:
            raise PolicyDenied(
                f"compiled context is {projection.label.sensitivity.name} but this run's "
                f"ceiling is {envelope.max_label.sensitivity.name}")

    def _check_not_looping(self, request: str, envelope: RunEnvelope, *,
                           loop_scope: str = "") -> None:
        """Refuse a request that is repeating **within one piece of work**.

        The signature was ``f"{envelope.task_id}:{hash(request)}"`` and ``task_id`` was
        always ``""`` at this point, because the envelope is minted at stage 2 and the task
        is not created until stage 3. Every run of one ``Runner`` therefore shared a single
        bucket, so three independent user requests that happened to be identical — a
        perfectly ordinary thing for a user to do — were counted as an agent spinning, and
        the third was refused. The counter was also never cleared and ``hash()`` of a str is
        salted per process, so the same request produced different signatures across runs.

        A loop is a repeat within one scope: a task, an orchestration session, or a
        delegation subtree. So the scope is explicit — the caller's ``loop_scope`` when it
        names one, else the parent run for a delegated run, else this run's task, which is
        unique per top-level ``run()`` and therefore cannot collide with another.
        """
        scope = loop_scope or envelope.parent_run_id or envelope.task_id or envelope.run_id
        digest = hashlib.sha256(request.encode("utf-8", "replace")).hexdigest()[:32]
        key = (scope, digest)
        count = self._signatures.get(key, 0) + 1
        self._signatures[key] = count
        if len(self._signatures) > self.MAX_TRACKED_SIGNATURES:
            for stale in list(self._signatures)[:len(self._signatures)
                                                - self.MAX_TRACKED_SIGNATURES]:
                self._signatures.pop(stale, None)
        if count >= self.config.stuck_loop_threshold:
            raise StuckLoop(
                f"the same request has been issued {count} times within scope {scope!r} "
                "with identical arguments; stopping rather than looping")

    def _execute(self, projection: ContextProjection, envelope: RunEnvelope) -> Any:
        """Run the model call through the broker. Never calls a provider directly."""
        from ..kernel.results import ModelCallResult, ModelUsage

        if self.model is None or self.model_invoke is None:
            # No model configured: the path still completes, which keeps the governance
            # layer usable and testable on its own.
            return ModelCallResult(content=projection.render()[-400:],
                                   usage=ModelUsage(), model_id="none")
        return self.kernel.broker.call_model(projection, self.model, envelope,
                                             invoke=self.model_invoke)

    def _ingest_evidence(self, sources: Mapping[str, Any],
                         envelope: RunEnvelope) -> dict[str, Any]:
        """Turn supplied sources into EvidenceRecords, preserving provenance.

        A caller may pass either a record (retrieved through a trusted capability) or bare
        text. Bare text is accepted but marked untrusted, so it cannot on its own establish
        support — the fabricated-abstract path a reviewer identified.
        """
        from .finalize import ingest_evidence

        return ingest_evidence(self.kernel, sources, envelope)

    def _verify_claims(self, output: str, records: Mapping[str, Any]) -> list[ClaimSupport]:
        """Verify every cited clinical claim against its record. Commits nothing."""
        from .finalize import verify_claims

        return verify_claims(self.kernel, output, records)

    def _commit_claims(self, supports: Sequence[ClaimSupport], project_id: str,
                       run_node_id: str, envelope: RunEnvelope) -> int:
        """Commit verified claims; record rejected ones by hash only.

        This runs *after* the release gate. In v0.1 it ran before, so a claim the gate then
        refused was already in project memory, retrievable by the context compiler into a
        later prompt — persistent epistemic contamination rather than a one-off error.
        """
        from .finalize import commit_claims

        return commit_claims(self.kernel, self.graph, supports, project_id=project_id,
                             run_node_id=run_node_id, envelope=envelope)

    # -------------------------------------------------------------- delegation
    def delegate(self, objective: str, parent: RunEnvelope, *,
                 backend: Callable[[DelegationContract], Any],
                 projection: ContextProjection | None = None,
                 capabilities: Sequence[str] = (),
                 max_label: Sensitivity | None = None,
                 output_schema: Mapping[str, Any] | None = None) -> Any:
        """Delegate through the gateway, with a strictly narrower envelope."""
        child = parent.restrict(
            allowed_capabilities=tuple(capabilities) or parent.allowed_capabilities,
            max_label=None if max_label is None else parent.max_label.__class__(max_label),
            budget=parent.budget.child())
        contract = DelegationContract(
            task_id=parent.task_id, objective=objective, envelope=child,
            projection=projection, output_schema=dict(output_schema or {}),
            backend="local_agent")
        return self.kernel.broker.delegate(contract, parent, backend)

    def report(self) -> dict[str, Any]:
        return {"kernel": self.kernel.report(), "registry": self.registry.stats(),
                "workgraph": self.graph.stats(),
                "compilations": self.compiler.compilations}


DEFAULT_SYSTEM_PROMPT = """\
You are a research assistant for a physician-scientist, operating inside a governed
harness. Three rules follow from that.

**Provenance.** Every clinical claim must carry an identifier you retrieved this session,
and the source must actually support the sentence you attach it to. A citation that exists
but does not support the claim will be refused at the output gate, so state what the source
shows rather than what you expect it to show. Where a source is not determinate, say so.

**Data boundary.** The harness classifies data and enforces where it may travel. You do not
need to police this, but you should understand it: identifiable clinical content cannot
reach a public model or a public API, and a summary of identifiable content is still
identifiable. If a step is refused on those grounds, restate the question in general terms
rather than trying to work around it.

**Durability.** Your work is recorded in a project graph, not a chat log. Record decisions
and their reasons as you make them, because in three months the reason is what matters and
the conversation will be gone.
"""
