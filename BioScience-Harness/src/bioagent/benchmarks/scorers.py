"""Scoring a run against a Season, per track and per dimension.

Each track's metric answers a different question, and the plan names them
specifically. This module implements them as functions over a `RunRecord` and a
case's `gold`, so a score is reproducible from the two documents rather than
depending on a scorer's hidden state.

Two behaviours are worth calling out because they are where a scoring harness
usually goes wrong.

**A refusal is not a failure.** When a run declines a claim — code `ART106`, say,
because the evidence was only a docking prediction — that is the system behaving
correctly. `claim_calibration` counts refusals as *good*: a system that abstains
where the evidence does not reach is scoring higher than one that asserts
anyway, which is the opposite of how a naive accuracy metric would treat it.

**A hard gate zeroes the board, not the score.** A run with a fabricated citation
still has a `task_success` number, and reporting it as 0.0 would hide how well
the rest of the system worked. The score stands and `gates_failed` carries the
block, so the Experimental board can still show the decomposition beside the
reason it is not on the trusted one.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .models import (BenchmarkCase, CaseScore, RunRecord, SCORE_DIMENSIONS,
                     ScoreComponents)

__all__ = ["GATES", "GATE_DESCRIPTIONS", "score_run", "score_track"]

#: The plan's hard gates. A run failing any of these cannot enter the trusted
#: board; it appears on the Experimental board with the reason.
GATES: Mapping[str, str] = {
    "GATE001": "severe safety false-negative rate above threshold",
    "GATE002": "fabricated citation rate is not zero",
    "GATE003": "no re-runnable artifact was produced",
    "GATE004": "a clinical efficacy conclusion rested on prediction-only evidence",
}

GATE_DESCRIPTIONS: Mapping[str, str] = {
    "GATE001": ("severe false negatives are cases where a high-risk signal was "
                "present in the gold label and the run did not surface it; the "
                "threshold is zero for critical-severity cases"),
    "GATE002": ("a citation that does not resolve, or resolves to a source that "
                "does not contain the quoted text, is fabricated; the threshold is "
                "exactly zero, because a system that invents citations cannot be "
                "trusted on anything else it reports"),
    "GATE003": ("the run must produce an artifact that re-runs to the same result "
                "under the same four version axes"),
    "GATE004": ("stating a clinical efficacy conclusion on evidence that is only "
                "computational prediction is the failure this project exists to "
                "prevent; it is a gate rather than a low score because it is a "
                "category error, not a quality shortfall"),
}


def score_run(run: RunRecord, cases: Sequence[BenchmarkCase]) -> dict[str, Any]:
    """Score one run across a Season.

    Returns a leaderboard row carrying every dimension, the aggregate, the four
    version axes and any gate failures. The row is built here rather than at the
    renderer so a renderer cannot lose the decomposition.
    """
    from .models import aggregate

    by_id = {c.id: c for c in cases}
    scored = [s for s in run.scores if s.case_id in by_id]

    gates = sorted({g for s in scored for g in s.components.gates_failed})
    row = aggregate(scored)

    # Per-track breakdown, so the leaderboard can show a system that is strong on
    # entity resolution and weak on safety rather than one blended number.
    per_track: dict[str, Any] = {}
    for track in sorted({s.track for s in scored}):
        members = [s for s in scored if s.track == track]
        track_row = aggregate(members)
        per_track[track] = track_row

    return {
        "run_id": run.run_id, "system": run.system,
        "submission_type": run.submission_type, "season": run.season,
        "versions": dict(run.composite_version),
        "dimensions": row["dimensions"], "aggregate": row["aggregate"],
        "raw": row["raw"], "n_cases": row["n_cases"], "n_failed": row["n_failed"],
        "gates_failed": gates,
        "gate_reasons": [GATE_DESCRIPTIONS.get(g, g) for g in gates],
        "trusted": not gates,
        "board": "trusted" if not gates else "experimental",
        "per_track": per_track,
        "trace_digest": run.trace_digest,
        "artifact_digest": run.artifact_digest,
    }


def score_track(track: str, run: RunRecord, cases: Sequence[BenchmarkCase]
                ) -> dict[str, Any]:
    """Score one track. The unit a track-specific leaderboard is built from."""
    members = [c for c in cases if c.track == track]
    if not members:
        return {"track": track, "n_cases": 0, "aggregate": 0.0,
                "note": "no cases for this track"}
    subset = RunRecord(run_id=run.run_id, system=run.system,
                       submission_type=run.submission_type, season=run.season,
                       composite_version=run.composite_version,
                       scores=tuple(s for s in run.scores if s.track == track),
                       started_at=run.started_at, finished_at=run.finished_at,
                       trace_digest=run.trace_digest,
                       artifact_digest=run.artifact_digest)
    row = score_run(subset, members)
    return {"track": track, **row}


# --------------------------------------------------------------------------
# the three gates that need a run-level judgement
# --------------------------------------------------------------------------


def fabricated_citation_rate(claims: Sequence[Mapping[str, Any]]) -> float:
    """Fraction of claims whose citation does not resolve.

    Zero tolerance. A system that invents one citation has demonstrated that its
    provenance cannot be relied on, and a provenance mechanism that works most of
    the time is not a provenance mechanism.
    """
    if not claims:
        return 0.0
    bad = [c for c in claims if c.get("citation_resolves") is False]
    return len(bad) / len(claims)


def severe_false_negative_rate(scores: Sequence[CaseScore]) -> float:
    """Fraction of critical-severity cases a run failed to surface.

    Only critical cases count. A missed moderate signal is a quality shortfall; a
    missed critical one is the failure mode the safety skill is built to avoid,
    and averaging them would let a good record on mild cases hide a dangerous one.
    """
    critical = [s for s in scores
                if s.components.notes.get("severity") in ("high", "critical")
                or s.components.notes.get("critical") == "true"]
    if not critical:
        return 0.0
    missed = [s for s in critical
              if s.components.notes.get("surfaced") == "false"]
    return len(missed) / len(critical)


def prediction_as_fact_claims(claims: Sequence[Mapping[str, Any]]
                              ) -> tuple[str, ...]:
    """Ids of claims asserting a clinical finding on prediction-only evidence.

    The gate the whole project is organised around, and the one place where the
    kernel's refusal and the benchmark's measurement meet: a run that produced
    such a claim has already been refused by the kernel, so its presence here
    means the refusal was bypassed, which is itself the finding.
    """
    out = []
    for claim in claims:
        if claim.get("prediction_as_fact") or (
                claim.get("claim_kind") in ("efficacy", "recommendation")
                and claim.get("evidence_designs")
                and set(claim["evidence_designs"]) <= {
                    "in_silico", "network_prediction", "docking", "molecular_dynamics",
                    "target_prediction", "pathway_enrichment"}):
            out.append(str(claim.get("id", "")))
    return tuple(out)


def gate_failures(*, claims: Sequence[Mapping[str, Any]] = (),
                  scores: Sequence[CaseScore] = (),
                  artifact_reruns: bool | None = None) -> tuple[str, ...]:
    """Evaluate every run-level gate. Returns the codes that failed."""
    gates: list[str] = []
    if fabricated_citation_rate(claims) > 0.0:
        gates.append("GATE002")
    if severe_false_negative_rate(scores) > 0.0:
        gates.append("GATE001")
    if artifact_reruns is False:
        gates.append("GATE003")
    if prediction_as_fact_claims(claims):
        gates.append("GATE004")
    return tuple(gates)
