"""Compaction: what is left out is summarised and said, not silently dropped.

The compiler fills a token budget by rank and discards what does not fit, reporting a
count. A count is not much use to the model receiving the projection: it is answering with
an incomplete context and has no way to know, so it cannot hedge, ask for more, or say
which part of the question it could not address. Long research runs make this the normal
case rather than the edge case.

The shape is DeepSeek Harness's: compaction emits a **summary** and **shadows** what it
replaced, rather than truncating the window. The originals are not deleted — they are
recorded in a ``CompactionRecord`` so the run can say what was set aside.

One property here is specific to this package and is the reason compaction could not be a
generic utility:

> **A summary inherits the join of the labels it summarises.**

``DEFAULT_SYSTEM_PROMPT`` already tells the model "a summary of identifiable content is
still identifiable". That has to be true by construction rather than by instruction. If a
summary's label were derived by re-classifying the summary *text*, then a summary of PHI
that happened to omit the identifiers would classify as PUBLIC — and would then be
permitted at a destination the material it came from could never reach. Summarisation
would be a laundering path, reachable by accident rather than by attack. So the label is
computed from the inputs, never from the output.

The default summariser is extractive and deterministic. Compression on the critical path
must not itself require egress, for the reason ``_truncate_compressor`` already gives: the
compiler would need a gateway decision in order to build the context the gateway is about
to judge. A model-backed summariser can be supplied, and belongs behind the broker like any
other model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..contracts import ContextItem, content_hash
from ..labels import DataLabel, Destination, Sensitivity, combine

__all__ = ["Compactor", "CompactionRecord", "extractive_summary"]

#: The smallest compaction worth emitting. Below this there is no room to say anything
#: useful, and a notice is emitted instead.
MIN_SUMMARY_TOKENS = 48


@dataclass(frozen=True, slots=True)
class CompactionRecord:
    """What was set aside, and what replaced it. Kept whether or not it was included."""

    items: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    label: DataLabel = field(default_factory=DataLabel)
    shadowed: tuple[str, ...] = ()
    #: Set when the summary itself could not reach the destination, so only a count was
    #: included. The material is still recorded here.
    withheld: bool = False

    @property
    def saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def summary(self) -> str:
        what = "withheld" if self.withheld else "summarised"
        return (f"{self.items} item(s) {what}, {self.tokens_before} -> "
                f"{self.tokens_after} tokens, label={self.label.sensitivity.name}")


_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")


def extractive_summary(items: Sequence[ContextItem], max_chars: int) -> str:
    """Deterministic summary: the leading sentence of each item, newest first.

    Extractive rather than abstractive, and that is a correctness decision as much as an
    engineering one. An abstractive summary states things the source did not, and this text
    is about to be presented to a model as context it may rely on. Sentences lifted
    verbatim can be wrong only in what they omit.
    """
    lines: list[str] = []
    used = 0
    for item in items:
        text = " ".join(item.content.split())
        if not text:
            continue
        first = _SENTENCE.split(text, maxsplit=1)[0]
        label = f"[{item.source_ref}] " if item.source_ref else ""
        line = f"- {label}{first}"
        if len(line) > 240:
            line = line[:237] + "..."
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


class Compactor:
    """Turns items that will not fit into one labelled summary item."""

    def __init__(self,
                 summarise: Callable[[Sequence[ContextItem], int], str] | None = None
                 ) -> None:
        #: Supply a model-backed summariser here if you want one; it must route through
        #: ``ExecutionBroker`` like any other model call, and it will be handed a token
        #: allowance rather than being trusted to respect one.
        self.summarise = summarise or extractive_summary
        self.compactions = 0

    def compact(self, items: Sequence[ContextItem], *, budget_tokens: int,
                destination: Destination = Destination.LOCAL_MODEL,
                ceilings=None) -> tuple[ContextItem | None, CompactionRecord]:
        """Return the summary item to include, and the record of what it stands for.

        The item is ``None`` only when there is nothing to compact. When the summary's
        label may not reach ``destination`` a bare count is returned instead — a number
        carries no content, so it is safe where the material is not, and the run still
        learns that something was set aside.
        """
        if not items:
            return None, CompactionRecord()

        self.compactions += 1
        # The join, from the INPUTS. Never from the summary text: a summary of PHI that
        # omits the identifiers would otherwise classify as PUBLIC and become permitted
        # somewhere its sources could never go.
        label = combine(*[i.label for i in items])
        tokens_before = sum(i.tokens for i in items)
        shadowed = tuple(i.source_ref or content_hash(i.content)[:16] for i in items)

        if not label.permits(destination, ceilings):
            notice = ContextItem(
                kind="instruction",
                content=(f"[compaction] {len(items)} item(s) of {label.sensitivity.name} "
                         "material were withheld from this context because they may not "
                         f"reach {destination.name}. Answer from what is present, and say "
                         "so if it is not enough."),
                label=DataLabel(Sensitivity.PUBLIC))
            return notice, CompactionRecord(
                items=len(items), tokens_before=tokens_before, tokens_after=notice.tokens,
                label=label, shadowed=shadowed, withheld=True)

        allowance = max(MIN_SUMMARY_TOKENS, int(budget_tokens))
        body = self.summarise(list(items), allowance * 4)
        content = (f"[compaction] {len(items)} item(s) did not fit and are summarised "
                   f"here; treat this as partial.\n{body}")
        summary = ContextItem(kind="memory", content=content, label=label,
                              source_ref=f"compaction:{content_hash(shadowed)[:16]}")
        return summary, CompactionRecord(
            items=len(items), tokens_before=tokens_before, tokens_after=summary.tokens,
            label=label, shadowed=shadowed)
