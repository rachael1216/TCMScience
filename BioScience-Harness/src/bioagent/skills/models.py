"""SkillSpec — the declarative contract a skill is compiled from.

Per ADR-0003 this is the *only* source of a skill's inputs, outputs, permissions
and evidence policy. `SKILL.md` is attached to the artifact as documentation and
is never parsed for authority.

The model is deliberately not Turing-complete. Every field is a value, a set or
a pointer; there is no expression language, no conditional and no reference to
anything outside the document. That is what makes the security argument
reviewable — an auditor reads one file and knows what the skill may do, without
evaluating anything.

Two rules are enforced here that are worth stating plainly:

* **A permission is a request, never a grant.** :meth:`SkillSpec.requested_authority`
  is the *widest* authority the skill asks for; the compiler intersects it with
  the run envelope and refuses a manifest that widens it. Nothing in this module
  decides what the skill actually gets.
* **A skill cannot claim more than it declares.** ``evidence.claim_kinds`` must
  be consistent with ``evidence.max_tier``: a kind whose floor exceeds the tier
  ceiling is a skill that could never support the claim it advertises. That is a
  configuration error, not a runtime one, and it is caught at load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from psh.contracts import Autonomy, RiskTier
from psh.labels import DEFAULT_CEILINGS, Destination, Sensitivity

from ..tcm.model import CLAIM_KINDS, EvidenceTier

__all__ = ["SkillSpec", "SkillRuntime", "SkillPermissions", "SkillEvidencePolicy",
           "SkillResources", "SKILL_API_VERSION", "destinations_for", "label_ceiling_for"]

SKILL_API_VERSION = "1"

#: Backends a skill may declare. Mirrors the BioScience `BACKENDS` vocabulary,
#: restricted to the ones a *skill* can meaningfully use — `none` is excluded
#: because a skill that cannot run cannot be benchmarked, and the upstream
#: SKILL.md-only case goes through an adapter that declares a real backend.
SKILL_BACKENDS = ("python", "http", "container", "subprocess", "mcp")

#: Tier names in the lowercase form the YAML uses.
_TIER_BY_NAME: Mapping[str, EvidenceTier] = {
    t.name.lower(): t for t in EvidenceTier}
_RISK_BY_NAME: Mapping[str, RiskTier] = {r.name.lower(): r for r in RiskTier}


@dataclass(frozen=True, slots=True)
class SkillRuntime:
    """How the skill executes. Mirrors `runtime.component.RuntimeSpec`, narrowed."""

    backend: str = "python"
    entrypoint: str = ""
    timeout_s: float = 120.0
    memory_mb: int = 2048
    deterministic: bool = True
    idempotent: bool = True
    image: str = ""

    def __post_init__(self) -> None:
        if self.backend not in SKILL_BACKENDS:
            raise ValueError(
                f"backend {self.backend!r} is not one of {SKILL_BACKENDS}; a skill that "
                "cannot execute cannot be benchmarked, and `none` here would mean the "
                "latter while reading like the former")
        if self.backend in ("python", "subprocess", "container") and not self.entrypoint:
            raise ValueError(f"backend {self.backend!r} requires an entrypoint")
        if self.backend == "container" and not self.image:
            raise ValueError("backend 'container' requires an image")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.memory_mb <= 0:
            raise ValueError("memory_mb must be positive")


@dataclass(frozen=True, slots=True)
class SkillPermissions:
    """What the skill asks to touch. A request; the envelope decides the grant."""

    network: tuple[str, ...] = ()
    filesystem_read: tuple[str, ...] = ()
    filesystem_write: tuple[str, ...] = ()
    subprocess: bool = False
    secrets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("filesystem_read", "filesystem_write"):
            for path in getattr(self, name):
                if path.strip() in ("/", "/*", "**") or path.startswith("/**"):
                    raise ValueError(
                        f"{name} entry {path!r} is a filesystem-wide grant; a skill must "
                        "name the directories it reads or writes")

    @property
    def mutates(self) -> bool:
        return bool(self.filesystem_write or self.subprocess)

    def as_dict(self) -> dict[str, Any]:
        return {"network": list(self.network),
                "filesystem_read": list(self.filesystem_read),
                "filesystem_write": list(self.filesystem_write),
                "subprocess": self.subprocess, "secrets": list(self.secrets)}


@dataclass(frozen=True, slots=True)
class SkillEvidencePolicy:
    """What the skill may cite and what it may assert.

    This is the field that makes a TCM skill's boundaries structural. A network
    pharmacology skill declares ``claim_kinds: [mechanism]``; the compiler then
    refuses any artifact from it carrying an efficacy claim, and the refusal
    happens before the artifact reaches a reader rather than in a review.
    """

    #: Ceiling on the evidence tiers this skill may cite, by name.
    max_tier: str = "preclinical"
    #: Claim kinds the skill may emit.
    claim_kinds: tuple[str, ...] = ("mechanism",)
    #: Claim kinds explicitly forbidden. Recorded so a refusal can quote the
    #: skill's own declaration rather than an inference.
    forbidden_claims: tuple[str, ...] = ()
    #: Whether cited sources must be pinned to a snapshot.
    require_pinned_sources: bool = True
    #: Whether a supporting excerpt must be located in its source.
    require_quote_verified: bool = True
    #: Quality dimensions the skill undertakes to assess for each item.
    required_quality_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_tier not in _TIER_BY_NAME:
            raise ValueError(
                f"max_tier {self.max_tier!r} is not one of {sorted(_TIER_BY_NAME)}")
        ceiling = _TIER_BY_NAME[self.max_tier]
        for kind in self.claim_kinds:
            floor = CLAIM_KINDS.get(kind)
            if floor is None:
                raise ValueError(
                    f"claim_kind {kind!r} is not one of {sorted(CLAIM_KINDS)}")
            if floor > ceiling:
                # The skill advertises a claim kind it has declared it cannot
                # support. Catching this here means the contradiction surfaces
                # when the manifest is reviewed, not when a run silently fails
                # to support the claim it promised.
                raise ValueError(
                    f"skill declares claim_kind {kind!r}, which needs "
                    f"{floor.name.lower()} evidence, but caps itself at "
                    f"max_tier {self.max_tier!r}; the skill can never support the "
                    "claim it advertises")
        overlap = set(self.claim_kinds) & set(self.forbidden_claims)
        if overlap:
            raise ValueError(
                f"claim kinds {sorted(overlap)} are both permitted and forbidden")

    @property
    def tier_ceiling(self) -> EvidenceTier:
        return _TIER_BY_NAME[self.max_tier]

    @property
    def permitted_kinds(self) -> frozenset[str]:
        return frozenset(self.claim_kinds)

    def permits(self, claim_kind: str) -> tuple[bool, str]:
        """Whether the policy allows a claim kind, with the reason if not."""
        if claim_kind in self.forbidden_claims:
            return False, f"the skill declares {claim_kind!r} a forbidden claim"
        if claim_kind not in self.claim_kinds:
            return False, (f"the skill permits only {sorted(self.claim_kinds)}; "
                           f"{claim_kind!r} is not among them")
        return True, ""

    def as_dict(self) -> dict[str, Any]:
        return {"max_tier": self.max_tier, "claim_kinds": list(self.claim_kinds),
                "forbidden_claims": list(self.forbidden_claims),
                "require_pinned_sources": self.require_pinned_sources,
                "require_quote_verified": self.require_quote_verified,
                "required_quality_dimensions": list(self.required_quality_dimensions)}


def destinations_for(spec: "SkillSpec") -> tuple[Destination, ...]:
    """Every destination a call to this skill reaches. Never empty.

    Follows what the skill *reaches*, not what backend it uses — the same
    direction as `psh.bridge.manifest.destinations_for`, so a `python` skill
    that declares a host cannot end up with local-compute-only authority.
    """
    out: list[Destination] = [Destination.LOCAL_COMPUTE]
    if spec.permissions.network:
        out.append(Destination.PUBLIC_REMOTE)
    if spec.permissions.filesystem_write:
        out.append(Destination.PERSISTENT)
    return tuple(dict.fromkeys(out))


def label_ceiling_for(spec: "SkillSpec") -> Sensitivity:
    """The highest data label the skill may receive, before the envelope narrows it.

    The minimum ceiling across the destinations it reaches, capped by what the
    skill's own reach implies. Defined once, here, because the compiler and
    :meth:`SkillSpec.requested_authority` both need it and two copies had already
    drifted apart.
    """
    ceilings = [DEFAULT_CEILINGS[d] for d in destinations_for(spec)]
    return min(ceilings)


@dataclass(frozen=True, slots=True)
class SkillResources:
    """Expected cost, for feasibility rather than billing."""

    expected_tokens: int = 0
    expected_usd: float = 0.0
    expected_latency_s: float = 1.0

    def __post_init__(self) -> None:
        for name in ("expected_tokens", "expected_usd", "expected_latency_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """One skill, as declared. The unit the registry pins and the compiler reads."""

    id: str
    name: str
    version: str
    summary: str = ""
    description: str = ""
    api_version: str = SKILL_API_VERSION
    #: Who maintains it. "TCMScience" for a native skill; the upstream project
    #: for an adapter wrapping a third-party SKILL.md.
    maintainer: str = ""
    license_spdx: str = ""
    integration_mode: str = "native"
    #: Path to the human documentation, relative to the skill directory. Read
    #: for nothing but attached to the artifact.
    documentation: str = ""
    runtime: SkillRuntime = field(default_factory=SkillRuntime)
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    evidence: SkillEvidencePolicy = field(default_factory=SkillEvidencePolicy)
    resources: SkillResources = field(default_factory=SkillResources)
    #: Ids of the SourceCards this skill reads. Resolved by the registry so a
    #: skill cannot reach a source it did not declare.
    sources: tuple[str, ...] = ()
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    #: Requested risk tier and autonomy. Narrowed by the envelope, never widened.
    risk: str = "r1_routine"
    min_autonomy: str = "observe"
    #: Registry bookkeeping, set at promotion. Empty for a candidate.
    source_repo: str = ""
    source_commit: str = ""
    content_hash: str = ""
    #: Where the skill was loaded from, for error messages and provenance.
    origin: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SkillSpec needs an id")
        if not self.name:
            raise ValueError(f"skill {self.id!r} needs a name")
        if not self.version:
            raise ValueError(f"skill {self.id!r} needs a version")
        if self.api_version != SKILL_API_VERSION:
            raise ValueError(
                f"skill {self.id!r} declares api_version {self.api_version!r}; this "
                f"compiler implements {SKILL_API_VERSION}")
        if self.risk not in _RISK_BY_NAME:
            raise ValueError(
                f"risk {self.risk!r} is not one of {sorted(_RISK_BY_NAME)}")
        if self.min_autonomy not in {a.value for a in Autonomy}:
            raise ValueError(
                f"min_autonomy {self.min_autonomy!r} is not one of "
                f"{sorted(a.value for a in Autonomy)}")
        if self.integration_mode not in ("vendor", "native", "federated"):
            raise ValueError(
                f"integration_mode {self.integration_mode!r} is not one of "
                "vendor/native/federated")

    # -- derived authority --------------------------------------------------

    @property
    def risk_tier(self) -> RiskTier:
        return _RISK_BY_NAME[self.risk]

    @property
    def autonomy(self) -> Autonomy:
        return Autonomy(self.min_autonomy)

    @property
    def mutates(self) -> bool:
        return self.permissions.mutates or self.runtime.backend in ("subprocess",
                                                                    "container")

    @property
    def composite_id(self) -> str:
        """The id the registry and a lockfile pin."""
        return f"{self.id}@{self.version}"

    def requested_authority(self) -> Mapping[str, Any]:
        """The widest authority the skill asks for, in envelope terms.

        Returned as a mapping so the compiler can hand it to an intersection
        rather than to a setter. Nothing here is a grant — this is the *request*
        side of the comparison that produces
        `SKILL_PERMISSION_WIDENS_ENVELOPE`.
        """
        return {"risk": self.risk_tier,
                "autonomy": self.autonomy,
                "destinations": tuple(self.permissions.network),
                "mutates": self.mutates,
                "requires_network": bool(self.permissions.network),
                "requires_filesystem": bool(self.permissions.filesystem_read
                                            or self.permissions.filesystem_write),
                "requires_secrets": tuple(self.permissions.secrets),
                "sensitivity_ceiling": self._label_ceiling()}

    def _label_ceiling(self) -> Sensitivity:
        """The highest label the skill may receive, before the envelope narrows it.

        Delegates to :func:`label_ceiling_for` so that there is exactly one
        definition of this rule. An earlier version carried its own copy here
        and the two disagreed — this returned ``SENSITIVE`` for a local-only
        skill while the compiler, reading PSH's ``DEFAULT_CEILINGS``, allowed
        ``PHI``. One of them was dead code, which is the only reason the
        disagreement was not a live bug.
        """
        return label_ceiling_for(self)

    def as_dict(self) -> dict[str, Any]:
        return {"api_version": self.api_version, "id": self.id, "name": self.name,
                "version": self.version, "summary": self.summary,
                "description": self.description, "maintainer": self.maintainer,
                "license_spdx": self.license_spdx,
                "integration_mode": self.integration_mode,
                "documentation": self.documentation,
                "runtime": {"backend": self.runtime.backend,
                            "entrypoint": self.runtime.entrypoint,
                            "timeout_s": self.runtime.timeout_s,
                            "memory_mb": self.runtime.memory_mb,
                            "deterministic": self.runtime.deterministic,
                            "idempotent": self.runtime.idempotent,
                            "image": self.runtime.image},
                "permissions": self.permissions.as_dict(),
                "evidence": self.evidence.as_dict(),
                "resources": {"expected_tokens": self.resources.expected_tokens,
                              "expected_usd": self.resources.expected_usd,
                              "expected_latency_s": self.resources.expected_latency_s},
                "sources": list(self.sources), "inputs": dict(self.inputs),
                "outputs": dict(self.outputs), "risk": self.risk,
                "min_autonomy": self.min_autonomy,
                "source_repo": self.source_repo, "source_commit": self.source_commit,
                "content_hash": self.content_hash,
                # `origin` and `composite_id` are derived — where this instance was
                # loaded from, and the id@version a lockfile pins. Excluded so the
                # dict round-trips: `spec_from_dict(spec.as_dict()) == spec`, and
                # the hash of a manifest does not depend on the path it sits at.
                "origin": self.origin, "composite_id": self.composite_id}

    #: Keys `as_dict` emits for reporting but which must not be fed back into
    #: `spec_from_dict`. Kept beside the method so the two cannot drift.
    DERIVED_KEYS = ("origin", "composite_id")


def _tuple_str(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError(f"{field} must be a list, not a bare string: {value!r}")
    if not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list, got {type(value).__name__}")
    return tuple(str(v) for v in value)


def spec_from_dict(data: Mapping[str, Any], *, origin: str = "") -> SkillSpec:
    """Build a :class:`SkillSpec` from a parsed `skill.yaml`.

    Strict about unknown keys, in the same spirit as PSH's
    ``ScientificProgram.from_dict`` and unlike the permissive legacy
    ``Plan.from_dict``: a skill manifest with a misspelled `permisions:` block
    would otherwise load as a skill that asks for nothing, which reads exactly
    like a skill that needs nothing.
    """
    known = {"api_version", "id", "name", "version", "summary", "description",
             "maintainer", "license_spdx", "license", "integration_mode",
             "documentation", "runtime", "permissions", "evidence", "resources",
             "sources", "inputs", "outputs", "risk", "min_autonomy", "source_repo",
             "source_commit", "content_hash"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(
            f"skill manifest has unknown key(s) {unknown}; a misspelled permission "
            "block would otherwise load as a skill that asks for nothing")

    runtime_raw = dict(data.get("runtime") or {})
    _check_keys(runtime_raw, {"backend", "entrypoint", "timeout_s", "memory_mb",
                              "deterministic", "idempotent", "image"}, "runtime")
    perm_raw = dict(data.get("permissions") or {})
    _check_keys(perm_raw, {"network", "filesystem_read", "filesystem_write",
                           "subprocess", "secrets"}, "permissions")
    ev_raw = dict(data.get("evidence") or {})
    _check_keys(ev_raw, {"max_tier", "claim_kinds", "forbidden_claims",
                         "require_pinned_sources", "require_quote_verified",
                         "required_quality_dimensions"}, "evidence")
    res_raw = dict(data.get("resources") or {})
    _check_keys(res_raw, {"expected_tokens", "expected_usd", "expected_latency_s"},
                "resources")

    return SkillSpec(
        id=str(data.get("id") or ""), name=str(data.get("name") or ""),
        version=str(data.get("version") or ""),
        summary=" ".join(str(data.get("summary") or "").split()),
        description=str(data.get("description") or ""),
        api_version=str(data.get("api_version") or SKILL_API_VERSION),
        maintainer=str(data.get("maintainer") or ""),
        license_spdx=str(data.get("license_spdx") or data.get("license") or ""),
        integration_mode=str(data.get("integration_mode") or "native"),
        documentation=str(data.get("documentation") or ""),
        runtime=SkillRuntime(
            backend=str(runtime_raw.get("backend") or "python"),
            entrypoint=str(runtime_raw.get("entrypoint") or ""),
            timeout_s=float(runtime_raw.get("timeout_s") or 120.0),
            memory_mb=int(runtime_raw.get("memory_mb") or 2048),
            deterministic=bool(runtime_raw.get("deterministic", True)),
            idempotent=bool(runtime_raw.get("idempotent", True)),
            image=str(runtime_raw.get("image") or "")),
        permissions=SkillPermissions(
            network=_tuple_str(perm_raw.get("network"), "permissions.network"),
            filesystem_read=_tuple_str(perm_raw.get("filesystem_read"),
                                       "permissions.filesystem_read"),
            filesystem_write=_tuple_str(perm_raw.get("filesystem_write"),
                                        "permissions.filesystem_write"),
            subprocess=bool(perm_raw.get("subprocess")),
            secrets=_tuple_str(perm_raw.get("secrets"), "permissions.secrets")),
        evidence=SkillEvidencePolicy(
            max_tier=str(ev_raw.get("max_tier") or "preclinical").lower(),
            claim_kinds=_tuple_str(ev_raw.get("claim_kinds") or ("mechanism",),
                                   "evidence.claim_kinds"),
            forbidden_claims=_tuple_str(ev_raw.get("forbidden_claims"),
                                        "evidence.forbidden_claims"),
            require_pinned_sources=bool(ev_raw.get("require_pinned_sources", True)),
            require_quote_verified=bool(ev_raw.get("require_quote_verified", True)),
            required_quality_dimensions=_tuple_str(
                ev_raw.get("required_quality_dimensions"),
                "evidence.required_quality_dimensions")),
        resources=SkillResources(
            expected_tokens=int(res_raw.get("expected_tokens") or 0),
            expected_usd=float(res_raw.get("expected_usd") or 0.0),
            expected_latency_s=float(res_raw.get("expected_latency_s") or 1.0)),
        sources=_tuple_str(data.get("sources"), "sources"),
        inputs=dict(data.get("inputs") or {}), outputs=dict(data.get("outputs") or {}),
        risk=str(data.get("risk") or "r1_routine").lower(),
        min_autonomy=str(data.get("min_autonomy") or "observe").lower(),
        source_repo=str(data.get("source_repo") or ""),
        source_commit=str(data.get("source_commit") or ""),
        content_hash=str(data.get("content_hash") or ""),
        origin=origin)


def _check_keys(block: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise ValueError(f"{where} has unknown key(s) {unknown}; allowed: {sorted(allowed)}")
