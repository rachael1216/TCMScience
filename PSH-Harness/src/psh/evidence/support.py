"""Claim, Evidence and ClaimSupport — does the source actually support the sentence?

This module closes the second demonstrated hole. The predecessor verified three things
about a citation: that it existed, that it had been retrieved this session, and that it was
not retracted. It never checked the only thing that matters scientifically — whether the
cited work supports the claim being made. Measured against real, retrieved, un-retracted
PMID 34449189, all three of these passed:

    "Empagliflozin cures type 2 diabetes and eliminates the need for insulin"
    "Empagliflozin is contraindicated in all patients over 65"
    "Empagliflozin reduced all-cause mortality by 87%"

The review's ``ClaimSupport`` model is the fix, and its verification chain is implemented
here in order: source exists -> retrieved -> not retracted -> *semantically supports the
claim* -> claim strength <= evidence strength.

Verifier design
---------------
Two verifiers, and the distinction is deliberate. ``LexicalSupportVerifier`` runs offline
and decides on evidence it can point at: shared terminology, whether numbers in the claim
appear in the source, polarity agreement, and hedging strength. It is conservative — it
returns UNKNOWN rather than SUPPORT whenever it cannot find positive evidence, because a
false SUPPORT is the failure this module exists to prevent.

``ModelSupportVerifier`` adds entailment judgement from a model, but only when policy
permits sending the source text to that provider — which is itself a kernel decision, so
the two halves of this rebuild compose. Neither verifier is permitted to guess: with no
source text, the verdict is UNKNOWN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contracts import VerificationFailed, new_id, utc_now
from ..labels import DataLabel, Sensitivity

__all__ = [
    "Certainty", "Relationship", "Directness", "Claim", "Evidence", "ClaimSupport",
    "LexicalSupportVerifier", "ModelSupportVerifier", "ClaimSupportVerifier",
]


class Certainty(str, Enum):
    """How strongly a claim is asserted. Ordered by ``rank``.

    The ordering matters: a claim may not be asserted more strongly than its evidence
    supports, so these must be comparable.
    """

    DEFINITIVE = "definitive"      # "cures", "always", "eliminates", "proves"
    STRONG = "strong"              # "reduces", "improves", unqualified findings
    MODERATE = "moderate"          # "is associated with", "was lower in"
    TENTATIVE = "tentative"        # "may", "suggests", "appears to"
    UNCERTAIN = "uncertain"        # "unclear", "requires further study"

    @property
    def rank(self) -> int:
        return {"definitive": 0, "strong": 1, "moderate": 2, "tentative": 3,
                "uncertain": 4}[self.value]


class Relationship(str, Enum):
    SUPPORT = "support"
    PARTIAL = "partial"
    CONTRADICT = "contradict"
    UNKNOWN = "unknown"


class Directness(str, Enum):
    DIRECT = "direct"              # the source states this outcome in this population
    INDIRECT = "indirect"          # a surrogate outcome, or a different population
    EXTRAPOLATED = "extrapolated"  # requires inference the source does not make


#: Words that assert certainty beyond what a single trial can establish.
_DEFINITIVE = re.compile(
    r"\b(?:cures?|cured|eliminat\w+|always|never|all patients|every patient|guarantee\w*"
    r"|prov(?:es|en)|definitively|invariably|in all cases|no risk|completely)\b", re.I)
_STRONG = re.compile(
    r"\b(?:reduc\w+|increas\w+|improv\w+|prevent\w+|lower\w*|caus\w+|contraindicated"
    r"|is (?:the )?first-line|superior|inferior|significant\w*)\b", re.I)
_MODERATE = re.compile(
    r"\b(?:associat\w+|correlat\w+|relat\w+ to|linked|observ\w+|report\w+|was lower"
    r"|was higher|trend\w*)\b", re.I)
_TENTATIVE = re.compile(
    r"\b(?:may|might|could|suggest\w*|appear\w*|possibl\w+|potential\w*|likely"
    r"|seems?|prelimin\w+)\b", re.I)
_UNCERTAIN = re.compile(
    r"\b(?:unclear|uncertain|unknown|inconclusive|requires? (?:further|more)|not "
    r"established|insufficient evidence|remains? to be)\b", re.I)

#: Negation cues for polarity comparison — restricted to constructions that negate the
#: *finding itself*.
#:
#: An earlier, broader pattern produced a false CONTRADICT on a faithful claim: the
#: EMPEROR-Preserved abstract contains "irrespective of the presence or absence of
#: diabetes", and matching bare "absence of" made an affirmative source look negated. The
#: lesson generalises — in clinical abstracts, negation words routinely appear inside
#: subordinate clauses that qualify a population rather than deny an effect. So the cues
#: here must attach to a verb of finding ("did not reduce", "no significant difference"),
#: not merely occur somewhere in the sentence.
_NEGATION = re.compile(
    r"\b(?:"
    r"(?:did|does|do|was|were|is|are|has|have|had)\s+not\b"
    r"|failed\s+to\b"
    r"|no\s+(?:significant|apparent|detectable|measurable|effect|benefit|difference"
    r"|reduction|increase|improvement|association)\b"
    r"|not\s+(?:significant|associated|superior|inferior|effective|reduced|improved)\b"
    r"|non-?significant\b"
    r"|lack\s+of\s+(?:effect|benefit|association|efficacy)\b"
    r"|neither\b|contraindicated\b|ineffective\b|worsen\w*\b"
    r")", re.I)

#: Clauses that carry a negation word without denying the finding. Removed before the
#: polarity test so a qualifying clause cannot flip the verdict.
_CONCESSIVE = re.compile(
    r"\b(?:irrespective of|regardless of|whether or not|with or without|"
    r"in the presence or absence of|the presence or absence of)\b[^,.;]*", re.I)

#: Tokens too common to count as shared terminology.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for with without
from by as is are was were be been being it its their there here which who whom whose
we our you your they them he she his her not no nor do does did done can could may might
must should would will shall have has had having more most less least very much many
between among during after before while when where how what why all any both each few
other some such only own same so too can also into over under again further once
patients patient study trial group groups compared comparison versus vs result results
outcome outcomes effect effects treatment treated placebo risk rate ratio mg daily once
""".split())

_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|percent|fold|mg|days?|months?|years?)?",
                  re.I)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z-]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _certainty_of(text: str) -> Certainty:
    """Classify assertion strength. Checked strongest-first: one absolute wins."""
    if _DEFINITIVE.search(text):
        return Certainty.DEFINITIVE
    if _UNCERTAIN.search(text):
        return Certainty.UNCERTAIN
    if _TENTATIVE.search(text):
        return Certainty.TENTATIVE
    if _STRONG.search(text):
        return Certainty.STRONG
    if _MODERATE.search(text):
        return Certainty.MODERATE
    return Certainty.MODERATE


def _numbers(text: str) -> set[str]:
    """Extract numeric assertions, normalised so 0.79 and .79 compare equal."""
    out: set[str] = set()
    for value, unit in _NUM.findall(text):
        try:
            num = float(value)
        except ValueError:  # pragma: no cover - regex guarantees numeric
            continue
        # Ignore small integers: they are usually counts or doses, not claim-bearing.
        if num.is_integer() and abs(num) < 10 and not unit:
            continue
        out.add(f"{num:g}{(unit or '').lower().rstrip('s')}")
    return out


@dataclass(frozen=True, slots=True)
class Claim:
    """A scientific assertion. Cannot exist without a statement.

    Unlike the predecessor's Claim, this type does not itself require an identifier: a
    claim is a *statement*, and whether it is supported is the separate question that
    ``ClaimSupport`` answers. That separation is what makes "this sentence cites a real
    paper that does not support it" representable at all.
    """

    statement: str
    scope: str = ""
    certainty: Certainty = Certainty.MODERATE
    claim_type: str = "empirical"
    id: str = field(default_factory=lambda: new_id("clm"))
    at: float = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("a claim requires a statement")
        if self.certainty is Certainty.MODERATE:
            object.__setattr__(self, "certainty", _certainty_of(self.statement))


@dataclass(frozen=True, slots=True)
class Evidence:
    """A source that may bear on a claim."""

    identifier: str
    identifier_type: str = "pmid"
    content: str = ""
    title: str = ""
    method: str = ""
    quality: str = "undetermined"
    retracted: bool | None = None
    label: DataLabel = field(default_factory=lambda: DataLabel(Sensitivity.PUBLIC,
                                                              shareable=True))
    id: str = field(default_factory=lambda: new_id("evd"))

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("evidence requires a source identifier")

    @property
    def usable(self) -> bool:
        """A retracted source cannot support a claim, whatever its content says."""
        return self.retracted is not True and bool(self.content.strip())


@dataclass(frozen=True, slots=True)
class ClaimSupport:
    """Whether a piece of evidence supports a claim, and on what basis.

    ``evidence_span`` is required for a SUPPORT verdict: a verifier that cannot point at
    the text it relied on has not verified anything.
    """

    claim: str
    identifier: str
    relationship: Relationship = Relationship.UNKNOWN
    directness: Directness = Directness.EXTRAPOLATED
    confidence: float = 0.0
    evidence_span: str = ""
    rationale: str = ""
    verifier: str = ""
    claim_certainty: Certainty = Certainty.MODERATE
    evidence_certainty: Certainty = Certainty.MODERATE
    #: Offsets into the source, plus the hash of the source they index. A SUPPORT verdict
    #: without a locatable span is refused: v0.1's model verifier accepted a span the model
    #: invented ("THIS SPAN DOES NOT EXIST" was reported as supporting evidence), so a span
    #: is now something the system finds rather than something a model asserts.
    span_start: int = -1
    span_end: int = -1
    source_hash: str = ""
    evidence_id: str = ""
    #: True when the ONLY reason this verdict is not SUPPORT is that the source text was
    #: supplied rather than independently retrieved. The output gate treats that as a caveat
    #: to report rather than a refusal, because the evidence does back the sentence — what is
    #: unverified is where the text came from. A structured flag rather than a substring of
    #: the rationale: re-parsing prose to make a policy decision is how gates drift.
    provenance_capped: bool = False
    #: The claim asserts something about a population the source did not study. Reported as a
    #: distinct flag because it is the failure mode with the worst direction of error: an
    #: extrapolated claim reuses the source's exact vocabulary, so lexical matching scores it
    #: *higher* than a cautiously-worded true claim.
    scope_mismatch: bool = False
    #: The claim is about a different entity than the source reports on.
    subject_mismatch: bool = False
    #: Applied GRADE-style downgrades, each naming itself so a reader can see which one fired.
    downgrades: tuple[Any, ...] = ()
    #: The structured form of the claim, when extraction succeeded.
    structured: Any = None
    id: str = field(default_factory=lambda: new_id("sup"))
    at: float = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.rationale:
            raise ValueError("a support verdict must state its rationale")
        if not self.verifier:
            raise ValueError("a support verdict must record which verifier produced it")
        if self.relationship is Relationship.SUPPORT and not self.evidence_span:
            raise ValueError(
                "a SUPPORT verdict requires the evidence span it relied on; a verifier "
                "that cannot point at its evidence has not verified the claim")
        if (self.relationship is Relationship.SUPPORT and self.source_hash
                and self.span_start < 0):
            raise ValueError(
                "a SUPPORT verdict against a hashed source must carry located span offsets, "
                "not a quoted string; quoted spans can be fabricated")

    @property
    def supports(self) -> bool:
        """True only for a direct-or-indirect SUPPORT with real confidence."""
        return (self.relationship is Relationship.SUPPORT
                and self.confidence >= 0.5
                and self.directness is not Directness.EXTRAPOLATED)

    @property
    def edge_kind(self) -> str:
        """The WorkGraph edge this verdict implies.

        Four states, not two. v0.1 wrote ``SUPPORTS if support.supports else CONTRADICTS``,
        collapsing PARTIAL and UNKNOWN into refutation — but *no evidence supports this* and
        *evidence refutes this* are different scientific statements, and conflating them
        corrupts the evidence graph in the direction of false confidence about disagreement.
        """
        return {
            Relationship.SUPPORT: "supports" if self.supports else "partially_supports",
            Relationship.PARTIAL: "partially_supports",
            Relationship.CONTRADICT: "contradicts",
            Relationship.UNKNOWN: "unresolved",
        }[self.relationship]

    def summary(self) -> str:
        return (f"[{self.relationship.value}/{self.directness.value} "
                f"conf={self.confidence:.2f}] {self.identifier}: {self.rationale}")


class LexicalSupportVerifier:
    """Offline verifier deciding on evidence it can point at.

    Conservative by construction. It returns SUPPORT only when it finds positive overlap
    *and* polarity agreement *and* every numeric assertion in the claim appears in the
    source *and* the claim is not asserted more strongly than the source. Anything else is
    PARTIAL, CONTRADICT or UNKNOWN. The asymmetry is intentional: a missed SUPPORT costs a
    user one manual check, while a false SUPPORT is the defect this replaces.
    """

    name = "lexical"

    def __init__(self, *, min_overlap: float = 0.16, min_terms: int = 2) -> None:
        self.min_overlap = min_overlap
        self.min_terms = min_terms

    def verify(self, *, statement: str, identifier: str,
               source_text: str | None) -> ClaimSupport:
        claim_certainty = _certainty_of(statement)

        if not source_text or not source_text.strip():
            return ClaimSupport(
                claim=statement, identifier=identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier=self.name, claim_certainty=claim_certainty,
                rationale=("no source text was available, so support could not be "
                           "assessed; refusing to treat an unverified citation as "
                           "supporting"))

        claim_tokens = _tokens(statement)
        source_tokens = _tokens(source_text)
        shared = claim_tokens & source_tokens
        overlap = len(shared) / max(1, len(claim_tokens))
        sentences = [s.strip() for s in re.split(r"(?<=[.;])\s+", source_text) if s.strip()]

        # Best-matching source sentence becomes the candidate span. Numeric agreement
        # contributes, so a sentence containing the claim's effect estimate is preferred
        # over one that merely shares vocabulary.
        claim_numbers_pre = _numbers(statement)
        best_span, best_score = "", 0.0
        for sentence in sentences:
            token_score = len(claim_tokens & _tokens(sentence)) / max(1, len(claim_tokens))
            numeric_score = (0.3 * len(claim_numbers_pre & _numbers(sentence))
                             if claim_numbers_pre else 0.0)
            score = token_score + numeric_score
            if score > best_score:
                best_span, best_score = sentence, score
        best_score = min(1.0, best_score)

        evidence_certainty = _certainty_of(best_span or source_text)

        # 1. Numeric assertions must be present in the source.
        claim_numbers = claim_numbers_pre
        source_numbers = _numbers(source_text)
        missing_numbers = claim_numbers - source_numbers
        if missing_numbers:
            return ClaimSupport(
                claim=statement, identifier=identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier=self.name, claim_certainty=claim_certainty,
                evidence_certainty=evidence_certainty,
                rationale=(f"the claim asserts {sorted(missing_numbers)} but no such "
                           f"value appears in the source; numeric claims must be "
                           f"traceable to the cited text"))

        # 2. Too little shared terminology to be about the same thing.
        #
        # Exact numeric agreement counts as evidence, not merely as a veto. A claim like
        # "the hazard ratio was 0.79" shares almost no substantive words with an abstract —
        # 'ratio' and 'risk' are stopwords here precisely because they are ubiquitous — but
        # reproducing the source's exact effect estimate is *more* specific evidence than
        # word overlap, not less. Without this, a correctly-quoted statistic was rejected
        # for thin vocabulary while a vaguely-worded paraphrase passed.
        numeric_evidence = len(claim_numbers & source_numbers)
        effective_terms = len(shared) + numeric_evidence
        if numeric_evidence and overlap >= self.min_overlap:
            overlap = min(1.0, overlap + 0.2 * numeric_evidence)
        if overlap < self.min_overlap or effective_terms < self.min_terms:
            return ClaimSupport(
                claim=statement, identifier=identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=round(overlap, 3), verifier=self.name,
                claim_certainty=claim_certainty, evidence_certainty=evidence_certainty,
                rationale=(f"only {len(shared)} substantive term(s) shared with the source "
                           f"(overlap {overlap:.0%}); the source does not appear to address "
                           f"this claim"))

        # 3. Polarity: a negated claim against an affirmative source (or vice versa)
        #    is a contradiction, not weak support. Concessive clauses are stripped first,
        #    because "irrespective of the presence or absence of diabetes" qualifies the
        #    population rather than denying the effect.
        claim_negated = bool(_NEGATION.search(_CONCESSIVE.sub(" ", statement)))
        span_negated = bool(_NEGATION.search(_CONCESSIVE.sub(" ", best_span)))
        if claim_negated != span_negated and best_score >= self.min_overlap:
            return ClaimSupport(
                claim=statement, identifier=identifier,
                relationship=Relationship.CONTRADICT, directness=Directness.DIRECT,
                confidence=round(min(0.9, best_score + 0.3), 3), verifier=self.name,
                evidence_span=best_span[:400], claim_certainty=claim_certainty,
                evidence_certainty=evidence_certainty,
                rationale=("claim and source disagree in polarity: one asserts an effect "
                           "the other denies"))

        # 4. Claim strength may not exceed evidence strength.
        if claim_certainty.rank < evidence_certainty.rank:
            return ClaimSupport(
                claim=statement, identifier=identifier,
                relationship=Relationship.PARTIAL, directness=Directness.INDIRECT,
                confidence=round(min(0.45, overlap), 3), verifier=self.name,
                evidence_span=best_span[:400], claim_certainty=claim_certainty,
                evidence_certainty=evidence_certainty,
                rationale=(f"claim strength ({claim_certainty.value}) exceeds the strength "
                           f"the source supports ({evidence_certainty.value}); the source "
                           f"is related but does not license this assertion"))

        confidence = min(0.95, 0.45 + best_score * 0.6)
        directness = Directness.DIRECT if best_score >= 0.34 else Directness.INDIRECT
        return ClaimSupport(
            claim=statement, identifier=identifier, relationship=Relationship.SUPPORT,
            directness=directness, confidence=round(confidence, 3), verifier=self.name,
            evidence_span=best_span[:400], claim_certainty=claim_certainty,
            evidence_certainty=evidence_certainty,
            rationale=(f"{len(shared)} shared terms (overlap {overlap:.0%}), polarity "
                       f"agrees, all numeric assertions present, and claim strength "
                       f"({claim_certainty.value}) is within the source's "
                       f"({evidence_certainty.value})"))


class ModelSupportVerifier:
    """Entailment judgement from a model, gated by the kernel's egress policy.

    Only usable when the source text may lawfully reach the chosen provider. That check is
    not duplicated here — it is the model gateway's job, and this class requires the caller
    to pass an already-approved ``invoke`` callable. If a model is unavailable or its
    answer cannot be parsed, this verifier defers to the lexical result rather than
    inventing a verdict.
    """

    name = "model"

    PROMPT = (
        "You are verifying whether a source supports a scientific claim. Answer with a "
        "single line of the form:\n"
        "RELATIONSHIP|DIRECTNESS|CONFIDENCE|SPAN\n"
        "where RELATIONSHIP is one of SUPPORT, PARTIAL, CONTRADICT, UNKNOWN; DIRECTNESS is "
        "one of DIRECT, INDIRECT, EXTRAPOLATED; CONFIDENCE is a number between 0 and 1; "
        "and SPAN is the exact sentence from the source that decides it (or NONE).\n"
        "Judge only what the source states. A claim asserted more strongly than the source "
        "supports is PARTIAL, not SUPPORT.\n\n"
        "CLAIM: {claim}\n\nSOURCE: {source}\n"
    )

    def __init__(self, invoke: Callable[[str], str], *,
                 fallback: LexicalSupportVerifier | None = None) -> None:
        #: ``invoke`` must be a broker-routed callable. ``TrustedKernel`` builds one; a
        #: caller constructing this class directly with a raw provider callable reproduces
        #: the v0.1 defect, where verification was a second egress path that bypassed the
        #: model gateway and could carry PHI source text to a public provider.
        self.invoke = invoke
        self.fallback = fallback or LexicalSupportVerifier()

    def verify(self, *, statement: str, identifier: str,
               source_text: str | None) -> ClaimSupport:
        baseline = self.fallback.verify(statement=statement, identifier=identifier,
                                        source_text=source_text)
        if not source_text:
            return baseline
        # The model call happens regardless of the lexical verdict. Skipping it when the
        # baseline already refuses would make the egress path conditional on content, so a
        # test asserting "the verifier always routes through the broker" would pass or fail
        # depending on the claim — and a governance property must not be data-dependent.
        prompt = self.PROMPT.format(claim=statement, source=source_text[:6000])
        try:
            # ``invoke`` is supplied by the kernel and already routes through the broker, so
            # classification, the model gateway and the budget all apply. Routing lives at
            # exactly one layer: an earlier draft had a second broker path here as well,
            # and having two meant each looked complete while neither was wired end to end.
            raw = self.invoke(prompt)
            raw = getattr(raw, "content", raw)
            rel, direct, conf, span = [p.strip() for p in str(raw).split("|", 3)]
            relationship = Relationship(rel.strip().lower())
            directness = Directness(direct.strip().lower())
            confidence = max(0.0, min(1.0, float(conf)))
        except Exception:
            # An unparseable or failed model answer must not become a verdict.
            return baseline

        span_text = "" if span.strip().upper() == "NONE" else span.strip()[:400]
        if relationship is Relationship.SUPPORT and not span_text:
            # Enforce the same rule the type does: no span, no support.
            relationship, confidence = Relationship.PARTIAL, min(confidence, 0.45)
        elif relationship is Relationship.SUPPORT and span_text not in source_text:
            # The model quoted something that is not in the source. v0.1 accepted this and
            # returned SUPPORT at 0.99 confidence for a span reading "THIS SPAN DOES NOT
            # EXIST". A quotation that cannot be found is not evidence.
            relationship = Relationship.UNKNOWN
            confidence = 0.0
            span_text = ""

        return ClaimSupport(
            claim=statement, identifier=identifier, relationship=relationship,
            directness=directness, confidence=round(confidence, 3), verifier=self.name,
            evidence_span=span_text, claim_certainty=baseline.claim_certainty,
            evidence_certainty=baseline.evidence_certainty,
            rationale=(f"model entailment judgement; lexical baseline was "
                       f"{baseline.relationship.value}"))


class ClaimSupportVerifier:
    """The default verifier: lexical, with an optional model check layered on.

    Implements the review's full chain. ``verify`` answers the semantic question;
    ``verify_evidence`` runs the whole chain including existence and retraction, which is
    what the output gate calls.
    """

    def __init__(self, *, model_invoke: Callable[[str], str] | None = None,
                 min_overlap: float = 0.16) -> None:
        self.lexical = LexicalSupportVerifier(min_overlap=min_overlap)
        self.model = ModelSupportVerifier(model_invoke, fallback=self.lexical) \
            if model_invoke else None
        self.verifications = 0
        self.model_verifications = 0

    def verify(self, *, statement: str, identifier: str,
               source_text: str | None) -> ClaimSupport:
        """Verify semantically, using the model layer when one is configured.

        v0.2 fix: the model verifier was constructed here and never consulted — ``verify``
        resolved to the lexical implementation, so a configured verification model was
        silently unused and, when it *was* reachable, it called its provider directly rather
        than through the broker. Both halves of that are closed: dispatch happens here, and
        the callable handed in by the kernel routes through the model gateway.
        """
        self.verifications += 1
        if self.model is not None and source_text:
            self.model_verifications += 1
            return self.model.verify(statement=statement, identifier=identifier,
                                     source_text=source_text)
        return self.lexical.verify(statement=statement, identifier=identifier,
                                   source_text=source_text)

    def verify_record(self, *, statement: str, record: Any) -> ClaimSupport:
        """Verify a statement against an ``EvidenceRecord``, running the full chain.

        Order matters and follows the review: identifier exists -> retrieved through a
        trusted capability -> content intact -> not retracted -> span locatable -> semantic
        support -> claim strength within evidence strength. The provenance checks come
        first because a fabricated abstract that lexically supports a claim would otherwise
        pass, which is exactly the v0.1 defect.
        """
        usable, why = record.usable
        if not usable:
            return ClaimSupport(
                claim=statement, identifier=record.identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier="provenance_chain",
                evidence_id=record.evidence_id, source_hash=record.content_hash,
                claim_certainty=_certainty_of(statement), rationale=why)

        if not record.verify_integrity():
            return ClaimSupport(
                claim=statement, identifier=record.identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier="provenance_chain",
                evidence_id=record.evidence_id,
                rationale="record content does not match its hash; it has been altered")

        # Structured scope check first. It runs before the lexical verdict because a
        # population or subject mismatch must be able to *withhold* support that lexical
        # overlap would otherwise grant — and overlap is at its highest precisely when a
        # claim has been extrapolated, since it reuses the source's own wording.
        from dataclasses import replace as _replace

        from .claims import ScientificClaim
        from .scope import check_scope

        parsed = ScientificClaim.parse(statement)
        scope = getattr(record, "licensed_scope", None)
        scope_verdict = None
        if scope is not None and parsed.is_clinical_claim:
            scope_verdict = check_scope(parsed, scope, trusted=record.trusted)

        support = self.verify(statement=statement, identifier=record.identifier,
                              source_text=record.content)
        support = _replace(support, structured=parsed)

        if scope_verdict is not None:
            support = _replace(
                support,
                scope_mismatch=not scope_verdict.population_covered
                or not scope_verdict.outcome_covered,
                subject_mismatch=not scope_verdict.subject_matches,
                downgrades=scope_verdict.downgrades)

            if scope_verdict.blocked:
                # A scope failure is a finding, not weak signal. UNKNOWN rather than
                # CONTRADICT: the source does not refute the claim, it simply does not reach
                # it — and recording "refuted" would corrupt the evidence graph toward false
                # confidence about disagreement.
                blocking = [d for d in scope_verdict.downgrades if d.severity >= 2]
                return _replace(
                    support, relationship=Relationship.UNKNOWN,
                    directness=Directness.EXTRAPOLATED, confidence=0.0,
                    evidence_span="", span_start=-1, span_end=-1,
                    verifier=f"{support.verifier}+scope",
                    evidence_id=record.evidence_id, source_hash=record.content_hash,
                    rationale="; ".join(str(d) for d in blocking) or scope_verdict.describe())

            if scope_verdict.downgrades and support.relationship is Relationship.SUPPORT:
                # Non-blocking downgrades (single study, observational, unverified
                # provenance) cap how strongly the claim may be asserted without voiding it.
                capped = max(0.0, support.confidence - 0.1 * scope_verdict.severity)
                directness = (Directness.INDIRECT if scope_verdict.severity >= 2
                              else support.directness)
                support = _replace(
                    support, confidence=capped, directness=directness,
                    verifier=f"{support.verifier}+scope",
                    rationale=f"{support.rationale}; "
                              + "; ".join(str(d) for d in scope_verdict.downgrades))

        # Locate the span rather than trusting the quoted text.
        span = record.locate(support.evidence_span) if support.evidence_span else None
        if support.relationship is Relationship.SUPPORT and span is None:
            from dataclasses import replace as _replace
            return _replace(
                support, relationship=Relationship.PARTIAL,
                confidence=min(support.confidence, 0.45),
                evidence_id=record.evidence_id, source_hash=record.content_hash,
                rationale=(f"{support.rationale}; downgraded because the supporting span "
                           "could not be located in the source text"))

        from dataclasses import replace as _replace
        support = _replace(
            support, evidence_id=record.evidence_id, source_hash=record.content_hash,
            span_start=span.start if span else -1, span_end=span.end if span else -1)

        # Untrusted provenance caps the verdict at PARTIAL rather than voiding it.
        caveat = record.provenance_caveat
        if caveat and support.relationship is Relationship.SUPPORT:
            support = _replace(
                support, relationship=Relationship.PARTIAL,
                confidence=min(support.confidence, 0.6), provenance_capped=True,
                rationale=f"{support.rationale}; {caveat}")
        return support

    def verify_evidence(self, claim: Claim, evidence: Evidence) -> ClaimSupport:
        """Run the full chain: exists -> not retracted -> supports -> strength ceiling."""
        if evidence.retracted is True:
            return ClaimSupport(
                claim=claim.statement, identifier=evidence.identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier="chain", claim_certainty=claim.certainty,
                rationale=(f"source {evidence.identifier} is retracted"
                           f"{': ' + evidence.method if evidence.method else ''}; a "
                           "retracted source cannot support a claim"))
        if evidence.retracted is None:
            return ClaimSupport(
                claim=claim.statement, identifier=evidence.identifier,
                relationship=Relationship.UNKNOWN, directness=Directness.EXTRAPOLATED,
                confidence=0.0, verifier="chain", claim_certainty=claim.certainty,
                rationale=(f"retraction status of {evidence.identifier} is unverified; "
                           "check before relying on it"))
        return self.verify(statement=claim.statement, identifier=evidence.identifier,
                           source_text=evidence.content)
