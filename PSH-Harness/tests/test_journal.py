from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Barrier

import pytest

from psh.contracts import OperationUnresolved
from psh.runtime import (
    AgentLoopController, ExecutionGraph, JournalCheckpointStore, LoopLimits,
    LoopState, OperationLedger, ResumeRefused, StaticPlanner, Termination, capture, resume,
)
from psh.capabilities import CapabilityRegistry
from psh.labels import DataLabel, Sensitivity
from test_checkpoint import kernel_with, two_step_plan, Tool


@pytest.fixture
def setup(tmp_path):
    kernel = kernel_with(tmp_path, "kernel")
    state = LoopState(loop_id="loop_fixture", objective="synthetic objective",
                      envelope=kernel.policy.envelope())
    state.plan = two_step_plan()
    state.graph = ExecutionGraph(state.plan)
    yield kernel, state
    kernel.close()


def test_reopen_journal_roundtrip_and_duplicate_delivery(tmp_path, setup):
    kernel, state = setup
    checkpoint = capture(state, policy=kernel.policy)
    path = tmp_path / "journal.sqlite"
    with JournalCheckpointStore(path) as store:
        assert store.save(checkpoint) == store.save(checkpoint) == 1
        anchor = store.anchor()
    with JournalCheckpointStore(path) as store:
        loaded = store.load(checkpoint.checkpoint_id, expected_anchor=anchor)
        assert loaded.to_dict() == checkpoint.to_dict()
        assert len(store.all()) == 1


def test_sequence_not_wall_clock_defines_latest(tmp_path, setup):
    kernel, state = setup
    first = capture(state, policy=kernel.policy)
    second = replace(capture(state, policy=kernel.policy), created_at=1.0, hash="")
    with JournalCheckpointStore(tmp_path / "journal.sqlite") as store:
        store.save(first)
        store.save(second)
        assert store.latest_for(state.loop_id).checkpoint_id == second.checkpoint_id


def test_reused_id_with_different_content_is_refused(tmp_path, setup):
    kernel, state = setup
    checkpoint = capture(state, policy=kernel.policy)
    different = replace(checkpoint, objective="changed", hash="")
    with JournalCheckpointStore(tmp_path / "journal.sqlite") as store:
        store.save(checkpoint)
        with pytest.raises(ResumeRefused, match="ID reused"):
            store.save(different)
        assert len(store.all()) == 1


@pytest.mark.parametrize("sql", [
    "UPDATE checkpoint_journal SET body = '{}' WHERE seq = 1",
    "UPDATE checkpoint_journal SET version = 2 WHERE seq = 1",
    "UPDATE checkpoint_journal SET checkpoint_id = 'changed' WHERE seq = 1",
    "UPDATE checkpoint_journal SET loop_id = 'changed' WHERE seq = 1",
    "UPDATE checkpoint_journal SET previous = 'broken' WHERE seq = 1",
    "DELETE FROM checkpoint_journal WHERE seq = 1",
])
def test_corruption_is_refused_on_read_and_append(tmp_path, setup, sql):
    kernel, state = setup
    with JournalCheckpointStore(tmp_path / "journal.sqlite") as store:
        store.save(capture(state, policy=kernel.policy))
        store.save(capture(state, policy=kernel.policy))
        store._conn.execute(sql)
        with pytest.raises(ResumeRefused):
            store.all()
        with pytest.raises(ResumeRefused):
            store.save(capture(state, policy=kernel.policy))


def test_external_anchor_detects_suffix_loss(tmp_path, setup):
    kernel, state = setup
    with JournalCheckpointStore(tmp_path / "journal.sqlite") as store:
        store.save(capture(state, policy=kernel.policy))
        anchor = store.anchor()
        store._conn.execute("DELETE FROM checkpoint_journal")
        with pytest.raises(ResumeRefused, match="anchor"):
            store.all(expected_anchor=anchor)


def test_partial_sql_transaction_does_not_leave_a_snapshot(tmp_path, setup):
    kernel, state = setup
    path = tmp_path / "journal.sqlite"
    with JournalCheckpointStore(path):
        pass
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO checkpoint_journal VALUES (1,1,'partial','loop','{}','','')")
    conn.close()  # interrupted writer, no COMMIT
    with JournalCheckpointStore(path) as store:
        assert store.all() == []
        assert store.save(capture(state, policy=kernel.policy)) == 1


def test_two_connections_append_without_lost_snapshots(tmp_path, setup):
    kernel, state = setup
    checkpoints = [capture(state, policy=kernel.policy) for _ in range(8)]
    path = tmp_path / "journal.sqlite"
    with JournalCheckpointStore(path) as a, JournalCheckpointStore(path) as b:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit((a if i % 2 else b).save, c)
                       for i, c in enumerate(checkpoints)]
            assert sorted(f.result() for f in futures) == list(range(1, 9))
        assert {c.checkpoint_id for c in a.all()} == {c.checkpoint_id for c in checkpoints}


def test_withheld_content_is_not_written_to_journal(tmp_path, setup):
    kernel, state = setup
    secret = "private fixture content"
    state.objective = secret
    state.objective_label = DataLabel(Sensitivity.PHI)
    state.graph.mark_running("first", at=1)
    state.graph.mark_succeeded("first", {"private": secret}, at=2,
                               label=DataLabel(Sensitivity.PHI))
    checkpoint = capture(state, policy=kernel.policy, allow_results=False)
    with JournalCheckpointStore(tmp_path / "journal.sqlite") as store:
        store.save(checkpoint)
        body = store._conn.execute("SELECT body FROM checkpoint_journal").fetchone()[0]
        assert secret not in body
        assert store.load(checkpoint.checkpoint_id).withheld["first"]


def test_loop_resumes_from_reopened_journal_without_repeating_completed_tool(tmp_path, setup):
    kernel, _ = setup
    registry = CapabilityRegistry()
    first, second = Tool("first", {"evidence_ref": "fixture:source-1"}), Tool("second")
    registry.register(first)
    registry.register(second)
    path = tmp_path / "journal.sqlite"
    with JournalCheckpointStore(path) as store:
        interrupted = AgentLoopController(kernel, planner=StaticPlanner(two_step_plan()),
            registry=registry, checkpoints=store,
            limits=LoopLimits(max_iterations=1, max_replans=0)).run("two steps", kernel.policy.envelope())
        assert interrupted.termination == Termination.MAX_ITERATIONS
    with JournalCheckpointStore(path) as store:
        state = resume(store.latest_for(interrupted.loop_id), kernel)
        continued = AgentLoopController(kernel, planner=StaticPlanner(two_step_plan()),
            registry=registry, checkpoints=store).run("two steps", state.envelope, resume_from=state)
        assert continued.termination == Termination.GOAL_SATISFIED
        assert continued.results["first"]["evidence_ref"] == "fixture:source-1"
        assert len(first.calls) == len(second.calls) == 1


def test_atomic_operation_claim_allows_only_one_nonrepeatable_writer(tmp_path):
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = OperationLedger(tmp_path / "ops.sqlite")
        b = OperationLedger(tmp_path / "ops.sqlite")

        def claim(ledger):
            barrier.wait(timeout=5)
            try:
                ledger.begin("run:task", component_id="submit", idempotent=False)
                return "allowed"
            except OperationUnresolved:
                return "refused"

        try:
            results = [pool.submit(claim, ledger) for ledger in (a, b)]
            assert sorted(f.result() for f in results) == ["allowed", "refused"]
            assert a.get("run:task").attempts == 1
        finally:
            a.close()
            b.close()


def test_manifest_change_cannot_make_uncertain_side_effect_replay_safe(tmp_path):
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    try:
        ledger.begin("run:task", component_id="submit", idempotent=False)
        with pytest.raises(OperationUnresolved):
            ledger.begin("run:task", component_id="submit", idempotent=True)
        with pytest.raises(OperationUnresolved):
            ledger.begin("run:task", component_id="different", idempotent=True)
    finally:
        ledger.close()


def test_exception_after_nonrepeatable_side_effect_is_not_blindly_retried(tmp_path, setup):
    from psh.runtime import RetryPolicy, OperationState
    kernel, _ = setup
    registry = CapabilityRegistry()
    first = Tool("first", fail_times=1, idempotent=False)
    registry.register(first)
    registry.register(Tool("second"))
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    try:
        plan = two_step_plan(retry=RetryPolicy(max_attempts=2, retryable=("Exception",)))
        envelope = kernel.policy.envelope()
        result = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
            operations=ledger).run("two steps", envelope)
        assert result.termination != Termination.GOAL_SATISFIED
        assert len(first.calls) == 1
        assert ledger.get(f"{envelope.run_id}:first").state == OperationState.UNKNOWN
    finally:
        ledger.close()
