"""The context compiler: context is a build product, not accumulated chat history.

The review's argument is that prompt assembly should be a first-class runtime stage with
its own pipeline — retrieve, rank, deduplicate, classify, compress, budget, assemble — and
that each delegated worker receives its *own* projection rather than a shared transcript.

Two properties matter beyond token thrift:

1. **The projection carries an aggregate label.** Whatever went in determines what the
   whole context is classified as, so the model gateway can decide provider eligibility
   from one field instead of re-scanning assembled text. This is what makes the compiler
   part of the security story rather than only an efficiency measure.

2. **Compilation can *drop* items on policy grounds.** If a projection is destined for a
   provider that may not receive PHI, PHI-labelled items are excluded during compilation
   rather than causing a refusal at the gateway. A refusal is correct but useless; a
   redacted projection lets the work continue with what is permissible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contracts import ContextItem, ContextProjection, RunEnvelope
from ..labels import DataLabel, Destination, Sensitivity, combine

__all__ = ["ContextCompiler", "CompilationTrace"]

#: Kinds that may never be dropped by the policy filter. Dropping either silently changes
#: what the model was asked, which is a correctness failure, not a redaction.
_LOAD_BEARING = frozenset({"instruction", "turn"})


@dataclass
class CompilationTrace:
    """What the compiler did, for inspection and benchmarking."""

    candidates: int = 0
    after_dedup: int = 0
    dropped_policy: int = 0
    dropped_budget: int = 0
    compacted: int = 0
    included: int = 0
    tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"candidates": self.candidates, "after_dedup": self.after_dedup,
                "dropped_policy": self.dropped_policy,
                "dropped_budget": self.dropped_budget, "compacted": self.compacted,
                "included": self.included, "tokens": self.tokens}


class ContextCompiler:
    """Compiles a ``ContextProjection`` for one task and one destination.

    Ranking weights are explicit parameters rather than constants, because the right
    balance differs by work mode: a literature review wants evidence weighted heavily, a
    coding task wants procedural memory.
    """

    #: Relative priority by item kind. Instructions are never dropped for budget.
    DEFAULT_WEIGHTS: Mapping[str, float] = {
        "instruction": 100.0, "manifest": 3.0, "evidence": 2.5, "memory": 2.0,
        "turn": 1.5,
    }

    def __init__(self, *, weights: Mapping[str, float] | None = None,
                 compressor: Callable[[str, int], str] | None = None,
                 compactor: Any = None) -> None:
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.compressor = compressor or _truncate_compressor
        #: Turns what will not fit into one labelled summary instead of discarding it.
        #: On by default: reporting a count of dropped items tells the *caller* something
        #: was omitted and tells the model, which is the party that has to answer with the
        #: gap, nothing at all.
        if compactor is None:
            from .compaction import Compactor
            compactor = Compactor()
        self.compactor = compactor
        self.compilations = 0
        self.last_trace = CompilationTrace()
        #: The most recent compaction, kept so a run can say what it set aside.
        self.last_compaction = None

    def compile(self, *, items: Iterable[ContextItem], envelope: RunEnvelope,
                destination: Destination = Destination.LOCAL_MODEL,
                token_budget: int | None = None,
                query: str = "") -> ContextProjection:
        """Run the pipeline and return the projection."""
        self.compilations += 1
        trace = CompilationTrace()
        budget = token_budget or envelope.budget.tokens_soft
        candidates = list(items)
        trace.candidates = len(candidates)

        # --- deduplicate: identical or near-identical content earns one slot ---
        seen: dict[str, ContextItem] = {}
        for item in candidates:
            key = _fingerprint(item.content)
            existing = seen.get(key)
            if existing is None or item.score > existing.score:
                seen[key] = item
        deduped = list(seen.values())
        trace.after_dedup = len(deduped)

        # --- policy filter: exclude what this destination may not receive ---------
        #
        # Load-bearing kinds are NEVER silently dropped. An earlier version filtered every
        # kind uniformly, which produced a dangerous success: a PHI-bearing user question
        # heading for a public model had the *question itself* removed, and the run then
        # completed against a clean-but-meaningless context. Silently answering a different
        # question than the one asked is worse than refusing.
        #
        # So: retrieved material (memory, evidence, manifests) may be dropped to make a
        # projection permissible, but the instruction block and the current turn may not.
        # If those cannot lawfully reach the destination, that is a refusal for the
        # gateway to raise, not something the compiler may paper over.
        permitted: list[ContextItem] = []
        for item in deduped:
            if item.kind in _LOAD_BEARING or item.label.permits(destination):
                permitted.append(item)
            else:
                trace.dropped_policy += 1
        # --- rank ----------------------------------------------------------------
        query_terms = _terms(query)
        def rank(item: ContextItem) -> float:
            weight = self.weights.get(item.kind, 1.0)
            relevance = 0.0
            if query_terms:
                overlap = len(query_terms & _terms(item.content))
                relevance = overlap / max(1, len(query_terms))
            return weight * (1.0 + relevance) + item.score

        permitted.sort(key=rank, reverse=True)

        # --- budget: fill, compressing the last item that would overflow ---------
        included: list[ContextItem] = []
        overflow: list[ContextItem] = []
        used = 0
        for item in permitted:
            if used + item.tokens <= budget:
                included.append(item)
                used += item.tokens
                continue
            remaining = budget - used
            if remaining >= 64 and item.kind not in _LOAD_BEARING:
                shortened = self.compressor(item.content, remaining * 4)
                compressed = ContextItem(kind=item.kind, content=shortened,
                                         label=item.label, source_ref=item.source_ref,
                                         score=item.score)
                if used + compressed.tokens <= budget:
                    included.append(compressed)
                    used += compressed.tokens
                    continue
            if item.kind in _LOAD_BEARING:
                # Keep it and overrun rather than answer a mutilated question. The
                # projection reports itself as over budget so the caller can react.
                included.append(item)
                used += item.tokens
                continue
            overflow.append(item)
            trace.dropped_budget += 1

        # --- compaction: say what did not fit, rather than only counting it -------
        #
        # The dropped items used to vanish, and the projection reported a number. The model
        # receiving the context could not tell it was incomplete, so it could not hedge,
        # ask, or name the part of the question it was unable to address.
        self.last_compaction = None
        if overflow and self.compactor is not None:
            summary, record = self.compactor.compact(
                overflow, budget_tokens=max(budget - used, 0), destination=destination)
            self.last_compaction = record
            if summary is not None:
                # Included even when it overruns, on the same reasoning that keeps a
                # load-bearing item: a projection that is honestly over budget is better
                # than one that is quietly wrong. ``within_budget`` reports it.
                included.append(summary)
                used += summary.tokens
                trace.compacted = record.items

        # Restore reading order within each kind, so the prompt is coherent.
        order = {k: i for i, k in enumerate(
            ("instruction", "manifest", "memory", "evidence", "turn"))}
        included.sort(key=lambda i: order.get(i.kind, 99))

        trace.included = len(included)
        trace.tokens = used
        self.last_trace = trace

        return ContextProjection(
            items=tuple(included), token_budget=budget,
            label=combine(*[i.label for i in included]) if included else DataLabel(),
            dropped=trace.dropped_policy + trace.dropped_budget, run_id=envelope.run_id)


_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


_TOKEN = re.compile(r"[a-z0-9][a-z0-9.-]*")


def _fingerprint(text: str) -> str:
    """Normalise for dedup: case, whitespace and punctuation insensitive.

    Numbers are load-bearing and must be part of the fingerprint. An earlier version reused
    the word pattern, which requires a leading letter and therefore discarded every numeral —
    so "cohort 1 result" and "cohort 2 result" produced identical fingerprints and one was
    silently dropped. The benchmark caught it: 592 distinct candidates deduplicated to 4.
    Silently discarding a distinct memory or evidence item is a correctness failure, not a
    thrift measure.
    """
    return " ".join(sorted(_TOKEN.findall(text.lower())))[:400]


def _truncate_compressor(text: str, max_chars: int) -> str:
    """Default compressor: keep the head and tail, mark the elision.

    Deliberately not a model call. Compression on the critical path must not itself require
    egress, or the compiler would need a gateway decision to build the context that the
    gateway is about to judge.
    """
    if len(text) <= max_chars:
        return text
    if max_chars < 80:
        return text[:max_chars]
    head = int(max_chars * 0.6)
    tail = max_chars - head - 24
    return f"{text[:head]}\n[... {len(text) - head - tail} chars elided ...]\n{text[-tail:]}"
