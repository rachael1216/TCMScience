"""The AuthorityLattice: one subset check behind every authority-narrowing operation.

A reviewer showed that ``RunEnvelope.restrict()`` in v0.1 enforced monotonicity on four
dimensions (destinations, capabilities, data ceiling, token budget) and silently allowed
escalation on the rest. A child could be constructed with:

    risk       R1_ROUTINE -> R4_KERNEL
    autonomy   SUGGEST    -> ACT
    deadline   1000       -> 9e9
    usd_hard   1.0        -> 999.0
    tool_calls 5          -> 999

Each individual omission is an oversight. Collectively they are the reason this module
exists: **authority monotonicity must be one predicate, not fifteen scattered comparisons.**
A check repeated at each call site will drift; a check called from each call site cannot.

The lattice covers every dimension of authority. Adding a new one means adding it here, and
the property test then covers it automatically for restrict, delegation and subagent
creation alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..contracts import (
    AUTONOMY_ORDER, Autonomy, Budget, PolicyDenied, RiskTier, RunEnvelope,
    _autonomy_rank,
)

__all__ = ["AuthorityLattice", "AuthorityViolation", "UNRESTRICTED"]


class _Unrestricted(tuple):
    """Sentinel for "any capability not explicitly denied".

    A tuple subclass so existing code that iterates or truth-tests ``allowed_capabilities``
    keeps working, while identity comparison distinguishes "unrestricted" from the empty
    set. Before this, both were ``()``, so a legitimately empty intersection was
    indistinguishable from unlimited authority.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNRESTRICTED"


#: The default value of ``RunEnvelope.allowed_capabilities``.
UNRESTRICTED = _Unrestricted()

#: Placeholder for "the clamp permits nothing". No component may declare this id, so an
#: envelope holding it can invoke nothing at all — which is what a disjoint intersection
#: means, and what a bare empty tuple could not express.
_NO_CAPABILITY = "__no_capability__"


#: Autonomy from most to least permissive, re-exported from ``contracts`` where it is
#: defined beside the enum. Two copies of an ordering is two things to get out of step,
#: and the policy lattice needs the same one.
_AUTONOMY_ORDER: tuple[Autonomy, ...] = AUTONOMY_ORDER


@dataclass(frozen=True, slots=True)
class AuthorityViolation:
    """One dimension on which a child exceeded its parent."""

    dimension: str
    parent: Any
    child: Any

    def __str__(self) -> str:
        return f"{self.dimension}: parent={self.parent!r} child={self.child!r}"


class AuthorityLattice:
    """Decides whether one envelope's authority is contained by another's.

    Every dimension is compared explicitly. Where a dimension is unbounded on the parent
    (no deadline, empty capability allow-list meaning "anything not denied"), containment
    is interpreted in the permissive direction for the parent and the restrictive direction
    for the child — an unbounded parent contains any child, but an unbounded child is only
    contained by an unbounded parent.
    """

    #: Budget dimensions where a child may never exceed its parent. Named rather than
    #: reflected so adding a Budget field is a deliberate decision here.
    BUDGET_DIMENSIONS: tuple[str, ...] = (
        "tokens_soft", "tokens_hard", "usd_soft", "usd_hard", "seconds_soft",
        "seconds_hard", "max_model_calls", "max_tool_calls", "max_delegations")

    @staticmethod
    def _capabilities(envelope: RunEnvelope) -> tuple[str, ...]:
        """Return the capability set, mapping the legacy bare ``()`` onto UNRESTRICTED.

        Envelopes built before this convention existed, or by callers passing ``()``
        directly, still mean "unrestricted" — so the compatibility mapping lives here rather
        than being duplicated at each comparison.
        """
        caps = envelope.allowed_capabilities
        if isinstance(caps, _Unrestricted):
            return UNRESTRICTED
        return UNRESTRICTED if len(caps) == 0 else tuple(caps)

    @classmethod
    def violations(cls, child: RunEnvelope, parent: RunEnvelope) -> list[AuthorityViolation]:
        """Return every dimension on which ``child`` exceeds ``parent``. Empty means safe."""
        out: list[AuthorityViolation] = []

        if child.max_label.sensitivity > parent.max_label.sensitivity:
            out.append(AuthorityViolation("max_label", parent.max_label.sensitivity.name,
                                          child.max_label.sensitivity.name))

        if not child.allowed_destinations <= parent.allowed_destinations:
            extra = child.allowed_destinations - parent.allowed_destinations
            out.append(AuthorityViolation(
                "destinations", sorted(d.name for d in parent.allowed_destinations),
                sorted(d.name for d in extra)))

        # Capability containment, with the empty-list convention handled explicitly.
        #
        # ``allowed_capabilities=()`` historically meant "unrestricted", which collides with
        # the perfectly ordinary result of intersecting two disjoint sets — a maximally
        # narrow child then reads as maximally wide. A property test surfaced it. The
        # convention is now: unrestricted is expressed by ``UNRESTRICTED``, and an empty
        # tuple means exactly what it says.
        parent_caps = cls._capabilities(parent)
        child_caps = cls._capabilities(child)
        if parent_caps is not UNRESTRICTED:
            if child_caps is UNRESTRICTED:
                out.append(AuthorityViolation("capabilities", list(parent_caps),
                                              "unrestricted"))
            elif not set(child_caps) <= set(parent_caps) | {_NO_CAPABILITY}:
                # _NO_CAPABILITY is the bottom of this dimension — it permits nothing, so it
                # is contained by every parent regardless of what the parent allows.
                extra = set(child_caps) - set(parent_caps) - {_NO_CAPABILITY}
                out.append(AuthorityViolation("capabilities", list(parent_caps),
                                              sorted(extra)))

        # A child must inherit at least the parent's denials.
        missing_denials = set(parent.denied_capabilities) - set(child.denied_capabilities)
        if missing_denials:
            out.append(AuthorityViolation("denied_capabilities",
                                          list(parent.denied_capabilities),
                                          f"dropped {sorted(missing_denials)}"))

        if child.risk > parent.risk:
            out.append(AuthorityViolation("risk", parent.risk.name, child.risk.name))

        if _autonomy_rank(child.autonomy) < _autonomy_rank(parent.autonomy):
            out.append(AuthorityViolation("autonomy", parent.autonomy.value,
                                          child.autonomy.value))

        if parent.deadline is not None:
            if child.deadline is None:
                out.append(AuthorityViolation("deadline", parent.deadline, "unbounded"))
            elif child.deadline > parent.deadline:
                out.append(AuthorityViolation("deadline", parent.deadline, child.deadline))

        # Isolation is a requirement, so containment runs the other way from a permission:
        # a parent that demands process-isolated tools is exceeded by a child that does
        # not. Without this, a delegate simply dropped the requirement and ran the same
        # component in the kernel process.
        if parent.require_isolated_tools and not child.require_isolated_tools:
            out.append(AuthorityViolation("require_isolated_tools", True, False))

        # Licence provenance: two plain subset dimensions. They needed no new comparison
        # logic, which is the argument for having one lattice — the work of adding a
        # governed dimension is naming it here and in `meet`, and delegation, `restrict`
        # and the property tests then cover it without being told.
        for dimension in ("allowed_integration_modes", "allowed_license_classes"):
            extra = set(getattr(child, dimension)) - set(getattr(parent, dimension))
            if extra:
                out.append(AuthorityViolation(dimension,
                                              sorted(getattr(parent, dimension)),
                                              sorted(extra)))

        for dimension in cls.BUDGET_DIMENSIONS:
            parent_value = getattr(parent.budget, dimension)
            child_value = getattr(child.budget, dimension)
            if child_value > parent_value:
                out.append(AuthorityViolation(f"budget.{dimension}", parent_value,
                                              child_value))
        return out

    @classmethod
    def is_subset(cls, child: RunEnvelope, parent: RunEnvelope) -> bool:
        """True when ``child`` holds no authority ``parent`` lacks."""
        return not cls.violations(child, parent)

    @classmethod
    def enforce(cls, child: RunEnvelope, parent: RunEnvelope, *,
                operation: str = "restrict") -> RunEnvelope:
        """Return ``child`` if contained by ``parent``, else raise ``PolicyDenied``."""
        violations = cls.violations(child, parent)
        if violations:
            detail = "; ".join(str(v) for v in violations)
            raise PolicyDenied(
                f"{operation} would grant authority the parent run does not hold: {detail}")
        return child

    @classmethod
    def meet(cls, requested: RunEnvelope, parent: RunEnvelope) -> RunEnvelope:
        """Return the greatest lower bound: ``requested`` clamped to ``parent``.

        Used where clamping is kinder than refusing — a profile applied to an already
        narrow run should not fail merely because the profile is broader.
        """
        from dataclasses import replace as _replace

        # Only the dimensions the lattice governs are min-ed; any other Budget field is
        # carried over from the request unchanged via dataclasses.replace, so adding a
        # non-authority field to Budget cannot break this.
        budget = _replace(requested.budget, **{
            dimension: min(getattr(requested.budget, dimension),
                           getattr(parent.budget, dimension))
            for dimension in cls.BUDGET_DIMENSIONS})

        deadline = requested.deadline
        if parent.deadline is not None:
            deadline = parent.deadline if deadline is None else min(deadline, parent.deadline)

        # Capabilities need the empty-list convention handled explicitly: an empty list
        # means "unrestricted", so intersecting naively would turn a bounded parent into an
        # unrestricted child. A property test caught this — the example-based tests never
        # called meet() with a bounded parent and an unrestricted request.
        parent_caps = cls._capabilities(parent)
        requested_caps = cls._capabilities(requested)
        if parent_caps is UNRESTRICTED:
            capabilities = requested_caps
        elif requested_caps is UNRESTRICTED:
            capabilities = tuple(parent_caps)
        else:
            capabilities = tuple(c for c in requested_caps if c in set(parent_caps))
            if not capabilities:
                # Disjoint sets: the clamp legitimately permits nothing. A bare () would be
                # read as UNRESTRICTED by the compatibility mapping, so name the empty case
                # with a capability no component declares.
                capabilities = (_NO_CAPABILITY,)

        return _replace(
            requested,
            max_label=(requested.max_label
                       if requested.max_label.sensitivity <= parent.max_label.sensitivity
                       else parent.max_label),
            allowed_destinations=requested.allowed_destinations & parent.allowed_destinations,
            allowed_capabilities=capabilities,
            denied_capabilities=tuple(dict.fromkeys(
                requested.denied_capabilities + parent.denied_capabilities)),
            risk=min(requested.risk, parent.risk),
            autonomy=(requested.autonomy
                      if _autonomy_rank(requested.autonomy) >= _autonomy_rank(parent.autonomy)
                      else parent.autonomy),
            require_isolated_tools=(requested.require_isolated_tools
                                    or parent.require_isolated_tools),
            allowed_integration_modes=tuple(
                m for m in requested.allowed_integration_modes
                if m in set(parent.allowed_integration_modes)),
            allowed_license_classes=tuple(
                c for c in requested.allowed_license_classes
                if c in set(parent.allowed_license_classes)),
            deadline=deadline, budget=budget)
