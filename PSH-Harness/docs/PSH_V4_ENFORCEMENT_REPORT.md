# psh v0.4 — Self-audit closure and borrowed enforcement · psh v0.4 — 自审计闭环与借用的强制执行

**What changed.** Two things, in this order. First, I turned the review method on my own code:
an adversarial self-audit of v0.3 found eleven vulnerabilities, several worse than anything the
four external reviews had found, and nine are now closed as tests. Second, the user asked that
Codex and Claude Code be leveraged as far as possible. Codex is open source and was read from
source (165 files, 2.3M chars); Claude Code is not, and was read from its official documentation
(20 pages, 1.37M chars). Six patterns were adopted with file- or page-level attribution; four
were deliberately not, with reasons. `PATTERN_ATTRIBUTION.md` is the full record.

<!-- zh -->
**改了什么（What changed）。** 两件事，按此顺序。第一，我把评审方法反过来用在自己的代码上：一次针对 v0.3 的对抗性自审计发现了十一个漏洞，其中若干比四份外部评审所发现的任何问题都更严重，目前有九个已作为测试关闭。第二，用户要求尽可能充分地利用 Codex 与 Claude Code。Codex 是开源的，直接读其源码（165 个文件，2.3M 字符）；Claude Code 不开源，读的是其官方文档（20 页，1.37M 字符）。有六个模式被采纳，并附文件级或页级归属；有四个被有意不采纳，并给出理由。`PATTERN_ATTRIBUTION.md` 是完整记录。


**What did not change.** The positioning: a governance kernel, not an agent OS and not
clinical-safe. And the two-package structure: `sable` is untouched (139 offline tests still pass)
and composed through its adapter.

<!-- zh -->
**没有改什么（What did not change）。** 定位没变：一个治理内核（governance kernel），不是智能体操作系统（agent OS），也不是临床安全的。双包结构也没变：`sable` 未被改动（139 个离线测试仍然通过），并通过其适配器组合进来。


---

## 1. The self-audit · 1. 自审计

Eleven probes tripped on v0.3 (`handoff/self_audit_v03_baseline.json`). Triage before fixing
mattered, because they were not one class of defect: some were real bugs with in-process fixes,
one was a data-model gap, and two were architectural — properties no in-process code can
provide.

<!-- zh -->
十一个探针在 v0.3 上触发（`handoff/self_audit_v03_baseline.json`）。修复之前先做分诊很重要，因为它们并不是同一类缺陷：一些是真正的 bug，可以在进程内修复；一个是数据模型缺口；还有两个是架构性的 —— 那种进程内代码无法提供的性质。


| Probe | Severity | v0.4 |
|---|---|---|
| `A1 direct_provider_call_bypasses_broker` | critical | **open — architectural** |
| `B_base64_evades` | medium | closed |
| `B_rot13_evades` | medium | closed |
| `B_hex_evades` | medium | closed |
| `B_split_across_fields_evades` | medium | closed |
| `C2_label_setattr_bypass` | high | **open — architectural** |
| `D1_anyone_can_declassify` | high | closed (re-probed on the real property) |
| `F1_budget_state_public` | low | closed (re-probed on the real property) |
| `G1_chain_breaks_under_concurrency` | high | closed |
| `J1_trust_is_a_string_claim` | high | closed |
| `K1_unknown_condition_extrapolation_passes` | medium | closed |

<!-- zh -->
| 探针 | 严重度 | v0.4 |
|---|---|---|
| `A1 direct_provider_call_bypasses_broker` | 严重 | **open — architectural（仍然开放 —— 架构性）** |
| `B_base64_evades` | 中 | 已关闭 |
| `B_rot13_evades` | 中 | 已关闭 |
| `B_hex_evades` | 中 | 已关闭 |
| `B_split_across_fields_evades` | 中 | 已关闭 |
| `C2_label_setattr_bypass` | 高 | **open — architectural（仍然开放 —— 架构性）** |
| `D1_anyone_can_declassify` | 高 | 已关闭（针对真实性质重新探测） |
| `F1_budget_state_public` | 低 | 已关闭（针对真实性质重新探测） |
| `G1_chain_breaks_under_concurrency` | 高 | 已关闭 |
| `J1_trust_is_a_string_claim` | 高 | 已关闭 |
| `K1_unknown_condition_extrapolation_passes` | 中 | 已关闭 |


Two probes (`D1`, `F1`) still trip their *original* criteria after the fix, because those
criteria measured a proxy for the defect — whether a `Declassification` dataclass can be
constructed, whether an attribute named `state` exists. Re-probed on the property that matters:
a forged declassification is re-classified to PHI at ingress and counted, an unlisted principal is
refused, the budget snapshot is frozen and no live counter is reachable by a public name. I did
not edit the audit script to make it pass; the re-probe is recorded alongside the original.

<!-- zh -->
两个探针（`D1`、`F1`）在修复之后仍然触及其*原始*判据，因为那些判据度量的是缺陷的代理指标 —— 一个 `Declassification` 数据类能否被构造出来、是否存在名为 `state` 的属性。针对真正重要的性质重新探测：一份伪造的去分类（declassification）在入口处被重新分类为 PHI 并被计数；未列入名单的主体（principal）被拒绝；预算快照被冻结，且没有任何活跃计数器可通过公开名字触达。我没有为了让审计脚本通过而去改动它；重新探测的结果与原始结果一并记录。


**The worst finding was `G1`.** The event store read the chain head and then inserted, with no
lock and autocommit. Six concurrent writers lost five of every six records to `UNIQUE(seq)`
violations. An audit log that drops entries under load is not an audit log. Fixed with a
process-level lock around the read-insert pair *and* `BEGIN IMMEDIATE` with a busy timeout, so a
second process serialises at the database rather than racing the SELECT. Verified: 6 threads × 30
appends and 5 processes × 40 appends, zero lost, chain intact; 11,166 appends/s.

<!-- zh -->
**最严重的发现是 `G1`。** 事件存储先读链头、然后插入，期间没有加锁，也没有显式事务（autocommit）。六个并发写入者因 `UNIQUE(seq)` 冲突丢失了每六条记录中的五条。一份在负载下会丢条目的事件日志不是审计日志。修复方式是在"读取-插入"这一对操作外面加进程级锁，*并且* 使用带忙等待超时的 `BEGIN IMMEDIATE`，使得第二个进程在数据库处串行化，而不是与 SELECT 竞争。已验证：6 线程 × 30 次追加，以及 5 进程 × 40 次追加，零丢失，链完整；11,166 次追加/秒。


**The most instructive was `D1`.** While wiring the gated declassification path I found a
pre-existing `TrustedKernel.declassify` that recorded who did it and checked nothing — any
principal string lowered any label — and it shadowed the new method. Same duplicate-definition
trap that bit the v0.2 verifier. Removed; the policy now names permitted declassifiers (empty by
default) and a floor, and ingress honours a lowered label only when every declassification id on
it was issued by this kernel.

<!-- zh -->
**最有教益的是 `D1`。** 在接经受门控的去分类路径时，我发现了一个先前就已存在的 `TrustedKernel.declassify`：它记录了是谁做的，却不做任何检查 —— 任意主体字符串都能降低任意标签 —— 而且它遮蔽了新方法。这与当初坑了 v0.2 校验器的重复定义陷阱是同一个。已移除；策略现在指定了被允许的去分类者（默认为空）以及一个下限，并且入口只有在某个已被降低的标签上的每一个去分类 id 都是由本内核签发时，才承认该标签。


**`J1` reframed evidence trust.** `trusted = retrieved_by in TRUSTED_RETRIEVERS` was a string
comparison anyone could satisfy by typing the name. Trust now derives from an HMAC signature under
a kernel-held key; `from_text()` defaults `trusted=False` regardless of the retriever name; the
runner recomputes trust from the signature on every incoming record because a frozen dataclass
copied with `replace()` keeps the stale flag. Six existing tests had been getting trust for free
by naming the retriever. They now sign, which is what the real path does.

<!-- zh -->
**`J1` 重新定义了证据信任。** `trusted = retrieved_by in TRUSTED_RETRIEVERS` 是一种字符串比较，任何人只要敲上那个名字就能满足。信任现在来自内核持有密钥下的 HMAC 签名；`from_text()` 无论 retriever 名字为何都默认 `trusted=False`；运行器对每一条传入记录都从签名重算信任，因为用 `replace()` 复制出来的冻结数据类会保留那个陈旧的标志位。既有的六个测试此前只要写出 retriever 名字就能白拿信任。它们现在要签名，而真实路径做的正是签名。


## 2. The two that stay open · 2. 仍然开放的两项

`A1` (in-process code calls a provider; the broker sees nothing) and `C2`
(`object.__setattr__` defeats a frozen dataclass) have no in-process fix. Both surveyed systems
answer them the same way — the operating system enforces the boundary — and v0.4 adopts the
portable part of that answer (section 3.5–3.6): tool execution moves to a subprocess with a
default-deny environment behind a kernel-owned egress proxy. That raises the **tool** boundary
from cooperation to process level. It does not confine code running inside the kernel process
itself, and `test_in_process_bypass_is_a_documented_limit_not_a_closure` asserts the limit exists
so nobody mistakes it for closed.

<!-- zh -->
`A1`（进程内代码直接调用提供方；broker 什么都看不到）与 `C2`（`object.__setattr__` 击败冻结数据类）没有进程内的修复办法。被考察的两个系统用同一种方式回答它们 —— 由操作系统来强制这条边界 —— v0.4 采纳了该答案中可移植的那部分（section 3.5–3.6）：工具执行移入一个子进程（subprocess），其环境为默认拒绝（default-deny），并且位于内核拥有的出网代理（egress proxy）之后。这把 **工具** 边界从依赖配合提升到进程级。它并不约束运行在内核进程自身内部的代码，并且 `test_in_process_bypass_is_a_documented_limit_not_a_closure` 断言该限制确实存在，以免有人把它误当成已关闭。


## 3. Patterns adopted · 3. 已采纳的模式

Full attribution with file paths and page names is in `PATTERN_ATTRIBUTION.md`. Summary:

<!-- zh -->
完整的归属说明（含文件路径与页面名称）在 `PATTERN_ATTRIBUTION.md` 中。摘要如下：


| # | Pattern | From | Landed in | Measured |
|---|---|---|---|---|
| 1 | Declarative execution policy with self-testing rules | Codex `execpolicy` | `kernel/execpolicy.py` | evaluate 0.0084 ms; load + self-test all 12 rules 0.062 ms |
| 2 | Approval writes back a rule | Codex `ReviewDecision::ApprovedExecpolicyAmendment` | `kernel/approvals.py` | — |
| 3 | `forbidden` absolute, first match, specificity irrelevant | Claude Code `permissions` | `execpolicy.py` | — |
| 4 | Typed hook lifecycle, one JSON protocol | Claude Code `hooks`; same shape in Codex `hooks/` | `kernel/hooks.py` | 5 hooks + 5 audit events 0.3869 ms |
| 5 | Default-deny environment | Codex `exec_env.rs` `env_clear()` | `kernel/isolation.py` | env build 0.0022 ms |
| 6 | Egress proxy as network boundary; private ranges refused even when allowlisted | Codex `network-proxy` | `kernel/isolation.py` | host decision 0.0342 ms; spawn overhead +1.47 ms |

<!-- zh -->
| # | 模式 | 来源 | 落地位置 | 实测 |
|---|---|---|---|---|
| 1 | 声明式执行策略，规则自测试 | Codex `execpolicy` | `kernel/execpolicy.py` | evaluate 0.0084 ms；加载 + 自测试全部 12 条规则 0.062 ms |
| 2 | 审批回写一条规则 | Codex `ReviewDecision::ApprovedExecpolicyAmendment` | `kernel/approvals.py` | — |
| 3 | `forbidden` 绝对优先，首个匹配生效，具体程度无关 | Claude Code `permissions` | `execpolicy.py` | — |
| 4 | 带类型的 hook 生命周期，单一 JSON 协议 | Claude Code `hooks`；Codex `hooks/` 中形状相同 | `kernel/hooks.py` | 5 个 hook + 5 个审计事件 0.3869 ms |
| 5 | 默认拒绝（default-deny）环境 | Codex `exec_env.rs` `env_clear()` | `kernel/isolation.py` | env 构建 0.0022 ms |
| 6 | 出网代理（egress proxy）作为网络边界；私有网段即使被列入允许名单也被拒绝 | Codex `network-proxy` | `kernel/isolation.py` | 主机判定 0.0342 ms；spawn 开销 +1.47 ms |


**One recorded departure.** Claude Code evaluates deny → ask → allow with first match winning and
specificity irrelevant. psh keeps that for `forbidden` — nothing relaxes a forbidden rule — but
between `prompt` and `allow` the most specific rule wins (ties to the later rule). Under strict
ask-before-allow, an approval amendment could never stop a question: "ask before `git push`" plus
"allow `git push origin` from now on" would still ask forever. Codex's
`ApprovedExecpolicyAmendment` presupposes a prompted command *becomes* allowed, so where the two
sources disagree this follows Codex. The property that matters is unchanged and tested: a
narrower `allow` never reaches a command a `forbidden` rule refuses.

<!-- zh -->
**一处有记录的偏离。** Claude Code 按 deny → ask → allow 求值，首个匹配胜出，具体程度无关。psh 对 `forbidden` 保留这一做法 —— 任何东西都不能放宽一条 forbidden 规则 —— 但在 `prompt` 与 `allow` 之间，由更具体的规则胜出（打平时取靠后的规则）。在严格的先询问后允许（ask-before-allow）之下，一条审批修正案永远无法终止一次询问："在 `git push` 前询问"加上"从今往后允许 `git push origin`"仍然会永远询问下去。Codex 的 `ApprovedExecpolicyAmendment` 预设了一条被提示的命令 *会变成* 被允许，因此在两个来源不一致之处，这里遵循 Codex。真正重要的性质没有改变并且有测试覆盖：一条更窄的 `allow` 永远无法触达被 `forbidden` 规则拒绝的命令。


**Convergence worth noting.** Codex's own hook implementation consumes Claude Code's exact JSON
shape — `hook_event_name`, `hookSpecificOutput`, `permissionDecision`, and Codex's
`decision.behavior` variant. Two harnesses converging on one protocol is the strongest available
reason to adopt it rather than invent a third; psh's parser accepts both variants.

<!-- zh -->
**值得注意的收敛。** Codex 自己的 hook 实现消费的正是 Claude Code 的那套 JSON 形状 —— `hook_event_name`、`hookSpecificOutput`、`permissionDecision`，以及 Codex 的 `decision.behavior` 变体。两套 harness 收敛到同一个协议，是采纳它、而不是另造第三套的最有力理由；psh 的解析器同时接受两种变体。


## 4. Patterns deliberately not adopted · 4. 有意不采纳的模式

| Pattern | Why not |
|---|---|
| Classifier-based auto mode (a model judges whether an action is safe) | A second model call on the critical path that needs its own egress decision. psh's position is that policy decisions must not themselves require an egress. |
| SOCKS5 + HTTPS MITM in the proxy | MITM means the kernel holds a CA key and terminates TLS for every tool — a large trust concentration for a single-user harness. CONNECT-level host policy covers the threat model. |
| Starlark policy language | Rules are dataclasses / JSON. A second language inside a small kernel is cost without benefit at this scale. |
| OS sandbox (Seatbelt / bubblewrap / seccomp) | Cannot be installed or verified from inside this session. `SandboxBackend` is an adapter; `NoSandbox.describe()` states that raw sockets are not confined. |

<!-- zh -->
| 模式 | 不采纳的理由 |
|---|---|
| 基于分类器的自动模式（由一个模型判断某个动作是否安全） | 在关键路径上多出一次模型调用，而它自己还需要一个出网决策。psh 的立场是：策略决策本身不得要求出网。 |
| 代理中的 SOCKS5 + HTTPS MITM | MITM 意味着内核持有一把 CA 密钥并为每一个工具终结 TLS —— 对单用户 harness 而言这是极大的信任集中。CONNECT 层级的主机策略已覆盖该威胁模型。 |
| Starlark 策略语言 | 规则就是数据类 / JSON。在一个小内核里再塞一门语言，在这个规模上只有成本没有收益。 |
| 操作系统沙箱（Seatbelt / bubblewrap / seccomp） | 无法从本次会话内部安装或验证。`SandboxBackend` 是一个适配器；`NoSandbox.describe()` 明确说明原始套接字（raw sockets）未被约束。 |


## 5. What the environment would not let me verify · 5. 环境不允许我验证的内容

This sandbox forbids **all** listening sockets — TCP loopback and Unix domain alike. The egress
proxy therefore could not bind here. Three consequences, each handled rather than hidden:

<!-- zh -->
这个沙箱禁止 **一切** 监听套接字 —— TCP 环回与 Unix 域套接字一视同仁。因此出网代理（egress proxy）在这里无法绑定。三个后果，每一个都是被处理而不是被藏起来：


1. The runner **fails closed**: when a run wants network and no proxy can be bound, it raises
   `ProxyUnavailable` rather than running unproxied. Running unproxied would be the v0.3 bypass
   with extra steps. When no network is wanted, the child is pointed at a dead loopback port.
2. The proxy's request handler is tested over in-memory socketpairs with a threaded upstream:
   allow, refuse with reason, CONNECT refuse, `Proxy-Authorization` stripped before forwarding,
   every decision recorded. That driver found a real bug — the handler honoured HTTP keep-alive
   and blocked 15 s after every forwarded exchange. It now closes after each.
3. The two full child → proxy → upstream TCP tests are marked `needs_listener` and **skip here
   with a stated reason**. They run on an ordinary workstation. The spawn-overhead figure above
   therefore excludes proxy start-up, and `v4_benchmark_results.json` says so.

<!-- zh -->
1. 运行器**失败即关闭（fail closed）**：当一次运行需要网络、而又无法绑定任何代理时，它抛出 `ProxyUnavailable`，而不是在无代理的情况下运行。无代理运行就等于 v0.3 的绕过加上几个额外步骤。当不需要网络时，子进程被指向一个死掉的环回端口。
2. 代理的请求处理器通过内存中的 socketpair 加一个多线程上游来测试：允许、带理由拒绝、CONNECT 拒绝、转发前剥离 `Proxy-Authorization`、每一次决策都被记录。这个驱动发现了一个真实的 bug —— 处理器遵循了 HTTP keep-alive，在每一次转发交换之后阻塞 15 秒。现在它在每次交换后关闭连接。
3. 两个完整的 子进程 → 代理 → 上游 TCP 测试被标记为 `needs_listener`，并且**在这里带明确理由跳过**。它们会在普通工作站上运行。因此上面的 spawn 开销数字不包含代理启动，`v4_benchmark_results.json` 也是这样说明的。


## 6. Bugs the process caught this round · 6. 本轮流程捕获的缺陷

1. Event chain lost records under concurrency (audit G1).
2. Ungated `declassify` shadowing the gated one — the audit finding, in plain sight.
3. Evidence trust by string comparison (J1), and six tests that depended on it.
4. `is_clinical_claim` exempted any sentence whose outcome was not in the vocabulary, so the
   population conflict `check_scope` correctly detected for a cystic-fibrosis claim against a
   sickle-cell trial was never consulted. An unrecognised outcome now widens the checks, not
   exempts the sentence.
5. Execution policy applied to every tool with an `args` key, prompting on a local summariser.
   Now scoped to command-executing components.
6. `urlsafe_b64decode` does not take `validate=`; the decode layer silently never ran for one
   alphabet.
7. Proxy handler keep-alive stall (above).
8. Under strict deny→ask→allow, amendments could never relax a prompt — a design conflict between
   the two sources, resolved and recorded (section 3).

<!-- zh -->
1. 事件链在并发下丢失记录（审计 G1）。
2. 未受门控的 `declassify` 遮蔽了受门控的那个 —— 正是审计发现，就摆在明面上。
3. 用字符串比较来建立证据信任（J1），以及六个依赖它的测试。
4. `is_clinical_claim` 会豁免任何结局不在词表中的句子，因此 `check_scope` 针对"一项囊性纤维化（cystic-fibrosis）主张对一项镰状细胞（sickle-cell）试验"所正确检测到的群体冲突，从未被纳入考量。现在，未被识别的结局会扩大检查范围，而不是豁免该句子。
5. 执行策略被套用到每一个带 `args` 键的工具上，连一个本地摘要器（summariser）都会触发提示。现在已限定到执行命令的组件。
6. `urlsafe_b64decode` 不接受 `validate=`；解码层对某一个字母表静默地从未运行。
7. 代理处理器的 keep-alive 卡顿（见上）。
8. 在严格的 deny→ask→allow 之下，修正案永远无法放宽一次提示 —— 这是两个来源之间的设计冲突，已解决并记录（section 3）。


## 7. Measured · 7. 实测

| Mechanism | Figure |
|---|---|
| Classification, 600 chars, with decode-and-rescan | 0.3053 ms (text-only 0.141 ms; ×2.17) |
| Base64-encoded PHI detected | 0.1119 ms → PHI |
| Random 800-char blob | → SENSITIVE (uninspectable is not clean) |
| Execution policy evaluate / load+self-test | 0.0084 ms / 0.062 ms |
| Hook dispatch, 5 hooks | 0.3869 ms |
| Isolated spawn vs bare | 10.2715 ms vs 8.8001 ms (+1.47 ms, excl. proxy start) |
| Concurrent event appends | 400 → 400 records, 0 lost, 11,166/s, intact=True |
| Evidence sign / verify | 0.0038 / 0.0015 ms |

<!-- zh -->
| 机制 | 数字 |
|---|---|
| 分类，600 字符，含解码并重扫描 | 0.3053 ms（纯文本 0.141 ms；×2.17） |
| 被检测出的 Base64 编码 PHI | 0.1119 ms → PHI |
| 随机 800 字符 blob | → SENSITIVE（不可检视不等于干净） |
| 执行策略 evaluate / load+self-test | 0.0084 ms / 0.062 ms |
| Hook 分发，5 个 hook | 0.3869 ms |
| 隔离 spawn 对比裸 spawn | 10.2715 ms vs 8.8001 ms（+1.47 ms，不含代理启动） |
| 并发事件追加 | 400 → 400 条记录，0 丢失，11,166/秒，intact=True |
| 证据签名 / 验证 | 0.0038 / 0.0015 ms |


Suites: **170 psh tests pass, 2 skip (listener)**; sable 139 offline pass untouched. Run with
`PYTHONPATH=sable_pkg/src:psh_pkg/src`.

<!-- zh -->
测试套件：**170 个 psh 测试通过，2 个跳过（listener）**；sable 139 个离线测试全部通过且未被改动。运行方式：`PYTHONPATH=sable_pkg/src:psh_pkg/src`。


## 8. Boundaries, restated · 8. 边界，重申

- Tool execution is process-isolated; kernel-internal code is not. An OS sandbox is the next
  layer and the adapter for it exists.
- The proxy governs clients that honour proxy variables. A raw socket bypasses it.
- Classification decodes base64/hex/rot13 and recombines fragments within one payload. Fragments
  split across separate calls are not recombined; the coverage note says so.
- Signed evidence proves a record came through a registered capability. It does not stop a
  compromised capability from signing garbage — that is the correct boundary.
- Claude Code patterns are drawn from documentation, so they support claims about behaviour, not
  implementation.

<!-- zh -->
- 工具执行是进程隔离的；内核内部代码不是。操作系统沙箱是下一层，其适配器已经存在。
- 代理只能管束遵守代理变量的客户端。原始套接字（raw socket）可以绕过它。
- 分类会解码 base64/hex/rot13，并在单个载荷内重组碎片。跨不同调用拆分出去的碎片不会被重组；覆盖范围说明（coverage note）里写明了这一点。
- 已签名的证据证明某条记录是经由一个已注册能力（capability）来的。它不能阻止一个被攻陷的能力去签垃圾 —— 那正是正确的边界。
- Claude Code 的模式取自文档，因此它们只支撑关于行为的论断，不支撑关于实现的论断。


## 9. Next · 9. 下一步

Sandboxing remains the largest gap and now has a defined seam (`SandboxBackend.wrap`). After it:
the planner behind the model gateway, then semantic entailment on top of structured claims.

<!-- zh -->
沙箱化仍然是最主要的缺口，并且现在有了一条已定义的接缝（`SandboxBackend.wrap`）。在它之后：模型网关（model gateway）背后的规划器，然后是叠加在结构化主张之上的语义蕴含（semantic entailment）。
