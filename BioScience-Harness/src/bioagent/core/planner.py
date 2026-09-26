"""Planner interface plus a default retrieval-based plan-execute-critique loop.

The interface is deliberately small so that an LLM planner, or an upstream
project's planner, can be substituted without touching the executor. The
default implementation follows the pattern shared by Biomni (generate →
execute → self_critic) and CellAgent (planner/executor/evaluator): retrieve
candidate capabilities from the registry, order them, then let the critique
step decide whether the result is acceptable.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass
class PlanStep:
    """One planned capability invocation."""

    capability: str
    kind: str
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Plan:
    """An ordered sequence of steps addressing a task."""

    task: str
    steps: list[PlanStep] = field(default_factory=list)
    notes: str = ""

    def __len__(self) -> int:
        return len(self.steps)


@dataclass
class Critique:
    """Judgement of an executed plan.

    `accepted` decides whether the runtime stops retrying; `verdict` says
    whether the science holds. Only a validator that compared against a metric,
    threshold, benchmark or ground truth may set ACCEPTED — steps completing is
    not a finding.
    """

    accepted: bool
    reason: str
    retry_hint: str | None = None
    #: None means "this critique did not judge the science"; the report then
    #: falls back to deriving the verdict from `accepted`. The built-in planners
    #: always set it explicitly, so their runs never claim an unearned ACCEPTED.
    verdict: str | None = None


class Planner(abc.ABC):
    """Turns a natural-language task into an ordered plan over the registry."""

    @abc.abstractmethod
    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        ...

    def critique(self, plan: Plan, results: Sequence[Any]) -> Critique:
        """Default critique: accept when every step succeeded."""
        failed = [r for r in results if not getattr(r, "ok", False)]
        if not results:
            return Critique(False, "no steps were executed", verdict="REJECTED")
        if failed:
            names = ", ".join(getattr(r, "capability", "?") for r in failed[:3])
            return Critique(
                False,
                f"{len(failed)}/{len(results)} step(s) failed: {names}",
                retry_hint="drop failing capabilities or select alternatives",
            )
        return Critique(True, f"all {len(results)} step(s) succeeded; no validator "
                              "judged the result, so the finding is not established",
                        verdict="INCONCLUSIVE")


class RetrievalPlanner(Planner):
    """Retrieval-augmented planner: rank catalogue entries against the task.

    This mirrors Biomni's retrieval-based tool selection, but over the merged
    catalogue rather than one project's registry, and it prefers capabilities
    that are locally available so a plan does not silently depend on network
    access or on an uninstalled upstream project.
    """

    #: kinds worth planning over, in preference order
    PREFERRED_KINDS = ("dataset", "tool", "skill", "database")

    def __init__(self, prefer_offline: bool = True, prefer_redistributable: bool = True) -> None:
        self.prefer_offline = prefer_offline
        self.prefer_redistributable = prefer_redistributable

    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        candidates: list[Any] = []
        for kind in self.PREFERRED_KINDS:
            hits = registry.find(task, kind=kind, limit=max_steps * 2)
            candidates.extend(hits)

        def score(cap: Any) -> tuple[int, int, int]:
            offline = 0 if (self.prefer_offline and cap.kind == "dataset") else 1
            redistributable = 0 if (self.prefer_redistributable and cap.redistributable) else 1
            available = 0 if cap.availability in ("already-available", "wrappable") else 1
            return (offline, available, redistributable)

        ordered = sorted(candidates, key=score)
        seen: set[str] = set()
        steps: list[PlanStep] = []
        for cap in ordered:
            if cap.name in seen:
                continue
            seen.add(cap.name)
            steps.append(
                PlanStep(
                    capability=cap.name,
                    kind=cap.kind,
                    rationale=(
                        f"{cap.kind} from {'/'.join(cap.contributing_projects) or 'unknown'}"
                        f" ({cap.integration_mode}, {cap.availability})"
                    ),
                )
            )
            if len(steps) >= max_steps:
                break
        return Plan(
            task=task,
            steps=steps,
            notes=f"selected {len(steps)} of {len(candidates)} candidate capabilities",
        )
