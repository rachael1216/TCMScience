"""PolicySnapshot: one frozen policy object, so declared policy is effective policy.

A reviewer found two related defects in v0.1's profile plumbing. The first was a plain bug:
the documented usage ``runner.run(request, **profile.as_envelope_kwargs())`` raised
``TypeError`` because ``max_label`` was not a parameter of ``run``. The second was worse and
is the reason this module replaces the mechanism rather than adding the missing parameter:

    literature profile declares  require_citation = True
    as_config_kwargs()           does not carry it
    OutputGate ends up with      require_citation = False

The profile claimed literature work must cite; the kernel did not enforce it. That is the
general failure mode of splitting one policy across several keyword paths — every split is
an opportunity for a field to be declared in one place and dropped in another.

So policy travels as a single immutable object from profile to runner to kernel to gate. A
field that exists on the snapshot reaches every enforcement point, or the snapshot fails to
construct. There is no partial path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from .contracts import (
    Autonomy, Budget, PolicyDenied, RiskTier, RunEnvelope, _autonomy_rank,
)
from .labels import DataLabel, Destination, Sensitivity
from .licensing import INTEGRATION_MODES, LICENSE_CLASSES

__all__ = ["PolicySnapshot", "PolicyLattice", "PolicyViolation"]


def _as_label(value: Any) -> DataLabel | None:
    """Accept either a ``Sensitivity`` or an already-built ``DataLabel``.

    Callers mint envelopes with both shapes, and silently ignoring one of them is how a
    requested ceiling goes unchecked.
    """
    if value is None:
        return None
    return value if isinstance(value, DataLabel) else DataLabel(value)


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """The complete, frozen policy for one run.

    Frozen because a run's policy must not change under it. A component that could widen the
    policy it operates under is not governed by it — the same argument that keeps the kernel
    out of the component system.
    """

    profile_id: str
    profile_version: str = "1"
    max_data_label: Sensitivity = Sensitivity.PHI
    allowed_destinations: tuple[Destination, ...] = (
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT)
    require_citation: bool = False
    require_claim_support: bool = True
    #: When True the broker refuses any component that is not process-isolated. A profile
    #: that touches identifiable data should set it: a tool running inside the kernel
    #: process is governed by convention, not by the operating system.
    require_isolated_tools: bool = False
    #: Refuse to build a kernel on the built-in fallback classifier. A clinical
    #: deployment whose ceiling is PHI should not start on a detector that admits it is
    #: narrower than the validated one — a reviewer showed the fallback missing Chinese
    #: record identifiers entirely. Off by default so research profiles still run
    #: anywhere; on for any profile that will see charts.
    require_validated_classifier: bool = False
    #: Refuse to build a kernel, or to admit a run policy, whose isolated tools would run
    #: under ``NoSandbox``. Process isolation with a clean environment behind the egress
    #: proxy is what the runner always provides; confining the child's filesystem and raw
    #: sockets needs an OS sandbox backend, and a profile that executes untrusted code
    #: (a self-evolution candidate, a downloaded pipeline) must say it needs one.
    require_os_isolation: bool = False
    autonomy: Autonomy = Autonomy.ACT_WITH_APPROVAL
    risk_ceiling: RiskTier = RiskTier.R1_ROUTINE
    budget: Budget = field(default_factory=Budget)
    deadline: float | None = None
    approval_required_at: RiskTier = RiskTier.R3_CLINICAL
    verification_model_id: str = ""
    #: Principals permitted to lower a label. Empty by default: declassification is the ONE
    #: operation in the lattice that moves data toward wider exposure, so it is closed unless
    #: a profile opens it by name. My own audit found the Declassification type carried a
    #: principal field that nothing ever checked — a record of who did it, with no control
    #: over who may.
    declassifiers: tuple[str, ...] = ()
    #: The lowest sensitivity a permitted declassifier may reach. PHI -> RESEARCH_DEIDENTIFIED
    #: is the ordinary clinical case; PHI -> PUBLIC is not something a profile should grant
    #: without saying so.
    declassify_floor: Sensitivity = Sensitivity.RESEARCH_DEIDENTIFIED
    #: Licence provenance, adopted from BioScience-Harness. The default is everything the
    #: table knows about, because the table already refuses the combination that matters
    #: (unlicensed code may not be vendored). A profile narrows from there — a group that
    #: distributes its analysis pipelines would set ``("native", "federated")`` and stop
    #: vendoring outright, and one with a copyleft prohibition would drop that class.
    allowed_integration_modes: tuple[str, ...] = INTEGRATION_MODES
    allowed_license_classes: tuple[str, ...] = LICENSE_CLASSES
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("a policy snapshot must name the profile it came from")
        if self.require_citation and not self.require_claim_support:
            # Requiring a citation while not checking whether it supports the claim is the
            # exact failure the previous version shipped: a citation that exists and does
            # not support the sentence. Refuse the combination rather than implement it.
            raise PolicyDenied(
                "require_citation without require_claim_support would enforce the presence "
                "of an identifier while never checking that it supports the claim")

    @property
    def permits_network(self) -> bool:
        return any(d in (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)
                   for d in self.allowed_destinations)

    def ceiling(self) -> RunEnvelope:
        """The maximum authority this policy grants. Nothing minted may exceed it."""
        return RunEnvelope(
            risk=self.risk_ceiling, autonomy=self.autonomy,
            max_label=DataLabel(self.max_data_label),
            allowed_destinations=frozenset(self.allowed_destinations),
            budget=self.budget, deadline=self.deadline, profile=self.profile_id,
            require_isolated_tools=self.require_isolated_tools,
            allowed_integration_modes=self.allowed_integration_modes,
            allowed_license_classes=self.allowed_license_classes)

    def envelope(self, *, clamp: bool = False, **kw: Any) -> RunEnvelope:
        """Mint a ``RunEnvelope`` under this policy. The single construction path.

        The policy is a **ceiling, not a set of defaults**. v0.4 read the requested risk and
        autonomy with ``kw.pop(field, self.field)``, so a caller who passed nothing got the
        policy's value and a caller who passed something got whatever they asked for —
        including ``risk=R4_KERNEL`` under an ``R2`` policy and ``autonomy=ACT`` under a
        ``SUGGEST`` profile. A ceiling that applies only when the caller declines to state a
        value is not a ceiling.

        So every requested dimension is checked against the ceiling by the one predicate
        that already defines authority containment, ``AuthorityLattice``:

            effective = policy ∩ requested

        Widening raises ``PolicyDenied``. Passing ``clamp=True`` asks for the meet instead —
        the request is narrowed to what the policy permits rather than refused, which is the
        right behaviour where a broad default profile is applied to an already narrow run,
        and the wrong behaviour where a caller states an authority it must not have. Refusal
        is the default for that reason.
        """
        from .kernel.authority import AuthorityLattice

        ceiling = self.ceiling()
        # ``or`` is not usable for defaulting here: RiskTier.R0_TRIVIAL is 0 and therefore
        # falsy, so ``kw.pop("risk", None) or self.risk_ceiling`` would silently promote the
        # lowest risk tier to the policy ceiling — the reverse of the bug being fixed.
        def _requested(name: str, fallback: Any) -> Any:
            value = kw.pop(name, None)
            return fallback if value is None else value

        requested = RunEnvelope(
            task_id=kw.pop("task_id", ""), project_id=kw.pop("project_id", ""),
            principal=_requested("principal", ceiling.principal),
            risk=_requested("risk", self.risk_ceiling),
            autonomy=_requested("autonomy", self.autonomy),
            max_label=_as_label(kw.pop("max_label", None)) or DataLabel(self.max_data_label),
            allowed_destinations=frozenset(
                _requested("allowed_destinations", self.allowed_destinations)),
            budget=_requested("budget", self.budget),
            deadline=kw.pop("deadline", self.deadline),
            # Stamped from the policy and OR-ed with any request, so a run minted under a
            # policy that requires isolation carries the requirement to whoever executes
            # it. Before this the flag lived only on the kernel, so a *narrower* per-run
            # policy could declare isolation and the broker — constructed from the kernel's
            # policy — would never see it.
            require_isolated_tools=bool(kw.pop("require_isolated_tools", False)
                                        or self.require_isolated_tools),
            allowed_integration_modes=tuple(_requested(
                "allowed_integration_modes", self.allowed_integration_modes)),
            allowed_license_classes=tuple(_requested(
                "allowed_license_classes", self.allowed_license_classes)),
            profile=self.profile_id, **kw)

        if clamp:
            return AuthorityLattice.meet(requested, ceiling)
        violations = AuthorityLattice.violations(requested, ceiling)
        if violations:
            raise PolicyDenied(
                f"policy {self.profile_id!r} is a ceiling and the requested envelope exceeds "
                f"the authority it grants: " + "; ".join(str(v) for v in violations))
        return requested

    def with_(self, **kw: Any) -> "PolicySnapshot":
        """Return a narrowed copy. Widening is refused, as it is for envelopes.

        This checked three of the twelve dimensions a policy actually carries — data
        ceiling, destinations, risk — so ``SUGGEST -> ACT``,
        ``require_isolated_tools=True -> False``, ``tokens_hard 10 -> 999``, a relaxed
        approval threshold, a lowered declassification floor and an extended deadline were
        all accepted by a method whose docstring said widening is refused. The fix is not a
        fourth, fifth and sixth ``if``: it is the same move ``RunEnvelope.restrict`` already
        made, deferring to one predicate that names every dimension in one place.
        """
        return PolicyLattice.enforce(replace(self, **kw), self, operation="with_")

    def is_narrower_than(self, parent: "PolicySnapshot") -> bool:
        """True when this policy grants no authority ``parent`` lacks."""
        return not PolicyLattice.violations(self, parent)

    def narrow_to(self, parent: "PolicySnapshot") -> "PolicySnapshot":
        """Return the meet of this policy and ``parent``: never wider than either."""
        return PolicyLattice.meet(self, parent)

    def summary(self) -> str:
        net = "network" if self.permits_network else "local only"
        return (f"{self.profile_id} v{self.profile_version}: {self.max_data_label.name}, "
                f"{net}, autonomy={self.autonomy.value}, risk<={self.risk_ceiling.name}, "
                f"citation={self.require_citation}, support={self.require_claim_support}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id, "profile_version": self.profile_version,
            "max_data_label": self.max_data_label.name,
            "allowed_destinations": [d.name for d in self.allowed_destinations],
            "require_citation": self.require_citation,
            "require_claim_support": self.require_claim_support,
            "require_isolated_tools": self.require_isolated_tools,
            "require_validated_classifier": self.require_validated_classifier,
            "require_os_isolation": self.require_os_isolation,
            "autonomy": self.autonomy.value, "risk_ceiling": self.risk_ceiling.name,
            "tokens_hard": self.budget.tokens_hard, "usd_hard": self.budget.usd_hard,
            "integration_modes": list(self.allowed_integration_modes),
            "license_classes": list(self.allowed_license_classes),
            "deadline": self.deadline}


# ------------------------------------------------------------------- policy lattice

@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """One dimension on which a policy exceeded the policy that must contain it."""

    dimension: str
    parent: Any
    child: Any

    def __str__(self) -> str:
        return f"{self.dimension}: parent={self.parent!r} child={self.child!r}"


class PolicyLattice:
    """Containment for whole policies, as ``AuthorityLattice`` is for envelopes.

    Two defects shared one cause. ``PolicySnapshot.with_()`` claimed to refuse widening and
    compared three dimensions. ``Runner.run(policy=...)`` accepted any snapshot the caller
    handed it and minted the run envelope from that, never meeting it against
    ``TrustedKernel.policy`` — so a kernel that forbade public providers executed a run
    that reached one, because the envelope the gateways check had been minted by a
    different policy from the one the kernel was built with.

    Both are the same shape as the envelope defect this package already fixed once:
    containment open-coded at a call site drifts from the set of fields it has to cover.
    So containment is defined once, here, over **every** field that grants or withholds
    authority. A new policy field is added to one of the tables below or it is not
    governed, and the property test then covers it everywhere.

    Direction, stated per group because it is not the same for all of them:

    * *permissions* (data ceiling, destinations, risk, budget, deadline, declassifiers)
      narrow by getting smaller;
    * *requirements* (``require_citation``, ``require_claim_support``,
      ``require_isolated_tools``) narrow by being turned ON, so a child may set one and
      never clear it;
    * *thresholds* narrow in whichever direction means "more is checked":
      ``approval_required_at`` lower means more work needs a human;
      ``declassify_floor`` higher means less may be declassified.
    """

    #: Turned on, never off. A child inherits every requirement its parent states.
    REQUIREMENTS: tuple[str, ...] = (
        "require_citation", "require_claim_support", "require_isolated_tools",
        "require_validated_classifier", "require_os_isolation")

    #: Delegated to the authority lattice so budget containment has exactly one definition.
    @staticmethod
    def _budget_dimensions() -> tuple[str, ...]:
        from .kernel.authority import AuthorityLattice
        return AuthorityLattice.BUDGET_DIMENSIONS

    @classmethod
    def violations(cls, child: PolicySnapshot,
                   parent: PolicySnapshot) -> list[PolicyViolation]:
        """Every dimension on which ``child`` grants more than ``parent``."""
        out: list[PolicyViolation] = []

        if child.max_data_label > parent.max_data_label:
            out.append(PolicyViolation("max_data_label", parent.max_data_label.name,
                                       child.max_data_label.name))

        extra = set(child.allowed_destinations) - set(parent.allowed_destinations)
        if extra:
            out.append(PolicyViolation(
                "allowed_destinations",
                sorted(d.name for d in parent.allowed_destinations),
                sorted(d.name for d in extra)))

        if child.risk_ceiling > parent.risk_ceiling:
            out.append(PolicyViolation("risk_ceiling", parent.risk_ceiling.name,
                                       child.risk_ceiling.name))

        if _autonomy_rank(child.autonomy) < _autonomy_rank(parent.autonomy):
            out.append(PolicyViolation("autonomy", parent.autonomy.value,
                                       child.autonomy.value))

        for name in cls.REQUIREMENTS:
            if getattr(parent, name) and not getattr(child, name):
                out.append(PolicyViolation(name, True, False))

        if child.approval_required_at > parent.approval_required_at:
            # A higher threshold means fewer runs stop for a human.
            out.append(PolicyViolation("approval_required_at",
                                       parent.approval_required_at.name,
                                       child.approval_required_at.name))

        if child.declassify_floor < parent.declassify_floor:
            # A lower floor means a declassifier may reach further down the lattice.
            out.append(PolicyViolation("declassify_floor", parent.declassify_floor.name,
                                       child.declassify_floor.name))

        for dimension in ("allowed_integration_modes", "allowed_license_classes"):
            extra = set(getattr(child, dimension)) - set(getattr(parent, dimension))
            if extra:
                out.append(PolicyViolation(dimension, sorted(getattr(parent, dimension)),
                                           sorted(extra)))

        new_principals = set(child.declassifiers) - set(parent.declassifiers)
        if new_principals:
            out.append(PolicyViolation("declassifiers", sorted(parent.declassifiers),
                                       sorted(new_principals)))

        if parent.verification_model_id and (child.verification_model_id
                                             != parent.verification_model_id):
            # Swapping the verifier a policy named — including for none at all — changes
            # what "verified" means under that policy.
            out.append(PolicyViolation("verification_model_id",
                                       parent.verification_model_id,
                                       child.verification_model_id or "none"))

        if parent.deadline is not None:
            if child.deadline is None:
                out.append(PolicyViolation("deadline", parent.deadline, "unbounded"))
            elif child.deadline > parent.deadline:
                out.append(PolicyViolation("deadline", parent.deadline, child.deadline))

        for dimension in cls._budget_dimensions():
            parent_value = getattr(parent.budget, dimension)
            child_value = getattr(child.budget, dimension)
            if child_value > parent_value:
                out.append(PolicyViolation(f"budget.{dimension}", parent_value, child_value))

        return out

    @classmethod
    def is_narrower(cls, child: PolicySnapshot, parent: PolicySnapshot) -> bool:
        return not cls.violations(child, parent)

    @classmethod
    def enforce(cls, child: PolicySnapshot, parent: PolicySnapshot, *,
                operation: str = "narrow") -> PolicySnapshot:
        """Return ``child`` when contained by ``parent``, else raise ``PolicyDenied``."""
        violations = cls.violations(child, parent)
        if violations:
            raise PolicyDenied(
                f"{operation} would grant authority the policy {parent.profile_id!r} does "
                "not hold: " + "; ".join(str(v) for v in violations))
        return child

    @classmethod
    def meet(cls, requested: PolicySnapshot, parent: PolicySnapshot) -> PolicySnapshot:
        """The greatest lower bound: ``requested`` clamped to ``parent`` on every field.

        Used where clamping is kinder than refusing. The result is never wider than either
        input on any dimension, which is the property the tests assert rather than the
        field-by-field arithmetic below.
        """
        budget = replace(requested.budget, **{
            dimension: min(getattr(requested.budget, dimension),
                           getattr(parent.budget, dimension))
            for dimension in cls._budget_dimensions()})

        deadline = requested.deadline
        if parent.deadline is not None:
            deadline = parent.deadline if deadline is None else min(deadline, parent.deadline)

        destinations = tuple(d for d in requested.allowed_destinations
                             if d in set(parent.allowed_destinations))

        return replace(
            requested,
            max_data_label=min(requested.max_data_label, parent.max_data_label),
            allowed_destinations=destinations,
            risk_ceiling=min(requested.risk_ceiling, parent.risk_ceiling),
            autonomy=(requested.autonomy
                      if _autonomy_rank(requested.autonomy) >= _autonomy_rank(parent.autonomy)
                      else parent.autonomy),
            approval_required_at=min(requested.approval_required_at,
                                     parent.approval_required_at),
            declassify_floor=max(requested.declassify_floor, parent.declassify_floor),
            declassifiers=tuple(d for d in requested.declassifiers
                                if d in set(parent.declassifiers)),
            allowed_integration_modes=tuple(
                m for m in requested.allowed_integration_modes
                if m in set(parent.allowed_integration_modes)),
            allowed_license_classes=tuple(
                c for c in requested.allowed_license_classes
                if c in set(parent.allowed_license_classes)),
            verification_model_id=(parent.verification_model_id
                                   or requested.verification_model_id),
            deadline=deadline, budget=budget,
            **{name: bool(getattr(requested, name) or getattr(parent, name))
               for name in cls.REQUIREMENTS})
