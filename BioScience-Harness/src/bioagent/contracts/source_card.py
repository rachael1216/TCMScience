"""SourceCard — everything a downstream artifact must know about where data came from.

The plan's E1-01 asks for a schema that records licence, version and access
method. Those three are not enough on their own, and the gap is the one that
bites in practice: two runs against "HERB 2.0" three months apart are not the
same experiment if the database moved underneath them, and nothing in a licence
field or a version string records that.

So the card is pinned by **content**, not by name or date:

* ``snapshot_hash`` — SHA-256 over the canonical form of every declared
  operation's response, taken at ``snapshot_at``. This is the version. "HERB
  2.0" is a label a maintainer chose; the hash is a fact about bytes.
* ``snapshot_at`` — when that hash was taken, so a reader can judge staleness.

A card with no snapshot is :attr:`SourceCard.pinned` false, and the artifact
validator refuses to let an *unpinned* source support a publishable claim. That
is the enforcement half of ADR-0002: a research object cannot cite a source
whose contents were never fixed.

Access is described, not attempted. This module never opens a socket; it says
what a connector would be allowed to do, which is what the bridge and the
licence lattice need to rule on it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = ["AccessMethod", "SourceCard", "SourceKind", "canonical_hash"]

#: How a source is reached. This drives what the bridge derives: a card whose
#: method is ``HTTP_API`` implies an allowed host and a PUBLIC_REMOTE
#: destination; ``LOCAL_FILE`` implies PERSISTENT and no network at all.
ACCESS_METHODS = ("http_api", "bulk_download", "local_file", "mcp", "manual_import",
                  "literature_index", "registry_query")

#: What kind of thing the source is, which is *not* the same as its evidence
#: strength. A database can hold case reports and RCTs side by side.
SOURCE_KINDS = ("database", "literature_index", "trial_registry", "pharmacopoeia",
                "ontology", "classical_corpus", "dataset", "compound_library",
                "safety_register", "guideline")

AccessMethod = str
SourceKind = str


def canonical_hash(value: Any) -> str:
    """SHA-256 over a canonical JSON rendering of ``value``.

    Sorted keys, no NaN, compact separators, UTF-8. Deterministic across
    platforms and Python versions, which is the whole requirement for a hash
    that is going to be quoted in a paper and re-checked by a reviewer.
    """
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceCard:
    """Identity, provenance, licence and access policy for one external source."""

    id: str
    name: str
    kind: SourceKind
    #: Owner or maintainer as they describe themselves, e.g. "Institute of
    #: Chinese Materia Medica, CACMS". Attribution, not authorisation.
    maintainer: str = ""
    version: str = ""
    #: Canonical URL a human would cite, and the machine endpoint if different.
    home_url: str = ""
    endpoint: str = ""
    access_method: AccessMethod = "http_api"
    #: Hosts a connector to this source may reach, exactly as declared. The
    #: bridge turns these into destinations; an undeclared host is refused.
    allowed_hosts: tuple[str, ...] = ()
    #: SPDX id, or "" when the licence is unclear. Empty is honest and is
    #: treated as "no grant" by the licence lattice, never as permissive.
    license_spdx: str = ""
    #: The licence as the maintainer wrote it, kept verbatim when it does not
    #: reduce to one SPDX id ("CC-BY / mixed per article"). Nothing is guessed.
    license_note: str = ""
    integration_mode: str = "federated"
    #: SHA-256 over the snapshot, and when it was taken. Both or neither.
    snapshot_hash: str = ""
    snapshot_at: str = ""
    #: Per-operation content hashes, so a partial change is localisable rather
    #: than just invalidating the whole card.
    operation_hashes: Mapping[str, str] = field(default_factory=dict)
    #: Free-text limits: coverage dates, known gaps, language bias, update
    #: cadence. These travel into the artifact so a reader sees them.
    known_limits: tuple[str, ...] = ()
    #: True when the source is safe to use with no network at all.
    offline_capable: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("SourceCard needs a non-empty id and name")
        if self.access_method not in ACCESS_METHODS:
            raise ValueError(
                f"access_method {self.access_method!r} is not one of {ACCESS_METHODS}")
        if self.kind not in SOURCE_KINDS:
            raise ValueError(f"kind {self.kind!r} is not one of {SOURCE_KINDS}")
        if bool(self.snapshot_hash) != bool(self.snapshot_at):
            raise ValueError(
                "snapshot_hash and snapshot_at must be set together: a hash with "
                "no date cannot be judged for staleness, and a date with no hash "
                "pins nothing")
        if self.snapshot_hash and len(self.snapshot_hash) != 64:
            raise ValueError("snapshot_hash must be a 64-character SHA-256 hex digest")
        if self.access_method in ("http_api", "bulk_download", "literature_index",
                                  "registry_query") and not self.allowed_hosts:
            raise ValueError(
                f"access_method {self.access_method!r} reaches the network and must "
                "declare allowed_hosts; an undeclared host cannot be gated")

    @property
    def pinned(self) -> bool:
        """Whether the source's contents were fixed at a known moment."""
        return bool(self.snapshot_hash and self.snapshot_at)

    @property
    def licensed(self) -> bool:
        """Whether a licence was identified at all. Not the same as *usable* —
        whether the licence permits a given integration mode is the lattice's
        question, and this module does not answer it."""
        return bool(self.license_spdx)

    @property
    def citation(self) -> str:
        bits = [self.maintainer or self.name, self.name]
        if self.version:
            bits[1] = f"{self.name} {self.version}"
        if self.home_url:
            bits.append(self.home_url)
        if self.pinned:
            bits.append(f"snapshot {self.snapshot_hash[:12]} at {self.snapshot_at}")
        return " · ".join(b for b in bits if b)

    def opens_network(self) -> bool:
        return self.access_method in ("http_api", "bulk_download", "literature_index",
                                      "registry_query")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "maintainer": self.maintainer, "version": self.version,
                "home_url": self.home_url, "endpoint": self.endpoint,
                "access_method": self.access_method,
                "allowed_hosts": list(self.allowed_hosts),
                "license_spdx": self.license_spdx, "license_note": self.license_note,
                "integration_mode": self.integration_mode,
                "snapshot_hash": self.snapshot_hash, "snapshot_at": self.snapshot_at,
                "operation_hashes": dict(self.operation_hashes),
                "known_limits": list(self.known_limits),
                "offline_capable": self.offline_capable, "notes": self.notes,
                "pinned": self.pinned, "licensed": self.licensed,
                "citation": self.citation}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceCard":
        return cls(
            id=str(data["id"]), name=str(data["name"]), kind=str(data.get("kind") or "database"),
            maintainer=str(data.get("maintainer") or ""), version=str(data.get("version") or ""),
            home_url=str(data.get("home_url") or ""), endpoint=str(data.get("endpoint") or ""),
            access_method=str(data.get("access_method") or "http_api"),
            allowed_hosts=tuple(data.get("allowed_hosts") or ()),
            license_spdx=str(data.get("license_spdx") or ""),
            license_note=str(data.get("license_note") or ""),
            integration_mode=str(data.get("integration_mode") or "federated"),
            snapshot_hash=str(data.get("snapshot_hash") or ""),
            snapshot_at=str(data.get("snapshot_at") or ""),
            operation_hashes=dict(data.get("operation_hashes") or {}),
            known_limits=tuple(data.get("known_limits") or ()),
            offline_capable=bool(data.get("offline_capable")),
            notes=str(data.get("notes") or ""))


SOURCE_CARD_SCHEMA: dict[str, Any] = {
    "title": "SourceCard",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "name", "kind", "access_method"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": list(SOURCE_KINDS)},
        "maintainer": {"type": "string"},
        "version": {"type": "string"},
        "home_url": {"type": "string"},
        "endpoint": {"type": "string"},
        "access_method": {"type": "string", "enum": list(ACCESS_METHODS)},
        "allowed_hosts": {"type": "array", "items": {"type": "string"}},
        "license_spdx": {"type": "string"},
        "license_note": {"type": "string"},
        "integration_mode": {"type": "string", "enum": ["vendor", "native", "federated"]},
        "snapshot_hash": {"type": "string", "pattern": "^([0-9a-f]{64})?$"},
        "snapshot_at": {"type": "string"},
        "operation_hashes": {"type": "object", "additionalProperties": {"type": "string"}},
        "known_limits": {"type": "array", "items": {"type": "string"}},
        "offline_capable": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "description": "Licence, access policy and content pin for one external source.",
}


def merge_operation_hashes(cards: Sequence[SourceCard]) -> str:
    """One hash over a set of cards, so an artifact can name its whole source set."""
    return canonical_hash([c.as_dict() for c in sorted(cards, key=lambda c: c.id)])
