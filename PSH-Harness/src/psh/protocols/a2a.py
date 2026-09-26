"""Remote agents over A2A: an AgentCard is a claim, and a remote reply is input.

The Agent2Agent protocol describes a remote agent with an ``AgentCard`` — name, skills,
capabilities, a URL — and hands work to it as a task whose reply carries artifacts and a
status. Its own samples say every one of those must be treated as untrusted. This adapter
does, and the consequence is that delegation to a remote agent needs **no new gate**: it
runs through the two that exist.

    Supervisor.dispatch --> broker.delegate      DelegationGateway: the child's authority
                              |
                        RemoteAgent.backend
                              |
                        broker.call_tool        ToolGateway: the outbound data vs the
                              |                 remote destination, approval, hooks, audit
                          transport.send
                              |
                        ExecutionResult         classified on the way back in

The first gate answers "may this child hold this authority" and the second "may this data
leave for that destination". Both were built for local work; a remote agent is the case
where they are most obviously necessary, and the fact that neither needed changing is the
argument for having built them as gates rather than as features of the local path.

Three rules, mirroring the MCP adapter:

* **The card grants nothing.** Its ``capabilities`` and ``skills`` become retrieval
  metadata (``intents``, ``tags``); its ``url`` must fall inside the operator's
  ``allowed_hosts`` or the card is refused; the destination class is the operator's.
* **``input-required`` is escalated, never answered.** A remote agent asking a question
  is asking a human. The loop has no business inventing the answer.
* **The reply's label is the join** of what was sent and what came back, classified — a
  remote agent that echoes a patient's name has produced PHI whatever it calls it.

No A2A SDK is imported. The transport is ``send(message) -> task``, with the protocol's
JSON shapes, so the official client and a test double share one seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from ..contracts import (
    ComponentKind, ComponentManifest, ContractViolation, DelegationContract, PolicyDenied,
    RiskTier, _VALID_COMPONENT_ID,
)
from ..labels import DEFAULT_CEILINGS, DataLabel, Destination, Sensitivity
from ..runtime.subagent import SubagentResult
from .mcp import REMOTE_DESTINATIONS

__all__ = ["AgentCard", "A2AAgentAdapter", "RemoteAgent"]

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_DESCRIPTION_CHARS = 500

#: A2A task states, mapped onto this runtime's named terminations.
_TERMINATION_FOR = {
    "completed": "goal_satisfied",
    "failed": "unrecoverable_error",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "rejected": "policy_denied",
    "input-required": "escalated",
    "input_required": "escalated",
    "auth-required": "escalated",
}


@dataclass(frozen=True, slots=True)
class AgentCard:
    """The protocol's description of a remote agent. Metadata, not a manifest."""

    name: str
    url: str
    description: str = ""
    version: str = ""
    skills: tuple[Mapping[str, Any], ...] = ()
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    provider: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "AgentCard":
        return cls(
            name=str(data.get("name") or ""), url=str(data.get("url") or ""),
            description=str(data.get("description") or ""),
            version=str(data.get("version") or ""),
            skills=tuple(dict(s) for s in data.get("skills") or () if isinstance(s, Mapping)),
            capabilities=dict(data.get("capabilities") or {}),
            provider=dict(data.get("provider") or {}))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an AgentCard needs a name")
        if not self.url.strip():
            raise ValueError("an AgentCard needs a url")

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()


class RemoteAgent:
    """A remote agent as a component, so the ToolGateway gates every message to it.

    ``invoke`` is what the broker calls: it sends one message and returns the task reply.
    ``backend`` is what the supervisor calls: it builds the message from a delegation
    contract, routes it through ``broker.call_tool`` — which is where the outbound data
    meets the remote destination's ceiling — and reduces the reply to a ``SubagentResult``.
    """

    def __init__(self, manifest: ComponentManifest, card: AgentCard, kernel: Any,
                 send: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> None:
        self.manifest = manifest
        self.card = card
        self.kernel = kernel
        self._send = send
        self.calls = 0

    # ------------------------------------------------------------- component
    def invoke(self, payload: Any, envelope: Any) -> Mapping[str, Any]:
        message = dict(payload) if isinstance(payload, Mapping) else {"text": str(payload)}
        message = {k: v for k, v in message.items() if not str(k).startswith("_psh_")}
        self.calls += 1
        reply = self._send(message)
        if not isinstance(reply, Mapping):
            raise ContractViolation(
                f"remote agent {self.card.name!r} replied with {type(reply).__name__}, "
                "not a task object")
        return reply

    # --------------------------------------------------------------- backend
    def backend(self, contract: DelegationContract, token: Any = None,
                heartbeat: Callable[[], None] | None = None) -> SubagentResult:
        """The delegation backend. Same signature as ``LocalSubagentBackend``."""
        if token is not None and getattr(token, "cancelled", False):
            return SubagentResult(child_run_id=contract.envelope.run_id, summary="not sent",
                                  termination="cancelled")
        if heartbeat is not None:
            heartbeat()

        # The message is the objective plus the contract's projection — compiled context
        # for this delegate, never the parent's transcript. Its label is the projection's,
        # and the ToolGateway compares that label to the remote destination.
        projection = contract.projection
        text = contract.objective
        rendered = projection.render() if projection is not None else ""
        if rendered:
            text = f"{contract.objective}\n\n{rendered}"
        payload: Any = {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]},
                        "output_schema": dict(contract.output_schema)}
        if projection is not None:
            from ..labels import Labeled
            payload = Labeled(value=payload, label=projection.label,
                              origin=f"a2a:{self.card.name}")

        # THE gate for the data. Label vs destination, approval, hooks, audit, and the
        # reply classified on the way in — none of it written for A2A.
        result = self.kernel.broker.call_tool(self, payload, contract.envelope)
        reply = result.value if hasattr(result, "value") else result
        label = getattr(result, "label", DataLabel())
        return self._reduce(reply, contract, label)

    def _reduce(self, reply: Mapping[str, Any], contract: DelegationContract,
                label: DataLabel) -> SubagentResult:
        status = reply.get("status") or {}
        state = str(status.get("state") if isinstance(status, Mapping) else status or "")
        state = state.strip().lower()
        termination = _TERMINATION_FOR.get(state)
        if termination is None:
            # working / submitted / unknown: the transport returned before the task
            # ended. Polling is not implemented, and pretending a partial reply is a
            # result would be the kind of claim this package exists to refuse.
            termination = "escalated"
            note = f"remote task state {state or 'unknown'!r} is not terminal"
        else:
            note = state

        claims: list[str] = []
        evidence: list[str] = []
        artifacts: list[str] = []
        lines: list[str] = []
        for artifact in reply.get("artifacts") or ():
            if not isinstance(artifact, Mapping):
                continue
            artifacts.append(str(artifact.get("artifactId") or artifact.get("name") or "artifact"))
            for part in artifact.get("parts") or ():
                if not isinstance(part, Mapping):
                    continue
                data = part.get("data")
                if isinstance(data, Mapping):
                    # Structured parts are the only ones claims are read from. Free text
                    # from a remote agent is a summary line, not a fact.
                    claims.extend(str(c) for c in data.get("claims") or ())
                    evidence.extend(str(e) for e in data.get("evidence") or ())
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    lines.append(" ".join(text.split())[:200])
        message = status.get("message") if isinstance(status, Mapping) else None
        if isinstance(message, Mapping):
            for part in message.get("parts") or ():
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    lines.append(" ".join(part["text"].split())[:200])

        summary = "\n".join([f"{termination}: remote agent {self.card.name} — {note}"]
                            + [f"- {line}" for line in lines[:8]])
        return SubagentResult(
            child_run_id=contract.envelope.run_id, summary=summary, termination=termination,
            claims=tuple(dict.fromkeys(claims)), evidence=tuple(dict.fromkeys(evidence)),
            artifacts=tuple(dict.fromkeys(artifacts)), label=label,
            failures=() if termination == "goal_satisfied" else (note,))


class A2AAgentAdapter:
    """Admits remote agents from their cards, on the operator's terms."""

    def __init__(self, kernel: Any, *, destination: Destination,
                 send: Callable[[Mapping[str, Any]], Mapping[str, Any]],
                 allowed_hosts: Sequence[str] = (),
                 max_label: Sensitivity | None = None) -> None:
        if destination not in REMOTE_DESTINATIONS:
            raise PolicyDenied(
                f"a remote agent is a remote destination; {destination.name} is not one of "
                f"{[d.name for d in REMOTE_DESTINATIONS]}")
        if not allowed_hosts:
            # Without this the card's url decides where the kernel's data goes.
            raise PolicyDenied(
                "an A2A adapter needs allowed_hosts; a card's url is a claim and must fall "
                "inside hosts the operator named")
        self.kernel = kernel
        self.destination = destination
        self.send = send
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        ceiling = DEFAULT_CEILINGS[destination]
        self.max_label = ceiling if max_label is None else min(max_label, ceiling)
        self.admitted: dict[str, RemoteAgent] = {}

    def admit(self, card: AgentCard | Mapping[str, Any]) -> RemoteAgent:
        if not isinstance(card, AgentCard):
            card = AgentCard.from_wire(card)
        if card.host not in self.allowed_hosts:
            raise PolicyDenied(
                f"AgentCard {card.name!r} points at {card.host or card.url!r}, which is not "
                f"among the operator's allowed hosts {list(self.allowed_hosts)}")

        raw = _CONTROL.sub("", card.description)[:MAX_DESCRIPTION_CHARS]
        labelled = self.kernel.ingress.ensure(raw or card.name, origin=f"a2a:{card.name}")
        component_id = f"a2a.{_UNSAFE_ID.sub('_', card.name)}"
        if not _VALID_COMPONENT_ID.fullmatch(component_id):
            raise ContractViolation(f"AgentCard name {card.name!r} cannot form a component id")

        # Skills become retrieval metadata. They grant nothing.
        intents = tuple(dict.fromkeys(
            str(s.get("name") or s.get("id") or "") for s in card.skills
            if s.get("name") or s.get("id")))
        tags = tuple(dict.fromkeys(
            str(t) for s in card.skills for t in (s.get("tags") or ())))

        manifest = ComponentManifest(
            id=component_id, name=card.name, kind=ComponentKind.AGENT,
            version=card.version or "0", publisher=f"a2a:{card.host}",
            description=str(labelled.value), intents=intents, tags=tags,
            backend="python", allowed_hosts=(card.host,),
            max_label=self.max_label, destinations=(self.destination,),
            requires_network=True, mutates=True, idempotent=False,
            risk_tier=RiskTier.R2_CONSEQUENTIAL, integration_mode="federated",
            provenance={"source": "a2a", "url": card.url,
                        "description_sensitivity": labelled.label.sensitivity.name,
                        "capabilities_claimed": dict(card.capabilities),
                        "provider": dict(card.provider)})
        agent = RemoteAgent(manifest, card, self.kernel, self.send)
        self.admitted[component_id] = agent
        audit = getattr(self.kernel, "audit", None)
        if audit is not None:
            audit("a2a_agent_admitted", component_id=component_id,
                  detail={"host": card.host, "destination": self.destination.name,
                          "description_sensitivity": labelled.label.sensitivity.name,
                          "skills": len(card.skills)})
        return agent
