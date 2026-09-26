"""Execution status and component lifecycle — replaces the v1 boolean `ok`.

The v1 audit's central finding was that a boolean cannot distinguish "the call
succeeded" from "we worked out where the call *would* go". A native connector
route with no bound dispatcher returned ok=True, so a demo run reported 3/3
successes having executed nothing.

`ExecutionStatus` makes that distinction unrepresentable: RESOLVED and SUCCEEDED
are different states, and only SUCCEEDED (and, partially, DEGRADED) counts as
execution. `LifecycleState` does the same job for components — being catalogued
is not being loaded, and being loaded is not being executable.
"""

from __future__ import annotations

from enum import Enum


class ExecutionStatus(str, Enum):
    """Terminal or intermediate state of a single capability invocation."""

    # --- pre-execution (no upstream work has happened yet)
    DISCOVERED = "DISCOVERED"    # known to the registry
    RESOLVED = "RESOLVED"        # backend/route determined, nothing invoked
    LOADED = "LOADED"            # implementation imported/attached
    READY = "READY"              # dependencies satisfied, invocable now
    # --- execution
    RUNNING = "RUNNING"
    # --- terminal
    SUCCEEDED = "SUCCEEDED"      # ran and produced a result
    DEGRADED = "DEGRADED"        # ran, but with a documented shortfall
    FAILED = "FAILED"            # ran and errored
    UNAVAILABLE = "UNAVAILABLE"  # cannot run here (missing dep/runtime/install)
    DENIED = "DENIED"            # blocked by policy (license/permission)
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def executed(self) -> bool:
        """True only if upstream work actually ran."""
        return self in (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED,
                        ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT)

    @property
    def successful(self) -> bool:
        """True only for states that produced a usable result."""
        return self in (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED)


_TERMINAL = frozenset({
    ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED, ExecutionStatus.FAILED,
    ExecutionStatus.UNAVAILABLE, ExecutionStatus.DENIED, ExecutionStatus.TIMEOUT,
    ExecutionStatus.CANCELLED,
})


class RunOutcome(str, Enum):
    """Outcome of a whole task run."""

    SUCCESS = "SUCCESS"                  # every step succeeded
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"  # some succeeded, some did not
    FAILED = "FAILED"                    # nothing succeeded
    DEGRADED = "DEGRADED"                # nothing executed; only resolved

    @property
    def accepted(self) -> bool:
        return self is RunOutcome.SUCCESS


class ScientificVerdict(str, Enum):
    """Whether the run's *findings* were accepted — orthogonal to whether it ran.

    A validator that executes cleanly and returns a score of 0.0 is a successful
    execution reporting a failed validation. Collapsing the two let a run whose
    critique said "validation failed" still report SUCCESS with accepted=True,
    which is precisely the false positive a scientific harness must not produce.
    """

    ACCEPTED = "ACCEPTED"          # the critique accepted the result
    REJECTED = "REJECTED"          # the critique ran and rejected the result
    INCONCLUSIVE = "INCONCLUSIVE"  # no critique, or not enough evidence to judge

    @property
    def accepted(self) -> bool:
        return self is ScientificVerdict.ACCEPTED


class LifecycleState(str, Enum):
    """Where a component sits between "catalogued" and "executable"."""

    DISCOVERED = "DISCOVERED"      # found by a provider / present in the index
    REGISTERED = "REGISTERED"      # manifest validated and in the registry
    AVAILABLE = "AVAILABLE"        # policy permits it in principle
    RESOLVED = "RESOLVED"          # dependency DAG resolved
    LOADED = "LOADED"              # implementation imported
    READY = "READY"                # invocable right now
    UNAVAILABLE = "UNAVAILABLE"    # blocked; see blocking_reason
    QUARANTINED = "QUARANTINED"    # failed validation; must not be used


#: Legal forward transitions. Anything else raises.
_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DISCOVERED: frozenset({LifecycleState.REGISTERED, LifecycleState.QUARANTINED,
                                          LifecycleState.UNAVAILABLE}),
    LifecycleState.REGISTERED: frozenset({LifecycleState.AVAILABLE, LifecycleState.UNAVAILABLE,
                                          LifecycleState.QUARANTINED}),
    LifecycleState.AVAILABLE: frozenset({LifecycleState.RESOLVED, LifecycleState.UNAVAILABLE}),
    LifecycleState.RESOLVED: frozenset({LifecycleState.LOADED, LifecycleState.UNAVAILABLE}),
    LifecycleState.LOADED: frozenset({LifecycleState.READY, LifecycleState.UNAVAILABLE,
                                      LifecycleState.QUARANTINED}),
    LifecycleState.READY: frozenset({LifecycleState.LOADED, LifecycleState.UNAVAILABLE}),
    LifecycleState.UNAVAILABLE: frozenset({LifecycleState.DISCOVERED, LifecycleState.REGISTERED,
                                           LifecycleState.AVAILABLE, LifecycleState.RESOLVED}),
    LifecycleState.QUARANTINED: frozenset(),
}


class IllegalTransition(RuntimeError):
    """Raised when a component is moved between incompatible lifecycle states."""


def assert_transition(old: LifecycleState, new: LifecycleState) -> None:
    """Guard a lifecycle transition; raise IllegalTransition when not permitted."""
    if new not in _TRANSITIONS.get(old, frozenset()):
        raise IllegalTransition(f"{old.value} -> {new.value} is not a legal transition")


def legal_transitions(state: LifecycleState) -> frozenset[LifecycleState]:
    return _TRANSITIONS.get(state, frozenset())
