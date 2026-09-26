"""Shared machinery for the four P0 skills.

Moved out of `skills/base.py` so the package boundary is visible: `base.py` is
the generic artifact-assembly helper any skill may use, and this module is the
part the four TCM skills specifically share — their version table, their
sparse-and-honest quality heuristic, and the seed-corpus source card they all
declare.

**The seed knowledge base is a source, and it is declared as one.** All four
skills read `bioagent.tcm.knowledge`, which is compiled into the package rather
than fetched. That is not a reason to leave it out of an artifact: a resolution
is only checkable if the reader knows which corpus produced it. So
:func:`seed_source_card` builds a real `SourceCard` for it — `local_file`,
offline, `native` — pinned by a hash over the corpus *contents*. Swapping the
seed data changes the hash, which is what makes the pin mean something.
"""

from __future__ import annotations

from typing import Any, Sequence

from ...contracts import Directness, EvidenceQuality
from ...tcm.model import CLAIM_KINDS
from ...tcm.model import EvidenceTier

__all__ = ["SKILL_VERSIONS", "SEED_SOURCE_ID", "SEED_SNAPSHOT_AT", "SEED_CORPUS",
           "_now", "_quality_for", "seed_source_card", "strongest_supported_kind"]

#: Version of each P0 skill. Kept here rather than in each module so a release
#: bump is one edit, and so the registry lockfile and the artifacts they produce
#: cannot disagree about which version ran.
SKILL_VERSIONS = {
    "normalize-tcm-entities": "1.0.0",
    "retrieve-tcm-evidence": "1.0.0",
    "analyze-tcm-network-pharmacology": "1.0.0",
    "assess-tcm-safety": "1.0.0",
}

SEED_SOURCE_ID = "tcmscience.tcm.seed"

#: Pinned snapshot date for the shipped corpus. Bumped whenever the seed data
#: changes, which is what makes the hash a pin rather than a decoration.
SEED_SNAPSHOT_AT = "2026-09-25T00:00:00Z"

#: The seed collections, so a snapshot hash is attributable to a set of tables
#: rather than to "the code".
SEED_CORPUS = ("_HERBS", "_PROCESSED", "_FORMULAS", "_SYNDROMES", "_PASSAGES",
               "_STUDIES", "_RELATIONS", "_SAFETY")


def _now() -> str:
    """Wall clock, isolated so an artifact's timestamp has one source."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quality_for(tier: EvidenceTier, *, assessed_by: str) -> EvidenceQuality:
    """Quality dimensions appropriate to what this corpus actually contains.

    Deliberately sparse, and the sparseness is the design. The seed corpus holds
    pharmacopoeia entries and textbook records — no trials, no cohorts — so there
    is nothing to judge for risk of bias, precision or consistency. Those stay
    `NOT_ASSESSED` rather than being filled with a plausible-looking `LOW`,
    because an unassessed dimension that reads as "fine" is how a quality summary
    begins to lie.

    `directness` *is* assessed, because it is the one dimension this corpus can
    speak to honestly: a classical text recording a traditional use is direct
    evidence for an attribution claim and indirect for anything about a patient.
    """
    directness = (Directness.DIRECT if tier <= EvidenceTier.EXPERT_EXPERIENCE
                  else Directness.PARTIAL)
    return EvidenceQuality(
        directness=directness,
        rationale={"directness":
                   "direct for attribution and traditional-use claims; not evidence "
                   "about clinical outcomes"},
        assessed_by=assessed_by, assessment_tool="corpus-tier-heuristic")


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items())}
    if hasattr(value, "value"):          # enum
        return value.value
    return str(value)


def _seed_payload() -> dict[str, Any]:
    """The seed corpus as plain data, for hashing.

    Read from the module's own tables rather than by calling `seed()`, because
    the point is to hash the *data*: a change to how those tables are assembled
    into a knowledge base is a code change, not a data change, and should not
    silently invalidate every published resolution.
    """
    from ...tcm import knowledge as tcm_knowledge

    payload: dict[str, Any] = {}
    for name in SEED_CORPUS:
        table = getattr(tcm_knowledge, name, None)
        if table is None:
            continue
        rows = []
        for row in table:
            if hasattr(row, "__dataclass_fields__"):
                rows.append({f: _plain(getattr(row, f)) for f in row.__dataclass_fields__})
            else:
                rows.append(_plain(row))
        payload[name] = rows
    return payload


def seed_source_card():
    """A pinned `SourceCard` for the shipped TCM seed corpus."""
    from ...contracts import SourceCard
    from ...contracts.source_card import canonical_hash

    return SourceCard(
        id=SEED_SOURCE_ID, name="TCMScience TCM seed corpus", kind="pharmacopoeia",
        maintainer="TCMScience", version="0.2.6",
        home_url="https://github.com/psknlr/TCMScience",
        access_method="local_file",
        license_spdx="MIT", integration_mode="native", offline_capable=True,
        snapshot_hash=canonical_hash(_seed_payload()),
        snapshot_at=SEED_SNAPSHOT_AT,
        known_limits=(
            "seed corpus only: 23 herbs, 5 processed forms, 6 formulas, 8 syndromes, "
            "9 passages, 2 study records, 14 relations, 14 safety records",
            "no clinical study data is included; every study record is at "
            "EXPERT_EXPERIENCE or below",
            "coverage is illustrative, not exhaustive; absence from this corpus is "
            "not evidence of absence",
        ),
        notes="Compiled into the package. Declared as a source so a resolution can be "
              "traced to the corpus that produced it.")


def strongest_supported_kind(evidence: Sequence[Any],
                             permitted: Sequence[str]) -> str:
    """The strongest claim kind these items can actually license.

    Skills take this rather than naming a kind directly, because naming one is
    how a skill ends up asserting more than its evidence carries. The case that
    forced the helper: `assess-tcm-safety` wants to make a `safety_signal` claim
    about a 十八反 contraindication, but that floor is `CASE_REPORT` and the
    corpus's records are pharmacopoeia entries at `EXPERT_EXPERIENCE`. The
    contract refused it — correctly, because

        "the classics record these two as incompatible"

    and

        "there is clinical evidence of harm from this combination"

    are different statements, and only the second is a safety signal. Both are
    safety-relevant; they are not interchangeable, and a pharmacovigilance tool
    that conflated them would be overstating classical knowledge as clinical
    evidence.

    Returns the highest-ranked ``permitted`` kind whose tier floor is met by at
    least one item, preferring the first-listed on ties. Raises when nothing is
    supported, so the caller must handle that case rather than emitting a claim
    it cannot support.
    """
    if not evidence:
        raise ValueError("no evidence: no claim kind is supported")
    best_tier = max(e.tier for e in evidence)
    for kind in permitted:
        floor = CLAIM_KINDS.get(kind)
        if floor is not None and best_tier >= floor:
            return kind
    raise ValueError(
        f"evidence at {best_tier.name.lower()} supports none of the permitted claim "
        f"kinds {list(permitted)}; the skill would have to claim more than it has")


# Re-exported so a skill module needs one import from here, not three.
from ..base import artifact, file_of, json_file  # noqa: E402,F401
