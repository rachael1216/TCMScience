"""Benchmark cases, runs and scores — the shapes, not the content.

The cases themselves are not published (see `.gitignore`): a benchmark whose gold
answers ship with it cannot evaluate a system that has read the repository. What
ships is everything needed to *reproduce* a score — the schema, the loader, the
scorers, the data card, and the procedure — so a reader can audit how a number
was produced even though they cannot re-derive it themselves.

**A case declares its visibility, and the loader enforces it.** `dev`, `hidden`
and `adversarial` are not tags for organisation; the split is what makes a
leaderboard mean anything. :func:`split_cases` refuses a case whose visibility
does not match the directory it was found in, so a hidden case cannot be
accidentally committed to the public tree and a dev case cannot be passed off as
held out.

**The scoring dimensions are named here, once.** The plan's eight dimensions are
the columns of every leaderboard row, and a run that reported an aggregate
without its decomposition would be refused by the Arena's own check. Naming them
as a frozen tuple is what lets that check be mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts.source_card import canonical_hash

__all__ = ["CASE_VISIBILITIES", "SCORE_DIMENSIONS", "TRACKS", "BenchmarkCase",
           "BenchmarkSeason", "CaseScore", "RunRecord", "ScoreComponents",
           "aggregate", "split_cases"]

#: The six tracks, 20 cases each in v1. Names are stable: they are column
#: headers on a public leaderboard and identifiers in a paper table.
TRACKS: Mapping[str, str] = {
    "TCM-Entity": "entity accuracy, ambiguity retention, false-merge rate",
    "TCM-Evidence": "Recall@K, nDCG, citation validity, study-design classification",
    "TCM-NetPharm": "edge provenance rate, measured/predicted separation, "
                    "enrichment reproducibility",
    "TCM-Safety": "risk recall, severe false-negative rate, evidence sufficiency",
    "TCM-TrialAudit": "STRICTA / SPIRIT-TCM / CONSORT-CHM checklist coverage",
    "TCM-End2End": "artifact completeness, re-run success, claim calibration",
}

#: Where a case may be used. `hidden` cases are delivered out of band and their
#: digest published, so a score can be tied to a revision without disclosure.
CASE_VISIBILITIES = ("dev", "hidden", "adversarial")

#: The eight score dimensions. Every leaderboard row shows all of them; an
#: aggregate alone is a design failure, and `arena-release.yml` fails the build
#: rather than render one.
SCORE_DIMENSIONS = (
    "task_success",
    "evidence_grounding",
    "provenance_completeness",
    "reproducibility",
    "safety_abstention",
    "claim_calibration",
    "latency",
    "cost",
)

#: Dimensions where a *higher* number is worse. Kept explicit so a formatter
#: cannot invert a column, and so the aggregate knows which way each points.
LOWER_IS_BETTER = frozenset({"latency", "cost"})


class CaseError(ValueError):
    """A benchmark case that cannot be used as described."""


class SplitViolation(CaseError):
    """A case was found where its visibility says it must not be."""


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One evaluation instance.

    `question` is what the system sees. `gold` is what a scorer compares against
    and is **not** sent to the system — the split between them is the whole
    reason a hidden set can exist, so they are separate fields rather than one
    document with a flag.
    """

    id: str
    track: str
    visibility: str
    question: str
    #: What a scorer compares against. Shape is per-track: a list of entity ids,
    #: a set of required citations, a severity label. Never sent to the system.
    gold: Mapping[str, Any] = field(default_factory=dict)
    #: Where the case's material came from, so a reader can judge its provenance.
    sources: tuple[str, ...] = ()
    licence: str = ""
    #: Free-text notes for an annotator or a reviewer.
    notes: str = ""
    #: The adjudicated disagreements, if any. Present so a case that two
    #: annotators read differently carries that history rather than hiding it.
    adjudication: str = ""
    #: Ids of other cases this one must not be confused with — near-duplicates
    #: that would leak between splits.
    siblings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise CaseError("a BenchmarkCase needs an id")
        if self.track not in TRACKS:
            raise CaseError(f"case {self.id!r} has track {self.track!r}, not one of "
                            f"{sorted(TRACKS)}")
        if self.visibility not in CASE_VISIBILITIES:
            raise CaseError(f"case {self.id!r} has visibility {self.visibility!r}, "
                            f"not one of {CASE_VISIBILITIES}")
        if not self.question.strip():
            raise CaseError(f"case {self.id!r} has no question")

    @property
    def is_held_out(self) -> bool:
        """Whether this case must not appear in any published artifact."""
        return self.visibility in ("hidden", "adversarial")

    @property
    def digest(self) -> str:
        return canonical_hash({"id": self.id, "track": self.track,
                               "question": self.question, "gold": self.gold})

    def as_public_dict(self) -> dict[str, Any]:
        """The case with its gold answer removed. What may be logged or shown."""
        return {"id": self.id, "track": self.track, "visibility": self.visibility,
                "question": self.question, "sources": list(self.sources),
                "licence": self.licence, "notes": self.notes}

    def as_dict(self) -> dict[str, Any]:
        return {**self.as_public_dict(), "gold": dict(self.gold),
                "adjudication": self.adjudication, "siblings": list(self.siblings)}


@dataclass(frozen=True, slots=True)
class BenchmarkSeason:
    """A frozen set of cases. Immutable once cut (ADR-0001)."""

    season: str
    cut_at: str
    cases: tuple[BenchmarkCase, ...] = ()
    #: Per-split digests, published so a score can be tied to a revision of the
    #: cases without the cases being disclosed.
    split_digests: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def dev(self) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.visibility == "dev")

    @property
    def hidden(self) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.visibility == "hidden")

    @property
    def adversarial(self) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.visibility == "adversarial")

    def by_track(self, track: str) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.track == track)

    def compute_split_digests(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for split in CASE_VISIBILITIES:
            members = sorted((c for c in self.cases if c.visibility == split),
                             key=lambda c: c.id)
            out[split] = canonical_hash([c.as_dict() for c in members])
        return out

    def data_card(self) -> dict[str, Any]:
        """What ships publicly: the shape and the provenance, never the gold."""
        digests = self.compute_split_digests()
        return {
            "season": self.season, "cut_at": self.cut_at,
            "tracks": {t: len(self.by_track(t)) for t in TRACKS},
            "splits": {s: len([c for c in self.cases if c.visibility == s])
                       for s in CASE_VISIBILITIES},
            "split_digests": digests,
            "sources": sorted({s for c in self.cases for s in c.sources}),
            "licences": sorted({c.licence for c in self.cases if c.licence}),
            "notes": self.notes,
            "warning": ("The cases and their gold labels are not published. Only "
                        "these digests are, so a reported score can be tied to a "
                        "revision of the case set without the set being disclosed."),
        }


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    """The eight dimensions for one run. Never collapsed on its own."""

    task_success: float = 0.0
    evidence_grounding: float = 0.0
    provenance_completeness: float = 0.0
    reproducibility: float = 0.0
    safety_abstention: float = 0.0
    claim_calibration: float = 0.0
    latency: float = 0.0
    cost: float = 0.0
    #: Hard gates this run failed, if any. A non-empty tuple means the run
    #: cannot enter the trusted board whatever its aggregate.
    gates_failed: tuple[str, ...] = ()
    #: Per-dimension notes, so a reader sees why a number is what it is.
    notes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in SCORE_DIMENSIONS:
            value = getattr(self, name)
            if value < 0:
                raise CaseError(f"{name} cannot be negative")

    @property
    def trusted(self) -> bool:
        return not self.gates_failed

    def as_dict(self) -> dict[str, Any]:
        return {**{name: getattr(self, name) for name in SCORE_DIMENSIONS},
                "gates_failed": list(self.gates_failed),
                "trusted": self.trusted, "notes": dict(self.notes)}


@dataclass(frozen=True, slots=True)
class CaseScore:
    """One case's outcome for one run."""

    case_id: str
    track: str
    components: ScoreComponents
    #: Every claim the run refused, with its code. Refusals are output, not
    #: noise: a system that declines to overclaim is behaving correctly and the
    #: record should show it.
    refusals: tuple[tuple[str, str], ...] = ()
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "track": self.track,
                "components": self.components.as_dict(),
                "refusals": [{"code": c, "detail": d} for c, d in self.refusals],
                "error": self.error, "failed": self.failed}


@dataclass(frozen=True, slots=True)
class RunRecord:
    """What a system did on one Season, with the trace needed to audit it."""

    run_id: str
    system: str
    submission_type: str          # skill | container | remote_api
    season: str
    composite_version: Mapping[str, str] = field(default_factory=dict)
    scores: tuple[CaseScore, ...] = ()
    started_at: str = ""
    finished_at: str = ""
    trace_digest: str = ""
    artifact_digest: str = ""
    notes: str = ""

    @property
    def failed_cases(self) -> tuple[CaseScore, ...]:
        return tuple(s for s in self.scores if s.failed)

    def by_track(self) -> Mapping[str, tuple[CaseScore, ...]]:
        out: dict[str, list[CaseScore]] = {}
        for score in self.scores:
            out.setdefault(score.track, []).append(score)
        return {k: tuple(v) for k, v in out.items()}

    def as_dict(self, *, public: bool = True) -> dict[str, Any]:
        return {"run_id": self.run_id, "system": self.system,
                "submission_type": self.submission_type, "season": self.season,
                "composite_version": dict(self.composite_version),
                "per_track": {track: [s.as_dict() for s in scores]
                              for track, scores in self.by_track().items()},
                "started_at": self.started_at, "finished_at": self.finished_at,
                "trace_digest": self.trace_digest,
                "artifact_digest": self.artifact_digest,
                "failed_cases": len(self.failed_cases), "notes": self.notes}


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def _harmonic(values: Sequence[float]) -> float:
    """A geometric-style mean that tolerates a zero without collapsing the row.

    A true geometric mean with any zero term is zero, which would make a single
    failed dimension indistinguishable from a system that scored nothing
    anywhere. The harmonic mean is still dominated by the weakest dimension —
    which is the property that matters, since a system must be good at
    provenance *and* safety, not good at one and absent at the other — but it
    degrades gracefully and a zero reads as "very weak here" rather than
    erasing everything else.
    """
    usable = [v for v in values if v > 0]
    if not usable:
        return 0.0
    return len(usable) / sum(1.0 / v for v in usable)


def aggregate(scores: Sequence[CaseScore], *,
              weights: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Aggregate a run's per-case scores into a leaderboard row.

    Returns **every** dimension plus the aggregate, never the aggregate alone —
    that requirement is enforced at the renderer, but building the row this way
    means a renderer cannot lose the decomposition by accident.

    `latency` and `cost` are inverted before aggregation, so a smaller number is
    a better score; the raw values are kept alongside so the row can display what
    it measured rather than only its rank.
    """
    if not scores:
        # The same keys as the populated path. A caller indexing `n_failed` would
        # otherwise work on every run except an empty one — the failure mode that
        # only appears in production, on the case nobody tested.
        return {"dimensions": {}, "aggregate": 0.0, "raw": {},
                "gates_failed": [], "trusted": True, "n_cases": 0, "n_failed": 0,
                "note": "no scores to aggregate"}

    weights = dict(weights or {name: 1.0 for name in SCORE_DIMENSIONS})
    raw: dict[str, float] = {}
    normalised: dict[str, float] = {}

    for name in SCORE_DIMENSIONS:
        values = [getattr(s.components, name) for s in scores]
        raw[name] = sum(values) / len(values)
        if name in LOWER_IS_BETTER:
            # Display a bounded "better is higher" score without pretending the
            # measurement is bounded; the raw value is reported beside it.
            normalised[name] = 1.0 / (1.0 + raw[name])
        else:
            normalised[name] = raw[name]

    gates = sorted({g for s in scores for g in s.components.gates_failed})
    weighted = [(normalised[name], weights.get(name, 1.0))
                for name in SCORE_DIMENSIONS if weights.get(name, 1.0) > 0]
    expanded = [n for value, weight in weighted for n in [value] * int(weight * 100)]
    value = _harmonic(expanded) if expanded else 0.0

    return {"dimensions": {k: round(v, 4) for k, v in normalised.items()},
            "aggregate": round(value, 4),
            "raw": {k: round(v, 4) for k, v in raw.items()},
            "gates_failed": gates,
            "trusted": not gates,
            "n_cases": len(scores),
            "n_failed": len([s for s in scores if s.failed]),
            "note": ("aggregate is a weighted harmonic mean of the eight dimensions; "
                     "it is never reported without them")}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def split_cases(cases: Sequence[BenchmarkCase], *, where: str = ""
                ) -> Mapping[str, tuple[BenchmarkCase, ...]]:
    """Group cases by visibility, refusing one that is out of place.

    ``where`` is the directory the cases came from — `dev`, `hidden`,
    `adversarial`. A case whose declared visibility disagrees with its location
    is refused rather than reclassified: the location is what a `.gitignore` acts
    on, so a mismatch is exactly how a held-out case would end up committed.
    """
    out: dict[str, list[BenchmarkCase]] = {v: [] for v in CASE_VISIBILITIES}
    for case in cases:
        if where and case.visibility != where:
            raise SplitViolation(
                f"case {case.id!r} declares visibility {case.visibility!r} but was "
                f"loaded from {where!r}; the directory is what the ignore rules act "
                "on, so the mismatch would decide whether it is published")
        out[case.visibility].append(case)
    return {k: tuple(v) for k, v in out.items()}
