"""The benchmark harness: cases, runs, and the eight-dimension score.

    case  ──▶ run  ──▶ CaseScore  ──▶ aggregate  ──▶ leaderboard row

**What ships and what does not.** The schema, the loader, the scorers, the gate
definitions and the data card are published; the *cases and their gold labels*
are not (see `.gitignore`). A benchmark whose answers ship with it cannot
evaluate a system that has read the repository, and the hidden split only means
something while it stays hidden. `scripts/check_leakage.py` asserts that no case
content reaches a committed file or a published page.

**A refusal is not a failure.** When a run declines a claim because its evidence
does not reach — code `ART106`, say — that is correct behaviour and
`claim_calibration` counts it as such. A harness that scored abstention as a miss
would push systems toward asserting more, which is the opposite of the point.

**A hard gate does not zero the score.** A run that fabricated a citation still
has a `task_success` number; reporting it as 0 would hide how well the rest
worked. The score stands and `gates_failed` carries the block, so the
Experimental board can show the decomposition beside the reason the run is not on
the trusted one.
"""

from __future__ import annotations

from .models import (CASE_VISIBILITIES, LOWER_IS_BETTER, SCORE_DIMENSIONS, TRACKS,
                     BenchmarkCase, BenchmarkSeason, CaseError, CaseScore, RunRecord,
                     ScoreComponents, SplitViolation, aggregate, split_cases)
from .scorers import (GATES, GATE_DESCRIPTIONS, fabricated_citation_rate, gate_failures,
                      prediction_as_fact_claims, score_run, score_track,
                      severe_false_negative_rate)

__all__ = [
    "CASE_VISIBILITIES", "LOWER_IS_BETTER", "SCORE_DIMENSIONS", "TRACKS",
    "BenchmarkCase", "BenchmarkSeason", "CaseError", "CaseScore", "RunRecord",
    "ScoreComponents", "SplitViolation", "aggregate", "split_cases",
    "GATES", "GATE_DESCRIPTIONS", "fabricated_citation_rate", "gate_failures",
    "prediction_as_fact_claims", "score_run", "score_track",
    "severe_false_negative_rate",
]
