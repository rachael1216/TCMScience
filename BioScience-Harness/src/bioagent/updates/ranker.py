"""Scoring a candidate skill, and the conditions under which no score applies.

The plan's 100-point table, implemented with its weight structure intact. Two
properties of the design are load-bearing and easy to lose in an implementation:

**Hard elimination comes before scoring, not after.** The plan lists seven
conditions that do not participate in the score — a missing licence, an
unpinnable commit, a supply-chain vulnerability, a script that evades the runtime,
no tests, output that fails the artifact schema, or a claim presented as clinical
fact. A candidate failing any of them is not "low-scoring"; it is *ineligible*,
and a ranker that summed them into a low number would let a high enough score
elsewhere outvote a licensing problem. So :func:`eliminations` runs first and its
result is not negotiable.

**Community growth is capped at 5 points, and the cap is enforced.** The plan
says star count must not drive adoption, and 5/100 is the whole of that
commitment. `GROWTH_CEILING` is asserted rather than trusted, so a future edit
that quietly raises the weight fails a test instead of shipping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .registry import CandidateSkill
from ..skills.models import SkillSpec

__all__ = ["DIMENSIONS", "ELIMINATIONS", "GROWTH_CEILING", "Score", "eliminations",
           "rank", "score_candidate"]

#: The plan's table. Weights sum to 100 and that is asserted by a test.
DIMENSIONS: Mapping[str, int] = {
    "task_relevance": 25,
    "benchmark_gain": 25,
    "reproducibility": 15,
    "maturity": 10,
    "safety_permissions": 10,
    "license_clarity": 5,
    "community_growth": 5,
    "documentation": 5,
}

#: The plan's constraint: community growth may never be worth more than 5% of the
#: total. A candidate that is popular and wrong must not be able to outscore one
#: that is useful.
GROWTH_CEILING = 5

#: Hard eliminations. Keyed by a stable code; the value is what a reviewer reads.
ELIMINATIONS: Mapping[str, str] = {
    "ELIM001": "licence missing or forbids the intended use",
    "ELIM002": "cannot be pinned to an immutable commit",
    "ELIM003": "requires circumventing a login, captcha or access control",
    "ELIM004": "serious supply-chain vulnerability",
    "ELIM005": "script bypasses the runtime to reach the network or spawn undeclared "
               "processes",
    "ELIM006": "no minimal test",
    "ELIM007": "output cannot satisfy the artifact schema",
    "ELIM008": "presents a computational prediction as an experimental or clinical fact",
}


@dataclass(frozen=True, slots=True)
class Score:
    """A scored candidate: its per-dimension breakdown and its total."""

    skill_id: str
    total: float
    breakdown: Mapping[str, float]
    weights: Mapping[str, int] = field(default_factory=lambda: dict(DIMENSIONS))
    notes: tuple[str, ...] = ()

    @property
    def growth_share(self) -> float:
        """How much of this score came from community growth."""
        return self.breakdown.get("community_growth", 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "total": round(self.total, 2),
                "breakdown": {k: round(v, 2) for k, v in self.breakdown.items()},
                "weights": dict(self.weights), "notes": list(self.notes),
                "growth_share": round(self.growth_share, 2)}

    def explain(self) -> str:
        lines = [f"{self.skill_id}: {self.total:.1f}/100"]
        for name, weight in self.weights.items():
            lines.append(f"  {name:<20} {self.breakdown.get(name, 0.0):5.1f} / {weight}")
        return "\n".join(lines)


def eliminations(spec: SkillSpec, *, report: Mapping[str, Any] | None = None
                 ) -> tuple[str, ...]:
    """Hard-elimination codes for this candidate. Empty means eligible.

    ``report`` carries what an audit found that the manifest cannot state about
    itself: `vulnerabilities`, `tests_present`, `artifact_schema_ok`,
    `immutable_commit`, `login_required`, `prediction_as_fact`. A missing key is
    treated as *unknown* and does **not** eliminate — an audit that did not run
    is not a finding of guilt, and the right response to "not checked" is to
    check, which the registry records as a candidate still awaiting audit rather
    than one that passed.
    """
    report = dict(report or {})
    codes: list[str] = []

    # ELIM001 — licence. An empty SPDX id is "no grant identified", which the
    # licence lattice treats as no permission to redistribute. For a candidate
    # that would be vendored this is fatal; `integration_mode` decides whether it
    # is, and the caller has already set it.
    if not spec.license_spdx:
        codes.append("ELIM001")
    else:
        from psh.licensing import license_ruling
        ruling = license_ruling(spec.license_spdx, spec.integration_mode)
        if not ruling.allowed:
            codes.append("ELIM001")

    # ELIM002 — pinnability. A `source_repo` with no commit is a moving target.
    if report.get("immutable_commit") is False:
        codes.append("ELIM002")
    elif spec.source_repo and not spec.source_commit:
        codes.append("ELIM002")

    # ELIM003 — access control circumvention.
    if report.get("login_required") is True:
        codes.append("ELIM003")

    # ELIM004 — supply chain.
    if report.get("vulnerabilities"):
        codes.append("ELIM004")

    # ELIM005 — runtime evasion. `secrets` outside the declared set and a
    # subprocess permission with no entrypoint are both signs of a script that
    # means to reach around the gate.
    if report.get("evades_runtime") is True:
        codes.append("ELIM005")

    # ELIM006 — no test. Only eliminates when the audit positively found none.
    if report.get("tests_present") is False:
        codes.append("ELIM006")

    # ELIM007 — artifact schema.
    if report.get("artifact_schema_ok") is False:
        codes.append("ELIM007")

    # ELIM008 — prediction as fact. This is the one condition specific to this
    # project, and it is the failure the whole contract layer exists to catch. A
    # skill whose own documentation presents a docking result as clinical
    # efficacy is not a low-scoring skill; it is one that will produce overclaims
    # no matter how the pipeline is configured.
    if report.get("prediction_as_fact") is True:
        codes.append("ELIM008")

    return tuple(codes)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_candidate(spec: SkillSpec, *,
                    report: Mapping[str, Any] | None = None,
                    relevance: float = 0.0,
                    benchmark_gain: float = 0.0,
                    reproducibility: float = 0.0,
                    maturity: float = 0.0,
                    safety: float = 0.0,
                    license_clarity: float = 0.0,
                    community_growth: float = 0.0,
                    documentation: float = 0.0) -> Score:
    """Score a candidate on the plan's eight dimensions.

    Each input is a fraction in [0, 1] and is multiplied by its weight, so the
    total is out of 100 and each dimension's contribution is legible. Inputs are
    clamped rather than rejected: an audit tool that returned 1.2 for relevance
    should not crash a monthly job.

    A candidate with eliminations still returns a score — it is useful to see how
    a rejected skill would have ranked — but `CandidateSkill.eligible` is False and
    the registry refuses to promote it regardless of the number.
    """
    raw = {
        "task_relevance": _clamp(relevance),
        "benchmark_gain": _clamp(benchmark_gain),
        "reproducibility": _clamp(reproducibility),
        "maturity": _clamp(maturity),
        "safety_permissions": _clamp(safety),
        "license_clarity": _clamp(license_clarity),
        # Growth is capped *before* weighting, so no input can exceed the ceiling.
        "community_growth": _clamp(community_growth) * (
            GROWTH_CEILING / DIMENSIONS["community_growth"]),
        "documentation": _clamp(documentation),
    }
    breakdown = {name: raw[name] * weight for name, weight in DIMENSIONS.items()}
    total = sum(breakdown.values())

    notes: list[str] = []
    codes = eliminations(spec, report=report)
    if codes:
        notes.append("eliminated: " + "; ".join(ELIMINATIONS.get(c, c) for c in codes))
    if report and report.get("tests_present") is None:
        notes.append("test presence not audited; not treated as a failure")
    return Score(skill_id=spec.composite_id, total=total, breakdown=breakdown,
                 notes=tuple(notes))


def rank(candidates: Sequence[CandidateSkill]) -> tuple[CandidateSkill, ...]:
    """Order candidates for a review queue.

    Eligible first, then by score, then by id so the order is deterministic and a
    monthly diff is readable. Ineligible candidates are kept and ranked last
    rather than dropped: the backlog of things that cannot be adopted is
    information, and hiding it would make the pipeline look healthier than it is.
    """
    return tuple(sorted(candidates, key=lambda c: (not c.eligible, -c.score,
                                                   c.spec.composite_id)))


def to_candidate(spec: SkillSpec, score: Score, *, first_seen: str = "",
                 last_seen: str = "", recommendation: str = "",
                 report: Mapping[str, Any] | None = None) -> CandidateSkill:
    """Fold a score and its eliminations into a registry candidate."""
    codes = eliminations(spec, report=report)
    return CandidateSkill(spec=spec, first_seen=first_seen, last_seen=last_seen,
                          eliminated_by=codes, score=score.total,
                          score_breakdown=dict(score.breakdown),
                          recommendation=recommendation)
