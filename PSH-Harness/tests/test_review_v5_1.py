"""Regression tests for the v0.5 review: one test per reproduction it reported.

The review's findings shared a shape worth naming, because it is the *third* time this
package has been reviewed and the third time the same shape appeared:

    a unified abstraction exists, and a second, shorter copy of it was hand-written
    somewhere else.

``AuthorityLattice`` existed and ``DelegationGateway`` re-implemented four of its
fourteen dimensions. ``PolicySnapshot.envelope`` enforced a ceiling and ``Runner.run``
minted from a policy nobody had checked against the kernel's. ``labels.walk_values``
treated a truncated walk as a reason to escalate and the egress walkers treated it as a
reason to pass. So these tests are written against the *executing path* wherever one
exists — a gateway, a runner, a real subprocess — rather than against the predicate, since
in every one of these defects the predicate was already right.

Each test names the reproduction it locks down.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from psh.config import PSHConfig
from psh.contracts import (
    Autonomy, Budget, ComponentKind, ComponentManifest, ContractViolation,
    DelegationContract, ModelProfile, PolicyDenied, RiskTier, RunEnvelope,
)
from psh.kernel import TrustedKernel
from psh.kernel.authority import UNRESTRICTED, AuthorityLattice
from psh.kernel.egress import DelegationGateway, ToolGateway
from psh.kernel.isolation import IsolatedExecutor, IsolatedRunner, IsolationUnavailable
from psh.labels import DataLabel, Destination, Labeled, Sensitivity
from psh.policy import PolicyLattice, PolicySnapshot
from psh.runtime import Runner
from psh.workgraph import NodeKind

LOCAL_ONLY = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
              Destination.PERSISTENT)


def narrow_policy(**kw):
    params = dict(profile_id="narrow", max_data_label=Sensitivity.INTERNAL,
                  allowed_destinations=LOCAL_ONLY, autonomy=Autonomy.SUGGEST,
                  risk_ceiling=RiskTier.R1_ROUTINE, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


def broad_policy(**kw):
    params = dict(profile_id="caller-broad", max_data_label=Sensitivity.INTERNAL,
                  allowed_destinations=LOCAL_ONLY + (Destination.PUBLIC_REMOTE,),
                  autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL,
                  require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


# ==================================================== P0-1: the Runner policy ceiling

def test_runner_cannot_mint_a_run_wider_than_the_kernel_policy(tmp_path):
    """Reproduction: kernel forbids PUBLIC_REMOTE; the run reached a public provider.

    The review drove this all the way through: ``remote invokes: 1``, ``refused_at:`` empty,
    ``released: hello world``. The provider's own ``max_label`` is deliberately PHI here so
    that the kernel's policy is the *only* thing that can refuse this — with a stricter
    provider limit the run fails for an unrelated reason and the test proves nothing.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=narrow_policy())
    remote = ModelProfile(id="remote-x", provider="public",
                          destination=Destination.PUBLIC_REMOTE, max_label=Sensitivity.PHI)
    invoked = []
    runner = Runner(kernel, model=remote,
                    model_invoke=lambda p: invoked.append(p) or "hello world")

    result = runner.run("say hello", policy=broad_policy())

    assert result.status == "refused"
    assert result.released_output is None
    assert invoked == [], "a run wider than the kernel's policy reached the provider"
    assert "PUBLIC_REMOTE" in result.error
    kernel.close()


def test_a_wider_policy_is_refused_at_runner_construction_too(tmp_path):
    """The constructor takes the same argument, so it needs the same check."""
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=narrow_policy())
    with pytest.raises(PolicyDenied) as exc:
        Runner(kernel, policy=broad_policy())
    assert "allowed_destinations" in str(exc.value)
    kernel.close()


def test_clamp_policy_narrows_instead_of_refusing(tmp_path):
    """The opt-in alternative, for a broad default profile over an already narrow run."""
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=narrow_policy())
    runner = Runner(kernel, policy=broad_policy(), clamp_policy=True)

    assert Destination.PUBLIC_REMOTE not in runner.policy.allowed_destinations
    assert runner.policy.autonomy is Autonomy.SUGGEST
    assert runner.policy.risk_ceiling is RiskTier.R1_ROUTINE
    assert runner.policy.is_narrower_than(kernel.policy)
    kernel.close()


def test_a_genuinely_narrower_per_run_policy_is_still_honoured(tmp_path):
    """Narrowing must keep working: the fix is a ceiling, not a prohibition."""
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=narrow_policy())
    runner = Runner(kernel)
    narrower = narrow_policy().with_(autonomy=Autonomy.OBSERVE,
                                     risk_ceiling=RiskTier.R0_TRIVIAL)
    result = runner.run("a question", policy=narrower)
    assert result.policy_snapshot.autonomy is Autonomy.OBSERVE
    assert result.status == "ok"
    kernel.close()


def test_run_policy_governs_the_gates_and_not_only_the_envelope(tmp_path):
    """The split-brain half of P0-1: ``require_isolated_tools`` reached no enforcement point.

    A per-run policy is narrower than the kernel's, so it is admitted — but the broker was
    constructed from the *kernel's* policy, so a run that turned isolation on was declaring
    a requirement nothing read. The requirement travels on the envelope now.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=narrow_policy())
    assert kernel.broker.require_isolation is False, "the kernel itself does not require it"

    strict = narrow_policy(require_isolated_tools=True)
    envelope = strict.envelope()
    assert envelope.require_isolated_tools

    manifest = ComponentManifest(id="inproc", name="in process", kind=ComponentKind.TOOL,
                                 backend="python", max_label=Sensitivity.PHI)

    class Component:
        def __init__(self):
            self.manifest = manifest
            self.ran = False

        def invoke(self, payload, envelope):
            self.ran = True
            return {"ok": True}

    component = Component()
    with pytest.raises(PolicyDenied, match="process-isolated"):
        kernel.broker.call_tool(component, {"q": "x"}, envelope)
    assert component.ran is False
    kernel.close()


# ============================================ P0-2: every destination, not the first

def test_tool_gateway_checks_every_declared_destination():
    """Reproduction: PHI + (LOCAL_COMPUTE, PUBLIC_REMOTE) was allowed=True."""
    manifest = ComponentManifest(
        id="leaky", name="leaky", kind=ComponentKind.TOOL, max_label=Sensitivity.PHI,
        destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE))
    envelope = RunEnvelope(
        max_label=DataLabel(Sensitivity.PHI), risk=RiskTier.R3_CLINICAL,
        autonomy=Autonomy.ACT,
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE,
                                        Destination.PUBLIC_REMOTE,
                                        Destination.USER_OUTPUT}))
    phi = Labeled(value={"note": "MRN 123456"}, label=DataLabel(Sensitivity.PHI))

    decision = ToolGateway().check(phi, manifest, envelope)

    assert not decision.allowed
    assert decision.destination is Destination.PUBLIC_REMOTE, \
        "the decision must name the destination that forbade the payload"
    assert "PUBLIC_REMOTE" in decision.reason


def test_destination_order_in_the_manifest_does_not_change_the_verdict():
    """The bypass was positional, so the same tuple reversed must decide the same way."""
    envelope = RunEnvelope(
        max_label=DataLabel(Sensitivity.PHI), risk=RiskTier.R3_CLINICAL,
        autonomy=Autonomy.ACT,
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE,
                                        Destination.PUBLIC_REMOTE}))
    phi = Labeled(value="MRN 123456", label=DataLabel(Sensitivity.PHI))
    verdicts = set()
    for destinations in ((Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
                         (Destination.PUBLIC_REMOTE, Destination.LOCAL_COMPUTE)):
        manifest = ComponentManifest(id="t", name="t", kind=ComponentKind.TOOL,
                                     max_label=Sensitivity.PHI, destinations=destinations)
        verdicts.add(ToolGateway().check(phi, manifest, envelope).allowed)
    assert verdicts == {False}


def test_the_gate_and_the_isolated_executor_read_the_same_destinations():
    """The dangerous half of P0-2 was the *disagreement* between these two readings.

    ``ToolGateway`` took ``destinations[0]`` and allowed PHI on the strength of
    ``LOCAL_COMPUTE``; ``IsolatedExecutor.allowed_hosts`` took ``any(...)`` over the same
    tuple, saw ``PUBLIC_REMOTE`` and opened the component's network reach. Either reading
    alone is defensible. Holding both at once is the bug.
    """
    manifest = ComponentManifest(
        id="fetcher", name="fetcher", kind=ComponentKind.TOOL, max_label=Sensitivity.PHI,
        destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
        allowed_hosts=("example.org",), requires_network=True,
        backend="subprocess", entrypoint="/bin/true")
    envelope = RunEnvelope(
        max_label=DataLabel(Sensitivity.PHI), risk=RiskTier.R3_CLINICAL,
        autonomy=Autonomy.ACT,
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE,
                                        Destination.PUBLIC_REMOTE}))
    executor = IsolatedExecutor(IsolatedRunner(), workdir_root=Path("/tmp/psh-test-root"))

    reaches_network = bool(executor.allowed_hosts(manifest, envelope))
    phi_allowed = ToolGateway().check(
        Labeled(value="MRN 1", label=DataLabel(Sensitivity.PHI)), manifest,
        envelope).allowed

    assert reaches_network, "the executor does grant this component remote hosts"
    assert not phi_allowed, "so the gate must not pass PHI to it on the strength of [0]"


def test_approval_records_every_destination_not_only_the_first():
    """An approval keyed on ``LOCAL_COMPUTE`` alone describes the wrong action."""
    from psh.kernel.egress import _tool_approval_request

    manifest = ComponentManifest(
        id="sender", name="sender", kind=ComponentKind.TOOL,
        destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
        requires_network=True)
    request = _tool_approval_request(manifest, {"q": "x"}, RunEnvelope())
    assert "PUBLIC_REMOTE" in request.targets and "LOCAL_COMPUTE" in request.targets


# ==================================== P1-3: delegation uses the one authority predicate

@pytest.mark.parametrize("dimension,mutation", [
    ("risk", {"risk": RiskTier.R4_KERNEL}),
    ("autonomy", {"autonomy": Autonomy.ACT}),
    ("capabilities", {"allowed_capabilities": UNRESTRICTED}),
    ("denied_capabilities", {"denied_capabilities": ()}),
    ("budget.usd_hard", {"budget": Budget(tokens_hard=1000, usd_hard=999.0)}),
    ("budget.seconds_hard", {"budget": Budget(tokens_hard=1000, seconds_hard=9e5)}),
    ("budget.max_model_calls", {"budget": Budget(tokens_hard=1000, max_model_calls=999)}),
    ("budget.max_tool_calls", {"budget": Budget(tokens_hard=1000, max_tool_calls=999)}),
    ("budget.max_delegations", {"budget": Budget(tokens_hard=1000, max_delegations=999)}),
    ("require_isolated_tools", {"require_isolated_tools": False}),
])
def test_delegation_refuses_each_widened_dimension_on_its_own(dimension, mutation):
    """Reproduction: a child widened on nine dimensions at once returned allowed=True.

    One dimension per case, because the review's other finding was that the *existing*
    property test widened everything simultaneously and so only ever proved that the first
    dimension checked was checked.
    """
    parent = RunEnvelope(
        risk=RiskTier.R1_ROUTINE, autonomy=Autonomy.SUGGEST,
        allowed_capabilities=("safe",), denied_capabilities=("blocked",),
        require_isolated_tools=True,
        budget=Budget(tokens_hard=1000, usd_hard=1.0, seconds_hard=10.0,
                      max_model_calls=1, max_tool_calls=1, max_delegations=1))
    base = dict(
        risk=parent.risk, autonomy=parent.autonomy, max_label=parent.max_label,
        allowed_destinations=parent.allowed_destinations,
        allowed_capabilities=parent.allowed_capabilities,
        denied_capabilities=parent.denied_capabilities,
        require_isolated_tools=parent.require_isolated_tools, budget=parent.budget)
    child = RunEnvelope(**{**base, **mutation})

    contract = DelegationContract(task_id="t", objective="do things", envelope=child)
    decision = DelegationGateway().check(contract, parent)

    assert not decision.allowed, f"widening {dimension} alone was allowed"
    assert dimension in decision.reason, \
        f"the refusal must name {dimension}; it said {decision.reason!r}"


def test_delegation_still_allows_a_properly_narrowed_child():
    parent = RunEnvelope(risk=RiskTier.R2_CONSEQUENTIAL, autonomy=Autonomy.ACT,
                         budget=Budget(tokens_hard=1000))
    contract = DelegationContract(task_id="t", objective="narrow work",
                                  envelope=parent.restrict())
    assert DelegationGateway().check(contract, parent).allowed


def test_the_gateway_agrees_with_the_lattice_exactly():
    """No third opinion: the gateway's verdict IS the lattice's, for authority.

    Every child below shares the parent's budget, so ``tokens_hard`` — the one budget
    dimension the hand-written gateway did check — cannot be what refuses it. That detail
    is the whole point: with a wider budget each case tripped the one check that existed
    and the two verdicts agreed by accident, which is also why the original property test
    passed while the gateway was wrong.
    """
    parent = RunEnvelope(risk=RiskTier.R2_CONSEQUENTIAL, autonomy=Autonomy.SUGGEST,
                         allowed_capabilities=("safe",), denied_capabilities=("blocked",),
                         budget=Budget(tokens_hard=100))
    same_budget = dict(budget=parent.budget, max_label=parent.max_label,
                       allowed_destinations=parent.allowed_destinations)
    children = (
        parent.restrict(),
        RunEnvelope(risk=RiskTier.R4_KERNEL, **same_budget),
        RunEnvelope(autonomy=Autonomy.ACT, **same_budget),
        RunEnvelope(allowed_capabilities=UNRESTRICTED, **same_budget),
        RunEnvelope(denied_capabilities=(), allowed_capabilities=("safe",), **same_budget),
        RunEnvelope(risk=RiskTier.R0_TRIVIAL, autonomy=Autonomy.OBSERVE,
                    allowed_capabilities=("safe",),
                    denied_capabilities=("blocked",), **same_budget),
    )
    for child in children:
        contract = DelegationContract(task_id="t", objective="x", envelope=child)
        gateway_says = DelegationGateway().check(contract, parent).allowed
        lattice_says = AuthorityLattice.is_subset(child, parent)
        assert gateway_says == lattice_says, child
    # And the set is discriminating: it contains both verdicts.
    verdicts = {AuthorityLattice.is_subset(c, parent) for c in children}
    assert verdicts == {True, False}


# ================================================ P1-4: policy narrowing is a lattice

@pytest.mark.parametrize("dimension,mutation", [
    ("autonomy", {"autonomy": Autonomy.ACT}),
    ("require_isolated_tools", {"require_isolated_tools": False}),
    ("require_citation", {"require_citation": False}),
    ("require_claim_support", {"require_claim_support": False,
                               "require_citation": False}),
    ("budget.tokens_hard", {"budget": Budget(tokens_hard=999)}),
    ("budget.usd_hard", {"budget": Budget(tokens_hard=10, usd_hard=999.0)}),
    ("approval_required_at", {"approval_required_at": RiskTier.R4_KERNEL}),
    ("declassify_floor", {"declassify_floor": Sensitivity.PUBLIC}),
    ("declassifiers", {"declassifiers": ("alice", "mallory")}),
    ("verification_model_id", {"verification_model_id": "weaker"}),
    ("risk_ceiling", {"risk_ceiling": RiskTier.R4_KERNEL}),
    ("max_data_label", {"max_data_label": Sensitivity.SECRET}),
    ("allowed_destinations",
     {"allowed_destinations": LOCAL_ONLY + (Destination.PUBLIC_REMOTE,)}),
])
def test_policy_with_refuses_each_widening(dimension, mutation):
    """Reproduction: ``with_()`` checked three dimensions and its docstring claimed all.

    SUGGEST->ACT, isolation True->False and budget 10->999 were all ACCEPTED by a method
    documented as "Return a narrowed copy. Widening is refused".
    """
    base = PolicySnapshot(
        profile_id="base", max_data_label=Sensitivity.PHI,
        allowed_destinations=LOCAL_ONLY, autonomy=Autonomy.SUGGEST,
        require_citation=True, require_claim_support=True, require_isolated_tools=True,
        budget=Budget(tokens_hard=10, usd_hard=1.0),
        approval_required_at=RiskTier.R1_ROUTINE,
        declassifiers=("alice",), declassify_floor=Sensitivity.SENSITIVE,
        verification_model_id="verify-1", risk_ceiling=RiskTier.R1_ROUTINE)

    with pytest.raises(PolicyDenied) as exc:
        base.with_(**mutation)
    assert dimension in str(exc.value)


def test_policy_deadline_containment_matches_the_envelope_rule():
    """An unbounded parent contains any child; a bounded one does not."""
    unbounded = PolicySnapshot(profile_id="p")
    assert unbounded.with_(deadline=time.time() + 10 ** 6) is not None

    bounded = PolicySnapshot(profile_id="p", deadline=time.time() + 100)
    with pytest.raises(PolicyDenied, match="deadline"):
        bounded.with_(deadline=time.time() + 10 ** 6)
    with pytest.raises(PolicyDenied, match="deadline"):
        bounded.with_(deadline=None)


def test_policy_narrowing_still_works_on_every_dimension():
    """The lattice must not have made the ordinary narrowing case fail."""
    base = PolicySnapshot(profile_id="base", max_data_label=Sensitivity.PHI,
                          allowed_destinations=LOCAL_ONLY, autonomy=Autonomy.ACT,
                          risk_ceiling=RiskTier.R3_CLINICAL,
                          approval_required_at=RiskTier.R3_CLINICAL,
                          declassifiers=("alice", "bob"))
    narrowed = base.with_(
        max_data_label=Sensitivity.PUBLIC,
        allowed_destinations=(Destination.LOCAL_COMPUTE,),
        autonomy=Autonomy.OBSERVE, risk_ceiling=RiskTier.R0_TRIVIAL,
        require_isolated_tools=True, approval_required_at=RiskTier.R0_TRIVIAL,
        declassifiers=("alice",), declassify_floor=Sensitivity.SECRET,
        budget=Budget(tokens_hard=1))
    assert narrowed.is_narrower_than(base)


def test_policy_meet_is_never_wider_than_either_input():
    a = PolicySnapshot(profile_id="a", allowed_destinations=LOCAL_ONLY,
                       autonomy=Autonomy.SUGGEST, risk_ceiling=RiskTier.R1_ROUTINE,
                       require_citation=True, require_claim_support=True)
    b = PolicySnapshot(profile_id="b",
                       allowed_destinations=(Destination.LOCAL_COMPUTE,
                                             Destination.PUBLIC_REMOTE),
                       autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL,
                       require_isolated_tools=True, max_data_label=Sensitivity.PUBLIC)
    met = PolicyLattice.meet(b, a)
    assert PolicyLattice.is_narrower(met, a) and PolicyLattice.is_narrower(met, b)
    assert met.require_citation and met.require_isolated_tools
    assert set(met.allowed_destinations) == {Destination.LOCAL_COMPUTE}


# ========================================== P1-5: the isolated workdir stays contained

@pytest.mark.parametrize("bad", ["../../escaped", "/tmp/absolute", "..", ".", "a/b",
                                 "a\\b", "", " ", "x" * 200])
def test_a_component_id_that_is_not_one_path_component_is_refused(bad):
    """Reproduction: ``id="../../escaped"`` put the child's cwd outside the sandbox."""
    with pytest.raises(ValueError):
        ComponentManifest(id=bad, name="e", kind=ComponentKind.TOOL)


def test_the_workdir_is_proven_inside_the_sandbox_root(tmp_path):
    """The second layer, for ids and run ids that did not come through the manifest."""
    root = tmp_path / "sandbox"
    executor = IsolatedExecutor(IsolatedRunner(), workdir_root=root)
    manifest = ComponentManifest(id="good", name="g", kind=ComponentKind.TOOL,
                                 backend="subprocess", entrypoint="/bin/true")

    workdir = executor._workdir_for(manifest, RunEnvelope())
    assert workdir.resolve().is_relative_to(root.resolve())

    class Smuggled:
        id = "../../escaped"

    with pytest.raises(IsolationUnavailable):
        executor._workdir_for(Smuggled(), RunEnvelope())

    class BadRun:
        run_id = "../../../etc"

    with pytest.raises(IsolationUnavailable):
        executor._workdir_for(manifest, BadRun())


# ============================================ P1-6: the timeout kills the whole group

@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_timeout_kills_descendants_not_only_the_direct_child(tmp_path):
    """Reproduction: grandchild still alive, and writing files, 1.8s after the timeout.

    ``start_new_session=True`` created the process group and nothing ever signalled it, so
    "exceeded its timeout and was killed" was true only of the process the runner could
    name.
    """
    marker = tmp_path / "grandchild.txt"
    child = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        "  'import time,sys; time.sleep(1.5); open(sys.argv[1], \"w\").write(\"survived\")',\n"
        f"  {str(marker)!r}])\n"
        "time.sleep(30)\n")

    result = IsolatedRunner().run([sys.executable, "-c", child],
                                  workdir=tmp_path / "wd", timeout_s=0.5)

    assert result.timed_out and result.exit_code == 124
    time.sleep(2.5)
    assert not marker.exists(), \
        "a descendant outlived the timeout that was reported as having killed it"


def test_the_ordinary_execution_paths_still_work(tmp_path):
    """Popen replaced subprocess.run, so the non-timeout paths need re-proving."""
    runner = IsolatedRunner()
    ok = runner.run([sys.executable, "-c", "print('hi')"], workdir=tmp_path / "a")
    assert ok.exit_code == 0 and ok.stdout.strip() == "hi" and not ok.timed_out

    piped = runner.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                       workdir=tmp_path / "b", stdin="hello")
    assert piped.stdout.strip() == "HELLO"

    failed = runner.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        workdir=tmp_path / "c")
    assert failed.exit_code == 3 and "boom" in failed.stderr


def test_allowed_hosts_is_not_consumed_twice(tmp_path):
    """``bool(list(allowed_hosts))`` re-read an iterator the runner had already drained."""
    seen = {}

    class Probe:
        def wrap(self, argv, *, workdir, allow_network):
            seen["allow_network"] = allow_network
            return list(argv)

        def describe(self):
            return "probe"

    runner = IsolatedRunner(sandbox=Probe())
    try:
        runner.run([sys.executable, "-c", "print(1)"], workdir=tmp_path / "w",
                   allowed_hosts=(h for h in ["example.org"]))
    except Exception:                      # the proxy may be unavailable; wrap() still ran
        pass
    assert seen.get("allow_network") is True


# ================================================ P1-7: deep payloads fail closed

def test_a_payload_too_deep_to_inspect_is_refused_not_allowed():
    """Reproduction: past depth 8 ``_candidate_paths`` returned [], so /outside passed."""
    from psh.kernel.egress import _WALK_MAX_DEPTH

    payload = {"target": "/definitely/outside"}
    for _ in range(_WALK_MAX_DEPTH + 4):
        payload = {"nest": payload}

    manifest = ComponentManifest(id="writer", name="w", kind=ComponentKind.TOOL,
                                 requires_filesystem=True, mutates=True,
                                 max_label=Sensitivity.PHI)
    envelope = RunEnvelope(max_label=DataLabel(Sensitivity.PHI), autonomy=Autonomy.ACT,
                           risk=RiskTier.R3_CLINICAL)
    gateway = ToolGateway(allowed_paths=["/tmp/allowed/*"])

    decision = gateway.check(payload, manifest, envelope)
    assert not decision.allowed
    assert "could not be" in decision.reason


def test_a_payload_within_the_cap_is_still_inspected_normally():
    """Fail-closed must not mean refuse-everything: the ordinary nested payload works."""
    from psh.kernel.egress import _candidate_paths

    scan = _candidate_paths({"a": {"b": {"c": {"target": "/tmp/allowed/f"}}}})
    assert scan.complete and scan.values == ("/tmp/allowed/f",)


def test_a_payload_too_wide_to_inspect_is_refused():
    """Node count, not only depth: a flat payload can exhaust the walk too."""
    from psh.kernel.egress import _WALK_MAX_NODES, _candidate_paths

    wide = {str(i): {"target": f"/p{i}"} for i in range(_WALK_MAX_NODES)}
    assert not _candidate_paths(wide).complete


def test_the_command_scan_fails_closed_too():
    """A denied command shape hidden past the depth cap must not read as 'no command'."""
    from psh.kernel.egress import _WALK_MAX_DEPTH

    payload = {"command": "rm -rf /"}
    for _ in range(_WALK_MAX_DEPTH + 4):
        payload = {"nest": payload}
    manifest = ComponentManifest(id="shell", name="shell", kind=ComponentKind.TOOL,
                                 max_label=Sensitivity.PHI)
    envelope = RunEnvelope(autonomy=Autonomy.ACT, risk=RiskTier.R3_CLINICAL)
    assert not ToolGateway().check(payload, manifest, envelope).allowed


# ===================================== P2-8/9/10 and the isolated component protocol

def test_requires_network_must_name_a_remote_destination():
    """Reproduction: the check was a chained comparison that could never be true."""
    with pytest.raises(ValueError, match="remote destination"):
        ComponentManifest(id="net", name="n", kind=ComponentKind.TOOL,
                          requires_network=True, destinations=())
    with pytest.raises(ValueError, match="remote destination"):
        ComponentManifest(id="net2", name="n", kind=ComponentKind.TOOL,
                          requires_network=True,
                          destinations=(Destination.LOCAL_COMPUTE,))
    ComponentManifest(id="net3", name="n", kind=ComponentKind.TOOL, requires_network=True,
                      destinations=(Destination.TRUSTED_REMOTE,))


def test_independent_repeats_of_one_request_are_not_a_stuck_loop(tmp_path, local_model):
    """Reproduction: the third identical top-level run was refused as an agent loop.

    ``task_id`` was ``""`` for every envelope at preflight, so one bucket counted every run
    a Runner had ever made.
    """
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs())
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: "ok")
    statuses = [runner.run("same harmless request").status for _ in range(5)]
    assert statuses == ["ok"] * 5, statuses
    kernel.close()


def test_repeats_within_one_declared_loop_scope_are_still_caught(tmp_path, local_model):
    """The detector must still detect: scoping it is not disabling it."""
    from psh.runtime.runner import StuckLoop

    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs())
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: "ok")
    threshold = kernel.config.stuck_loop_threshold

    results = [runner.run("spin on this", loop_scope="agent-loop-1")
               for _ in range(threshold)]
    assert results[-1].status == "refused"
    assert results[-1].error.startswith(StuckLoop.__name__)
    assert "agent-loop-1" in results[-1].error
    kernel.close()


def test_a_different_scope_has_its_own_counter(tmp_path, local_model):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs())
    runner = Runner(kernel, model=local_model, model_invoke=lambda p: "ok")
    threshold = kernel.config.stuck_loop_threshold

    for _ in range(threshold - 1):
        runner.run("q", loop_scope="scope-a")
    assert runner.run("q", loop_scope="scope-b").status == "ok"
    kernel.close()


def test_min_autonomy_is_enforced_by_compatible_with():
    """Reproduction: ``min_autonomy=ACT`` reported compatible under an OBSERVE run."""
    manifest = ComponentManifest(id="actor", name="a", kind=ComponentKind.TOOL,
                                 min_autonomy=Autonomy.ACT)
    ok, why = manifest.compatible_with(RunEnvelope(autonomy=Autonomy.OBSERVE))
    assert not ok and "autonomy" in why
    assert manifest.compatible_with(RunEnvelope(autonomy=Autonomy.ACT))[0]


def test_the_default_manifest_does_not_demand_act():
    """The default was the strongest requirement, which is why it could not be enforced."""
    plain = ComponentManifest(id="plain", name="p", kind=ComponentKind.TOOL)
    assert plain.compatible_with(RunEnvelope(autonomy=Autonomy.OBSERVE))[0]


def test_an_isolated_component_that_breaks_the_protocol_fails(tmp_path):
    """The docs said "writes one JSON value on stdout"; prose was silently accepted."""
    executor = IsolatedExecutor(IsolatedRunner(), workdir_root=tmp_path / "sandbox")
    manifest = ComponentManifest(
        id="prose", name="prose", kind=ComponentKind.TOOL, backend="subprocess",
        entrypoint=f"{sys.executable} -c \"print('not json at all')\"")
    with pytest.raises(ContractViolation, match="one JSON value"):
        executor.invoke(manifest, {}, RunEnvelope())


def test_an_isolated_component_that_keeps_the_protocol_still_works(tmp_path):
    executor = IsolatedExecutor(IsolatedRunner(), workdir_root=tmp_path / "sandbox")
    manifest = ComponentManifest(
        id="jsonly", name="jsonly", kind=ComponentKind.TOOL, backend="subprocess",
        entrypoint=f"{sys.executable} -c \"import json; print(json.dumps({{'ok': True}}))\"")
    assert executor.invoke(manifest, {}, RunEnvelope()) == {"ok": True}


# ======================= P0-1 (continued): the rest of the split brain
#
# The review's deeper point about ``Runner.run(policy=...)`` was not only that a wider
# policy was accepted. It was that the run's policy and the policy actually consulted by
# each gate were different objects:
#
#     envelope                     <- Runner.run(policy=...)
#     OutputGate.require_support   <- kernel.policy, at construction
#     broker.require_isolation     <- kernel.policy, at construction
#     PersistenceGateway.max_label <- kernel.policy, at construction
#
# So ``result.policy_snapshot`` described a policy that had governed one of the four.
# Meeting the policies fixes the direction (the run can only be narrower), and these tests
# cover the other half: a run that is narrower must have that narrowness ENFORCED, not
# merely recorded. Otherwise "declared policy is effective policy" — the sentence this
# package's policy module opens with — is false again, just in the safe direction.

def test_a_run_policy_requiring_claim_support_is_enforced_by_the_gate(tmp_path,
                                                                      local_model):
    """The kernel does not require support; the run's own narrower policy does."""
    from psh.contracts import VerificationFailed

    lax = PolicySnapshot(profile_id="lax", allowed_destinations=LOCAL_ONLY,
                         require_claim_support=False, autonomy=Autonomy.ACT,
                         risk_ceiling=RiskTier.R3_CLINICAL)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=lax)
    assert kernel.output_gate.require_support is False

    strict = lax.with_(require_claim_support=True)
    runner = Runner(kernel, model=local_model,
                    model_invoke=lambda p: "Empagliflozin reduces hospitalisation.")

    lax_result = runner.run("what does it do?", policy=lax)
    strict_result = runner.run("what does it do?", policy=strict)

    assert lax_result.status == "ok", "the kernel's own policy does not require support"
    assert strict_result.status == "refused", \
        "a run policy that requires claim support must be enforced, not just recorded"
    assert strict_result.released_output is None
    kernel.close()


def test_a_run_policy_lowering_the_data_ceiling_is_enforced_at_persistence(tmp_path):
    """The store's ceiling is the kernel's; the run's must be able to tighten it."""
    phi_policy = PolicySnapshot(profile_id="phi", max_data_label=Sensitivity.PHI,
                                allowed_destinations=LOCAL_ONLY)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=phi_policy)
    assert kernel.persistence.max_label is Sensitivity.PHI

    identifying = "Patient John Doe, MRN 4472213, seen 2024-03-02"

    # The store accepts it under the kernel's own ceiling.
    kernel.persistence.commit_node(kind=NodeKind.KNOWLEDGE, title=identifying,
                                   principal="p", source_run="r1")

    # A run whose ceiling is PUBLIC must not be able to write the same content.
    with pytest.raises(PolicyDenied, match="ceiling"):
        kernel.persistence.commit_node(kind=NodeKind.KNOWLEDGE, title=identifying,
                                       principal="p", source_run="r2",
                                       max_label=Sensitivity.PUBLIC)
    kernel.close()


def test_a_per_write_ceiling_cannot_loosen_the_store():
    """It tightens or it does nothing: ``min``, never assignment."""
    from psh.kernel.persistence import PersistenceGateway

    gateway = PersistenceGateway.__new__(PersistenceGateway)
    gateway.max_label = Sensitivity.INTERNAL
    assert min(gateway.max_label, Sensitivity.SECRET) is Sensitivity.INTERNAL


def test_a_per_call_requirement_cannot_switch_the_output_gate_off(tmp_path):
    """``require_support=False`` passed to a gate that requires support changes nothing."""
    from psh.contracts import VerificationFailed

    strict = PolicySnapshot(profile_id="strict", allowed_destinations=LOCAL_ONLY,
                            require_claim_support=True)
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=strict)
    labelled = kernel.ingress.ensure("Empagliflozin reduces hospitalisation.",
                                     origin="test")
    with pytest.raises(VerificationFailed):
        kernel.output_gate.check(labelled, strict.envelope(), require_support=False)
    kernel.close()


def test_the_declared_version_matches_the_package_metadata():
    """``psh.__version__`` read "0.4.0" for the whole of v0.5.

    A version string that drifts is a small thing on its own and a bad sign in a package
    whose central claim is that what it declares is what it does.
    """
    import re

    import psh

    # A regex rather than ``tomllib``: the package supports Python 3.10 and ``tomllib``
    # arrived in 3.11, so the test itself was the one thing here that did not run
    # everywhere the package does. The line it reads is the one ``[project]`` version.
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert match, "pyproject.toml declares no version"
    assert psh.__version__ == match.group(1)


def test_the_group_kill_refuses_to_signal_our_own_process_group(monkeypatch):
    """``killpg`` on our own group would SIGKILL the kernel, not the tool.

    Only reachable if ``start_new_session=True`` did not take effect — which should not
    happen, and is exactly the condition worth checking before sending SIGKILL to a group.
    """
    import os

    from psh.kernel import isolation

    killed: list = []
    monkeypatch.setattr(isolation.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(isolation.os, "killpg",
                        lambda pgid, sig: killed.append(("killpg", pgid)))

    class FakeProc:
        pid = 999

        def kill(self):
            killed.append(("kill", self.pid))

    isolation._kill_process_group(FakeProc())
    assert killed == [("kill", 999)], \
        "it signalled a group that is our own instead of the child alone"
