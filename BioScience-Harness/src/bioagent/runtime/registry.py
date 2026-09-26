"""Component registry, resolver and loader.

Division of labour, deliberately strict:

* **Registry** — discover, search, rank, resolve *identity*. It never executes.
* **Resolver** — check the dependency DAG and the live environment, and decide
  whether a component is genuinely READY or UNAVAILABLE (with the reason).
* **Loader** — lazily import an implementation, cache it, and unload it.

The resolver is where v1's central error is corrected: a component is only READY
if its declared requirements are actually satisfied *here*. On this machine 45 of
the 53 third-party imports Biomni's tools need are absent, so most components
resolve to UNAVAILABLE with a precise blocking reason instead of being counted as
available capability.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from ..status import LifecycleState
from .component import ComponentManifest


class ComponentRegistry:
    """An index of component manifests. Pure lookup — no execution."""

    def __init__(self, manifests: Iterable[ComponentManifest] = ()) -> None:
        self._by_id: dict[str, ComponentManifest] = {}
        self._index: dict[str, set[str]] | None = None     # term -> component ids
        self._by_name: dict[str, list[str]] | None = None  # lowercased name -> ids
        for m in manifests:
            self.add(m)

    def _ensure_name_index(self) -> dict[str, list[str]]:
        """Name -> ids, built lazily. Lineage resolution looks up dataset
        requirements by name, and a linear scan per lookup is quadratic over a
        2,567-component catalogue."""
        if self._by_name is None:
            idx: dict[str, list[str]] = {}
            for m in self._by_id.values():
                idx.setdefault(m.name.lower(), []).append(m.id)
            self._by_name = idx
        return self._by_name

    def _ensure_index(self) -> dict[str, set[str]]:
        """Inverted term index over name/description/domain, built lazily."""
        if self._index is None:
            idx: dict[str, set[str]] = {}
            for m in self._by_id.values():
                text = f"{m.name} {m.description} {m.domain} {m.omics_type}".lower()
                for t in set(re.split(r"[^a-z0-9]+", text)):
                    if len(t) > 1:
                        idx.setdefault(t, set()).add(m.id)
            self._index = idx
        return self._index

    # ------------------------------------------------------------------ basics
    def add(self, manifest: ComponentManifest, *, validate: bool = True) -> ComponentManifest:
        if validate:
            errs = manifest.validate()
            if errs:
                manifest.state = LifecycleState.QUARANTINED
                manifest.blocking_reason = "invalid manifest: " + "; ".join(errs)
                self._by_id[manifest.id] = manifest
                # a quarantined manifest is still in the registry, so both lazy
                # indexes are stale until rebuilt
                self._index = None
                self._by_name = None
                return manifest
        if manifest.state is LifecycleState.DISCOVERED:
            manifest.transition(LifecycleState.REGISTERED)
        self._by_id[manifest.id] = manifest
        self._index = None          # invalidate; rebuilt lazily on next search
        self._by_name = None
        return manifest

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[ComponentManifest]:
        return iter(self._by_id.values())

    def __contains__(self, cid: object) -> bool:
        return str(cid) in self._by_id

    def get(self, cid: str) -> ComponentManifest | None:
        return self._by_id.get(cid)

    def by_name(self, name: str) -> list[ComponentManifest]:
        ids = self._ensure_name_index().get(str(name).lower(), ())
        return [self._by_id[i] for i in ids if i in self._by_id]

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        omics_type: str | None = None,
        project: str | None = None,
        state: LifecycleState | None = None,
        backend: str | None = None,
        ready_only: bool = False,
        limit: int = 20,
    ) -> list[ComponentManifest]:
        """Rank components by term overlap, with composable filters."""
        pool = list(self._by_id.values())
        if kind:
            pool = [m for m in pool if m.kind == kind]
        if omics_type:
            pool = [m for m in pool if m.omics_type == omics_type]
        if project:
            p = project.lower()
            pool = [m for m in pool if p in m.provider.project.lower()]
        if state is not None:
            pool = [m for m in pool if m.state is state]
        if backend:
            pool = [m for m in pool if m.runtime.backend == backend]
        if ready_only:
            pool = [m for m in pool if m.state is LifecycleState.READY]
        if not query:
            return pool[:limit]
        terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 1]
        if not terms:
            return pool[:limit]
        # candidate narrowing: only components sharing at least one term
        idx = self._ensure_index()
        hit_ids: set[str] = set()
        for t in terms:
            hit_ids |= idx.get(t, set())
        if hit_ids:
            pool = [m for m in pool if m.id in hit_ids]
        scored: list[tuple[float, ComponentManifest]] = []
        for m in pool:
            name = m.name.lower()
            desc = m.description.lower()
            dom = f"{m.domain} {m.omics_type}".lower()
            s = 0.0
            for t in terms:
                s += name.count(t) * 3.0
                s += 1.5 if t in dom else 0.0
                s += 1.0 if t in desc else 0.0
            if s > 0:
                scored.append((s, m))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [m for _, m in scored[:limit]]

    # --------------------------------------------------------------- summaries
    def counts_by(self, attr: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self._by_id.values():
            v = getattr(m, attr, None)
            key = v.value if hasattr(v, "value") else str(v)
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def state_census(self) -> dict[str, int]:
        return self.counts_by("state")


@dataclass
class Resolution:
    """Outcome of resolving one component against the live environment."""

    component_id: str
    state: LifecycleState
    missing_python: tuple[str, ...] = ()
    missing_binaries: tuple[str, ...] = ()
    missing_datasets: tuple[str, ...] = ()
    missing_components: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    reason: str = ""
    fetchable: bool = False
    fetch_command: str = ""

    @property
    def ready(self) -> bool:
        return self.state is LifecycleState.READY


class DependencyCycle(RuntimeError):
    """Raised when component dependencies form a cycle."""


#: Container runtimes the default backend probe looks for on PATH.
CONTAINER_RUNTIMES = ("docker", "podman", "nerdctl")


@dataclass(frozen=True)
class RuntimeProbe:
    """Whether this machine can actually run a container, and why not if it cannot."""

    binary: str | None = None
    usable: bool = False
    reason: str = ""


#: Memoised result of the live probe. Probing spawns a process, and the resolver asks
#: once per component; `refresh=True` re-measures after the environment changes.
_CONTAINER_PROBE: RuntimeProbe | None = None
_CONTAINER_PROBE_AT: float = 0.0

#: How long a measurement is trusted. Unbounded memoisation would reintroduce, inside one
#: process, exactly the defect the comment on `default_backend_probe` describes: the
#: resolver hardcoded a "no runtime" answer, "so installing Docker changed nothing and
#: container components stayed UNAVAILABLE forever". A long-lived agent that starts before
#: its daemon would be in that position for its whole life. A daemon can also stop, so the
#: positive answer expires on the same clock as the negative one — "it worked a minute ago"
#: is not a measurement.
_CONTAINER_PROBE_TTL_S = 60.0


def probe_container_runtime(*, timeout_s: float = 8.0,
                            refresh: bool = False) -> RuntimeProbe:
    """Measure container availability: the CLI must exist AND the runtime must answer.

    `shutil.which("docker")` finds the **client binary**. It says nothing about whether a
    daemon is reachable, and on a machine with the CLI installed and no daemon running —
    a CI container, a fresh workstation, this very environment — it returns a path and the
    backend reports itself available. `invoke()` then runs `docker run`, the CLI fails with
    "failed to connect to the docker API", the return code is non-zero, and the result is
    classified **FAILED** rather than **UNAVAILABLE**.

    Those two are not interchangeable here. `ExecutionStatus.executed` is True for FAILED
    and False for UNAVAILABLE, so the misclassification asserts that upstream work ran when
    nothing ran at all. Downstream, `EvolutionAgent.analyze_failures` counts the component
    as failing and can propose rewriting a component that is perfectly healthy — the host
    simply has no container daemon. An availability check that reports a capability the
    machine does not have is worse than one that is merely pessimistic.

    So the binary is necessary and not sufficient: the runtime is asked. `info` is the one
    subcommand that is meaningful for all three — it contacts dockerd, and it is also how
    daemonless podman reports a usable graph driver.
    """
    global _CONTAINER_PROBE, _CONTAINER_PROBE_AT
    fresh_enough = (time.monotonic() - _CONTAINER_PROBE_AT) < _CONTAINER_PROBE_TTL_S
    if _CONTAINER_PROBE is not None and fresh_enough and not refresh:
        return _CONTAINER_PROBE

    binary = next((r for r in CONTAINER_RUNTIMES if shutil.which(r)), None)
    if binary is None:
        probe = RuntimeProbe(
            None, False,
            "container backend requires a container runtime (looked for "
            f"{', '.join(CONTAINER_RUNTIMES)} on PATH; none found)")
    else:
        try:
            proc = subprocess.run([binary, "info"], capture_output=True, text=True,
                                  timeout=timeout_s, check=False)
            if proc.returncode == 0:
                probe = RuntimeProbe(binary, True, "")
            else:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                probe = RuntimeProbe(
                    binary, False,
                    f"{binary} is installed but not usable: "
                    f"{detail[-1][:180] if detail else 'it reported an error'}")
        except subprocess.TimeoutExpired:
            probe = RuntimeProbe(binary, False,
                                 f"{binary} did not respond within {timeout_s:g}s")
        except OSError as exc:
            probe = RuntimeProbe(binary, False, f"{binary} could not be executed: {exc}")

    _CONTAINER_PROBE = probe
    _CONTAINER_PROBE_AT = time.monotonic()
    return probe


def default_backend_probe(backend: str) -> tuple[bool, str]:
    """Ask the live machine whether a backend can run, instead of assuming.

    The previous resolver hardcoded "container backend requires a container
    runtime (none available)", so installing Docker changed nothing: container
    components stayed UNAVAILABLE forever. Now the claim is measured.

    It delegates to `probe_container_runtime` rather than repeating the lookup, because it
    and `ContainerBackend.available()` both answered "can this machine run containers?" with
    their own copy of `shutil.which` — and a question answered twice is a question that will
    eventually be answered two different ways.
    """
    if backend == "container":
        probe = probe_container_runtime()
        return (True, "") if probe.usable else (False, probe.reason)
    if backend == "none":
        return False, "no execution backend declared (catalogue metadata only)"
    return True, ""


class Resolver:
    """Checks dependency DAGs and the live environment.

    `dataset_probe` lets a caller declare which dataset ids exist locally, so the
    resolver can distinguish "declared" from "present" without importing anything.
    `backend_probe` answers whether a declared backend can run here; the default
    measures the machine, and `Runtime` supplies one backed by its live
    `BackendRegistry`.
    """

    def __init__(self, registry: ComponentRegistry,
                 dataset_probe: Callable[[str], bool] | None = None,
                 service_probe: Callable[[str], bool] | None = None,
                 backend_probe: Callable[[str], tuple[bool, str]] | None = None) -> None:
        self.registry = registry
        self._dataset_probe = dataset_probe or (lambda _cid: False)
        self._service_probe = service_probe or (lambda _sid: False)
        self._backend_probe = backend_probe or default_backend_probe
        self._resolving: set[str] = set()

    # ------------------------------------------------------------ environment
    @staticmethod
    def python_module_available(mod: str) -> bool:
        root = mod.split(".")[0]
        if root in sys.modules:
            return True
        try:
            return importlib.util.find_spec(root) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False

    @staticmethod
    def binary_available(name: str) -> bool:
        return shutil.which(name) is not None

    # ------------------------------------------------------------------- DAG
    def dependency_order(self, cid: str, _seen: tuple[str, ...] = ()) -> list[str]:
        """Topologically order a component's transitive component deps."""
        if cid in _seen:
            raise DependencyCycle(" -> ".join([*_seen, cid]))
        m = self.registry.get(cid)
        if m is None:
            return []
        order: list[str] = []
        for dep in m.requires.components:
            for sub in self.dependency_order(dep, (*_seen, cid)):
                if sub not in order:
                    order.append(sub)
        if cid not in order:
            order.append(cid)
        return order

    def _unusable_dependencies(self, m: ComponentManifest) -> list[tuple[str, str]]:
        """Required components that are registered but cannot actually run.

        Guarded against recursion: a dependency cycle is reported by
        `dependency_order()`, and re-entering `resolve()` for a component already
        on the stack would otherwise loop forever.
        """
        out: list[tuple[str, str]] = []
        for dep_id in m.requires.components:
            dep = self.registry.get(dep_id)
            if dep is None or dep_id in self._resolving:
                continue          # absent deps are already reported as missing
            if dep.state is LifecycleState.QUARANTINED:
                out.append((dep_id, dep.blocking_reason or "quarantined"))
                continue
            sub = self.resolve(dep_id)
            if sub.state in (LifecycleState.UNAVAILABLE, LifecycleState.QUARANTINED):
                out.append((dep_id, sub.reason or sub.state.value))
        return out

    def _dependency_ids(self, m: ComponentManifest) -> list[str]:
        """Direct dependencies of a manifest: sub-components and the data it reads."""
        return [*m.requires.components, *m.requires.datasets]

    def _lookup(self, dep_id: str) -> ComponentManifest | None:
        """Resolve a dependency reference by id, falling back to name.

        `requires.datasets` holds dataset *names* for catalogue-derived
        components and component ids for hand-written manifests; both must reach
        the same manifest or a lineage check would silently find nothing.
        """
        m = self.registry.get(dep_id)
        if m is not None:
            return m
        by_name = self.registry.by_name(dep_id)
        return by_name[0] if by_name else None

    def dependency_contexts(self, cid: str, *, max_depth: int = 8) -> tuple:
        """The transitive policy surface of everything `cid` consumes.

        Returned as `policy.DependencyContext` entries for
        `AuthorizationRequest.dependencies`, so the kernel can apply
        most-restrictive-wins across the lineage. A dependency that is declared
        but absent from the registry is reported with an unknown licence, which
        fails closed rather than vanishing from the ruling.
        """
        from ..policy import DependencyContext

        root = self.registry.get(cid)
        if root is None:
            return ()
        out: list[DependencyContext] = []
        seen: set[str] = {cid}
        frontier = [(dep, cid, 1) for dep in self._dependency_ids(root)]
        while frontier:
            dep_id, parent, depth = frontier.pop(0)
            if dep_id in seen or depth > max_depth:
                continue
            seen.add(dep_id)
            dm = self._lookup(dep_id)
            path = f"{parent} -> {dep_id}"
            if dm is not None and dm.id == cid:
                # catalogue-derived datasets declare their own name as a
                # requirement; that self-reference is not lineage.
                continue
            if dm is None:
                out.append(DependencyContext(
                    component_id=dep_id, kind="unknown", license_spdx=None,
                    integration_mode="vendor", path=path))
                continue
            out.append(DependencyContext(
                component_id=dm.id, kind=dm.kind, license_spdx=dm.license.spdx,
                integration_mode=dm.license.integration_mode,
                network_hosts=tuple(dm.permissions.network),
                filesystem_read=tuple(dm.permissions.filesystem_read),
                filesystem_write=tuple(dm.permissions.filesystem_write),
                subprocess=bool(dm.permissions.subprocess), path=path))
            frontier += [(sub, dm.id, depth + 1) for sub in self._dependency_ids(dm)]
        return tuple(out)

    def resolve_manifest(self, m: ComponentManifest) -> tuple[bool, str]:
        """Check an unregistered manifest's requirements against the environment.

        Used to score evolution candidates, which are not in the registry yet.
        """
        missing_py = [x for x in m.requires.python if not self.python_module_available(x)]
        missing_bin = [x for x in m.requires.binaries if not self.binary_available(x)]
        missing_ds = [x for x in m.requires.datasets if not self._dataset_probe(x)]
        problems = []
        fetchable, fetch_cmd = False, ""
        if missing_ds and m.runtime.backend == "dataset":
            from ..acquisition.sources import acquisition_for, fetch_command
            if acquisition_for(m) is not None:
                fetchable, fetch_cmd = True, fetch_command(m)
        if missing_py:
            problems.append(f"missing python modules: {', '.join(missing_py[:5])}")
        if missing_bin:
            problems.append(f"missing binaries: {', '.join(missing_bin[:5])}")
        if missing_ds:
            problems.append(("FETCHABLE: " if fetchable else "") +
                            f"missing datasets: {', '.join(missing_ds[:4])}" +
                            (f" — run: {fetch_cmd}" if fetchable else ""))
        backend_ok, backend_reason = self._backend_probe(m.runtime.backend)
        if not backend_ok:
            problems.append(backend_reason or f"backend {m.runtime.backend!r} unavailable")
        return (not problems), "; ".join(problems) or "requirements satisfied"

    # -------------------------------------------------------------- resolve
    def resolve(self, cid: str) -> Resolution:
        """Resolve one component, following its required components recursively."""
        self._resolving.add(cid)
        try:
            return self._resolve(cid)
        finally:
            self._resolving.discard(cid)

    def _resolve(self, cid: str) -> Resolution:
        m = self.registry.get(cid)
        if m is None:
            return Resolution(cid, LifecycleState.UNAVAILABLE, reason="not in registry")
        if m.state is LifecycleState.QUARANTINED:
            return Resolution(cid, LifecycleState.QUARANTINED, reason=m.blocking_reason)

        try:
            order = tuple(self.dependency_order(cid))
        except DependencyCycle as exc:
            m.mark_unavailable(f"dependency cycle: {exc}")
            return Resolution(cid, LifecycleState.UNAVAILABLE, reason=str(exc))

        missing_py = tuple(x for x in m.requires.python if not self.python_module_available(x))
        missing_bin = tuple(x for x in m.requires.binaries if not self.binary_available(x))
        missing_ds = tuple(x for x in m.requires.datasets if not self._dataset_probe(x))
        missing_comp = tuple(x for x in m.requires.components if x not in self.registry)
        # Being *registered* is not being *usable*. Checking only membership let a
        # parent report "dependencies satisfied" while a required component was
        # itself UNAVAILABLE for a missing import — so a top-level workflow could
        # look ready with a broken tool underneath it.
        broken_deps = tuple(self._unusable_dependencies(m))
        missing_svc = tuple(x for x in m.requires.services if not self._service_probe(x))

        problems = []
        fetchable, fetch_cmd = False, ""
        if missing_ds and m.runtime.backend == "dataset":
            from ..acquisition.sources import acquisition_for, fetch_command
            if acquisition_for(m) is not None:
                fetchable, fetch_cmd = True, fetch_command(m)
        if missing_py:
            problems.append(f"missing python modules: {', '.join(missing_py[:6])}")
        if missing_bin:
            problems.append(f"missing binaries: {', '.join(missing_bin[:6])}")
        if missing_ds:
            problems.append(("FETCHABLE: " if fetchable else "") +
                            f"missing datasets: {', '.join(missing_ds[:4])}" +
                            (f" — run: {fetch_cmd}" if fetchable else ""))
        if missing_comp:
            problems.append(f"missing components: {', '.join(missing_comp[:4])}")
        if broken_deps:
            problems.append("unusable dependencies: "
                            + "; ".join(f"{cid} ({why})" for cid, why in broken_deps[:3]))
        if missing_svc:
            problems.append(f"missing services: {', '.join(missing_svc[:4])}")
        backend_ok, backend_reason = self._backend_probe(m.runtime.backend)
        if not backend_ok:
            problems.append(backend_reason or f"backend {m.runtime.backend!r} unavailable here")

        if problems:
            reason = "; ".join(problems)
            m.mark_unavailable(reason)
            return Resolution(cid, LifecycleState.UNAVAILABLE, missing_py, missing_bin,
                              missing_ds, missing_comp, order, reason,
                              fetchable=fetchable, fetch_command=fetch_cmd)

        if m.state in (LifecycleState.REGISTERED, LifecycleState.UNAVAILABLE):
            m.mark_recovered(LifecycleState.AVAILABLE)
        if m.state is LifecycleState.AVAILABLE:
            m.transition(LifecycleState.RESOLVED)
        # Backends that need no import (datasets) are invocable as soon as their
        # declared inputs exist, so RESOLVED already implies READY for them.
        if m.runtime.backend == "dataset" and m.state is LifecycleState.RESOLVED:
            m.transition(LifecycleState.LOADED)
            m.transition(LifecycleState.READY)
        return Resolution(cid, m.state, order=order, reason="dependencies satisfied")


class Loader:
    """Lazily imports component implementations and caches them."""

    def __init__(self, registry: ComponentRegistry, resolver: Resolver | None = None) -> None:
        self.registry = registry
        self.resolver = resolver or Resolver(registry)
        self._cache: dict[str, Any] = {}
        self.load_count = 0
        self.unload_count = 0

    @property
    def loaded_ids(self) -> tuple[str, ...]:
        return tuple(self._cache)

    def load(self, cid: str) -> Any:
        """Import and return the callable for a component, or None if blocked."""
        if cid in self._cache:
            return self._cache[cid]
        m = self.registry.get(cid)
        if m is None:
            return None
        res = self.resolver.resolve(cid)
        if not (res.state is LifecycleState.RESOLVED or res.state is LifecycleState.READY):
            return None
        if m.runtime.backend != "python":
            # Non-import backends have nothing to load. READY is granted only by
            # a successful invocation through the backend (Runtime.invoke), never
            # here — the v2 defect marked mcp/http components READY with no proof.
            return None
        target = m.runtime.entrypoint
        if ":" not in target:
            m.mark_unavailable(f"malformed entrypoint {target!r} (expected 'module:function')")
            return None
        mod_name, _, fn_name = target.partition(":")
        try:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name)
        except Exception as exc:  # noqa: BLE001 - recorded as a blocking reason
            m.mark_unavailable(f"import failed: {type(exc).__name__}: {exc}")
            return None
        self._cache[cid] = fn
        self.load_count += 1
        if m.state is LifecycleState.RESOLVED:
            m.transition(LifecycleState.LOADED)
        if m.state is LifecycleState.LOADED:
            m.transition(LifecycleState.READY)
        return fn

    def unload(self, cid: str) -> bool:
        """Drop a cached implementation, returning the component to LOADED."""
        if cid not in self._cache:
            return False
        del self._cache[cid]
        self.unload_count += 1
        m = self.registry.get(cid)
        if m is not None and m.state is LifecycleState.READY:
            m.transition(LifecycleState.LOADED)
        return True

    def unload_all(self) -> int:
        n = 0
        for cid in list(self._cache):
            n += bool(self.unload(cid))
        return n
