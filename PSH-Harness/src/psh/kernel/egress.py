"""Egress gateways: the model gateway, the tool gateway, and the execution broker.

This module exists because of a specific measured failure in the predecessor design.
There, egress policy was enforced on tool *arguments*. A local tool declared
``reads_phi=True``, read a chart note containing an MRN into the transcript, and the next
model call carried that MRN to a remote provider. The gate reported zero blocks. The
predecessor's own test suite asserted this was correct behaviour, describing it as
"the boundary gates egress, not utility".

The correction, which the review states plainly: **a model call is egress.** Prompt and
context leave the machine exactly as tool arguments do. So the same gate runs on both, and
one further gate runs on delegation, because handing context to a subagent that may use a
different provider is also egress.

Structure:

* ``ModelGateway``   - may this context reach this model provider?
* ``ToolGateway``    - may these arguments reach this tool's destination?
* ``DelegationGateway`` - may this projection be handed to this delegate?
* ``ExecutionBroker`` - the single door through which all of the above pass, so that
  "nothing bypasses the trusted path" is an enforced invariant rather than a convention.
"""

from __future__ import annotations

import fnmatch
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contracts import (
    ApprovalDenied, ApprovalRequired, Autonomy, BrokerBypass, ComponentManifest,
    ContextProjection, DelegationContract, EgressDenied, ModelProfile, PolicyDenied,
    RiskTier, RunEnvelope,
)
from ..labels import (
    DataLabel, Destination, Labeled, Sensitivity, label_of, unwrap,
)

__all__ = ["EgressDecision", "ModelGateway", "ToolGateway", "DelegationGateway",
           "ExecutionBroker", "ApprovalEngine"]


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """The verdict on one egress attempt. Recorded whether allowed or refused.

    Carries the label and destination but never the inspected value, so the decision log
    does not become a copy of the data the gate was protecting.
    """

    allowed: bool
    destination: Destination
    label: DataLabel
    reason: str
    gate: str
    target: str = ""
    #: Allowed, but only after a human says so — the execution policy's PROMPT decision.
    requires_approval: bool = False
    at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("an egress decision must state its reason")

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise EgressDenied(self.reason, label=self.label, destination=self.destination)


class _GateBase:
    """Shared decision recording. Subclasses implement the policy question."""

    name = "gate"

    def __init__(self, *, ceilings: Mapping[Destination, Sensitivity] | None = None,
                 audit: Callable[..., Any] | None = None) -> None:
        self.ceilings = ceilings
        self._audit = audit
        self.decisions: list[EgressDecision] = []
        self.checks = 0
        # The agent loop may run independent branches on threads; a plain ``+= 1`` on a
        # counter that tests and audits read would lose updates under that load.
        self._count_lock = threading.Lock()

    def _tick(self) -> None:
        with self._count_lock:
            self.checks += 1

    def _record(self, decision: EgressDecision) -> EgressDecision:
        self.decisions.append(decision)
        if self._audit is not None:
            self._audit(
                "egress_decision" if decision.allowed else "egress_refused",
                gate=decision.gate, allowed=decision.allowed,
                destination=decision.destination.name,
                sensitivity=decision.label.sensitivity.name,
                categories=list(decision.label.categories), target=decision.target)
        return decision

    @property
    def refusals(self) -> list[EgressDecision]:
        return [d for d in self.decisions if not d.allowed]


class ModelGateway(_GateBase):
    """Gates every model call on the data label of its context.

    Two independent checks, both of which must pass:

    1. The model's own ``max_label`` — a provider's contractual limit.
    2. The run envelope's ``allowed_destinations`` — the profile's limit for this work.

    Both exist because they answer different questions. A provider under an institutional
    agreement may be permitted to receive PHI in general, while a peer-review run is still
    forbidden from sending anything outward at all.
    """

    name = "model_gateway"

    def check(self, value: Labeled | ContextProjection | str, model: ModelProfile,
              envelope: RunEnvelope | None = None) -> EgressDecision:
        """Return the decision. Does not raise — call ``raise_if_denied()`` to enforce.

        The permissive default is deliberate: the broker raises, so the gate can be used
        for pre-flight inspection ("would this be allowed?") without exception handling.
        """
        self._tick()
        label = self._label_of(value)
        target = f"{model.provider}/{model.id}"

        if envelope is not None and not envelope.permits_destination(model.destination):
            return self._record(EgressDecision(
                allowed=False, destination=model.destination, label=label, gate=self.name,
                target=target,
                reason=(f"run profile {envelope.profile!r} does not permit destination "
                        f"{model.destination.name}; model {target} refused")))

        # The run's own ceiling, at the gate. ``Runner._preflight`` made this comparison
        # before its single model call, and nothing else did — so a caller that reached the
        # broker by another route (a loop, a planner) executed under a ceiling no gate read.
        # The envelope travels with every call; the check belongs where the envelope is.
        if envelope is not None and label.sensitivity > envelope.max_label.sensitivity:
            return self._record(EgressDecision(
                allowed=False, destination=model.destination, label=label, gate=self.name,
                target=target,
                reason=(f"context is classified {label.sensitivity.name} but this run's "
                        f"ceiling is {envelope.max_label.sensitivity.name}; model egress "
                        "refused")))

        if not model.may_receive(label):
            return self._record(EgressDecision(
                allowed=False, destination=model.destination, label=label, gate=self.name,
                target=target,
                reason=(f"context is classified {label.sensitivity.name}"
                        f"{' [' + ', '.join(label.categories) + ']' if label.categories else ''}"
                        f" but model {target} accepts at most "
                        f"{model.max_label.name}; model egress refused")))

        if not label.permits(model.destination, self.ceilings):
            return self._record(EgressDecision(
                allowed=False, destination=model.destination, label=label, gate=self.name,
                target=target,
                reason=(f"destination ceiling forbids {label.sensitivity.name} data at "
                        f"{model.destination.name}")))

        return self._record(EgressDecision(
            allowed=True, destination=model.destination, label=label, gate=self.name,
            target=target,
            reason=f"{label.sensitivity.name} permitted at {model.destination.name}"))

    @staticmethod
    def _label_of(value: Any) -> DataLabel:
        if isinstance(value, ContextProjection):
            return value.label
        return label_of(value)


class ToolGateway(_GateBase):
    """Gates tool arguments, path access, and destructive commands."""

    name = "tool_gateway"

    #: Command shapes refused regardless of autonomy. A harness that can be talked into
    #: running these is not governed, whatever its permission mode says.
    DENIED_COMMANDS: tuple["re.Pattern[str]", ...] = (
        re.compile(r"\brm\s+-[a-z]*[rf]", re.I),
        re.compile(r"\b(?:mkfs|dd)\b.*\bof=/dev/", re.I),
        re.compile(r":\(\)\s*\{.*\};\s*:", re.S),
        re.compile(r"\bchmod\s+-R\s+777\s+/", re.I),
        re.compile(r"\bgit\s+push\s+.*--force", re.I),
        re.compile(r">\s*/dev/sd[a-z]"),
    )

    def __init__(self, *, allowed_paths: Sequence[str] = (),
                 ceilings: Mapping[Destination, Sensitivity] | None = None,
                 audit: Callable[..., Any] | None = None,
                 policy: Any = None) -> None:
        super().__init__(ceilings=ceilings, audit=audit)
        self.allowed_paths = tuple(allowed_paths)
        #: The declarative execution policy (Codex execpolicy pattern). When present it is
        #: consulted BEFORE the regex denylist and can return allow / prompt / forbidden. The
        #: regex list stays as a second layer for shell shapes a token-prefix rule cannot
        #: express — a fork bomb, a redirect onto a block device.
        if policy is None:
            from .execpolicy import ExecPolicy
            policy = ExecPolicy()
        self.policy = policy

    @staticmethod
    def _split_glob(pattern: str) -> tuple[str, str]:
        """Split an allowed-path pattern into its literal prefix and the glob remainder."""
        head: list[str] = []
        parts = PurePath(pattern).parts
        for index, part in enumerate(parts):
            if any(ch in part for ch in "*?["):
                return (str(PurePath(*head)) if head else "/",
                        str(PurePath(*parts[index:])))
            head.append(part)
        return pattern, ""

    def _allowed_roots(self) -> tuple[tuple[Path, str], ...]:
        """Resolved (root, pattern) pairs, computed once per allowed-path set.

        The pattern is re-anchored on the *resolved* root so that a platform where the
        allowed directory is itself a symlink — ``/tmp`` -> ``/private/tmp`` on macOS is the
        common one — still matches the resolved target.
        """
        cached = getattr(self, "_roots_cache", None)
        if cached is not None and cached[0] == self.allowed_paths:
            return cached[1]
        roots: list[tuple[Path, str]] = []
        for pattern in self.allowed_paths:
            head, tail = self._split_glob(str(pattern))
            root = Path(head).expanduser()
            try:
                root = root.resolve(strict=False)
            except OSError:  # pragma: no cover - unresolvable root, keep the literal
                pass
            roots.append((root, str(root / tail) if tail else str(root)))
        out = tuple(roots)
        self._roots_cache = (self.allowed_paths, out)
        return out

    def _path_permitted(self, candidate: str) -> tuple[bool, str]:
        """Whether a path a payload names lies inside the allowed roots, after resolution.

        v0.4 ran ``fnmatch`` on the string the payload supplied. ``/allowed/../secret``
        matches ``/allowed/*`` as text and names a file outside it; a symlink at
        ``/allowed/link`` matches too and can point anywhere. A filesystem boundary has to
        be decided on the object the path resolves to, so the check is:

            expanduser -> resolve (follows ``..`` and symlinks) -> inside a root -> matches

        Resolution is done with ``strict=False`` because a tool writing a new file names a
        path that does not exist yet; the parent chain is still resolved, which is what the
        symlink case needs.
        """
        try:
            target = Path(candidate).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:   # RuntimeError: symlink loop
            return False, f"path could not be resolved ({exc})"
        for root, pattern in self._allowed_roots():
            try:
                inside = target == root or root in target.parents
            except OSError:  # pragma: no cover - platform specific
                inside = False
            if inside and fnmatch.fnmatch(str(target), pattern):
                return True, "inside an allowed root"
        return False, f"resolves to {target}"

    def check(self, payload: Any, manifest: ComponentManifest,
              envelope: RunEnvelope) -> EgressDecision:
        self._tick()
        label = label_of(payload) if isinstance(payload, Labeled) else DataLabel()
        raw = unwrap(payload)
        destinations = manifest_destinations(manifest)
        # The destination named on the decision is the **most exposed** one the component
        # declares, not the first one it happens to list. A component declaring
        # ``(LOCAL_COMPUTE, PUBLIC_REMOTE)`` used to be gated and recorded as local: the
        # gate checked ``destinations[0]``, so a PHI payload passed, while
        # ``IsolatedExecutor.allowed_hosts`` read the *same* manifest with ``any(...)``,
        # saw PUBLIC_REMOTE and opened the component's network reach. The two halves
        # disagreed about the same tuple, and the audit record took the reassuring half.
        destination = widest_destination(destinations)
        target = manifest.id

        ok, why = manifest.compatible_with(envelope)
        if not ok:
            return self._record(EgressDecision(
                allowed=False, destination=destination, label=label, gate=self.name,
                target=target, reason=f"component rejected by run authority: {why}"))

        # The run's ceiling applies to what a tool is handed, not only to what a model is
        # shown. ``max_label`` is the highest sensitivity this run may handle at all.
        if label.sensitivity > envelope.max_label.sensitivity:
            return self._record(EgressDecision(
                allowed=False, destination=destination, label=label, gate=self.name,
                target=target,
                reason=(f"payload is {label.sensitivity.name} but this run's ceiling is "
                        f"{envelope.max_label.sensitivity.name}; tool call refused")))

        # A component's declared ceiling applies at EVERY destination, including local
        # ones. An earlier version skipped this check for local destinations on the
        # reasoning that local data never leaves the machine — but the ceiling is not only
        # about egress. A component that declares it handles no more than PUBLIC data has
        # said something about its own competence and safety: passing it a PHI payload may
        # write identifiers into a log, a cache, or a shared file it manages. Honour the
        # declaration wherever the component runs.
        if label.sensitivity > manifest.max_label:
            return self._record(EgressDecision(
                allowed=False, destination=destination, label=label, gate=self.name,
                target=target,
                reason=(f"payload is {label.sensitivity.name} but component {target} "
                        f"declares it accepts at most {manifest.max_label.name}")))

        # EVERY declared destination must accept this label. A component reaches all of
        # the destinations it declares, so the gate's question is about the whole set: the
        # safe intersection, not the first element.
        for candidate in destinations:
            if not label.permits(candidate, self.ceilings):
                return self._record(EgressDecision(
                    allowed=False, destination=candidate, label=label, gate=self.name,
                    target=target,
                    reason=(f"payload classified {label.sensitivity.name} may not reach "
                            f"{candidate.name} via {target}, which declares destinations "
                            f"{sorted(d.name for d in destinations)}")))

        scan = _flatten_text(raw)
        if not scan.complete:
            return self._record(EgressDecision(
                allowed=False, destination=destination, label=label, gate=self.name,
                target=target,
                reason=(f"payload for {target} is nested beyond {_WALK_MAX_DEPTH} levels or "
                        f"{_WALK_MAX_NODES} nodes, so it could not be fully inspected for "
                        "denied command shapes; refusing rather than passing what was not "
                        "checked — flatten the payload and retry")))
        text = " ".join(scan.values)

        # Declarative policy first. A command payload is anything with a "command"/"cmd"/
        # "argv" key or a bare string.
        command = _candidate_command(raw) if _executes_commands(manifest) else None
        if command is not None:
            evaluation = self.policy.evaluate(command)
            if evaluation.decision.value == "forbidden":
                return self._record(EgressDecision(
                    allowed=False, destination=destination, label=label, gate=self.name,
                    target=target,
                    reason=(f"execution policy forbids this command: "
                            f"{evaluation.describe()}; refused regardless of autonomy")))
            if evaluation.decision.value == "prompt":
                return self._record(EgressDecision(
                    allowed=True, destination=destination, label=label, gate=self.name,
                    target=target, requires_approval=True,
                    reason=f"execution policy requires approval: {evaluation.describe()}"))

        for pattern in self.DENIED_COMMANDS:
            if pattern.search(text):
                return self._record(EgressDecision(
                    allowed=False, destination=destination, label=label, gate=self.name,
                    target=target,
                    reason=(f"payload matches a denied command pattern "
                            f"({pattern.pattern[:40]}); refused regardless of autonomy")))

        if (manifest.mutates or manifest.requires_filesystem) and self.allowed_paths:
            scanned = _candidate_paths(raw)
            if not scanned.complete:
                return self._record(EgressDecision(
                    allowed=False, destination=destination, label=label, gate=self.name,
                    target=target,
                    reason=(f"payload for {target} is nested beyond {_WALK_MAX_DEPTH} levels "
                            f"or {_WALK_MAX_NODES} nodes, so the paths it names could not "
                            "be enumerated; a filesystem boundary that cannot be checked is "
                            "not a boundary, so this is refused")))
            for path in scanned:
                ok, why = self._path_permitted(path)
                if not ok:
                    return self._record(EgressDecision(
                        allowed=False, destination=destination, label=label,
                        gate=self.name, target=target,
                        reason=(f"access to {path!r} is outside the allowed paths "
                                f"{list(self.allowed_paths)}: {why}")))

        return self._record(EgressDecision(
            allowed=True, destination=destination, label=label, gate=self.name,
            target=target,
            reason=(f"{label.sensitivity.name} permitted for {target} at every declared "
                    f"destination {sorted(d.name for d in destinations)}")))


class DelegationGateway(_GateBase):
    """Gates handing a context projection to a delegate.

    Delegation is egress when the delegate may reach a different provider or a wider
    capability set. The contract's envelope is checked for monotonicity here — a delegate
    must not receive authority its parent did not hold.
    """

    name = "delegation_gateway"

    def check(self, contract: DelegationContract,
              parent: RunEnvelope) -> EgressDecision:
        """Refuse a delegation whose child holds authority the parent does not.

        This re-implemented containment by hand and covered four dimensions of the
        fourteen ``AuthorityLattice`` defines — destinations, data ceiling, the projection
        label and ``tokens_hard`` — so a child could be handed ``R4_KERNEL`` from an ``R1``
        parent, ``ACT`` from ``SUGGEST``, unrestricted capabilities from a named list, the
        parent's denials dropped, and 999x the USD, wall-clock and per-call budgets, and
        this gate returned ``allowed=True`` on all of it.

        The package already had the predicate; ``RunEnvelope.restrict`` uses it. Writing a
        second, shorter version beside it is the exact failure mode the lattice module
        exists to prevent — "a check repeated at each call site will drift; a check called
        from each call site cannot" — so the hand-written comparisons are gone and the one
        predicate decides. A dimension added to the lattice is enforced here for free.
        """
        from .authority import AuthorityLattice

        self._tick()
        label = contract.projection.label if contract.projection else DataLabel()
        child = contract.envelope

        violations = AuthorityLattice.violations(child, parent)
        if violations:
            return self._record(EgressDecision(
                allowed=False, destination=Destination.LOCAL_COMPUTE, label=label,
                gate=self.name, target=contract.backend,
                reason=("delegation would grant authority the parent run does not hold: "
                        + "; ".join(str(v) for v in violations))))

        # Not an authority question and so not the lattice's: this asks whether the DATA
        # being handed over fits the child's ceiling, which is about the projection rather
        # than about the relationship between two envelopes.
        if label.sensitivity > child.max_label.sensitivity:
            return self._record(EgressDecision(
                allowed=False, destination=Destination.LOCAL_COMPUTE, label=label,
                gate=self.name, target=contract.backend,
                reason=(f"projection is {label.sensitivity.name} but the delegate's "
                        f"envelope permits at most {child.max_label.sensitivity.name}")))

        return self._record(EgressDecision(
            allowed=True, destination=Destination.LOCAL_COMPUTE, label=label,
            gate=self.name, target=contract.backend,
            reason="delegation is within parent authority on every lattice dimension"))


class ApprovalEngine:
    """Human-in-the-loop decisions. Fails closed when no handler is present."""

    def __init__(self, handler: Callable[[str, Mapping[str, Any]], bool] | None = None,
                 *, audit: Callable[..., Any] | None = None) -> None:
        self.handler = handler
        self._audit = audit
        self.requests: list[dict[str, Any]] = []

    def request(self, what: str, envelope: RunEnvelope, **detail: Any) -> None:
        """Raise unless a human approves. Records every request either way."""
        record = {"what": what, "run_id": envelope.run_id, "risk": envelope.risk.name,
                  **detail}
        self.requests.append(record)
        if self._audit is not None:
            self._audit("approval_requested", what=what, risk=envelope.risk.name)
        if self.handler is None:
            raise ApprovalRequired(
                f"{what} requires human approval and no approval handler is configured; "
                "failing closed")
        if not self.handler(what, record):
            if self._audit is not None:
                self._audit("approval_denied", what=what)
            raise ApprovalDenied(f"a human refused: {what}")
        if self._audit is not None:
            self._audit("approval_granted", what=what)


class ExecutionBroker:
    """The single door for model calls, tool calls, code execution and delegation.

    Everything routes through ``call_model``, ``call_tool`` or ``delegate``. Each records
    an event, checks the relevant gate, enforces budget, and requests approval when the
    risk tier demands it. The broker also counts invocations so a test can assert that a
    component did not reach a provider by some other path.

    ``BrokerBypass`` exists for the invariant test: a component that calls a provider
    directly is a bug, and the broker's counters are how that bug is detected.
    """

    def __init__(self, *, model_gateway: ModelGateway, tool_gateway: ToolGateway,
                 delegation_gateway: DelegationGateway, approvals: Any,
                 budget_governor: Any, ingress: Any,
                 audit: Callable[..., Any] | None = None,
                 isolation: Any = None, require_isolation: bool = False) -> None:
        #: Mandatory classification for every value entering the broker. This is what makes
        #: "everything goes through the broker" mean "everything is classified" — in v0.1
        #: the two were different statements and a raw payload satisfied the first without
        #: the second.
        self.ingress = ingress
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway
        self.delegation_gateway = delegation_gateway
        self.approvals = approvals
        self.budget = budget_governor
        self._audit = audit
        #: Executes components whose manifest declares ``backend="subprocess"``. v0.4
        #: shipped ``IsolatedRunner`` and a README paragraph about process isolation while
        #: ``call_tool`` ran every component with ``component.invoke(...)`` inside the
        #: kernel process — the mechanism existed and the only path that executes anything
        #: did not use it. It is wired here, and the two paths are counted separately so a
        #: report cannot claim isolation a run did not have.
        self.isolation = isolation
        #: When True a component that cannot be isolated is refused rather than run in
        #: process. This is the switch a clinical profile turns on: a tool that has not been
        #: packaged to run in a subprocess simply may not run.
        self.require_isolation = require_isolation
        self.model_calls = 0
        self.tool_calls = 0
        self.isolated_tool_calls = 0
        self.in_process_tool_calls = 0
        self.delegations = 0
        self.refusals = 0
        #: The counters are how a test proves nothing bypassed the broker, so they have to
        #: be right under concurrency or the proof is worthless exactly when it matters.
        #: ``+=`` on an attribute is a load, an add and a store; a worker pool interleaves
        #: them.
        self._counter_lock = threading.Lock()
        #: Typed hook lifecycle (Claude Code / Codex protocol). Hooks run HERE, after the
        #: gates, and can only narrow or rewrite — see hooks.py.
        from .hooks import HookRegistry
        self.hooks = HookRegistry(audit=audit)

    # -------------------------------------------------------------- model calls
    def call_model(self, projection: ContextProjection | Labeled, model: ModelProfile,
                   envelope: RunEnvelope,
                   invoke: Callable[[str], Any]) -> Any:
        """Run a model call through the gateway, then execute it.

        ``invoke`` receives the rendered prompt text. It is passed in rather than held by
        the broker so that the broker never needs a provider SDK, which keeps the trusted
        path free of network code.
        """
        # Classify first, always. A ContextProjection carries an aggregate label computed
        # by the compiler — the join of its items' labels — which is what the CALLER says
        # the context is. Items are labelled by whoever built them, and an item built
        # without a label says PUBLIC. So the projection was the one value the broker took
        # on trust, and the agent loop built its evidence items from tool results without
        # carrying their labels: PHI a tool had returned reached a public provider under a
        # PUBLIC verdict, one hop after the gate had correctly labelled it. The text that
        # will actually be sent is classified here and joined with the claimed label. A
        # projection can be escalated at this boundary and is never trusted downward.
        if isinstance(projection, ContextProjection):
            checked: Any = self._reclassified(projection)
        else:
            checked = self.ingress.ensure(projection, origin="model_call")

        decision = self.model_gateway.check(checked, model, envelope)
        if not decision.allowed:
            self._bump("refusals")
            decision.raise_if_denied()

        self.budget.check_model_call(envelope)
        prompt = (checked.render() if isinstance(checked, ContextProjection)
                  else str(unwrap(checked)))

        # PreModelCall hook (psh addition to the protocol: the model call is a boundary).
        # The hook sees the destination and the label, never the prompt text — a hook is
        # observability and policy, not a second reader of the data.
        from .hooks import HookBlocked, HookEvent
        try:
            self.hooks.dispatch(HookEvent.PRE_MODEL_CALL, target=model.id,
                                run_id=envelope.run_id,
                                payload={"model_id": model.id,
                                         "destination": model.destination.name,
                                         "label": checked.label.sensitivity.name,
                                         "prompt_chars": len(prompt)})
        except HookBlocked as exc:
            self._bump("refusals")
            raise EgressDenied(f"hook {exc.hook} denied model call to {model.id}: {exc}",
                               label=checked.label, destination=model.destination) from exc

        started = time.time()
        raw = invoke(prompt)
        latency = time.time() - started
        self._bump("model_calls")

        # Standardise the return so token and cost ceilings are fed by real usage. v0.1
        # counted calls while tokens and USD stayed at zero, so a hard token ceiling of 1
        # never fired.
        from .results import ModelCallResult, ModelUsage

        if isinstance(raw, ModelCallResult):
            call_result = raw
        else:
            content = str(getattr(raw, "content", raw))
            call_result = ModelCallResult(
                content=content,
                usage=ModelUsage.estimate(prompt, content,
                                          usd_per_1k_input=model.usd_per_1k_input,
                                          usd_per_1k_output=model.usd_per_1k_output),
                model_id=model.id, provider=model.provider, latency_s=latency)
        # A model's output is at least as sensitive as the context it was shown. The
        # result says so itself, so a caller building on it starts from the join rather
        # than from PUBLIC.
        from dataclasses import replace as _replace
        call_result = _replace(call_result,
                               label=call_result.label.merged_with(decision.label))

        self.budget.record_model_usage(envelope, call_result.usage.input_tokens,
                                       call_result.usage.output_tokens,
                                       call_result.usage.cost_usd)
        if self._audit is not None:
            self._audit("model_call", run_id=envelope.run_id, model_id=model.id,
                        latency_s=round(latency, 4), tokens=call_result.tokens,
                        cost_usd=round(call_result.usage.cost_usd, 6),
                        sensitivity=decision.label.sensitivity.name)
        return call_result

    def _reclassified(self, projection: ContextProjection) -> ContextProjection:
        """Join a projection's claimed label with a classification of its rendered text."""
        classifier = getattr(self.ingress, "classifier", None)
        if classifier is None:                       # pragma: no cover - always wired
            return projection
        fresh = classifier.classify(projection.render(), origin="model_call").label
        joined = projection.label.merged_with(fresh)
        if joined == projection.label:
            return projection
        from dataclasses import replace as _replace
        return _replace(projection, label=joined)

    # --------------------------------------------------------------- tool calls
    def call_tool(self, component: Any, payload: Any, envelope: RunEnvelope) -> Any:
        """Run a tool call through the gateway and any required approval."""
        manifest: ComponentManifest = component.manifest
        # Same rule as model calls: classify at the boundary, never trust the caller.
        payload = self.ingress.ensure(payload, origin=f"tool:{manifest.id}")
        decision = self.tool_gateway.check(payload, manifest, envelope)
        if not decision.allowed:
            self._bump("refusals")
            decision.raise_if_denied()

        needs_approval = (
            decision.requires_approval        # the execution policy said PROMPT
            or manifest.human_approval
            or manifest.risk_tier >= RiskTier.R3_CLINICAL
            or (manifest.mutates and envelope.autonomy is Autonomy.ACT_WITH_APPROVAL))
        if needs_approval:
            what = (f"run {manifest.id}: {decision.reason[:80]}" if decision.requires_approval
                    else f"run {manifest.id}")
            self.approvals.request(
                _tool_approval_request(manifest, payload, envelope, summary=what),
                envelope, mutates=manifest.mutates,
                policy_prompt=decision.requires_approval)

        self.budget.check_tool_call(envelope)

        # PreToolUse hooks. After every gate has already ruled, so a hook cannot widen; it
        # can deny, ask, or rewrite. A rewrite goes back through ingress — a hook cannot
        # launder a label by rewriting the payload.
        from .hooks import HookBlocked, HookDecision, HookEvent
        from ..labels import unwrap_deep
        try:
            hook_decision, rewritten, _ = self.hooks.dispatch(
                HookEvent.PRE_TOOL_USE, target=manifest.id, run_id=envelope.run_id,
                payload={"tool_name": manifest.id, "tool_input": unwrap_deep(payload)})
        except HookBlocked as exc:
            self._bump("refusals")
            raise EgressDenied(f"hook {exc.hook} denied {manifest.id}: {exc}",
                               label=payload.label, destination=Destination.LOCAL_COMPUTE) from exc
        if rewritten is not None:
            payload = self.ingress.ensure(rewritten, origin=f"hook_rewrite:{manifest.id}")
            decision = self.tool_gateway.check(payload, manifest, envelope)
            if not decision.allowed:
                self._bump("refusals")
                decision.raise_if_denied()
        if hook_decision is HookDecision.ASK:
            self.approvals.request(
                _tool_approval_request(
                    manifest, payload, envelope, kind="hook_ask",
                    summary=f"run {manifest.id}: a hook asked for confirmation"),
                envelope, hook_ask=True)

        started = time.time()
        raw, execution = self._invoke(component, manifest, unwrap_deep(payload), envelope)
        self._bump("tool_calls")
        try:
            self.hooks.dispatch(HookEvent.POST_TOOL_USE, target=manifest.id,
                                run_id=envelope.run_id,
                                payload={"tool_name": manifest.id,
                                         "tool_response_type": type(raw).__name__})
        except HookBlocked as exc:
            # A PostToolUse deny means the RESULT must not reach the model.
            self._bump("refusals")
            raise EgressDenied(f"hook {exc.hook} withheld the result of {manifest.id}: {exc}",
                               label=payload.label, destination=Destination.LOCAL_COMPUTE) from exc

        # The output inherits the input's label. Without this a component launders taint
        # simply by returning a plain dict, which is the ordinary case rather than an
        # adversarial one.
        from .results import ExecutionResult

        result = ExecutionResult.from_component(
            raw, inputs=[payload], component_id=manifest.id, run_id=envelope.run_id,
            classifier=getattr(self.ingress, "classifier", None))
        if self._audit is not None:
            self._audit("tool_call", run_id=envelope.run_id, component_id=manifest.id,
                        latency_s=round(time.time() - started, 4),
                        execution=execution,
                        sensitivity=result.label.sensitivity.name)
        return result

    def _invoke(self, component: Any, manifest: ComponentManifest, payload: Any,
                envelope: RunEnvelope) -> tuple[Any, str]:
        """Execute the component, isolated where it declares it, and say which ran.

        Three outcomes, all explicit:

        * the manifest declares ``subprocess`` and an executor is wired — the component runs
          in a child process with a clean environment behind the kernel's egress proxy;
        * the manifest declares ``subprocess`` and no executor is wired — refused, because
          silently running it in process is exactly the claim/behaviour gap this closes;
        * the manifest declares ``python`` — it runs in the kernel process, and if the run
          requires isolation it is refused instead. The event records ``in_process`` either
          way, so the audit trail never implies containment the call did not have.
        """
        if manifest.runs_isolated:
            if self.isolation is None:
                from .isolation import IsolationUnavailable

                self._bump("refusals")
                raise IsolationUnavailable(
                    f"component {manifest.id!r} declares isolated execution but this kernel "
                    "has no isolated runner wired; refusing rather than running it in the "
                    "kernel process")
            self._bump("isolated_tool_calls")
            return self.isolation.invoke(manifest, payload, envelope), "isolated"

        # The kernel's policy OR the run's own. The broker is constructed once, from the
        # kernel's policy, so a run executing under a NARROWER policy that turns isolation
        # on was previously declaring a requirement nothing read. The envelope carries it
        # now, and the envelope is what travels with the run and its delegates.
        if self.require_isolation or getattr(envelope, "require_isolated_tools", False):
            self._bump("refusals")
            raise PolicyDenied(
                f"this run requires process-isolated tools and component {manifest.id!r} "
                f"declares backend={manifest.backend!r}; package it with "
                "backend='subprocess' and an entrypoint, or run it under a policy that "
                "does not require isolation")

        self._bump("in_process_tool_calls")
        return component.invoke(payload, envelope), "in_process"

    # -------------------------------------------------------------- delegation
    def delegate(self, contract: DelegationContract, parent: RunEnvelope,
                 backend: Callable[[DelegationContract], Any]) -> Any:
        decision = self.delegation_gateway.check(contract, parent)
        if not decision.allowed:
            self._bump("refusals")
            decision.raise_if_denied()
        self.budget.check_delegation(parent)
        result = backend(contract)
        self._bump("delegations")
        if self._audit is not None:
            self._audit("delegation", run_id=parent.run_id, target=contract.backend,
                        objective_len=len(contract.objective))
        return result

    def _bump(self, name: str) -> None:
        with self._counter_lock:
            setattr(self, name, getattr(self, name) + 1)

    def stats(self) -> dict[str, int]:
        return {"model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "isolated_tool_calls": self.isolated_tool_calls,
                "in_process_tool_calls": self.in_process_tool_calls,
                "delegations": self.delegations, "refusals": self.refusals,
                "model_gate_checks": self.model_gateway.checks,
                "tool_gate_checks": self.tool_gateway.checks}


# ------------------------------------------------------------------------ helpers

def model_free_destination(destination: Destination) -> bool:
    """True for destinations that never leave the machine."""
    return destination in (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL)


#: Destinations from least to most exposed. The enum's own integer order is a declaration
#: order, not an exposure order — PERSISTENT is 5 and PUBLIC_REMOTE is 3, and writing to
#: the user's own disk is not more exposing than handing data to a public provider. So the
#: ranking is stated explicitly, here, once.
_EXPOSURE_ORDER: tuple[Destination, ...] = (
    Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.PERSISTENT,
    Destination.USER_OUTPUT, Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE)


def manifest_destinations(manifest: Any) -> tuple[Destination, ...]:
    """Every destination a component reaches, de-duplicated and never empty.

    A manifest that names none is treated as local compute — the same default the gateway
    always applied, but stated once so the gate, the approval request and the audit record
    cannot each invent their own.
    """
    declared = tuple(dict.fromkeys(getattr(manifest, "destinations", ()) or ()))
    return declared or (Destination.LOCAL_COMPUTE,)


def widest_destination(destinations: Iterable[Destination]) -> Destination:
    """The most exposed destination in a set: what a decision about the set must name."""
    ranked = sorted(destinations, key=lambda d: (_EXPOSURE_ORDER.index(d)
                                                 if d in _EXPOSURE_ORDER else len(_EXPOSURE_ORDER)))
    return ranked[-1] if ranked else Destination.LOCAL_COMPUTE


#: Caps for the two recursive payload walks below. Generous, because the failure mode on
#: hitting one is refusal rather than a silent pass, and refusing an ordinary deeply
#: structured payload would be its own defect.
_WALK_MAX_DEPTH = 16
_WALK_MAX_NODES = 20_000


@dataclass(frozen=True, slots=True)
class _Scan:
    """The result of walking a payload, and whether the walk finished.

    ``complete=False`` means the walk hit a cap, so the values it collected are a *subset*
    of what the payload contains. A security walker that returns a subset and says nothing
    about it is fail-open: the previous ``_candidate_paths`` returned ``[]`` past depth 8,
    so wrapping ``{"target": "/outside"}`` in nine dictionaries produced "no paths found",
    and the gate read that as "no paths to object to" and allowed the call.

    ``psh.labels.walk_values`` already had the right shape — it yields ``_TRUNCATED`` and
    ``deep_label_of`` escalates on it rather than assuming the remainder was clean. This
    makes the egress walkers agree with it: unable to validate is a refusal, not a pass.
    """

    values: tuple[str, ...] = ()
    complete: bool = True

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def _flatten_text(value: Any) -> _Scan:
    """Collect all string content from a nested structure, for pattern checks."""
    parts: list[str] = []
    budget = [_WALK_MAX_NODES]
    complete = _flatten_into(value, parts, budget, 0)
    return _Scan(values=(" ".join(parts),), complete=complete)


def _flatten_into(value: Any, out: list[str], budget: list[int], depth: int) -> bool:
    if depth > _WALK_MAX_DEPTH or budget[0] <= 0:
        return False
    budget[0] -= 1
    if isinstance(value, str):
        out.append(value)
        return True
    complete = True
    if isinstance(value, Mapping):
        for item in value.values():
            complete &= _flatten_into(item, out, budget, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            complete &= _flatten_into(item, out, budget, depth + 1)
    return complete


_PATH_KEYS = ("path", "file", "filename", "dest", "destination", "output", "target")
_COMMAND_KEYS = ("command", "cmd", "argv", "args", "shell")


_COMMAND_COMPONENT_HINTS = ("shell", "bash", "sh", "exec", "command", "terminal", "subprocess")


def _executes_commands(manifest: Any) -> bool:
    """Whether the execution policy governs this component.

    The policy speaks the language of commands, so it applies to components that run them.
    Applying it to every tool whose payload happens to have an ``args`` key turned the
    PROMPT default into an approval prompt on a local summariser — found the moment the
    suite ran. A component opts in with ``executes_commands=True`` in its manifest metadata,
    or by having a shell-like id.
    """
    meta = getattr(manifest, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get("executes_commands") is not None:
        return bool(meta["executes_commands"])
    ident = f"{getattr(manifest, 'id', '')} {getattr(manifest, 'name', '')}".lower()
    return any(h in ident.split() or ident.startswith(h) for h in _COMMAND_COMPONENT_HINTS)


def _candidate_command(payload: Any, _depth: int = 0) -> Any:
    """Extract a command (string or argv list) from a payload, or None if there is none.

    Walked rather than read from the top level, for the reason ``_candidate_paths`` was
    changed in v0.5: nesting is the ordinary shape of a structured tool payload, so a
    top-level-only check is close to no check at all. ``{"opts": {"command": "curl … | sh"}}``
    reached the regex denylist through ``_flatten_text`` but never reached the declarative
    execution policy, so a rule that said PROMPT or FORBIDDEN about that command did not
    apply to it. The two extractors now agree about where a payload can hide things.

    Bounded by the same caps as the other walkers. There is no fail-closed flag here
    because there does not need to be one: ``ToolGateway.check`` has already refused a
    payload whose ``_flatten_text`` walk was truncated, and that walk covers at least as
    much of the structure as this one.
    """
    if _depth > _WALK_MAX_DEPTH:
        return None
    if isinstance(payload, str):
        return payload if payload.strip() and _depth == 0 else None
    if (isinstance(payload, (list, tuple)) and payload
            and all(isinstance(x, str) for x in payload)):
        return list(payload)
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).lower() in _COMMAND_KEYS:
                if isinstance(value, str) and value.strip():
                    return value
                if isinstance(value, (list, tuple)) and value:
                    return [str(v) for v in value]
        # No command at this level: look inside. A command under a non-command key is
        # still a command, and the component that reads it will not care how deep it was.
        for value in payload.values():
            found = _candidate_command(value, _depth + 1)
            if found is not None:
                return found
    return None


def _candidate_paths(payload: Any) -> _Scan:
    """Extract values that look like filesystem paths, at any depth.

    v0.4 looked only at top-level keys, so ``{"config": {"target": "/etc/shadow"}}`` named
    a path the gate never saw — and nesting is the ordinary shape of a structured tool
    payload, not an adversarial one. v0.5 walked deeply and returned ``[]`` past depth 8,
    which turned the depth cap into a bypass: nest the payload far enough and the gate saw
    no paths at all. The walk is still bounded — an unbounded walk over an attacker-shaped
    object graph is a denial of service — but hitting the bound is now reported, and the
    caller refuses.
    """
    out: list[str] = []
    budget = [_WALK_MAX_NODES]
    complete = _paths_into(payload, out, budget, 0, False)
    return _Scan(values=tuple(dict.fromkeys(out)), complete=complete)


def _paths_into(payload: Any, out: list[str], budget: list[int], depth: int,
                keyed: bool) -> bool:
    if depth > _WALK_MAX_DEPTH or budget[0] <= 0:
        return False
    budget[0] -= 1
    complete = True
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            sub_keyed = any(k in str(key).lower() for k in _PATH_KEYS)
            complete &= _paths_into(value, out, budget, depth + 1, sub_keyed)
    elif isinstance(payload, (list, tuple, set, frozenset)):
        for item in payload:
            complete &= _paths_into(item, out, budget, depth + 1, keyed)
    elif isinstance(payload, str) and keyed and payload.strip():
        out.append(payload)
    return complete


def _candidate_paths_for_approval(payload: Any) -> tuple[str, ...]:
    """The paths an approval record should name. Descriptive, so truncation is not fatal.

    The gate has already refused an unverifiable payload by the time an approval is built,
    so this is a record of what is being approved rather than a second enforcement point.
    """
    return _candidate_paths(payload).values


def _tool_approval_request(manifest: ComponentManifest, payload: Any, envelope: RunEnvelope,
                           *, kind: str = "tool_call", summary: str = "") -> Any:
    """Describe a tool call precisely enough that approving it approves only it.

    The action is the command's argv where the component runs commands, and a digest of the
    payload otherwise. The targets are the paths the payload names plus the destination the
    component reaches. Two calls that differ in any of those are different questions, which
    is what stops one "approve for this session" from covering the rest of the run.
    """
    import shlex

    from ..contracts import content_hash
    from .approvals import ApprovalRequest
    from ..labels import unwrap_deep

    raw = unwrap_deep(payload)
    command = _candidate_command(raw) if _executes_commands(manifest) else None
    if isinstance(command, str):
        try:
            action = tuple(shlex.split(command))
        except ValueError:                      # unbalanced quotes: key on the raw text
            action = (command,)
    elif isinstance(command, (list, tuple)):
        action = tuple(str(c) for c in command)
    else:
        # No command to name, so the payload itself identifies the action. A digest rather
        # than the payload: an approval record must not become a second copy of the data.
        action = ("invoke", content_hash(raw)[:32])

    # ALL destinations, not the first. An approval record naming only ``LOCAL_COMPUTE``
    # for a component that also reaches ``PUBLIC_REMOTE`` describes a different action from
    # the one the human is being asked to approve, and the approval key is derived from
    # these targets — so two materially different calls would have shared one approval.
    destinations = [d.name for d in manifest_destinations(manifest)]
    targets = tuple(sorted({*(_candidate_paths_for_approval(raw)), *destinations}))
    return ApprovalRequest(
        kind=kind, component=manifest.id, action=action, targets=targets,
        risk=envelope.risk.name, summary=summary or f"run {manifest.id}",
        detail={"mutates": manifest.mutates, "component": manifest.id})
