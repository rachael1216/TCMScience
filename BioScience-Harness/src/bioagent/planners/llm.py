"""LLM-backed planner.

The v1 report noted the planner was retrieval + heuristics with no live model.
This plugin calls a model to order and justify the retrieved component set. The
model client is injected, so the runtime has no vendor dependency and the planner
degrades to heuristic ordering when no client is supplied.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .base import Plan, PlannerPlugin, PlanStep, register_planner
from .heuristic import HeuristicPlanner


@register_planner
class LLMPlanner(PlannerPlugin):
    """Asks a model which retrieved components to use, and in what order."""

    name = "llm"

    def __init__(self, client: Callable[[str], str] | None = None, model: str = "",
                 fallback: PlannerPlugin | None = None) -> None:
        self._client = client
        self.model = model
        self.fallback = fallback or HeuristicPlanner()

    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        candidates = registry.search(task, limit=max_steps * 6)
        if not candidates:
            return Plan(task=task, steps=[], planner=self.name, notes="no candidates retrieved")
        if self._client is None:
            p = self.fallback.plan(task, registry, max_steps=max_steps)
            p.planner = f"{self.name}->{self.fallback.name}"
            p.notes += " (no model client bound; fell back to heuristic ordering)"
            return p

        menu = [{"id": m.id, "kind": m.kind, "backend": m.runtime.backend,
                 "state": m.state.value, "description": m.description[:160]}
                for m in candidates]
        prompt = (
            "You are planning a biomedical analysis. Choose the components to call, in order.\n"
            f"TASK: {task}\n\n"
            f"AVAILABLE COMPONENTS (JSON):\n{json.dumps(menu, indent=1)[:6000]}\n\n"
            "Rules: prefer components whose state is READY or RESOLVED; a backend of 'none' "
            "cannot execute. Reply with ONLY a JSON array of objects "
            '[{"id": "...", "rationale": "..."}] '
            f"with at most {max_steps} entries."
        )
        try:
            raw = self._client(prompt)
            payload = self._extract_json(raw)
            valid = {m.id: m for m in candidates}
            steps = [PlanStep(component_id=item["id"], kind=valid[item["id"]].kind,
                              rationale=str(item.get("rationale", ""))[:200])
                     for item in payload if isinstance(item, dict) and item.get("id") in valid]
            if not steps:
                raise ValueError("model returned no valid component ids")
            return Plan(task=task, steps=steps[:max_steps], planner=self.name,
                        notes=f"model={self.model or 'unspecified'}; "
                              f"chose {len(steps)} of {len(candidates)} candidates")
        except Exception as exc:  # noqa: BLE001 - planning must not crash the run
            p = self.fallback.plan(task, registry, max_steps=max_steps)
            p.planner = f"{self.name}->{self.fallback.name}"
            p.notes += f" (model planning failed: {type(exc).__name__}: {exc}; used heuristic)"
            return p

    @staticmethod
    def _extract_json(text: str) -> list:
        t = text.strip()
        start, end = t.find("["), t.rfind("]")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise ValueError("no JSON array found in model response")
