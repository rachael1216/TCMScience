"""Planner plugin interface and registry.

v1 hardcoded a retrieval planner inside the agent, so swapping in an LLM planner
meant editing the agent. Here planners register themselves and an AgentSpec names
one, so the runtime never imports a specific planner.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class PlanStep:
    """One planned component invocation."""

    component_id: str
    kind: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Plan:
    task: str
    steps: list[PlanStep] = field(default_factory=list)
    planner: str = ""
    notes: str = ""

    def __len__(self) -> int:
        return len(self.steps)


@dataclass
class Critique:
    """A planner's judgement of a run.

    `accepted` answers "should the runtime stop retrying?"; `verdict` answers
    "does the science hold?". They are not the same question, and conflating
    them is how "both tools returned 200" became a scientific result. A planner
    that only watched steps execute must leave `verdict` as INCONCLUSIVE; only a
    validator that compared against a metric, threshold, benchmark or ground
    truth may raise it to ACCEPTED.
    """

    accepted: bool
    reason: str
    retry_hint: str | None = None
    #: None means "this critique did not judge the science"; the report then
    #: falls back to deriving the verdict from `accepted`. The built-in planners
    #: always set it explicitly, so their runs never claim an unearned ACCEPTED.
    verdict: str | None = None


class PlannerPlugin(abc.ABC):
    """Turns a task into an ordered plan over the component registry."""

    #: registry key used by AgentSpec.planner
    name: str = "planner"

    @abc.abstractmethod
    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        ...

    def critique(self, plan: Plan, results: Sequence[Any]) -> Critique:
        """Default: accept only if something executed and nothing failed."""
        if not results:
            return Critique(False, "no steps were executed", verdict="REJECTED")
        executed = [r for r in results if getattr(r, "executed", False)]
        failed = [r for r in results if not getattr(r, "status", None) or
                  not getattr(r.status, "successful", False)]
        if not executed:
            return Critique(False, "no step actually executed (all merely resolved)",
                            retry_hint="bind a dispatcher or choose executable components",
                            verdict="REJECTED")
        if failed:
            names = ", ".join(getattr(r, "capability", "?") for r in failed[:3])
            return Critique(False, f"{len(failed)}/{len(results)} step(s) did not succeed: {names}",
                            retry_hint="drop failing components or pick alternatives",
                            verdict="REJECTED")
        # Execution is complete and nothing judged the result. That is
        # INCONCLUSIVE, not ACCEPTED: a statistical test can run cleanly and
        # return p = 0.83, and a model can return an accuracy of 0.30. Both are
        # successful executions of a negative result.
        return Critique(True, f"all {len(results)} step(s) succeeded; no validator "
                              "judged the result, so the finding is not established",
                        verdict="INCONCLUSIVE")


def merge_step_arguments(step: Any, step_kwargs: dict | None) -> dict:
    """Combine run-wide kwargs with a plan step's own arguments.

    `PlanStep.arguments` existed on both planner interfaces and no execution path
    ever read it, so every REST path, query parameter and tool argument a planner
    produced was silently dropped and the backend was called with the run-wide
    defaults alone. A planner could "plan" a call to `/lookup/id/ENSG…` and the
    runtime would invoke the component with nothing.

    Step arguments win over the run-wide defaults: they are the specific decision
    the planner made for this step, and a global default must not overwrite it.
    """
    merged = dict(step_kwargs or {})
    merged.update(getattr(step, "arguments", None) or {})
    return merged


PLANNER_REGISTRY: dict[str, type[PlannerPlugin]] = {}


def register_planner(cls: type[PlannerPlugin]) -> type[PlannerPlugin]:
    PLANNER_REGISTRY[cls.name] = cls
    return cls


def get_planner(name: str, **kwargs: Any) -> PlannerPlugin:
    if name not in PLANNER_REGISTRY:
        raise KeyError(f"unknown planner {name!r}; registered: {sorted(PLANNER_REGISTRY)}")
    return PLANNER_REGISTRY[name](**kwargs)
