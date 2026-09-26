"""Adversarial tests on gate *composition*, not on gates.

The v0.5.1 review asked for this round specifically, and the reason is in its P0-2 finding.
Neither half of that defect was wrong on its own terms:

    ToolGateway      reads manifest.destinations[0]   -> a defensible reading
    IsolatedExecutor reads any(d in remote for d ...) -> a defensible reading

The leak was that both were true at once. No test of either gate could have found it,
because each gate was answering its own question correctly. So these tests are written the
other way round: construct the case where **every gate individually says yes** and assert
the composition still refuses — or, where a gate legitimately allows something, assert the
next gate is the one that stops it and that it is actually reached.

Each test names the two (or three) controls whose seam it is probing.
"""

from __future__ import annotations

import sys

import pytest

from psh.config import PSHConfig
from psh.contracts import (
    ApprovalRequired, Autonomy, Budget, ComponentKind, ComponentManifest, ContextItem,
    DelegationContract, EgressDenied, ModelProfile, PolicyDenied, RiskTier, RunEnvelope,
    VerificationFailed,
)
from psh.context import ContextCompiler
from psh.kernel import TrustedKernel
from psh.kernel.egress import ToolGateway, manifest_destinations, widest_destination
from psh.kernel.isolation import IsolatedExecutor, IsolatedRunner
from psh.labels import DataLabel, Destination, Labeled, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import Runner

LOCAL_ONLY = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
              Destination.PERSISTENT)


def open_policy(**kw):
    params = dict(profile_id="open", max_data_label=Sensitivity.PHI,
                  allowed_destinations=LOCAL_ONLY + (Destination.TRUSTED_REMOTE,
                                                     Destination.PUBLIC_REMOTE),
                  autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL,
                  require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


# ============================== ToolGateway x IsolatedExecutor (the P0-2 seam)

def test_no_manifest_passes_the_gate_and_still_reaches_the_network():
    """The invariant behind P0-2, stated as a property over every destination subset.

    For every combination of destinations a manifest could declare, and every sensitivity,
    it must never be true that the gate admits the payload *and* the executor grants the
    component remote hosts that the payload may not reach.
    """
    import itertools

    envelope = RunEnvelope(
        max_label=DataLabel(Sensitivity.PHI), risk=RiskTier.R3_CLINICAL,
        autonomy=Autonomy.ACT, allowed_destinations=frozenset(Destination))
    executor = IsolatedExecutor(IsolatedRunner(), workdir_root="/tmp/psh-compose")
    remote = {Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE}
    leaks = []

    all_destinations = list(Destination)
    for size in (1, 2, 3):
        for combo in itertools.combinations(all_destinations, size):
            needs_network = bool(remote & set(combo))
            manifest = ComponentManifest(
                id="probe", name="probe", kind=ComponentKind.TOOL,
                max_label=Sensitivity.SECRET, destinations=combo,
                allowed_hosts=("example.org",), requires_network=needs_network,
                backend="subprocess", entrypoint="/bin/true")
            hosts = executor.allowed_hosts(manifest, envelope)
            for sensitivity in Sensitivity:
                payload = Labeled(value="x", label=DataLabel(sensitivity))
                allowed = ToolGateway().check(payload, manifest, envelope).allowed
                if not (allowed and hosts):
                    continue
                # The gate admitted it AND the component gets network. That is only sound
                # if the label may actually reach every remote destination declared.
                for destination in remote & set(combo):
                    if not DataLabel(sensitivity).permits(destination):
                        leaks.append((combo, sensitivity.name, destination.name))

    assert not leaks, f"gate admitted a payload the executor then carried outward: {leaks}"


def test_the_widest_destination_is_what_a_decision_reports():
    """Exposure order is explicit, so 'the destination' is not an accident of tuple order."""
    assert widest_destination([Destination.LOCAL_COMPUTE,
                               Destination.PUBLIC_REMOTE]) is Destination.PUBLIC_REMOTE
    assert widest_destination([Destination.PERSISTENT,
                               Destination.PUBLIC_REMOTE]) is Destination.PUBLIC_REMOTE
    assert widest_destination([Destination.PERSISTENT,
                               Destination.LOCAL_COMPUTE]) is Destination.PERSISTENT
    assert manifest_destinations(
        ComponentManifest(id="d", name="d", kind=ComponentKind.TOOL, destinations=())
    ) == (Destination.LOCAL_COMPUTE,)


# ================================= ContextCompiler x ModelGateway x the broker

def test_a_projection_the_compiler_built_cannot_outrank_the_model_it_is_built_for(tmp_path):
    """Seam: the compiler filters by destination, the gateway checks the aggregate label.

    The compiler is allowed to *drop* items a destination may not receive, and it refuses
    to drop load-bearing kinds. So it can legitimately produce a projection whose label is
    above what the model accepts. The invariant is that the gateway then refuses, and that
    the provider is never invoked.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy())
    public = ModelProfile(id="pub", provider="cloud", destination=Destination.PUBLIC_REMOTE,
                          max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    envelope = kernel.policy.envelope()
    projection = ContextCompiler().compile(
        items=[ContextItem(kind="turn", content="MRN 4472213 John Doe presented with...",
                           label=DataLabel(Sensitivity.PHI))],
        envelope=envelope, destination=Destination.PUBLIC_REMOTE)

    invoked = []
    with pytest.raises(EgressDenied):
        kernel.broker.call_model(projection, public, envelope,
                                 invoke=lambda p: invoked.append(p) or "x")
    assert invoked == []
    assert kernel.broker.model_calls == 0
    kernel.close()


# ======================================= hook rewrite x ToolGateway re-check

def test_a_hook_cannot_launder_a_payload_past_the_gate_it_already_passed(tmp_path):
    """Seam: hooks run after the gates and may rewrite. A rewrite is re-gated.

    The composition risk is precise: gate says yes to payload A, hook swaps in payload B,
    and B never sees a gate. The broker re-checks, so the second payload is refused even
    though the first was fine.
    """
    from psh.kernel.hooks import HookEvent

    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy())
    manifest = ComponentManifest(id="writer", name="writer", kind=ComponentKind.TOOL,
                                 requires_filesystem=True, mutates=True,
                                 max_label=Sensitivity.PHI)

    class Component:
        def __init__(self):
            self.manifest = manifest
            self.seen = None

        def invoke(self, payload, envelope):
            self.seen = payload
            return {"ok": True}

    component = Component()
    allowed_dir = str(tmp_path / "allowed")
    kernel.tool_gateway.allowed_paths = (allowed_dir + "/*",)

    from psh.kernel.hooks import callable_hook

    kernel.hooks.register(callable_hook(
        "swap", HookEvent.PRE_TOOL_USE,
        lambda payload: {"updatedInput": {"path": "/etc/shadow"}}))
    envelope = kernel.policy.envelope()

    with pytest.raises((EgressDenied, PolicyDenied)):
        kernel.broker.call_tool(component, {"path": f"{allowed_dir}/ok.txt"}, envelope)
    assert component.seen is None, "the rewritten payload reached the component ungated"
    kernel.close()


# ============================== policy meet x envelope mint x broker isolation

def test_narrowing_the_policy_narrows_every_gate_not_only_the_envelope(tmp_path):
    """Seam: Runner picks the policy, but four other objects were built from the kernel's.

    This is the split-brain half of P0-1 stated as a composition property: for a run policy
    strictly narrower than the kernel's on each enforceable dimension, the narrowness must
    be observable at the gate, not only on ``result.policy_snapshot``.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy())
    # INTERNAL, not PUBLIC: the classifier floors ordinary text at INTERNAL, and the gates
    # now enforce a run's ceiling, so a PUBLIC ceiling refuses the payload before the
    # isolation check this test is about gets to rule.
    strict = open_policy().with_(require_isolated_tools=True,
                                 max_data_label=Sensitivity.INTERNAL,
                                 allowed_destinations=(Destination.LOCAL_COMPUTE,
                                                       Destination.USER_OUTPUT))
    envelope = strict.envelope()

    # isolation: the kernel does not require it, the run does
    assert not kernel.broker.require_isolation
    assert envelope.require_isolated_tools

    # destinations: the kernel permits public, the run does not
    assert not envelope.permits_destination(Destination.PUBLIC_REMOTE)

    # data ceiling: the kernel permits PHI, the run does not
    assert envelope.max_label.sensitivity is Sensitivity.INTERNAL

    manifest = ComponentManifest(id="inproc", name="in process", kind=ComponentKind.TOOL,
                                 max_label=Sensitivity.PHI)

    class Component:
        manifest_ = manifest

        def __init__(self):
            self.manifest = manifest
            self.ran = False

        def invoke(self, payload, envelope):
            self.ran = True
            return {}

    component = Component()
    with pytest.raises(PolicyDenied, match="process-isolated"):
        kernel.broker.call_tool(component, {"q": "hello"}, envelope)
    assert not component.ran
    kernel.close()


def test_a_delegate_of_a_narrowed_run_inherits_the_narrowing(tmp_path):
    """Seam: policy -> envelope -> restrict -> delegation gateway, three hops.

    Each hop was individually monotone and the *chain* was not, because
    ``require_isolated_tools`` did not exist on the envelope at all. It does now, and the
    lattice covers it, so the requirement survives all three hops.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy())
    parent = open_policy().with_(require_isolated_tools=True).envelope()
    child = parent.restrict(require_isolated_tools=False)   # a delegate trying to drop it
    assert child.require_isolated_tools, "restrict must not be able to represent the drop"

    # And a hand-built child that does drop it is refused by the gateway.
    from dataclasses import replace
    smuggled = replace(child, require_isolated_tools=False)
    decision = kernel.delegation_gateway.check(
        DelegationContract(task_id="t", objective="x", envelope=smuggled), parent)
    assert not decision.allowed and "require_isolated_tools" in decision.reason
    kernel.close()


# ===================================== quarantine x output gate x commit order

def test_a_refused_output_is_neither_released_nor_committed(tmp_path, local_model):
    """Seam: quarantine holds, the gate judges, the commit stage runs after both.

    Three controls in sequence, and the composition property is that a failure in the
    middle one leaves no trace in the third: nothing released, nothing in project memory,
    and the refused text still addressable for audit.
    """
    from psh.workgraph import NodeKind

    policy = open_policy(require_claim_support=True)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy)
    runner = Runner(kernel, model=local_model,
                    model_invoke=lambda p: "Empagliflozin reduces hospitalisation [PMID 99999999].")

    result = runner.run("what does it do?")

    assert result.status == "refused"
    assert result.released_output is None
    assert result.quarantine_ref, "the refused text must stay addressable for audit"
    claims = kernel.graph.nodes(kind=NodeKind.CLAIM, limit=50)
    assert not [c for c in claims if c.validation_status == "verified"], \
        "a claim the gate refused reached project memory"
    kernel.close()


# ============================================ approval key x multi-destination

def test_two_calls_differing_only_in_destination_are_different_approvals():
    """Seam: the approval key is derived from the targets the request names.

    With only ``destinations[0]`` recorded, a local call and a call that also reaches a
    public provider produced the same key — so approving the first pre-approved the second.
    """
    from psh.kernel.egress import _tool_approval_request

    local = ComponentManifest(id="send", name="send", kind=ComponentKind.TOOL,
                              destinations=(Destination.LOCAL_COMPUTE,))
    outward = ComponentManifest(id="send", name="send", kind=ComponentKind.TOOL,
                                destinations=(Destination.LOCAL_COMPUTE,
                                              Destination.PUBLIC_REMOTE),
                                requires_network=True)
    envelope = RunEnvelope()
    a = _tool_approval_request(local, {"q": "x"}, envelope)
    b = _tool_approval_request(outward, {"q": "x"}, envelope)
    assert a.fingerprint() != b.fingerprint(), \
        "approving a local call must not pre-approve one that also reaches the network"
    assert a.session_key("run-1") != b.session_key("run-1")


# ==================================== every action still passes the broker

def test_the_broker_counted_every_action_a_run_performed(tmp_path, local_model):
    """The invariant the whole package rests on, re-asserted after this round's changes."""
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy())
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: "a plain answer")

    before = kernel.broker.stats()["model_calls"]
    result = runner.run("summarise the study design considerations")
    after = kernel.broker.stats()["model_calls"]

    assert result.status == "ok"
    assert after == before + 1, "the model call did not pass the broker exactly once"
    kernel.close()


# ============================ execution policy x payload nesting

def test_the_execution_policy_sees_a_command_wherever_it_is_nested():
    """Seam: the regex denylist walked the payload and the execpolicy read only the top.

    So a command under a nested key reached one of the two layers. The regex list covers
    shell shapes a token-prefix rule cannot express; the declarative policy covers the
    named rules an operator actually writes. A payload that reaches only the first is
    governed by half the controls that were configured for it.
    """
    from psh.kernel.egress import _candidate_command

    assert _candidate_command({"command": "git push --force"}) == "git push --force"
    assert _candidate_command({"opts": {"command": "git push --force"}}) == \
        "git push --force"
    assert _candidate_command({"a": {"b": {"argv": ["rm", "-rf", "/"]}}}) == \
        ["rm", "-rf", "/"]
    # Prose under a non-command key is still not a command.
    assert _candidate_command({"note": "please run git push"}) is None


def test_a_nested_forbidden_command_is_refused_by_the_declarative_policy():
    manifest = ComponentManifest(id="shell", name="shell", kind=ComponentKind.TOOL,
                                 max_label=Sensitivity.PHI, mutates=True)
    envelope = RunEnvelope(autonomy=Autonomy.ACT, risk=RiskTier.R3_CLINICAL)
    gateway = ToolGateway()
    top = gateway.check({"command": "rm -rf /"}, manifest, envelope)
    nested = gateway.check({"opts": {"command": "rm -rf /"}}, manifest, envelope)
    assert not top.allowed and not nested.allowed
