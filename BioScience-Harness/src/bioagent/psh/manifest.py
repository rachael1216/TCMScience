"""Derive a PSH manifest from a BioScience one.

The two manifests describe different things. BioScience's says how a component runs
(backend, entrypoint, image), what it needs and what it may touch. PSH's says what the
gates need to rule: which destinations a call reaches, the highest label the component
may receive, its risk tier, whether it mutates, its licence class and integration mode.
The derivation is the whole security argument of the bridge, so each rule is stated:

* **destinations** follow the mechanism. An ``http`` connector reaches its declared hosts
  and nothing local; a local backend reaches ``LOCAL_COMPUTE``, plus each declared host,
  plus ``PERSISTENT`` if it declares writes. A host is ``PUBLIC_REMOTE`` unless the
  operator's ``HostPolicy`` names it trusted — a public research API is a third party
  that receives the query, whatever its reputation.
* **the ceiling** is the lowest of the destination ceilings the component reaches, capped
  by the operator's local ceiling. A PubMed search therefore accepts at most
  ``RESEARCH_DEIDENTIFIED``: PHI in a query would leave the machine, and the gate refuses
  it by the same rule that refuses PHI to a public model.
* **risk and mutation** follow what the component may do: a subprocess, a container or a
  declared write is consequential (R2) and mutating; a read-only query is routine (R1).
  A mutating component requires at least ``ACT_WITH_APPROVAL`` autonomy.
* **licence** strings are normalised onto SPDX ids the PSH lattice knows. BioScience
  records data licences as free text ("Public-domain (US Gov)"); an exact alias table
  maps the ones this package ships, and anything else stays unlicensed — which the
  lattice permits in ``native`` and ``federated`` modes and refuses for ``vendor``.
  The raw string is kept in provenance so nothing is lost, only nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from psh.contracts import Autonomy, ComponentKind, ComponentManifest as PSHManifest, RiskTier
from psh.labels import DEFAULT_CEILINGS, Destination, Sensitivity
from psh.licensing import normalise_mode

from ..runtime.component import ComponentManifest as BioManifest

__all__ = ["HostPolicy", "KIND_MAP", "SPDX_ALIASES", "bridge_manifest", "ceiling_for",
           "destinations_for", "normalise_spdx", "psh_id_for", "risk_for"]

_VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_SPDX_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")


def psh_id_for(bio_id: str) -> str:
    """A BioScience id as a PSH component id: one safe path component, or a refusal."""
    cleaned = _UNSAFE.sub("-", str(bio_id)).strip("-.")[:128]
    if not cleaned or not _VALID_ID.fullmatch(cleaned):
        raise ValueError(f"BioScience id {bio_id!r} cannot be made a PSH component id")
    return cleaned


#: BioScience kinds onto PSH kinds. ``connector`` is a tool from the gate's point of
#: view; a ``database`` or ``dataset`` is knowledge the run may read.
KIND_MAP: Mapping[str, ComponentKind] = {
    "tool": ComponentKind.TOOL, "connector": ComponentKind.TOOL,
    "software": ComponentKind.TOOL, "skill": ComponentKind.SKILL,
    "workflow": ComponentKind.WORKFLOW, "agent": ComponentKind.AGENT,
    "agent_role": ComponentKind.AGENT, "planner": ComponentKind.AGENT,
    "evaluator": ComponentKind.EVALUATOR, "benchmark": ComponentKind.EVALUATOR,
    "dataset": ComponentKind.DATASET, "database": ComponentKind.KNOWLEDGE,
    "memory": ComponentKind.MEMORY_PROVIDER,
}

#: Exact BioScience licence strings onto SPDX ids. Conservative on purpose: a string
#: that says "mixed", "per article" or "per source" is not one licence and maps to none.
SPDX_ALIASES: Mapping[str, str] = {
    "Public-domain (US Gov)": "US-Gov-Public-Domain",
    "Public (UCSC terms)": "",
    "Public (NCI GDC open data)": "US-Gov-Public-Domain",
    "CC0-1.0 (data) / CC-BY-4.0": "CC-BY-4.0",
    "CC0-1.0 (metadata)": "CC0-1.0",
    "CC-BY-4.0 (metadata)": "CC-BY-4.0",
    "MIT (code) / CC-BY-4.0 (data)": "CC-BY-4.0",
    "Apache-2.0 (service)": "Apache-2.0",
    "BSD-3-Clause (service)": "BSD-3-Clause",
    "CC-BY": "CC-BY-4.0",
    "EMBL-EBI terms of use (open)": "",
    "Open (EMBL-EBI terms)": "",
    "Open (INSDC)": "",
    "Open (GTEx terms)": "",
    "CC-BY / mixed per article": "",
    "Apache-2.0 (service) / per ontology": "",
    "BSD-3-Clause (service) / per source": "",
    "Mixed per source": "",
    "Mixed per resource (some CC-BY-NC-SA)": "",
    "HPO licence (free with attribution)": "",
    "Free for research use (PANTHER terms)": "",
    "Academic use free; commercial requires license": "",
    "ODbL / per-study terms": "",
}


def normalise_spdx(text: str | None) -> str:
    """An SPDX id the PSH lattice can classify, or "" for anything it should not guess."""
    value = (text or "").strip()
    if not value:
        return ""
    if value in SPDX_ALIASES:
        return SPDX_ALIASES[value]
    return value if _SPDX_SHAPE.fullmatch(value) else ""


@dataclass(frozen=True)
class HostPolicy:
    """Which hosts the operator treats as trusted rather than public. Default: none."""

    trusted_hosts: frozenset[str] = field(default_factory=frozenset)

    def destination_for(self, host: str) -> Destination:
        return (Destination.TRUSTED_REMOTE if host.lower() in self.trusted_hosts
                else Destination.PUBLIC_REMOTE)


def destinations_for(bio: BioManifest, host_policy: HostPolicy | None = None
                     ) -> tuple[Destination, ...]:
    """Every destination a call to this component reaches. Never empty."""
    policy = host_policy or HostPolicy()
    hosts = tuple(h for h in bio.permissions.network if h)
    remote = tuple(dict.fromkeys(policy.destination_for(h) for h in hosts))
    if bio.runtime.backend == "http":
        return remote or (Destination.PUBLIC_REMOTE,)
    out: list[Destination] = [Destination.LOCAL_COMPUTE, *remote]
    if bio.permissions.filesystem_write:
        out.append(Destination.PERSISTENT)
    return tuple(dict.fromkeys(out))


def ceiling_for(destinations: Sequence[Destination], *,
                local_ceiling: Sensitivity = Sensitivity.PHI) -> Sensitivity:
    """The highest label the component may receive: the lowest ceiling it reaches."""
    ceilings = [DEFAULT_CEILINGS[d] for d in destinations]
    return min([*ceilings, local_ceiling])


def _mutates(bio: BioManifest) -> bool:
    return bool(bio.permissions.filesystem_write or bio.permissions.subprocess
                or bio.runtime.backend in ("subprocess", "container"))


def risk_for(bio: BioManifest) -> RiskTier:
    return RiskTier.R2_CONSEQUENTIAL if _mutates(bio) else RiskTier.R1_ROUTINE


def _operations_schema(operations: Sequence[Any]) -> dict[str, Any]:
    if not operations:
        return {}
    properties: dict[str, Any] = {
        "operation": {"type": "string", "enum": [op.name for op in operations],
                      "description": "which typed operation of this source to call"}}
    for op in operations:
        for arg in op.args:
            properties.setdefault(arg, {"description": f"required by {op.name}"})
    return {"type": "object", "required": ["operation"], "properties": properties,
            "operations": {op.name: {"description": op.description, "args": list(op.args),
                                     "example": dict(op.example)} for op in operations}}


def bridge_manifest(bio: BioManifest, *, host_policy: HostPolicy | None = None,
                    local_ceiling: Sensitivity = Sensitivity.PHI,
                    backend: str = "python", entrypoint: str = "",
                    operations: Sequence[Any] = (), verification: Mapping[str, Any] | None = None,
                    description_sensitivity: str = "", extra_provenance: Mapping[str, Any] | None = None
                    ) -> PSHManifest:
    """The PSH manifest for a BioScience component. Pure; admission adds the audit."""
    destinations = destinations_for(bio, host_policy)
    hosts = tuple(h for h in bio.permissions.network if h)
    mutates = _mutates(bio)
    kind = KIND_MAP.get(bio.kind, ComponentKind.TOOL)
    verified = dict(verification or {})
    latency_s = float(verified.get("latency_ms") or 0) / 1000.0 or (2.0 if hosts else 1.0)
    provenance: dict[str, Any] = {
        "bridge": "bioscience", "bio_id": bio.id, "bio_kind": bio.kind,
        "bio_backend": bio.runtime.backend, "project": bio.provider.project,
        "commit": bio.provider.commit, "source_path": bio.provider.source_path,
        "data_license": bio.license.spdx, "license_note": bio.license.note,
        "integration_mode_declared": bio.license.integration_mode,
        "docs": bio.provider.repo,
        "verified_at": verified.get("verified_at", ""),
        "verification": (f"{verified.get('ok', 0)}/{verified.get('total', 0)} operations live"
                         if verified else "unverified"),
        "operations": [op.name for op in operations],
    }
    if description_sensitivity:
        provenance["description_sensitivity"] = description_sensitivity
    provenance.update(dict(extra_provenance or {}))
    return PSHManifest(
        id=psh_id_for(bio.id), name=bio.name or bio.id, kind=kind, version=bio.version,
        publisher=bio.provider.project or "bioscience",
        description=(bio.description or bio.name or bio.id)[:400],
        intents=tuple(op.name for op in operations)[:12],
        domain=bio.domain, tags=tuple(t for t in (bio.omics_type, bio.kind) if t),
        input_schema=_operations_schema(operations) or dict(bio.inputs),
        output_schema=dict(bio.outputs),
        backend=backend, entrypoint=entrypoint,
        allowed_hosts=hosts, timeout_s=120.0,
        idempotent=(bio.runtime.backend == "http" or bio.runtime.deterministic) and not mutates,
        max_label=ceiling_for(destinations, local_ceiling=local_ceiling),
        destinations=destinations,
        requires_network=bool(hosts) or bio.runtime.backend == "http",
        requires_filesystem=bool(bio.permissions.filesystem_read
                                 or bio.permissions.filesystem_write),
        mutates=mutates,
        min_autonomy=Autonomy.ACT_WITH_APPROVAL if mutates else Autonomy.OBSERVE,
        risk_tier=risk_for(bio),
        expected_latency_s=latency_s,
        known_limits=tuple(x for x in (bio.license.note,) if x),
        license_spdx=normalise_spdx(bio.license.spdx),
        integration_mode=normalise_mode(bio.license.integration_mode),
        provenance=provenance)
