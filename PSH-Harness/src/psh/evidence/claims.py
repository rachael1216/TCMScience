"""Structured scientific claims and the licensed scope of a source.

This module exists because of a measured failure, not a design preference. In v0.2 a claim
was a free-text string, and verification compared it to a source by shared terminology,
numeric agreement, polarity and hedge strength. Against a trial enrolling *middle-aged
European adults*, the verifier reported SUPPORT for:

    "Drug A improves survival in elderly Asian women."          confidence 0.65
    "Drug A improves survival in children under 5."             confidence 0.75
    "Drug A improves survival in pregnant patients."            confidence 0.75
    "Drug A improves survival in dialysis-dependent patients."  confidence 0.75
    "Drug A improves survival in middle-aged European adults."  confidence 0.95  <- licensed

Every unlicensed population landed in the same band as the licensed one. And given two real
papers — "P1NP predicts fracture" and "CTX predicts mortality" — the fused claim "P1NP
predicts all-cause mortality" was reported as supported by the CTX paper.

The diagnosis matters for the fix. These are not near-misses that a better matcher would
catch: the verifier has **no representation of a population and no representation of a
subject**, so those questions were never asked. A stronger matcher over an unstructured
claim inherits the same blindness — which is why an entailment model is the wrong *first*
move, however useful it becomes later.

So a claim gains structure: subject, predicate, object, population, outcome, direction and
magnitude. Verification then reads fields rather than counting shared words, and a mismatched
population or subject is a *finding* rather than an absence of signal.

Honest limits of the extraction here
------------------------------------
Extraction is pattern-based and local — no model call, so it works offline and inside the
kernel's own trust boundary. It will fail on complex syntax. That failure mode is deliberate:
an unextractable field becomes UNKNOWN, and an UNKNOWN subject or outcome cannot be used to
*establish* support, only to withhold it. Extraction accuracy is therefore measured
separately from verification accuracy, so a wrong verdict is attributable to the right layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import new_id

__all__ = [
    "PopulationSpec", "EffectDirection", "ScientificClaim", "LicensedScope",
    "Downgrade", "DowngradeReason", "extract_population", "extract_outcome",
]


# --------------------------------------------------------------------- populations

#: Population descriptor patterns, grouped by the axis they constrain. Grouping matters:
#: two descriptors on the *same* axis can conflict (elderly vs children), while descriptors
#: on different axes simply narrow (elderly + Asian). A flat keyword list cannot express that
#: distinction, and it is the distinction the population check depends on.
_POPULATION_AXES: dict[str, tuple[tuple[str, str], ...]] = {
    "age": (
        (r"\b(?:elderly|older adults?|geriatric|aged\s*(?:>|over|≥)\s*\d+"
         r"|age[ds]?\s*(?:>|over|≥)\s*(?:6[5-9]|[7-9]\d))\b", "elderly"),
        (r"\b(?:middle-aged|mean age\s*(?:of\s*)?[45]\d)\b", "middle-aged"),
        (r"\b(?:children|paediatric|pediatric|infants?|neonat\w+|under\s*5"
         r"|age[ds]?\s*(?:<|under)\s*1[0-8])\b", "paediatric"),
        (r"\b(?:young adults?|adolescents?)\b", "young-adult"),
    ),
    "sex": (
        (r"\b(?:women|females?|postmenopausal)\b", "female"),
        (r"\b(?:men|males?)\b", "male"),
    ),
    "ancestry": (
        (r"\b(?:asian|east asian|south asian|chinese|japanese|korean)\b", "asian"),
        (r"\b(?:european|caucasian|white)\b", "european"),
        (r"\b(?:african|black|african[- ]american)\b", "african"),
        (r"\b(?:hispanic|latino|latina)\b", "hispanic"),
    ),
    "physiological_state": (
        (r"\b(?:pregnan\w+|gestation\w*|obstetric)\b", "pregnant"),
        (r"\b(?:lactating|breastfeeding)\b", "lactating"),
    ),
    "organ_function": (
        (r"\b(?:dialysis|end-stage renal|ESRD|renal failure|CKD stage [45])\b",
         "renal-failure"),
        (r"\b(?:hepatic (?:impairment|failure)|cirrho\w+|Child-Pugh)\b", "hepatic-impairment"),
    ),
    "condition": (
        (r"\bHFpEF\b|\bpreserved ejection fraction\b", "hfpef"),
        (r"\bHFrEF\b|\breduced ejection fraction\b", "hfref"),
        (r"\b(?:heart failure)\b", "heart-failure"),
        (r"\b(?:type 2 diabetes|T2D|T2DM)\b", "type-2-diabetes"),
        (r"\b(?:osteoporo\w+)\b", "osteoporosis"),
        (r"\b(?:pancreatic cancer)\b", "pancreatic-cancer"),
        (r"\b(?:chronic kidney disease)\b", "ckd"),
    ),
}

#: Axes whose descriptors are mutually exclusive: a study in one cannot license a claim in
#: another. A trial in middle-aged adults says nothing about neonates.
_EXCLUSIVE_AXES = frozenset({"age", "ancestry", "physiological_state", "organ_function",
                             "sex"})

#: Axes where a source's SILENCE means exclusion rather than inclusion.
#:
#: This encodes a fact about how trials are actually run. Pregnancy, severe organ impairment
#: and paediatric age are conventionally excluded from adult trials unless the protocol
#: explicitly enrols them — so an abstract that never mentions pregnancy has almost certainly
#: not studied pregnant patients. Reading that silence as inclusion is exactly how "Drug A
#: improves survival in pregnant patients" came to be reported as supported by a trial in
#: middle-aged European adults: there was no *stated* conflict, so no conflict was found.
#:
#: The asymmetry is deliberate and conservative in the safe direction. Silence on ancestry or
#: sex does NOT imply exclusion — a trial not reporting ancestry probably enrolled a mixed
#: population — so those axes stay permissive.
_SILENCE_IMPLIES_EXCLUSION: dict[str, frozenset[str]] = {
    "physiological_state": frozenset({"pregnant", "lactating"}),
    "organ_function": frozenset({"renal-failure", "hepatic-impairment"}),
    "age": frozenset({"paediatric"}),
}


@dataclass(frozen=True, slots=True)
class PopulationSpec:
    """A population as a set of axis-tagged descriptors.

    ``is_unstated`` is a first-class state rather than an empty set treated as "everyone".
    The distinction is load-bearing: an unstated claim population should *inherit* the
    source's population (prose usually leaves it implicit, and refusing every implicit
    sentence would make the check unusable), whereas an explicitly *different* population is
    a mismatch. Conflating the two either refuses everything or catches nothing.
    """

    descriptors: tuple[str, ...] = ()
    axes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    raw: str = ""

    @property
    def is_unstated(self) -> bool:
        return not self.descriptors

    def conflicts_with(self, other: "PopulationSpec") -> tuple[str, ...]:
        """Return the axes on which this population is incompatible with ``other``.

        ``self`` is the claim's population and ``other`` the studied one; the relation is
        directional because of the silence rule below.

        Two sources of conflict:

        * **Stated conflict** — both populations name an exclusive axis and the values are
          disjoint (middle-aged vs paediatric).
        * **Implied exclusion** — the claim names a group that trials conventionally exclude
          (pregnancy, organ failure, children) and the source never mentions it. Treating
          that silence as inclusion is what let a claim about pregnant patients pass against
          a trial that never enrolled any.
        """
        out: list[str] = []
        # Two different opaque (unlisted) conditions conflict. Listed conditions keep their
        # narrowing semantics (heart-failure -> hfpef), so only the unlisted case is exclusive.
        mine_c = {v for v in self.axes.get("condition", ()) if v.startswith("unlisted:")}
        theirs_c = {v for v in other.axes.get("condition", ()) if v.startswith("unlisted:")}
        if mine_c and theirs_c and not (mine_c & theirs_c):
            out.append("condition")
        for axis in _EXCLUSIVE_AXES:
            mine = set(self.axes.get(axis, ()))
            theirs = set(other.axes.get(axis, ()))
            if mine and theirs and not (mine & theirs):
                out.append(axis)
                continue
            # Implied exclusion: the claim asserts a conventionally-excluded group and the
            # source is silent on that axis.
            special = _SILENCE_IMPLIES_EXCLUSION.get(axis, frozenset())
            if mine & special and not theirs:
                out.append(axis)
        return tuple(out)

    def narrows(self, other: "PopulationSpec") -> bool:
        """True when this population is contained by ``other`` (a subgroup claim)."""
        if self.is_unstated or other.is_unstated:
            return True
        if self.conflicts_with(other):
            return False
        for axis, values in other.axes.items():
            mine = set(self.axes.get(axis, ()))
            if values and mine and not mine <= set(values):
                return False
        return True

    def describe(self) -> str:
        return ", ".join(self.descriptors) if self.descriptors else "unstated"


#: "patients with X", "adults with X", "in X" — the condition phrase, captured when the
#: vocabulary has no entry for it. The condition axis is a fixed list, and my own audit showed
#: the consequence: a sickle-cell trial "supported" a cystic-fibrosis claim because neither
#: condition was in the list, so neither was seen, so nothing conflicted. Capturing the phrase
#: as an opaque token means two DIFFERENT unknown conditions still conflict. It cannot resolve
#: synonyms ("T2DM" vs "type 2 diabetes") without an ontology — that stays a stated limit.
_CONDITION_PHRASE = re.compile(
    r"\b(?:patients?|adults?|women|men|children|participants?|individuals?|subjects?|people)"
    r"\s+(?:with|who have|having|diagnosed with|suffering from)\s+"
    r"((?:[A-Za-z][A-Za-z0-9'-]*\s?){1,5}?)(?=[,.;:)]|\s+(?:and|or|who|that|which|were|was|"
    r"received|randomi|treated|undergoing|aged|\d)|$)", re.I)

_CONDITION_STOP = frozenset({"the", "a", "an", "and", "or", "of", "in", "at", "to", "for",
                             "disease", "diseases", "disorder", "condition", "syndrome"})


def _condition_token(phrase: str) -> str:
    words = [w.lower().strip("'-") for w in phrase.split()]
    words = [w for w in words if w and w not in _CONDITION_STOP]
    return "-".join(words)


def extract_population(text: str) -> PopulationSpec:
    """Extract axis-tagged population descriptors from a sentence or abstract."""
    axes: dict[str, list[str]] = {}
    for axis, patterns in _POPULATION_AXES.items():
        for pattern, label in patterns:
            if re.search(pattern, text, re.I):
                axes.setdefault(axis, [])
                if label not in axes[axis]:
                    axes[axis].append(label)
    # Opaque condition tokens for conditions the vocabulary does not know.
    if "condition" not in axes:
        for m in _CONDITION_PHRASE.finditer(text):
            token = _condition_token(m.group(1))
            if token and len(token) >= 3:
                axes.setdefault("condition", [])
                if f"unlisted:{token}" not in axes["condition"]:
                    axes["condition"].append(f"unlisted:{token}")
    descriptors = tuple(label for values in axes.values() for label in values)
    return PopulationSpec(descriptors=descriptors,
                          axes={a: tuple(v) for a, v in axes.items()}, raw=text[:400])


# ------------------------------------------------------------------------ outcomes

#: Outcome vocabulary, split by whether an outcome is patient-relevant or a surrogate.
#: GRADE treats surrogate outcomes as indirect evidence, and the distinction is why a bone
#: density trial cannot license a fracture claim.
_CLINICAL_OUTCOMES: tuple[tuple[str, str], ...] = (
    (r"\b(?:all-cause mortality|overall survival|survival|death|mortalit\w+)\b", "mortality"),
    (r"\b(?:fractures?|vertebral fracture|hip fracture)\b", "fracture"),
    (r"\b(?:hospitali[sz]ation|admission)\b", "hospitalisation"),
    (r"\b(?:myocardial infarction|stroke|MACE|cardiovascular death)\b",
     "cardiovascular-event"),
    (r"\b(?:progression-free survival|PFS)\b", "progression-free-survival"),
    (r"\b(?:remission|cure[ds]?|resolution)\b", "remission"),
    (r"\b(?:quality of life|QoL|functional status)\b", "quality-of-life"),
)

_SURROGATE_OUTCOMES: tuple[tuple[str, str], ...] = (
    (r"\b(?:bone mineral density|BMD)\b", "bone-density"),
    (r"\b(?:HbA1c|glycated haemoglobin|glycated hemoglobin)\b", "hba1c"),
    (r"\b(?:LDL|cholesterol|lipid)\b", "lipids"),
    (r"\b(?:blood pressure|systolic|diastolic)\b", "blood-pressure"),
    (r"\b(?:ejection fraction|eGFR|creatinine|biomarker levels?)\b", "surrogate-marker"),
    (r"\b(?:tumou?r (?:size|response)|response rate)\b", "tumour-response"),
)


def extract_outcome(text: str) -> tuple[str, bool]:
    """Return ``(outcome, is_surrogate)``; the outcome is empty when none is identifiable."""
    for pattern, label in _CLINICAL_OUTCOMES:
        if re.search(pattern, text, re.I):
            return label, False
    for pattern, label in _SURROGATE_OUTCOMES:
        if re.search(pattern, text, re.I):
            return label, True
    return "", False


# -------------------------------------------------------------------------- claims

class EffectDirection(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    NO_EFFECT = "no_effect"
    ASSOCIATION = "association"
    UNKNOWN = "unknown"


_DIRECTION_PATTERNS: tuple[tuple[str, EffectDirection], ...] = (
    # A benefit claim. "improves survival" and "improved overall survival" both mean the
    # risk went down, so the outcome word need not be adjacent to the verb.
    (r"\b(?:reduc\w+|decreas\w+|lower\w*|prevent\w*|cure[ds]?|eliminat\w+"
     r"|improv\w+(?:\s+\w+){0,2}\s+(?:survival|mortalit\w+|outcomes?))\b",
     EffectDirection.DECREASE),
    (r"\b(?:increas\w+|rais\w+|higher|elevat\w+|worsen\w+)\b", EffectDirection.INCREASE),
    (r"\b(?:no (?:significant )?(?:effect|difference|association|benefit)"
     r"|did not (?:reduce|improve|affect))\b", EffectDirection.NO_EFFECT),
    (r"\b(?:predict\w*|associat\w+|correlat\w+|linked to)\b", EffectDirection.ASSOCIATION),
)

#: The claim's subject: the intervention or exposure it is about. Captured before the
#: predicate, since "P1NP predicts mortality" and "CTX predicts mortality" differ only here —
#: and that difference is what v0.2 could not see.
#: Words that qualify an analyte rather than naming it. "Serum CTX" is a claim about CTX;
#: capturing "Serum" would make every biomarker claim look like the same subject, which is
#: precisely the subject-blindness this module exists to fix.
_ASSAY_PREFIX = ("serum", "plasma", "urinary", "urine", "blood", "circulating", "salivary",
                 "csf", "the", "this", "these", "a", "an", "in", "among", "there", "it",
                 "results", "mean", "median", "baseline", "total",
                 # Grammatical connectives and study-design words that sit immediately
                 # before a claim verb without being its subject. Without these the
                 # case-insensitive pattern above captures "patients" or "which".
                 "that", "which", "who", "whom", "and", "but", "also", "significantly",
                 "patients", "participants", "subjects", "adults", "women", "men",
                 "treatment", "therapy", "placebo", "group", "groups", "arm", "trial",
                 "study", "cohort", "analysis", "supplementation", "levels", "level",
                 "concentration", "concentrations", "risk", "rate", "incidence",
                 # Negation and auxiliaries. "was not associated with" would otherwise
                 # capture "not" as the entity under study.
                 "not", "no", "none", "neither", "nor", "was", "were", "is", "are", "been",
                 "being", "had", "has", "have", "did", "does", "do", "significantly",
                 # Prepositions and determiners. "was not associated with reduced risk"
                 # would otherwise yield "with" as the entity under study.
                 "with", "without", "for", "from", "by", "to", "at", "on", "of", "as",
                 "than", "after", "before", "during", "versus", "vs", "compared",
                 # Causal and reporting verbs. "X causes reduced Y" would otherwise capture
                 # "causes" as the entity.
                 "causes", "caused", "cause", "leads", "led", "results", "resulted",
                 "showed", "shows", "demonstrated", "found", "reported", "observed",
                 # Outcome nouns that can head a passive clause ("Hospitalization was
                 # reduced by X") -- the true subject follows "by", handled below.
                 "hospitalization", "hospitalisation", "mortality", "survival", "fracture",
                 "death", "deaths", "risk", "outcome", "outcomes", "events",
                 # Modals and hedges. "Empagliflozin may reduce hospitalization" parsed
                 # with the subject "may": the word sits between the noun phrase and its
                 # verb, and the mid-sentence pattern took it for the entity. A correctly
                 # cited, signed, hedged claim was then refused for a subject mismatch
                 # against its own source.
                 "may", "might", "could", "can", "should", "would", "will", "shall", "must",
                 "appears", "appeared", "seems", "seemed", "tends", "tended", "likely",
                 "probably", "possibly", "potentially", "apparently")

#: A modal or hedge that may sit between a claim's subject and its verb.
_MODAL = (r"(?:(?:may|might|could|can|should|would|will|shall|must|appears?\s+to|seems?\s+to"
          r"|tends?\s+to|is\s+likely\s+to|are\s+likely\s+to)\s+)?")

_SUBJECT_PATTERNS: tuple[str, ...] = (
    # Passive voice: "Hospitalization ... was reduced BY empagliflozin". The agent follows
    # "by", and without this the outcome noun heading the clause is taken as the subject —
    # a false subject mismatch on a correctly-stated claim.
    r"\b(?:reduc|increas|improv|prevent|lower|rais|worsen)\w*\s+(?:\w+\s+){0,3}?"
    r"by\s+([A-Za-z][A-Za-z0-9-]{2,24})\b",
    # Assay-prefixed analyte first: "Serum P1NP concentration predicted ..." -> P1NP.
    r"\b(?:serum|plasma|urinary|urine|blood|circulating)\s+([A-Za-z][A-Za-z0-9-]{1,20})\b",
    # Subject immediately before a claim verb. The multiword group is greedy within a bound
    # so "Drug A improves" yields "Drug A" rather than "Drug".
    r"^\s*(?:the\s+|among\s+|in\s+)?([A-Z][A-Za-z0-9-]*(?:\s+[A-Z0-9][A-Za-z0-9-]*){0,2})"
    r"\s+(?:concentrations?\s+|levels?\s+)?"
    # An adverb may sit between the noun phrase and its verb ("P1NP independently
    # predicted"), so allow one; so may a modal ("Empagliflozin may reduce").
    + _MODAL + r"(?:\w+ly\s+)?"
    r"(?:reduc|increas|improv|predict|associat|correlat|prevent|cure|eliminat|lower|rais"
    r"|worsen|had|was|were|is|does|did|showed|yielded)",
    # Anywhere in the sentence, for abstracts whose subject is not sentence-initial.
    # Case-insensitive on the first letter: a drug name mid-sentence is normally lowercase
    # ("..., empagliflozin reduced hospitalization"), and requiring a capital missed the
    # subject in most real abstracts — which silently disabled the subject check, because an
    # unextractable subject is reported as "not checked" rather than as a mismatch.
    r"\b([A-Za-z][A-Za-z0-9-]{2,24})\s+(?:concentrations?\s+|levels?\s+)?" + _MODAL
    + r"(?:\w+ly\s+)?"
    r"(?:reduc|increas|improv|predict|associat|correlat|prevent|lower|rais|worsen)\w*",
    r"^\s*([A-Z][A-Za-z0-9-]{1,24})\b",
)

#: Normative language: a recommendation or a standard-of-care assertion rather than an
#: empirical finding. A trial establishes what an intervention *did*, never what a clinician
#: *should do* — that inference needs guidelines, comparative effectiveness and cost, none of
#: which a single efficacy abstract contains. Held-out testing found both forms accepted:
#: "empagliflozin is first-line therapy" scored 0.75, and a recommendation appended to a
#: genuinely supported finding scored 0.85 by inheriting the finding's overlap.
_NORMATIVE = re.compile(
    r"\b(?:should (?:be|receive|not)|must (?:be|receive)|recommend\w*|indicated for"
    r"|first-line|second-line|standard of care|treatment of choice|preferred (?:agent|"
    r"therapy|treatment)|ought to|is warranted|guidelines?)\b", re.I)

#: A verb followed by a noun phrase: the shape of "X reduces Y" when Y is unknown to us.
_OBJECT_PHRASE = re.compile(
    r"\b(?:reduc|increas|improv|predict|prevent|lower|rais|worsen|cure|eliminat)\w*\s+"
    r"(?:the\s+)?(?:risk\s+of\s+|rate\s+of\s+|incidence\s+of\s+)?[a-z][a-z-]+(?:\s+[a-z-]+){0,3}", re.I)

_NON_CLAIM = re.compile(
    r"\b(?:we (?:plan|propose|will|intend|aim)|further (?:study|research|work) is"
    r"|future (?:studies|work)|the results were|this (?:paper|study) (?:describes|reports)"
    r"|figure \d|table \d)\b", re.I)

_MAGNITUDE = re.compile(
    r"\b(?:hazard ratio|HR|odds ratio|OR|risk ratio|RR|relative risk)\s*"
    r"(?:of\s*|[=:,]\s*)?(\d+\.?\d*)|(\d+\.?\d*)\s*%|by\s+(\d+\.?\d*)\s*(?:%|percent)", re.I)


@dataclass(frozen=True, slots=True)
class ScientificClaim:
    """A claim as structure, with the original sentence retained for display."""

    text: str
    subject: str = ""
    predicate: str = ""
    object: str = ""
    population: PopulationSpec = field(default_factory=PopulationSpec)
    outcome: str = ""
    outcome_is_surrogate: bool = False
    direction: EffectDirection = EffectDirection.UNKNOWN
    magnitude: float | None = None
    hedged: bool = False
    #: The claim asserts what should be done rather than what was observed. Evidence can
    #: never license this on its own, so it is tracked separately from the empirical fields.
    normative: bool = False
    claim_id: str = field(default_factory=lambda: new_id("clm"))

    @property
    def is_clinical_claim(self) -> bool:
        """Whether this assertion needs evidence before it may be released.

        Normally requires an identifiable subject *and* an outcome: a sentence with neither
        is prose, and one with only a subject is too underspecified to verify.

        A **normative** sentence is included even without an extractable outcome. "X is
        first-line therapy" names no measured outcome, so an outcome-based test excluded it
        from checking entirely and the lexical verdict stood — it scored 0.75 SUPPORT purely
        on sharing two terms with the abstract. A recommendation is the assertion most in
        need of evidence, so excluding it for lacking an outcome inverted the priority.
        """
        if _NON_CLAIM.search(self.text):
            return False
        if self.normative and self.subject:
            return True
        if self.subject and self.outcome:
            return True
        # A subject with a directional verb and an object phrase is a claim even when the
        # outcome is not in the vocabulary. "Drug Z reduces vaso-occlusive crises" was
        # exempted from every check because "vaso-occlusive crises" had no outcome label —
        # so the population conflict that check_scope correctly detected was never consulted.
        # An unrecognised outcome must widen the checks that run, not exempt the sentence.
        return bool(self.subject and self.direction is not EffectDirection.UNKNOWN
                    and self.direction is not EffectDirection.ASSOCIATION or
                    (self.subject and self.predicate and _OBJECT_PHRASE.search(self.text)))

    @property
    def subject_key(self) -> str:
        """Normalised subject, for comparison across a claim and a source."""
        return re.sub(r"[^a-z0-9]", "", self.subject.lower())

    @classmethod
    def parse(cls, text: str) -> "ScientificClaim":
        """Extract structure from a sentence. Unextractable fields stay empty/UNKNOWN."""
        stripped = re.sub(r"\((?:PMID|NCT|DOI)[^)]*\)", "", text, flags=re.I).strip()

        subject = ""
        for pattern in _SUBJECT_PATTERNS:
            for match in re.finditer(pattern, stripped):
                candidate = match.group(1).strip()
                # A qualifier is not an entity. Keep looking rather than accepting it, or
                # every serum biomarker becomes the same subject.
                if candidate.lower() in _ASSAY_PREFIX:
                    continue
                words = candidate.split()
                # Strip a LEADING assay qualifier: "Serum CTX" is a claim about CTX. Merely
                # rejecting a bare "Serum" left the two-word form intact, so every serum
                # biomarker still compared equal.
                while len(words) > 1 and words[0].lower() in _ASSAY_PREFIX:
                    words.pop(0)
                # Strip a trailing qualifier the greedy group may have absorbed.
                while words and words[-1].lower() in ("concentration", "concentrations",
                                                      "level", "levels", "group", "arm"):
                    words.pop()
                if words:
                    subject = " ".join(words)
                    break
            if subject:
                break

        # Earliest match in the sentence wins, not earliest pattern in the table. Ordering
        # by pattern made direction depend on the arbitrary order of the alternation: a
        # sentence containing both "improved" and "associated" resolved by table position
        # rather than by which verb governs the claim.
        direction = EffectDirection.UNKNOWN
        predicate = ""
        best: tuple[int, EffectDirection, str] | None = None
        for pattern, value in _DIRECTION_PATTERNS:
            match = re.search(pattern, stripped, re.I)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), value, match.group(0).lower())
        if best is not None:
            _, direction, predicate = best

        outcome, surrogate = extract_outcome(stripped)
        magnitude = None
        mm = _MAGNITUDE.search(stripped)
        if mm:
            for group in mm.groups():
                if group:
                    try:
                        magnitude = float(group)
                    except ValueError:
                        pass
                    break

        return cls(
            text=text.strip(), subject=subject, predicate=predicate, object=outcome,
            population=extract_population(stripped), outcome=outcome,
            outcome_is_surrogate=surrogate, direction=direction, magnitude=magnitude,
            hedged=bool(re.search(r"\b(?:may|might|could|suggests?|appears?|possibly)\b",
                                  stripped, re.I)),
            normative=bool(_NORMATIVE.search(stripped)))

    def describe(self) -> str:
        return (f"{self.subject or '?'} [{self.direction.value}] "
                f"{self.outcome or '?'} in {self.population.describe()}")


# ------------------------------------------------------------------ licensed scope

class DowngradeReason(str, Enum):
    """GRADE-style reasons a claim may not be asserted at full strength."""

    POPULATION_EXTRAPOLATION = "population_extrapolation"
    SURROGATE_OUTCOME = "surrogate_outcome"
    SUBJECT_MISMATCH = "subject_mismatch"
    OUTCOME_MISMATCH = "outcome_mismatch"
    SINGLE_STUDY = "single_study"
    OBSERVATIONAL_ONLY = "observational_only"
    UNVERIFIED_PROVENANCE = "unverified_provenance"
    IMPRECISION = "imprecision"


@dataclass(frozen=True, slots=True)
class Downgrade:
    """One applied downgrade, with the reason a reader needs to see."""

    kind: DowngradeReason
    reason: str
    severity: int = 1

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.reason}"


#: Study designs, ordered by how much a single study licenses.
_DESIGN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:meta-analys\w+|systematic review)\b", "meta-analysis"),
    (r"\b(?:randomi[sz]ed|randomly assigned|double-blind|placebo-controlled|RCT)\b",
     "randomised-trial"),
    (r"\b(?:prospective cohort|followed prospectively)\b", "prospective-cohort"),
    (r"\b(?:cohort study|cohort of)\b", "cohort"),
    (r"\b(?:case-control)\b", "case-control"),
    (r"\b(?:cross-sectional)\b", "cross-sectional"),
    (r"\b(?:case (?:report|series))\b", "case-series"),
)


@dataclass(frozen=True, slots=True)
class LicensedScope:
    """What a source actually licenses: who was studied, what was measured, how.

    The name is deliberate. A source does not "contain facts" that a claim may borrow — it
    licenses assertions within a boundary, and a claim outside that boundary is unsupported
    even when every word of it appears in the text.
    """

    population: PopulationSpec
    outcomes: tuple[str, ...] = ()
    surrogate_outcomes: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    design: str = "unknown"
    effects: tuple[float, ...] = ()
    sample_size: int | None = None
    #: The direction the source itself reports, parsed from its prose rather than inferred
    #: from effect ratios. Inferring from ratios alone is fragile: an abstract may quote a
    #: percentage ("94% of European ancestry") before its hazard ratio, and whichever number
    #: parses first then decides the direction.
    direction: "EffectDirection" = None  # type: ignore[assignment]

    @property
    def is_observational(self) -> bool:
        return self.design in ("prospective-cohort", "cohort", "case-control",
                               "cross-sectional", "case-series")

    @property
    def subject_keys(self) -> frozenset[str]:
        return frozenset(re.sub(r"[^a-z0-9]", "", s.lower()) for s in self.subjects if s)

    @classmethod
    def from_text(cls, text: str) -> "LicensedScope":
        """Derive the scope from a source abstract."""
        design = "unknown"
        for pattern, label in _DESIGN_PATTERNS:
            if re.search(pattern, text, re.I):
                design = label
                break

        clinical = tuple(label for pattern, label in _CLINICAL_OUTCOMES
                         if re.search(pattern, text, re.I))
        surrogate = tuple(label for pattern, label in _SURROGATE_OUTCOMES
                          if re.search(pattern, text, re.I))

        # Subjects: every sentence's subject, since an abstract may report on several
        # analytes or interventions and any of them may be what a claim is about.
        subjects: list[str] = []
        # Split on clause boundaries as well as sentences. An abstract routinely opens with a
        # design-and-population clause ("In this randomized trial of ..., Drug A improved
        # ..."), so sentence-level parsing alone finds no subject in exactly the sentence
        # that carries the finding.
        for clause in re.split(r"(?<=[.;])\s+|(?<=\)),\s+|(?<=[a-z]),\s+(?=[A-Z])", text):
            parsed = ScientificClaim.parse(clause.strip())
            if parsed.subject and parsed.subject not in subjects:
                subjects.append(parsed.subject)

        sample = None
        nm = re.search(r"\bn\s*=\s*(\d[\d,]*)", text, re.I)
        if nm:
            try:
                sample = int(nm.group(1).replace(",", ""))
            except ValueError:
                pass

        effects: list[float] = []
        for match in _MAGNITUDE.finditer(text):
            for group in match.groups():
                if group:
                    try:
                        effects.append(float(group))
                    except ValueError:
                        pass
                    break

        # The source's own direction, taken from the clause that carries its finding.
        direction = EffectDirection.UNKNOWN
        for clause in re.split(r"(?<=[.;])\s+|(?<=\)),\s+|(?<=[a-z]),\s+(?=[A-Z])", text):
            parsed = ScientificClaim.parse(clause.strip())
            if parsed.direction is not EffectDirection.UNKNOWN:
                direction = parsed.direction
                break

        return cls(population=extract_population(text), outcomes=clinical,
                   surrogate_outcomes=surrogate, subjects=tuple(subjects), design=design,
                   effects=tuple(effects), sample_size=sample, direction=direction)

    def describe(self) -> str:
        outcomes = ", ".join(self.outcomes + self.surrogate_outcomes) or "unstated outcome"
        return (f"{self.design} in {self.population.describe()}; "
                f"measured {outcomes}")
