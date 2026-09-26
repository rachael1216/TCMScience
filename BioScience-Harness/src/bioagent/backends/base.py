"""Execution backends — how a component actually runs.

v1's three "integration modes" (vendor / native / federated) conflated *licensing*
with *mechanism*. A backend is the mechanism: a component declares
`runtime.backend`, and the runtime picks the matching backend without the
component knowing which class serves it. Licensing stays in the policy kernel
where it belongs.

Every backend returns a `CallResult` carrying an `ExecutionStatus`, so "resolved
but not dispatched" is representable — the v1 defect that produced false success.
"""

from __future__ import annotations

import abc
import time
from typing import Any

from ..adapters.base import CallResult
from ..runtime.component import ComponentManifest
from ..status import ExecutionStatus


class Backend(abc.ABC):
    """Executes components that declare a matching `runtime.backend`."""

    #: value matched against ComponentManifest.runtime.backend
    backend: str = "none"

    @abc.abstractmethod
    def invoke(self, manifest: ComponentManifest, **kwargs: Any) -> CallResult:
        """Execute the component and report a status."""

    def available(self) -> bool:
        """Whether this backend can run anything on this machine."""
        return True

    def unavailable_reason(self) -> str:
        return ""

    def handles(self, manifest: ComponentManifest) -> bool:
        return manifest.runtime.backend == self.backend

    def rebind(self, loader: Any) -> "Backend":
        """Return a backend that loads implementations through `loader`.

        Only backends that import code need to override this. It exists so an
        unregistered candidate can be executed against a scratch loader instead
        of the production one — otherwise a candidate sharing the incumbent's id
        is silently scored by running the incumbent.
        """
        return self

    # ------------------------------------------------------------------ helpers
    def _result(self, manifest: ComponentManifest, status: ExecutionStatus,
                t0: float, value: Any = None, error: str | None = None,
                metadata: dict | None = None) -> CallResult:
        return CallResult(capability=manifest.id, adapter=f"{self.backend}-backend",
                          status=status, value=value, error=error,
                          duration_s=time.perf_counter() - t0, metadata=metadata)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} backend={self.backend} available={self.available()}>"


class BackendRegistry:
    """Routes a component to the backend that declares its mechanism."""

    def __init__(self, backends: list[Backend] | None = None) -> None:
        self._backends: dict[str, Backend] = {}
        for b in backends or []:
            self.register(b)

    def register(self, backend: Backend) -> None:
        self._backends[backend.backend] = backend

    def get(self, name: str) -> Backend | None:
        return self._backends.get(name)

    def all(self) -> tuple[Backend, ...]:
        return tuple(self._backends.values())

    def for_component(self, manifest: ComponentManifest) -> Backend | None:
        return self._backends.get(manifest.runtime.backend)

    def availability(self) -> dict[str, dict]:
        return {name: {"available": b.available(), "reason": b.unavailable_reason()}
                for name, b in sorted(self._backends.items())}

    def __len__(self) -> int:
        return len(self._backends)
