"""BioAgent — the federating agent loop.

Wires the five layers the survey identified as necessary:

    registry   - one catalogue over 16 upstream projects (2,567 capabilities)
    adapters   - invoke capabilities without copying unlicensed code
    planner    - retrieval-based plan / execute / critique
    executor   - sandboxed subprocess for generated code
    provenance - append-only, replayable record of every call

The loop is intentionally small; the value is in the routing decisions, which
are driven by catalogue metadata (license, availability, omics type) rather
than hard-coded per-tool logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .adapters.base import Adapter, CallResult
from .core.executor import SandboxExecutor
from .core.planner import Critique, Plan, Planner, RetrievalPlanner
from .planners.base import merge_step_arguments
from .core.provenance import ProvenanceLog
from .policy import AuthorizationRequest, PolicyKernel
from .status import ExecutionStatus, RunOutcome, ScientificVerdict
from .registry import CapabilityRegistry


@dataclass
class RunReport:
    """Result of one end-to-end agent run.

    Execution and acceptance are reported separately: `execution_outcome` says
    whether the steps ran, `verdict` says whether the critique accepted what they
    produced, and `ok` requires both.
    """

    task: str
    plan: Plan
    results: list[CallResult] = field(default_factory=list)
    all_results: list[CallResult] = field(default_factory=list)
    critique: Critique | None = None
    provenance: ProvenanceLog | None = None
    attempts: int = 1

    @property
    def execution_outcome(self) -> str:
        """Did the machinery run? Says nothing about whether the science holds."""
        if not self.results:
            return RunOutcome.FAILED.value
        n_exec = sum(r.executed for r in self.results)
        n_succ = sum(r.status.successful for r in self.results)
        if n_exec == 0:
            return RunOutcome.DEGRADED.value
        if n_succ == len(self.results):
            return RunOutcome.SUCCESS.value
        if n_succ > 0:
            return RunOutcome.PARTIAL_SUCCESS.value
        return RunOutcome.FAILED.value

    @property
    def outcome(self) -> str:
        """Back-compatible alias for `execution_outcome`."""
        return self.execution_outcome

    @property
    def verdict(self) -> str:
        """Did the science hold? Taken from the critique, never from exit codes.

        A critique that only watched steps execute reports INCONCLUSIVE, so a run
        cannot be `ok` until a validator has actually judged the result.
        """
        if self.critique is None:
            return ScientificVerdict.INCONCLUSIVE.value
        declared = getattr(self.critique, "verdict", None)
        if declared in tuple(v.value for v in ScientificVerdict):
            return declared
        return (ScientificVerdict.ACCEPTED.value if self.critique.accepted
                else ScientificVerdict.REJECTED.value)

    @property
    def ok(self) -> bool:
        """Accepted only when the run both executed cleanly and passed validation.

        `execution_outcome` alone used to decide this, so an evaluator that ran
        successfully and scored 0.0 produced outcome=SUCCESS, ok=True and
        accepted=True while its own critique said the validation had failed.
        """
        return (self.execution_outcome == RunOutcome.SUCCESS.value
                and self.verdict == ScientificVerdict.ACCEPTED.value)

    def summary(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "n_steps": len(self.plan),
            "n_executed": sum(r.executed for r in self.results),
            "n_succeeded": sum(r.status.successful for r in self.results),
            "execution_outcome": self.execution_outcome,
            "scientific_verdict": self.verdict,
            "outcome": self.execution_outcome,
            "accepted": self.ok,
            "critique": self.critique.reason if self.critique else None,
            "attempts": self.attempts,
            "statuses": {r.capability: r.status.value for r in self.results},
            "adapters_used": sorted({r.adapter for r in self.results}),
            "provenance_steps": len(self.provenance) if self.provenance else 0,
        }


class BioAgent:
    """Plans over the catalogue and invokes capabilities through a policy gate."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapters: Sequence[Adapter],
        planner: Planner | None = None,
        executor: SandboxExecutor | None = None,
        catalogue_version: str = "unknown",
        kernel: PolicyKernel | None = None,
        profile: str = "biomedical-research",
    ) -> None:
        self.registry = registry
        self.adapters = list(adapters)
        self.planner = planner or RetrievalPlanner()
        self.executor = executor or SandboxExecutor()
        self.catalogue_version = catalogue_version
        # The policy kernel is not optional: without it the license boundary is
        # documentation rather than enforcement (the v1 defect).
        self.kernel = kernel or PolicyKernel()
        self.profile = profile

    # ------------------------------------------------------------- dispatch
    def adapter_for(self, capability: Any) -> Adapter | None:
        for ad in self.adapters:
            try:
                if ad.can_handle(capability):
                    return ad
            except Exception:  # noqa: BLE001 - a broken adapter must not halt routing
                continue
        return None

    def invoke(self, name: str, log: ProvenanceLog | None = None,
               attempt: int = 1, **kwargs: Any) -> CallResult:
        """Resolve, authorize, then dispatch — recording provenance either way."""
        cap = self.registry.get(name)
        if cap is None:
            res = CallResult(capability=name, adapter="none",
                             status=ExecutionStatus.UNAVAILABLE,
                             error=f"capability {name!r} not found in registry")
            self._record(log, res, cap, kwargs, attempt)
            return res

        ad = self.adapter_for(cap)
        if ad is None:
            res = CallResult(capability=name, adapter="none",
                             status=ExecutionStatus.UNAVAILABLE,
                             error=f"no adapter can handle {cap.kind}:{name}")
            self._record(log, res, cap, kwargs, attempt)
            return res

        # ---- POLICY GATE: consulted before any backend runs.
        mode = getattr(ad, "integration_mode", "federated")
        auth = self.kernel.authorize(AuthorizationRequest(
            component_id=name,
            license_spdx=(cap.licenses[0] if cap.licenses else None),
            integration_mode=mode,
            backend=("subprocess" if mode == "federated" else "python"),
            network_hosts=self._network_hosts(cap),
            profile=self.profile,
        ))
        if not auth.allowed:
            res = CallResult(capability=name, adapter=ad.name,
                             status=ExecutionStatus.DENIED,
                             error=auth.reason, authorization=auth)
            self._record(log, res, cap, kwargs, attempt)
            return res

        res = ad.invoke(cap, **kwargs)
        res.authorization = auth
        self._record(log, res, cap, kwargs, attempt)
        return res

    @staticmethod
    def _network_hosts(cap: Any) -> tuple[str, ...]:
        """Extract real hostnames from a capability's declared dependencies.

        The catalogue's `external_deps` mixes hostnames ("rest.ensembl.org") with
        human-readable database labels ("ChEMBL", "openFDA drug labeling"). Only
        the former are network endpoints; treating a label as a host made the
        permission check reject legitimate calls.
        """
        hosts = []
        for dep in (getattr(cap, "external_deps", ()) or ()):
            d = str(dep).strip().lower()
            if not d or " " in d:
                continue
            if "." in d and not d.endswith("."):
                hosts.append(d)
        return tuple(hosts)

    def _record(self, log: ProvenanceLog | None, res: CallResult, cap: Any,
                inputs: dict, attempt: int) -> None:
        if log is None:
            return
        log.record(
            capability=res.capability, adapter=res.adapter,
            integration_mode=getattr(cap, "integration_mode", "n/a") if cap else "n/a",
            inputs=inputs, output=res.value, status=res.status.value,
            error=res.error, duration_s=res.duration_s, attempt=attempt,
            contributing_projects=list(getattr(cap, "contributing_projects", ()) or ()),
            licenses=list(getattr(cap, "licenses", ()) or ()),
            policy_reason=(res.authorization.reason if res.authorization else None),
        )

    # ------------------------------------------------------------------ run
    def run(self, task: str, *, max_steps: int = 5, max_attempts: int = 2,
            step_kwargs: dict[str, Any] | None = None) -> RunReport:
        """Plan, execute, critique — retrying once with failed steps excluded.

        Provenance and results stay 1:1 per attempt (the v1 defect left 2
        provenance entries against 0 results when the first attempt failed).
        `results` holds the final attempt; `all_results` holds every attempt.
        """
        log = ProvenanceLog(catalogue_version=self.catalogue_version)
        excluded: set[str] = set()
        every: list[CallResult] = []
        report: RunReport | None = None
        for attempt in range(1, max_attempts + 1):
            plan = self.planner.plan(task, self.registry, max_steps=max_steps)
            plan.steps = [s for s in plan.steps if s.capability not in excluded]
            results = [self.invoke(step.capability, log=log, attempt=attempt,
                                   **merge_step_arguments(step, step_kwargs))
                       for step in plan.steps]
            every.extend(results)
            critique = self.planner.critique(plan, results)
            report = RunReport(task=task, plan=plan, results=results,
                               all_results=list(every), critique=critique,
                               provenance=log, attempts=attempt)
            if critique.accepted or not plan.steps:
                break
            excluded.update(r.capability for r in results if not r.status.successful)
        assert report is not None
        return report

    def save_trace(self, report: RunReport, path: str | Path) -> Path:
        if report.provenance is None:
            raise ValueError("report has no provenance log")
        return report.provenance.save(path)
