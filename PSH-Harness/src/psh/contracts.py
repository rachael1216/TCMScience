"""The nine frozen abstractions. This module and ``psh.labels`` are not replaceable.

The review this package responds to made one architectural correction that governs the
whole design:

    "Everything is a Component" -> "Everything *Extensible* is a Component"

The trusted kernel, the policy root and the event store cannot be swappable in the same
way a skill is, because a component that can replace the thing enforcing policy is not
governed by it. So: components are extensible; the kernel is not. Nothing in this file
may be overridden by a manifest.

The nine abstractions, in the order the review prioritised them:

1. ``RunEnvelope``       - the classified, budgeted authority for one unit of work.
2. ``ComponentManifest`` - what a capability is, costs, risks and requires.
3. ``ContextProjection`` - compiled context for one worker, not a shared transcript.
4. ``DelegationContract``- the single protocol behind handoff/subagent/nested harness.
5. ``EventEnvelope``     - references and hashes, never payloads.
6. ``DataLabel``         - in ``psh.labels`` (the taint lattice).
7. ``ModelProfile``      - capability *and* data policy per model.
8. ``ArtifactRef``       - content-addressed output.
9. ``HarnessAdapter``    - in ``psh.protocols`` (composition boundary).

Storage model, also corrected by the review from "everything mutable is a file":

    authored state  -> Git workspace        (prompts, skills, policies, docs)
    runtime state   -> event/checkpoint store
    large outputs   -> content-addressed artifact store
    queryable state -> index/graph store
    secrets         -> secret manager, outside all four
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .labels import DataLabel, Destination, Labeled, Sensitivity
from .licensing import (
    INTEGRATION_MODES, LICENSE_CLASSES, LicenseDecision, license_ruling, normalise_mode,
)

__all__ = [
    "PSHError", "PolicyDenied", "EgressDenied", "BudgetExhausted", "ApprovalRequired",
    "ApprovalDenied", "VerificationFailed", "CapabilityUnavailable", "BrokerBypass",
    "ContractViolation", "ToolTimeout", "OperationUnresolved", "DegradedResult",
    "RiskTier", "Autonomy", "AUTONOMY_ORDER", "RunEnvelope", "Budget", "Principal",
    "ComponentKind", "ComponentManifest", "ContextProjection", "ContextItem",
    "DelegationContract", "EventEnvelope", "ModelProfile", "ArtifactRef",
    "new_id", "utc_now", "content_hash",
]


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}_{raw}" if prefix else raw


def utc_now() -> float:
    return time.time()


def content_hash(payload: Any) -> str:
    """Stable SHA-256 over canonical JSON, for references and integrity chains."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- errors

class PSHError(Exception):
    """Base class for every error raised by this package."""


class PolicyDenied(PSHError):
    """A policy refused an operation. The message must state which policy and why."""


class EgressDenied(PolicyDenied):
    """A value's label forbids the destination it was about to reach.

    Carries the label and destination so the caller can explain the refusal without
    re-deriving it — and, critically, without quoting the offending data.
    """

    def __init__(self, message: str, *, label: DataLabel | None = None,
                 destination: Destination | None = None) -> None:
        super().__init__(message)
        self.label = label
        self.destination = destination


class BudgetExhausted(PSHError):
    """A hard ceiling was reached. The run must stop, not warn."""


class ApprovalRequired(PSHError):
    """A human decision is required and no handler was available: fail closed."""


class ApprovalDenied(PSHError):
    """A human explicitly refused the operation."""


class VerificationFailed(PSHError):
    """An output failed the final gate or a claim failed support verification."""


class CapabilityUnavailable(PSHError):
    """No capability satisfied the request under the active policy."""


class BrokerBypass(PSHError):
    """Something attempted execution without going through the broker.

    This is an internal invariant violation rather than a user-facing condition: the
    review's requirement is that no tool call, model call, delegation or nested harness
    may bypass the trusted path, and this error exists so that requirement is testable.
    """


class ContractViolation(PSHError):
    """A component returned something its manifest said it would not."""


class ToolTimeout(ContractViolation):
    """A component did not finish within its declared ``timeout_s`` and was stopped.

    A timeout is a *kind* of contract violation, so a retry policy naming
    ``ContractViolation`` still covers it and one naming ``ToolTimeout`` covers only this.
    It is its own class because a call that timed out may have done its work: a
    side-effecting component killed mid-flight is exactly the case the operation ledger
    has to record as unknown rather than failed.
    """


class OperationUnresolved(PSHError):
    """A side-effecting operation's outcome is unknown and re-running it is not safe.

    Raised when the operation ledger records that an earlier attempt of a non-idempotent
    call started and never reported, or succeeded without its result reaching this run,
    so neither "it ran" nor "it did not run" can be asserted. The task fails without a
    retry and the loop escalates: reconciling a side effect is a decision for whoever
    owns it, not for a retry policy.
    """


@dataclass(frozen=True, slots=True)
class DegradedResult:
    """A component's value together with the shortfall it ran under.

    BioScience reports ``DEGRADED`` for a call that produced a result with a documented
    limitation — a fallback dataset, a truncated page, an estimate where a measurement
    was asked for. The bridge used to hand the value back as if the call had succeeded,
    so the shortfall was visible in BioScience's event log and nowhere in PSH's. A
    component returns this wrapper instead: the broker records the result as
    ``degraded``, the loop carries the caveat on the task, and a release lists it as a
    limitation.
    """

    value: Any
    reason: str = ""


# ------------------------------------------------------------------- run envelope

class RiskTier(IntEnum):
    """Risk of the work, which drives autonomy and approval requirements.

    Mirrors the review's R0-R4 evolution tiers, applied to runs rather than to code
    changes. R4 exists so that "modify the kernel" is representable and always refused
    to an automated principal.
    """

    R0_TRIVIAL = 0
    R1_ROUTINE = 1
    R2_CONSEQUENTIAL = 2
    R3_CLINICAL = 3
    R4_KERNEL = 4


class Autonomy(str, Enum):
    OBSERVE = "observe"            # read-only; nothing changes state
    SUGGEST = "suggest"            # may propose, may not act
    ACT_WITH_APPROVAL = "act_with_approval"
    ACT = "act"


#: Autonomy from most to least permissive. Comparison is by index, so a LOWER index is
#: strictly MORE authority. It lives here, beside the enum, because three modules need the
#: ordering — the authority lattice, the policy lattice and ``ComponentManifest`` — and a
#: second copy of an ordering is a second thing to get out of step.
AUTONOMY_ORDER: tuple["Autonomy", ...] = (
    Autonomy.ACT, Autonomy.ACT_WITH_APPROVAL, Autonomy.SUGGEST, Autonomy.OBSERVE)


def _autonomy_rank(value: "Autonomy") -> int:
    return AUTONOMY_ORDER.index(value)


#: Destinations that leave the machine. Named once so a manifest, a gateway and the
#: isolated executor cannot disagree about which destinations are remote.
_REMOTE_DESTINATIONS: frozenset = frozenset(
    {Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE})

#: A component id must be usable as exactly one filesystem path component, because the
#: isolated executor makes the component's working directory by joining it onto the
#: sandbox root. Leading dot, slash, backslash and NUL are all excluded by construction.
_VALID_COMPONENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}")


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is acting. Every event and every approval names one."""

    id: str
    kind: str = "human"            # human | agent | system
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"human", "agent", "system"}:
            raise ValueError(f"unknown principal kind {self.kind!r}")


@dataclass(frozen=True, slots=True)
class Budget:
    """Token, cost, time and call ceilings for one run.

    Soft limits warn; hard limits raise ``BudgetExhausted``. The distinction is the
    review's point that a budget which only reports is a metric, not a control.
    """

    tokens_soft: int = 120_000
    tokens_hard: int = 400_000
    usd_soft: float = 2.0
    usd_hard: float = 10.0
    seconds_soft: float = 30 * 60
    seconds_hard: float = 90 * 60
    max_model_calls: int = 60
    max_tool_calls: int = 200
    max_delegations: int = 12

    def child(self, fraction: float = 0.25) -> "Budget":
        """Return a reduced budget for a delegated worker.

        A child can never exceed its parent, which is what stops a delegation tree from
        multiplying a budget by fanning out.
        """
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        return Budget(
            tokens_soft=int(self.tokens_soft * fraction),
            tokens_hard=int(self.tokens_hard * fraction),
            usd_soft=self.usd_soft * fraction, usd_hard=self.usd_hard * fraction,
            seconds_soft=self.seconds_soft * fraction,
            seconds_hard=self.seconds_hard * fraction,
            max_model_calls=max(1, int(self.max_model_calls * fraction)),
            max_tool_calls=max(1, int(self.max_tool_calls * fraction)),
            max_delegations=max(0, int(self.max_delegations * fraction)))


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    """The authority under which one unit of work executes.

    Nothing executes without one. It is created once, at the entry to the trusted path,
    from the request's classification and the active profile — and it is frozen, so a
    component cannot widen its own authority mid-run. Narrowing is allowed
    (``restrict``); widening is not representable.
    """

    run_id: str = field(default_factory=lambda: new_id("run"))
    task_id: str = ""
    project_id: str = ""
    principal: Principal = field(default_factory=lambda: Principal(id="local", kind="human"))
    risk: RiskTier = RiskTier.R1_ROUTINE
    autonomy: Autonomy = Autonomy.ACT_WITH_APPROVAL
    max_label: DataLabel = field(default_factory=lambda: DataLabel(Sensitivity.PHI))
    allowed_destinations: frozenset[Destination] = frozenset({
        Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT})
    allowed_capabilities: tuple[str, ...] = ()
    denied_capabilities: tuple[str, ...] = ()
    budget: Budget = field(default_factory=Budget)
    profile: str = "default"
    deadline: float | None = None
    #: Whether this run refuses components that are not process-isolated. It lives on the
    #: envelope rather than only on the kernel because the kernel holds ONE policy while a
    #: run may execute under a narrower one: a per-run policy that turns isolation on was
    #: previously declared on the snapshot and enforced nowhere, because the broker read
    #: the flag it was constructed with. An authority dimension that travels with the run
    #: is enforced by whoever holds the run, which is the only arrangement that survives
    #: delegation.
    require_isolated_tools: bool = False
    #: Licence provenance, adopted from BioScience-Harness. Both default to everything the
    #: table knows, so the *table* does the refusing (unlicensed code may not be vendored)
    #: and a profile narrows further ("this work may not vendor at all"). Stated as sets
    #: rather than a flag because they are subset dimensions, which is what lets the
    #: authority lattice govern them with no new comparison logic.
    allowed_integration_modes: tuple[str, ...] = INTEGRATION_MODES
    allowed_license_classes: tuple[str, ...] = LICENSE_CLASSES
    parent_run_id: str | None = None
    created_at: float = field(default_factory=utc_now)

    def permits_capability(self, name: str) -> bool:
        """Deny list wins over allow list; an empty allow list means 'any not denied'."""
        if name in self.denied_capabilities:
            return False
        return not self.allowed_capabilities or name in self.allowed_capabilities

    def permits_destination(self, destination: Destination) -> bool:
        return destination in self.allowed_destinations

    def restrict(self, **kw: Any) -> "RunEnvelope":
        """Return a narrower envelope. Any widening is refused.

        Every dimension is checked by ``AuthorityLattice``, which is the single place
        authority containment is defined. v0.1 checked four dimensions here and silently
        permitted escalation of risk, autonomy, deadline, cost and call ceilings — the
        predictable result of open-coding the comparison at the call site instead of
        deferring to one predicate.
        """
        from .kernel.authority import AuthorityLattice

        budget = kw.pop("budget", None)
        candidate = RunEnvelope(
            run_id=new_id("run"), task_id=kw.pop("task_id", self.task_id),
            project_id=self.project_id, principal=kw.pop("principal", self.principal),
            risk=kw.pop("risk", self.risk), autonomy=kw.pop("autonomy", self.autonomy),
            max_label=kw.pop("max_label", None) or self.max_label,
            allowed_destinations=frozenset(kw.pop("allowed_destinations",
                                                  self.allowed_destinations)),
            allowed_capabilities=tuple(kw.pop("allowed_capabilities",
                                              self.allowed_capabilities)),
            denied_capabilities=tuple(dict.fromkeys(
                self.denied_capabilities + tuple(kw.pop("denied_capabilities", ())))),
            budget=budget if budget is not None else self.budget.child(),
            profile=self.profile, deadline=kw.pop("deadline", self.deadline),
            # A child may turn isolation ON and never off, so the parent's requirement is
            # the floor rather than a default the child can drop by passing False.
            require_isolated_tools=bool(kw.pop("require_isolated_tools", False)
                                        or self.require_isolated_tools),
            allowed_integration_modes=tuple(kw.pop("allowed_integration_modes",
                                                   self.allowed_integration_modes)),
            allowed_license_classes=tuple(kw.pop("allowed_license_classes",
                                                 self.allowed_license_classes)),
            parent_run_id=self.run_id)

        return AuthorityLattice.enforce(candidate, self, operation="restrict")

    @property
    def expired(self) -> bool:
        return self.deadline is not None and utc_now() > self.deadline


# --------------------------------------------------------------- component manifest

class ComponentKind(str, Enum):
    TOOL = "tool"
    SKILL = "skill"
    WORKFLOW = "workflow"
    AGENT = "agent"
    EVALUATOR = "evaluator"
    DATASET = "dataset"
    KNOWLEDGE = "knowledge"
    MEMORY_PROVIDER = "memory_provider"
    HARNESS = "harness"


@dataclass(frozen=True, slots=True)
class ComponentManifest:
    """What a capability is, requires, risks and costs.

    The review's point about retrieval is encoded in the field set: selection is not
    embedding similarity alone but ``semantic relevance x availability x policy
    compatibility x quality x cost x latency x historical success``. Every one of those
    factors needs a field here, which is why ``success_rate`` and ``max_label`` are part
    of the manifest rather than of the registry.
    """

    id: str
    name: str
    kind: ComponentKind
    version: str = "0.1.0"
    api_version: str = "1"
    publisher: str = ""
    description: str = ""
    intents: tuple[str, ...] = ()
    domain: str = ""
    tags: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    #: ``python`` runs the component object in the kernel's own process; ``subprocess``
    #: runs ``entrypoint`` through the kernel's ``IsolatedRunner`` — a clean environment
    #: behind the kernel-owned egress proxy. The distinction is load-bearing rather than
    #: cosmetic: only the second is process-isolated, and ``ExecutionBroker`` records which
    #: of the two actually ran so the claim is auditable per call.
    backend: str = "python"
    entrypoint: str = ""
    #: Hosts this component needs when it runs isolated. They are offered to the egress
    #: proxy only when the run envelope also permits a remote destination, so the manifest
    #: can narrow the run's network reach and never widen it.
    allowed_hosts: tuple[str, ...] = ()
    timeout_s: float = 120.0
    memory_mb: int = 2048
    idempotent: bool = True
    # risk
    max_label: Sensitivity = Sensitivity.RESEARCH_DEIDENTIFIED
    destinations: tuple[Destination, ...] = (Destination.LOCAL_COMPUTE,)
    requires_network: bool = False
    requires_filesystem: bool = False
    requires_secrets: tuple[str, ...] = ()
    mutates: bool = False
    #: The least autonomy a run must hold for this component to be usable. ``OBSERVE`` is
    #: the default because it is the *weakest* requirement: it was ``ACT`` — the strongest —
    #: which would have made every manifest that never thought about the field demand full
    #: autonomy, and is why the field could not be enforced as written. It is enforced in
    #: ``compatible_with`` now, so a component that genuinely needs ``ACT`` says so and is
    #: excluded from an ``OBSERVE`` run.
    min_autonomy: Autonomy = Autonomy.OBSERVE
    human_approval: bool = False
    risk_tier: RiskTier = RiskTier.R1_ROUTINE
    # economics and quality
    expected_tokens: int = 0
    expected_usd: float = 0.0
    expected_latency_s: float = 1.0
    success_rate: float = 1.0
    known_limits: tuple[str, ...] = ()
    #: SPDX id of the upstream this capability comes from, and HOW it is integrated.
    #: ``federated`` is the default because it is the weakest claim — invoking upstream in
    #: its own process is use rather than redistribution, so a manifest that never
    #: considered the question does not accidentally assert a right to copy code.
    license_spdx: str = ""
    integration_mode: str = "federated"
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("a component manifest requires an id and a name")
        if not _VALID_COMPONENT_ID.fullmatch(self.id) or self.id in (".", ".."):
            # The id is not only a key: the isolated executor joins it onto the sandbox
            # root to make the component's working directory. ``id="../../escaped"`` put
            # that directory outside the sandbox, and an absolute id discarded the root
            # entirely, because ``Path("/a") / "/tmp/x"`` is ``/tmp/x``. Constraining the
            # id to one safe path component is the first of two defences; the executor
            # still checks containment after resolution.
            raise ValueError(
                f"component id {self.id!r} is not a valid identifier: ids must match "
                f"{_VALID_COMPONENT_ID.pattern} and are used as a single path component")
        if not 0.0 <= self.success_rate <= 1.0:
            raise ValueError("success_rate must be in [0, 1]")
        if self.requires_network and not any(d in _REMOTE_DESTINATIONS
                                             for d in self.destinations):
            # This was written ``Destination.LOCAL_COMPUTE == self.destinations == ()``,
            # a chained comparison requiring an enum member to equal a tuple — never true,
            # so the check never fired and ``requires_network=True, destinations=()`` was
            # accepted. The gateway then defaulted such a component to LOCAL_COMPUTE, so
            # the audit record said "local" about a component that declared it needs the
            # network.
            raise ValueError(
                f"component {self.id!r} declares requires_network=True but names no remote "
                f"destination; declare PUBLIC_REMOTE or TRUSTED_REMOTE in destinations so "
                "the gateway and the audit record agree with the manifest")
        if normalise_mode(self.integration_mode) not in INTEGRATION_MODES:
            raise ValueError(
                f"component {self.id!r} declares integration_mode "
                f"{self.integration_mode!r}; it must be one of {list(INTEGRATION_MODES)} "
                "(or the 'adapter-only' alias for federated)")
        if self.runs_isolated and not self.entrypoint:
            raise ValueError(
                f"component {self.id!r} declares backend={self.backend!r} but no entrypoint; "
                "an isolated component is executed as a command line, so there must be one")

    @property
    def runs_isolated(self) -> bool:
        """Whether the broker executes this component in a subprocess.

        A component whose backend is ``python`` runs inside the kernel process and is
        therefore *not* contained by anything the kernel can enforce: it can read the
        kernel's environment, open a socket, or call a provider directly. The honest
        statement of the boundary is per component, which is what this property makes
        available to the broker, the audit event and the report.
        """
        return self.backend in ("subprocess", "isolated")

    def compatible_with(self, envelope: RunEnvelope) -> tuple[bool, str]:
        """Check this component against a run's authority.

        Returns ``(ok, reason)`` rather than raising: the resolver needs to rank many
        candidates and report why each was excluded, not abort on the first mismatch.
        """
        if not envelope.permits_capability(self.id):
            return False, f"capability {self.id!r} is not permitted by this run"
        if self.risk_tier > envelope.risk:
            return False, (f"component risk {self.risk_tier.name} exceeds run risk "
                           f"{envelope.risk.name}")
        for dest in self.destinations:
            if not envelope.permits_destination(dest):
                return False, f"destination {dest.name} is not permitted by this run"
        if self.mutates and envelope.autonomy in (Autonomy.OBSERVE, Autonomy.SUGGEST):
            return False, f"component mutates state but autonomy is {envelope.autonomy.value}"
        ruling = license_ruling(self.license_spdx, self.integration_mode)
        if not ruling.allowed:
            return False, ruling.reason
        if ruling.mode not in envelope.allowed_integration_modes:
            return False, (f"component is integrated as {ruling.mode!r} and this run permits "
                           f"{sorted(envelope.allowed_integration_modes)}")
        if ruling.license_class not in envelope.allowed_license_classes:
            return False, (f"component licence {self.license_spdx or 'unlicensed'} is "
                           f"{ruling.license_class!r} and this run permits "
                           f"{sorted(envelope.allowed_license_classes)}")
        if _autonomy_rank(envelope.autonomy) > _autonomy_rank(self.min_autonomy):
            # ``min_autonomy`` was a declared field that no predicate read, so a component
            # requiring ACT was reported compatible with an OBSERVE run.
            return False, (f"component requires at least {self.min_autonomy.value} autonomy "
                           f"but this run holds {envelope.autonomy.value}")
        return True, "compatible"


# ------------------------------------------------------------- context projection

@dataclass(frozen=True, slots=True)
class ContextItem:
    """One labelled piece of compiled context, with the reason it was included."""

    kind: str                      # instruction | memory | evidence | manifest | turn
    content: str
    label: DataLabel = field(default_factory=DataLabel)
    tokens: int = 0
    source_ref: str = ""
    score: float = 0.0

    def __post_init__(self) -> None:
        if self.tokens == 0 and self.content:
            object.__setattr__(self, "tokens", max(1, len(self.content) // 4))


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """Compiled context for exactly one worker.

    The review's argument is that context is a runtime build product, not accumulated
    chat history — and that each delegated worker gets its *own* projection. So this type
    carries an aggregate label: the join of everything included. That single field is
    what lets the model gateway decide whether this context may reach a given provider,
    without re-scanning the assembled text.
    """

    items: tuple[ContextItem, ...] = ()
    token_budget: int = 0
    label: DataLabel = field(default_factory=DataLabel)
    dropped: int = 0
    compiled_at: float = field(default_factory=utc_now)
    run_id: str = ""

    @property
    def tokens(self) -> int:
        return sum(i.tokens for i in self.items)

    @property
    def within_budget(self) -> bool:
        return self.token_budget == 0 or self.tokens <= self.token_budget

    def render(self) -> str:
        """Assemble the projection into prompt text, grouped by kind."""
        order = ("instruction", "manifest", "memory", "evidence", "turn")
        parts: list[str] = []
        for kind in order:
            chunk = [i for i in self.items if i.kind == kind]
            if chunk:
                parts.append("\n\n".join(i.content for i in chunk))
        return "\n\n".join(parts)

    def summary(self) -> str:
        by_kind: dict[str, int] = {}
        for i in self.items:
            by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
        return (f"{len(self.items)} items ({by_kind}), {self.tokens} tokens, "
                f"label={self.label}, dropped={self.dropped}")


# ------------------------------------------------------------ delegation contract

@dataclass(frozen=True, slots=True)
class DelegationContract:
    """The one protocol behind every kind of delegation.

    The review's insight: handoff, subagent, parallel worker, remote agent and nested
    harness are not five abstractions but one contract with five *backends*. Everything a
    delegate is allowed to do is stated here, so the delegation gateway has a single
    object to check rather than five code paths to keep consistent.
    """

    task_id: str
    objective: str
    envelope: RunEnvelope
    projection: ContextProjection | None = None
    input_refs: tuple[str, ...] = ()
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    acceptance_tests: tuple[str, ...] = ()
    evidence_required: bool = False
    backend: str = "local_agent"
    return_policy: str = "structured"
    id: str = field(default_factory=lambda: new_id("dlg"))

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("a delegation contract requires an objective")
        if self.evidence_required and not self.output_schema:
            raise ValueError(
                "evidence_required implies a structured return; supply an output_schema")


# ------------------------------------------------------------------ event envelope

def _forbidden_keys_deep(value: Any, forbidden: Sequence[str], _depth: int = 0
                         ) -> list[str]:
    """Find content-bearing keys at any depth.

    v0.1 checked only top-level keys, so ``{"nested": {"note": "...MRN..."}}`` was accepted.
    Nesting is the normal shape of structured detail, so a top-level check is close to no
    check at all.
    """
    if _depth > 8:
        return ["<too deeply nested to verify>"]
    out: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                out.append(str(key))
            out.extend(_forbidden_keys_deep(item, forbidden, _depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            out.extend(_forbidden_keys_deep(item, forbidden, _depth + 1))
    return out


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """One hash-chained event. References and hashes only — never payloads.

    The review is explicit that events must not carry raw data. That keeps the event
    store small enough to verify quickly, and means the integrity chain never becomes a
    second copy of the sensitive material it describes.
    """

    seq: int
    event_type: str
    run_id: str
    principal_id: str
    prev_hash: str
    hash: str = ""
    parent_event_id: str | None = None
    event_id: str = field(default_factory=lambda: new_id("evt"))
    component_id: str = ""
    model_id: str = ""
    policy_version: str = ""
    input_refs: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    cost_usd: float = 0.0
    tokens: int = 0
    latency_s: float = 0.0
    status: str = "ok"
    detail: Mapping[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=utc_now)

    #: Keys that would smuggle raw content into an event. Checked on construction.
    FORBIDDEN_KEYS = ("content", "text", "body", "prompt", "raw", "payload",
                      "value", "note", "message", "snippet", "arguments")

    def __post_init__(self) -> None:
        offenders = _forbidden_keys_deep(self.detail, self.FORBIDDEN_KEYS)
        if offenders:
            raise ValueError(
                f"event detail may not carry raw content keys {offenders}; store a "
                "reference or hash instead — events must not become a second copy of "
                "the data they describe")
        if not self.hash:
            object.__setattr__(self, "hash", self.compute_hash())

    #: Schema version of the hashed envelope. Bumping it invalidates old chains by design:
    #: a verifier must never silently accept a record hashed under different rules.
    ENVELOPE_SCHEMA = "2"

    def _body(self) -> Mapping[str, Any]:
        """The canonical immutable envelope covered by the hash.

        v0.1 omitted ``at``, ``latency_s``, ``parent_event_id`` and ``event_id``, so those
        fields could be edited in place and verification still reported the chain intact —
        a reviewer demonstrated it. Anything that is part of the record's meaning must be
        part of what the hash protects, so the envelope now covers every immutable field and
        ``detail`` enters as its own hash.
        """
        return {
            "schema": self.ENVELOPE_SCHEMA,
            "event_id": self.event_id,
            "seq": self.seq, "event_type": self.event_type, "run_id": self.run_id,
            "parent_event_id": self.parent_event_id,
            "principal_id": self.principal_id, "component_id": self.component_id,
            "model_id": self.model_id, "policy_version": self.policy_version,
            "at": self.at, "latency_s": self.latency_s,
            "input_refs": list(self.input_refs), "output_refs": list(self.output_refs),
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs), "labels": list(self.labels),
            "cost_usd": self.cost_usd, "tokens": self.tokens, "status": self.status,
            "detail_hash": content_hash(dict(self.detail)),
        }

    def compute_hash(self) -> str:
        return content_hash({"prev": self.prev_hash, "body": self._body()})

    @property
    def intact(self) -> bool:
        return self.hash == self.compute_hash()


# ------------------------------------------------------------------- model profile

@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A model's capability *and* its data policy.

    The review's objection to ordinary routers is that they select on price and quality
    while ignoring whether a given provider may lawfully receive the data. So the data
    fields here are not optional metadata: ``max_label`` and ``destination`` are consulted
    before any quality ranking, and a model that fails the policy filter is never ranked
    at all.
    """

    id: str
    provider: str
    destination: Destination = Destination.PUBLIC_REMOTE
    max_label: Sensitivity = Sensitivity.PUBLIC
    reasoning: float = 0.5
    coding: float = 0.5
    tool_use: float = 0.5
    multimodal: bool = False
    structured_output: bool = True
    context_window: int = 128_000
    usd_per_1k_input: float = 0.003
    usd_per_1k_output: float = 0.015
    latency_s: float = 2.0
    reliability: float = 0.95
    region: str = ""
    retention: str = "unknown"
    notes: str = ""

    def may_receive(self, label: DataLabel) -> bool:
        return label.sensitivity <= self.max_label

    def estimated_usd(self, in_tokens: int, out_tokens: int) -> float:
        return (in_tokens / 1000 * self.usd_per_1k_input
                + out_tokens / 1000 * self.usd_per_1k_output)


# -------------------------------------------------------------------- artifact ref

@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A content-addressed output. Large content lives on disk, not in context."""

    digest: str
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    filename: str = ""
    label: DataLabel = field(default_factory=DataLabel)
    produced_by: str = ""
    run_id: str = ""
    at: float = field(default_factory=utc_now)

    @property
    def ref(self) -> str:
        return f"artifact:{self.digest[:16]}"


# ------------------------------------------------------------------------ protocols

@runtime_checkable
class Component(Protocol):
    """Anything the resolver can select and the broker can execute."""

    @property
    def manifest(self) -> ComponentManifest:
        """Return this component's manifest. Must be stable for its lifetime."""
        ...

    def invoke(self, payload: Mapping[str, Any], envelope: RunEnvelope) -> Any:
        """Execute. Must not perform egress directly — request it via the broker.

        Raises ``ContractViolation`` if the payload does not match ``input_schema``.
        """
        ...


@runtime_checkable
class EventSink(Protocol):
    """Where events are appended. Implementations must be append-only."""

    def append(self, event_type: str, run_id: str, **fields: Any) -> EventEnvelope:
        """Append one event and return it with its chained hash populated."""
        ...
