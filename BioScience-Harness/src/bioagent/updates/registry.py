"""The registry: three catalogues, one promotion path, no automatic install.

Per ADR-0001 there are three separately versioned objects, and this module is
where their separation is enforced rather than described.

    candidate  ──audit──▶ scored ──human decision──▶ stable ──cut──▶ Season

The rule the whole design exists to protect: **the monthly job may write only to
the candidate catalogue.** It cannot promote, and it cannot touch a Season. That
is not a convention here; :meth:`Registry.promote` is the only function that
writes a stable entry and it requires a `PromotionDecision`, so a scheduled job
that tried would have to forge one.

**Pinning is by content, not by version string.** A `SkillVersion` records the
source commit *and* the hash of the skill's files. An upstream maintainer who
edits a skill without bumping its version produces a different hash, so the pin
still holds. Trusting the version string alone is how "pinned" dependencies
silently move.

**The lockfile is the artifact.** `skills.lock.yaml` is what a reviewer reads to
answer "what exactly ran". It is generated from the registry, never hand-edited,
and a test asserts it round-trips.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..skills.models import SkillSpec

__all__ = ["CandidateSkill", "PromotionDecision", "Registry", "RegistryRelease",
           "SkillVersion", "StableSkill", "DECISION_KINDS"]

#: What a human may decide about a candidate. `defer` is a real option and not a
#: euphemism for rejection — most candidates will be deferred, and a process
#: whose only outcomes are yes and no pushes reviewers toward yes.
DECISION_KINDS = ("approve", "reject", "defer", "ignore")


class RegistryError(ValueError):
    """A registry operation that would break an invariant."""


class PromotionRefused(RegistryError):
    """A promotion was attempted without the authority to make it."""


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """One pinned revision of one skill. The unit a lockfile records."""

    skill_id: str
    version: str
    source_repo: str = ""
    #: The immutable commit this was taken from. A branch or tag is not a pin.
    source_commit: str = ""
    #: SHA-256 over the skill's files, taken at promotion. Catches an upstream
    #: edit that did not bump the version.
    content_hash: str = ""
    license_spdx: str = ""
    integration_mode: str = "native"
    allowed_hosts: tuple[str, ...] = ()
    filesystem_permissions: tuple[str, ...] = ()
    subprocess_permissions: bool = False
    dependencies: tuple[str, ...] = ()
    #: Digest of the SBOM and of the test run, so both are addressable.
    sbom_digest: str = ""
    test_digest: str = ""
    #: The frozen benchmark this was scored against, and the scores.
    benchmark_version: str = "unversioned"
    benchmark_scores: Mapping[str, float] = field(default_factory=dict)
    approved_by: str = ""
    approved_at: str = ""
    #: The version this one replaces, so a rollback is one field away.
    rollback_version: str = ""

    def __post_init__(self) -> None:
        if not self.skill_id or not self.version:
            raise RegistryError("a SkillVersion needs a skill_id and a version")
        if self.source_repo and not self.source_commit:
            # A repository with no commit is a moving target. Accepting it is
            # how a "pinned" dependency starts drifting.
            raise RegistryError(
                f"skill {self.skill_id!r} names source_repo {self.source_repo!r} "
                "but no source_commit; a branch or tag is not a pin")
        if self.source_commit and len(self.source_commit) < 7:
            raise RegistryError(
                f"source_commit {self.source_commit!r} is too short to be a commit")

    @property
    def composite_id(self) -> str:
        return f"{self.skill_id}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "version": self.version,
                "composite_id": self.composite_id,
                "source_repo": self.source_repo,
                "source_commit": self.source_commit,
                "content_hash": self.content_hash,
                "license": self.license_spdx,
                "integration_mode": self.integration_mode,
                "allowed_hosts": list(self.allowed_hosts),
                "filesystem_permissions": list(self.filesystem_permissions),
                "subprocess_permissions": self.subprocess_permissions,
                "dependencies": list(self.dependencies),
                "sbom_digest": self.sbom_digest,
                "test_digest": self.test_digest,
                "benchmark_version": self.benchmark_version,
                "benchmark_scores": dict(self.benchmark_scores),
                "approved_by": self.approved_by, "approved_at": self.approved_at,
                "rollback_version": self.rollback_version}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillVersion":
        return cls(
            skill_id=str(data["skill_id"]), version=str(data["version"]),
            source_repo=str(data.get("source_repo") or ""),
            source_commit=str(data.get("source_commit") or ""),
            content_hash=str(data.get("content_hash") or ""),
            license_spdx=str(data.get("license") or data.get("license_spdx") or ""),
            integration_mode=str(data.get("integration_mode") or "native"),
            allowed_hosts=tuple(data.get("allowed_hosts") or ()),
            filesystem_permissions=tuple(data.get("filesystem_permissions") or ()),
            subprocess_permissions=bool(data.get("subprocess_permissions")),
            dependencies=tuple(data.get("dependencies") or ()),
            sbom_digest=str(data.get("sbom_digest") or ""),
            test_digest=str(data.get("test_digest") or ""),
            benchmark_version=str(data.get("benchmark_version") or "unversioned"),
            benchmark_scores=dict(data.get("benchmark_scores") or {}),
            approved_by=str(data.get("approved_by") or ""),
            approved_at=str(data.get("approved_at") or ""),
            rollback_version=str(data.get("rollback_version") or ""))


@dataclass(frozen=True, slots=True)
class CandidateSkill:
    """A skill that has been discovered and not yet decided on."""

    spec: SkillSpec
    first_seen: str = ""
    last_seen: str = ""
    #: Why it could not be scored, if it could not be. A candidate with an audit
    #: failure is still listed — hiding it would make the backlog invisible.
    eliminated_by: tuple[str, ...] = ()
    score: float = 0.0
    score_breakdown: Mapping[str, float] = field(default_factory=dict)
    recommendation: str = ""

    @property
    def eligible(self) -> bool:
        """Whether this candidate may be put to a human decision at all."""
        return not self.eliminated_by

    def as_dict(self) -> dict[str, Any]:
        return {"skill": self.spec.as_dict(), "first_seen": self.first_seen,
                "last_seen": self.last_seen,
                "eliminated_by": list(self.eliminated_by), "score": self.score,
                "score_breakdown": dict(self.score_breakdown),
                "recommendation": self.recommendation,
                "eligible": self.eligible}


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """A human's decision about one candidate. The only key to the stable registry."""

    skill_id: str
    version: str
    decision: str
    decided_by: str
    decided_at: str
    reason: str = ""
    #: For `defer`, when to look again. A deferral with no date is a rejection
    #: that nobody wrote down.
    revisit_after: str = ""

    def __post_init__(self) -> None:
        if self.decision not in DECISION_KINDS:
            raise RegistryError(
                f"decision {self.decision!r} is not one of {DECISION_KINDS}")
        if not self.decided_by:
            # An unattributed approval is not an approval. The whole point of the
            # human step is that a person is answerable for it.
            raise RegistryError(
                f"decision on {self.skill_id}@{self.version} names no decider; an "
                "unattributed promotion is not a review")
        if self.decision == "defer" and not self.revisit_after:
            raise RegistryError(
                f"deferral of {self.skill_id}@{self.version} has no revisit_after; a "
                "deferral with no date is a rejection nobody recorded")

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


@dataclass(frozen=True, slots=True)
class StableSkill:
    """A skill in the stable registry, with the decision that put it there."""

    version: SkillVersion
    decision: PromotionDecision

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version.as_dict(), "decision": {
            "decision": self.decision.decision, "decided_by": self.decision.decided_by,
            "decided_at": self.decision.decided_at, "reason": self.decision.reason}}


@dataclass(frozen=True, slots=True)
class RegistryRelease:
    """A signed, immutable cut of the stable registry."""

    release_id: str
    created_at: str
    entries: tuple[SkillVersion, ...]
    #: SHA-256 over the entries, so a release is verifiable without trusting the
    #: file it came in.
    digest: str = ""
    notes: str = ""

    @property
    def composite_ids(self) -> tuple[str, ...]:
        return tuple(v.composite_id for v in self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {"release_id": self.release_id, "created_at": self.created_at,
                "digest": self.digest or self.compute_digest(),
                "skills": [v.as_dict() for v in self.entries], "notes": self.notes}

    def compute_digest(self) -> str:
        from ..contracts.source_card import canonical_hash
        return canonical_hash([v.as_dict() for v in
                               sorted(self.entries, key=lambda v: v.composite_id)])

    def verify(self) -> bool:
        return not self.digest or self.digest == self.compute_digest()


class Registry:
    """Candidate, stable and Season catalogues with a one-way promotion path."""

    def __init__(self) -> None:
        self._candidates: dict[str, CandidateSkill] = {}
        self._stable: dict[str, StableSkill] = {}
        self._decisions: list[PromotionDecision] = []
        #: Set once a Season is cut. After that the stable registry is frozen:
        #: ADR-0001 forbids a monthly update from changing a benchmark.
        self._frozen_season: str = ""

    # -- candidate layer: the only thing the monthly job may write ---------

    def add_candidate(self, candidate: CandidateSkill) -> None:
        self._candidates[candidate.spec.composite_id] = candidate

    def candidates(self, *, eligible_only: bool = False) -> tuple[CandidateSkill, ...]:
        items = sorted(self._candidates.values(), key=lambda c: c.spec.composite_id)
        if eligible_only:
            items = [c for c in items if c.eligible]
        return tuple(items)

    def candidate(self, composite_id: str) -> CandidateSkill | None:
        return self._candidates.get(composite_id)

    # -- stable layer: human-only, and frozen once a Season is cut ---------

    def promote(self, candidate: CandidateSkill, decision: PromotionDecision, *,
               decided_version: SkillVersion, season: str = "") -> StableSkill:
        """Move a candidate into the stable registry. Requires a decision.

        Refuses in three cases, each for a stated reason rather than a bare no:

        * the candidate was eliminated by an audit — a hard-elimination
          condition is exactly that, and a score does not override it;
        * the decision is not an approval;
        * a Season is frozen, so the stable registry cannot move under it.
        """
        if candidate.eliminated_by:
            raise PromotionRefused(
                f"{candidate.spec.composite_id} was eliminated by "
                f"{list(candidate.eliminated_by)}; a hard-elimination condition is "
                "not overridable by a decision")
        if not decision.approved:
            raise PromotionRefused(
                f"decision on {decision.skill_id}@{decision.version} is "
                f"{decision.decision!r}, not an approval")
        if decision.skill_id != decided_version.skill_id:
            raise PromotionRefused(
                f"decision names {decision.skill_id!r} but the version names "
                f"{decided_version.skill_id!r}")
        if self._frozen_season:
            raise PromotionRefused(
                f"benchmark season {self._frozen_season!r} is frozen; the stable "
                "registry cannot move under a cut benchmark (ADR-0001)")

        # The decision's attribution is stamped onto the version here, not left
        # to the caller. A stable entry must name who approved it and when —
        # that is the entire content of the human step — and a version that
        # reached the lockfile with a blank `approved_by` would be
        # indistinguishable from one nobody reviewed.
        prior = self._stable.get(decided_version.skill_id)
        decided_version = replace(
            decided_version,
            approved_by=decision.decided_by,
            approved_at=decision.decided_at,
            rollback_version=prior.version.version if prior is not None else "")

        entry = StableSkill(version=decided_version, decision=decision)
        self._stable[decided_version.skill_id] = entry
        self._decisions.append(decision)
        return entry

    def stable(self) -> tuple[StableSkill, ...]:
        return tuple(self._stable[k] for k in sorted(self._stable))

    def stable_versions(self) -> tuple[SkillVersion, ...]:
        return tuple(s.version for s in self.stable())

    def freeze_season(self, season: str) -> None:
        """Cut a Season. After this the stable registry is read-only.

        Deliberately irreversible in-process: unfreezing would be a way to make
        an inconvenient comparison go away, and the cost of cutting a new Season
        instead is one command.
        """
        if self._frozen_season:
            raise RegistryError(
                f"season {self._frozen_season!r} is already frozen; cut a new season "
                "rather than unfreezing this one")
        self._frozen_season = season

    @property
    def frozen_season(self) -> str:
        return self._frozen_season

    def rollback(self, skill_id: str, to_version: str, decision: PromotionDecision
                 ) -> StableSkill:
        """Reinstate a previous version. Also a decision, and also recorded."""
        if not decision.approved:
            raise PromotionRefused("a rollback is a promotion and needs an approval")
        if decision.skill_id != skill_id:
            raise PromotionRefused("the rollback decision names a different skill")
        current = self._stable.get(skill_id)
        if current is None:
            raise PromotionRefused(f"{skill_id!r} is not in the stable registry")
        if current.version.version == to_version:
            raise PromotionRefused(f"{skill_id!r} is already at {to_version!r}")
        target = replace(current.version, version=to_version,
                         rollback_version=current.version.version,
                         approved_by=decision.decided_by,
                         approved_at=decision.decided_at)
        entry = StableSkill(version=target, decision=decision)
        self._stable[skill_id] = entry
        self._decisions.append(decision)
        return entry

    # -- release -----------------------------------------------------------

    def release(self, release_id: str, *, created_at: str, notes: str = ""
                ) -> RegistryRelease:
        entries = self.stable_versions()
        release = RegistryRelease(release_id=release_id, created_at=created_at,
                                  entries=entries, notes=notes)
        return replace(release, digest=release.compute_digest())

    def decisions(self) -> tuple[PromotionDecision, ...]:
        return tuple(self._decisions)

    def audit_trail(self) -> tuple[dict[str, Any], ...]:
        """Every decision taken, in order. The registry's own history."""
        return tuple({"skill_id": d.skill_id, "version": d.version,
                      "decision": d.decision, "decided_by": d.decided_by,
                      "decided_at": d.decided_at, "reason": d.reason}
                     for d in self._decisions)

    # -- lockfile ----------------------------------------------------------

    def lockfile(self, *, generated_at: str = "", header: str = "") -> str:
        """`skills.lock.yaml` — the pinned set a reviewer reads.

        Generated, never hand-edited; :func:`load_lockfile` round-trips it.
        """
        import yaml
        payload = {
            "api_version": "1",
            "generated_at": generated_at,
            "frozen_season": self._frozen_season,
            "skills": [v.as_dict() for v in self.stable_versions()],
        }
        body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=100)
        prefix = header or (
            "# skills.lock.yaml — the pinned stable registry.\n"
            "# Generated from the Registry; do not hand-edit. Every entry names an\n"
            "# immutable commit and a content hash, so an upstream edit that did not\n"
            "# bump the version still changes what is pinned.\n")
        return prefix + body


def load_lockfile(text: str) -> tuple[SkillVersion, ...]:
    """Read a lockfile back. The inverse of :meth:`Registry.lockfile`."""
    import yaml
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise RegistryError("lockfile must be a mapping")
    unknown = sorted(set(data) - {"api_version", "generated_at", "frozen_season",
                                  "skills"})
    if unknown:
        raise RegistryError(f"lockfile has unknown key(s) {unknown}")
    return tuple(SkillVersion.from_dict(v) for v in data.get("skills") or ())


def version_from_spec(spec: SkillSpec, *, benchmark_version: str = "unversioned",
                      approved_by: str = "", approved_at: str = "",
                      benchmark_scores: Mapping[str, float] | None = None) -> SkillVersion:
    """Build a `SkillVersion` from a loaded skill. One place, so the fields a
    lockfile records cannot drift from the fields a manifest declares."""
    return SkillVersion(
        skill_id=spec.id, version=spec.version,
        source_repo=spec.source_repo, source_commit=spec.source_commit,
        content_hash=spec.content_hash, license_spdx=spec.license_spdx,
        integration_mode=spec.integration_mode,
        allowed_hosts=tuple(spec.permissions.network),
        filesystem_permissions=tuple(spec.permissions.filesystem_read
                                     + spec.permissions.filesystem_write),
        subprocess_permissions=spec.permissions.subprocess,
        benchmark_version=benchmark_version,
        benchmark_scores=dict(benchmark_scores or {}),
        approved_by=approved_by, approved_at=approved_at)


def content_digest(paths: Iterable[Path]) -> str:
    """SHA-256 over a file set, path-qualified so a rename changes it."""
    digest = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
