"""Checkpoint and resume, with one rule that makes it safe.

``Runner``'s ``checkpoint`` stage wrote an audit event and nothing else, so "checkpoint"
named a record of having finished rather than a state a run could be continued from. This
is the real thing, and it is short, because ``LoopState`` was written to be the object a
checkpoint copies.

The rule is the whole design:

> **A resumed run re-meets its authority against the policy in force *now*.**

Restoring the envelope a run held is the obvious implementation and it is a hole. An
envelope is a grant, and a grant that outlives the policy that issued it is a capability
the kernel did not agree to — exactly the P0-1 defect, arriving through a file instead of a
keyword argument. A run checkpointed under a broad policy, resumed after the kernel's
policy narrows, would hold the old authority and every gateway would honour it, because a
gateway checks the envelope it is handed.

So resuming computes ``AuthorityLattice.meet(stored, current_ceiling)``. The resumed run is
never wider than the stored one and never wider than the policy in force, whichever is
narrower. Any dimension that was narrowed is named in an audit event, because a run that
quietly loses authority mid-flight is as confusing as one that quietly gains it.

Two further things follow, and both are refusals rather than best-effort continuations:

* **The record is verified before it is trusted.** A checkpoint carries a content hash and
  is refused if it does not match. Same posture as the event chain: tamper-evident, not
  tamper-proof.
* **Remaining work is re-authorised, one task at a time.** If the narrowed envelope no
  longer admits a task that has not run, the resume is refused and names it. Completed
  tasks are history and are not re-checked; a task that has not run yet needs authority it
  may no longer have.

And one rule about contents, because a checkpoint is a durable write:

* **A result is stored only where the persistence rules would store it.** Each succeeded
  task's result carries the label the broker gave it. A result above the persistence
  ceiling, one whose label does not permit ``PERSISTENT``, or any result of a run whose
  envelope does not permit ``PERSISTENT`` at all, is *withheld*: the record says so, and
  on resume the task runs again. The labels are stored with the results and restored with
  them, joined with a fresh classification, so a resumed graph starts from what the
  original knew and never from PUBLIC. A record without a hash is refused outright — "it
  was never hashed" is not a weaker form of "it verifies".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import (
    Autonomy, Budget, PolicyDenied, Principal, RiskTier, RunEnvelope, content_hash, new_id,
)
from ..labels import combine, DataLabel, Destination, Sensitivity
from .execgraph import ExecutionGraph, TaskState
from .loop import LoopState
from .plan import Plan
from .plan_validator import task_envelope

__all__ = ["Checkpoint", "CheckpointStore", "ResumeRefused", "capture", "resume"]


def _label_to_dict(label: Any) -> dict[str, Any] | None:
    if label is None:
        return None
    return {"sensitivity": label.sensitivity.name, "categories": list(label.categories),
            "shareable": bool(getattr(label, "shareable", True))}


def _label_from_dict(data: Mapping[str, Any] | None) -> DataLabel | None:
    if not data:
        return None
    sensitivity = Sensitivity[data["sensitivity"]]
    # ``shareable`` is data from a file; at SENSITIVE and above it is never honoured,
    # because ``DataLabel`` refuses that combination and a refusal here should be a
    # resume decision, not a constructor error.
    shareable = bool(data.get("shareable", False)) and sensitivity < Sensitivity.SENSITIVE
    return DataLabel(sensitivity, categories=tuple(data.get("categories") or ()),
                     rationale="restored from a checkpoint", shareable=shareable)


class ResumeRefused(PolicyDenied):
    """A checkpoint could not be resumed. Says which of the three reasons applies."""


# --------------------------------------------------------------- the record

def _envelope_to_dict(envelope: RunEnvelope) -> dict[str, Any]:
    return {
        "run_id": envelope.run_id, "task_id": envelope.task_id,
        "project_id": envelope.project_id,
        "principal": {"id": envelope.principal.id, "kind": envelope.principal.kind,
                      "display_name": envelope.principal.display_name},
        "risk": envelope.risk.name, "autonomy": envelope.autonomy.value,
        "max_label": envelope.max_label.sensitivity.name,
        "allowed_destinations": sorted(d.name for d in envelope.allowed_destinations),
        "allowed_capabilities": list(envelope.allowed_capabilities),
        "denied_capabilities": list(envelope.denied_capabilities),
        "require_isolated_tools": envelope.require_isolated_tools,
        "allowed_integration_modes": list(envelope.allowed_integration_modes),
        "allowed_license_classes": list(envelope.allowed_license_classes),
        "budget": {f: getattr(envelope.budget, f) for f in (
            "tokens_soft", "tokens_hard", "usd_soft", "usd_hard", "seconds_soft",
            "seconds_hard", "max_model_calls", "max_tool_calls", "max_delegations")},
        "deadline": envelope.deadline, "profile": envelope.profile,
        "parent_run_id": envelope.parent_run_id,
    }


def _envelope_from_dict(data: Mapping[str, Any]) -> RunEnvelope:
    principal = data.get("principal") or {}
    return RunEnvelope(
        run_id=data["run_id"], task_id=data.get("task_id", ""),
        project_id=data.get("project_id", ""),
        principal=Principal(id=principal.get("id", "local"),
                            kind=principal.get("kind", "human"),
                            display_name=principal.get("display_name", "")),
        risk=RiskTier[data["risk"]], autonomy=Autonomy(data["autonomy"]),
        max_label=DataLabel(Sensitivity[data["max_label"]]),
        allowed_destinations=frozenset(Destination[d]
                                       for d in data["allowed_destinations"]),
        allowed_capabilities=tuple(data.get("allowed_capabilities") or ()),
        denied_capabilities=tuple(data.get("denied_capabilities") or ()),
        require_isolated_tools=bool(data.get("require_isolated_tools")),
        allowed_integration_modes=tuple(data.get("allowed_integration_modes") or ()),
        allowed_license_classes=tuple(data.get("allowed_license_classes") or ()),
        budget=Budget(**(data.get("budget") or {})),
        deadline=data.get("deadline"), profile=data.get("profile", "default"),
        parent_run_id=data.get("parent_run_id"))


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Everything needed to continue a loop, plus the hash that proves it is intact."""

    loop_id: str
    run_id: str
    objective: str
    envelope: Mapping[str, Any]
    plan: Mapping[str, Any]
    task_states: Mapping[str, Mapping[str, Any]]
    results: Mapping[str, Any]
    iteration: int = 0
    replans: int = 0
    digests: tuple[str, ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)
    #: Per-result labels, keyed like ``results``. A result without one is classified on
    #: resume rather than assumed PUBLIC.
    labels: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Succeeded tasks whose result was NOT written, and why. They run again on resume.
    withheld: Mapping[str, str] = field(default_factory=dict)
    objective_label: Mapping[str, Any] | None = None
    plan_label: Mapping[str, Any] | None = None
    #: True when the run may not persist (no ``PERSISTENT`` destination) or the task text
    #: is above the store's ceiling: the objective, every task's objective and payload,
    #: the criteria and the error texts were withheld, and only the plan's shape — ids,
    #: kinds, components, dependencies — was written. Resuming such a record needs the
    #: caller to supply the objective and the plan again.
    redacted: bool = False
    #: The run's consumed budget at capture time, restored into the governor on resume so
    #: a restart does not reset what was already spent.
    usage: Mapping[str, Any] = field(default_factory=dict)
    checkpoint_id: str = field(default_factory=lambda: new_id("ckpt"))
    created_at: float = field(default_factory=time.time)
    hash: str = ""

    def __post_init__(self) -> None:
        if not self.hash:
            object.__setattr__(self, "hash", self.compute_hash())

    def _body(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id, "loop_id": self.loop_id,
                "run_id": self.run_id, "objective": self.objective,
                "envelope": dict(self.envelope), "plan": dict(self.plan),
                "task_states": {k: dict(v) for k, v in self.task_states.items()},
                "results": dict(self.results), "iteration": self.iteration,
                "replans": self.replans, "digests": list(self.digests),
                "policy": dict(self.policy),
                "labels": {k: dict(v) for k, v in self.labels.items()},
                "withheld": dict(self.withheld),
                "objective_label": (dict(self.objective_label)
                                    if self.objective_label else None),
                "plan_label": dict(self.plan_label) if self.plan_label else None,
                "redacted": self.redacted, "usage": dict(self.usage),
                "created_at": self.created_at}

    def compute_hash(self) -> str:
        return content_hash(self._body())

    @property
    def intact(self) -> bool:
        return self.hash == self.compute_hash()

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "hash": self.hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Checkpoint":
        # A record with no hash is not one that "cannot be checked"; it is one nobody
        # wrote through this code, and constructing it would hash it for them. An editor
        # who deletes the key must not thereby obtain "intact".
        stored = data.get("hash") or ""
        if not stored:
            raise ResumeRefused(
                f"checkpoint {data.get('checkpoint_id', '?')!r} carries no hash; a record "
                "that was never hashed cannot be verified and may not be resumed from")
        return cls(
            loop_id=data["loop_id"], run_id=data["run_id"],
            objective=data.get("objective", ""), envelope=data["envelope"],
            plan=data["plan"], task_states=data.get("task_states") or {},
            results=data.get("results") or {}, iteration=int(data.get("iteration") or 0),
            replans=int(data.get("replans") or 0),
            digests=tuple(data.get("digests") or ()), policy=data.get("policy") or {},
            labels=data.get("labels") or {}, withheld=data.get("withheld") or {},
            objective_label=data.get("objective_label") or None,
            plan_label=data.get("plan_label") or None,
            redacted=bool(data.get("redacted", False)), usage=data.get("usage") or {},
            checkpoint_id=data.get("checkpoint_id") or new_id("ckpt"),
            created_at=float(data.get("created_at") or time.time()),
            hash=stored)


class CheckpointStore:
    """Content-hashed checkpoints on disk. One file per checkpoint, newest last."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, checkpoint_id: str) -> Path:
        return self.root / f"{checkpoint_id}.json"

    def save(self, checkpoint: Checkpoint) -> Path:
        path = self.path_for(checkpoint.checkpoint_id)
        # Written whole then moved, so a crash mid-write leaves the previous checkpoint
        # rather than a truncated one that would fail its own hash check on resume.
        temporary = path.with_suffix(".json.partial")
        temporary.write_text(json.dumps(checkpoint.to_dict(), indent=2, default=str))
        # Owner-only, like the quarantine: the record holds task results.
        try:
            os.chmod(temporary, 0o600)
        except OSError:                                    # pragma: no cover - platform
            pass
        temporary.replace(path)
        return path

    def load(self, checkpoint_id: str) -> Checkpoint:
        path = self.path_for(checkpoint_id)
        if not path.exists():
            raise ResumeRefused(f"no checkpoint {checkpoint_id!r} in {self.root}")
        checkpoint = Checkpoint.from_dict(json.loads(path.read_text()))
        if not checkpoint.intact:
            raise ResumeRefused(
                f"checkpoint {checkpoint_id!r} does not match its own hash; it has been "
                "modified or truncated since it was written, and a run may not be resumed "
                "from a record that cannot be verified")
        return checkpoint

    def latest_for(self, loop_id: str) -> Checkpoint | None:
        found = [c for c in self.all() if c.loop_id == loop_id]
        return max(found, key=lambda c: c.created_at) if found else None

    def all(self) -> list[Checkpoint]:
        out: list[Checkpoint] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(Checkpoint.from_dict(json.loads(path.read_text())))
            except (json.JSONDecodeError, KeyError, ResumeRefused):
                continue                                   # corrupt or hashless: not listed
        return out


# ----------------------------------------------------------------- capture

def _withholding_reason(label: Any, ceiling: Sensitivity | None,
                        allow_results: bool) -> str | None:
    """Why a result may not be written, or None when it may."""
    if not allow_results:
        return "this run's envelope does not permit the PERSISTENT destination"
    if label is None:
        return "the result carries no label, so it cannot be shown to be storable"
    if ceiling is not None and label.sensitivity > ceiling:
        return (f"result classified {label.sensitivity.name} exceeds the persistence "
                f"ceiling of {ceiling.name}")
    if not label.permits(Destination.PERSISTENT):
        return (f"result classified {label.sensitivity.name} may not reach persistent "
                "storage under the destination ceilings")
    return None


def capture(state: LoopState, *, policy: Any = None, ceiling: Sensitivity | None = None,
            allow_results: bool = True, kernel: Any = None) -> Checkpoint:
    """Snapshot a loop. Pure: it reads ``state`` and writes nothing.

    ``ceiling`` is the persistence gateway's — the highest sensitivity durable state may
    hold — and ``allow_results`` is whether the run's envelope permits ``PERSISTENT`` at
    all. A result the gateway would refuse is withheld here for the same reasons, so the
    checkpoint file is not a second, ungoverned copy of what the WorkGraph refused.
    """
    graph = state.graph
    results: dict[str, Any] = {}
    labels: dict[str, Mapping[str, Any]] = {}
    withheld: dict[str, str] = {}
    for node in (graph.succeeded if graph else ()):
        reason = _withholding_reason(node.label, ceiling, allow_results)
        if reason is not None:
            withheld[node.id] = reason
            continue
        results[node.id] = node.result
        labels[node.id] = _label_to_dict(node.label) or {}

    # The whole record is a durable write, not only the results. A run that may not
    # persist used to have its results withheld while its objective, its task objectives
    # and payloads, and its error texts — the same content, arrived at a different way —
    # were written in full. So the objective's label (joined with the plan's) is judged
    # like a result's, and a run that may not persist writes no body text at all: the
    # plan's shape survives so progress can be resumed, the words do not.
    body_label = combine(*[l for l in (state.objective_label, state.plan_label)
                           if l is not None]) if (state.objective_label or state.plan_label) \
        else None
    body_reason = _withholding_reason(body_label, ceiling, allow_results) \
        if allow_results else "the run's envelope does not permit PERSISTENT"
    if body_label is None and allow_results:
        body_reason = None
    redacted = body_reason is not None
    if redacted:
        withheld["objective"] = body_reason
        withheld["plan_bodies"] = body_reason
        withheld["errors"] = body_reason

    task_states = {}
    for node in (graph.nodes.values() if graph else ()):
        error = node.error
        if redacted and error:
            error = error.split(":", 1)[0].strip() or "error"      # the class, not the text
        task_states[node.id] = {"state": node.state.value, "attempts": node.attempts,
                                "error": error}

    usage: dict[str, Any] = {}
    governor = getattr(kernel, "budget", None) if kernel is not None else None
    if governor is not None and hasattr(governor, "snapshot"):
        try:
            usage = dict(governor.snapshot(state.envelope).as_dict())
        except Exception:  # noqa: BLE001 - usage is an aid to resume, never a reason to fail
            usage = {}

    return Checkpoint(
        loop_id=state.loop_id, run_id=state.envelope.run_id,
        objective="" if redacted else state.objective,
        envelope=_envelope_to_dict(state.envelope),
        plan=(_redact_plan(state.plan.to_dict()) if redacted else state.plan.to_dict())
        if state.plan else {},
        task_states=task_states,
        results=results, labels=labels, withheld=withheld,
        objective_label=_label_to_dict(state.objective_label),
        plan_label=_label_to_dict(state.plan_label),
        iteration=state.iteration, replans=state.replans,
        digests=tuple(state.digests), redacted=redacted, usage=usage,
        policy=policy.as_dict() if policy is not None and hasattr(policy, "as_dict")
        else {})


def _redact_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's shape without its words: ids, kinds, components, dependencies, authority."""
    tasks = []
    for task in plan.get("tasks") or ():
        kept = {k: v for k, v in dict(task).items()
                if k not in ("objective", "payload", "acceptance_tests", "output_schema")}
        kept["objective"] = "withheld"
        kept["payload"] = {}
        kept["acceptance_tests"] = []
        kept["output_schema"] = {}
        tasks.append(kept)
    return {"plan_id": plan.get("plan_id", ""), "objective": "withheld",
            "produced_by": plan.get("produced_by", "unknown"), "assumptions": [],
            "evidence_requirements": [],
            "completion_criteria": [{"description": "withheld", "kind": c.get("kind", "manual")}
                                    for c in plan.get("completion_criteria") or ()],
            "tasks": tasks}


def plan_shape(plan: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """What a redacted plan still states, for matching a supplied plan against it.

    Input bindings are part of the shape: they say which argument reads which field of
    which result, and a resumed plan wiring its steps differently is a different plan.
    """
    return [(str(t.get("task_id")), str(t.get("kind")), str(t.get("component_id") or ""),
             tuple(t.get("dependencies") or ()),
             tuple((str(b.get("argument")), str(b.get("source")), str(b.get("pointer") or ""))
                   for b in t.get("inputs") or ()),
             t.get("input_sensitivity", "PUBLIC"))
            for t in plan.get("tasks") or ()]


# ------------------------------------------------------------------ resume

@dataclass(frozen=True, slots=True)
class _Narrowing:
    dimension: str
    was: Any
    now: Any


def resume(checkpoint: Checkpoint, kernel: Any, *, policy: Any = None,
           objective: str | None = None, plan: Plan | None = None) -> LoopState:
    """Rebuild a ``LoopState`` under **today's** authority, or refuse and say why.

    A redacted checkpoint (one written for a run that may not persist) holds the plan's
    shape and none of its words, so ``objective`` and ``plan`` must be supplied by the
    caller; the supplied plan must have the same tasks, kinds, components and
    dependencies as the shape that was recorded, or the resume is refused.
    """
    from ..kernel.authority import AuthorityLattice

    if not checkpoint.intact:
        raise ResumeRefused(
            f"checkpoint {checkpoint.checkpoint_id!r} does not match its own hash")
    if not checkpoint.plan:
        raise ResumeRefused(
            f"checkpoint {checkpoint.checkpoint_id!r} holds no plan; there is nothing to "
            "continue")
    if checkpoint.redacted:
        if objective is None or plan is None:
            raise ResumeRefused(
                f"checkpoint {checkpoint.checkpoint_id!r} was written for a run that may "
                "not persist, so it holds the plan's shape and none of its text; resuming "
                "needs the objective and the plan supplied again")
        if plan_shape(plan.to_dict()) != plan_shape(checkpoint.plan):
            raise ResumeRefused(
                "the supplied plan does not match the shape recorded in checkpoint "
                f"{checkpoint.checkpoint_id!r} (task ids, kinds, components, dependencies)")
    elif objective is not None or plan is not None:
        raise ResumeRefused("objective and plan may only be supplied for a redacted checkpoint")
    objective_text = checkpoint.objective if objective is None else objective

    stored = _envelope_from_dict(checkpoint.envelope)
    effective_policy = policy if policy is not None else getattr(kernel, "policy", None)
    if effective_policy is None:
        raise ResumeRefused("resuming needs a policy to re-authorise the run against")

    # THE rule. Not `stored`, and not the current ceiling either: the meet of both, so the
    # resumed run is no wider than it was and no wider than the policy in force now.
    ceiling = effective_policy.ceiling()
    resumed = AuthorityLattice.meet(stored, ceiling)

    narrowed = [_Narrowing(v.dimension, v.child, v.parent)
                for v in AuthorityLattice.violations(stored, ceiling)]

    from .loop import classify_with

    plan = plan if plan is not None else Plan.from_dict(checkpoint.plan)
    graph = ExecutionGraph(plan)
    for task_id, record in checkpoint.task_states.items():
        node = graph.nodes.get(task_id)
        if node is None:                      # the plan changed under the checkpoint
            continue
        node.state = TaskState(record.get("state", TaskState.PENDING.value))
        node.attempts = int(record.get("attempts") or 0)
        node.error = str(record.get("error") or "")
        if node.state is TaskState.SUCCEEDED and task_id in checkpoint.withheld:
            # The task finished, but its result was not written. It is not known here,
            # so the task runs again — the case ``_psh_idempotency_key`` exists for.
            node.state = TaskState.RETRYABLE
            node.error = ("result withheld from the checkpoint: "
                          f"{checkpoint.withheld[task_id]}")
        elif node.state is TaskState.SUCCEEDED:
            node.result = checkpoint.results.get(task_id)
            # The stored label joined with a fresh classification: a restored result can
            # be escalated by what it says, never lowered by what the file says.
            stored = _label_from_dict(checkpoint.labels.get(task_id))
            fresh = classify_with(kernel, node.result, origin=f"resume:{task_id}")
            node.label = fresh if stored is None else fresh.merged_with(stored)
        elif node.state is TaskState.RUNNING:
            # Nothing is running after a restart. Treat it as retryable rather than
            # succeeded: the process died mid-call and what it did is not known.
            node.state = TaskState.RETRYABLE
            node.error = node.error or "the process ended while this task was running"

    # Re-authorise only what has not run. Completed tasks are history; a task that has yet
    # to execute needs authority it may no longer have. The envelopes are kept rather than
    # recomputed later, so the loop executes the very ones this check ruled on.
    inadmissible: list[str] = []
    envelopes: dict[str, RunEnvelope] = {}
    for node in graph.nodes.values():
        try:
            envelopes[node.id] = task_envelope(node.task, resumed)
        except PolicyDenied as exc:
            if node.state is not TaskState.SUCCEEDED:
                inadmissible.append(f"{node.id}: {exc}")
    if inadmissible:
        raise ResumeRefused(
            f"the policy in force no longer permits {len(inadmissible)} unfinished task(s) "
            f"of checkpoint {checkpoint.checkpoint_id!r}; refusing rather than continuing a "
            "plan that would not be admitted today: " + "; ".join(inadmissible[:4]))

    audit = getattr(kernel, "audit", None)
    if audit is not None:
        audit("loop_resumed", run_id=resumed.run_id,
              detail={"loop_id": checkpoint.loop_id,
                      "checkpoint_id": checkpoint.checkpoint_id,
                      "iteration": checkpoint.iteration,
                      "narrowed": [n.dimension for n in narrowed],
                      "policy": getattr(effective_policy, "profile_id", "")})

    from .plan_validator import ValidatedPlan

    # The objective is classified again — the text is here, the classifier is here — and
    # joined with what the checkpoint knew. The plan's label is restored as stored: it is
    # the join of what a planner's model was shown, which the plan's text does not show.
    objective_label = classify_with(kernel, objective_text, origin="resume")
    stored_objective = _label_from_dict(checkpoint.objective_label)
    if stored_objective is not None:
        objective_label = objective_label.merged_with(stored_objective)

    # What the run had already spent is restored, so a restart does not reset the
    # ceilings it was running against.
    governor = getattr(kernel, "budget", None)
    if checkpoint.usage and governor is not None and hasattr(governor, "restore"):
        governor.restore(resumed, checkpoint.usage)

    state = LoopState(loop_id=checkpoint.loop_id, objective=objective_text,
                      envelope=resumed, iteration=checkpoint.iteration,
                      replans=checkpoint.replans, plan=plan, graph=graph,
                      validated=ValidatedPlan(plan=plan, envelopes=envelopes,
                                              order=tuple(graph.nodes)),
                      digests=list(checkpoint.digests),
                      objective_label=objective_label,
                      plan_label=_label_from_dict(checkpoint.plan_label))
    state.observe(resumed_from=checkpoint.checkpoint_id,
                  narrowed=[n.dimension for n in narrowed])
    return state
