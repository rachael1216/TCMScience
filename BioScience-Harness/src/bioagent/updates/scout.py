"""Discovery: find candidate skills, and never promote one.

The scout walks a *declared* set of sources — the repositories the plan fixes for
monthly monitoring, the GitHub topics it names, and any URL an administrator has
submitted — and produces candidates. It executes nothing.

That restraint is the design. The scout is the component that talks to the open
internet, so it is the component most exposed to hostile input; keeping it unable
to run what it finds means a compromised scout can at worst produce bad
candidates, which the audit and the human step then reject. A scout that could
also execute would collapse that ordering.

**Discovery is best-effort and reports what it could not do.** A source that
timed out is recorded as unreachable, not skipped: a monthly report that silently
omitted a repository would read as "nothing changed there", which is the opposite
of what happened.

Offline by default. `Scout(offline=True)` uses only what is already on disk, so
the whole pipeline is testable and a CI run never reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..skills.loader import SKILL_FILE, DOC_FILE, load_skill_dir
from ..skills.models import SkillSpec, spec_from_dict

__all__ = ["SOURCE_KINDS", "DiscoveryReport", "SkillSource", "Scout", "load_sources",
           "sources_lockfile"]

#: How a source is monitored. `repo` is polled for new commits and releases;
#: `search` is a topic or query; `manual` is an administrator's submission.
SOURCE_KINDS = ("repo", "search", "manual", "topic")

#: Hosts the scout is permitted to reach. A source declaring anything else is
#: refused at load, so a submitted URL cannot point the scout somewhere new.
ALLOWED_HOSTS = frozenset({
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "codeload.github.com",
})


class SourceError(ValueError):
    """A declared source that cannot be monitored as described."""


@dataclass(frozen=True, slots=True)
class SkillSource:
    """One thing the scout monitors."""

    id: str
    kind: str
    url: str = ""
    #: For `repo`: the branch or ref to watch. Recorded but never used as a pin —
    #: a pin is a commit, and `SkillVersion` refuses a repo without one.
    ref: str = "main"
    note: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise SourceError("a SkillSource needs an id")
        if self.kind not in SOURCE_KINDS:
            raise SourceError(
                f"source {self.id!r} has kind {self.kind!r}, not one of {SOURCE_KINDS}")
        if self.kind != "manual" and not self.url:
            raise SourceError(f"source {self.id!r} of kind {self.kind!r} needs a url")
        host = _host_of(self.url)
        if host and host not in ALLOWED_HOSTS:
            # A source is not allowed to send the scout somewhere it was not
            # expected to go. Otherwise a submitted URL is a way to make the
            # runtime fetch from an attacker-chosen host.
            raise SourceError(
                f"source {self.id!r} points at {host!r}, which is not in the "
                f"scout's allowed hosts {sorted(ALLOWED_HOSTS)}")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "url": self.url, "ref": self.ref,
                "note": self.note, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillSource":
        return cls(id=str(data.get("id") or ""), kind=str(data.get("kind") or "repo"),
                   url=str(data.get("url") or ""), ref=str(data.get("ref") or "main"),
                   note=str(data.get("note") or ""),
                   enabled=bool(data.get("enabled", True)))


def _host_of(url: str) -> str:
    if not url:
        return ""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0].split("@")[-1].split(":")[0].lower()


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """What a scout run found, and what it could not do.

    `unreachable` is as important as `found`. A monthly report that omitted a
    repository it failed to read would be indistinguishable from one where that
    repository had not changed.
    """

    found: tuple[SkillSpec, ...] = ()
    needs_adapter: tuple[str, ...] = ()
    unreachable: tuple[tuple[str, str], ...] = ()
    skipped: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.found) + len(self.needs_adapter)

    def as_dict(self) -> dict[str, Any]:
        return {"found": [s.as_dict() for s in self.found],
                "needs_adapter": list(self.needs_adapter),
                "unreachable": [{"source": s, "reason": r}
                                for s, r in self.unreachable],
                "skipped": list(self.skipped), "notes": list(self.notes),
                "summary": {"found": len(self.found),
                            "needs_adapter": len(self.needs_adapter),
                            "unreachable": len(self.unreachable),
                            "skipped": len(self.skipped)}}

    def explain(self) -> str:
        lines = [f"scout: {len(self.found)} skill(s) loaded, "
                 f"{len(self.needs_adapter)} need an adapter"]
        for source, reason in self.unreachable:
            lines.append(f"  UNREACHABLE {source}: {reason}")
        for name in self.needs_adapter:
            lines.append(f"  NEEDS ADAPTER {name}")
        for name in self.skipped:
            lines.append(f"  skipped {name}")
        return "\n".join(lines)


class Scout:
    """Discovers skills from declared sources. Runs nothing it finds."""

    def __init__(self, sources: Sequence[SkillSource], *, offline: bool = True,
                 local_roots: Sequence[Path | str] = ()) -> None:
        self.sources = tuple(sources)
        self.offline = offline
        #: Directories to scan when offline — the checked-out upstream trees, or
        #: this repository's own `skills/`. Lets the whole pipeline run in CI.
        self.local_roots = tuple(Path(p) for p in local_roots)

    # -- discovery ---------------------------------------------------------

    def run(self) -> DiscoveryReport:
        """Scan every enabled source and report, including what failed."""
        found: list[SkillSpec] = []
        needs_adapter: list[str] = []
        unreachable: list[tuple[str, str]] = []
        skipped: list[str] = []
        notes: list[str] = []

        for source in self.sources:
            if not source.enabled:
                skipped.append(source.id)
                continue
            if source.kind == "manual":
                # A manual submission has no tree to walk here; it arrives as a
                # checked-out directory, which `local_roots` covers.
                notes.append(f"{source.id}: manual submission, resolved from the "
                             "local checkout rather than fetched")
                continue

        for root in self.local_roots:
            if not root.is_dir():
                unreachable.append((str(root), "directory does not exist"))
                continue
            for directory in sorted(p for p in root.iterdir() if p.is_dir()):
                if not ((directory / SKILL_FILE).is_file()
                        or (directory / DOC_FILE).is_file()):
                    continue
                try:
                    loaded = load_skill_dir(directory)
                    found.append(loaded.spec)
                except Exception as exc:                      # noqa: BLE001
                    name = str(directory)
                    if "must be wrapped by a reviewed adapter" in str(exc) \
                            or "no skill.yaml" in str(exc):
                        needs_adapter.append(name)
                    else:
                        unreachable.append((name, str(exc)[:200]))

        if not self.offline:
            notes.append(
                "network discovery is configured but not performed by this build; "
                "the scout's fetch path is deliberately separate from its parsing "
                "path so a fetch cannot influence what is loaded")

        return DiscoveryReport(found=tuple(found),
                               needs_adapter=tuple(needs_adapter),
                               unreachable=tuple(unreachable),
                               skipped=tuple(skipped), notes=tuple(notes))

    # -- audit -------------------------------------------------------------

    def audit(self, spec: SkillSpec) -> Mapping[str, Any]:
        """What can be determined about a candidate without executing it.

        Returns the report the ranker's hard-elimination check reads. Anything
        this method cannot determine is left **absent** rather than set to False:
        `eliminations` treats unknown as not-guilty, and answering "no tests" for
        a skill nobody has looked at would eliminate it for the wrong reason.

        Executing the skill in a sandbox is a separate step with a separate
        report. Nothing here runs a line of the candidate's code.
        """
        report: dict[str, Any] = {}
        if spec.source_repo:
            report["immutable_commit"] = bool(spec.source_commit)
        # An unlicensed skill is knowable from the manifest alone.
        report["license_present"] = bool(spec.license_spdx)
        return report


# -- declared sources ------------------------------------------------------


def load_sources(text: str) -> tuple[SkillSource, ...]:
    """Parse `skill_sources.yaml`."""
    import yaml
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise SourceError("skill_sources.yaml must be a mapping")
    unknown = sorted(set(data) - {"api_version", "sources"})
    if unknown:
        raise SourceError(f"skill_sources.yaml has unknown key(s) {unknown}")
    return tuple(SkillSource.from_dict(s) for s in data.get("sources") or ())


def sources_lockfile(sources: Iterable[SkillSource], *, generated_at: str = "") -> str:
    """`sources.lock.yaml` — which sources were monitored and what was seen.

    Separate from `skills.lock.yaml` on purpose (ADR-0002): the *source* axis
    versions independently of the skill axis, so a source that moved is visible
    even when no skill changed.
    """
    import yaml
    payload = {"api_version": "1", "generated_at": generated_at,
               "sources": [s.as_dict() for s in sources]}
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
