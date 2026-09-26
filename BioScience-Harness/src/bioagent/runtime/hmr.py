"""Transactional hot-module reload for components.

A naive reload swaps the implementation and hopes. In a scientific setting that is
unacceptable: a broken swap mid-analysis loses the run. So promotion is
transactional — validate schema, resolve dependencies, run a smoke test, and only
then atomically swap. On any failure the previous version keeps serving and the
candidate is quarantined.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..status import LifecycleState
from .component import ComponentManifest, ManifestError
from .registry import ComponentRegistry, Loader, Resolver


@dataclass
class SwapResult:
    """Outcome of one transactional reload attempt."""

    component_id: str
    promoted: bool
    stage_failed: str = ""
    reason: str = ""
    previous_version: str = ""
    new_version: str = ""
    registry_version: int = 0
    duration_s: float = 0.0

    @property
    def rolled_back(self) -> bool:
        return not self.promoted


class HotReloader:
    """Validates a candidate component and swaps it in atomically."""

    #: ordered gates a candidate must pass
    STAGES = ("schema", "dependencies", "security", "smoke_test", "swap")

    def __init__(self, registry: ComponentRegistry, resolver: Resolver | None = None,
                 loader: Loader | None = None,
                 smoke_runner: Callable[[ComponentManifest], tuple[bool, str]] | None = None,
                 security_scan: Callable[[ComponentManifest], tuple[bool, str]] | None = None) -> None:
        self.registry = registry
        self.resolver = resolver or Resolver(registry)
        self.loader = loader or Loader(registry, self.resolver)
        self.registry_version = 1
        self._smoke = smoke_runner or (lambda m: (True, "no smoke test configured"))
        self._security = security_scan or default_security_scan

    def swap(self, candidate: ComponentManifest) -> SwapResult:
        """Attempt to promote `candidate`, keeping the incumbent on any failure."""
        t0 = time.perf_counter()
        cid = candidate.id
        incumbent = self.registry.get(cid)
        prev_version = incumbent.version if incumbent else ""

        def fail(stage: str, reason: str) -> SwapResult:
            candidate.state = LifecycleState.QUARANTINED
            candidate.blocking_reason = f"{stage}: {reason}"
            # incumbent untouched — it keeps serving
            return SwapResult(cid, False, stage, reason, prev_version, candidate.version,
                              self.registry_version, time.perf_counter() - t0)

        # 1. schema
        errs = candidate.validate()
        if errs:
            return fail("schema", "; ".join(errs))

        # 2. security (before any import)
        ok, msg = self._security(candidate)
        if not ok:
            return fail("security", msg)

        # 3. dependencies — resolve the candidate in an isolated scratch registry
        scratch = ComponentRegistry([m for m in self.registry if m.id != cid])
        scratch.add(candidate)
        # Carry the backend probe too. Dropping it silently reverted to
        # `default_backend_probe`, which answers True for every backend except
        # container/none — so a candidate could clear the dependency gate under
        # environment assumptions the real runtime does not hold.
        scratch_resolver = Resolver(scratch, dataset_probe=self.resolver._dataset_probe,
                                    service_probe=self.resolver._service_probe,
                                    backend_probe=self.resolver._backend_probe)
        res = scratch_resolver.resolve(cid)
        if res.state is LifecycleState.UNAVAILABLE:
            return fail("dependencies", res.reason)

        # 4. smoke test
        ok, msg = self._smoke(candidate)
        if not ok:
            return fail("smoke_test", msg)

        # 5. atomic swap — a candidate that was quarantined on an earlier attempt
        #    and has since been repaired must enter the registry clean.
        self.loader.unload(cid)
        candidate.state = LifecycleState.DISCOVERED
        candidate.blocking_reason = ""
        self.registry.add(candidate)
        self.registry_version += 1
        return SwapResult(cid, True, "", "promoted", prev_version, candidate.version,
                          self.registry_version, time.perf_counter() - t0)


#: Patterns that must not appear in a candidate's declared metadata.
_SUSPICIOUS = (
    "eval(", "exec(", "__import__", "subprocess.Popen", "os.system",
    "shutil.rmtree", "rm -rf", "curl ", "wget ", "base64.b64decode",
)


def default_security_scan(manifest: ComponentManifest) -> tuple[bool, str]:
    """Reject a candidate whose declared surface looks unsafe.

    This is a metadata-level check, not a sandbox: it catches obvious attempts to
    smuggle shell commands through a manifest field. Real containment is the
    executor's job, and the executor is honest about what it cannot enforce.
    """
    blob = " ".join([
        manifest.description, manifest.runtime.entrypoint, manifest.runtime.image,
        manifest.signature, " ".join(manifest.permissions.filesystem_write),
    ]).lower()
    for pat in _SUSPICIOUS:
        if pat.lower() in blob:
            return False, f"suspicious pattern in manifest metadata: {pat!r}"
    if manifest.permissions.filesystem_write and any(
            p.startswith("/") and not p.startswith("/tmp") for p in manifest.permissions.filesystem_write):
        return False, "manifest requests write access outside the workspace"
    return True, "no suspicious patterns"


class LazyComponentSet:
    """Retrieve → policy-filter → load only the top few → unload after use.

    Loading every component up front is what makes a 2,567-component system
    impossible to run: imports, MCP schemas and tool descriptions would all be
    paid before the first task. Here context is the resolved component set for
    *this* task, not everything the system knows.
    """

    def __init__(self, registry: ComponentRegistry, loader: Loader,
                 policy_filter: Callable[[ComponentManifest], bool] | None = None,
                 backends: Any = None) -> None:
        self.registry = registry
        self.loader = loader
        self._policy_filter = policy_filter or (lambda m: True)
        self.backends = backends
        self.stats: dict[str, int] = {"retrieved": 0, "policy_passed": 0, "loaded": 0,
                                      "unloaded": 0, "backend_unavailable": 0}

    def _backend_can_run(self, m: ComponentManifest) -> bool:
        """False when the component's mechanism is known to be unavailable here.

        Without a backend registry the set is permissive (v2 behaviour); with
        one, a component whose backend cannot run is never selected — the v2
        defect handed the planner four MCP tools with no dispatcher bound.
        """
        if self.backends is None:
            return True
        b = self.backends.for_component(m)
        return bool(b is not None and b.available())

    def acquire(self, query: str, *, candidates: int = 30, top_k: int = 5,
                kind: str | None = None) -> list[ComponentManifest]:
        """Return at most `top_k` loaded components for a task."""
        found = self.registry.search(query, kind=kind, limit=candidates)
        self.stats["retrieved"] += len(found)
        allowed = [m for m in found if self._policy_filter(m)]
        self.stats["policy_passed"] += len(allowed)
        chosen: list[ComponentManifest] = []
        for m in allowed:
            if len(chosen) >= top_k:
                break
            if not self._backend_can_run(m):
                self.stats["backend_unavailable"] += 1
                continue
            if m.runtime.backend == "python":
                if self.loader.load(m.id) is None:
                    continue
                self.stats["loaded"] += 1
            chosen.append(m)
        return chosen

    def release(self) -> int:
        n = self.loader.unload_all()
        self.stats["unloaded"] += n
        return n
