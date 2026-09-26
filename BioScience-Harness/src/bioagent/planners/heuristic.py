"""Retrieval + heuristic planner — deterministic, no model required."""

from __future__ import annotations

from typing import Any

from .base import Plan, PlannerPlugin, PlanStep, register_planner


@register_planner
class HeuristicPlanner(PlannerPlugin):
    """Ranks components by retrieval score, preferring ones that can actually run."""

    name = "heuristic"
    PREFERRED_KINDS = ("dataset", "tool", "database", "skill")

    def __init__(self, prefer_ready: bool = True, prefer_offline: bool = True,
                 backends: Any = None, exclude_unavailable: bool = True) -> None:
        self.prefer_ready = prefer_ready
        self.prefer_offline = prefer_offline
        self.backends = backends
        self.exclude_unavailable = exclude_unavailable

    def _runnable(self, m: Any) -> bool:
        """Whether the component's mechanism can execute in this runtime."""
        if m.runtime.backend == "none":
            return False
        if self.backends is None:
            return True
        b = self.backends.for_component(m)
        return bool(b is not None and b.available())

    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        from ..status import LifecycleState

        candidates: list[Any] = []
        for kind in self.PREFERRED_KINDS:
            candidates.extend(registry.search(task, kind=kind, limit=max_steps * 3))

        def score(m: Any) -> tuple[int, int, int]:
            ready = 0 if (self.prefer_ready and m.state in
                          (LifecycleState.READY, LifecycleState.RESOLVED)) else 1
            runnable = 0 if self._runnable(m) else 1
            offline = 0 if (self.prefer_offline and m.offline_capable) else 1
            return (runnable, ready, offline)

        if self.exclude_unavailable and self.backends is not None:
            # Never plan a step that is known to be unexecutable here when any
            # executable candidate exists; fall back to ranking only if none does.
            runnable = [m for m in candidates if self._runnable(m)]
            if runnable:
                candidates = runnable

        seen: set[str] = set()
        steps: list[PlanStep] = []
        for m in sorted(candidates, key=score):
            if m.id in seen:
                continue
            seen.add(m.id)
            steps.append(PlanStep(
                component_id=m.id, kind=m.kind,
                rationale=(f"{m.kind} via {m.runtime.backend} backend "
                           f"({m.provider.project or 'unknown'}, state={m.state.value})")))
            if len(steps) >= max_steps:
                break
        return Plan(task=task, steps=steps, planner=self.name,
                    notes=f"selected {len(steps)} of {len(candidates)} candidates")
