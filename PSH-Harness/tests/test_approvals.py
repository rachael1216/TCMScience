"""Approval outcomes that write back policy (Codex ReviewDecision pattern)."""

from __future__ import annotations

import pytest

from psh import ApprovalDenied, Destination, PolicyDenied, Sensitivity
from psh.contracts import ComponentKind, ComponentManifest
from psh.kernel.approvals import ApprovalKind, ApprovalOutcome, PolicyAmendment
from psh.kernel.execpolicy import Decision


class _Shell:
    """A tool whose payload is a command, so the execution policy governs it."""

    def __init__(self):
        self.ran: list[str] = []

    @property
    def manifest(self):
        return ComponentManifest(id="shell", name="shell", kind=ComponentKind.TOOL,
                                 max_label=Sensitivity.INTERNAL, mutates=True,
                                 destinations=(Destination.LOCAL_COMPUTE,))

    def invoke(self, payload, envelope):
        self.ran.append(payload["command"])
        return {"stdout": "ok"}


def _kernel(tmp_path, handler):
    from psh.config import PSHConfig
    from psh.kernel import TrustedKernel
    from psh.contracts import Autonomy

    # The policy is the ceiling, so a bench that exercises ACT autonomy must declare it.
    from conftest import bench_policy

    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                      policy=bench_policy(profile_id="test_approvals"),
                      approval_handler=handler)
    return k


def test_forbidden_command_is_refused_before_any_approval_is_asked(tmp_path):
    asked = []
    k = _kernel(tmp_path, lambda what, rec: asked.append(what) or True)
    shell = _Shell()
    from psh import EgressDenied

    with pytest.raises(EgressDenied, match="forbids"):
        k.broker.call_tool(shell, k.classify({"command": "rm -rf /tmp/x"}), k.envelope())
    assert asked == [], "a forbidden command must never reach a human as a question"
    assert shell.ran == []


def test_prompt_decision_asks_and_a_plain_bool_still_works(tmp_path):
    k = _kernel(tmp_path, lambda what, rec: True)
    shell = _Shell()
    from psh.contracts import Autonomy

    env = k.envelope(autonomy=Autonomy.ACT)
    k.broker.call_tool(shell, k.classify({"command": "git push origin main"}), env)
    assert shell.ran == ["git push origin main"]
    assert any(r["what"].startswith("run shell:") for r in k.approvals.requests)


def test_approved_for_session_is_not_asked_twice(tmp_path):
    calls = []
    def handler(what, rec):
        calls.append(what)
        return ApprovalOutcome(ApprovalKind.APPROVED_FOR_SESSION)
    k = _kernel(tmp_path, handler)
    shell = _Shell()
    from psh.contracts import Autonomy

    env = k.envelope(autonomy=Autonomy.ACT)
    for _ in range(3):
        k.broker.call_tool(shell, k.classify({"command": "git push origin main"}), env)
    assert len(shell.ran) == 3
    assert len(calls) == 1, "the second and third identical requests must use the session cache"


def test_amendment_adds_an_allow_rule_and_the_question_stops(tmp_path):
    calls = []
    def handler(what, rec):
        calls.append(what)
        return ApprovalOutcome(ApprovalKind.APPROVED_WITH_AMENDMENT, amendment=PolicyAmendment(
            pattern=("git", "push", "origin"), justification="pushing to origin is routine here",
            match=(("git", "push", "origin", "main"),)))
    k = _kernel(tmp_path, handler)
    shell = _Shell()
    from psh.contracts import Autonomy

    env = k.envelope(autonomy=Autonomy.ACT)
    k.broker.call_tool(shell, k.classify({"command": "git push origin main"}), env)
    assert k.execpolicy.evaluate("git push origin main").decision is Decision.ALLOW
    # A different branch matches the amended prefix too -- that is what the rule says.
    k.broker.call_tool(shell, k.classify({"command": "git push origin feature"}), env)
    assert len(calls) == 1, "after the amendment, no further question"
    assert any(e.event_type == "policy_amended" for e in k.events.records())


def test_amendment_cannot_override_a_forbidden_rule(tmp_path):
    def handler(what, rec):
        return ApprovalOutcome(ApprovalKind.APPROVED_WITH_AMENDMENT, amendment=PolicyAmendment(
            pattern=("rm", "-rf"), justification="trust me", match=(("rm", "-rf", "x"),)))
    k = _kernel(tmp_path, handler)
    # Trigger any prompt so the handler runs and proposes the hostile amendment.
    from psh.contracts import Autonomy

    with pytest.raises(PolicyDenied, match="forbidden rule"):
        k.broker.call_tool(_Shell(), k.classify({"command": "git push origin main"}),
                           k.envelope(autonomy=Autonomy.ACT))
    assert k.execpolicy.evaluate("rm -rf x").decision is Decision.FORBIDDEN
    assert any(e.event_type == "amendment_refused" for e in k.events.records())


def test_amendment_with_failing_self_test_is_refused(tmp_path):
    def handler(what, rec):
        return ApprovalOutcome(ApprovalKind.APPROVED_WITH_AMENDMENT, amendment=PolicyAmendment(
            pattern=("git", "push"), justification="x", match=(("ls",),)))
    k = _kernel(tmp_path, handler)
    from psh.contracts import Autonomy

    with pytest.raises(PolicyDenied, match="claims to match"):
        k.broker.call_tool(_Shell(), k.classify({"command": "git push origin main"}),
                           k.envelope(autonomy=Autonomy.ACT))


def test_persisted_amendment_survives_a_new_kernel(tmp_path):
    def handler(what, rec):
        return ApprovalOutcome(ApprovalKind.APPROVED_WITH_AMENDMENT, amendment=PolicyAmendment(
            pattern=("pip", "install", "numpy"), justification="numpy is always fine",
            persist=True, match=(("pip", "install", "numpy"),)))
    k = _kernel(tmp_path, handler)
    from psh.contracts import Autonomy

    k.broker.call_tool(_Shell(), k.classify({"command": "pip install numpy"}),
                       k.envelope(autonomy=Autonomy.ACT))
    assert (tmp_path / "k" / "execpolicy.json").exists()

    k2 = _kernel(tmp_path, handler=None)  # no handler: any prompt would fail closed
    assert k2.execpolicy.evaluate("pip install numpy").decision is Decision.ALLOW
    assert k2.execpolicy.evaluate("pip install anything-else").decision is Decision.PROMPT


def test_session_amendments_expire_with_the_run(tmp_path):
    def handler(what, rec):
        return ApprovalOutcome(ApprovalKind.APPROVED_WITH_AMENDMENT, amendment=PolicyAmendment(
            pattern=("git", "push"), justification="this session only",
            match=(("git", "push", "x"),)))
    k = _kernel(tmp_path, handler)
    from psh.contracts import Autonomy

    env = k.envelope(autonomy=Autonomy.ACT)
    k.broker.call_tool(_Shell(), k.classify({"command": "git push origin main"}), env)
    assert k.execpolicy.evaluate("git push x").decision is Decision.ALLOW
    removed = k.approvals.expire_session(env.run_id)
    assert removed == 1
    assert k.execpolicy.evaluate("git push x").decision is Decision.PROMPT
