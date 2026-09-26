"""The final output gate: nothing important reaches the user unchecked.

The review's requirement is that final responses, manuscript text, clinical conclusions,
figures and statistical results all pass an output check. Two independent concerns:

1. **Egress**: is this value permitted at USER_OUTPUT? A PHI-labelled answer is fine for
   the treating clinician but must not be written into a shared manuscript draft.
2. **Claim support**: does every clinical sentence carrying a citation have a source that
   actually supports it? This is where the second demonstrated hole is closed at the
   boundary, so a component that skips verification internally still cannot ship an
   unsupported claim.

The gate raises rather than annotating, because the review places it on the path to the
user: a warning appended to text the user has already been handed is not a gate.
"""

from __future__ import annotations

import threading

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts import (
    ContextProjection, EgressDenied, RunEnvelope, VerificationFailed,
)
from ..evidence.support import Claim, ClaimSupport, ClaimSupportVerifier, Relationship
from ..labels import DataLabel, Destination, Labeled, label_of, unwrap

__all__ = ["OutputGate", "OutputVerdict"]

#: Identifier shapes recognised in prose.
_IDS = (
    re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.I),
    re.compile(r"\b(NCT\d{8})\b"),
    re.compile(r"\bdoi:\s*(10\.\d{4,9}/[-._;()/:a-z0-9]+)\b", re.I),
)

#: Sentences making a clinical assertion. Deliberately narrower than "any sentence":
#: gating prose that makes no clinical claim would make the harness unusable for drafting.
_CLINICAL = re.compile(
    r"\b(?:reduc\w+|increas\w+|improv\w+|prevent\w+|cur\w+|treat\w+|caus\w+|eliminat\w+"
    r"|contraindicat\w+|indicat\w+|efficac\w+|mortality|morbidity|survival|outcome"
    r"|first-line|superior|inferior|risk|dose|adverse|safe\w*|associat\w+)\b", re.I)

#: The same assertion in Chinese. A harness for a physician-scientist who publishes in two
#: languages that gates only one of them has a language-shaped hole in its output policy: an
#: unsupported claim is no less a claim for being written in Chinese. Chinese has no word
#: boundaries, so these match without ``\b``.
_CLINICAL_ZH = re.compile(
    "(?:降低|升高|增高|增加|减少|改善|提高|缓解|预防|治疗|治愈|导致|引起|诱发|加重"
    "|死亡率|病死率|生存率|生存期|发病率|患病率|复发率|有效率|疗效|不良反应|副作用"
    "|安全性|禁忌|适应症|适应证|剂量|显著|明显|优于|劣于|相关|危险因素|保护因素"
    "|独立预测|预后|敏感性|特异性|准确率|阳性率)")

#: A reported statistic is a claim about the world even when no clinical verb appears.
#: "AUC was 0.91", "the odds ratio was 1.84", "sensitivity reached 93%" each assert
#: something that must trace to a source, and v0.4's verb list matched none of them — the
#: sentences a methods-heavy manuscript is mostly made of were the ones the gate ignored.
_STATISTICAL = re.compile(
    r"(?:\b(?:auc|auroc|auprc|c-statistic|c-index|hazard ratio|odds ratio|risk ratio"
    r"|relative risk|incidence rate ratio|mean difference|effect size"
    r"|sensitivit\w+|specificit\w+|accurac\w+|precision|recall|f1|kappa|r-squared)\b"
    r"|\b(?:hr|or|rr|ci|sd|se|iqr)\s*[=:]\s*[-+]?\d"
    r"|\bp\s*[<>=≤≥]\s*0?\.\d+"
    r"|\b\d+(?:\.\d+)?\s*%\s*(?:ci|confidence)"
    r"|(?:曲线下面积|风险比|比值比|相对危险度|置信区间|可信区间|统计学(?:意义|差异)|显著性差异))",
    re.I)

#: Constructions that make a sentence NOT a claim about the world: statements of intent,
#: methodology, or calls for further work. Distinct from hedging, which weakens a claim
#: without removing it.
_NON_CLAIM = re.compile(
    r"\b(?:we (?:plan|propose|will|intend|aim)|this (?:study|analysis|paper) (?:will|aims)"
    r"|further (?:study|research|work) is (?:needed|required|warranted)"
    r"|future (?:studies|work)|methods? section|protocol specifies"
    r"|has not been (?:studied|investigated))\b"
    r"|(?:本研究拟|有待(?:进一步|今后)|尚需进一步|未来研究|尚未(?:研究|证实|明确)|方法部分)", re.I)

#: Hedge cues. Retained for certainty grading, no longer used to skip verification.
_HEDGE = re.compile(
    r"\b(?:may|might|could|suggest\w*|appear\w*|possibl\w+|unclear|uncertain|hypothes\w+"
    r"|would|not established)\b", re.I)


@dataclass(frozen=True, slots=True)
class OutputVerdict:
    """The gate's decision, retained whether it passed or not."""

    allowed: bool
    reason: str
    supports: tuple[ClaimSupport, ...] = ()
    unsupported: tuple[str, ...] = ()
    uncited: tuple[str, ...] = ()
    label: DataLabel = field(default_factory=DataLabel)

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("an output verdict must state its reason")


class OutputGate:
    """Checks anything about to be returned to the user.

    Parameters
    ----------
    verifier:
        The claim-support verifier. Shared with the evidence layer so a single
        configuration governs both.
    require_support:
        When True (the default) a cited clinical sentence whose source does not support it
        blocks the output. When False the gate records the verdict and allows the text,
        which is appropriate only for drafting modes.
    require_citation:
        When True, a clinical assertion carrying no identifier at all also blocks. Off by
        default: an unhedged sentence in a draft is a normal intermediate state, and the
        review places strict citation policy in the literature and writing profiles rather
        than in the kernel.
    """

    name = "final_output_gate"

    def __init__(self, *, verifier: ClaimSupportVerifier | None = None,
                 require_support: bool = True, require_citation: bool = False,
                 audit: Callable[..., Any] | None = None) -> None:
        self.verifier = verifier or ClaimSupportVerifier()
        self.require_support = require_support
        self.require_citation = require_citation
        self._audit = audit
        self.verdicts: list[OutputVerdict] = []
        self.checks = 0
        self._count_lock = threading.Lock()

    @staticmethod
    def is_clinical(sentence: str) -> bool:  # noqa: D401 - name kept for callers
        """Whether a sentence makes a clinical assertion that needs a source.

        Hedging does **not** exempt a sentence. v0.1 skipped any sentence containing a hedge
        word, so "Drug X may cure pancreatic cancer" bypassed verification entirely while
        the unhedged form was checked — an evasion available to anyone who writes "may".

        Hedging belongs in ``Certainty``, where it lowers the evidence bar: a speculative
        claim needs weaker evidence than a definitive one, but it still needs evidence. A
        sentence that is purely methodological or forward-looking ("we plan to", "further
        study is needed") makes no claim about the world and is genuinely out of scope.
        """
        asserts = (_CLINICAL.search(sentence) or _CLINICAL_ZH.search(sentence)
                   or _STATISTICAL.search(sentence))
        if not asserts:
            return False
        return not _NON_CLAIM.search(sentence)

    def check(self, output: Any, envelope: RunEnvelope, *,
              sources: Mapping[str, Any] | None = None,
              require_citation: bool | None = None,
              require_support: bool | None = None,
              destination: Destination = Destination.USER_OUTPUT) -> OutputVerdict:
        """Verify an output. Raises on refusal; returns the verdict on success.

        ``require_citation`` and ``require_support`` may be passed per call to state a
        requirement **stronger** than the gate's own. A run executing under a policy
        narrower than the kernel's needs that: this gate is constructed once from the
        kernel's policy, so a run whose policy turns claim support on had nowhere to say so
        and the stricter setting silently did not apply. They can only tighten — ``or``,
        never assignment — so a per-call argument cannot switch a requirement off.
        """
        with self._count_lock:
            self.checks += 1
        require_support = bool(self.require_support or require_support)
        label = label_of(output)
        text = unwrap(output)
        text = text if isinstance(text, str) else str(text)

        # --- 1. egress -------------------------------------------------------
        if not envelope.permits_destination(destination):
            verdict = OutputVerdict(
                False, label=label,
                reason=(f"run profile {envelope.profile!r} does not permit output to "
                        f"{destination.name}"))
            self._record(verdict)
            raise EgressDenied(verdict.reason, label=label, destination=destination)

        if not label.permits(destination):
            verdict = OutputVerdict(
                False, label=label,
                reason=(f"output is classified {label.sensitivity.name} and may not be "
                        f"released to {destination.name}"))
            self._record(verdict)
            raise EgressDenied(verdict.reason, label=label, destination=destination)

        # --- 2. claim support ------------------------------------------------
        sources = dict(sources or {})
        require_citation = (self.require_citation if require_citation is None
                            else require_citation)
        supports: list[ClaimSupport] = []
        unsupported: list[str] = []
        caveats: list[str] = []
        uncited: list[str] = []

        for sentence in self._sentences(text):
            if not self.is_clinical(sentence):
                continue
            identifiers = self._identifiers(sentence)
            if not identifiers:
                uncited.append(sentence[:200])
                continue
            for identifier in identifiers:
                source = sources.get(identifier)
                statement = self._strip_citations(sentence)
                if source is not None and hasattr(source, "content_hash"):
                    support = self.verifier.verify_record(statement=statement,
                                                          record=source)
                else:
                    support = self.verifier.verify(
                        statement=statement, identifier=identifier,
                        source_text=source if isinstance(source, str) else None)
                supports.append(support)
                # A claim downgraded solely because its source was supplied rather than
                # retrieved is reported as a caveat, not a refusal: the evidence does back
                # the sentence, and what is unverified is the provenance of the text. A
                # fabrication fails the semantic check itself and still refuses here.
                if support.provenance_capped:
                    caveats.append(f"{identifier}: source supplied, not independently "
                                   "retrieved")
                elif not support.supports:
                    unsupported.append(f"{identifier}: {sentence[:160]}")

        if unsupported and require_support:
            verdict = OutputVerdict(
                False, supports=tuple(supports), unsupported=tuple(unsupported),
                uncited=tuple(uncited), label=label,
                reason=(f"{len(unsupported)} cited clinical claim(s) are not supported by "
                        f"the source cited: " + "; ".join(unsupported[:3])))
            self._record(verdict)
            raise VerificationFailed(verdict.reason)

        # A clinical assertion with no source at all is refused whenever claim support is
        # required, not only under require_citation. Otherwise hedging plus omitting the
        # citation evades verification entirely — the sentence is neither checked (no
        # identifier) nor blocked (citation not mandatory), which is the gap the reviewer
        # found from the other direction.
        if uncited and (require_citation or require_support):
            verdict = OutputVerdict(
                False, supports=tuple(supports), uncited=tuple(uncited), label=label,
                reason=(f"{len(uncited)} clinical assertion(s) carry no source identifier: "
                        + "; ".join(uncited[:3])))
            self._record(verdict)
            raise VerificationFailed(verdict.reason)

        verdict = OutputVerdict(
            True, supports=tuple(supports), uncited=tuple(uncited), label=label,
            reason=(f"{len(supports)} citation(s) verified, {len(uncited)} uncited "
                    f"assertion(s), released at {label.sensitivity.name}"))
        self._record(verdict)
        return verdict

    # ----------------------------------------------------------------- helpers
    def _record(self, verdict: OutputVerdict) -> None:
        self.verdicts.append(verdict)
        if self._audit is not None:
            self._audit("output_gate" if verdict.allowed else "output_refused",
                        allowed=verdict.allowed, citations=len(verdict.supports),
                        unsupported=len(verdict.unsupported),
                        sensitivity=verdict.label.sensitivity.name)

    #: Sentence terminators, Latin and CJK. Chinese prose is not space-separated and ends
    #: sentences with 。！？；, so the v0.4 split on ``(?<=[.!?])\s+`` returned a whole
    #: Chinese paragraph as one "sentence" — one blob that either failed wholesale or,
    #: carrying a single citation, passed wholesale. Neither is a check.
    _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？；])\s*|\n+")

    @classmethod
    def _sentences(cls, text: str) -> list[str]:
        return [s.strip() for s in cls._SENTENCE_SPLIT.split(text) if s and s.strip()]

    @staticmethod
    def _identifiers(text: str) -> list[str]:
        out: list[str] = []
        for pattern in _IDS:
            out.extend(m.group(1) for m in pattern.finditer(text))
        return list(dict.fromkeys(out))

    @staticmethod
    def _strip_citations(sentence: str) -> str:
        """Remove citation parentheticals so they do not inflate token overlap."""
        cleaned = sentence
        for pattern in _IDS:
            cleaned = pattern.sub(" ", cleaned)
        return re.sub(r"\(\s*[;,]?\s*\)", " ", cleaned).strip()
