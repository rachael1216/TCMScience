"""MCP and A2A: external metadata is a claim, external data is input, and no new gate.

Both adapters exist to make one thing true: connecting a remote tool or agent adds no
execution path the kernel does not already gate. An MCP tool is a ``ComponentManifest``
whose destination is the operator's, so PHI to it is refused by the same ``ToolGateway``
check that refuses PHI to a public model. A remote agent is a component *and* a
delegation, so it passes ``DelegationGateway`` for its authority and ``ToolGateway`` for
its data. Neither gate was touched.

The tests that matter most are the ones that hold the protocols to their own words:
MCP's annotations are hints, and an ``AgentCard`` is untrusted.
"""

from __future__ import annotations

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.context import ContextCompiler
from psh.contracts import (
    Autonomy, ContextItem, ContractViolation, EgressDenied, PolicyDenied, RiskTier,
    RunEnvelope,
)
from psh.kernel import TrustedKernel
from psh.labels import DataLabel, Destination, Labeled, Sensitivity
from psh.policy import PolicySnapshot
from psh.protocols import (
    A2AAgentAdapter, AgentCard, MCPToolAdapter, MCPToolDescriptor, RemoteAgent,
)
from psh.runtime import (
    ChildState, Criterion, DelegationRequest, LoopLimits, AgentLoopController, Plan,
    PlanTask, StaticPlanner, Supervisor, TaskKind, Termination,
)

ALL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
       Destination.PERSISTENT, Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE)

PHI_TEXT = "Patient John Doe, MRN 4472213, admitted 2024-03-02 with HFpEF."
DEID_TEXT = "A cohort of 412 adults with HFpEF was followed for 18 months."


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
        policy=PolicySnapshot(profile_id="interop", allowed_destinations=ALL,
                              max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
                              risk_ceiling=RiskTier.R3_CLINICAL,
                              require_claim_support=False))
    yield k
    k.close()


class Transport:
    """A recording MCP/A2A transport that returns whatever it was told to."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else {"content": [{"type": "text",
                                                                    "text": "ok"}]}
        self.calls: list = []

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return self.reply

    def send(self, message):
        self.calls.append(message)
        return self.reply


def descriptor(name="search", description="Search the literature.", **annotations):
    return MCPToolDescriptor(name=name, description=description,
                             input_schema={"type": "object"}, annotations=annotations)


# ======================================================= MCP: hints are hints

def test_a_read_only_hint_does_not_relax_mutates(kernel):
    """MCP's own docs: annotations are hints, not security guarantees. So they cannot relax."""
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool)
    component = adapter.admit(descriptor(readOnlyHint=True, idempotentHint=True))
    assert component.manifest.mutates is True
    assert component.manifest.idempotent is False
    assert component.manifest.provenance["annotations"]["readOnlyHint"] is True, \
        "the hint is recorded; it is not believed"


def test_a_destructive_hint_tightens(kernel):
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool)
    component = adapter.admit(descriptor(destructiveHint=True))
    assert component.manifest.human_approval is True
    assert component.manifest.risk_tier is RiskTier.R3_CLINICAL


def test_only_an_operator_override_relaxes_a_default(kernel):
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool,
                             overrides={"search": {"mutates": False,
                                                   "risk_tier": RiskTier.R1_ROUTINE}})
    component = adapter.admit(descriptor())
    assert component.manifest.mutates is False
    assert component.manifest.risk_tier is RiskTier.R1_ROUTINE
    assert component.manifest.provenance["overrides"] == {
        "mutates": False, "risk_tier": RiskTier.R1_ROUTINE}


def test_the_destination_is_the_operators_not_the_servers(kernel):
    with pytest.raises(PolicyDenied, match="remote destination"):
        MCPToolAdapter(kernel, server="lit", destination=Destination.LOCAL_COMPUTE,
                       call_tool=Transport().call_tool)
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.TRUSTED_REMOTE,
                             call_tool=Transport().call_tool)
    component = adapter.admit(descriptor())
    assert component.manifest.destinations == (Destination.TRUSTED_REMOTE,)
    assert component.manifest.requires_network is True


def test_the_adapter_cannot_raise_the_destinations_ceiling(kernel):
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool, max_label=Sensitivity.PHI)
    assert adapter.max_label is Sensitivity.RESEARCH_DEIDENTIFIED, \
        "an adapter is not a policy; the destination's ceiling wins"


# ================================================ MCP: the existing gates apply

def test_phi_cannot_reach_an_mcp_tool_on_a_public_server(kernel):
    """The whole point: no MCP-specific check, the ToolGateway does it."""
    transport = Transport()
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=transport.call_tool)
    component = adapter.admit(descriptor())
    with pytest.raises(EgressDenied):
        kernel.broker.call_tool(component, {"query": PHI_TEXT}, kernel.policy.envelope())
    assert transport.calls == [], "the payload reached the server before being refused"


def test_deidentified_data_can_reach_it_and_the_reply_is_classified(kernel):
    transport = Transport(reply={"content": [{"type": "text", "text": PHI_TEXT}]})
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=transport.call_tool)
    component = adapter.admit(descriptor())
    result = kernel.broker.call_tool(component, {"query": DEID_TEXT},
                                     kernel.policy.envelope())
    assert transport.calls[0][0] == "search"
    assert result.label.sensitivity is Sensitivity.PHI, \
        "a reply that carries an identifier is PHI whatever the server calls it"


def test_an_error_reply_is_a_contract_violation_not_a_result(kernel):
    transport = Transport(reply={"content": [{"type": "text", "text": "quota exceeded"}],
                                 "isError": True})
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=transport.call_tool)
    component = adapter.admit(descriptor())
    with pytest.raises(ContractViolation, match="quota exceeded"):
        kernel.broker.call_tool(component, {"query": DEID_TEXT}, kernel.policy.envelope())


def test_runtime_bookkeeping_is_not_leaked_to_the_server(kernel):
    transport = Transport()
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=transport.call_tool)
    component = adapter.admit(descriptor())
    kernel.broker.call_tool(component, {"query": "x", "_psh_idempotency_key": "run:task"},
                            kernel.policy.envelope())
    assert "_psh_idempotency_key" not in transport.calls[0][1]


def test_a_tool_name_is_namespaced_and_collisions_are_refused(kernel):
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool)
    weird = adapter.admit(descriptor(name="search/papers v2"))
    assert weird.manifest.id == "mcp.lit.search_papers_v2"
    assert weird.manifest.provenance["tool"] == "search/papers v2"
    with pytest.raises(ContractViolation, match="shadow"):
        adapter.admit(descriptor(name="search papers:v2"))     # sanitises to the same id
    # A different name that sanitises differently is not a collision.
    assert adapter.admit(descriptor(name="search_papers-v2")).manifest.id == \
        "mcp.lit.search_papers-v2"


def test_a_description_carrying_phi_cannot_reach_a_public_model(kernel):
    """A server's description is rendered into the model's context. It is classified."""
    adapter = MCPToolAdapter(kernel, server="ehr", destination=Destination.TRUSTED_REMOTE,
                             call_tool=Transport().call_tool)
    component = adapter.admit(descriptor(name="chart", description=PHI_TEXT))
    assert component.manifest.provenance["description_sensitivity"] == "PHI"

    registry = CapabilityRegistry()
    registry.register(component)
    candidates = registry.resolve("chart", kernel.policy.envelope())
    (item,) = registry.manifest_items(candidates)
    assert item.label.sensitivity is Sensitivity.PHI

    envelope = kernel.policy.envelope()
    public = ContextCompiler().compile(
        items=[ContextItem(kind="turn", content="which tools?"), item],
        envelope=envelope, destination=Destination.PUBLIC_REMOTE)
    local = ContextCompiler().compile(
        items=[ContextItem(kind="turn", content="which tools?"), item],
        envelope=envelope, destination=Destination.LOCAL_MODEL)
    assert "MRN" not in public.render(), "the description reached a public model's context"
    assert "MRN" in local.render()


def test_an_mcp_tool_is_governed_by_the_loop_like_any_other(kernel):
    transport = Transport(reply={"content": [{"type": "text", "text": DEID_TEXT}]})
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=transport.call_tool,
                             overrides={"search": {"mutates": False}})
    registry = CapabilityRegistry()
    adapter.register_into(registry, [descriptor()])
    plan = Plan(objective="search", produced_by="test",
                # The MCP manifest's conservative default is R2; a task must ask for at
                # least that or the validator refuses the plan — which the first version of
                # this test demonstrated by accident.
                tasks=(PlanTask(task_id="s", objective="search", kind=TaskKind.TOOL,
                                component_id="mcp.lit.search", payload={"query": "HFpEF"},
                                max_risk=RiskTier.R2_CONSEQUENTIAL,
                                destinations=(Destination.PUBLIC_REMOTE,)),),
                completion_criteria=(Criterion(description="ran", kind="task"),))
    before = kernel.broker.stats()["tool_calls"]
    result = AgentLoopController(kernel, planner=StaticPlanner(plan), registry=registry,
                                 limits=LoopLimits(max_iterations=4),
                                 sleep=lambda s: None).run("search", kernel.policy.envelope())
    assert result.termination is Termination.GOAL_SATISFIED, result.summary()
    assert kernel.broker.stats()["tool_calls"] == before + 1
    assert len(transport.calls) == 1


def test_admit_all_records_a_refusal_without_losing_the_rest(kernel):
    adapter = MCPToolAdapter(kernel, server="lit", destination=Destination.PUBLIC_REMOTE,
                             call_tool=Transport().call_tool)
    admitted = adapter.admit_all([{"name": "a"}, {"name": ""}, {"name": "b"}])
    assert [c.tool_name for c in admitted] == ["a", "b"]
    assert len(adapter.refused) == 1
    assert adapter.stats()["admitted"] == 2


# ================================================= A2A: the card grants nothing

def card(**kw):
    base = {"name": "trial-finder", "url": "https://agents.example.org/a2a",
            "description": "Finds clinical trials.",
            "skills": [{"id": "find", "name": "find trials", "tags": ["trials", "search"]}],
            "capabilities": {"streaming": True, "pushNotifications": True}}
    base.update(kw)
    return AgentCard.from_wire(base)


def test_an_agent_card_is_not_a_trusted_manifest(kernel):
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=Transport().send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card(capabilities={"admin": True, "readsPHI": True}))
    m = agent.manifest
    assert m.destinations == (Destination.PUBLIC_REMOTE,), "the operator's, not the card's"
    assert m.mutates is True and m.risk_tier is RiskTier.R2_CONSEQUENTIAL
    assert m.max_label is Sensitivity.RESEARCH_DEIDENTIFIED
    assert m.intents == ("find trials",) and "trials" in m.tags, "skills are retrieval metadata"
    assert m.provenance["capabilities_claimed"] == {"admin": True, "readsPHI": True}


def test_a_card_pointing_outside_the_allowed_hosts_is_refused(kernel):
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=Transport().send, allowed_hosts=["agents.example.org"])
    with pytest.raises(PolicyDenied, match="allowed hosts"):
        adapter.admit(card(url="https://evil.example.net/a2a"))


def test_an_adapter_without_allowed_hosts_does_not_construct(kernel):
    with pytest.raises(PolicyDenied, match="allowed_hosts"):
        A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE, send=Transport().send)


# ============================================ A2A: both gates, no new ones

def completed_task(claims=(), evidence=(), text="done"):
    return {"id": "t1", "status": {"state": "completed"},
            "artifacts": [{"artifactId": "a1", "parts": [
                {"kind": "text", "text": text},
                {"kind": "data", "data": {"claims": list(claims), "evidence": list(evidence)}}]}]}


def supervisor_with(kernel, agent):
    return Supervisor(kernel, agent.backend, max_concurrency=2, lease_ttl_s=5.0)


def test_phi_cannot_be_delegated_to_a_public_remote_agent(kernel):
    """DelegationGateway admits the authority; ToolGateway refuses the data. Both ran."""
    from psh.contracts import ContextProjection

    transport = Transport(reply=completed_task())
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    parent = kernel.policy.envelope()
    projection = ContextProjection(
        items=(ContextItem(kind="evidence", content=PHI_TEXT,
                           label=DataLabel(Sensitivity.PHI)),),
        label=DataLabel(Sensitivity.PHI))

    supervisor = supervisor_with(kernel, agent)
    contract = supervisor.mint(DelegationRequest(objective="find trials",
                                                 budget_fraction=0.3), parent)
    contract = type(contract)(**{**contract.__dict__, "projection": projection}) \
        if hasattr(contract, "__dict__") else contract
    from dataclasses import replace
    contract = replace(contract, projection=projection)

    gate_before = kernel.tool_gateway.checks
    with pytest.raises(EgressDenied):
        kernel.broker.delegate(contract, parent, agent.backend)
    assert transport.calls == [], "PHI reached the remote agent"
    assert kernel.tool_gateway.checks == gate_before + 1
    supervisor.close()


def test_deidentified_delegation_returns_a_summary_with_claims(kernel):
    transport = Transport(reply=completed_task(
        claims=["Empagliflozin reduces hospitalisation."], evidence=["PMID 34449189"]))
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    (child,) = supervisor.dispatch(
        [DelegationRequest(objective="find HFpEF trials", budget_fraction=0.3)],
        kernel.policy.envelope())

    assert child.state is ChildState.COMPLETED, child.error
    result = child.result
    assert result.termination == Termination.GOAL_SATISFIED.value
    assert result.claims == ("Empagliflozin reduces hospitalisation.",)
    assert result.evidence == ("PMID 34449189",)
    assert result.artifacts == ("a1",)
    assert len(transport.calls) == 1
    sent = transport.calls[0]["message"]["parts"][0]["text"]
    assert "find HFpEF trials" in sent
    supervisor.close()


def test_a_remote_reply_is_classified(kernel):
    transport = Transport(reply=completed_task(text=PHI_TEXT))
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    (child,) = supervisor.dispatch(
        [DelegationRequest(objective="find trials", budget_fraction=0.3)],
        kernel.policy.envelope())
    assert child.result.label.sensitivity is Sensitivity.PHI
    supervisor.close()


def test_input_required_is_escalated_never_answered(kernel):
    transport = Transport(reply={"id": "t1", "status": {
        "state": "input-required",
        "message": {"parts": [{"kind": "text", "text": "Which population?"}]}}})
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    (child,) = supervisor.dispatch(
        [DelegationRequest(objective="find trials", budget_fraction=0.3)],
        kernel.policy.envelope())
    assert child.result.termination == Termination.ESCALATED.value
    assert "Which population?" in child.result.summary
    assert len(transport.calls) == 1, "the runtime answered the question itself"
    supervisor.close()


def test_a_non_terminal_reply_is_not_reported_as_a_result(kernel):
    transport = Transport(reply={"id": "t1", "status": {"state": "working"}})
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    (child,) = supervisor.dispatch(
        [DelegationRequest(objective="x", budget_fraction=0.3)], kernel.policy.envelope())
    assert child.result.termination == Termination.ESCALATED.value
    assert "not terminal" in child.result.summary
    supervisor.close()


def test_a_remote_failure_is_reported(kernel):
    transport = Transport(reply={"id": "t1", "status": {"state": "failed"}})
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    (child,) = supervisor.dispatch(
        [DelegationRequest(objective="x", budget_fraction=0.3)], kernel.policy.envelope())
    assert child.state is ChildState.COMPLETED           # the delegation completed...
    assert child.result.termination == Termination.UNRECOVERABLE_ERROR.value  # ...honestly
    assert child.result.failures
    supervisor.close()


def test_a_cancelled_token_prevents_the_send(kernel):
    from psh.runtime import CancellationToken

    transport = Transport(reply=completed_task())
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    contract = supervisor.mint(DelegationRequest(objective="x", budget_fraction=0.3),
                               kernel.policy.envelope())
    token = CancellationToken()
    token.cancel("stop")
    result = agent.backend(contract, token=token)
    assert result.termination == Termination.CANCELLED.value
    assert transport.calls == []
    supervisor.close()


def test_a_remote_delegation_passes_both_gates_once_each(kernel):
    transport = Transport(reply=completed_task())
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    supervisor = supervisor_with(kernel, agent)
    d0, t0 = kernel.delegation_gateway.checks, kernel.tool_gateway.checks
    supervisor.dispatch([DelegationRequest(objective="x", budget_fraction=0.3)],
                        kernel.policy.envelope())
    assert kernel.delegation_gateway.checks == d0 + 1
    assert kernel.tool_gateway.checks == t0 + 1
    kinds = [e.event_type for e in kernel.events.records()]
    assert "a2a_agent_admitted" in kinds and "delegation" in kinds and "tool_call" in kinds
    assert kernel.events.verify().intact
    supervisor.close()


def test_a_malformed_reply_is_a_contract_violation(kernel):
    transport = Transport(reply="just a string")
    adapter = A2AAgentAdapter(kernel, destination=Destination.PUBLIC_REMOTE,
                              send=transport.send, allowed_hosts=["agents.example.org"])
    agent = adapter.admit(card())
    with pytest.raises(ContractViolation, match="not a task object"):
        kernel.broker.call_tool(agent, {"message": {"parts": []}}, kernel.policy.envelope())
