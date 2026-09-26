# Borrowed patterns: Codex and Claude Code → psh v0.4 · 借用模式：Codex 与 Claude Code → psh v0.4

Two primary sources, of different kinds, and the difference matters for how much weight each
claim below can bear:

- **Codex** (`openai/codex`, Apache-2.0) is open source. Every Codex pattern here cites a
  file path in the repository and was read from source on 2026-09-03. 165 files, 2.3M chars.
- **Claude Code** is **not** open source. Its repository holds documentation and issues, not
  implementation. Every Claude Code pattern here cites a page of the official documentation
  (`code.claude.com/docs/en/*`), fetched 2026-09-03. Where a page describes behaviour, I can
  say what the product *does*; I cannot say how it is *implemented*, and I have not guessed.

<!-- zh -->
两个主要来源，性质不同，而这一差异决定了下面每一条主张能承载多少分量：

- **Codex**（`openai/codex`，Apache-2.0）是开源的。这里的每一条 Codex 模式都引用仓库中的一个文件路径，并于 2026-09-03 读过源码。165 个文件，230 万字符。
- **Claude Code** **不是**开源的。它的仓库里放的是文档与议题（issues），不是实现。这里的每一条 Claude Code 模式都引用官方文档中的一页（`code.claude.com/docs/en/*`），于 2026-09-03 抓取。当某页描述行为时，我能说该产品**做了什么**；我不能说它**如何实现**，而且我没有猜测。


## What both do that psh did not · 两者都做了而 psh 没有做的事

The single most important convergence. Both systems reduce the burden on approval prompts by
**having the operating system enforce a boundary** rather than asking the model or the user
to respect one:

<!-- zh -->
最重要的一处趋同。两个系统都靠**让操作系统来强制一条边界**，而不是要求模型或用户去尊重一条边界，从而减轻审批提示的负担：


| | Codex | Claude Code | psh v0.3 |
|---|---|---|---|
| Filesystem isolation | Seatbelt (macOS), bubblewrap + seccomp (Linux) — `codex-rs/sandboxing/src/lib.rs`, `linux-sandbox/src/lib.rs` | Seatbelt (macOS), bubblewrap (Linux/WSL2) — `docs/en/sandboxing` | none |
| Network egress | local HTTP/SOCKS5 proxy with allow/deny host policy — `network-proxy/README.md` | domain allowlist enforced by the sandbox — `docs/en/sandboxing` | in-process gateway only |
| Environment | `env_clear()` then re-add by policy — `core/src/exec_env.rs`, `hooks/src/engine/command_runner.rs` | not documented | inherited |
| Process hardening | disable core dumps, deny ptrace, strip `LD_PRELOAD`/`DYLD_*` pre-main — `process-hardening/src/lib.rs` | not documented | none |

<!-- zh -->
| 维度 | Codex | Claude Code | psh v0.3 |
|---|---|---|---|
| 文件系统隔离 | Seatbelt（macOS）、bubblewrap + seccomp（Linux）—— `codex-rs/sandboxing/src/lib.rs`、`linux-sandbox/src/lib.rs` | Seatbelt（macOS）、bubblewrap（Linux/WSL2）—— `docs/en/sandboxing` | 无 |
| 网络出口 | 本地 HTTP/SOCKS5 代理，带允许/拒绝主机策略 —— `network-proxy/README.md` | 由沙箱强制的域名允许名单 —— `docs/en/sandboxing` | 仅进程内网关 |
| 环境 | `env_clear()` 之后按策略重新添加 —— `core/src/exec_env.rs`、`hooks/src/engine/command_runner.rs` | 未记载 | 继承 |
| 进程加固 | 禁用 core dump、拒绝 ptrace、在 main 之前剥离 `LD_PRELOAD`/`DYLD_*` —— `process-hardening/src/lib.rs` | 未记载 | 无 |


My own audit reproduced the consequence of that gap in one line: in-process code called the
provider directly and the broker saw nothing (`A1` in `handoff/self_audit.json`). Every psh
invariant rested on cooperation. Neither Codex nor Claude Code accepts that premise.

<!-- zh -->
我自己的一次审计用一行代码复现了这一差距的后果：进程内代码直接调用了提供方，而 broker 什么都没看到（`handoff/self_audit.json` 中的 `A1`）。psh 的每一条不变量都建立在"合作"之上。Codex 和 Claude Code 都不接受这个前提。


## Patterns adopted, with attribution · 已采纳的模式，附出处

### 1. Declarative execution policy with self-testing rules — from Codex · 1. 带自测试规则的声明式执行策略 —— 来自 Codex

**Source:** `codex-rs/execpolicy/README.md`, `codex-rs/execpolicy/src/policy.rs`.

<!-- zh -->
**来源：** `codex-rs/execpolicy/README.md`、`codex-rs/execpolicy/src/policy.rs`。

Codex expresses command policy as `prefix_rule(pattern, decision, justification, match,
not_match)`. Three properties transfer directly:

- `decision ∈ {allow, prompt, forbidden}` — three outcomes, not two. psh had allow/deny.
- `justification` is a first-class field, surfaced in refusals. A forbidden rule is asked to
  name an alternative ("Use `jj` instead of `git`").
- `match` / `not_match` are **examples validated at load time**. A rule that does not match
  its own examples is rejected before it can be applied. The rule tests itself.

<!-- zh -->
Codex 把命令策略表达为 `prefix_rule(pattern, decision, justification, match, not_match)`。有三条性质可以直接迁移：

- `decision ∈ {allow, prompt, forbidden}` —— 三种结果，而不是两种。psh 只有允许/拒绝。
- `justification` 是一等字段，会在拒绝中呈现。一条 forbidden 规则会被要求给出替代方案（"Use `jj` instead of `git`"）。
- `match` / `not_match` 是**在加载时被校验的示例**。一条不符合自身示例的规则会在能够被应用之前就被拒绝。规则会测试它自己。


**Landed in:** `psh/kernel/execpolicy.py`. psh's hardcoded destructive-command denylist becomes
the shipped default policy file, expressed in this form.

<!-- zh -->
**落地于：** `psh/kernel/execpolicy.py`。psh 中硬编码的破坏性命令拒绝名单（denylist）变成了随包发布的默认策略文件，用这种形式表达。


**Not borrowed:** Starlark. psh rules are Python dataclasses / JSON; a second language inside
a small kernel is cost without benefit at this scale.

<!-- zh -->
**未借用：** Starlark。psh 的规则是 Python dataclass / JSON；在一个小内核里再塞一门语言，在这个规模上是只有成本没有收益。


### 2. Approval writes back a rule — from Codex · 2. 审批会回写一条规则 —— 来自 Codex

**Source:** `codex-rs/protocol/src/protocol.rs`, `pub enum ReviewDecision` — variants
`Approved`, `ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }`,
`ApprovedForSession`, `ApprovedMcpPolicyAmendment`, `NetworkPolicyAmendment { … }`,
`Denied { rejection }`, `TimedOut`, `Abort`. Read verbatim from the fetched file. The payload
types `ExecPolicyAmendment` and `NetworkPolicyAmendment` are *referenced* there but defined in
crates not fetched, so this document cites the enum and its variants, not the payload structs.

<!-- zh -->
**来源：** `codex-rs/protocol/src/protocol.rs`，`pub enum ReviewDecision` —— 变体包括 `Approved`、`ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }`、`ApprovedForSession`、`ApprovedMcpPolicyAmendment`、`NetworkPolicyAmendment { … }`、`Denied { rejection }`、`TimedOut`、`Abort`。逐字读自所抓取的文件。载荷类型 `ExecPolicyAmendment` 与 `NetworkPolicyAmendment` 在那里被**引用**，但定义在未抓取的 crate 中，因此本文档引用的是该枚举及其变体，而不是那些载荷结构体。


A user decision is not only a yes/no on one call — it may carry an *amendment* that adds a
rule so the same question is not asked again, scoped to the session or persisted. This is how
a governed system stops being an interruption machine.

<!-- zh -->
用户的一次决定不只是对某一次调用的"是/否" —— 它可能携带一条**修正（amendment）**，添加一条规则，使得同一个问题不再被问第二次，可以限定在会话范围内或持久化。一个受治理的系统正是靠这个不再成为一台打断机器。


**Landed in:** `psh/kernel/approvals.py` — `ApprovalOutcome` carries an optional
`PolicyAmendment`; amendments are events, and are checked against the run envelope so an
approval can never grant more than the envelope holds.

<!-- zh -->
**落地于：** `psh/kernel/approvals.py` —— `ApprovalOutcome` 携带一个可选的 `PolicyAmendment`；修正是事件，并且会对照运行信封检查，因此一次审批授予的永远不会超过信封所持有的。


### 3. Rule evaluation order: deny, then ask, then allow — from Claude Code · 3. 规则求值顺序：先拒绝，再询问，后允许 —— 来自 Claude Code

**Source:** `docs/en/permissions`, "Rules are evaluated in order: deny, then ask, then allow.
The first match in that order determines the outcome, and rule specificity doesn't change the
order." A broad deny "can't carry allowlist exceptions".

<!-- zh -->
**来源：** `docs/en/permissions`，"Rules are evaluated in order: deny, then ask, then allow. The first match in that order determines the outcome, and rule specificity doesn't change the order." 一条宽泛的 deny "can't carry allowlist exceptions"。


This is the property that makes a policy file safe to hand to users: no amendment, however
specific, can punch a hole in a deny. psh's execpolicy evaluator enforces exactly this order,
and the amendment mechanism (pattern 2) can therefore only ever add `allow` rules that a
`forbidden` rule still overrides.

<!-- zh -->
正是这条性质让策略文件可以安全地交给用户：任何修正，无论多么具体，都无法在一条 deny 上打洞。psh 的 execpolicy 求值器精确强制这一顺序，因此修正机制（模式 2）永远只能添加 `allow` 规则，而 `forbidden` 规则仍然覆盖它们。


### 4. Typed hook lifecycle with a decision protocol — from Claude Code, confirmed by Codex · 4. 带决策协议的有类型钩子生命周期 —— 来自 Claude Code，并由 Codex 印证

**Source:** `docs/en/hooks` — events `PreToolUse`, `PostToolUse`, `PermissionRequest`,
`PreCompact`, `SessionStart`, `Stop`, and more; decision via `permissionDecision` ∈
{allow, deny, ask} with `permissionDecisionReason`; input rewriting via `updatedInput`; exit
code 2 = block, exit 0 = no decision.

<!-- zh -->
**来源：** `docs/en/hooks` —— 事件 `PreToolUse`、`PostToolUse`、`PermissionRequest`、`PreCompact`、`SessionStart`、`Stop` 等等；决策经 `permissionDecision` ∈ {allow, deny, ask}，并带 `permissionDecisionReason`；输入改写经 `updatedInput`；退出码 2 = 阻断，退出码 0 = 无决策。


**Convergence:** Codex's own hook implementation (`codex-rs/hooks/src/events/pre_tool_use.rs`,
`core/src/mcp_tool_call_tests.rs`) consumes the **same JSON shape** — `hook_event_name`,
`hookSpecificOutput`, `decision.behavior`. Two independent harnesses have converged on one
hook protocol, which is a strong reason to adopt that shape rather than invent a third.

<!-- zh -->
**趋同：** Codex 自己的钩子实现（`codex-rs/hooks/src/events/pre_tool_use.rs`、`core/src/mcp_tool_call_tests.rs`）消费**同样的 JSON 形状** —— `hook_event_name`、`hookSpecificOutput`、`decision.behavior`。两个独立的执行框架趋同到了同一份钩子协议，这是采纳该形状、而不是另造第三种的有力理由。


**Landed in:** `psh/kernel/hooks.py`. Hooks run *inside the broker*, so the path that bypasses
the broker also bypasses hooks — hooks are not a second boundary, they are a policy surface on
the one boundary.

<!-- zh -->
**落地于：** `psh/kernel/hooks.py`。钩子运行**在 broker 内部**，因此绕过 broker 的路径也绕过了钩子 —— 钩子不是第二道边界，它们是那唯一一道边界上的一个策略面。


### 5. Default-deny environment and process hardening — from Codex · 5. 默认拒绝的环境与进程加固 —— 来自 Codex

**Source:** `codex-rs/core/src/exec_env.rs` ("`env_clear()` to ensure no unintended variables
are leaked to the spawned process"), `hooks/src/engine/command_runner.rs`
(`command.env_clear(); … scrub_non_inheritable_env_vars`), `process-hardening/src/lib.rs`.

The environment is *rebuilt from policy*, not inherited-then-scrubbed. A scrub list misses
the variable you did not think of; `env_clear()` does not.

<!-- zh -->
**来源：** `codex-rs/core/src/exec_env.rs`（"`env_clear()` to ensure no unintended variables are leaked to the spawned process"）、`hooks/src/engine/command_runner.rs`（`command.env_clear(); … scrub_non_inheritable_env_vars`）、`process-hardening/src/lib.rs`。

环境是**从策略重建**的，不是先继承再擦洗。一份擦洗清单会漏掉你没想到的那个变量；`env_clear()` 不会。


**Landed in:** `psh/kernel/isolation.py` — tool subprocesses start from an empty environment
and receive only what the run envelope grants, plus `HTTP_PROXY`/`HTTPS_PROXY` pointed at the
kernel's egress proxy.

<!-- zh -->
**落地于：** `psh/kernel/isolation.py` —— 工具子进程从一个空环境启动，只接收运行信封所授予的内容，外加指向内核出口代理的 `HTTP_PROXY`/`HTTPS_PROXY`。


### 6. Egress proxy as the network boundary — from Codex · 6. 出口代理作为网络边界 —— 来自 Codex

**Source:** `codex-rs/network-proxy/README.md`: a local HTTP + SOCKS5 proxy enforcing
allow/deny host policy; "limited" mode for read-only network; private-range destinations
rejected unless explicitly allowlisted, and *hostnames resolving to private IPs blocked even
when allowlisted*. `exec-server-protocol/src/network_policy.rs`:
`ExecServerNetworkPolicyDecision ∈ {Allow, Deny{reason}, Ask{reason}}` — the same three-way
decision as execpolicy.

<!-- zh -->
**来源：** `codex-rs/network-proxy/README.md`：一个本地 HTTP + SOCKS5 代理，强制允许/拒绝主机策略；"limited" 模式用于只读网络；私有地址段目的地除非被显式列入允许名单否则拒绝，并且*解析到私有 IP 的主机名即使被列入允许名单也会被阻断*。`exec-server-protocol/src/network_policy.rs`：`ExecServerNetworkPolicyDecision ∈ {Allow, Deny{reason}, Ask{reason}}` —— 与 execpolicy 相同的三路决策。


**Landed in:** `psh/kernel/isolation.py` — a kernel-owned HTTP CONNECT proxy on loopback that
resolves the run envelope's `allowed_destinations` to a per-host decision, logs every decision
as an event, and refuses private ranges by default.

<!-- zh -->
**落地于：** `psh/kernel/isolation.py` —— 一个内核自有的、监听回环地址的 HTTP CONNECT 代理，把运行信封的 `allowed_destinations` 解析为逐主机决策，把每一次决策记录为一条事件，并默认拒绝私有地址段。


**Honest limit:** psh's proxy is HTTP CONNECT only (no SOCKS5, no MITM). A subprocess that
opens a raw socket bypasses it. Codex closes that with the OS sandbox denying network to the
process; psh cannot install bubblewrap or Seatbelt from inside this session, so that layer
remains an adapter interface. What was *verified* here is: a subprocess honouring proxy
environment variables is refused for a non-allowlisted host, and the refusal is logged.

<!-- zh -->
**诚实的限制：** psh 的代理只支持 HTTP CONNECT（没有 SOCKS5，没有 MITM）。一个打开裸套接字的子进程会绕过它。Codex 靠操作系统沙箱拒绝该进程的网络来堵住这一点；psh 无法从本次会话内部安装 bubblewrap 或 Seatbelt，因此那一层仍是一个适配器接口。这里**已验证**的是：一个遵守代理环境变量的子进程，对非允许名单中的主机会被拒绝，且该拒绝会被记录。


### 7. Subagent isolation with a restricted tool set — from Claude Code · 7. 带受限工具集的子智能体隔离 —— 来自 Claude Code

**Source:** `docs/en/sub-agents`: "Each subagent runs in its own context window with a custom
system prompt, specific tool access, and independent permissions"; "inherits the parent
conversation's permissions; most run with a restricted tool set"; model "capped at" the
parent's so a child never runs on a more expensive model than the parent chose.

<!-- zh -->
**来源：** `docs/en/sub-agents`："Each subagent runs in its own context window with a custom system prompt, specific tool access, and independent permissions"；"inherits the parent conversation's permissions; most run with a restricted tool set"；模型"capped at"父级的，因此子级绝不会跑在比父级所选更贵的模型上。


psh's `DelegationContract` already required the child's authority to be a subset of the
parent's (the lattice). What transfers is the *model cap* as an authority dimension — a child
may not select a model the parent did not permit — and the explicit tool list rather than
inheritance.

<!-- zh -->
psh 的 `DelegationContract` 早已要求子级的权限是父级权限的子集（权限格）。可迁移的是把**模型上限**作为一个权限维度 —— 子级不得选用父级未许可的模型 —— 以及显式的工具清单，而不是继承。


**Landed in:** the explicit tool list — `RunEnvelope.allowed_capabilities` /
`denied_capabilities`, contained by `AuthorityLattice.violations` on every `restrict`,
delegation and subagent creation (`psh/kernel/authority.py`), with the `UNRESTRICTED`
sentinel so an empty intersection is not read as unlimited authority.

<!-- zh -->
**落地于：** 显式的工具清单 —— `RunEnvelope.allowed_capabilities` / `denied_capabilities`，在每一次 `restrict`、委派与子智能体创建时由 `AuthorityLattice.violations` 施加包含约束（`psh/kernel/authority.py`），并以 `UNRESTRICTED` 哨兵值确保空交集不会被读成不受限的权限。


**NOT landed, and previously misdescribed here:** the *model cap*. Until v0.5 this section
claimed "`AuthorityLattice.BUDGET_DIMENSIONS` gains `max_model_tier`". No such dimension
exists in the code, and none ever did — an authority claim that reads as implemented and is
not. It is recorded here as an open item rather than quietly deleted, because a manuscript
drawing on this document would otherwise state that the lattice governs model selection. A
child's model choice is today constrained only indirectly, by the destinations and
capabilities its envelope permits.

<!-- zh -->
**未落地，且此处先前描述有误：** *模型上限*。直到 v0.5，本节还声称 "`AuthorityLattice.BUDGET_DIMENSIONS` gains `max_model_tier`"。代码中不存在这样的维度，也从未存在过 —— 这是一条读起来像已实现、实际却没有的权限主张。它被作为未决项记录在这里，而不是悄悄删除，因为一篇引用本文档的稿件否则会声称权限格治理了模型选择。子级的模型选择今天只被间接约束，即受其信封所允许的目的地与能力约束。


Documented claims are now checked against the code by test (`tests/test_self_audit.py`,
`tests/test_enforcement_closure.py`); the audit that found this one is the v0.5 practice of
requiring every architecture claim to name the code that enforces it and at least one test
that exercises it.

<!-- zh -->
文档中的主张现在由测试对照代码检查（`tests/test_self_audit.py`、`tests/test_enforcement_closure.py`）；发现这一条的那次审计，就是 v0.5 的实践：要求每一条架构主张都指名执行它的代码，以及至少一个演练它的测试。


## Patterns deliberately not borrowed · 刻意未借用的模式

| Pattern | Source | Why not |
|---|---|---|
| Classifier-based auto mode (a model reviews actions instead of the user) | Claude Code `docs/en/permission-modes` | A model judging whether a model's action is safe is a second model call on the critical path, and it needs its own egress decision. psh's position is that policy decisions must not themselves require an egress. Defensible either way; this is the choice, stated. |
| SOCKS5 + HTTPS MITM in the proxy | Codex `network-proxy` | MITM means the kernel holds a CA private key and terminates TLS for every tool. That is a large trust concentration for a single-user research harness; CONNECT-level host policy covers the threat model without it. |
| Starlark policy language | Codex `execpolicy` | See pattern 1. |
| Multi-platform sandbox (Seatbelt / bwrap / Windows) | both | Cannot be installed or verified from inside this session. Adapter interface, honestly labelled. |
| Remote compaction (`compact_remote*.rs`) | Codex | Compaction that calls a remote model is an egress; psh compacts locally by design so the compaction path never needs a policy decision. |

<!-- zh -->
| 模式 | 来源 | 为何不用 |
|---|---|---|
| 基于分类器的自动模式（由模型而非用户来审查动作） | Claude Code `docs/en/permission-modes` | 在关键路径上让一个模型去判断另一个模型的动作是否安全，是对模型的第二次调用，而且它自身还需要一个出口决策。psh 的立场是：策略决策本身不得要求一次出口。两种做法都站得住；这就是那个选择，明说在此。 |
| 代理中的 SOCKS5 + HTTPS MITM | Codex `network-proxy` | MITM 意味着内核持有一把 CA 私钥，并为每一个工具终结 TLS。对一个单用户研究执行框架来说，那是过大的信任集中；CONNECT 层面的主机策略在不引入它的前提下已覆盖该威胁模型。 |
| Starlark 策略语言 | Codex `execpolicy` | 见模式 1。 |
| 多平台沙箱（Seatbelt / bwrap / Windows） | 两者 | 无法从本次会话内部安装或验证。适配器接口，如实标注。 |
| 远程压缩（`compact_remote*.rs`） | Codex | 调用远程模型的压缩就是一次出口；psh 有意在本地做压缩，因此压缩路径永远不需要一个策略决策。 |


## What psh does that neither source does · psh 做了而两个来源都没做的事

Recorded so the borrowing is not mistaken for convergence on everything:

- **Data-label taint** through derivations, containers and results. Both sources gate
  *actions*; neither labels *data*. Codex's `secrets` crate redacts known secret values, which
  is a denylist of strings, not a lattice.
- **Claim–evidence verification** with structured population/subject/outcome scope. Out of
  scope for a coding agent, and absent from both.
- **A hash-chained event store.** Codex `rollout/src/policy.rs` decides what to persist;
  nothing in either source makes the persisted record tamper-evident.

<!-- zh -->
记录下来，是为了不让"借用"被误认为在所有事情上都趋同：

- 贯穿派生、容器与结果的**数据标签污点（data-label taint）**。两个来源都只对*动作*设卡；两者都不给*数据*打标签。Codex 的 `secrets` crate 会脱敏已知的机密值，那是一份字符串拒绝名单，不是一个格。
- 带结构化人群/受试对象/结局范围的**主张–证据校验**。这在编码智能体的范围之外，两者都没有。
- **哈希链事件存储。** Codex 的 `rollout/src/policy.rs` 决定持久化什么；两个来源中都没有任何东西让持久化记录具备防篡改可见性。

