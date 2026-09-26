"""Offline design declarations, strict wire parsing and compiler integration."""

from dataclasses import replace
import json

import pytest

from psh.scientist import Protocol
from psh.workflow import ScientificProgram, StatisticalDesign, TaskContract, assess_amendment
from test_scientific_workflow import compile_program, contract, policy, program, rejected, task


def design(**kwargs):
    protocol = Protocol("synthetic endpoint", (), "prespecified exclusions",
                        "declared test", "declared power assumptions", (),
                        "no subgroups", "fixed sample size")
    return StatisticalDesign(protocol=protocol, **kwargs)


@pytest.mark.parametrize("kwargs,code", [
    ({"comparisons": 2}, "STAT101"),
    ({"comparisons": 2, "multiplicity_plan": "  "}, "STAT101"),
    ({"holdout": True}, "STAT102"),
    ({"holdout": True, "training_units": ("opaque",),
      "evaluation_units": ("opaque",)}, "STAT103"),
    ({"holdout": True, "training_units": ("a",),
      "evaluation_units": ("b",), "selection_units": ("b",)}, "STAT104"),
    ({"training_units": ("a",)}, "STAT105"),
    ({"repeated_measures": True}, "STAT106"),
    ({"time_to_event": True}, "STAT107"),
])
def test_compiler_rejects_incomplete_or_leaking_design(kwargs, code):
    rejected(program(contracts={"a": contract(statistics=design(**kwargs))}), code)


def test_complete_design_roundtrips_and_compiles():
    stats = design(comparisons=3, multiplicity_plan="prespecified family-wise adjustment",
                   holdout=True, training_units=("a", "b"), evaluation_units=("c",),
                   selection_units=("a",), repeated_measures=True,
                   dependence_plan="cluster by independent unit", time_to_event=True,
                   censoring_plan="prespecified censoring rules and sensitivity analysis")
    p = program(contracts={"a": contract(statistics=stats)})
    restored = ScientificProgram.from_dict(json.loads(json.dumps(p.to_dict())))
    assert restored == p
    assert compile_program(restored).fingerprint == compile_program(p).fingerprint
    assert stats.to_dict()["protocol"]["covariates"] == []


@pytest.mark.parametrize("kwargs", [
    {"comparisons": True}, {"comparisons": 0}, {"comparisons": 1.5},
    {"holdout": "false"}, {"repeated_measures": 1}, {"time_to_event": None},
    {"multiplicity_plan": None}, {"dependence_plan": 1}, {"censoring_plan": []},
    {"training_units": ["a"]}, {"training_units": ("a", "a")},
    {"evaluation_units": (" ",)}, {"selection_units": (" a",)},
    {"selection_units": (1,)},
])
def test_invalid_types_rejected(kwargs):
    with pytest.raises(ValueError):
        design(**kwargs)


@pytest.mark.parametrize("field", ["training_units", "evaluation_units", "selection_units"])
def test_wire_arrays_cannot_be_strings(field):
    payload = design().to_dict()
    payload[field] = "opaque"
    with pytest.raises(ValueError):
        StatisticalDesign.from_dict(payload)


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_fields_fail_closed(nested):
    payload = design().to_dict()
    (payload["protocol"] if nested else payload)["typo"] = True
    with pytest.raises(TypeError):
        StatisticalDesign.from_dict(payload)


def test_primary_endpoint_required_by_reused_protocol():
    payload = design().to_dict()
    payload["protocol"]["primary_endpoint"] = " "
    with pytest.raises(ValueError):
        StatisticalDesign.from_dict(payload)
    payload["protocol"].pop("primary_endpoint")
    with pytest.raises(TypeError):
        StatisticalDesign.from_dict(payload)


def test_legacy_contract_wire_shape_unchanged():
    legacy = contract().to_dict()
    assert set(legacy) == {"sensitivity", "effects", "side_effect", "evidence", "claim"}
    assert TaskContract.from_dict(legacy).statistics is None
    assert TaskContract.from_dict(dict(legacy, statistics=None)).to_dict() == legacy
    compile_program(program())


def test_contract_rejects_untyped_statistics():
    with pytest.raises(ValueError):
        contract(statistics=design().to_dict())


def test_all_diagnostics_collected_without_unit_ids():
    stats = design(holdout=True, training_units=("sensitive-opaque-token",),
                   evaluation_units=("sensitive-opaque-token",),
                   selection_units=("sensitive-opaque-token",), comparisons=2,
                   repeated_measures=True, time_to_event=True)
    assert {c for c, _ in stats.violations()} == {
        "STAT101", "STAT103", "STAT104", "STAT106", "STAT107"}
    assert "sensitive-opaque-token" not in repr(stats.violations())


def test_design_change_invalidates_downstream_only():
    p = program([task("a"), task("b", ("a",)), task("c")], {
        "a": contract(statistics=design()), "b": contract(), "c": contract()})
    contracts = dict(p.contracts)
    contracts["a"] = replace(contracts["a"], statistics=design(
        comparisons=2, multiplicity_plan="declared strategy"))
    amended = replace(p, contracts=contracts)
    result = assess_amendment(p, amended, policy().envelope(), policy=policy())
    assert set(result.invalidated) == {"a", "b"}
    assert result.old_fingerprint != result.new_fingerprint
