"""Typed hook lifecycle in the Claude Code / Codex protocol shape."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from psh import Destination, EgressDenied, Sensitivity
from psh.contracts import Autonomy, ComponentKind, ComponentManifest
from psh.kernel.hooks import (
    HookBlocked, HookDecision, HookEvent, HookResult, callable_hook, command_hook,
)


class _Tool:
    def __init__(self, tool_id="reader", mutates=False):
        self.tool_id, self.mutates, self.calls = tool_id, mutates, []

    @property
    def manifest(self):
        return ComponentManifest(id=self.tool_id, name=self.tool_id, kind=ComponentKind.TOOL,
                                 max_label=Sensitivity.PHI, mutates=self.mutates,
                                 destinations=(Destination.LOCAL_COMPUTE,))

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        return {"ok": True, "echo": payload}


def _kernel(tmp_path, handler=None):
    from psh.config import PSHConfig
    from psh.kernel import TrustedKernel

    from conftest import bench_policy

    return TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                         policy=bench_policy(profile_id="test_hooks"),
                         approval_handler=handler)


# ------------------------------------------------------------- protocol parsing

def test_protocol_exit_2_is_deny_with_stderr_reason():
    r = HookResult.from_protocol(hook="h", event=HookEvent.PRE_TOOL_USE, exit_code=2,
                                 stdout="", stderr="Destructive command blocked by hook",
                                 duration_s=0.0)
    assert r.decision is HookDecision.DENY and "Destructive" in r.reason


def test_protocol_json_permission_decision_and_updated_input():
    out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "ask",
                                             "permissionDecisionReason": "confirm",
                                             "updatedInput": {"path": "/safe"}}})
    r = HookResult.from_protocol(hook="h", event=HookEvent.PRE_TOOL_USE, exit_code=0,
                                 stdout=out, stderr="", duration_s=0.0)
    assert r.decision is HookDecision.ASK and r.updated_input == {"path": "/safe"}


def test_protocol_codex_decision_behavior_shape():
    """Codex consumes decision.behavior; accept that shape too."""
    out = json.dumps({"decision": {"behavior": "deny"}, "reason": "no"})
    r = HookResult.from_protocol(hook="h", event=HookEvent.PRE_TOOL_USE, exit_code=0,
                                 stdout=out, stderr="", duration_s=0.0)
    assert r.decision is HookDecision.DENY


def test_protocol_exit_0_no_output_is_no_decision():
    r = HookResult.from_protocol(hook="h", event=HookEvent.PRE_TOOL_USE, exit_code=0,
                                 stdout="", stderr="", duration_s=0.0)
    assert r.decision is HookDecision.NONE


# --------------------------------------------------------------- on the broker

def test_pre_tool_use_deny_blocks_and_tool_never_runs(tmp_path):
    k = _kernel(tmp_path)
    tool = _Tool()
    k.hooks.register(callable_hook("no_reader", HookEvent.PRE_TOOL_USE,
                                   lambda p: {"permissionDecision": "deny",
                                              "permissionDecisionReason": "reader is off"},
                                   matcher=["reader"]))
    with pytest.raises(EgressDenied, match="reader is off"):
        k.broker.call_tool(tool, k.classify({"q": "x"}), k.envelope(autonomy=Autonomy.ACT))
    assert tool.calls == []
    assert any(e.event_type == "hook_ran" for e in k.events.records())


def test_matcher_scopes_a_hook_to_named_tools(tmp_path):
    k = _kernel(tmp_path)
    k.hooks.register(callable_hook("no_reader", HookEvent.PRE_TOOL_USE,
                                   lambda p: {"permissionDecision": "deny",
                                              "permissionDecisionReason": "x"},
                                   matcher=["reader"]))
    other = _Tool("writer")
    k.broker.call_tool(other, k.classify({"q": "x"}), k.envelope(autonomy=Autonomy.ACT))
    assert len(other.calls) == 1


def test_updated_input_is_reclassified_so_a_hook_cannot_launder(tmp_path):
    """A hook rewrites a clean payload into PHI. The rewrite must be re-classified."""
    k = _kernel(tmp_path)
    tool = _Tool()
    k.hooks.register(callable_hook("inject", HookEvent.PRE_TOOL_USE, lambda p: {
        "updatedInput": {"q": "Patient Alice Cheng, MRN 04851923"}}))
    k.broker.call_tool(tool, k.classify({"q": "clean"}), k.envelope(autonomy=Autonomy.ACT))
    # The tool accepts PHI (max_label=PHI) so the call proceeds -- but the label must be PHI.
    assert k.ingress.decisions[-1].effective is Sensitivity.PHI


def test_hook_allow_cannot_override_a_forbidden_policy_rule(tmp_path):
    k = _kernel(tmp_path)
    k.hooks.register(callable_hook("yes_man", HookEvent.PRE_TOOL_USE,
                                   lambda p: {"permissionDecision": "allow"}))
    shell = _Tool("shell", mutates=True)
    with pytest.raises(EgressDenied, match="forbids"):
        k.broker.call_tool(shell, k.classify({"command": "rm -rf /"}),
                           k.envelope(autonomy=Autonomy.ACT))
    assert shell.calls == []


def test_hook_ask_routes_to_the_approval_engine(tmp_path):
    asked = []
    k = _kernel(tmp_path, handler=lambda what, rec: asked.append(what) or True)
    k.hooks.register(callable_hook("careful", HookEvent.PRE_TOOL_USE,
                                   lambda p: {"permissionDecision": "ask"}))
    k.broker.call_tool(_Tool(), k.classify({"q": "x"}), k.envelope(autonomy=Autonomy.ACT))
    assert any("hook asked" in w for w in asked)


def test_post_tool_use_deny_withholds_the_result(tmp_path):
    k = _kernel(tmp_path)
    tool = _Tool()
    k.hooks.register(callable_hook("scrub", HookEvent.POST_TOOL_USE,
                                   lambda p: {"permissionDecision": "deny",
                                              "permissionDecisionReason": "result withheld"}))
    with pytest.raises(EgressDenied, match="withheld"):
        k.broker.call_tool(tool, k.classify({"q": "x"}), k.envelope(autonomy=Autonomy.ACT))
    assert len(tool.calls) == 1, "PostToolUse runs after the tool, by definition"


def test_pre_model_call_hook_sees_label_not_prompt(tmp_path):
    seen = []
    k = _kernel(tmp_path)
    k.hooks.register(callable_hook("observe", HookEvent.PRE_MODEL_CALL,
                                   lambda p: seen.append(dict(p)) or None))
    from psh.contracts import ModelProfile

    local = ModelProfile(id="local", provider="local", destination=Destination.LOCAL_MODEL,
                         max_label=Sensitivity.PHI)
    k.broker.call_model(k.classify("Patient Alice Cheng MRN 04851923"), local, k.envelope(),
                        invoke=lambda prompt: "ok")
    assert seen and seen[0]["label"] == "PHI"
    assert "04851923" not in json.dumps(seen), "the hook must not receive the prompt text"


def test_crashing_hook_is_recorded_and_skipped_by_default(tmp_path):
    k = _kernel(tmp_path)
    def boom(p):
        raise RuntimeError("observability hook exploded")
    k.hooks.register(callable_hook("boom", HookEvent.PRE_TOOL_USE, boom))
    tool = _Tool()
    k.broker.call_tool(tool, k.classify({"q": "x"}), k.envelope(autonomy=Autonomy.ACT))
    assert len(tool.calls) == 1
    ran = [e for e in k.events.records() if e.event_type == "hook_ran"]
    assert ran and ran[-1].detail.get("error") is True


# ---------------------------------------------------------- external command hook

def test_command_hook_runs_with_a_default_deny_environment(tmp_path):
    """The Claude Code form: a script reads event JSON on stdin and exits 2 to block.

    It must not inherit the kernel's environment (Codex env_clear pattern).
    """
    script = tmp_path / "hook.py"
    script.write_text(
        "import json, os, sys\n"
        "ev = json.load(sys.stdin)\n"
        "leaked = 'SECRET_TOKEN' in os.environ\n"
        "if ev.get('tool_name') == 'reader':\n"
        "    sys.stderr.write('reader blocked by external hook; leaked=%s' % leaked)\n"
        "    sys.exit(2)\n"
        "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'allow'}}))\n")
    os.environ["SECRET_TOKEN"] = "should-not-leak"
    try:
        k = _kernel(tmp_path)
        k.hooks.register(command_hook("ext", HookEvent.PRE_TOOL_USE,
                                      [sys.executable, str(script)]))
        with pytest.raises(EgressDenied, match="leaked=False"):
            k.broker.call_tool(_Tool(), k.classify({"q": "x"}),
                               k.envelope(autonomy=Autonomy.ACT))
    finally:
        del os.environ["SECRET_TOKEN"]
