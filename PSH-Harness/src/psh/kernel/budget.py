"""Budget governor: soft limits warn, hard limits stop.

The review's framing, carried over from the predecessor and extended to delegation: a
budget that only reports consumption is a metric; one that stops the run is a control.
Delegation budgets are strictly nested, so fanning out cannot multiply a ceiling.

Nested is not the same as *summed*, and the 2026-09-18 review showed the difference. Every
task the loop executes runs under an envelope minted by ``restrict``, which carries a new
``run_id`` and the parent's ``run_id`` as ``parent_run_id``; the governor kept one ledger
per ``run_id`` and charged each call to the envelope that made it. So two tasks under a
$1.00 run, each spending $0.60, were each within their own $1.00 child ceiling while the run
that owned them read $0.00 spent and $1.20 was gone. A ceiling on a run that never sees its
own tasks' consumption is a ceiling on nothing.

Consumption is now charged to the run and to every ancestor it names, and a call is refused
when it would breach the ceiling at *any* level. A run's snapshot is what the run and its
descendants have spent together; the governor-wide aggregate sums the roots so nothing is
counted twice.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..contracts import Budget, BudgetExhausted, RunEnvelope

__all__ = ["BudgetSnapshot", "BudgetGovernor", "BudgetState"]


@dataclass
class BudgetState:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    delegations: int = 0
    started_at: float = field(default_factory=time.time)
    warned: set = field(default_factory=set)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd": round(self.usd, 4),
                "model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "delegations": self.delegations,
                "elapsed_minutes": round(self.elapsed / 60, 2)}

    def freeze(self) -> "BudgetSnapshot":
        return BudgetSnapshot(
            input_tokens=self.input_tokens, output_tokens=self.output_tokens, usd=self.usd,
            model_calls=self.model_calls, tool_calls=self.tool_calls,
            delegations=self.delegations, started_at=self.started_at,
            warned=frozenset(self.warned))


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """A read-only view of consumption.

    The governor's live counters were reachable as a public mutable attribute; a caller
    could zero them. Nothing outside the governor now sees the live object — only this
    frozen copy. (Frozen dataclasses are advisory against object.__setattr__, which is the
    documented in-process limit; this closes the ordinary path, not the adversarial one.)
    """

    input_tokens: int
    output_tokens: int
    usd: float
    model_calls: int
    tool_calls: int
    delegations: int
    started_at: float
    warned: frozenset

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd": round(self.usd, 4),
                "model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "delegations": self.delegations,
                "elapsed_minutes": round(self.elapsed / 60, 2)}


#: One level of the lineage a call is charged to: the run, its ledger, its ceiling.
_Ledger = tuple[str, BudgetState, Budget]


class BudgetGovernor:
    """Tracks consumption per run — and per run *tree* — and enforces its ceilings."""

    def __init__(self, budget: Budget | None = None, *,
                 on_warning: Callable[[str], None] | None = None,
                 audit: Callable[..., Any] | None = None) -> None:
        self.budget = budget or Budget()
        self._on_warning = on_warning
        self._audit = audit
        self._states: dict[str, BudgetState] = {}
        #: ``run_id -> parent_run_id``, learned from every envelope that passes through.
        #: The lineage is what lets a task's consumption reach the run that owns it.
        self._parents: dict[str, str] = {}
        #: ``run_id -> Budget``: the ceiling each run stated when it was first seen, so an
        #: ancestor's ceiling can be enforced from a descendant's envelope.
        self._budgets: dict[str, Budget] = {}
        #: Every check is a compare-then-increment on shared state. Under CPython's GIL the
        #: two happen to sit close enough that forty trials at the tightest switch interval
        #: could not race them — and a ceiling that holds because of scheduler timing is not
        #: a ceiling. Free-threaded builds exist, and a worker pool drives this from several
        #: threads. Re-entrant, because ``_enforce`` runs inside the checks.
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- lineage
    def _state(self, envelope: RunEnvelope) -> BudgetState:
        """The live, mutable counters for a run. Private: callers get snapshot().

        Seeing an envelope registers its lineage and its ceiling; nothing else has to
        remember to.
        """
        run_id = envelope.run_id
        state = self._states.get(run_id)
        if state is None:
            state = self._states[run_id] = BudgetState()
        self._budgets.setdefault(run_id, self._budget_for(envelope))
        parent = getattr(envelope, "parent_run_id", None)
        if parent and parent != run_id and run_id not in self._parents:
            self._parents[run_id] = parent
        return state

    def adopt(self, envelope: RunEnvelope) -> None:
        """Register a run, its ceiling and its parent before it consumes anything."""
        with self._lock:
            self._state(envelope)

    def lineage(self, run_id: str) -> tuple[str, ...]:
        """``run_id``, its parent, its grandparent ... as far as the governor has seen."""
        out: list[str] = []
        seen: set[str] = set()
        current: str | None = run_id
        while current and current not in seen:
            out.append(current)
            seen.add(current)
            current = self._parents.get(current)
        return tuple(out)

    def _ledgers(self, envelope: RunEnvelope) -> list[_Ledger]:
        """The run's ledger first, then each ancestor's, each with its own ceiling.

        An ancestor the governor never saw directly (a parent that made no call of its
        own before delegating) still gets a ledger, under the governor's default ceiling
        until its envelope is seen.
        """
        rows: list[_Ledger] = [(envelope.run_id, self._state(envelope),
                                self._budget_for(envelope))]
        for run_id in self.lineage(envelope.run_id)[1:]:
            state = self._states.get(run_id)
            if state is None:
                state = self._states[run_id] = BudgetState()
            rows.append((run_id, state, self._budgets.get(run_id, self.budget)))
        return rows

    # --------------------------------------------------------------- snapshots
    def snapshot(self, envelope: RunEnvelope | None = None) -> "BudgetSnapshot":
        """Read-only consumption for one run and its descendants, or for every root.

        The live BudgetState used to be reachable as ``governor.state(envelope)``, which
        returned the mutable object itself — a caller could zero the counters. Only frozen
        copies leave the governor now. A run's figure includes what its tasks and its
        delegated children spent, because that is what the run spent.
        """
        with self._lock:
            if envelope is not None:
                return self._state(envelope).freeze()
            agg = BudgetState()
            for run_id, st in self._states.items():
                if run_id in self._parents:
                    continue                      # counted through its root
                agg.input_tokens += st.input_tokens
                agg.output_tokens += st.output_tokens
                agg.usd += st.usd
                agg.model_calls += st.model_calls
                agg.tool_calls += st.tool_calls
                agg.delegations += st.delegations
                agg.started_at = min(agg.started_at, st.started_at)
            return agg.freeze()

    def state(self, envelope: RunEnvelope) -> "BudgetSnapshot":
        """Backward-compatible name; returns the frozen snapshot, never the live object."""
        return self.snapshot(envelope)

    def _budget_for(self, envelope: RunEnvelope) -> Budget:
        return envelope.budget or self.budget

    def _warn(self, envelope: RunEnvelope, state: BudgetState, run_id: str, key: str,
              message: str) -> None:
        if key in state.warned:
            return
        state.warned.add(key)
        if self._on_warning is not None:
            self._on_warning(message)
        if self._audit is not None:
            self._audit("budget_warning", run_id=envelope.run_id,
                        detail={"limit": key, "ceiling_of": run_id})

    # ---------------------------------------------------------------- restoring
    def restore(self, envelope: RunEnvelope, usage: Mapping[str, Any]) -> None:
        """Carry a checkpoint's recorded consumption into a resumed run.

        A restart used to begin at zero: whatever the run had spent before the checkpoint
        was spent again, against a fresh ceiling. The counters become at least what the
        record says, the clock continues from where it was, and if the record already
        stood at a hard ceiling the resume fails here rather than one call later.
        """
        with self._lock:
            state = self._state(envelope)
            for name in ("input_tokens", "output_tokens", "model_calls", "tool_calls",
                         "delegations"):
                recorded = usage.get(name)
                if recorded is not None:
                    setattr(state, name, max(getattr(state, name), int(recorded)))
            if usage.get("usd") is not None:
                state.usd = max(state.usd, float(usage["usd"]))
            minutes = usage.get("elapsed_minutes")
            if minutes:
                state.started_at = min(state.started_at, time.time() - float(minutes) * 60)
            self._enforce(envelope)

    # ---------------------------------------------------------------- recording
    def record_model_usage(self, envelope: RunEnvelope, in_tokens: int, out_tokens: int,
                           usd: float = 0.0) -> None:
        with self._lock:
            for _, state, _ in self._ledgers(envelope):
                state.input_tokens += in_tokens
                state.output_tokens += out_tokens
                state.usd += usd
            self._enforce(envelope)

    # ----------------------------------------------------------------- checking
    def peek_model_call(self, envelope: RunEnvelope) -> None:
        """Raise if the next model call would exceed a ceiling, consuming nothing.

        The loop's bounds check used ``check_model_call`` as its pre-check, and that call
        counts: a two-step plan of pure tool calls under ``max_model_calls=1`` stopped after
        its first tool with zero model calls made. Reading a ceiling and reserving against
        it are different operations, and only the broker reserves.
        """
        with self._lock:
            for run_id, state, budget in self._ledgers(envelope):
                if state.model_calls >= budget.max_model_calls:
                    raise BudgetExhausted(
                        f"model-call ceiling reached ({budget.max_model_calls}) for run "
                        f"{run_id}; run stopped")
            self._enforce(envelope)

    def check_model_call(self, envelope: RunEnvelope) -> None:
        with self._lock:
            rows = self._ledgers(envelope)
            for run_id, state, budget in rows:
                if state.model_calls >= budget.max_model_calls:
                    raise BudgetExhausted(
                        f"model-call ceiling reached ({budget.max_model_calls}) for run "
                        f"{run_id}; run stopped")
            for _, state, _ in rows:
                state.model_calls += 1
            self._enforce(envelope)

    def check_tool_call(self, envelope: RunEnvelope) -> None:
        with self._lock:
            rows = self._ledgers(envelope)
            for run_id, state, budget in rows:
                if state.tool_calls >= budget.max_tool_calls:
                    raise BudgetExhausted(
                        f"tool-call ceiling reached ({budget.max_tool_calls}) for run "
                        f"{run_id}; run stopped")
            for _, state, _ in rows:
                state.tool_calls += 1
            self._enforce(envelope)

    def check_delegation(self, envelope: RunEnvelope) -> None:
        with self._lock:
            rows = self._ledgers(envelope)
            for run_id, state, budget in rows:
                if state.delegations >= budget.max_delegations:
                    raise BudgetExhausted(
                        f"delegation ceiling reached ({budget.max_delegations}) for run "
                        f"{run_id}; run stopped")
            for _, state, _ in rows:
                state.delegations += 1

    def _enforce(self, envelope: RunEnvelope) -> None:
        for run_id, state, budget in self._ledgers(envelope):
            where = "" if run_id == envelope.run_id else f" for run {run_id}"
            if state.tokens >= budget.tokens_hard:
                raise BudgetExhausted(
                    f"hard token ceiling reached ({state.tokens} >= {budget.tokens_hard})"
                    f"{where}; run stopped")
            if state.usd >= budget.usd_hard:
                raise BudgetExhausted(
                    f"hard cost ceiling reached (${state.usd:.2f} >= ${budget.usd_hard:.2f})"
                    f"{where}")
            if state.elapsed >= budget.seconds_hard:
                raise BudgetExhausted(
                    f"hard time ceiling reached ({state.elapsed/60:.1f} min){where}")
            if state.tokens >= budget.tokens_soft:
                self._warn(envelope, state, run_id, "tokens",
                           f"soft token budget passed ({state.tokens}/{budget.tokens_soft})"
                           f"{where}")
            if state.usd >= budget.usd_soft:
                self._warn(envelope, state, run_id, "usd",
                           f"soft cost budget passed (${state.usd:.2f}/${budget.usd_soft:.2f})"
                           f"{where}")
            if state.elapsed >= budget.seconds_soft:
                self._warn(envelope, state, run_id, "time",
                           f"soft time budget passed ({state.elapsed/60:.0f} min){where}")
        if envelope.expired:
            raise BudgetExhausted("run deadline passed")

    def remaining(self, envelope: RunEnvelope) -> dict[str, Any]:
        """What the next call may still spend: the tightest level of the lineage."""
        with self._lock:
            rows = self._ledgers(envelope)
            return {
                "tokens": min(max(0, b.tokens_hard - s.tokens) for _, s, b in rows),
                "usd": round(min(max(0.0, b.usd_hard - s.usd) for _, s, b in rows), 4),
                "minutes": round(min(max(0.0, b.seconds_hard - s.elapsed)
                                     for _, s, b in rows) / 60, 1),
                "model_calls": min(max(0, b.max_model_calls - s.model_calls)
                                   for _, s, b in rows),
                "tool_calls": min(max(0, b.max_tool_calls - s.tool_calls)
                                  for _, s, b in rows)}
