"""Adapter interface — how the agent reaches a capability without copying code.

Every capability is invoked through an adapter. Adapters exist so that a
capability whose upstream project grants no license can still be *used*
(invoked in place, in its own process or over its own API) while never being
vendored into this package.

An adapter declares:

* ``name``            - stable adapter id, recorded in provenance
* ``integration_mode`` - "vendor" | "adapter-only" | "native"
* ``requires_network`` - whether calls leave the machine
* ``can_handle()``     - which capabilities it serves
* ``invoke()``         - the actual call

`LicenseError` is raised when a caller tries to vendor an adapter-only
capability, so the legal boundary is enforced in code rather than in a README.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Mapping

from ..status import ExecutionStatus


class AdapterError(RuntimeError):
    """Raised when an adapter cannot service a call."""


class LicenseError(AdapterError):
    """Raised when an operation would violate an upstream license boundary."""


@dataclass
class CallResult:
    """Outcome of one capability invocation.

    `status` replaces the v1 boolean `ok`. The distinction matters: a native
    route that was merely *resolved* (no dispatcher bound) is not a success, and
    with a boolean there was no way to say so — v1 reported 3/3 successes having
    executed nothing. `ok` is kept as a read-only property for compatibility and
    is True only for genuinely successful states.
    """

    capability: str
    adapter: str
    status: ExecutionStatus = ExecutionStatus.RESOLVED
    value: Any = None
    error: str | None = None
    duration_s: float = 0.0
    metadata: Mapping[str, Any] | None = None
    authorization: Any = None

    @property
    def ok(self) -> bool:
        """True only when the call actually produced a result."""
        return self.status.successful

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED

    @property
    def executed(self) -> bool:
        """True when upstream work ran, whether or not it worked."""
        return self.status.executed


class Adapter(abc.ABC):
    """Base class for all capability adapters."""

    #: stable identifier recorded in the provenance log
    name: str = "adapter"
    #: one of "vendor", "adapter-only", "native"
    integration_mode: str = "adapter-only"
    #: whether invocations require network access
    requires_network: bool = False

    @abc.abstractmethod
    def can_handle(self, capability: Any) -> bool:
        """Return True when this adapter can invoke ``capability``."""

    @abc.abstractmethod
    def invoke(self, capability: Any, **kwargs: Any) -> CallResult:
        """Invoke ``capability`` and return a :class:`CallResult`."""

    def assert_may_vendor(self) -> None:
        """Guard against reusing code from an unlicensed upstream project."""
        if self.integration_mode == "adapter-only":
            raise LicenseError(
                f"adapter {self.name!r} is adapter-only: its upstream project grants no "
                "license, so its implementation may be invoked in place but never copied."
            )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name} mode={self.integration_mode}>"
