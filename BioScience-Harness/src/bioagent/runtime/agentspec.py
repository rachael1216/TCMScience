"""AgentSpec and Runtime — the agent is a component, not the system's centre.

v1's `BioAgent` was the centre of gravity: it owned the planner, the adapters and
the loop, so every new agent shape meant a new class. Here an agent is a
declarative spec (which planner, which permission profile, which backends), and
one `Runtime` executes any spec. Multi-agent topology becomes composition of
specs rather than a fixed class hierarchy.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..adapters.base import CallResult
from ..backends.base import BackendRegistry
from ..planners.base import (Critique, Plan, PlannerPlugin, get_planner,
                             merge_step_arguments)
from ..policy import AuthorizationRequest, PolicyKernel
from ..status import ExecutionStatus, LifecycleState, RunOutcome, ScientificVerdict
from .component import ComponentManifest
from .events import EventLog, EventType
from .hmr import LazyComponentSet
from .registry import ComponentRegistry, Loader, Resolver


@dataclass
class AgentSpec:
    """Declarative description of an agent. Data, not code."""

    name: str
    planner: str = "heuristic"
    permission_profile: str = "biomedical-research"
    max_steps: int = 5
    max_attempts: int = 2
    top_k_components: int = 5
    candidate_pool: int = 30
    description: str = ""
    #: allow Runtime.invoke to download a FETCHABLE dataset on first use
    auto_fetch: bool = False
    auto_fetch_max_bytes: int = 256 * 1024 * 1024

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSpec":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class RunReport:
    """Outcome of one spec execution.

    Execution and acceptance are reported separately: `execution_outcome` says
    whether the steps ran, `verdict` says whether the critique accepted what they
    produced, and `ok` requires both.
    """

    task: str
    spec: AgentSpec
    plan: Plan
    results: list[CallResult] = field(default_factory=list)
    all_results: list[CallResult] = field(default_factory=list)
    critique: Critique | None = None
    events: EventLog | None = None
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
            "task": self.task, "agent": self.spec.name, "planner": self.plan.planner,
            "n_steps": len(self.plan),
            "n_executed": sum(r.executed for r in self.results),
            "n_succeeded": sum(r.status.successful for r in self.results),
            "execution_outcome": self.execution_outcome,
            "scientific_verdict": self.verdict,
            "outcome": self.execution_outcome, "accepted": self.ok,
            "critique": self.critique.reason if self.critique else None,
            "attempts": self.attempts,
            "statuses": {r.capability: r.status.value for r in self.results},
            "n_events": len(self.events) if self.events else 0,
        }


class Runtime:
    """Executes any AgentSpec against a component registry.

    The invocation path is fixed and cannot be bypassed:
        retrieve -> resolve -> POLICY -> backend -> event log
    """

    def __init__(self, registry: ComponentRegistry, backends: BackendRegistry,
                 kernel: PolicyKernel | None = None, resolver: Resolver | None = None,
                 loader: Loader | None = None, catalogue_version: str = "unknown",
                 git_commit: str = "", downloader: Any = None) -> None:
        self.downloader = downloader
        self.registry = registry
        self.backends = backends
        self.kernel = kernel or PolicyKernel()
        # A resolver we build ourselves asks *this runtime's* backends whether a
        # mechanism can run, rather than assuming (the resolver used to declare
        # every container component unavailable regardless of the machine).
        self.resolver = resolver or Resolver(registry, backend_probe=self._backend_probe,
                                             dataset_probe=self._dataset_probe)
        self.loader = loader or Loader(registry, self.resolver)
        self.catalogue_version = catalogue_version
        self.git_commit = git_commit

    def _dataset_probe(self, name: str) -> bool:
        """Is this dataset present for a backend that could actually read it?

        Without this the resolver's default probe answered False for everything,
        so dataset components were unavailable even with the file on disk, and a
        successful auto-fetch changed nothing.
        """
        for backend in self.backends.all():
            probe = getattr(backend, "has_dataset", None)
            if callable(probe) and probe(name):
                return True
        return False

    def _backend_probe(self, backend: str) -> tuple[bool, str]:
        """Can this runtime actually execute that mechanism, here and now?"""
        b = self.backends.get(backend)
        if b is None:
            return False, f"no backend registered for {backend!r}"
        if not b.available():
            return False, (b.unavailable_reason() or f"backend {backend!r} is unavailable here")
        return True, ""

    # ------------------------------------------------------------------ policy
    def _authorization_request(self, m: ComponentManifest,
                               spec: AgentSpec) -> AuthorizationRequest:
        """Build the full request: this component *and* everything it consumes.

        Two things used to be left out. The manifest's declared filesystem
        permissions were never passed, so the kernel could not rule on them; and
        the dependency closure was never passed, so a permissively licensed tool
        whose `requires.datasets` named a forbidden dataset was authorized on its
        own merits alone.
        """
        return AuthorizationRequest(
            component_id=m.id, license_spdx=m.license.spdx,
            integration_mode=m.license.integration_mode,
            backend=m.runtime.backend, network_hosts=tuple(m.permissions.network),
            profile=spec.permission_profile,
            filesystem_read=tuple(m.permissions.filesystem_read),
            filesystem_write=tuple(m.permissions.filesystem_write),
            subprocess=bool(m.permissions.subprocess),
            dependencies=self.resolver.dependency_contexts(m.id))

    # ------------------------------------------------------------------ invoke
    def invoke(self, component_id: str, *, spec: AgentSpec, events: EventLog | None = None,
               parent_event: str | None = None, attempt: int = 1, idempotency_key: str = "",
               **kwargs: Any) -> CallResult:
        """Invoke a registered component through resolution, policy and its backend.

        ``spec``, ``events``, ``parent_event``, ``attempt`` and ``idempotency_key`` are
        the runtime's own keywords and never reach the entrypoint. ``idempotency_key`` is
        what a governing loop attaches to a call so a replay can be recognised: it is
        recorded on the ``ToolCalled`` event and on the result's metadata, where a
        side-effecting backend can read it, and it is never an argument of the call.
        """
        bookkeeping: dict[str, Any] = {"attempt": attempt}
        if idempotency_key:
            bookkeeping["idempotency_key"] = idempotency_key
        m = self.registry.get(component_id)
        if m is None:
            res = CallResult(capability=component_id, adapter="none",
                             status=ExecutionStatus.UNAVAILABLE,
                             error=f"component {component_id!r} not in registry")
            if events:
                events.emit(EventType.TOOL_CALLED, parent=parent_event,
                            component_id=component_id, status=res.status.value,
                            inputs=kwargs, detail={**bookkeeping, "error": res.error})
            return res

        ev_retrieved = None
        if events:
            ev_retrieved = events.emit(EventType.COMPONENT_RETRIEVED, parent=parent_event,
                                       component_id=m.id, component_version=m.version,
                                       status=m.state.value).event_id

        resolution = self.resolver.resolve(m.id)
        if resolution.fetchable and spec.auto_fetch and self.downloader is not None:
            # The fetch runs under *this spec's* profile. Passing the harness
            # default here let an offline-analysis agent with auto_fetch=True
            # complete a download and only then be denied at invoke time — the
            # bytes were already on disk, so the profile had been bypassed.
            fetched = self.fetch(m, max_bytes=spec.auto_fetch_max_bytes, events=events,
                                 parent_event=ev_retrieved,
                                 profile=spec.permission_profile)
            if fetched.ok:
                resolution = self.resolver.resolve(m.id)
            else:
                # keep the FETCHABLE resolution but say why the automatic fetch did not happen
                resolution.reason = f"{resolution.reason} | auto-fetch {fetched.status.value}: {fetched.error}"
        if events:
            ev_retrieved = events.emit(EventType.COMPONENT_RESOLVED, parent=ev_retrieved,
                                       component_id=m.id, component_version=m.version,
                                       status=resolution.state.value,
                                       detail={"reason": resolution.reason}).event_id
        if resolution.state in (LifecycleState.UNAVAILABLE, LifecycleState.QUARANTINED):
            res = CallResult(capability=m.id, adapter="none",
                             status=ExecutionStatus.UNAVAILABLE, error=resolution.reason)
            if events:
                events.emit(EventType.TOOL_CALLED, parent=ev_retrieved, component_id=m.id,
                            component_version=m.version, status=res.status.value,
                            inputs=kwargs, detail={**bookkeeping, "error": resolution.reason})
            return res

        # ---- POLICY GATE
        auth = self.kernel.authorize(self._authorization_request(m, spec))
        ev_policy = None
        if events:
            ev_policy = events.emit(EventType.POLICY_CHECKED, parent=ev_retrieved,
                                    component_id=m.id, component_version=m.version,
                                    status=("ALLOW" if auth.allowed else "DENY"),
                                    policy_ruling=auth.reason).event_id
        if not auth.allowed:
            res = CallResult(capability=m.id, adapter="policy-kernel",
                             status=ExecutionStatus.DENIED, error=auth.reason,
                             authorization=auth)
            if events:
                events.emit(EventType.TOOL_CALLED, parent=ev_policy, component_id=m.id,
                            component_version=m.version, status=res.status.value,
                            inputs=kwargs, policy_ruling=auth.reason,
                            detail={**bookkeeping, "denied_rules": list(auth.denied_rules)})
            return res

        backend = self.backends.for_component(m)
        if backend is None:
            res = CallResult(capability=m.id, adapter="none",
                             status=ExecutionStatus.UNAVAILABLE,
                             error=f"no backend registered for {m.runtime.backend!r}")
        else:
            res = backend.invoke(m, **kwargs)
            res.authorization = auth
            if idempotency_key:
                res.metadata = {**dict(res.metadata or {}), "idempotency_key": idempotency_key}
            # A successful invocation is the only proof of READY. Advance the
            # lifecycle from evidence rather than from optimism.
            if res.status is ExecutionStatus.SUCCEEDED:
                if m.state is LifecycleState.RESOLVED:
                    m.transition(LifecycleState.LOADED)
                if m.state is LifecycleState.LOADED:
                    m.transition(LifecycleState.READY)
        if events:
            events.emit(EventType.TOOL_CALLED, parent=ev_policy, component_id=m.id,
                        component_version=m.version, status=res.status.value,
                        inputs=kwargs, output=res.value, policy_ruling=auth.reason,
                        detail={**bookkeeping, "adapter": res.adapter,
                                "error": res.error})
        return res

    def invoke_manifest(self, manifest: ComponentManifest, *, spec: AgentSpec,
                        events: EventLog | None = None, parent_event: str | None = None,
                        **kwargs: Any) -> CallResult:
        """Invoke a manifest that is not (or not yet) in the registry.

        This is the path for scoring an evolution candidate, and it goes through
        the same gates as `invoke()`: resolve, POLICY, backend. Two defects made
        that necessary.

        *The candidate was not the thing being executed.* Evolution scored a
        candidate by handing the manifest straight to a backend, but
        `PythonBackend` resolves an entrypoint via `Loader.load(manifest.id)`,
        and a candidate normally carries the incumbent's id — so the loader
        looked the id up in the production registry and ran the **incumbent**.
        A benchmark that reported "the candidate improved" was comparing the old
        version against itself. The fix is a scratch execution context: a
        registry holding the candidate in place of the incumbent, its own
        resolver and loader, and backends rebound to that loader, so the only
        implementation reachable is the candidate's.

        *Policy was skipped.* The old path went `resolve_manifest -> invoke`,
        never consulting the kernel, so a candidate that production would DENY
        still executed during benchmarking. Authorization happens here, on the
        same lineage-propagating request `invoke()` builds.
        """
        scratch_registry = ComponentRegistry(
            [m for m in self.registry if m.id != manifest.id])
        scratch_registry.add(manifest)
        scratch_resolver = Resolver(
            scratch_registry,
            dataset_probe=self.resolver._dataset_probe,
            service_probe=self.resolver._service_probe,
            backend_probe=self.resolver._backend_probe)
        scratch_loader = Loader(scratch_registry, scratch_resolver)
        scratch_backends = BackendRegistry(
            [b.rebind(scratch_loader) for b in self.backends.all()])
        scratch = Runtime(scratch_registry, scratch_backends, kernel=self.kernel,
                          resolver=scratch_resolver, loader=scratch_loader,
                          catalogue_version=self.catalogue_version,
                          git_commit=self.git_commit, downloader=self.downloader)
        return scratch.invoke(manifest.id, spec=spec, events=events,
                              parent_event=parent_event, **kwargs)

    # --------------------------------------------------------------------- run
    def run(self, task: str, spec: AgentSpec, *, planner: PlannerPlugin | None = None,
            step_kwargs: dict | None = None) -> RunReport:
        events = EventLog(catalogue_version=self.catalogue_version,
                          git_commit=self.git_commit)
        root = events.emit(EventType.TASK_CREATED, inputs={"task": task},
                           detail={"agent": spec.name, "planner": spec.planner}).event_id
        pl = planner or self._planner_for(spec)
        excluded: set[str] = set()
        every: list[CallResult] = []
        report: RunReport | None = None
        for attempt in range(1, spec.max_attempts + 1):
            plan = pl.plan(task, self.registry, max_steps=spec.max_steps)
            plan.steps = [s for s in plan.steps if s.component_id not in excluded]
            results = [self.invoke(s.component_id, spec=spec, events=events,
                                   parent_event=root, attempt=attempt,
                                   **merge_step_arguments(s, step_kwargs))
                       for s in plan.steps]
            every.extend(results)
            crit = pl.critique(plan, results)
            report = RunReport(task=task, spec=spec, plan=plan, results=results,
                               all_results=list(every), critique=crit, events=events,
                               attempts=attempt)
            if crit.accepted or not plan.steps:
                break
            excluded.update(r.capability for r in results if not r.status.successful)
        assert report is not None
        events.emit(EventType.RUN_COMPLETED, parent=root, status=report.execution_outcome,
                    detail={**report.summary(), "scientific_verdict": report.verdict})
        return report

    def _planner_for(self, spec: AgentSpec) -> PlannerPlugin:
        """Instantiate the spec's planner, handing it backend awareness when accepted."""
        try:
            return get_planner(spec.planner, backends=self.backends)
        except TypeError:
            return get_planner(spec.planner)

    def fetch(self, m: ComponentManifest, *, max_bytes: int | None = None, confirm: bool = False,
              events: EventLog | None = None, parent_event: str | None = None,
              profile: str | None = None, spec: AgentSpec | None = None) -> CallResult:
        """Download a FETCHABLE dataset through the acquisition layer.

        The host is checked by the policy kernel *before* any socket is opened
        (an acquisition URL on an un-allowlisted host is DENIED), and the
        downloader's size gate applies.

        `profile` is the permission profile the download runs under. It used to
        be hardcoded to "biomedical-research", which meant an agent running the
        offline-analysis profile could still reach the network through
        auto-fetch. Callers pass their own profile; with none given the kernel's
        most restrictive built-in profile applies, so the failure mode of
        forgetting is a refused download rather than an unauthorized one.
        """
        import time as _t
        from ..acquisition.sources import acquisition_for

        t0 = _t.perf_counter()
        spec_ = acquisition_for(m)
        if spec_ is None or self.downloader is None:
            return CallResult(capability=m.id, adapter="acquisition", status=ExecutionStatus.UNAVAILABLE,
                              error="component declares no acquisition source or no downloader is bound")
        effective_profile = profile or (spec.permission_profile if spec else None) or "offline-analysis"
        auth = self.kernel.authorize(AuthorizationRequest(
            component_id=m.id, license_spdx=m.license.spdx, integration_mode="native",
            backend="http", network_hosts=(spec_.host,), profile=effective_profile,
            dependencies=self.resolver.dependency_contexts(m.id)))
        if not auth.allowed:
            res = CallResult(capability=m.id, adapter="acquisition", status=ExecutionStatus.DENIED,
                             error=auth.reason, authorization=auth)
        else:
            try:
                if max_bytes is not None and (spec_.expected_bytes or 0) > max_bytes and not confirm:
                    raise RuntimeError(f"{spec_.filename} ({spec_.expected_bytes} B) exceeds the "
                                       f"auto-fetch limit of {max_bytes} B; fetch it explicitly")
                dl = self.downloader.fetch(spec_.url, spec_.filename, checksum=spec_.checksum,
                                           expected_bytes=spec_.expected_bytes, confirm=confirm)
                res = CallResult(capability=m.id, adapter="acquisition", status=ExecutionStatus.SUCCEEDED,
                                 value={"path": str(dl.path), "bytes": dl.bytes, "verified": dl.verified,
                                        "checksum": dl.checksum, "from_cache": dl.from_cache},
                                 duration_s=_t.perf_counter() - t0, authorization=auth)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                res = CallResult(capability=m.id, adapter="acquisition", status=ExecutionStatus.FAILED,
                                 error=f"{type(exc).__name__}: {exc}", duration_s=_t.perf_counter() - t0,
                                 authorization=auth)
        if events:
            events.emit(EventType.ARTIFACT_CREATED if res.ok else EventType.TOOL_CALLED,
                        parent=parent_event, component_id=m.id, component_version=m.version,
                        status=res.status.value, inputs={"url": spec_.url}, output=res.value,
                        policy_ruling=auth.reason, dataset_hash=(res.value or {}).get("checksum", ""),
                        detail={"adapter": "acquisition", "error": res.error})
        return res

    def lazy_set(self, spec: AgentSpec) -> LazyComponentSet:
        """A per-task component set honouring this spec's policy profile."""
        def allowed(m: ComponentManifest) -> bool:
            return self.kernel.authorize(self._authorization_request(m, spec)).allowed
        return LazyComponentSet(self.registry, self.loader, policy_filter=allowed,
                                backends=self.backends)
