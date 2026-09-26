"""PSH ⊕ BioScience: the capability plane, admitted into the trusted kernel.

The two packages were built for different halves of one system. PSH-Harness is a
trusted kernel: classification at ingress, an authority lattice, gates in front of every
model call, tool call and delegation, and a bounded agent loop that cannot act except
through them. BioScience-Harness is a capability plane: a catalogue of 2,567 biomedical
capabilities from 16 upstream projects, fifty-nine live public data sources with typed
operations, a resolver that measures what can actually run here, a policy kernel for
licences and lineage, and a self-evolution pipeline. Neither is the other's centre.

This package is the seam, and it obeys one asymmetry: **it depends on PSH; PSH never
depends on it.** A BioScience component becomes a PSH ``ComponentManifest`` with the
label, destination, licence and risk dimensions the PSH gates rule on, derived from what
the BioScience manifest declares — a public API is a ``PUBLIC_REMOTE`` destination with a
de-identified ceiling, a local tool keeps the local ceiling, a licence string is
normalised onto the SPDX ids the lattice knows and otherwise left unlicensed. Its
``invoke`` runs through BioScience's own runtime, so the BioScience policy kernel still
rules on licence and lineage, and BioScience's execution statuses become PSH's
exceptions. So a call crosses **both** kernels, in order: PSH classifies the payload and
gates it against the destination; BioScience resolves, authorises and executes; PSH
labels the result as the join of what went in and what came back.

Nothing here adds a gate, and nothing here reaches into ``psh.kernel``: the bridge
receives a kernel object and calls its public surface. ``bioagent.evolution.boundary``
keeps it that way from the other side — a proposal that targets the trusted plane is
quarantined before it is tested.

Usage::

    from psh.kernel import TrustedKernel
    from psh.capabilities import CapabilityRegistry
    from bioagent.psh import BioScienceBridge, default_runtime

    kernel = TrustedKernel(config, policy=policy)
    bridge = BioScienceBridge(kernel, default_runtime())
    bridge.admit_all()                      # every invocable component and connector
    registry = CapabilityRegistry()
    bridge.register_into(registry)          # one harness per domain, capabilities beneath

The registry then serves PSH's planner and loop; ``registry.resolve()`` narrows to the
domains a task needs before it ranks capabilities, so a 2,600-capability catalogue costs
a handful of manifests of context rather than thousands.
"""

from __future__ import annotations

import importlib
from typing import Any

from .arguments import ArgumentError, arguments_for
from .assembly import default_runtime, load_catalogue_rows, load_verification
from .guard import BoundaryViolation, KernelBoundary

#: Names that need PSH-Harness importable. They load on first use, so the parts of this
#: package that do not need PSH — the runtime assembly, the argument mapping, the kernel
#: boundary — work in a process that has BioScience alone, which is exactly what the
#: isolated entrypoint runs in.
_NEEDS_PSH: dict[str, str] = {
    "BioScienceBridge": ".bridge", "BridgeRefused": ".bridge", "EXEC_PATH": ".bridge",
    "BridgedComponent": ".component",
    "HostPolicy": ".manifest", "bridge_manifest": ".manifest", "ceiling_for": ".manifest",
    "destinations_for": ".manifest", "normalise_spdx": ".manifest", "psh_id_for": ".manifest",
}

__all__ = [
    "ArgumentError", "BioScienceBridge", "BridgeRefused", "BridgedComponent",
    "BoundaryViolation", "EXEC_PATH", "HostPolicy", "KernelBoundary", "arguments_for",
    "bridge_manifest", "ceiling_for", "default_runtime", "destinations_for",
    "load_catalogue_rows", "load_verification", "normalise_spdx", "psh_id_for",
]


def __getattr__(name: str) -> Any:
    target = _NEEDS_PSH.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module(target, __name__)
    except ImportError as exc:
        if exc.name and exc.name.split(".")[0] == "psh":
            raise ImportError(
                f"bioagent.psh.{name} needs the PSH-Harness package (`pip install -e "
                "../PSH-Harness` in the monorepo); the rest of bioagent works without it"
            ) from exc
        raise
    value = getattr(module, name)
    globals()[name] = value
    return value
