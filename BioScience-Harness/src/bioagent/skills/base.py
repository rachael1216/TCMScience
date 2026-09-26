"""Shared machinery the P0 skills build their artifacts with.

Each skill's job is to turn an input into a `ResearchArtifact` that
`validate_artifact` will publish. That means assembling sources, evidence,
claims, file hashes, limitations and a composite version — repeatedly, and
identically. This module is that shared part, so a skill file contains its
*reasoning* and not its bookkeeping.

**Nothing here decides anything.** There is no scoring, no threshold and no
policy in this module. `file_of` hashes a file; `artifact()` assembles one. A
skill that wants to refuse something does it in the skill, where a reviewer can
find it.

The TCM-specific shared parts — the skill version table, the seed-corpus
`SourceCard`, the quality heuristic — live in ``p0/common.py`` instead, so that
this module stays generic and a future non-TCM skill can use it unchanged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from ..contracts import ArtifactFile, CandidateClaim, EvidenceItem, ResearchArtifact
from ..contracts.source_card import canonical_hash

__all__ = ["artifact", "contract_version", "file_of", "json_file"]


def contract_version() -> str:
    """The contract-layer version, for the artifact's `composite_version`."""
    from ..contracts import SCHEMAS
    from .models import SKILL_API_VERSION
    return f"contracts-{len(SCHEMAS)}/api-{SKILL_API_VERSION}"


def json_file(path: str, payload: Any, *, description: str = "") -> tuple[ArtifactFile, str]:
    """Write ``payload`` as canonical JSON and return its record and its text.

    The digest is over the *canonical* rendering, so a reformatted copy of the
    same data has the same hash and a changed value does not.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2,
                      allow_nan=False, default=str)
    return ArtifactFile(path=path,
                        sha256=hashlib.sha256(
                            json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                       allow_nan=False,
                                       default=str).encode("utf-8")).hexdigest(),
                        media_type="application/json", bytes=len(text.encode("utf-8")),
                        description=description), text


def file_of(path: str, content: str, *, media_type: str = "text/plain",
            description: str = "") -> ArtifactFile:
    """An `ArtifactFile` for in-memory content, addressed by its bytes."""
    data = content.encode("utf-8")
    return ArtifactFile(path=path, sha256=hashlib.sha256(data).hexdigest(),
                        media_type=media_type, bytes=len(data),
                        description=description)


def artifact(*, id: str, run_id: str, skill_id: str, skill_version: str,
             question: str, sources: Sequence[SourceCard],
             evidence: Sequence[EvidenceItem] = (),
             claims: Sequence[CandidateClaim] = (),
             outputs: Sequence[ArtifactFile] = (),
             limitations: Sequence[str] = (),
             assumptions: Sequence[str] = (),
             benchmark_version: str = "unversioned",
             source_axis: str = "", created_at: str = "",
             provenance: Mapping[str, Any] | None = None,
             notes: str = "") -> ResearchArtifact:
    """Assemble an artifact with all four version axes filled in.

    `composite_version` is built here rather than by each skill because ADR-0002
    makes it mandatory on every artifact, and a skill that forgot one axis would
    produce a result nobody could reproduce. The runtime and skill axes come from
    the installed packages; the source axis is the digest of the declared source
    set; the benchmark axis is what the caller says it is scored against.
    """
    from .. import __version__ as bioagent_version
    try:
        import psh
        runtime = f"psh-{psh.__version__}+bioagent-{bioagent_version}"
    except Exception:                                   # pragma: no cover
        runtime = f"bioagent-{bioagent_version}"

    return ResearchArtifact(
        id=id, run_id=run_id, skill_id=skill_id, skill_version=skill_version,
        composite_version={
            "runtime": runtime,
            "skill": f"{skill_id}@{skill_version}",
            "source": source_axis or canonical_hash(
                [s.as_dict() for s in sorted(sources, key=lambda s: s.id)]),
            "benchmark": benchmark_version,
        },
        question=question, created_at=created_at,
        sources=tuple(sources), evidence=tuple(evidence), claims=tuple(claims),
        outputs=tuple(outputs), limitations=tuple(limitations),
        assumptions=tuple(assumptions),
        provenance=dict(provenance or {}), produced_by=skill_id, notes=notes)
