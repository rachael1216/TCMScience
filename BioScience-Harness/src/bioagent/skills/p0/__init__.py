"""The four P0 TCM skills — the first stable registry contents.

Each is a pure function from an input to a `ResearchArtifact`, plus a
`skill.yaml` under `skills/tcm/<name>/` declaring what it may do. The manifests
are the authority; these modules are the implementations they point at.

What the four share is a refusal to tidy away ambiguity or absence:

* `normalize_tcm_entities` returns *candidates* for an ambiguous name and never
  merges silently.
* `retrieve_tcm_evidence` labels study design and reports retraction state,
  coverage limits and quality per dimension instead of reducing a body of
  evidence to one verdict.
* `analyze_tcm_network_pharmacology` keeps predicted and measured edges in
  separate collections and states that its mechanism claims rest on prediction.
* `assess_tcm_safety` returns `unknown` rather than `safe` when it has no
  record. The absence of a contraindication record is not the absence of a
  contraindication.
"""

from .assess_safety import assess_tcm_safety
from .common import SKILL_VERSIONS
from .entities import normalize_tcm_entities
from .netpharm import analyze_tcm_network_pharmacology
from .retrieve import retrieve_tcm_evidence

__all__ = ["SKILL_VERSIONS", "analyze_tcm_network_pharmacology", "assess_tcm_safety",
           "normalize_tcm_entities", "retrieve_tcm_evidence"]
