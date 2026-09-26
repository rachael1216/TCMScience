from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from psh.labels import DataLabel, Destination as D, Sensitivity as S
from psh.runtime import PlanRejected
from psh.scientist import ScientificLedger
from psh.scientist.records import _hash, _json
from psh.workflow import (Effect, ProtocolBinding, ScientificCompiler, ScientificPlanner,
                          ScientificProgram, StatisticalDesign, assess_amendment)
from test_scientist_records import setup as records_setup, register, protocol, hypothesis
from test_scientific_workflow import contract, program, task, policy


@pytest.fixture
def setup(records_setup):
    kernel, ledger, _ = records_setup
    kernel.policy = policy()
    return kernel, ledger, kernel.policy.envelope()


def bound(record, actual=None):
    return program([task("a", destinations=(D.LOCAL_MODEL, D.PERSISTENT)),
                    task("b", ("a",))], {
        "a": contract(effects=(Effect.LOCAL_MODEL, Effect.PERSIST),
                      statistics=StatisticalDesign(actual or protocol()),
                      protocol_binding=ProtocolBinding(record.id, protocol().fingerprint)),
        "b": contract()})


def compile_bound(setup, p):
    kernel, ledger, env = setup
    return ScientificCompiler(scientific_ledger=ledger).compile(p, env, policy=kernel.policy)


def refuses(setup, p, code):
    with pytest.raises(PlanRejected) as exc:
        compile_bound(setup, p)
    assert code in {v.family for v in exc.value.violations}
    return str(exc.value)


def test_binding_roundtrip_and_governed_label_propagation(setup):
    kernel, ledger, env = setup
    node = register(setup, DataLabel(S.PHI))
    count = kernel.graph.stats()["nodes"]
    p = bound(node)
    assert ScientificProgram.from_dict(json.loads(json.dumps(p.to_dict()))) == p
    result = compile_bound(setup, p)
    assert result.sensitivities == {"a": S.PHI, "b": S.PHI}
    assert all(t.input_sensitivity == S.PHI for t in result.plan.tasks)
    assert ledger.resolve_protocol(node.id, env)[0] == protocol()
    assert kernel.graph.stats()["nodes"] == count


def test_no_ledger_fails_closed(setup):
    p = bound(register(setup))
    with pytest.raises(PlanRejected) as exc:
        ScientificCompiler().compile(p, setup[2])
    assert "PROTOCOL102" in {v.family for v in exc.value.violations}


def test_missing_design_refused(setup):
    p = bound(register(setup))
    p.contracts["a"] = replace(p.contracts["a"], statistics=None)
    refuses(setup, p, "PROTOCOL101")


def test_wrong_fingerprint_refused(setup):
    p = bound(register(setup))
    p.contracts["a"] = replace(p.contracts["a"], protocol_binding=replace(
        p.contracts["a"].protocol_binding, fingerprint="0" * 64))
    refuses(setup, p, "PROTOCOL104")


@pytest.mark.parametrize("field,value", [
    ("primary_endpoint", "changed"), ("statistical_test", "changed"),
    ("secondary_endpoints", ("changed",)), ("covariates", ("changed",)),
    ("exclusion_criteria", "changed"), ("sample_size_assumptions", "changed"),
    ("subgroup_plan", "changed"), ("stopping_criteria", "changed"),
])
def test_all_protocol_changes_refused(setup, field, value):
    refuses(setup, bound(register(setup), replace(protocol(), **{field: value})), "PROTOCOL105")


def test_missing_wrong_kind_and_cross_project_refs(setup):
    kernel, ledger, env = setup
    nodes = [SimpleNamespace(id="missing-private-id"), ledger.hypothesize(hypothesis(), env)]
    other = ScientificLedger(kernel, kernel.graph.project("other").id)
    h = other.hypothesize(hypothesis(), env)
    nodes.append(other.preregister(h.id, protocol(), env))
    for node in nodes:
        message = refuses(setup, bound(node), "PROTOCOL103")
        assert node.id not in message


def test_no_task_persistence_authority(setup):
    p = bound(register(setup))
    p = replace(p, plan=replace(p.plan, tasks=(task("a"), task("b", ("a",)))))
    p.contracts["a"] = replace(p.contracts["a"], effects=(Effect.LOCAL_MODEL,))
    refuses(setup, p, "PROTOCOL103")


def test_record_above_task_ceiling(setup):
    p = bound(register(setup, DataLabel(S.PHI)))
    p = replace(p, plan=replace(p.plan, tasks=(replace(p.plan.tasks[0], max_label=S.PUBLIC),
                                              p.plan.tasks[1])))
    refuses(setup, p, "PROTOCOL103")


def test_phi_cannot_reach_public_downstream(setup):
    p = bound(register(setup, DataLabel(S.PHI)))
    p = replace(p, plan=replace(p.plan, tasks=(p.plan.tasks[0],
        task("b", ("a",), destinations=(D.PUBLIC_REMOTE,)))))
    p.contracts["b"] = contract(effects=(Effect.PUBLIC_REMOTE,))
    with pytest.raises(PlanRejected):
        compile_bound(setup, p)


@pytest.mark.parametrize("tamper", ["body", "fingerprint", "sequence", "unknown_field"])
def test_corrupt_record_refused_without_echo(setup, tamper):
    kernel, _, _ = setup
    node = register(setup)
    body = json.loads(node.body)
    if tamper == "body":
        body["record"]["primary_endpoint"] = "private-mutated-text"
    elif tamper == "fingerprint":
        body["protocol_hash"] = "0" * 64
    elif tamper == "sequence":
        body["record"]["covariates"] = "private-mutated-text"
    else:
        body["record"]["private-mutated-text"] = "unknown"
    kernel.graph.update(node.id, body=_json(body),
                        ref=node.ref if tamper == "body" else "sha256:" + _hash(body))
    message = refuses(setup, bound(node), "PROTOCOL103")
    assert "private-mutated-text" not in message


def test_planner_rechecks_current_ledger_policy(setup):
    kernel, ledger, env = setup
    p = bound(register(setup, DataLabel(S.PHI)))
    planner = ScientificPlanner(p, scientific_ledger=ledger)
    state = SimpleNamespace(envelope=env)
    planner.plan(state)
    kernel.policy = replace(kernel.policy, max_data_label=S.PUBLIC)
    with pytest.raises(PlanRejected):
        planner.plan(state)


def test_changed_binding_invalidates_dependents(setup):
    kernel, ledger, env = setup
    before = bound(register(setup))
    after = bound(register(setup))
    result = assess_amendment(before, after, env, scientific_ledger=ledger)
    assert set(result.invalidated) == {"a", "b"}


@pytest.mark.parametrize("record_id,fingerprint", [
    ("", "0" * 64), (" padded", "0" * 64), (1, "0" * 64),
    ("id", "wrong"), ("id", "A" * 64), ("id", None),
])
def test_invalid_binding_rejected(record_id, fingerprint):
    with pytest.raises(ValueError):
        ProtocolBinding(record_id, fingerprint)


def test_legacy_wire_format_preserved():
    assert "protocol_binding" not in contract().to_dict()
    with pytest.raises(ValueError):
        contract(protocol_binding={})
