"""The bridge: admit BioScience components into a PSH kernel and its registry."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from psh.contracts import ComponentKind, ComponentManifest as PSHManifest, RiskTier
from psh.labels import Destination, Sensitivity

from ..runtime.agentspec import AgentSpec
from ..runtime.component import ComponentManifest as BioManifest
from .assembly import load_verification
from .component import BridgedComponent
from .manifest import HostPolicy, bridge_manifest, psh_id_for

__all__ = ["BioScienceBridge", "BridgeRefused", "EXEC_PATH"]

#: The isolated entrypoint, as an absolute path: PSH's child environment carries no
#: PYTHONPATH by design, so the script bootstraps its own package from its location.
EXEC_PATH = Path(__file__).resolve().with_name("exec.py")

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "general").lower()).strip("-") or "general"


class BridgeRefused(ValueError):
    """A component could not be admitted, and why."""


class BioScienceBridge:
    """Admits BioScience components as PSH components, one audited decision each.

    ``isolate=True`` declares every admitted component with PSH's ``subprocess`` backend:
    the kernel's ``IsolatedExecutor`` then runs ``exec.py`` in a child process with a
    clean environment behind the egress proxy, and BioScience executes inside it. A
    policy with ``require_isolated_tools`` admits only that mode, which is the point.
    """

    def __init__(self, kernel: Any, runtime: Any, *, spec: AgentSpec | None = None,
                 host_policy: HostPolicy | None = None, isolate: bool = False,
                 local_ceiling: Sensitivity = Sensitivity.PHI,
                 verification: Mapping[str, Mapping[str, Any]] | None = None,
                 events: Any = None, manifest_dir: str | Path | None = None) -> None:
        self.kernel = kernel
        self.runtime = runtime
        self.spec = spec or AgentSpec(name="psh-bridge", permission_profile="biomedical-research")
        self.host_policy = host_policy or HostPolicy()
        self.isolate = isolate
        self.local_ceiling = local_ceiling
        self.verification = dict(verification) if verification is not None else load_verification()
        self.events = events
        config = getattr(kernel, "config", None)
        state_dir = getattr(config, "state_dir", None)
        self.manifest_dir = (Path(manifest_dir) if manifest_dir
                             else (Path(state_dir) / "bioscience" if state_dir else None))
        self._components: dict[str, BridgedComponent] = {}
        self._by_bio_id: dict[str, str] = {}
        self._harnesses: dict[str, PSHManifest] = {}
        self.refusals: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ admit
    def admit(self, bio: BioManifest) -> PSHManifest:
        """One BioScience manifest in, one PSH manifest out, one audit event."""
        if bio.id in self._by_bio_id:
            return self._components[self._by_bio_id[bio.id]].manifest
        if bio.runtime.backend == "none" and bio.kind != "skill":
            raise BridgeRefused(f"{bio.id}: declares no execution backend (catalogue metadata "
                                "only); nothing to invoke")
        errs = bio.validate()
        if errs:
            raise BridgeRefused(f"{bio.id}: invalid manifest: " + "; ".join(errs))
        try:
            psh_id = psh_id_for(bio.id)
        except ValueError as exc:
            raise BridgeRefused(str(exc)) from None
        holder = self._components.get(psh_id)
        if holder is not None and holder.bio.id != bio.id:
            raise BridgeRefused(
                f"{bio.id}: sanitises to {psh_id!r}, already held by {holder.bio.id!r}; "
                "refusing rather than letting one component shadow another")

        source = self._source_for(bio)
        operations = tuple(source.operations) if source is not None else ()
        # The description is text this kernel did not write. Classify it at admission
        # and carry the label on the rendered manifest item, as the MCP adapter does.
        text = bio.description or bio.name or bio.id
        labelled = self.kernel.ingress.ensure(text, origin=f"bioscience:{bio.id}")

        entrypoint = ""
        backend = "python"
        if self.isolate:
            backend = "subprocess"
            entrypoint = self._isolated_entrypoint(bio)
        manifest = bridge_manifest(
            bio, host_policy=self.host_policy, local_ceiling=self.local_ceiling,
            backend=backend, entrypoint=entrypoint, operations=operations,
            verification=self.verification.get(source.key) if source is not None else None,
            description_sensitivity=labelled.label.sensitivity.name)
        component = BridgedComponent(bio, manifest, self.runtime, spec=self.spec,
                                     source=source, events=self.events)
        self._components[manifest.id] = component
        self._by_bio_id[bio.id] = manifest.id
        self._audit("bioscience_component_admitted", component_id=manifest.id,
                    detail={"bio_id": bio.id, "backend": bio.runtime.backend,
                            "destinations": sorted(d.name for d in manifest.destinations),
                            "max_label": manifest.max_label.name,
                            "license": manifest.license_spdx or "unlicensed",
                            "integration_mode": manifest.integration_mode,
                            "isolated": self.isolate,
                            "description_sensitivity": labelled.label.sensitivity.name})
        return manifest

    def admit_all(self, manifests: Iterable[BioManifest] | None = None, *,
                  kinds: Iterable[str] | None = None,
                  backends: Iterable[str] | None = None) -> list[PSHManifest]:
        """Admit everything invocable, recording each refusal instead of stopping."""
        wanted_kinds = set(kinds) if kinds else None
        wanted_backends = set(backends) if backends else None
        pool = list(manifests) if manifests is not None else list(self.runtime.registry)
        out: list[PSHManifest] = []
        for bio in pool:
            if wanted_kinds and bio.kind not in wanted_kinds:
                continue
            if wanted_backends and bio.runtime.backend not in wanted_backends:
                continue
            try:
                out.append(self.admit(bio))
            except BridgeRefused as exc:
                self.refusals.append((bio.id, str(exc)))
        return out

    # --------------------------------------------------------------- registry
    def register_into(self, registry: Any, *, harnesses: bool = True) -> dict[str, int]:
        """Register every admitted component; one harness per domain when asked.

        PSH's registry is two-level by design: a domain harness contributes one manifest
        to top-level retrieval and its capabilities are only ranked once the domain is
        selected. The catalogue's domains map onto that directly, so 2,600 capabilities
        cost the planner a handful of harness descriptions rather than 2,600 lines.
        """
        registered = 0
        for component in self._components.values():
            domain = component.manifest.domain
            if harnesses and domain:
                harness = self._harness_for(domain)
                if harness.id not in getattr(registry, "_top", {}):
                    registry.register(harness)
                registry.register(component, under=harness.id)
            else:
                registry.register(component)
            registered += 1
        return {"components": registered, "harnesses": len(self._harnesses) if harnesses else 0}

    def _harness_for(self, domain: str) -> PSHManifest:
        key = _slug(domain)
        if key in self._harnesses:
            return self._harnesses[key]
        members = [c.manifest for c in self._components.values()
                   if _slug(c.manifest.domain) == key]
        names = [m.name for m in members]
        intents = tuple(dict.fromkeys(n.lower() for n in names))[:12]
        description = (f"BioScience {domain} capabilities ({len(members)}): "
                       + ", ".join(names[:8]) + (" …" if len(names) > 8 else ""))
        harness = PSHManifest(
            id=f"bioscience.domain.{key}", name=f"BioScience: {domain}",
            kind=ComponentKind.HARNESS, publisher="bioscience", description=description[:400],
            intents=intents, domain=domain, tags=("bioscience", "harness"),
            destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI,
            risk_tier=RiskTier.R0_TRIVIAL, license_spdx="MIT", integration_mode="native",
            provenance={"bridge": "bioscience", "members": len(members),
                        "description_sensitivity": "PUBLIC"})
        self._harnesses[key] = harness
        return harness

    # ---------------------------------------------------------------- lookups
    def component(self, psh_id: str) -> BridgedComponent:
        try:
            return self._components[psh_id]
        except KeyError:
            raise KeyError(f"{psh_id!r} was not admitted through this bridge") from None

    def manifests(self) -> list[PSHManifest]:
        return [c.manifest for c in self._components.values()]

    def harnesses(self) -> list[PSHManifest]:
        return list(self._harnesses.values())

    def stats(self) -> dict[str, Any]:
        by_backend: dict[str, int] = {}
        for c in self._components.values():
            by_backend[c.bio.runtime.backend] = by_backend.get(c.bio.runtime.backend, 0) + 1
        return {"admitted": len(self._components), "refused": len(self.refusals),
                "harnesses": len(self._harnesses), "by_backend": by_backend,
                "isolated": self.isolate}

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _source_for(bio: BioManifest) -> Any:
        if not bio.id.startswith("public.connector."):
            return None
        from ..providers.public_apis import BY_KEY
        return BY_KEY.get(bio.id.rsplit(".", 1)[-1])

    def _isolated_entrypoint(self, bio: BioManifest) -> str:
        """``python exec.py --manifest <file>``: the child runs exactly the admitted manifest.

        Written under the kernel's state directory, owner-only, so the child needs no
        catalogue and no environment variable to find its component.
        """
        if self.manifest_dir is None:
            raise BridgeRefused(f"{bio.id}: isolated execution needs a manifest directory "
                                "(the kernel's state_dir or manifest_dir=)")
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        target = self.manifest_dir / f"{psh_id_for(bio.id)}.yaml"
        bio.save(target)
        try:
            import os
            os.chmod(target, 0o600)
        except OSError:                                  # pragma: no cover - platform
            pass
        return f"{sys.executable} {EXEC_PATH} --manifest {target}"

    def _audit(self, event: str, **fields: Any) -> None:
        audit = getattr(self.kernel, "audit", None)
        if audit is None:
            return
        try:
            audit(event, **fields)
        except Exception:  # noqa: BLE001 - an audit failure must not block admission
            pass
