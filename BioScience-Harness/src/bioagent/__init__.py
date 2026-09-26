"""bioagent — a federating biomedical agent over 16 open-source agent projects.

Contains no third-party source code: capability metadata is extracted by static
analysis, and capabilities are invoked through adapters that respect each
upstream project's license.

The public names below are loaded on first use rather than at import. The v1 surface
(``BioAgent``, the pandas-backed ``CapabilityRegistry``) pulls in pandas, and a process
that only needs the v2 runtime — an isolated child running one component under a clean
environment, a test over the connector table — should not pay for, or depend on, a
library it never calls.
"""

from __future__ import annotations

import importlib
from typing import Any

from .status import ExecutionStatus, LifecycleState, RunOutcome, ScientificVerdict

__version__ = "0.2.6"

_LAZY: dict[str, str] = {
    "BioAgent": ".agent", "RunReport": ".agent",
    "SandboxExecutor": ".core.executor", "HardenedExecutor": ".core.executor",
    "Plan": ".core.planner", "Planner": ".core.planner", "PlanStep": ".core.planner",
    "RetrievalPlanner": ".core.planner",
    "ProvenanceLog": ".core.provenance",
    "Authorization": ".policy", "AuthorizationRequest": ".policy", "LicensePolicy": ".policy",
    "PolicyDecision": ".policy", "PolicyKernel": ".policy",
    "Capability": ".registry", "CapabilityRegistry": ".registry",
}

__all__ = [
    "BioAgent",
    "RunReport",
    "Capability",
    "CapabilityRegistry",
    "Plan",
    "PlanStep",
    "Planner",
    "RetrievalPlanner",
    "ProvenanceLog",
    "SandboxExecutor",
    "HardenedExecutor",
    "ExecutionStatus",
    "LifecycleState",
    "RunOutcome",
    "ScientificVerdict",
    "PolicyKernel",
    "LicensePolicy",
    "PolicyDecision",
    "Authorization",
    "AuthorizationRequest",
]


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(target, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
