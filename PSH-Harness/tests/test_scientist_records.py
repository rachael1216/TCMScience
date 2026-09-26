from dataclasses import replace
import sqlite3

import pytest

from psh.config import PSHConfig
from psh.contracts import PolicyDenied
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.scientist import Hypothesis, Observation, Protocol, ScientificLedger
from psh.workgraph import EdgeKind, NodeKind


@pytest.fixture
def setup(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "kernel").ensure_dirs(),
                           policy=PolicySnapshot(profile_id="science", max_data_label=Sensitivity.PHI))
    project = kernel.graph.project("synthetic research")
    ledger = ScientificLedger(kernel, project.id)
    yield kernel, ledger, kernel.policy.envelope()
    kernel.close()


def hypothesis():
    return Hypothesis("fixture intervention changes endpoint", "fixture population",
                      ("endpoint changes",), ("no endpoint change",), ("batch effect",))


def protocol():
    return Protocol("endpoint A", (), "prespecified exclusions", "permutation test",
                    "simulation-based precision assumption", (), "none", "fixed sample size")


def observation():
    return Observation("no effect in fixture data", ("sha256:fixture-data",), "negative")


def register(setup, label=None):
    _, ledger, env = setup
    h = ledger.hypothesize(hypothesis(), env, label=label)
    return ledger.preregister(h.id, protocol(), env)


def test_cycle_preserves_protocol_and_lineage(setup):
    kernel, ledger, env = setup
    p = register(setup)
    result = ledger.observe(p.id, observation(), protocol(), env)
    body = ledger.read(result.id, env)
    assert body["deviation"] is None
    assert body["protocol_hash"] == protocol().fingerprint
    assert body["record"]["outcome"] == "negative"
    assert kernel.graph.get(p.id).body == p.body
    assert kernel.graph.get(p.id).ref == p.ref
    assert [n.kind for n in kernel.graph.lineage(result.id)] == [NodeKind.PROTOCOL, NodeKind.HYPOTHESIS]
    assert any("hypothesis" in line for line in kernel.graph.why(result.id))
    assert result.meta["validation_status"] == "candidate"


@pytest.mark.parametrize("field,value", [
    ("primary_endpoint", "endpoint B"), ("secondary_endpoints", ("added",)),
    ("exclusion_criteria", "changed"), ("statistical_test", "t test"),
    ("sample_size_assumptions", "changed"), ("covariates", ("age",)),
    ("subgroup_plan", "new subgroup"), ("stopping_criteria", "early stopping"),
])
def test_all_protocol_fields_require_explicit_deviation(setup, field, value):
    kernel, ledger, env = setup
    p = register(setup)
    actual = replace(protocol(), **{field: value})
    count = kernel.graph.stats()["nodes"]
    with pytest.raises(ValueError, match="deviation reason"):
        ledger.observe(p.id, observation(), actual, env)
    assert kernel.graph.stats()["nodes"] == count
    result = ledger.observe(p.id, observation(), actual, env, deviation_reason="documented change")
    body = ledger.read(result.id, env)
    assert list(body["deviation"]["changed_fields"]) == [field]
    assert body["actual_protocol_hash"] != body["protocol_hash"]
    assert ledger.read(p.id, env)["protocol_hash"] == protocol().fingerprint


def test_phi_derived_counts_remain_phi_in_persistence(setup):
    _, ledger, env = setup
    p = register(setup, DataLabel(Sensitivity.PHI))
    result = ledger.observe(p.id, observation(), protocol(), env)
    assert result.label.sensitivity == Sensitivity.PHI
    with pytest.raises(PolicyDenied):
        ledger.read(result.id, env.restrict(max_label=DataLabel(Sensitivity.PUBLIC)))


def test_explicit_observation_label_cannot_exceed_run_ceiling(setup):
    kernel, ledger, env = setup
    p = register(setup)
    count = kernel.graph.stats()["nodes"]
    with pytest.raises(PolicyDenied):
        ledger.observe(p.id, observation(), protocol(),
                       env.restrict(max_label=DataLabel(Sensitivity.PUBLIC)),
                       label=DataLabel(Sensitivity.PHI))
    assert kernel.graph.stats()["nodes"] == count


def test_run_without_persistence_cannot_create_records(setup):
    _, ledger, env = setup
    local = env.restrict(allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}))
    with pytest.raises(PolicyDenied, match="PERSISTENT"):
        ledger.hypothesize(hypothesis(), local)


def test_cross_project_reference_is_refused(setup):
    kernel, ledger, env = setup
    p = register(setup)
    other = ScientificLedger(kernel, kernel.graph.project("other").id)
    with pytest.raises(ValueError, match="cross-project"):
        other.observe(p.id, observation(), protocol(), env)


def test_mutated_protocol_body_is_refused(setup):
    kernel, ledger, env = setup
    p = register(setup)
    kernel.graph.update(p.id, body=p.body.replace("endpoint A", "endpoint B"))
    with pytest.raises(ValueError, match="hash mismatch"):
        ledger.observe(p.id, observation(), protocol(), env)


def test_durable_roundtrip(setup):
    from psh.workgraph import WorkGraph
    kernel, ledger, env = setup
    p = register(setup)
    result = ledger.observe(p.id, observation(), protocol(), env)
    with WorkGraph(kernel.graph.path) as reopened:
        assert reopened.get(result.id).body == result.body
        assert len(reopened.lineage(result.id)) == 2


def test_node_and_source_edges_commit_atomically(setup):
    kernel, _, env = setup
    before = kernel.graph.stats()
    commits = kernel.persistence.commits
    with pytest.raises(sqlite3.IntegrityError):
        kernel.persistence.commit_node(kind=NodeKind.OBSERVATION, title="fixture",
            principal=env.principal.id, source_run=env.run_id,
            links=(("nonexistent", EdgeKind.DERIVED_FROM),))
    assert kernel.graph.stats() == before
    assert kernel.persistence.commits == commits


def test_nested_transaction_rollback_and_recovery(setup):
    kernel, _, _ = setup
    before = kernel.graph.stats()
    with pytest.raises(RuntimeError):
        with kernel.graph.transaction():
            kernel.graph.add(NodeKind.HYPOTHESIS, "outer")
            with kernel.graph.transaction():
                kernel.graph.add(NodeKind.PROTOCOL, "inner")
            raise RuntimeError("fault injection")
    assert kernel.graph.stats() == before
    kernel.graph.add(NodeKind.HYPOTHESIS, "subsequent write")
    assert kernel.graph.stats()["nodes"] == before["nodes"] + 1


def test_candidate_science_is_not_verified_memory(setup):
    from psh.context.memory import MemoryRetriever
    kernel, ledger, env = setup
    h = ledger.hypothesize(hypothesis(), env)
    # Even an explicitly widened kind filter must retain the validation-status gate.
    memory = MemoryRetriever(kernel.graph, kinds=(NodeKind.HYPOTHESIS,))
    assert memory.retrieve("fixture intervention", envelope=env,
                           destination=Destination.LOCAL_MODEL) == []


def test_current_policy_can_narrow_a_previously_issued_envelope(setup):
    kernel, ledger, env = setup
    kernel.policy = replace(kernel.policy, allowed_destinations=(Destination.LOCAL_COMPUTE,))
    with pytest.raises(PolicyDenied):
        ledger.hypothesize(hypothesis(), env)


def test_concurrent_success_is_not_rolled_back_with_failed_transaction(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    kernel, _, _ = setup
    entered, release, attempted = Event(), Event(), Event()

    def fails():
        with pytest.raises(RuntimeError):
            with kernel.graph.transaction():
                kernel.graph.add(NodeKind.HYPOTHESIS, "discard")
                entered.set()
                assert release.wait(5)
                raise RuntimeError("rollback")

    def succeeds():
        assert entered.wait(5)
        attempted.set()
        return kernel.graph.add(NodeKind.HYPOTHESIS, "keep")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(fails), pool.submit(succeeds)
        assert attempted.wait(5)
        try:
            assert not second.done()
        finally:
            release.set()
        first.result()
        kept = second.result()
    assert kernel.graph.get(kept.id).title == "keep"
    assert [n.title for n in kernel.graph.nodes(kind=NodeKind.HYPOTHESIS)] == ["keep"]
