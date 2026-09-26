"""Trusted policy kernel — license, permission, capability and lineage rules.

This module is the immutable plane. Nothing an agent authors may modify it, and
no invocation may bypass it: the runtime consults `PolicyKernel.authorize()`
between resolution and execution, so a capability cannot reach a backend without
a recorded decision.

The v1 defect this fixed: `Adapter.assert_may_vendor()` existed but was called
only from a test, so the license boundary was documentation, not enforcement.

Two further gaps are closed here, both of which were "declared but not checked":

* **Filesystem capabilities.** `PermissionProfile` has always carried
  `allow_filesystem_read` / `allow_filesystem_write`, but `authorize()` looked
  only at license, network and subprocess — so a manifest could declare
  `filesystem_write = ()` and the kernel would never notice a component that
  wrote anywhere. `check_filesystem()` now rules on every declared path, and
  because the in-process python backend offers no OS-level confinement, a
  component that declares writes is refused that backend outright (see
  `confine_filesystem`). The kernel enforces the boundary it can and refuses the
  execution paths where it cannot — it never pretends.

* **Dependency lineage.** Rulings applied only to the component named in the
  request, so a tool whose `requires.datasets` pointed at a DENIED dataset was
  itself ALLOWED. Authorization is now lineage-propagating: the caller supplies
  the dependency closure as `DependencyContext` entries and the most restrictive
  rule across the whole closure wins.

`PolicyKernel.enforcement_report()` states, per capability class, whether the
rule is enforced by mechanism or only gated at declaration time.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    PREFER_ALTERNATIVE = "PREFER_ALTERNATIVE"


@dataclass(frozen=True)
class Ruling:
    """A policy decision with the reason that produced it."""

    decision: PolicyDecision
    reason: str
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is not PolicyDecision.DENY


#: SPDX ids that permit reusing (vendoring) upstream implementation code.
PERMISSIVE_SPDX = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "NLM-Public-Domain", "CC0-1.0", "Unlicense",
})

#: Treated as "no license granted" — all rights reserved by default.
NO_LICENSE_SPDX = frozenset({"NONE", "NOASSERTION", "", "UNKNOWN"})

#: license class -> integration mode -> ruling
_DEFAULT_LICENSE_POLICY: Mapping[str, Mapping[str, PolicyDecision]] = MappingProxyType({
    "permissive": MappingProxyType({
        "vendor": PolicyDecision.ALLOW,
        "native": PolicyDecision.ALLOW,
        "federated": PolicyDecision.ALLOW,
    }),
    "none": MappingProxyType({
        # Copying code from a project that grants no license is not permitted.
        "vendor": PolicyDecision.DENY,
        # Calling a native equivalent avoids the upstream code entirely.
        "native": PolicyDecision.ALLOW,
        # Invoking the upstream in its own process is use, not redistribution.
        "federated": PolicyDecision.ALLOW,
    }),
    "copyleft": MappingProxyType({
        "vendor": PolicyDecision.PREFER_ALTERNATIVE,
        "native": PolicyDecision.ALLOW,
        "federated": PolicyDecision.ALLOW,
    }),
})

COPYLEFT_SPDX = frozenset({"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-3.0", "LGPL-2.1"})


def license_class(spdx: str | None) -> str:
    s = (spdx or "").strip()
    if s in PERMISSIVE_SPDX:
        return "permissive"
    if s in COPYLEFT_SPDX:
        return "copyleft"
    if s in NO_LICENSE_SPDX:
        return "none"
    return "none"  # unrecognized ⇒ treat as all-rights-reserved (fail closed)


class LicensePolicy:
    """Decides whether a capability may be used through a given integration mode."""

    def __init__(self, table: Mapping[str, Mapping[str, PolicyDecision]] | None = None) -> None:
        self._table = table or _DEFAULT_LICENSE_POLICY

    def check(self, *, license_spdx: str | None, integration_mode: str) -> Ruling:
        cls = license_class(license_spdx)
        mode = (integration_mode or "").strip().lower()
        # normalize the v1 vocabulary onto policy modes
        if mode in ("adapter-only", "adapter_only"):
            mode = "federated"
        decision = self._table.get(cls, {}).get(mode)
        if decision is None:
            return Ruling(PolicyDecision.DENY,
                          f"no rule for license class {cls!r} with mode {mode!r}",
                          rule="license.default_deny")
        if decision is PolicyDecision.DENY:
            return Ruling(decision,
                          f"{license_spdx or 'no license'} does not permit {mode} use; "
                          "invoke upstream in place or use a native equivalent",
                          rule=f"license.{cls}.{mode}")
        if decision is PolicyDecision.PREFER_ALTERNATIVE:
            return Ruling(decision,
                          f"{license_spdx} is copyleft; prefer a native equivalent before vendoring",
                          rule=f"license.{cls}.{mode}")
        return Ruling(decision, f"{license_spdx or 'unlicensed'} permits {mode} use",
                      rule=f"license.{cls}.{mode}")


#: Backends that give executed code no OS-level filesystem confinement. Code on
#: these backends runs with the harness's own credentials and file access, so a
#: declared write boundary would be advisory only.
UNCONFINED_BACKENDS = frozenset({"python"})

#: Symbolic roots a profile may name instead of a machine-specific absolute path,
#: so profiles stay portable across checkouts and CI. Expanded at check time.
_ROOT_TOKENS = ("${workspace}", "${data_lake}", "${tmp}")


def expand_root(root: str) -> Path:
    """Resolve a profile root, expanding `${workspace}`, `${data_lake}`, `${tmp}`."""
    r = str(root).strip()
    if r == "${tmp}":
        return Path(tempfile.gettempdir()).resolve()
    if r in ("${workspace}", "${data_lake}"):
        from .config import data_lake_dir, workspace_dir

        base = workspace_dir() if r == "${workspace}" else data_lake_dir()
        # the directory need not exist yet; resolve(strict=False) still normalizes
        return Path(base).expanduser().resolve()
    return Path(r).expanduser().resolve()


def _resolve_requested(path: str) -> Path:
    """Normalize a requested path, so `..` cannot walk out of an allowed root."""
    p = Path(str(path).strip()).expanduser()
    if not p.is_absolute():
        from .config import workspace_dir

        p = Path(workspace_dir()) / p
    return p.resolve()


@dataclass(frozen=True)
class PermissionProfile:
    """What a component is allowed to touch.

    `allow_filesystem_read` / `allow_filesystem_write` are sets of permitted
    *roots*: a requested path is admitted only if it resolves inside one of them.
    An empty set means the profile grants no access of that mode at all, so a
    component that declares any path of that mode is denied.
    """

    name: str = "default"
    allow_network: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    allow_filesystem_read: frozenset[str] = frozenset()
    allow_filesystem_write: frozenset[str] = frozenset()
    allow_subprocess: bool = False
    #: Refuse to run a component that declares filesystem writes on a backend
    #: that cannot confine them (see `UNCONFINED_BACKENDS`). Default True: a
    #: boundary the kernel cannot enforce is not granted, it is declined.
    confine_filesystem: bool = True

    def check_network(self, hosts: Iterable[str]) -> Ruling:
        hosts = [h for h in hosts if h]
        if not hosts:
            return Ruling(PolicyDecision.ALLOW, "no network access requested", "perm.network.none")
        if not self.allow_network:
            return Ruling(PolicyDecision.DENY,
                          f"profile {self.name!r} forbids network access (requested: {', '.join(hosts[:3])})",
                          "perm.network.denied")
        if self.allowed_hosts:
            bad = [h for h in hosts if not any(h == a or h.endswith("." + a) for a in self.allowed_hosts)]
            if bad:
                return Ruling(PolicyDecision.DENY,
                              f"hosts not in profile allowlist: {', '.join(bad[:3])}",
                              "perm.network.allowlist")
        return Ruling(PolicyDecision.ALLOW, "network permitted by profile", "perm.network.ok")

    # ---------------------------------------------------------------- filesystem
    def allowed_roots(self, mode: str) -> frozenset[str]:
        return (self.allow_filesystem_write if mode == "write"
                else self.allow_filesystem_read)

    def check_filesystem(self, paths: Iterable[str], *, mode: str) -> Ruling:
        """Rule on declared filesystem paths for `mode` in ("read", "write").

        This is the check whose absence made the profile's filesystem fields
        decorative: they were declared on both sides and compared on neither.
        """
        want = [str(p).strip() for p in (paths or ()) if str(p).strip()]
        if not want:
            return Ruling(PolicyDecision.ALLOW, f"no filesystem {mode} access requested",
                          f"perm.fs_{mode}.none")
        roots = self.allowed_roots(mode)
        if not roots:
            return Ruling(PolicyDecision.DENY,
                          f"profile {self.name!r} grants no filesystem {mode} access "
                          f"(requested: {', '.join(want[:3])})",
                          f"perm.fs_{mode}.denied")
        resolved_roots = []
        for r in roots:
            try:
                resolved_roots.append(expand_root(r))
            except (OSError, RuntimeError):
                continue
        outside = []
        for raw in want:
            try:
                target = _resolve_requested(raw)
            except (OSError, RuntimeError):
                outside.append(raw)
                continue
            if not any(target == root or root in target.parents for root in resolved_roots):
                outside.append(raw)
        if outside:
            return Ruling(PolicyDecision.DENY,
                          f"filesystem {mode} paths outside the roots profile {self.name!r} "
                          f"permits: {', '.join(outside[:3])}",
                          f"perm.fs_{mode}.outside_root")
        return Ruling(PolicyDecision.ALLOW,
                      f"filesystem {mode} paths within profile roots", f"perm.fs_{mode}.ok")

    def check_confinement(self, *, backend: str, write_paths: Iterable[str]) -> Ruling:
        """Refuse writes on a backend that provides no OS-level confinement.

        `HardenedExecutor` is candid that without a container runtime it cannot
        isolate the filesystem; the in-process python backend cannot even do
        that much. Rather than record a write permission nothing enforces, the
        kernel declines the combination and names the backends that can.
        """
        want = [str(p).strip() for p in (write_paths or ()) if str(p).strip()]
        if not want or not self.confine_filesystem:
            return Ruling(PolicyDecision.ALLOW, "no unconfined write requested",
                          "perm.fs_confinement.none")
        if backend in UNCONFINED_BACKENDS:
            return Ruling(PolicyDecision.DENY,
                          f"backend {backend!r} runs in-process and cannot confine filesystem "
                          f"writes ({', '.join(want[:3])}); use the subprocess or container "
                          "backend, or a profile with confine_filesystem=False",
                          "perm.fs_confinement.unconfined_backend")
        return Ruling(PolicyDecision.ALLOW, f"backend {backend!r} can confine writes",
                      "perm.fs_confinement.ok")

    def enforcement(self) -> dict[str, str]:
        """State how each capability class is enforced under this profile.

        "mechanism" — the kernel's decision is backed by something that stops the
        action; "declaration" — the kernel gates what a component *declares*, but
        code on an unconfined backend could still act outside its declaration.
        """
        return {
            "license": "mechanism (invocation is refused)",
            "network_hosts": "declaration (hosts are gated; sockets are not intercepted)",
            "subprocess": "mechanism (invocation is refused)",
            "filesystem_read": "declaration (paths are gated; reads are not intercepted)",
            "filesystem_write": ("mechanism (unconfined backends are refused)"
                                 if self.confine_filesystem
                                 else "declaration (confinement disabled on this profile)"),
        }


#: Roots every profile may read: the local data lake and the agent workspace.
_READ_ROOTS = frozenset({"${data_lake}", "${workspace}"})
#: Roots a profile may write: the agent workspace, the harness-managed data lake
#: (where the acquisition layer lands downloads) and the process temp directory.
#: No profile grants a write root outside these three.
_WRITE_ROOTS = frozenset({"${workspace}", "${data_lake}", "${tmp}"})

#: Profiles are defined here, in the trusted plane — not in agent-writable files.
PROFILES: Mapping[str, PermissionProfile] = MappingProxyType({
    "offline-analysis": PermissionProfile(
        name="offline-analysis", allow_network=False,
        allow_filesystem_read=_READ_ROOTS, allow_filesystem_write=_WRITE_ROOTS),
    "biomedical-research": PermissionProfile(
        name="biomedical-research", allow_network=True, allow_subprocess=True,
        allow_filesystem_read=_READ_ROOTS, allow_filesystem_write=_WRITE_ROOTS,
        # Every host below was verified live from the harness (see
        # catalogue_v2/connector_live_verification.csv). Suffix matching applies,
        # so "ncbi.nlm.nih.gov" also covers eutils./pubchem.ncbi.nlm.nih.gov.
        allowed_hosts=frozenset({
            "ncbi.nlm.nih.gov", "ebi.ac.uk", "ensembl.org", "rcsb.org", "uniprot.org",
            "clinicaltrials.gov", "api.fda.gov", "string-db.org", "reactome.org",
            "rest.kegg.jp", "opentargets.org", "gnomad.broadinstitute.org",
            "mygene.info", "myvariant.info",
            # bulk-data hosts used by the acquisition layer
            "biomni-release.s3.amazonaws.com", "storage.googleapis.com",
            "ftp.ebi.ac.uk", "stringdb-downloads.org", "genenames.org",
            # not yet verified live from this harness, retained from v2
            "depmap.org",
            # v2.4 connector set, every host verified live (scripts/verify_connectors.py)
            "genome.ucsc.edu", "monarchinitiative.org", "alphafold.ebi.ac.uk",
            "proteinatlas.org", "gtexportal.org", "encodeproject.org",
            "cellxgene.cziscience.com", "wikipathways.org", "omnipathdb.org", "dgidb.org",
            "civicdb.org", "cbioportal.org", "gdc.cancer.gov", "clinicaltables.nlm.nih.gov",
            "rxnav.nlm.nih.gov", "dailymed.nlm.nih.gov", "id.nlm.nih.gov", "crossref.org",
            "openalex.org", "biorxiv.org", "ontology.jax.org", "disease-ontology.org",
            "bioregistry.io", "identifiers.org", "biit.cs.ut.ee", "pantherdb.org",
            "query.wikidata.org", "api.gbif.org",
            # v2.5 bulk natural-product and reference datasets (acquisition layer)
            "bidd.group", "npatlas.org", "zenodo.org",
        })),
    "sandbox-only": PermissionProfile(
        name="sandbox-only", allow_network=False, allow_subprocess=True,
        allow_filesystem_read=frozenset({"${tmp}"}),
        allow_filesystem_write=frozenset({"${tmp}"})),
})


@dataclass(frozen=True)
class DependencyContext:
    """One transitive dependency's policy-relevant surface.

    A tool is not safer than the data it consumes. Folding each dependency's
    licence and capability surface into the parent's ruling is what makes the
    kernel lineage-propagating rather than per-component.
    """

    component_id: str
    kind: str = ""
    license_spdx: str | None = None
    integration_mode: str = "federated"
    network_hosts: tuple[str, ...] = ()
    filesystem_read: tuple[str, ...] = ()
    filesystem_write: tuple[str, ...] = ()
    subprocess: bool = False
    #: how the dependency was reached, e.g. "tool.x -> dataset.y"
    path: str = ""


@dataclass
class AuthorizationRequest:
    """Everything the kernel needs to rule on one invocation.

    `dependencies` carries the resolved dependency closure. Supply it and the
    ruling covers the whole lineage; omit it and the kernel rules on this
    component alone, which is what let a tool consume a denied dataset.
    """

    component_id: str
    license_spdx: str | None
    integration_mode: str
    backend: str = "python"
    network_hosts: tuple[str, ...] = ()
    profile: str = "biomedical-research"
    filesystem_read: tuple[str, ...] = ()
    filesystem_write: tuple[str, ...] = ()
    #: the component's own declared subprocess permission, independent of whether
    #: the subprocess *backend* was selected
    subprocess: bool = False
    dependencies: tuple[DependencyContext, ...] = ()


@dataclass(frozen=True)
class Authorization:
    """The kernel's ruling, recorded in provenance whether allowed or denied."""

    component_id: str
    allowed: bool
    rulings: tuple[Ruling, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        denied = [r for r in self.rulings if r.decision is PolicyDecision.DENY]
        if denied:
            return "; ".join(r.reason for r in denied)
        return "; ".join(r.reason for r in self.rulings) or "authorized"

    @property
    def denied_rules(self) -> tuple[str, ...]:
        return tuple(r.rule for r in self.rulings if r.decision is PolicyDecision.DENY)


class PolicyKernel:
    """The single gate every invocation passes through.

    Immutability is structural rather than advisory: the kernel holds its policy
    tables as read-only mappings and exposes no setter. Callers construct a
    kernel; agent-authored code cannot rewrite one.
    """

    def __init__(self, license_policy: LicensePolicy | None = None,
                 profiles: Mapping[str, PermissionProfile] | None = None) -> None:
        self._license = license_policy or LicensePolicy()
        self._profiles = MappingProxyType(dict(profiles or PROFILES))

    @property
    def license_policy(self) -> LicensePolicy:
        return self._license

    def profile(self, name: str) -> PermissionProfile:
        return self._profiles.get(name, PROFILES["offline-analysis"])

    def enforcement_report(self, profile: str = "biomedical-research") -> dict[str, Any]:
        """What this kernel enforces by mechanism versus by declaration.

        Published so a caller never has to infer the strength of the boundary
        from the fact that a check exists.
        """
        prof = self.profile(profile)
        return {
            "profile": prof.name,
            "capabilities": prof.enforcement(),
            "lineage_propagating": True,
            "unconfined_backends": sorted(UNCONFINED_BACKENDS),
            "note": ("The kernel gates every invocation and refuses execution paths it "
                     "cannot confine. It does not interpose on syscalls: code reaching a "
                     "backend can still act within the harness's own OS privileges."),
        }

    # ------------------------------------------------------------------ ruling
    def _capability_rulings(self, prof: PermissionProfile, *, backend: str,
                            network_hosts: Iterable[str], filesystem_read: Iterable[str],
                            filesystem_write: Iterable[str], subprocess_: bool,
                            prefix: str = "") -> list[Ruling]:
        """Every capability check, for a component or for one of its dependencies."""
        rulings = [
            prof.check_network(network_hosts),
            prof.check_filesystem(filesystem_read, mode="read"),
            prof.check_filesystem(filesystem_write, mode="write"),
            prof.check_confinement(backend=backend, write_paths=filesystem_write),
        ]
        if (backend == "subprocess" or subprocess_) and not prof.allow_subprocess:
            rulings.append(Ruling(PolicyDecision.DENY,
                                  f"profile {prof.name!r} forbids subprocess execution",
                                  "perm.subprocess.denied"))
        if prefix:
            rulings = [Ruling(r.decision, f"{prefix}: {r.reason}", f"lineage.{prefix}.{r.rule}")
                       for r in rulings]
        return rulings

    def authorize(self, req: AuthorizationRequest) -> Authorization:
        """Rule on one invocation and its whole dependency lineage.

        Most restrictive wins: a single DENY anywhere in the closure denies the
        invocation, so a permissively licensed tool cannot launder access to a
        dataset the profile forbids.
        """
        prof = self.profile(req.profile)
        rulings: list[Ruling] = [
            self._license.check(license_spdx=req.license_spdx,
                                integration_mode=req.integration_mode)
        ]
        rulings += self._capability_rulings(
            prof, backend=req.backend, network_hosts=req.network_hosts,
            filesystem_read=req.filesystem_read, filesystem_write=req.filesystem_write,
            subprocess_=req.subprocess)

        seen: set[str] = {req.component_id}
        for dep in req.dependencies:
            if dep.component_id in seen:
                continue
            seen.add(dep.component_id)
            tag = dep.path or dep.component_id
            lic = self._license.check(license_spdx=dep.license_spdx,
                                      integration_mode=dep.integration_mode)
            rulings.append(Ruling(lic.decision, f"{tag}: {lic.reason}",
                                  f"lineage.{tag}.{lic.rule}"))
            # A dependency is data or a sub-component, not the execution path, so
            # its own backend does not gate confinement — the parent's does.
            rulings += self._capability_rulings(
                prof, backend=req.backend, network_hosts=dep.network_hosts,
                filesystem_read=dep.filesystem_read, filesystem_write=dep.filesystem_write,
                subprocess_=dep.subprocess, prefix=tag)

        allowed = all(r.allowed for r in rulings)
        return Authorization(component_id=req.component_id, allowed=allowed,
                             rulings=tuple(rulings))
