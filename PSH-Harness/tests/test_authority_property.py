"""Property-based testing of authority monotonicity.

A reviewer's finding was that ``restrict()`` enforced four dimensions and silently permitted
escalation on five others. That is the characteristic failure of example-based testing on a
lattice: each test covers the dimension its author was thinking about, and the uncovered
ones look fine because nothing exercises them.

So the guarantee is stated as a property over generated envelope pairs rather than as a
list of cases. Adding a dimension to ``AuthorityLattice.BUDGET_DIMENSIONS`` brings it under
these tests automatically, which is the point: the next dimension should not depend on
someone remembering to write its test.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from psh.contracts import (  # noqa: E402
    AUTONOMY_ORDER, Autonomy, Budget, PolicyDenied, RiskTier, RunEnvelope,
)
from psh.kernel.authority import UNRESTRICTED, AuthorityLattice  # noqa: E402
from psh.labels import DataLabel, Destination, Sensitivity  # noqa: E402
from psh.licensing import INTEGRATION_MODES, LICENSE_CLASSES  # noqa: E402

SETTINGS = settings(max_examples=250, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])

_DESTINATIONS = list(Destination)
_SENSITIVITIES = list(Sensitivity)


@st.composite
def budgets(draw):
    return Budget(
        tokens_soft=draw(st.integers(1, 10_000)),
        tokens_hard=draw(st.integers(1, 100_000)),
        usd_soft=draw(st.floats(0.01, 10.0, allow_nan=False)),
        usd_hard=draw(st.floats(0.01, 100.0, allow_nan=False)),
        seconds_soft=draw(st.floats(1.0, 600.0, allow_nan=False)),
        seconds_hard=draw(st.floats(1.0, 3600.0, allow_nan=False)),
        max_model_calls=draw(st.integers(1, 100)),
        max_tool_calls=draw(st.integers(1, 100)),
        max_delegations=draw(st.integers(0, 10)))


@st.composite
def envelopes(draw):
    return RunEnvelope(
        risk=draw(st.sampled_from(list(RiskTier))),
        autonomy=draw(st.sampled_from(list(Autonomy))),
        max_label=DataLabel(draw(st.sampled_from(_SENSITIVITIES))),
        allowed_destinations=frozenset(draw(st.lists(
            st.sampled_from(_DESTINATIONS), min_size=1, max_size=6, unique=True))),
        allowed_capabilities=tuple(draw(st.lists(
            st.sampled_from(["a", "b", "c", "d"]), max_size=4, unique=True))),
        denied_capabilities=tuple(draw(st.lists(
            st.sampled_from(["x", "y"]), max_size=2, unique=True))),
        deadline=draw(st.one_of(st.none(), st.floats(1.0, 1e6, allow_nan=False))),
        # Drawn, not defaulted. A dimension the strategy never varies is a dimension the
        # property tests below silently skip — which is how ``require_isolated_tools``
        # scored zero effective examples the first time the coverage test ran.
        require_isolated_tools=draw(st.booleans()),
        allowed_integration_modes=tuple(draw(st.lists(
            st.sampled_from(INTEGRATION_MODES), max_size=3, unique=True))),
        allowed_license_classes=tuple(draw(st.lists(
            st.sampled_from(LICENSE_CLASSES), max_size=3, unique=True))),
        budget=draw(budgets()))


@given(envelopes())
@SETTINGS
def test_reflexive(envelope):
    """An envelope always contains itself. A lattice failing this is unusable."""
    assert AuthorityLattice.is_subset(envelope, envelope)


@given(envelopes())
@SETTINGS
def test_transitive(root):
    """A delegation chain cannot accumulate authority.

    Constructed rather than filtered: random triples almost never form a chain, so
    ``assume`` would discard nearly every example and prove nothing. Building the chain with
    ``meet`` exercises the same property on inputs that actually reach the assertion.
    """
    middle = AuthorityLattice.meet(root.restrict(risk=RiskTier.R0_TRIVIAL), root)
    leaf = AuthorityLattice.meet(middle.restrict(autonomy=Autonomy.OBSERVE), middle)
    assert AuthorityLattice.is_subset(middle, root)
    assert AuthorityLattice.is_subset(leaf, middle)
    assert AuthorityLattice.is_subset(leaf, root), "authority leaked across a two-hop chain"


@given(envelopes())
@SETTINGS
def test_antisymmetric_on_authority(envelope):
    """Mutual containment means identical authority on every dimension.

    Tested against a re-derived copy: an envelope clamped to itself must be mutually
    contained with the original, and therefore equal on every governed dimension.
    """
    clone = AuthorityLattice.meet(envelope, envelope)
    assert AuthorityLattice.is_subset(clone, envelope)
    assert AuthorityLattice.is_subset(envelope, clone)
    assert clone.max_label.sensitivity == envelope.max_label.sensitivity
    assert clone.allowed_destinations == envelope.allowed_destinations
    assert clone.risk == envelope.risk and clone.autonomy == envelope.autonomy
    for dimension in AuthorityLattice.BUDGET_DIMENSIONS:
        assert getattr(clone.budget, dimension) == getattr(envelope.budget, dimension)


@given(parent=envelopes(), child_kwargs=st.lists(
    st.sampled_from(["risk", "autonomy", "deadline", "budget", "max_label",
                     "allowed_destinations"]),
    min_size=1, max_size=3, unique=True))
@SETTINGS
def test_restrict_never_widens(parent, child_kwargs):
    """The central property: no argument combination yields authority the parent lacks.

    Values are drawn adversarially — deliberately maximal — so a dimension the lattice
    forgot would surface as a child holding more than its parent.
    """
    widest = {
        "risk": RiskTier.R4_KERNEL,
        "autonomy": Autonomy.ACT,
        "deadline": 1e12,
        "budget": Budget(tokens_soft=10 ** 9, tokens_hard=10 ** 9, usd_soft=1e6,
                         usd_hard=1e6, seconds_soft=1e9, seconds_hard=1e9,
                         max_model_calls=10 ** 6, max_tool_calls=10 ** 6,
                         max_delegations=10 ** 6),
        "max_label": DataLabel(Sensitivity.SECRET),
        "allowed_destinations": frozenset(_DESTINATIONS),
    }
    kwargs = {key: widest[key] for key in child_kwargs}
    try:
        child = parent.restrict(**kwargs)
    except PolicyDenied:
        return  # refusing to widen is the correct outcome
    assert AuthorityLattice.is_subset(child, parent), (
        f"restrict({sorted(kwargs)}) produced a child exceeding its parent: "
        f"{AuthorityLattice.violations(child, parent)}")


@given(envelopes())
@SETTINGS
def test_narrowing_always_succeeds(parent):
    """Restricting to the bottom of every dimension must never be refused.

    The dual of the widening property. A lattice that rejected a strictly narrower child
    would push callers toward constructing envelopes directly, which is how authority checks
    get bypassed in practice.
    """
    child = parent.restrict(
        risk=RiskTier.R0_TRIVIAL, autonomy=Autonomy.OBSERVE,
        max_label=DataLabel(Sensitivity.PUBLIC),
        allowed_destinations=frozenset(),
        budget=Budget(tokens_soft=1, tokens_hard=1, usd_soft=0.0, usd_hard=0.0,
                      seconds_soft=0.0, seconds_hard=0.0, max_model_calls=0,
                      max_tool_calls=0, max_delegations=0),
        deadline=parent.deadline if parent.deadline is not None else 1.0)
    assert AuthorityLattice.is_subset(child, parent)


@given(envelopes(), envelopes())
@SETTINGS
def test_meet_is_a_lower_bound_of_both(requested, parent):
    """``meet`` clamps rather than refuses, and its result is contained by the parent."""
    clamped = AuthorityLattice.meet(requested, parent)
    assert AuthorityLattice.is_subset(clamped, parent), \
        AuthorityLattice.violations(clamped, parent)


@given(envelopes())
@SETTINGS
def test_delegation_contract_cannot_exceed_its_parent_run(parent):
    """Delegation uses the same predicate, so it inherits the same guarantee."""
    from psh.contracts import DelegationContract
    from psh.kernel.egress import DelegationGateway

    # A contract asking for the widest possible authority on every dimension.
    widest = RunEnvelope(
        risk=RiskTier.R4_KERNEL, autonomy=Autonomy.ACT,
        max_label=DataLabel(Sensitivity.SECRET),
        allowed_destinations=frozenset(_DESTINATIONS),
        budget=Budget(tokens_soft=10 ** 9, tokens_hard=10 ** 9, usd_soft=1e6, usd_hard=1e6,
                      seconds_soft=1e9, seconds_hard=1e9, max_model_calls=10 ** 6,
                      max_tool_calls=10 ** 6, max_delegations=10 ** 6))
    contract = DelegationContract(task_id="t1", objective="anything", envelope=widest)

    decision = DelegationGateway().check(contract, parent)
    if decision.allowed:
        assert AuthorityLattice.is_subset(contract.envelope, parent), \
            "a delegation the gateway allowed granted authority the parent lacks"
    else:
        assert not AuthorityLattice.is_subset(contract.envelope, parent)


# ------------------------------------------------- one dimension at a time
#
# The test above is necessary and not sufficient, and a reviewer showed exactly why. Its
# child asks for the maximum on *every* dimension at once, and ``tokens_hard = 10**9``
# always exceeds the strategy's ``tokens_hard <= 100_000`` — so the very first budget
# comparison refused every generated example and no other dimension was ever the reason
# for the verdict. The property looked like it covered the lattice; it covered one field.
# It stayed green through an entire release in which ``DelegationGateway`` checked four of
# fourteen dimensions and returned ``allowed=True`` for a child holding R4_KERNEL, ACT,
# unrestricted capabilities and 999x the budget.
#
# That is the general hazard of a conjunctive property on a lattice: whichever dimension
# fails earliest masks the rest, and the test cannot tell "all dimensions are enforced"
# from "one dimension is enforced". So the property below widens EXACTLY ONE dimension
# from a child that is otherwise identical to its parent, and asserts both that the
# delegation is refused and that the refusal names that dimension. A dimension that stops
# being enforced now fails its own case instead of hiding behind another's.

def _mutate(parent: RunEnvelope, dimension: str) -> RunEnvelope | None:
    """Return ``parent`` widened on exactly ``dimension``, or None if it cannot widen.

    "Cannot widen" is an ordinary outcome — a parent already at R4_KERNEL has no risk
    headroom — and is filtered with ``assume`` rather than fudged, so a case that never
    produces a mutation shows up as a lack of examples instead of as a silent pass.
    """
    from dataclasses import replace

    if dimension == "risk":
        if parent.risk is RiskTier.R4_KERNEL:
            return None
        return replace(parent, risk=RiskTier(parent.risk + 1))

    if dimension == "autonomy":
        rank = AUTONOMY_ORDER.index(parent.autonomy)
        if rank == 0:
            return None
        return replace(parent, autonomy=AUTONOMY_ORDER[rank - 1])

    if dimension == "max_label":
        if parent.max_label.sensitivity is Sensitivity.SECRET:
            return None
        return replace(parent,
                       max_label=DataLabel(Sensitivity(parent.max_label.sensitivity + 1)))

    if dimension == "destinations":
        missing = [d for d in _DESTINATIONS if d not in parent.allowed_destinations]
        if not missing:
            return None
        return replace(parent,
                       allowed_destinations=parent.allowed_destinations | {missing[0]})

    if dimension == "capabilities":
        # Bounded -> unrestricted is the widening; an already-unrestricted parent has no
        # headroom on this dimension.
        if not parent.allowed_capabilities:
            return None
        return replace(parent, allowed_capabilities=UNRESTRICTED)

    if dimension == "denied_capabilities":
        if not parent.denied_capabilities:
            return None
        return replace(parent, denied_capabilities=parent.denied_capabilities[1:])

    if dimension == "deadline":
        if parent.deadline is None:
            return None            # an unbounded parent contains any child deadline
        return replace(parent, deadline=parent.deadline + 1000.0)

    if dimension == "require_isolated_tools":
        if not parent.require_isolated_tools:
            return None
        return replace(parent, require_isolated_tools=False)

    if dimension in ("allowed_integration_modes", "allowed_license_classes"):
        universe = (INTEGRATION_MODES if dimension == "allowed_integration_modes"
                    else LICENSE_CLASSES)
        held = getattr(parent, dimension)
        missing = [v for v in universe if v not in held]
        if not missing:
            return None
        return replace(parent, **{dimension: tuple(held) + (missing[0],)})

    if dimension.startswith("budget."):
        field_name = dimension.split(".", 1)[1]
        current = getattr(parent.budget, field_name)
        widened = (current + 1) if isinstance(current, int) else (current + 1.0)
        return replace(parent, budget=replace(parent.budget, **{field_name: widened}))

    raise AssertionError(f"unmutatable dimension {dimension!r}")


#: Every dimension the lattice governs. Built from the lattice's own budget table so a
#: Budget field added there is covered here without anyone remembering to add it.
WIDENABLE_DIMENSIONS: tuple[str, ...] = (
    "risk", "autonomy", "max_label", "destinations", "capabilities",
    "denied_capabilities", "deadline", "require_isolated_tools",
    # Adopted from BioScience-Harness. Adding a dimension to the lattice meant naming it
    # here; the per-dimension property, the restrict property and the anti-vacuity check
    # then covered it with no further work, which is the argument for one lattice.
    "allowed_integration_modes", "allowed_license_classes",
) + tuple(f"budget.{d}" for d in AuthorityLattice.BUDGET_DIMENSIONS)


@given(envelopes())
@SETTINGS
def test_widening_exactly_one_dimension_is_caught_and_named(parent):
    """Widen one dimension, hold the other eighteen fixed, and require a refusal.

    Every dimension is checked against every generated parent, rather than drawing
    ``(parent, dimension)`` pairs. With nineteen dimensions, sampling the pair gave each
    dimension about a nineteenth of the examples and then filtered most of those away for
    want of headroom — so coverage was thin and, as the anti-vacuity test below caught,
    genuinely unreliable from run to run. Looping is both stronger and cheaper: one drawn
    envelope exercises every dimension it can.
    """
    from psh.contracts import DelegationContract
    from psh.kernel.egress import DelegationGateway

    for dimension in WIDENABLE_DIMENSIONS:
        # Envelopes carrying a bare () for capabilities mean "unrestricted", so a parent
        # built that way has no capability headroom to take away; None means this parent
        # cannot widen here, which is an ordinary outcome rather than a failure.
        child = _mutate(parent, dimension)
        if child is None:
            continue

        violations = AuthorityLattice.violations(child, parent)
        assert [v.dimension for v in violations] == [dimension], (
            f"the mutation was meant to widen {dimension} alone; the lattice saw "
            f"{[v.dimension for v in violations]}")

        decision = DelegationGateway().check(
            DelegationContract(task_id="t", objective="x", envelope=child), parent)
        assert not decision.allowed, f"widening {dimension} alone was allowed"
        assert dimension in decision.reason


@given(envelopes())
@SETTINGS
def test_restrict_refuses_the_same_single_widening(parent):
    """``restrict`` and the delegation gateway must not diverge on any one dimension."""
    for dimension in WIDENABLE_DIMENSIONS:
        # ``denied_capabilities`` and ``require_isolated_tools`` narrow by construction in
        # restrict(), so they have their own test below rather than a refusal here.
        if dimension in ("denied_capabilities", "require_isolated_tools"):
            continue
        child = _mutate(parent, dimension)
        if child is None:
            continue
        with pytest.raises(PolicyDenied):
            parent.restrict(**_restrict_kwargs(child, dimension))


def _restrict_kwargs(child: RunEnvelope, dimension: str) -> dict:
    """The one keyword ``restrict`` needs to reproduce this mutation.

    ``denied_capabilities`` and ``require_isolated_tools`` are excluded deliberately:
    ``restrict`` unions denials and ORs the isolation requirement, so neither widening is
    *representable* through it. That is the stronger guarantee — the gateway has to refuse
    what a caller constructs by hand, while ``restrict`` cannot build it in the first
    place — so those two are asserted that way instead.
    """
    if dimension.startswith("budget."):
        return {"budget": child.budget}
    return {
        "allowed_integration_modes": {
            "allowed_integration_modes": child.allowed_integration_modes},
        "allowed_license_classes": {
            "allowed_license_classes": child.allowed_license_classes},
        "risk": {"risk": child.risk},
        "autonomy": {"autonomy": child.autonomy},
        "max_label": {"max_label": child.max_label},
        "destinations": {"allowed_destinations": child.allowed_destinations},
        "capabilities": {"allowed_capabilities": child.allowed_capabilities},
        "deadline": {"deadline": child.deadline},
    }[dimension]


@given(envelopes())
@SETTINGS
def test_restrict_cannot_represent_dropping_a_denial_or_isolation(parent):
    """Two dimensions narrow by construction rather than by refusal."""
    child = parent.restrict(denied_capabilities=(), require_isolated_tools=False)
    assert set(parent.denied_capabilities) <= set(child.denied_capabilities)
    assert child.require_isolated_tools >= parent.require_isolated_tools


# ------------------------------------------- the property tests must not be vacuous
#
# The reason the old delegation property passed while the gateway was wrong is that every
# generated example was filtered out by one dominant dimension. A property test can be
# green because the invariant holds, or because nothing reached the assertion, and nothing
# in a passing run distinguishes the two.
#
# So this checks the tests themselves: for each dimension, at least one generated parent
# must actually have headroom to widen there. It caught a real hole on its first run —
# ``envelopes()`` defaulted ``require_isolated_tools`` to False, so ``_mutate`` returned
# None for every example and that dimension had exactly zero coverage.

def test_every_lattice_dimension_is_actually_exercised():
    """No dimension may be filtered out of the per-dimension property entirely."""
    import collections

    from hypothesis import HealthCheck as _HealthCheck
    from hypothesis import given as _given
    from hypothesis import settings as _settings

    effective: collections.Counter = collections.Counter()

    # One drawn envelope contributes to every dimension's counter, so 200 examples give
    # each dimension 200 chances rather than 200/19. Drawing the dimension as well made
    # this test itself flaky — several dimensions scored zero in roughly one run in eight,
    # which is the failure mode it exists to detect, reported about itself.
    @_given(envelopes())
    @_settings(max_examples=200, deadline=None,
               suppress_health_check=list(_HealthCheck))
    def probe(parent):
        for dimension in WIDENABLE_DIMENSIONS:
            if _mutate(parent, dimension) is not None:
                effective[dimension] += 1

    probe()
    starved = sorted(d for d in WIDENABLE_DIMENSIONS if effective[d] == 0)
    assert not starved, (
        f"these dimensions produced no examples, so the property tests above prove "
        f"nothing about them: {starved}. Widen the ``envelopes()`` strategy so a parent "
        "can actually have headroom on each one.")
