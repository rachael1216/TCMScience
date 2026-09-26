# Borrowed patterns: Codex and Claude Code → psh v0.4

Two primary sources, of different kinds, and the difference matters for how much weight each
claim below can bear:

- **Codex** (`openai/codex`, Apache-2.0) is open source. Every Codex pattern here cites a
  file path in the repository and was read from source on 2026-09-03. 165 files, 2.3M chars.
- **Claude Code** is **not** open source. Its repository holds documentation and issues, not
  implementation. Every Claude Code pattern here cites a page of the official documentation
  (`code.claude.com/docs/en/*`), fetched 2026-09-03. Where a page describes behaviour, I can
  say what the product *does*; I cannot say how it is *implemented*, and I have not guessed.

## What both do that psh did not

The single most important convergence. Both systems reduce the burden on approval prompts by
**having the operating system enforce a boundary** rather than asking the model or the user
to respect one:

| | Codex | Claude Code | psh v0.3 |
|---|---|---|---|
| Filesystem isolation | Seatbelt (macOS), bubblewrap + seccomp (Linux) — `codex-rs/sandboxing/src/lib.rs`, `linux-sandbox/src/lib.rs` | Seatbelt (macOS), bubblewrap (Linux/WSL2) — `docs/en/sandboxing` | none |
| Network egress | local HTTP/SOCKS5 proxy with allow/deny host policy — `network-proxy/README.md` | domain allowlist enforced by the sandbox — `docs/en/sandboxing` | in-process gateway only |
| Environment | `env_clear()` then re-add by policy — `core/src/exec_env.rs`, `hooks/src/engine/command_runner.rs` | not documented | inherited |
| Process hardening | disable core dumps, deny ptrace, strip `LD_PRELOAD`/`DYLD_*` pre-main — `process-hardening/src/lib.rs` | not documented | none |

My own audit reproduced the consequence of that gap in one line: in-process code called the
provider directly and the broker saw nothing (`A1` in `handoff/self_audit.json`). Every psh
invariant rested on cooperation. Neither Codex nor Claude Code accepts that premise.

## Patterns adopted, with attribution

### 1. Declarative execution policy with self-testing rules — from Codex

**Source:** `codex-rs/execpolicy/README.md`, `codex-rs/execpolicy/src/policy.rs`.

Codex expresses command policy as `prefix_rule(pattern, decision, justification, match,
not_match)`. Three properties transfer directly:

- `decision ∈ {allow, prompt, forbidden}` — three outcomes, not two. psh had allow/deny.
- `justification` is a first-class field, surfaced in refusals. A forbidden rule is asked to
  name an alternative ("Use `jj` instead of `git`").
- `match` / `not_match` are **examples validated at load time**. A rule that does not match
  its own examples is rejected before it can be applied. The rule tests itself.

**Landed in:** `psh/kernel/execpolicy.py`. psh's hardcoded destructive-command denylist becomes
the shipped default policy file, expressed in this form.

**Not borrowed:** Starlark. psh rules are Python dataclasses / JSON; a second language inside
a small kernel is cost without benefit at this scale.

### 2. Approval writes back a rule — from Codex

**Source:** `codex-rs/protocol/src/protocol.rs`, `pub enum ReviewDecision` — variants
`Approved`, `ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }`,
`ApprovedForSession`, `ApprovedMcpPolicyAmendment`, `NetworkPolicyAmendment { … }`,
`Denied { rejection }`, `TimedOut`, `Abort`. Read verbatim from the fetched file. The payload
types `ExecPolicyAmendment` and `NetworkPolicyAmendment` are *referenced* there but defined in
crates not fetched, so this document cites the enum and its variants, not the payload structs.

A user decision is not only a yes/no on one call — it may carry an *amendment* that adds a
rule so the same question is not asked again, scoped to the session or persisted. This is how
a governed system stops being an interruption machine.

**Landed in:** `psh/kernel/approvals.py` — `ApprovalOutcome` carries an optional
`PolicyAmendment`; amendments are events, and are checked against the run envelope so an
approval can never grant more than the envelope holds.

### 3. Rule evaluation order: deny, then ask, then allow — from Claude Code

**Source:** `docs/en/permissions`, "Rules are evaluated in order: deny, then ask, then allow.
The first match in that order determines the outcome, and rule specificity doesn't change the
order." A broad deny "can't carry allowlist exceptions".

This is the property that makes a policy file safe to hand to users: no amendment, however
specific, can punch a hole in a deny. psh's execpolicy evaluator enforces exactly this order,
and the amendment mechanism (pattern 2) can therefore only ever add `allow` rules that a
`forbidden` rule still overrides.

### 4. Typed hook lifecycle with a decision protocol — from Claude Code, confirmed by Codex

**Source:** `docs/en/hooks` — events `PreToolUse`, `PostToolUse`, `PermissionRequest`,
`PreCompact`, `SessionStart`, `Stop`, and more; decision via `permissionDecision` ∈
{allow, deny, ask} with `permissionDecisionReason`; input rewriting via `updatedInput`; exit
code 2 = block, exit 0 = no decision.

**Convergence:** Codex's own hook implementation (`codex-rs/hooks/src/events/pre_tool_use.rs`,
`core/src/mcp_tool_call_tests.rs`) consumes the **same JSON shape** — `hook_event_name`,
`hookSpecificOutput`, `decision.behavior`. Two independent harnesses have converged on one
hook protocol, which is a strong reason to adopt that shape rather than invent a third.

**Landed in:** `psh/kernel/hooks.py`. Hooks run *inside the broker*, so the path that bypasses
the broker also bypasses hooks — hooks are not a second boundary, they are a policy surface on
the one boundary.

### 5. Default-deny environment and process hardening — from Codex

**Source:** `codex-rs/core/src/exec_env.rs` ("`env_clear()` to ensure no unintended variables
are leaked to the spawned process"), `hooks/src/engine/command_runner.rs`
(`command.env_clear(); … scrub_non_inheritable_env_vars`), `process-hardening/src/lib.rs`.

The environment is *rebuilt from policy*, not inherited-then-scrubbed. A scrub list misses
the variable you did not think of; `env_clear()` does not.

**Landed in:** `psh/kernel/isolation.py` — tool subprocesses start from an empty environment
and receive only what the run envelope grants, plus `HTTP_PROXY`/`HTTPS_PROXY` pointed at the
kernel's egress proxy.

### 6. Egress proxy as the network boundary — from Codex

**Source:** `codex-rs/network-proxy/README.md`: a local HTTP + SOCKS5 proxy enforcing
allow/deny host policy; "limited" mode for read-only network; private-range destinations
rejected unless explicitly allowlisted, and *hostnames resolving to private IPs blocked even
when allowlisted*. `exec-server-protocol/src/network_policy.rs`:
`ExecServerNetworkPolicyDecision ∈ {Allow, Deny{reason}, Ask{reason}}` — the same three-way
decision as execpolicy.

**Landed in:** `psh/kernel/isolation.py` — a kernel-owned HTTP CONNECT proxy on loopback that
resolves the run envelope's `allowed_destinations` to a per-host decision, logs every decision
as an event, and refuses private ranges by default.

**Honest limit:** psh's proxy is HTTP CONNECT only (no SOCKS5, no MITM). A subprocess that
opens a raw socket bypasses it. Codex closes that with the OS sandbox denying network to the
process; psh cannot install bubblewrap or Seatbelt from inside this session, so that layer
remains an adapter interface. What was *verified* here is: a subprocess honouring proxy
environment variables is refused for a non-allowlisted host, and the refusal is logged.

### 7. Subagent isolation with a restricted tool set — from Claude Code

**Source:** `docs/en/sub-agents`: "Each subagent runs in its own context window with a custom
system prompt, specific tool access, and independent permissions"; "inherits the parent
conversation's permissions; most run with a restricted tool set"; model "capped at" the
parent's so a child never runs on a more expensive model than the parent chose.

psh's `DelegationContract` already required the child's authority to be a subset of the
parent's (the lattice). What transfers is the *model cap* as an authority dimension — a child
may not select a model the parent did not permit — and the explicit tool list rather than
inheritance.

**Landed in:** the explicit tool list — `RunEnvelope.allowed_capabilities` /
`denied_capabilities`, contained by `AuthorityLattice.violations` on every `restrict`,
delegation and subagent creation (`psh/kernel/authority.py`), with the `UNRESTRICTED`
sentinel so an empty intersection is not read as unlimited authority.

**NOT landed, and previously misdescribed here:** the *model cap*. Until v0.5 this section
claimed "`AuthorityLattice.BUDGET_DIMENSIONS` gains `max_model_tier`". No such dimension
exists in the code, and none ever did — an authority claim that reads as implemented and is
not. It is recorded here as an open item rather than quietly deleted, because a manuscript
drawing on this document would otherwise state that the lattice governs model selection. A
child's model choice is today constrained only indirectly, by the destinations and
capabilities its envelope permits.

Documented claims are now checked against the code by test (`tests/test_self_audit.py`,
`tests/test_enforcement_closure.py`); the audit that found this one is the v0.5 practice of
requiring every architecture claim to name the code that enforces it and at least one test
that exercises it.

## Patterns deliberately not borrowed

| Pattern | Source | Why not |
|---|---|---|
| Classifier-based auto mode (a model reviews actions instead of the user) | Claude Code `docs/en/permission-modes` | A model judging whether a model's action is safe is a second model call on the critical path, and it needs its own egress decision. psh's position is that policy decisions must not themselves require an egress. Defensible either way; this is the choice, stated. |
| SOCKS5 + HTTPS MITM in the proxy | Codex `network-proxy` | MITM means the kernel holds a CA private key and terminates TLS for every tool. That is a large trust concentration for a single-user research harness; CONNECT-level host policy covers the threat model without it. |
| Starlark policy language | Codex `execpolicy` | See pattern 1. |
| Multi-platform sandbox (Seatbelt / bwrap / Windows) | both | Cannot be installed or verified from inside this session. Adapter interface, honestly labelled. |
| Remote compaction (`compact_remote*.rs`) | Codex | Compaction that calls a remote model is an egress; psh compacts locally by design so the compaction path never needs a policy decision. |

## What psh does that neither source does

Recorded so the borrowing is not mistaken for convergence on everything:

- **Data-label taint** through derivations, containers and results. Both sources gate
  *actions*; neither labels *data*. Codex's `secrets` crate redacts known secret values, which
  is a denylist of strings, not a lattice.
- **Claim–evidence verification** with structured population/subject/outcome scope. Out of
  scope for a coding agent, and absent from both.
- **A hash-chained event store.** Codex `rollout/src/policy.rs` decides what to persist;
  nothing in either source makes the persisted record tamper-evident.
