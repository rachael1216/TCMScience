"""MCP tools as governed components: a server's self-description is a claim, not a policy.

The Model Context Protocol's own documentation says its tool annotations —
``readOnlyHint``, ``destructiveHint``, ``idempotentHint`` — are **hints, not security
guarantees**. A2A says the same of an ``AgentCard``. This adapter takes both at their word.
It turns one entry of a server's ``tools/list`` into a ``ComponentManifest`` under three
rules, and each rule closes a hole that the obvious adapter would have:

**Annotations may tighten and never loosen.** ``destructiveHint=True`` makes the manifest
require approval and raises its risk tier. ``readOnlyHint=True`` changes *nothing*: the
manifest still says ``mutates=True``, because a server that can lie about being read-only
is a server that can lie, and the whole point of the manifest is that the gate believes it.
The only way to relax a default is an operator ``override``, recorded in provenance as the
operator's decision rather than the server's.

**The destination is the operator's, never the server's.** Whether a server is
``TRUSTED_REMOTE`` (under an agreement) or ``PUBLIC_REMOTE`` is a fact about the deployment,
stated at adapter construction. It is then the ordinary destination the ``ToolGateway``
already gates on, which is what makes the rest of this module short: PHI to an MCP tool on
a public server is refused by the same check that refuses PHI to a public model, with no
MCP-specific code involved.

**Everything the server sends is classified on the way in.** Its tool descriptions are
rendered into the model's context, so a description is a place a server could put PHI or
an instruction; it is classified at ingress and the registry labels the rendered item
accordingly, so a description that carries PHI cannot reach a public model. Its results
come back through ``ExecutionResult.from_component``, classified like any component's.

No MCP SDK is imported. The transport is a callable — ``call_tool(name, arguments)`` — and
the wire shapes are the protocol's JSON, so the official client, a stdio subprocess or a
test double all fit the same seam. That keeps this package at zero dependencies and keeps
the trust boundary here rather than inside a library's session object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contracts import (
    ComponentKind, ComponentManifest, ContractViolation, PolicyDenied, RiskTier,
    _VALID_COMPONENT_ID,
)
from ..labels import DEFAULT_CEILINGS, Destination, Sensitivity

__all__ = ["MCPToolDescriptor", "MCPToolAdapter", "MCPComponent", "REMOTE_DESTINATIONS"]

REMOTE_DESTINATIONS = (Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE)

#: The longest description rendered into a model's context. A server that returns a
#: novel as a description is either broken or trying something; either way the model gets
#: the first part and the audit record gets the length.
MAX_DESCRIPTION_CHARS = 500

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    """One entry of ``tools/list``, as the protocol sends it."""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    annotations: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "MCPToolDescriptor":
        """Accept the protocol's camelCase and a snake_case rendering of it."""
        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            input_schema=dict(data.get("inputSchema") or data.get("input_schema") or {}),
            annotations=dict(data.get("annotations") or {}))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an MCP tool descriptor needs a name")


class MCPComponent:
    """The component the broker invokes. Holds the manifest and the transport, nothing else."""

    def __init__(self, manifest: ComponentManifest, tool_name: str,
                 call_tool: Callable[[str, Mapping[str, Any]], Any]) -> None:
        self.manifest = manifest
        self.tool_name = tool_name
        self._call_tool = call_tool
        self.calls = 0

    def invoke(self, payload: Any, envelope: Any) -> Any:
        """Send the arguments, normalise the reply. The broker has already gated both."""
        arguments = {k: v for k, v in (payload or {}).items()
                     if not str(k).startswith("_psh_")} if isinstance(payload, Mapping) \
            else {"input": payload}
        self.calls += 1
        reply = self._call_tool(self.tool_name, arguments)
        return _normalise_result(self.tool_name, reply)


def _normalise_result(tool_name: str, reply: Any) -> Any:
    """Turn a ``tools/call`` reply into a plain value, refusing an ``isError`` reply.

    The protocol's reply is ``{"content": [parts...], "isError": bool}``. An error reply
    is an error, not a result with an odd shape: returning it as a value would let a
    server's failure be recorded as this component's successful output.
    """
    if not isinstance(reply, Mapping) or "content" not in reply:
        return reply
    parts = reply.get("content") or []
    texts = [str(p.get("text", "")) for p in parts
             if isinstance(p, Mapping) and p.get("type") == "text"]
    if reply.get("isError"):
        raise ContractViolation(
            f"MCP tool {tool_name!r} reported an error: {' '.join(texts)[:300]}")
    structured = reply.get("structuredContent")
    if structured is not None:
        return structured
    if len(texts) == 1:
        return texts[0]
    return {"content": parts}


class MCPToolAdapter:
    """Admits a server's tools as governed components. The operator states the terms."""

    def __init__(self, kernel: Any, *, server: str, destination: Destination,
                 call_tool: Callable[[str, Mapping[str, Any]], Any],
                 allowed_hosts: Sequence[str] = (),
                 max_label: Sensitivity | None = None,
                 overrides: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        if destination not in REMOTE_DESTINATIONS:
            # An MCP server is a process this kernel does not own, reached over a
            # transport. Calling it LOCAL_COMPUTE would exempt it from every egress check.
            raise PolicyDenied(
                f"an MCP server is a remote destination; {destination.name} is not one of "
                f"{[d.name for d in REMOTE_DESTINATIONS]}")
        if not _VALID_COMPONENT_ID.fullmatch(server):
            raise ValueError(f"server name {server!r} must be a plain identifier")
        self.kernel = kernel
        self.server = server
        self.destination = destination
        self.call_tool = call_tool
        self.allowed_hosts = tuple(allowed_hosts)
        #: The most sensitive data a tool on this server may receive: the destination's
        #: ceiling, or lower if the operator says so. Never higher — the ceiling is the
        #: kernel's, and an adapter is not a policy.
        ceiling = DEFAULT_CEILINGS[destination]
        self.max_label = ceiling if max_label is None else min(max_label, ceiling)
        self.overrides = {k: dict(v) for k, v in (overrides or {}).items()}
        self.admitted: dict[str, MCPComponent] = {}
        self.refused: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ admit
    def component_id(self, tool_name: str) -> str:
        return f"mcp.{self.server}.{_UNSAFE_ID.sub('_', tool_name)}"

    def admit(self, descriptor: MCPToolDescriptor | Mapping[str, Any]) -> MCPComponent:
        """Build the manifest for one tool. Conservative by default, tightened by hints."""
        if not isinstance(descriptor, MCPToolDescriptor):
            descriptor = MCPToolDescriptor.from_wire(descriptor)

        component_id = self.component_id(descriptor.name)
        existing = self.admitted.get(component_id)
        if existing is not None and existing.tool_name != descriptor.name:
            # Two different names that sanitise to one id. Refuse rather than let the
            # second silently replace the first — a server could otherwise shadow a tool.
            self.refused.append((descriptor.name, f"id collides with {existing.tool_name!r}"))
            raise ContractViolation(
                f"MCP tool {descriptor.name!r} sanitises to {component_id!r}, which is "
                f"already held by {existing.tool_name!r}; refusing to shadow it")

        # The description is untrusted text that will be rendered into a model's context.
        raw_description = _CONTROL.sub("", descriptor.description)[:MAX_DESCRIPTION_CHARS]
        labelled = self.kernel.ingress.ensure(raw_description or descriptor.name,
                                              origin=f"mcp:{self.server}")
        annotations = dict(descriptor.annotations)

        # Conservative defaults. A remote tool nobody has vouched for mutates, is not
        # idempotent, and is consequential.
        fields: dict[str, Any] = dict(
            mutates=True, idempotent=False, risk_tier=RiskTier.R2_CONSEQUENTIAL,
            human_approval=False)
        # Hints tighten only.
        if annotations.get("destructiveHint") is True:
            fields["human_approval"] = True
            fields["risk_tier"] = RiskTier.R3_CLINICAL
        # ``readOnlyHint`` and ``idempotentHint`` are deliberately not consulted: each
        # would *relax* a default on the server's say-so.
        applied_overrides = self.overrides.get(descriptor.name, {})
        fields.update(applied_overrides)

        manifest = ComponentManifest(
            id=component_id, name=descriptor.name, kind=ComponentKind.TOOL,
            publisher=f"mcp:{self.server}",
            description=str(labelled.value),
            input_schema=dict(descriptor.input_schema),
            backend="python",           # the CLIENT runs in-process; see module docstring
            allowed_hosts=self.allowed_hosts,
            max_label=self.max_label,
            destinations=(self.destination,),
            requires_network=True,
            integration_mode="federated",
            provenance={
                "source": "mcp", "server": self.server, "tool": descriptor.name,
                "description_sensitivity": labelled.label.sensitivity.name,
                "description_chars": len(descriptor.description),
                "annotations": annotations,
                "overrides": applied_overrides,
            },
            **fields)
        component = MCPComponent(manifest, descriptor.name, self.call_tool)
        self.admitted[component_id] = component
        audit = getattr(self.kernel, "audit", None)
        if audit is not None:
            audit("mcp_tool_admitted", component_id=component_id,
                  detail={"server": self.server, "destination": self.destination.name,
                          "description_sensitivity": labelled.label.sensitivity.name,
                          "approval": manifest.human_approval,
                          "overridden": sorted(applied_overrides)})
        return component

    def admit_all(self, descriptors: Iterable[MCPToolDescriptor | Mapping[str, Any]]
                  ) -> list[MCPComponent]:
        """Admit what can be admitted; a refused tool is recorded, not fatal to the rest."""
        out: list[MCPComponent] = []
        for descriptor in descriptors:
            try:
                out.append(self.admit(descriptor))
            except (ContractViolation, ValueError) as exc:
                name = getattr(descriptor, "name", None) or (
                    descriptor.get("name") if isinstance(descriptor, Mapping) else "?")
                if not any(n == name for n, _ in self.refused):
                    self.refused.append((str(name), str(exc)))
        return out

    def register_into(self, registry: Any, descriptors: Iterable[Any]) -> list[MCPComponent]:
        components = self.admit_all(descriptors)
        for component in components:
            registry.register(component)
        return components

    def stats(self) -> dict[str, Any]:
        return {"server": self.server, "destination": self.destination.name,
                "admitted": len(self.admitted), "refused": len(self.refused),
                "calls": sum(c.calls for c in self.admitted.values())}
