"""The trusted kernel: classification, egress gateways, budgets, events, output gate.

Not a component. Nothing in a manifest can replace any of this, which is the review's
correction to "everything is a component" — a component able to replace the thing enforcing
policy is not governed by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import PSHConfig
from ..contracts import (
    PolicyDenied,
    ApprovalRequired, Autonomy, ModelProfile, Principal, RiskTier, RunEnvelope,
)
from ..evidence.signing import EvidenceSigner
from ..evidence.support import ClaimSupportVerifier
from ..labels import (
    DataLabel, Declassification, Destination, Labeled, Sensitivity,
)
from .authority import AuthorityLattice, AuthorityViolation
from .budget import BudgetGovernor, BudgetState
from .classify import Classifier, SABLE_AVAILABLE
from .egress import (
    ApprovalEngine, DelegationGateway, EgressDecision, ExecutionBroker, ModelGateway,
    ToolGateway,
)
from .events import ChainVerification, EventStore, GENESIS_HASH
from .ingress import IngressDecision, IngressGateway
from .output_gate import OutputGate, OutputVerdict
from .persistence import CommitRequest, PersistenceGateway, ValidationStatus
from .release import Quarantine, QuarantineRef, ReleaseDecision
from .results import ExecutionResult, ModelCallResult, ModelUsage

__all__ = [
    "TrustedKernel", "Classifier", "IngressGateway", "IngressDecision",
    "ModelGateway", "ToolGateway", "DelegationGateway", "PersistenceGateway",
    "CommitRequest", "ValidationStatus", "Quarantine", "QuarantineRef", "ReleaseDecision",
    "AuthorityLattice", "AuthorityViolation", "ExecutionResult", "ModelCallResult",
    "ModelUsage", "ExecutionBroker", "ApprovalEngine", "EgressDecision", "EventStore",
    "ChainVerification", "BudgetGovernor", "BudgetState", "OutputGate", "OutputVerdict",
    "SABLE_AVAILABLE", "GENESIS_HASH",
]


class TrustedKernel:
    """One object holding every enforcement point, wired in the correct order.

    The wiring is the security property. Classification happens first, because every later
    decision consults a label. The event sink is attached to every gate, so a refusal is
    recorded even when it aborts the run. And the broker is the only object exposing
    execution, so "nothing bypasses the trusted path" is structural.
    """

    def __init__(self, config: PSHConfig | None = None, *,
                 policy: Any = None,
                 approval_handler: Callable[[str, Mapping[str, Any]], bool] | None = None,
                 allowed_paths: Sequence[str] = (),
                 verification_model: Any = None,
                 verification_invoke: Callable[[str], str] | None = None,
                 on_warning: Callable[[str], None] | None = None,
                 strict_ingress: bool = False,
                 isolated_runner: Any = None,
                 sandbox: Any = None,
                 secret_resolver: Callable[[str], str | None] | None = None) -> None:
        from ..policy import PolicySnapshot
        from ..workgraph import WorkGraph

        self.config = (config or PSHConfig()).ensure_dirs()
        self.policy: PolicySnapshot = policy or PolicySnapshot(
            profile_id=self.config.profile,
            require_claim_support=self.config.require_claim_support,
            risk_ceiling=self.config.default_risk,
            budget=self.config.budget)

        self.events = EventStore(self.config.event_store,
                                 policy_version=self.config.policy_version)
        self.classifier = Classifier()
        if (not self.classifier.validated
                and self.policy.max_data_label >= Sensitivity.PHI
                and any(d in self.policy.allowed_destinations
                        for d in (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE))
                and on_warning is not None):
            on_warning("classification runs on psh's built-in fallback while this policy "
                       "admits PHI and permits remote destinations; identifiers the fallback "
                       "does not recognise (see Classifier.describe()) will not be withheld")
        # The sink sanitises recursively using the classifier, so nested sensitive strings
        # never reach the event store.
        sink = self.events.sink(classifier=self.classifier)
        self.audit = sink

        # Ingress first: every other gateway calls it, so classification cannot be skipped
        # by reaching a gateway through a different path.
        self.ingress = IngressGateway(self.classifier, audit=sink, strict=strict_ingress)

        self.model_gateway = ModelGateway(ceilings=self.config.destination_ceilings or None,
                                          audit=sink)
        # One declarative execution policy, shared by the gateway that consults it and the
        # approval engine that may amend it. Loaded from the state dir when a policy file
        # exists there, so persisted amendments survive restarts; the shipped defaults
        # otherwise.
        from .approvals import AmendingApprovalEngine
        from .execpolicy import ExecPolicy

        policy_path = self.config.state_dir / "execpolicy.json"
        self.execpolicy = (ExecPolicy.load(policy_path) if policy_path.exists()
                           else ExecPolicy())
        self.tool_gateway = ToolGateway(
            allowed_paths=allowed_paths or [str(self.config.state_dir / "*")],
            ceilings=self.config.destination_ceilings or None, audit=sink,
            policy=self.execpolicy)
        self.delegation_gateway = DelegationGateway(audit=sink)
        self.approvals = AmendingApprovalEngine(
            approval_handler, policy=self.execpolicy, audit=sink,
            persist=lambda pol: pol.save(policy_path))
        self.budget = BudgetGovernor(self.policy.budget, on_warning=on_warning, audit=sink)

        self.graph = WorkGraph(self.config.index_store)
        self.persistence = PersistenceGateway(self.graph, self.ingress, audit=sink,
                                              max_label=self.policy.max_data_label)
        self.quarantine = Quarantine(self.config.state_dir / "quarantine", audit=sink)

        # Process isolation is wired into the one execution path rather than offered as a
        # library the caller may remember to use. Components declaring backend="subprocess"
        # run through it; the rest run in process and are counted as such.
        from .isolation import IsolatedExecutor, IsolatedRunner

        self.isolated_runner = isolated_runner or IsolatedRunner(sandbox=sandbox, audit=sink)
        self.isolation = IsolatedExecutor(
            self.isolated_runner, workdir_root=self.config.state_dir / "sandbox",
            secret_resolver=secret_resolver)
        #: What this kernel can actually enforce, checked against what its policy — and
        #: every run policy admitted later — says it must. A kernel that cannot meet its
        #: own policy does not start.
        self.isolation_report = (self.isolated_runner.report()
                                 if hasattr(self.isolated_runner, "report") else None)
        self.check_requirements(self.policy)

        self.broker = ExecutionBroker(
            model_gateway=self.model_gateway, tool_gateway=self.tool_gateway,
            delegation_gateway=self.delegation_gateway, approvals=self.approvals,
            budget_governor=self.budget, ingress=self.ingress, audit=sink,
            isolation=self.isolation,
            require_isolation=self.policy.require_isolated_tools)

        # The verification model is a model call like any other: routed through the broker,
        # so its source text is classified and gated. v0.1 took a raw callable that reached
        # a provider directly, which was a second egress path.
        self.verification_model = verification_model
        self._verification_envelope = None
        model_invoke = None
        if verification_invoke is not None and verification_model is not None:
            # The verification model is egress like any other, so its envelope is minted
            # under the policy ceiling. A policy that forbids the verifier's destination
            # must fail here, loudly, at construction — not silently mint a wider envelope
            # and discover the contradiction at the first claim check.
            # The ceiling is what the verification model may lawfully receive under this
            # policy — the lower of the two. It used to be stated as PUBLIC, which no gate
            # read; now that the model gateway enforces a run's ceiling, PUBLIC would
            # refuse every verification, because the classifier floors ordinary text at
            # INTERNAL (the user's own working material).
            try:
                self._verification_envelope = self.envelope(
                    allowed_destinations=[verification_model.destination,
                                          Destination.LOCAL_COMPUTE],
                    max_label=min(self.policy.max_data_label,
                                  verification_model.max_label))
            except PolicyDenied as exc:
                raise PolicyDenied(
                    f"policy {self.policy.profile_id!r} does not permit destination "
                    f"{verification_model.destination.name}, so the verification model "
                    f"{verification_model.id!r} cannot be configured under it: {exc}") from exc

            def model_invoke(prompt: str) -> str:
                labelled = self.ingress.ensure(prompt, origin="claim_verification")
                result = self.broker.call_model(
                    labelled, verification_model, self._verification_envelope,
                    invoke=verification_invoke)
                return getattr(result, "content", str(result))

        self.evidence_signer = EvidenceSigner(self.config.secret_store / "evidence.key")

        self.verifier = ClaimSupportVerifier(model_invoke=model_invoke)
        self.output_gate = OutputGate(verifier=self.verifier,
                                      require_support=self.policy.require_claim_support,
                                      require_citation=self.policy.require_citation,
                                      audit=sink)

    # ------------------------------------------------------------ classification
    @property
    def hooks(self):
        """The typed hook registry, living on the broker (the one trusted path)."""
        return self.broker.hooks

    def declassify(self, labeled: Any, *, to: Sensitivity, method: str, principal: str,
                   rationale: str = "") -> Any:
        """Lower a label. The only operation in the lattice that widens exposure.

        Three checks, then an event. The principal must be named in the policy's
        ``declassifiers``; the target may not be below the policy's ``declassify_floor``; and
        the value must currently sit ABOVE the target (declassifying something already at or
        below the target is a no-op that would still create a misleading record). The event
        carries from/to/method/principal and never the value.
        """
        from ..labels import Declassification, Labeled

        if not isinstance(labeled, Labeled):
            labeled = self.ingress.ensure(labeled, origin="declassify")
        policy = self.policy
        if principal not in (policy.declassifiers or ()):
            self.events.append("declassification_refused", run_id="",
                               detail={"principal": principal, "reason": "not a permitted declassifier",
                                       "from": labeled.label.sensitivity.name, "to": to.name})
            raise PolicyDenied(
                f"principal {principal!r} may not declassify; permitted: "
                f"{list(policy.declassifiers) or 'none'}")
        if to < policy.declassify_floor:
            self.events.append("declassification_refused", run_id="",
                               detail={"principal": principal, "reason": "below policy floor",
                                       "from": labeled.label.sensitivity.name, "to": to.name,
                                       "floor": policy.declassify_floor.name})
            raise PolicyDenied(
                f"policy floor is {policy.declassify_floor.name}; cannot declassify to {to.name}")
        if labeled.label.sensitivity <= to:
            raise PolicyDenied(
                f"value is already {labeled.label.sensitivity.name}; nothing to declassify to {to.name}")

        record = Declassification(
            from_sensitivity=labeled.label.sensitivity, to_sensitivity=to, method=method,
            principal=principal, rationale=rationale or method)
        # The kernel remembers every declassification it issued. Ingress honours a lowered
        # label ONLY when its declassification ids are all in this registry — a caller who
        # constructs a Declassification themselves gets re-classified upward like anyone else.
        self.ingress.authorise_declassification(record)
        new_label = DataLabel(to, categories=(), shareable=to <= Sensitivity.RESEARCH_DEIDENTIFIED,
                              rationale=f"declassified by {principal}: {method}",
                              classifier=labeled.label.classifier)
        self.events.append("declassification", run_id="",
                           detail={"principal": principal, "method": method,
                                   "from": record.from_sensitivity.name, "to": to.name,
                                   "declassification_id": record.id,
                                   "categories_before": list(labeled.label.categories)})
        return Labeled(value=labeled.value, label=new_label, origin=labeled.origin,
                       derived_from=labeled.derived_from + (labeled.id,),
                       declassifications=labeled.declassifications + (record,))

    def classify(self, value: Any, *, origin: str = "") -> Labeled:
        """Label a value entering the system."""
        return self.classifier.classify(value, origin=origin)

    # -------------------------------------------------------------- envelopes
    def envelope(self, *, task_id: str = "", project_id: str = "",
                 principal: Principal | None = None, risk: RiskTier | None = None,
                 autonomy: Autonomy | None = None,
                 max_label: Sensitivity | None = None,
                 allowed_destinations: Sequence[Destination] | None = None,
                 clamp: bool = False, **kw: Any) -> RunEnvelope:
        """Mint a run envelope **under this kernel's policy**, which is the ceiling.

        v0.4 minted here from hard-coded defaults — ``autonomy=ACT_WITH_APPROVAL``,
        ``max_label=PHI``, the four local destinations — and never consulted
        ``self.policy`` at all. A kernel built with the ``peer_review`` policy (no network,
        ``SUGGEST``, ``SENSITIVE``) therefore handed out envelopes carrying ``ACT``, ``PHI``
        and any destination the caller asked for, and every gate downstream honoured them
        because a gate checks the envelope it is given. The policy was documentation.

        There is now exactly one minting path: ``PolicySnapshot.envelope``, which refuses
        any dimension wider than the policy. ``clamp=True`` narrows instead of refusing.
        """
        env = self.policy.envelope(
            clamp=clamp, task_id=task_id, project_id=project_id,
            principal=principal or Principal(id="local", kind="human"),
            risk=risk, autonomy=autonomy, max_label=max_label,
            allowed_destinations=allowed_destinations, **kw)
        self.audit("run_created", run_id=env.run_id, risk=env.risk.name,
                   autonomy=env.autonomy.value, profile=env.profile)
        return env

    # ------------------------------------------------------------------ report
    def report(self) -> dict[str, Any]:
        verification = self.events.verify()
        return {
            "policy": self.policy.as_dict(),
            "ingress": self.ingress.stats(),
            "persistence": self.persistence.stats(),
            "quarantine": self.quarantine.stats(),
            "policy_version": self.config.policy_version,
            "profile": self.config.profile,
            "classifier": self.classifier.detector_name,
            "sable_available": SABLE_AVAILABLE,
            "broker": self.broker.stats(),
            "isolation": {"sandbox": type(self.isolated_runner.sandbox).__name__,
                          "describes": self.isolated_runner.sandbox.describe(),
                          "isolated_runs": self.isolated_runner.runs,
                          "required_by_policy": self.policy.require_isolated_tools,
                          "os_isolation_required": getattr(
                              self.policy, "require_os_isolation", False),
                          "report": (self.isolation_report.as_dict()
                                     if self.isolation_report is not None else None)},
            "classifier_validated": self.classifier.validated,
            "egress_refusals": {
                "model": len(self.model_gateway.refusals),
                "tool": len(self.tool_gateway.refusals),
                "delegation": len(self.delegation_gateway.refusals)},
            "output_gate": {"checks": self.output_gate.checks,
                            "refused": sum(1 for v in self.output_gate.verdicts
                                           if not v.allowed)},
            "claim_verifications": self.verifier.verifications,
            "events": {"records": len(self.events), "intact": bool(verification),
                       "head_hash": self.events.head_hash[:16] + "..."},
            "approvals_requested": len(self.approvals.requests),
        }

    def check_requirements(self, policy: Any) -> None:
        """Refuse a policy whose requirements this process cannot meet.

        The policy lattice lets a child *add* a requirement, so a run policy may demand
        the validated classifier or OS-level isolation that the kernel's own policy did
        not. A requirement is a statement about the world, not a preference, and a kernel
        that cannot meet it must say so before the run starts — not run and describe
        itself as compliant afterwards.
        """
        if getattr(policy, "require_validated_classifier", False) and \
                not self.classifier.validated:
            raise PolicyDenied(
                f"policy {policy.profile_id!r} requires the validated PHI detector and "
                f"this process has only {self.classifier.detector_name}; install sable or "
                "run under a profile that does not handle identifiable records")
        report = self.isolation_report
        if getattr(policy, "require_os_isolation", False) and \
                (report is None or not report.os_isolation):
            have = report.sandbox if report is not None else "no isolated runner"
            raise PolicyDenied(
                f"policy {policy.profile_id!r} requires OS-level isolation for tools and "
                f"this kernel has {have}: the child process is not confined beyond a "
                "clean environment and the egress proxy; install a SandboxBackend "
                "(bubblewrap+seccomp on Linux, Seatbelt on macOS) or run under a profile "
                "that does not execute untrusted code")

    def close(self) -> None:
        """Release every handle this kernel opened, not only the event store.

        The event store was closed and the WorkGraph's SQLite connection was not, which is
        invisible on Linux and produces "database is locked" / undeletable files on Windows.
        Each close is independent: one failing must not leave the rest open.
        """
        for closer in (self.events, self.graph, self.persistence, self.quarantine):
            close = getattr(closer, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # noqa: BLE001 - a close failure must not mask the others
                continue

    def __enter__(self) -> "TrustedKernel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
