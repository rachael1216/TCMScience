"""Typed hook lifecycle with a decision protocol.

Borrowed from Claude Code (`docs/en/hooks`) and confirmed by Codex, whose own hook engine
(`codex-rs/hooks/src/events/pre_tool_use.rs`, `core/src/mcp_tool_call_tests.rs`) consumes the
same JSON shape. Two independent harnesses converging on one protocol is the strongest
available reason to adopt it rather than invent a third.

The protocol, as documented and as Codex implements it:

* Events: ``PreToolUse``, ``PostToolUse``, ``PermissionRequest``, ``PreCompact``,
  ``SessionStart``, ``SessionEnd``, ``Stop``, plus ``PreModelCall`` / ``PostModelCall`` which
  psh adds because its boundary is the model call, not only the tool call.
* Input: a JSON object carrying ``hook_event_name`` and the event's payload.
* Output: exit code 0 = no decision (normal flow continues); exit code 2 = block, with the
  reason on stderr; optionally a JSON object on stdout with ``hookSpecificOutput`` carrying
  ``permissionDecision`` ∈ {allow, deny, ask}, ``permissionDecisionReason`` and
  ``updatedInput`` (a rewritten payload).

Where psh differs, and why
--------------------------
Hooks run **inside the broker**, on the one trusted path, and only there. They are a policy
surface on the boundary, not a second boundary: whatever bypasses the broker also bypasses
hooks, and this module makes no claim otherwise. Two consequences follow from psh's other
invariants:

* A hook that returns ``allow`` cannot override a ``forbidden`` execution-policy rule or an
  egress refusal. Hooks are consulted *after* those gates and can only narrow (deny/ask) or
  rewrite; the most permissive thing a hook can do is decline to decide.
* A hook's ``updatedInput`` is re-classified at ingress like any other value. A hook cannot
  launder a label by rewriting a payload.

Hooks may be in-process callables (fast, testable) or external commands (the Claude Code /
Codex form, run as a subprocess with the event JSON on stdin). External hooks run under the
same default-deny environment as tools.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

__all__ = ["HookEvent", "HookDecision", "HookResult", "HookSpec", "HookRegistry",
           "HookBlocked", "callable_hook", "command_hook", "redact_payload", "CONTENT_KEYS"]


class HookEvent(str, Enum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_MODEL_CALL = "PreModelCall"        # psh addition: the model call is a boundary too
    POST_MODEL_CALL = "PostModelCall"
    PERMISSION_REQUEST = "PermissionRequest"
    PRE_COMPACT = "PreCompact"
    STOP = "Stop"


class HookDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    NONE = "none"          # exit 0, no output: the hook declined to decide


class HookBlocked(Exception):
    """A hook denied the operation."""

    def __init__(self, message: str, *, hook: str) -> None:
        super().__init__(message)
        self.hook = hook


@dataclass(frozen=True, slots=True)
class HookResult:
    hook: str
    event: HookEvent
    decision: HookDecision = HookDecision.NONE
    reason: str = ""
    updated_input: Any = None
    exit_code: int = 0
    duration_s: float = 0.0
    error: str = ""

    @classmethod
    def from_protocol(cls, *, hook: str, event: HookEvent, exit_code: int, stdout: str,
                      stderr: str, duration_s: float) -> "HookResult":
        """Parse the Claude Code / Codex hook output protocol."""
        if exit_code == 2:
            return cls(hook=hook, event=event, decision=HookDecision.DENY,
                       reason=(stderr.strip() or "hook exited 2 without a reason"),
                       exit_code=2, duration_s=duration_s)
        if exit_code != 0:
            # A crashing hook is not a decision. It is recorded and treated as NONE; policy
            # elsewhere decides whether an erroring hook should fail the call.
            return cls(hook=hook, event=event, exit_code=exit_code, duration_s=duration_s,
                       error=(stderr.strip() or f"hook exited {exit_code}")[:300])
        decision, reason, updated = HookDecision.NONE, "", None
        text = stdout.strip()
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {}
            specific = payload.get("hookSpecificOutput", payload) if isinstance(payload, dict) else {}
            if isinstance(specific, dict):
                raw = specific.get("permissionDecision") or specific.get("decision")
                if isinstance(raw, dict):           # Codex: decision.behavior
                    raw = raw.get("behavior")
                if raw in ("allow", "deny", "ask"):
                    decision = HookDecision(raw)
                reason = str(specific.get("permissionDecisionReason", "") or
                             specific.get("reason", ""))
                updated = specific.get("updatedInput")
        return cls(hook=hook, event=event, decision=decision, reason=reason,
                   updated_input=updated, exit_code=0, duration_s=duration_s)


Runner = Callable[[Mapping[str, Any]], HookResult]


@dataclass(frozen=True, slots=True)
class HookSpec:
    name: str
    event: HookEvent
    run: Runner
    #: Restrict to tool ids / model ids matching any of these (fnmatch). Empty = all.
    matcher: tuple[str, ...] = ()
    timeout_s: float = 10.0
    #: Whether this hook is inside the kernel's trust boundary and may therefore see the
    #: content of a tool payload.
    #:
    #: An in-process callable already shares the kernel's address space, so withholding the
    #: payload from it would be theatre — those default to trusted. An external command is a
    #: different process, usually third-party, and handing it the unwrapped payload makes it
    #: a second reader of PHI with its own unsupervised network access: the payload would
    #: have left the governed path through a door the broker does not watch. External hooks
    #: therefore default to untrusted and receive a redacted view.
    trusted: bool = False


def callable_hook(name: str, event: HookEvent,
                  fn: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
                  *, matcher: Sequence[str] = (), trusted: bool = True) -> HookSpec:
    """An in-process hook. ``fn`` returns a hookSpecificOutput-shaped dict or None."""

    def run(payload: Mapping[str, Any]) -> HookResult:
        started = time.time()
        try:
            out = fn(payload)
        except HookBlocked as exc:
            return HookResult(hook=name, event=event, decision=HookDecision.DENY,
                              reason=str(exc), exit_code=2, duration_s=time.time() - started)
        except Exception as exc:  # noqa: BLE001 - a crashing hook is recorded, not fatal
            return HookResult(hook=name, event=event, exit_code=1, error=repr(exc)[:300],
                              duration_s=time.time() - started)
        stdout = json.dumps({"hookSpecificOutput": dict(out)}) if out else ""
        return HookResult.from_protocol(hook=name, event=event, exit_code=0, stdout=stdout,
                                        stderr="", duration_s=time.time() - started)

    return HookSpec(name=name, event=event, run=run, matcher=tuple(matcher),
                    trusted=trusted)


def command_hook(name: str, event: HookEvent, argv: Sequence[str], *,
                 matcher: Sequence[str] = (), timeout_s: float = 10.0,
                 env: Mapping[str, str] | None = None, trusted: bool = False,
                 allowed_hosts: Sequence[str] = (), runner: Any = None,
                 workdir: Any = None) -> HookSpec:
    """An external hook in the Claude Code / Codex form: event JSON on stdin, protocol out.

    Runs through the same :class:`~psh.kernel.isolation.IsolatedRunner` a tool does, for the
    reason the reviewer put plainly: v0.4 ran hooks with a bare ``subprocess.run`` and handed
    ``PRE_TOOL_USE`` the fully unwrapped payload, so a third-party hook received the PHI a
    tool was about to process and could post it anywhere — egress through a door the broker
    does not watch, in a system whose central claim is that there is one door.

    So an external hook gets: a clean environment, a working directory of its own, the
    kernel's egress proxy (with ``allowed_hosts`` empty by default, meaning no network at
    all), a wall-clock limit, and a redacted view of the payload unless declared ``trusted``.
    """

    def run(payload: Mapping[str, Any]) -> HookResult:
        from .isolation import IsolatedRunner

        started = time.time()
        active = runner or IsolatedRunner()
        base = Path(workdir) if workdir is not None else Path(tempfile.gettempdir()) / "psh-hooks"
        try:
            result = active.run(list(argv), workdir=base / name,
                                allowed_hosts=list(allowed_hosts),
                                grants=dict(env or {}), timeout_s=timeout_s,
                                stdin=json.dumps(payload, default=str))
        except Exception as exc:  # noqa: BLE001 - an unrunnable hook is recorded, not fatal
            return HookResult(hook=name, event=event, exit_code=1, error=repr(exc)[:300],
                              duration_s=time.time() - started)
        if result.timed_out:
            return HookResult(hook=name, event=event, exit_code=124, error="hook timed out",
                              duration_s=time.time() - started)
        return HookResult.from_protocol(hook=name, event=event, exit_code=result.exit_code,
                                        stdout=result.stdout, stderr=result.stderr,
                                        duration_s=time.time() - started)

    return HookSpec(name=name, event=event, run=run, matcher=tuple(matcher),
                    timeout_s=timeout_s, trusted=trusted)


#: Payload keys whose *content* an untrusted hook must not receive. It still learns that
#: there was an input, its shape and a stable digest — enough to decide, not enough to
#: exfiltrate.
CONTENT_KEYS = ("tool_input", "tool_response", "prompt", "content", "text")


def redact_payload(body: Mapping[str, Any]) -> dict[str, Any]:
    """Replace content-bearing values with a digest and a description of their shape."""
    from ..contracts import content_hash

    out: dict[str, Any] = {}
    for key, value in body.items():
        if key in CONTENT_KEYS and value is not None:
            summary: dict[str, Any] = {"redacted": True, "digest": content_hash(value)[:16],
                                       "type": type(value).__name__}
            if isinstance(value, Mapping):
                summary["keys"] = sorted(str(k) for k in value)
            elif isinstance(value, str):
                summary["chars"] = len(value)
            out[key] = summary
        else:
            out[key] = value
    return out


class HookRegistry:
    """Holds hooks and dispatches events on the trusted path."""

    def __init__(self, *, audit: Callable[..., Any] | None = None,
                 fail_on_hook_error: bool = False) -> None:
        self._hooks: list[HookSpec] = []
        self._audit = audit
        #: If True, a hook that crashes fails the operation (fail closed). Default False:
        #: a broken hook is recorded and skipped, because an observability hook must not be
        #: able to take the harness down. Governance-critical logic belongs in gates, which
        #: do fail closed — hooks are a policy surface, not the boundary.
        self.fail_on_hook_error = fail_on_hook_error
        self.dispatched = 0
        self.denials = 0
        self.rewrites = 0

    def register(self, spec: HookSpec) -> None:
        self._hooks.append(spec)

    def hooks_for(self, event: HookEvent, target: str = "") -> list[HookSpec]:
        import fnmatch

        out = []
        for h in self._hooks:
            if h.event is not event:
                continue
            if h.matcher and not any(fnmatch.fnmatch(target, m) for m in h.matcher):
                continue
            out.append(h)
        return out

    def dispatch(self, event: HookEvent, *, target: str = "", run_id: str = "",
                 payload: Mapping[str, Any] | None = None) -> tuple[HookDecision, Any, list[HookResult]]:
        """Run every matching hook. Returns (effective decision, rewritten input, results).

        Combination rule: any DENY wins; else any ASK wins; else the last ``updatedInput``
        applies; an ALLOW from a hook is recorded but grants nothing beyond what gates already
        permitted — the most permissive thing a hook can do is decline to decide.
        """
        body = {"hook_event_name": event.value, "run_id": run_id, "target": target,
                **(dict(payload) if payload else {})}
        results: list[HookResult] = []
        decision, updated = HookDecision.NONE, None
        for spec in self.hooks_for(event, target):
            self.dispatched += 1
            res = spec.run(body if spec.trusted else redact_payload(body))
            results.append(res)
            if self._audit is not None:
                self._audit("hook_ran", hook=spec.name, event=event.value, target=target,
                            decision=res.decision.value, exit_code=res.exit_code,
                            error=bool(res.error), run_id=run_id)
            if res.error and self.fail_on_hook_error:
                raise HookBlocked(f"hook {spec.name} failed: {res.error}", hook=spec.name)
            if res.decision is HookDecision.DENY:
                self.denials += 1
                raise HookBlocked(res.reason or f"denied by hook {spec.name}", hook=spec.name)
            if res.decision is HookDecision.ASK and decision is not HookDecision.DENY:
                decision = HookDecision.ASK
            if res.updated_input is not None:
                self.rewrites += 1
                updated = res.updated_input
                body = {**body, "tool_input": updated}
        return decision, updated, results

    def stats(self) -> dict[str, Any]:
        return {"hooks": len(self._hooks), "dispatched": self.dispatched,
                "denials": self.denials, "rewrites": self.rewrites}
