"""Monthly skill discovery, audit and promotion — the governed update path.

    candidate  ──audit──▶ eliminated? ──no──▶ scored ──human──▶ stable
                              │                                 │
                             yes                          Season frozen
                              │                                 │
                        stays visible                     (immutable)

The rule this package exists to enforce, from the plan and ADR-0001:

> Automatic discovery, automatic audit, automatic sandbox test, automatic
> benchmark, automatic candidate PR — and a human decision before anything
> reaches production. A popular skill is never auto-activated.

Three modules, three responsibilities, no overlap:

* :mod:`registry` — the three catalogues and the one-way promotion path. The only
  writer of a stable entry, and it requires a `PromotionDecision`.
* :mod:`ranker` — hard eliminations first, then the 100-point score. Community
  growth is capped at 5/100 and the cap is asserted, because "don't let stars
  drive adoption" is a commitment that has to be enforced rather than stated.
* :mod:`scout` — discovery from declared sources. Finds candidates; never
  promotes one.

Nothing here executes a discovered skill. Sandbox execution is a separate step
with a separate report, and it writes its findings into the audit record the
ranker reads. Keeping discovery and execution apart means the module that talks
to the network cannot also be the module that decides what runs.
"""

from __future__ import annotations

from .ranker import (DIMENSIONS, ELIMINATIONS, GROWTH_CEILING, Score, eliminations,
                     rank, score_candidate, to_candidate)
from .registry import (DECISION_KINDS, CandidateSkill, PromotionDecision,
                       PromotionRefused, Registry, RegistryError, RegistryRelease,
                       SkillVersion, StableSkill, content_digest, load_lockfile,
                       version_from_spec)
from .scout import (ALLOWED_HOSTS, SOURCE_KINDS, DiscoveryReport, Scout, SkillSource,
                    SourceError, load_sources, sources_lockfile)

__all__ = [
    # registry
    "DECISION_KINDS", "CandidateSkill", "PromotionDecision", "PromotionRefused",
    "Registry", "RegistryError", "RegistryRelease", "SkillVersion", "StableSkill",
    "content_digest", "load_lockfile", "version_from_spec",
    # ranker
    "DIMENSIONS", "ELIMINATIONS", "GROWTH_CEILING", "Score", "eliminations", "rank",
    "score_candidate", "to_candidate",
    # scout
    "ALLOWED_HOSTS", "SOURCE_KINDS", "DiscoveryReport", "Scout", "SkillSource",
    "SourceError", "load_sources", "sources_lockfile",
]
