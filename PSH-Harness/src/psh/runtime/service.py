"""The application-facing entrance: plan, execute, verify, release, commit.

``AgentLoopController`` returns an internal ``LoopResult``; ``Runner`` returns a
``RunResult`` whose ``released_output`` has been through the gate. An application that
called the loop directly got the raw graph. ``ResearchRunService`` is the one door for
multi-step work: it runs the loop and hands the result to the ``Finalizer``, so every
deliverable — a fresh run, a resumed one, a delegated tree's root — leaves through the
same quarantine, verification and release gate.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..contracts import RunEnvelope
from .finalize import Finalizer, ReleasedResult
from .loop import AgentLoopController, LoopLimits, LoopResult, Planner

__all__ = ["ResearchRunService"]


class ResearchRunService:
    """Runs an objective through the governed loop and releases only what the gate passes."""

    def __init__(self, kernel: Any, *, planner: Planner, registry: Any = None,
                 model: Any = None, model_invoke: Callable[[str], str] | None = None,
                 evaluator: Any = None, validator: Any = None,
                 limits: LoopLimits | None = None,
                 delegate_backend: Callable[[Any], Any] | None = None,
                 checkpoints: Any = None, memory: Any = None, operations: Any = None,
                 policy: Any = None) -> None:
        self.kernel = kernel
        self.policy = policy if policy is not None else kernel.policy
        if policy is not None and hasattr(kernel, "check_requirements"):
            kernel.check_requirements(self.policy)
        self.planner = planner
        self.registry = registry
        self.model = model
        self.model_invoke = model_invoke
        self.evaluator = evaluator
        self.validator = validator
        self.limits = limits
        self.delegate_backend = delegate_backend
        self.checkpoints = checkpoints
        self.memory = memory
        self.operations = operations
        self.finalizer = Finalizer(kernel, policy=self.policy)
        #: The last internal result, for diagnostics. Not part of what is released.
        self.last_loop_result: LoopResult | None = None

    def _loop(self) -> AgentLoopController:
        kw: dict[str, Any] = dict(
            planner=self.planner, registry=self.registry, model=self.model,
            model_invoke=self.model_invoke, evaluator=self.evaluator,
            validator=self.validator, limits=self.limits,
            delegate_backend=self.delegate_backend, checkpoints=self.checkpoints,
            memory=self.memory, operations=self.operations, sleep=lambda s: None)
        return AgentLoopController(self.kernel, **kw)

    def run(self, objective: str, *, envelope: RunEnvelope | None = None,
            sources: Mapping[str, Any] | None = None, project_id: str = "",
            supports: Sequence[Any] = (), objective_label: Any = None,
            require_goal_verification: bool | None = None) -> ReleasedResult:
        envelope = envelope or self.policy.envelope(project_id=project_id)
        result = self._loop().run(objective, envelope, supports=supports,
                                  objective_label=objective_label)
        self.last_loop_result = result
        return self.finalizer.finalize(
            result, envelope, sources=sources, project_id=project_id or envelope.project_id,
            require_goal_verification=require_goal_verification)

    def resume(self, checkpoint: Any, *, sources: Mapping[str, Any] | None = None,
               project_id: str = "", objective: str | None = None, plan: Any = None,
               require_goal_verification: bool | None = None) -> ReleasedResult:
        """Continue a checkpointed loop under today's authority, then release the same way."""
        from .checkpoint import resume

        state = resume(checkpoint, self.kernel, policy=self.policy, objective=objective,
                       plan=plan)
        result = self._loop().run(state.objective, state.envelope, resume_from=state)
        self.last_loop_result = result
        return self.finalizer.finalize(
            result, state.envelope, sources=sources,
            project_id=project_id or state.envelope.project_id,
            require_goal_verification=require_goal_verification)
