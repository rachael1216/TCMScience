"""A stale run envelope must not override the compiler's current policy."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from psh.contracts import Autonomy, RiskTier
from psh.kernel.authority import AuthorityLattice
from psh.labels import Destination as D, Sensitivity as S
from psh.runtime import PlanRejected
from psh.workflow import ScientificCompiler, ScientificPlanner, assess_amendment
from test_scientific_workflow import contract, policy, program, task


@pytest.mark.parametrize("change", [
    {"allowed_destinations": (D.LOCAL_COMPUTE,)},
    {"max_data_label": S.PUBLIC},
])
def test_stale_envelope_cannot_override_tightened_policy(change):
    old = policy()
    current = replace(old, **change)
    with pytest.raises(PlanRejected):
        ScientificCompiler().compile(program(contracts={"a": contract(sensitivity=S.INTERNAL)}),
                                     old.envelope(), policy=current)


def test_budget_checked_against_current_policy():
    old = policy()
    current = replace(old, budget=replace(old.budget, tokens_hard=1, tokens_soft=1))
    p = program([task("a", estimated_tokens=2)])
    with pytest.raises(PlanRejected):
        ScientificCompiler().compile(p, old.envelope(), policy=current)


def test_effective_envelopes_inherit_all_policy_restrictions():
    old = policy()
    current = replace(old, autonomy=Autonomy.SUGGEST, risk_ceiling=RiskTier.R0_TRIVIAL,
                      require_isolated_tools=True,
                      deadline=1234567890.0, allowed_integration_modes=("native",),
                      allowed_license_classes=(old.allowed_license_classes[0],))
    stale = old.envelope(project_id="opaque-project", task_id="opaque-task")
    result = ScientificCompiler().compile(program(), stale, policy=current)
    effective = result.validated.envelope_for("a")
    assert not AuthorityLattice.violations(effective, current.ceiling())
    assert not AuthorityLattice.violations(effective, stale)
    assert effective.parent_run_id == stale.run_id
    assert effective.project_id == stale.project_id
    assert effective.principal == stale.principal


def test_broader_policy_does_not_widen_existing_run():
    current = policy()
    narrow = current.envelope().restrict(
        autonomy=Autonomy.SUGGEST, allowed_destinations=frozenset({D.LOCAL_MODEL}),
        require_isolated_tools=True, deadline=1234567890.0)
    result = ScientificCompiler().compile(program(), narrow, policy=current)
    assert not AuthorityLattice.violations(result.validated.envelope_for("a"), narrow)


def test_no_policy_keeps_explicit_envelope_contract():
    envelope = policy().envelope()
    result = ScientificCompiler().compile(program(), envelope)
    assert not AuthorityLattice.violations(result.validated.envelope_for("a"), envelope)


def test_planner_rechecks_updated_policy_with_old_run():
    old = policy()
    planner = ScientificPlanner(program(), policy=old)
    state = SimpleNamespace(envelope=old.envelope())
    planner.plan(state)
    planner.policy = replace(old, allowed_destinations=(D.LOCAL_COMPUTE,))
    with pytest.raises(PlanRejected):
        planner.plan(state)


def test_amendment_does_not_admit_candidates_under_revoked_destination():
    old = policy()
    current = replace(old, allowed_destinations=(D.LOCAL_COMPUTE,))
    p = program()
    with pytest.raises(PlanRejected):
        assess_amendment(p, p, old.envelope(), policy=current, completed=("a",))
