"""Protocol boundaries: HarnessAdapter, MCP and A2A.

Three boundaries, each an adapter that turns something external into a component the
kernel already knows how to gate:

* ``HarnessAdapter`` — harness-to-harness composition (the one the first build needed).
* ``MCPToolAdapter`` — a server's ``tools/list`` entry becomes a ``ComponentManifest``.
  Annotations may tighten and never loosen; the destination is the operator's; the
  description is classified before a model sees it.
* ``A2AAgentAdapter`` — an ``AgentCard`` becomes a ``RemoteAgent`` whose messages go out
  through ``ToolGateway`` and whose delegations go through ``DelegationGateway``. The card
  grants nothing; ``input-required`` is escalated, never answered.

None of the three imports a protocol SDK. Each takes a transport callable and the
protocol's own JSON shapes, so the trust boundary lives here rather than inside a
library's session object, and the package stays at zero dependencies.
"""

from .a2a import A2AAgentAdapter, AgentCard, RemoteAgent
from .harness_adapter import (
    EvidenceBundle, ExecutionPlan, HarnessAdapter, HarnessManifest, HealthReport,
    ProvenanceCapsule, RunHandle, RunStatus,
)
from .mcp import REMOTE_DESTINATIONS, MCPComponent, MCPToolAdapter, MCPToolDescriptor
from .sable_adapter import SABLE_AVAILABLE, SableAdapter

__all__ = [
    "HarnessAdapter", "HarnessManifest", "ExecutionPlan", "RunHandle", "RunStatus",
    "EvidenceBundle", "ProvenanceCapsule", "HealthReport",
    "SableAdapter", "SABLE_AVAILABLE",
    "MCPToolAdapter", "MCPToolDescriptor", "MCPComponent", "REMOTE_DESTINATIONS",
    "A2AAgentAdapter", "AgentCard", "RemoteAgent",
]
