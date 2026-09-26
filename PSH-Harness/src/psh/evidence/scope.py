"""Scope checking: does this source license this claim?

The v0.2 verifier answered "do these two texts share terminology, numbers, polarity and
hedge strength?" That is a real question, and a useful one — it catches fabrications whose
vocabulary does not match the source. But it is not the question a clinician needs answered,
which is closer to:

    Does what this study actually established license what this sentence actually asserts?

Three checks implement that, in the order their failures matter:

1. **Subject.** Is the claim about the entity the source reports on? A P1NP claim is not
   supported by a CTX paper, however well the outcome language matches.
2. **Population.** Was the claim's population studied? A trial in middle-aged Europeans does
   not license a claim about children, and the *direction* of that error is the dangerous
   one — extrapolation reads as confirmation.
3. **Outcome.** Is the claim's outcome the one measured? Bone density is not fracture.

Each failure is a *finding* rather than an absence of signal, which is the substantive change
from v0.2. There, a mismatch simply produced weaker lexical overlap, so it was
indistinguishable from a vaguely-worded true claim — and often produced *more* overlap,
because an extrapolated claim reuses the source's exact vocabulary.

What this does not do
---------------------
It does not understand meaning. It compares extracted fields, so it inherits every
extraction limit, and a claim whose subject cannot be extracted falls back to the lexical
path with its support capped. Deliberate: an unextractable field must not silently license a
claim, and it must not silently refuse a legitimate one either — so it withholds strength
rather than deciding either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .claims import (
    Downgrade, DowngradeReason, EffectDirection, LicensedScope, PopulationSpec,
    ScientificClaim,
)

__all__ = ["ScopeVerdict", "check_scope"]


@dataclass(frozen=True, slots=True)
class ScopeVerdict:
    """The result of comparing a structured claim against a source's licensed scope."""

    subject_matches: bool
    population_covered: bool
    outcome_covered: bool
    direction_agrees: bool
    downgrades: tuple[Downgrade, ...] = ()
    notes: tuple[str, ...] = ()
    #: True when a field could not be extracted, so the corresponding check was not run.
    #: A caller must treat this as "unknown", never as "passed".
    incomplete: tuple[str, ...] = ()

    @property
    def licensed(self) -> bool:
        """Whether the source licenses the claim outright, with no downgrade applied."""
        return (self.subject_matches and self.population_covered and self.outcome_covered
                and self.direction_agrees and not self.downgrades)

    @property
    def blocked(self) -> bool:
        """Whether a mismatch is severe enough that support must be withheld.

        Includes ``direction_agrees``. An earlier version computed the direction verdict and
        then omitted it here, so an inverted claim recorded its downgrade and was released
        anyway — the check ran and its answer was discarded, which is worse than not checking,
        because the audit trail shows a verdict that had no effect.
        """
        return not (self.subject_matches and self.population_covered
                    and self.outcome_covered and self.direction_agrees)

    @property
    def severity(self) -> int:
        return sum(d.severity for d in self.downgrades)

    def describe(self) -> str:
        if self.licensed:
            return "the source licenses this claim directly"
        parts = [str(d) for d in self.downgrades]
        if self.incomplete:
            parts.append(f"not checked: {', '.join(self.incomplete)}")
        return "; ".join(parts) or "no scope objection"


#: Outcomes that are hierarchically related, so one may inform a claim about the other. The
#: relation is directional: a mortality finding informs a survival claim (same construct),
#: but a bone-density finding does not license a fracture claim.
_OUTCOME_EQUIVALENTS: dict[str, frozenset[str]] = {
    "mortality": frozenset({"mortality"}),
    "fracture": frozenset({"fracture"}),
    "hospitalisation": frozenset({"hospitalisation"}),
    "cardiovascular-event": frozenset({"cardiovascular-event", "mortality"}),
    "progression-free-survival": frozenset({"progression-free-survival"}),
    "remission": frozenset({"remission"}),
    "quality-of-life": frozenset({"quality-of-life"}),
}

#: Surrogate -> the clinical outcome it is commonly used to stand in for. Present so the
#: verifier can *name* the substitution rather than merely failing to match, which is what
#: makes the refusal explainable to a clinician.
_SURROGATE_FOR: dict[str, str] = {
    "bone-density": "fracture",
    "hba1c": "cardiovascular-event",
    "lipids": "cardiovascular-event",
    "blood-pressure": "cardiovascular-event",
    "surrogate-marker": "mortality",
    "tumour-response": "progression-free-survival",
}


#: Direction pairs that may coexist. ASSOCIATION is compatible with a directional finding
#: (an association *is* the weaker reading of one); INCREASE and DECREASE are not, and
#: NO_EFFECT is incompatible with either.
_COMPATIBLE_DIRECTIONS: dict[EffectDirection, frozenset[EffectDirection]] = {
    EffectDirection.DECREASE: frozenset({EffectDirection.DECREASE,
                                         EffectDirection.ASSOCIATION}),
    EffectDirection.INCREASE: frozenset({EffectDirection.INCREASE,
                                         EffectDirection.ASSOCIATION}),
    EffectDirection.NO_EFFECT: frozenset({EffectDirection.NO_EFFECT}),
    EffectDirection.ASSOCIATION: frozenset({EffectDirection.ASSOCIATION,
                                            EffectDirection.INCREASE,
                                            EffectDirection.DECREASE}),
}


def _directions_compatible(claim_direction: EffectDirection,
                           source_direction: EffectDirection) -> bool:
    return source_direction in _COMPATIBLE_DIRECTIONS.get(
        claim_direction, frozenset({source_direction}))


def _source_direction(scope: LicensedScope) -> EffectDirection:
    """The source's effect direction: its own prose first, effect ratios as a fallback.

    Prose is preferred because it is what the authors asserted. Ratios are a fallback for
    abstracts that report a number without a directional verb, and they are filtered to
    plausible ratio values — a percentage such as "94% of European ancestry" is not an effect
    size, and letting it decide the direction was a real failure mode.
    """
    declared = getattr(scope, "direction", None)
    if declared is not None and declared is not EffectDirection.UNKNOWN:
        return declared
    ratios = [e for e in scope.effects if 0.05 < e < 5.0]
    if not ratios:
        return EffectDirection.UNKNOWN
    ratio = ratios[0]
    if 0.95 <= ratio <= 1.05:
        return EffectDirection.NO_EFFECT
    return EffectDirection.DECREASE if ratio < 1.0 else EffectDirection.INCREASE


def _subjects_match(claim: ScientificClaim, scope: LicensedScope) -> bool:
    """Whether the claim's subject is one the source reports on.

    Substring matching in both directions, because a source may name an analyte more fully
    than a claim does ("P1NP" vs "serum P1NP concentration"). Both directions are needed and
    neither alone suffices.
    """
    claim_key = claim.subject_key
    if not claim_key:
        return True  # unextractable: not a match, but not a mismatch either
    for key in scope.subject_keys:
        if claim_key == key or claim_key in key or key in claim_key:
            return True
    return False


def check_scope(claim: ScientificClaim, scope: LicensedScope, *,
                trusted: bool = True) -> ScopeVerdict:
    """Compare a structured claim against what a source licenses."""
    downgrades: list[Downgrade] = []
    notes: list[str] = []
    incomplete: list[str] = []

    # ------------------------------------------------------------------ subject
    subject_matches = True
    if not claim.subject:
        incomplete.append("subject")
        downgrades.append(Downgrade(
            DowngradeReason.SUBJECT_MISMATCH,
            "the claim's subject could not be identified, so it could not be checked "
            "against the source", severity=1))
    elif not scope.subjects:
        incomplete.append("source subject")
        notes.append("the source's subject could not be identified")
    elif not _subjects_match(claim, scope):
        subject_matches = False
        downgrades.append(Downgrade(
            DowngradeReason.SUBJECT_MISMATCH,
            f"the claim is about {claim.subject!r} but the source reports on "
            f"{', '.join(sorted(scope.subjects))}", severity=3))

    # --------------------------------------------------------------- population
    population_covered = True
    if claim.population.is_unstated:
        # Inherit the source's population. Prose usually leaves it implicit, and treating
        # every implicit sentence as a universal claim would refuse almost all real writing.
        notes.append(f"claim population unstated; read as the studied population "
                     f"({scope.population.describe()})")
    elif scope.population.is_unstated:
        incomplete.append("studied population")
        downgrades.append(Downgrade(
            DowngradeReason.POPULATION_EXTRAPOLATION,
            "the source does not state which population was studied, so the claim's "
            "population could not be verified against it", severity=1))
    else:
        conflicts = claim.population.conflicts_with(scope.population)
        if conflicts:
            population_covered = False
            downgrades.append(Downgrade(
                DowngradeReason.POPULATION_EXTRAPOLATION,
                f"the claim concerns a population the study did not enrol: it differs on "
                f"{', '.join(conflicts)} — claim is {claim.population.describe()}, study "
                f"enrolled {scope.population.describe()}", severity=3))
        elif not claim.population.narrows(scope.population):
            population_covered = False
            downgrades.append(Downgrade(
                DowngradeReason.POPULATION_EXTRAPOLATION,
                f"the claim's population ({claim.population.describe()}) is not contained by "
                f"the studied population ({scope.population.describe()})", severity=2))

    # ------------------------------------------------------------------ outcome
    outcome_covered = True
    if not claim.outcome:
        incomplete.append("outcome")
        if getattr(claim, "normative", False):
            # A recommendation with no measured outcome is the least verifiable sentence
            # there is; "not checked" must not read as "passed".
            outcome_covered = False
    else:
        licensed = set(scope.outcomes)
        acceptable = set()
        for outcome in licensed:
            acceptable |= _OUTCOME_EQUIVALENTS.get(outcome, {outcome})
        if claim.outcome in acceptable:
            pass
        elif claim.outcome in scope.surrogate_outcomes:
            # The claim itself is about the surrogate, which the source did measure.
            notes.append(f"{claim.outcome} is a surrogate outcome; the source measured it "
                         "directly")
        elif scope.surrogate_outcomes and not licensed:
            # The source measured only a surrogate, and the claim asserts a clinical outcome.
            surrogate = scope.surrogate_outcomes[0]
            stands_for = _SURROGATE_FOR.get(surrogate, "a clinical outcome")
            outcome_covered = False
            downgrades.append(Downgrade(
                DowngradeReason.SURROGATE_OUTCOME,
                f"the source measured {surrogate}, a surrogate for {stands_for}; it does not "
                f"establish the claim's outcome ({claim.outcome})", severity=3))
        elif licensed:
            outcome_covered = False
            downgrades.append(Downgrade(
                DowngradeReason.OUTCOME_MISMATCH,
                f"the claim's outcome ({claim.outcome}) is not among those the source "
                f"measured ({', '.join(sorted(licensed))})", severity=3))
        else:
            incomplete.append("source outcome")

    # ---------------------------------------------------------------- direction
    #
    # Compared for every direction pair, not only for null claims. An earlier version checked
    # only NO_EFFECT, so "empagliflozin *increased* hospitalization" passed against a source
    # reporting a reduction: the two sentences share every term, and lexical polarity read
    # them as agreeing because both are non-negated. Inversion is the failure mode where
    # term overlap is highest and meaning is furthest apart.
    direction_agrees = True
    source_direction = _source_direction(scope)
    if (claim.direction is not EffectDirection.UNKNOWN
            and source_direction is not EffectDirection.UNKNOWN
            and not _directions_compatible(claim.direction, source_direction)):
        direction_agrees = False
        downgrades.append(Downgrade(
            DowngradeReason.IMPRECISION,
            f"the claim asserts a {claim.direction.value} but the source reports a "
            f"{source_direction.value}", severity=3))

    # ---------------------------------------------------------------- normative
    #
    # A recommendation is not a finding. No abstract licenses "should be offered to all such
    # patients", however well it supports the effect the recommendation rests on — and the
    # dangerous form is exactly that: a true finding with a recommendation appended, which
    # inherits the finding's lexical overlap and scores high.
    normative_claim = getattr(claim, "normative", False)
    if normative_claim:
        outcome_covered = False
        downgrades.append(Downgrade(
            DowngradeReason.OUTCOME_MISMATCH,
            "the claim asserts what should be done rather than what was observed; an "
            "efficacy source cannot license a recommendation or a standard-of-care "
            "statement", severity=3))

    # -------------------------------------------------------- design and provenance
    if scope.is_observational:
        downgrades.append(Downgrade(
            DowngradeReason.OBSERVATIONAL_ONLY,
            f"a single {scope.design} supports association, not causation", severity=1))
    elif scope.design == "randomised-trial":
        downgrades.append(Downgrade(
            DowngradeReason.SINGLE_STUDY,
            "a single randomised trial; replication raises certainty", severity=1))

    if not trusted:
        downgrades.append(Downgrade(
            DowngradeReason.UNVERIFIED_PROVENANCE,
            "the source text was supplied rather than retrieved through a trusted "
            "capability", severity=1))

    return ScopeVerdict(
        subject_matches=subject_matches, population_covered=population_covered,
        outcome_covered=outcome_covered, direction_agrees=direction_agrees,
        downgrades=tuple(downgrades), notes=tuple(notes), incomplete=tuple(incomplete))
