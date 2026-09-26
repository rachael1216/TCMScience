"""Compaction: what is left out is summarised and said.

The compiler filled its budget by rank and discarded the rest, reporting a count. A count
tells the *caller* something was omitted and tells the model — the party that has to answer
around the gap — nothing at all.

The property that matters here is specific to this package:

> **A summary inherits the join of the labels it summarises.**

``DEFAULT_SYSTEM_PROMPT`` already tells the model "a summary of identifiable content is
still identifiable". That has to hold by construction. Deriving a summary's label by
re-classifying the summary *text* would mean a summary of PHI that omitted the identifiers
classified as PUBLIC, and became permitted at a destination its sources could never reach —
laundering by accident rather than by attack.
"""

from __future__ import annotations

import pytest

from psh.context import CompactionRecord, Compactor, ContextCompiler, extractive_summary
from psh.contracts import Autonomy, ContextItem, RiskTier, RunEnvelope
from psh.labels import DataLabel, Destination, Sensitivity


def item(content, sensitivity=Sensitivity.PUBLIC, kind="evidence", ref=""):
    return ContextItem(kind=kind, content=content, label=DataLabel(sensitivity),
                       source_ref=ref)


# ================================================== the labelling property

def test_a_summary_carries_the_join_of_what_it_summarises():
    """Summarising PHI does not produce non-PHI."""
    summary, record = Compactor().compact(
        [item("Patient John Doe, MRN 4472213, had an event.", Sensitivity.PHI, ref="n1"),
         item("A public fact about trial design.", Sensitivity.PUBLIC, ref="n2")],
        budget_tokens=200, destination=Destination.LOCAL_MODEL)

    assert summary.label.sensitivity is Sensitivity.PHI
    assert record.label.sensitivity is Sensitivity.PHI


def test_the_label_comes_from_the_inputs_not_the_summary_text():
    """The laundering case, stated directly.

    The summary of this item contains no identifier — its first sentence is innocuous — so
    re-classifying the summary text would return something far below PHI. The label must
    not be computed that way.
    """
    from psh.kernel.classify import Classifier

    source = item("The cohort was assembled in 2019. Patient John Doe, MRN 4472213.",
                  Sensitivity.PHI, ref="n1")
    summary, _ = Compactor().compact([source], budget_tokens=200)

    assert "MRN" not in summary.content, "the extract happens to omit the identifier"
    assert Classifier().classify(summary.content).label.sensitivity < Sensitivity.PHI, \
        "...so re-classifying the text would under-label it"
    assert summary.label.sensitivity is Sensitivity.PHI, \
        "the label must come from the sources, not from the summary"


def test_a_summary_that_cannot_reach_the_destination_is_withheld():
    """A count carries no content, so it is safe where the material is not."""
    summary, record = Compactor().compact(
        [item("Patient John Doe, MRN 4472213.", Sensitivity.PHI, ref="n1")],
        budget_tokens=200, destination=Destination.PUBLIC_REMOTE)

    assert record.withheld
    assert summary.label.sensitivity is Sensitivity.PUBLIC
    assert "MRN" not in summary.content and "John" not in summary.content
    assert "withheld" in summary.content and "PHI" in summary.content
    assert record.label.sensitivity is Sensitivity.PHI, "the record still knows"


def test_the_shadowed_items_are_recorded_not_deleted():
    items = [item("first thing", ref="a"), item("second thing", ref="b")]
    _, record = Compactor().compact(items, budget_tokens=200)
    assert record.items == 2
    assert set(record.shadowed) == {"a", "b"}


def test_compacting_nothing_produces_nothing():
    summary, record = Compactor().compact([], budget_tokens=100)
    assert summary is None and record.items == 0


# ===================================================== the summariser

def test_the_default_summariser_is_extractive():
    """Verbatim sentences can be wrong only in what they omit."""
    text = extractive_summary(
        [item("Empagliflozin reduced hospitalisation. Secondary endpoints varied.",
              ref="PMID1")], 400)
    assert "Empagliflozin reduced hospitalisation." in text
    assert "Secondary endpoints" not in text
    assert "PMID1" in text


def test_the_summariser_respects_its_allowance():
    items = [item("A sentence that is reasonably long and descriptive. More after it.",
                  ref=f"r{i}") for i in range(40)]
    assert len(extractive_summary(items, 300)) <= 300


def test_a_supplied_summariser_is_used():
    called = {}

    def fake(items, max_chars):
        called["n"] = len(items)
        return "SUMMARY"

    summary, _ = Compactor(summarise=fake).compact(
        [item("a"), item("b")], budget_tokens=200)
    assert called["n"] == 2 and "SUMMARY" in summary.content


# ================================================ wired into the compiler

def _envelope():
    return RunEnvelope(max_label=DataLabel(Sensitivity.PHI), autonomy=Autonomy.ACT,
                       risk=RiskTier.R2_CONSEQUENTIAL)


def test_overflow_is_compacted_rather_than_silently_dropped():
    many = [item(f"Observation number {i} with some accompanying detail text.",
                 ref=f"obs{i}") for i in range(40)]
    compiler = ContextCompiler()
    projection = compiler.compile(items=many, envelope=_envelope(), token_budget=120,
                                  destination=Destination.LOCAL_MODEL)

    assert compiler.last_trace.dropped_budget > 0, "the test needs actual overflow"
    assert compiler.last_trace.compacted > 0
    assert compiler.last_compaction is not None
    rendered = projection.render()
    assert "[compaction]" in rendered, "the model was not told anything was omitted"


def test_the_projection_label_still_covers_compacted_material():
    """The join must survive compaction, or the gateway would under-judge the projection."""
    items = [item(f"public detail {i}", Sensitivity.PUBLIC, ref=f"p{i}")
             for i in range(30)]
    items.append(item("Patient John Doe MRN 4472213 note.", Sensitivity.PHI, ref="phi"))
    compiler = ContextCompiler()
    projection = compiler.compile(items=items, envelope=_envelope(), token_budget=60,
                                  destination=Destination.LOCAL_MODEL)
    assert projection.label.sensitivity is Sensitivity.PHI


def test_compaction_does_not_fire_when_everything_fits():
    compiler = ContextCompiler()
    compiler.compile(items=[item("short")], envelope=_envelope(), token_budget=5000,
                     destination=Destination.LOCAL_MODEL)
    assert compiler.last_trace.compacted == 0
    assert compiler.last_compaction is None


def test_compaction_can_be_switched_off():
    """Off means the previous behaviour, not a different one."""

    class NoCompaction:
        def compact(self, items, **kw):
            return None, CompactionRecord()

    many = [item(f"Observation {i} with detail.", ref=f"o{i}") for i in range(40)]
    compiler = ContextCompiler(compactor=NoCompaction())
    projection = compiler.compile(items=many, envelope=_envelope(), token_budget=120,
                                  destination=Destination.LOCAL_MODEL)
    assert "[compaction]" not in projection.render()
    assert compiler.last_trace.dropped_budget > 0


def test_a_load_bearing_item_is_never_compacted_away():
    """The turn and the instructions are kept whatever the budget says."""
    items = [ContextItem(kind="turn", content="the actual question being asked"),
             ContextItem(kind="instruction", content="the system prompt")]
    items += [item(f"filler {i} with text", ref=f"f{i}") for i in range(40)]
    projection = ContextCompiler().compile(items=items, envelope=_envelope(),
                                           token_budget=40,
                                           destination=Destination.LOCAL_MODEL)
    rendered = projection.render()
    assert "the actual question being asked" in rendered
    assert "the system prompt" in rendered
