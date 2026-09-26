"""Checkpoint and resume, and the one rule that makes resuming safe.

``Runner``'s ``checkpoint`` stage wrote an audit event, so "checkpoint" named a record of
having finished rather than a state a run could continue from.

The centrepiece is ``test_a_resumed_run_cannot_hold_authority_the_policy_has_since_withdrawn``.
Restoring the envelope a run held is the obvious implementation and it is a hole: an
envelope is a grant, and a grant that outlives the policy that issued it is a capability
the kernel never agreed to. That is P0-1 — the run executing under an envelope minted by a
policy nobody compared to the kernel's — arriving through a file instead of a keyword
argument. The same defect has now had two vectors, which is the argument for the meet
being in one place and every entry point going through it.
"""

from __future__ import annotations

import json

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, ComponentKind, ComponentManifest, ModelProfile, RiskTier,
)
from psh.kernel import TrustedKernel
from psh.kernel.authority import AuthorityLattice
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    AgentLoopController, Checkpoint, CheckpointStore, Criterion, LoopLimits, LoopState,
    Plan, PlanTask, ResumeRefused, StaticPlanner, TaskKind, TaskState, Termination,
    capture, resume,
)

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)
WIDE = LOCAL + (Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE)


def policy(**kw):
    params = dict(profile_id="ckpt_bench", allowed_destinations=WIDE,
                  max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


def kernel_with(tmp_path, name, **kw):
    return TrustedKernel(PSHConfig(state_dir=tmp_path / name).ensure_dirs(),
                         policy=policy(**kw))


class Tool:
    def __init__(self, cid, result=None, fail_times=0, **manifest_kw):
        params = dict(id=cid, name=cid, kind=ComponentKind.TOOL,
                      max_label=Sensitivity.PHI, risk_tier=RiskTier.R1_ROUTINE)
        params.update(manifest_kw)
        self.manifest = ComponentManifest(**params)
        self.calls: list = []
        self._result = result if result is not None else {"ok": True}
        self._fail_times = fail_times

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        if len(self.calls) <= self._fail_times:
            raise RuntimeError("process died here")
        return self._result


def two_step_plan(**task_kw):
    return Plan(
        objective="two steps", produced_by="test",
        tasks=(PlanTask(task_id="first", objective="step one", kind=TaskKind.TOOL,
                        component_id="first", **task_kw),
               PlanTask(task_id="second", objective="step two", kind=TaskKind.TOOL,
                        component_id="second", dependencies=("first",), **task_kw)),
        completion_criteria=(Criterion(description="both ran", kind="task"),))


# ======================================================== THE rule

def test_a_resumed_run_cannot_hold_authority_the_policy_has_since_withdrawn(tmp_path):
    """P0-1's second vector: a grant that outlives the policy that issued it.

    Checkpoint under a policy permitting PUBLIC_REMOTE, R3 and PHI. Narrow the kernel's
    policy to local-only, R1, INTERNAL. Resume. The resumed run must hold the *meet*, not
    the envelope it was carrying when it stopped.
    """
    broad = kernel_with(tmp_path, "broad")
    state = LoopState(loop_id="loop_a", objective="work",
                      envelope=broad.policy.envelope())
    state.plan = two_step_plan()
    from psh.runtime import ExecutionGraph
    state.graph = ExecutionGraph(state.plan)
    checkpoint = capture(state, policy=broad.policy)

    assert Destination.PUBLIC_REMOTE in state.envelope.allowed_destinations
    assert state.envelope.risk is RiskTier.R3_CLINICAL
    broad.close()

    narrow = kernel_with(tmp_path, "narrow", allowed_destinations=LOCAL,
                         risk_ceiling=RiskTier.R1_ROUTINE,
                         max_data_label=Sensitivity.INTERNAL,
                         autonomy=Autonomy.SUGGEST)
    resumed = resume(checkpoint, narrow)

    assert Destination.PUBLIC_REMOTE not in resumed.envelope.allowed_destinations
    assert resumed.envelope.risk is RiskTier.R1_ROUTINE
    assert resumed.envelope.max_label.sensitivity is Sensitivity.INTERNAL
    assert resumed.envelope.autonomy is Autonomy.SUGGEST
    assert AuthorityLattice.is_subset(resumed.envelope, narrow.policy.ceiling())
    narrow.close()


def test_resuming_never_widens_either(tmp_path):
    """The meet cuts both ways: a policy that has *broadened* does not re-grant."""
    narrow = kernel_with(tmp_path, "n", allowed_destinations=LOCAL,
                         risk_ceiling=RiskTier.R1_ROUTINE)
    state = LoopState(loop_id="l", objective="w", envelope=narrow.policy.envelope())
    state.plan = two_step_plan()
    from psh.runtime import ExecutionGraph
    state.graph = ExecutionGraph(state.plan)
    checkpoint = capture(state, policy=narrow.policy)
    narrow.close()

    broad = kernel_with(tmp_path, "b")            # now permits PUBLIC_REMOTE and R3
    resumed = resume(checkpoint, broad)
    assert Destination.PUBLIC_REMOTE not in resumed.envelope.allowed_destinations
    assert resumed.envelope.risk is RiskTier.R1_ROUTINE
    broad.close()


def test_the_narrowing_is_recorded_not_silent(tmp_path):
    """A run that quietly loses authority mid-flight is as confusing as one that gains it."""
    broad = kernel_with(tmp_path, "broad")
    state = LoopState(loop_id="l", objective="w", envelope=broad.policy.envelope())
    state.plan = two_step_plan()
    from psh.runtime import ExecutionGraph
    state.graph = ExecutionGraph(state.plan)
    checkpoint = capture(state, policy=broad.policy)
    broad.close()

    narrow = kernel_with(tmp_path, "narrow", allowed_destinations=LOCAL,
                         risk_ceiling=RiskTier.R1_ROUTINE)
    resumed = resume(checkpoint, narrow)
    assert resumed.observations[-1]["narrowed"], "the narrowing was not reported"
    events = [e for e in narrow.events.all()] if hasattr(narrow.events, "all") else []
    narrow.close()
    assert "destinations" in resumed.observations[-1]["narrowed"]


def test_a_task_the_new_policy_forbids_refuses_the_resume(tmp_path):
    """Unfinished work is re-authorised one task at a time, and a refusal names it."""
    broad = kernel_with(tmp_path, "broad")
    plan = Plan(
        objective="reach out", produced_by="test",
        tasks=(PlanTask(task_id="outward", objective="call a public provider",
                        kind=TaskKind.MODEL,
                        destinations=(Destination.PUBLIC_REMOTE,)),),
        completion_criteria=(Criterion(description="done", kind="task"),))
    state = LoopState(loop_id="l", objective="w", envelope=broad.policy.envelope())
    state.plan = plan
    from psh.runtime import ExecutionGraph
    state.graph = ExecutionGraph(plan)
    checkpoint = capture(state, policy=broad.policy)
    broad.close()

    narrow = kernel_with(tmp_path, "narrow", allowed_destinations=LOCAL)
    with pytest.raises(ResumeRefused) as exc:
        resume(checkpoint, narrow)
    assert "outward" in str(exc.value)
    narrow.close()


def test_a_completed_task_is_not_re_authorised(tmp_path):
    """Completed work is history. Only what has yet to run needs authority today."""
    broad = kernel_with(tmp_path, "broad")
    plan = Plan(
        objective="two", produced_by="test",
        tasks=(PlanTask(task_id="outward", objective="already done",
                        kind=TaskKind.MODEL,
                        destinations=(Destination.PUBLIC_REMOTE,)),
               PlanTask(task_id="local", objective="still to do", kind=TaskKind.MODEL,
                        destinations=(Destination.LOCAL_MODEL,),
                        dependencies=("outward",))),
        completion_criteria=(Criterion(description="done", kind="task"),))
    from psh.runtime import ExecutionGraph
    state = LoopState(loop_id="l", objective="w", envelope=broad.policy.envelope())
    state.plan = plan
    state.graph = ExecutionGraph(plan)
    state.graph.mark_running("outward", at=1.0)
    # Labelled, as the loop always labels: an unlabelled result is withheld from the
    # checkpoint and its task runs again, which is a different test.
    state.graph.mark_succeeded("outward", {"done": True}, at=2.0, label=DataLabel())
    checkpoint = capture(state, policy=broad.policy)
    broad.close()

    narrow = kernel_with(tmp_path, "narrow", allowed_destinations=LOCAL)
    resumed = resume(checkpoint, narrow)         # must not refuse over the finished task
    assert resumed.graph.nodes["outward"].state is TaskState.SUCCEEDED
    assert resumed.graph.nodes["outward"].result == {"done": True}
    narrow.close()


# ================================================= the record is verified

def test_a_tampered_checkpoint_is_refused(tmp_path):
    store = CheckpointStore(tmp_path / "ckpt")
    kernel = kernel_with(tmp_path, "k")
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope())
    state.plan = two_step_plan()
    from psh.runtime import ExecutionGraph
    state.graph = ExecutionGraph(state.plan)
    path = store.save(capture(state, policy=kernel.policy))

    raw = json.loads(path.read_text())
    raw["envelope"]["risk"] = "R4_KERNEL"        # edited in place, hash left alone
    path.write_text(json.dumps(raw))

    with pytest.raises(ResumeRefused, match="hash"):
        store.load(raw["checkpoint_id"])
    kernel.close()


def test_a_checkpoint_round_trips_through_disk(tmp_path):
    store = CheckpointStore(tmp_path / "ckpt")
    kernel = kernel_with(tmp_path, "k")
    from psh.runtime import ExecutionGraph
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope())
    state.plan = two_step_plan()
    state.graph = ExecutionGraph(state.plan)
    state.graph.mark_running("first", at=1.0)
    state.graph.mark_succeeded("first", {"n": 1}, at=2.0, label=DataLabel())
    state.iteration = 3

    original = capture(state, policy=kernel.policy)
    store.save(original)
    loaded = store.load(original.checkpoint_id)

    assert loaded.intact and loaded.to_dict() == original.to_dict()
    resumed = resume(loaded, kernel)
    assert resumed.iteration == 3
    assert resumed.graph.nodes["first"].state is TaskState.SUCCEEDED
    assert resumed.graph.nodes["first"].result == {"n": 1}
    assert resumed.plan.tasks[1].task_id == "second"
    kernel.close()


def test_an_empty_checkpoint_is_refused(tmp_path):
    kernel = kernel_with(tmp_path, "k")
    empty = Checkpoint(loop_id="l", run_id="r", objective="o",
                       envelope={"run_id": "r", "risk": "R1_ROUTINE", "autonomy": "act",
                                 "max_label": "PUBLIC", "allowed_destinations": []},
                       plan={}, task_states={}, results={})
    with pytest.raises(ResumeRefused, match="no plan"):
        resume(empty, kernel)
    kernel.close()


# ============================================= end to end through the loop

def test_a_loop_checkpoints_every_iteration(tmp_path):
    kernel = kernel_with(tmp_path, "k")
    registry = CapabilityRegistry()
    registry.register(Tool("first"))
    registry.register(Tool("second"))
    store = CheckpointStore(tmp_path / "ckpt")

    loop = AgentLoopController(kernel, planner=StaticPlanner(two_step_plan()),
                               registry=registry, checkpoints=store,
                               limits=LoopLimits(max_iterations=6),
                               sleep=lambda s: None)
    result = loop.run("two steps", kernel.policy.envelope())

    assert result.termination is Termination.GOAL_SATISFIED
    saved = store.all()
    assert saved, "the loop wrote no checkpoints"
    assert all(c.intact for c in saved)
    assert store.latest_for(result.loop_id) is not None
    kernel.close()


def test_an_interrupted_run_resumes_without_repeating_finished_work(tmp_path):
    """The point of the feature: an interruption costs one iteration, not the run.

    The interruption is modelled as the loop stopping with work outstanding — one
    iteration, ``first`` done and ``second`` never started — rather than as a tool raising.
    A tool that raises is a *task failure*, which is information the checkpoint should
    preserve and a different thing from the process ending. The distinction has its own
    test below.
    """
    kernel = kernel_with(tmp_path, "k")
    registry = CapabilityRegistry()
    first = Tool("first", result={"n": 1})
    second = Tool("second", result={"n": 2})
    registry.register(first)
    registry.register(second)
    store = CheckpointStore(tmp_path / "ckpt")

    interrupted = AgentLoopController(
        kernel, planner=StaticPlanner(two_step_plan()), registry=registry,
        checkpoints=store, limits=LoopLimits(max_iterations=1, max_replans=0),
        sleep=lambda s: None).run("two steps", kernel.policy.envelope())

    assert interrupted.termination is Termination.MAX_ITERATIONS
    assert len(first.calls) == 1 and second.calls == []

    state = resume(store.latest_for(interrupted.loop_id), kernel)
    assert state.graph.nodes["first"].state is TaskState.SUCCEEDED

    continued = AgentLoopController(
        kernel, planner=StaticPlanner(two_step_plan()), registry=registry,
        limits=LoopLimits(max_iterations=6), sleep=lambda s: None
    ).run("two steps", kernel.policy.envelope(), resume_from=state)

    assert continued.termination is Termination.GOAL_SATISFIED, continued.summary()
    assert len(first.calls) == 1, "the finished task was re-run on resume"
    assert len(second.calls) == 1, "the outstanding task did not run on resume"
    assert continued.results["first"] == {"n": 1}, "the restored result was lost"
    kernel.close()


def test_a_task_that_genuinely_failed_stays_failed_across_a_resume(tmp_path):
    """A terminal failure is information. Resuming must not launder it into 'not yet run'.

    Only RUNNING is reset, because that is the state whose outcome is genuinely unknown
    after a restart. Resetting FAILED would re-run a task that had already decided.
    """
    kernel = kernel_with(tmp_path, "k")
    from psh.runtime import ExecutionGraph
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope())
    state.plan = two_step_plan()
    state.graph = ExecutionGraph(state.plan)
    state.graph.mark_running("first", at=1.0)
    state.graph.mark_failed("first", "the component said no", at=2.0)

    resumed = resume(capture(state, policy=kernel.policy), kernel)
    assert resumed.graph.nodes["first"].state is TaskState.FAILED
    assert "said no" in resumed.graph.nodes["first"].error
    assert resumed.graph.nodes["second"].state is TaskState.BLOCKED
    kernel.close()


def test_a_task_running_when_the_process_died_is_retried_not_believed(tmp_path):
    """Nothing is RUNNING after a restart, and what it did is not known."""
    kernel = kernel_with(tmp_path, "k")
    from psh.runtime import ExecutionGraph
    state = LoopState(loop_id="l", objective="w", envelope=kernel.policy.envelope())
    state.plan = two_step_plan()
    state.graph = ExecutionGraph(state.plan)
    state.graph.mark_running("first", at=1.0)     # and then the process ended

    resumed = resume(capture(state, policy=kernel.policy), kernel)
    node = resumed.graph.nodes["first"]
    assert node.state is TaskState.RETRYABLE
    assert "process ended" in node.error
    kernel.close()


def test_a_failing_checkpoint_store_does_not_end_the_run(tmp_path):
    """A snapshot that cannot be written must not fail a run that is otherwise fine."""
    kernel = kernel_with(tmp_path, "k")
    registry = CapabilityRegistry()
    registry.register(Tool("first"))
    registry.register(Tool("second"))

    class Broken:
        def save(self, checkpoint):
            raise OSError("disk full")

    loop = AgentLoopController(kernel, planner=StaticPlanner(two_step_plan()),
                               registry=registry, checkpoints=Broken(),
                               limits=LoopLimits(max_iterations=6), sleep=lambda s: None)
    result = loop.run("two steps", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED
    kernel.close()
