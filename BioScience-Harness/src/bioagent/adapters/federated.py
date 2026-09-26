"""Federated adapter — invokes an upstream project in its own process.

This is how capabilities from projects that grant NO license (OrigeneMCP,
OriGene, BioMedAgent, CellAgent, CRISPR-GPT) are reached: their code is never
imported or copied into this package. It stays where it was installed, runs in
a separate interpreter, and only data crosses the boundary.

The adapter deliberately does not install, build, or execute upstream setup
scripts. It requires that the user has already installed the upstream project
themselves and points at that installation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .base import Adapter, AdapterError, CallResult
from ..status import ExecutionStatus


class FederatedProcessAdapter(Adapter):
    """Runs an upstream project as a subprocess and exchanges JSON."""

    integration_mode = "adapter-only"
    requires_network = True

    def __init__(
        self,
        name: str,
        project_root: str | Path | None = None,
        interpreter: str | None = None,
        timeout_s: float = 120.0,
        capability_names: Sequence[str] = (),
    ) -> None:
        self.name = name
        self.project_root = Path(project_root) if project_root else None
        self.interpreter = interpreter or sys.executable
        self.timeout_s = timeout_s
        self._capability_names = {c.lower() for c in capability_names}

    def available(self) -> bool:
        """True only when the upstream project is actually installed."""
        return bool(self.project_root and self.project_root.is_dir()) and bool(
            shutil.which(self.interpreter) or Path(self.interpreter).exists()
        )

    def can_handle(self, capability: Any) -> bool:
        nm = str(getattr(capability, "name", capability)).lower()
        if self._capability_names:
            return nm in self._capability_names
        projects = {p.lower() for p in getattr(capability, "contributing_projects", ())}
        return self.name.lower() in projects

    def invoke(self, capability: Any, *, code: str | None = None, **kwargs: Any) -> CallResult:
        """Run ``code`` in the upstream project's interpreter.

        No upstream installer is executed; if the project is not installed the
        call fails loudly rather than silently degrading to a fake result.
        """
        t0 = time.perf_counter()
        cap_name = str(getattr(capability, "name", capability))
        if not self.available():
            return CallResult(
                capability=cap_name,
                adapter=self.name,
                status=ExecutionStatus.FAILED,
                error=(
                    f"upstream project {self.name!r} is not installed at "
                    f"{self.project_root}. This adapter never installs or copies "
                    "upstream code; install it yourself and re-point the adapter."
                ),
                duration_s=time.perf_counter() - t0,
            )
        if code is None:
            return CallResult(
                capability=cap_name, adapter=self.name, status=ExecutionStatus.FAILED,
                error="no code supplied for federated invocation",
                duration_s=time.perf_counter() - t0,
            )
        try:
            proc = subprocess.run(  # noqa: S603 - explicit interpreter, no shell
                [self.interpreter, "-c", code],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
            ok = proc.returncode == 0
            out = proc.stdout.strip()
            try:
                value = json.loads(out) if out else None
            except json.JSONDecodeError:
                value = {"stdout": out[:4000]}
            return CallResult(
                capability=cap_name,
                adapter=self.name,
                status=(ExecutionStatus.SUCCEEDED if ok else ExecutionStatus.FAILED),
                value=value,
                error=None if ok else proc.stderr.strip()[:2000],
                duration_s=time.perf_counter() - t0,
                metadata={"returncode": proc.returncode},
            )
        except subprocess.TimeoutExpired:
            return CallResult(
                capability=cap_name, adapter=self.name, status=ExecutionStatus.FAILED,
                error=f"timed out after {self.timeout_s}s",
                duration_s=time.perf_counter() - t0,
            )


class NativeConnectorAdapter(Adapter):
    """Routes a capability to a platform-native MCP connector.

    1,084 of the 2,567 catalogued capabilities (42%) have an equivalent among
    the host platform's biomedical connectors — including 872 tools whose only
    upstream implementation is unlicensed. Routing those here avoids the
    licensing problem entirely.

    The adapter records the intended connector and method; the host environment
    performs the actual MCP call, so no upstream code is involved.
    """

    integration_mode = "native"
    requires_network = True

    def __init__(self, name: str = "native-mcp", dispatcher: Any = None) -> None:
        self.name = name
        self._dispatcher = dispatcher

    def can_handle(self, capability: Any) -> bool:
        return bool(getattr(capability, "native_connectors", ()))

    def invoke(self, capability: Any, **kwargs: Any) -> CallResult:
        t0 = time.perf_counter()
        cap_name = str(getattr(capability, "name", capability))
        connectors = list(getattr(capability, "native_connectors", ()))
        if not connectors:
            return CallResult(
                capability=cap_name, adapter=self.name, status=ExecutionStatus.FAILED,
                error="capability has no native connector mapping",
                duration_s=time.perf_counter() - t0,
            )
        target = connectors[0]
        if self._dispatcher is None:
            # No live MCP dispatcher: return the resolved routing decision.
            value = {
                "resolved_connector": target,
                "alternatives": connectors[1:],
                "arguments": kwargs,
                "dispatched": False,
                "note": "no dispatcher bound; routing resolved but not executed",
            }
            # THE v1 BUG: this returned ok=True. Routing was worked out, but no
            # upstream call happened, so the only truthful status is RESOLVED.
            return CallResult(
                capability=cap_name, adapter=self.name,
                status=ExecutionStatus.RESOLVED, value=value,
                duration_s=time.perf_counter() - t0,
                metadata={"connector": target},
            )
        try:
            value = self._dispatcher(target, cap_name, **kwargs)
            return CallResult(
                capability=cap_name, adapter=self.name, status=ExecutionStatus.SUCCEEDED, value=value,
                duration_s=time.perf_counter() - t0, metadata={"connector": target},
            )
        except Exception as exc:  # noqa: BLE001
            return CallResult(
                capability=cap_name, adapter=self.name, status=ExecutionStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_s=time.perf_counter() - t0,
            )
