# psh — Physician-Scientist Harness · 医师科学家执行框架（Physician-Scientist Harness）

A governed control plane for physician-scientist work. Not a bigger agent: the layer your
agents, tools and harnesses run *through*.

<!-- zh -->
一个面向医师科学家工作的受治理控制平面（governed control plane）。它不是"更大的智能体"，而是你的智能体、工具与执行框架（harness）运行时所**穿过**的那一层。


Positioning, stated precisely, because the earlier "Kubernetes for AI agents" framing
promised hard isolation, distributed scheduling and multi-tenancy that this does not have:

<!-- zh -->
这里精确陈述定位，因为早先"AI 智能体的 Kubernetes"这一说法承诺了硬隔离（hard isolation）、分布式调度与多租户，而本系统并不具备这些能力：


> **A policy-enforced control plane for biomedical scientific agents.** Research prototype.

<!-- zh -->
> **面向生物医学科学智能体的策略强制控制平面（policy-enforced control plane）。** 研究原型。


## The design vocabulary learned to name a simulation · 设计词汇学会了为一个模拟命名

> **A change to the kernel's own vocabulary, made for a downstream skill and worth
> understanding on its own.** The clearest example so far of a downstream
> requirement forcing a kernel distinction that should have existed already.

<!-- zh -->
> **这是对内核自身词汇的一次改动，因一个下游技能（skill）而做，本身值得单独理解。** 它也是迄今最清楚的一个例子：一个下游需求迫使内核补上了一条本就应当存在的区分。


`workflow.ir` previously had one flat `DESIGNS` set, and it had no way to name a
*computational* result. Compiling TCMScience's network-pharmacology skill exposed
the consequence: a docking score or a predicted target relationship had no design
to declare. The nearest available value was `in_vitro` — so a simulation would
have been recorded in the IR as a bench experiment. That is precisely the confusion
the IR exists to prevent, and the IR could not express the distinction needed to
prevent it.

<!-- zh -->
`workflow.ir` 原先只有一个扁平的 `DESIGNS` 集合，无法为"计算性"的结果命名。编译 TCMScience 的网络药理学（network pharmacology, **NP**）技能时，后果暴露了出来：一个分子对接（docking）分数或一条预测的靶点关系，没有任何设计（design）可以声明。最接近的可用取值是 `in_vitro` —— 于是一次模拟（simulation）会被 IR 记录成一次台架实验。而这恰恰是 IR 存在就是为了防止的混淆，可 IR 却无法表达防止这种混淆所需的那条区分。


`DESIGNS` is now two named sets:

<!-- zh -->
`DESIGNS` 现在是两个具名集合：


```python
EVIDENCE_DESIGNS   = {classical_text, expert_consensus, in_vitro, animal,
                      case_report, observational, randomized_trial, systematic_review}
PREDICTIVE_DESIGNS = {in_silico, network_prediction, docking, molecular_dynamics,
                      target_prediction, pathway_enrichment}
DESIGNS            = EVIDENCE_DESIGNS | PREDICTIVE_DESIGNS
```

The split carries weight in `_SUPPORTS`, the claim-support table. Predictive
designs appear in **exactly one row**:

<!-- zh -->
这一拆分在 `_SUPPORTS`（主张支撑表，claim-support table）中具有实际约束力。预测性设计只出现在**恰好一行**中：


```python
ClaimType.MECHANISTIC: EVIDENCE_DESIGNS - {classical_text, expert_consensus} | PREDICTIVE_DESIGNS
```

A prediction can therefore license a mechanism claim — the entire output of
network pharmacology, and refusing it would make the tool useless — and can never
license `CLINICAL`, `ASSOCIATION` or `SAFETY`. Those are statements about
patients, and a prediction is not an observation of one. `CLINICAL` still accepts
only `randomized_trial`, so there is no row where a prediction and a clinical
claim meet.

<!-- zh -->
因此，一次预测可以为机制类主张（mechanism claim）提供支撑 —— 这是网络药理学的全部产出，若拒绝它，工具就毫无用处 —— 但它永远不能支撑 `CLINICAL`、`ASSOCIATION` 或 `SAFETY`。那些是关于患者的陈述，而预测并不是对患者的观察。`CLINICAL` 仍然只接受 `randomized_trial`，所以不存在任何一行会让预测与临床主张相遇。


This is the kernel enforcing "predictions must not be presented as clinical
facts" through the type system rather than through a prompt instruction — the
difference between a rule the model *should* follow and one it *cannot* break.
958 tests pass unchanged, which is the evidence that widening the vocabulary did
not loosen anything.

<!-- zh -->
这是内核通过类型系统、而不是通过提示词指令来强制"预测不得被表述为临床事实" —— 也就是"模型**应该**遵守的规则"与"模型**无法**违反的规则"之间的区别。958 个测试原样通过，这正是"扩展词汇没有放松任何约束"的证据。


---

## v0.5.3 — the third review, closed · v0.5.3 —— 第三轮评审已关闭

A third external review (2026-09-18) probed the merged tree with synthetic data and found
that the governance closed around one `Runner` pass had not closed around the loop.
Twelve findings; `docs/REVIEW_RESPONSE_2026-09-18.md` takes each through reproduction,
fix and test. What changed, in one place:

<!-- zh -->
第三轮外部评审（2026-09-18）用合成数据探查了合并后的代码树，发现围绕单次 `Runner` 通过（pass）关闭的治理，并未围绕循环（loop）关闭。共十二项发现；`docs/REVIEW_RESPONSE_2026-09-18.md` 对每一项都走了一遍复现、修复与测试。变更集中列在这里：


* **One release path (F01).** `LoopResult` is internal. `Finalizer` takes a loop's
  deliverable through the same quarantine, claim verification, output gate and claim
  commit that `Runner.run` uses, and hands back a `ReleasedResult` whose only text field
  is `released_output`; a refusal carries counts and a category, never the sentence.
  `ResearchRunService` is the application-facing door. A plan-level
  `evidence_requirements` string no longer satisfies a claim-support policy; only a task
  that declares `evidence_required` does, because only that is checked.
* **Labels are inherited (F02).** The runner's quarantined output is labelled with the
  join of the projection, the broker's result and a fresh scan; a count derived from a
  PHI chart is PHI.
* **Checkpoints are durable writes (F03).** A run that may not persist writes the plan's
  shape and none of its words: objective, task text, payloads and error texts are
  withheld, the record says `redacted=True`, and resuming it needs the objective and the
  plan supplied again with the same shape. The record carries the run's consumption.
* **Chinese PHI cues (F04).** The fallback classifier recognises 住院号 / 病案号, 身份证号,
  手机号, 出生日期, 住址 and 患者姓名 forms; a clinical origin floors the label at PHI
  whatever the language; `require_validated_classifier` refuses to start on the fallback.
* **Input bindings (F05).** A step fills an argument from an upstream result by JSON
  pointer, typed and labelled; a payload literal that reads like a reference is a plan
  error that names the binding it should have been.
* **The budget tree (F06).** Consumption is charged to the run and every ancestor; a call
  is refused at any level's ceiling; the loop's bounds check reserves nothing.
* **Strict evaluation (F07).** The schema check refuses a boolean as an integer and an
  unknown keyword; a manual criterion nobody judged is `pending_manual`, not verified,
  and a claim-support policy refuses an unverified goal before the gate. A modal no
  longer becomes a claim's subject.
* **Isolation as a report (F08).** `IsolationReport` states what the runner confines;
  `require_os_isolation` is a lattice requirement the kernel refuses when it cannot meet.
* **An operation ledger (F09).** Every tool call is recorded durably by its idempotency
  key; a side-effecting component whose earlier attempt is in doubt is not re-run. The
  bridge hands the key to BioScience's runtime and never to the entrypoint or the wire.
* **Bilingual retrieval (F10).** Capability resolution and memory recall tokenise
  Chinese through a checked-in lexicon with bigram fallback.
* **Outcomes and shortfalls (F12).** Tool outcomes feed the registry's prior; a degraded
  result carries its caveat to the release as a limitation; a timeout is `ToolTimeout`.

<!-- zh -->
* **单一发布路径（F01）。** `LoopResult` 是内部类型。`Finalizer` 让一次循环的交付物经过与 `Runner.run` 相同的隔离区（quarantine）、主张校验、输出门（output gate）与主张提交，并交回一个 `ReleasedResult`，其唯一的文本字段是 `released_output`；拒绝时只携带计数与一个类别，绝不携带原句。`ResearchRunService` 是面向应用的门面。计划层级的 `evidence_requirements` 字符串不再能满足主张支撑（claim support）策略；只有一个声明了 `evidence_required` 的任务才可以，因为只有它会被检查。
* **标签会被继承（F02）。** 运行器（runner）被隔离的输出所带标签，是投影（projection）、broker 的结果与一次新扫描三者的并（join）；从一个 PHI 图表推导出的计数就是 PHI。
* **检查点是持久化写入（F03）。** 一次不得持久化的运行，只写入计划的形状而不写入其中任何文字：目标（objective）、任务文本、载荷（payload）与错误文本都被扣留，记录标注 `redacted=True`，恢复它需要重新提供目标与计划且形状一致。记录中携带该运行的资源消耗。
* **中文 PHI 线索（F04）。** 回退分类器可识别 住院号 / 病案号、身份证号、手机号、出生日期、住址 与 患者姓名 等形式；无论语言为何，只要来源是临床，标签下限即被抬升到 PHI；`require_validated_classifier` 在回退分类器上拒绝启动。
* **输入绑定（F05）。** 一个步骤通过 JSON 指针从上游结果填充某个参数，带类型、带标签；一个读起来像引用的载荷字面量属于计划错误，并会指出它本应使用的那个绑定。
* **预算树（F06）。** 消耗被计入该运行及其每一个祖先；在任一层级的上限处调用都会被拒绝；循环的边界检查不预留任何额度。
* **严格求值（F07）。** 模式检查拒绝把布尔值当作整数，也拒绝未知关键字；一条没有人评判的手工准则状态为 `pending_manual`，而不是已验证，并且主张支撑策略会在门之前拒绝一个未验证的目标。情态词不再成为主张的主语。
* **隔离作为一份报告（F08）。** `IsolationReport` 陈述运行器限制了哪些东西；`require_os_isolation` 是一项权限格（AuthorityLattice）要求，内核在无法满足时予以拒绝。
* **操作账本（F09）。** 每一次工具调用都按其幂等键（idempotency key）被持久记录；一个有副作用、且其先前尝试处于存疑状态的组件不会被重新运行。桥接层把该键交给 BioScience 的运行时，而绝不交给入口点或线上传输。
* **双语检索（F10）。** 能力解析与记忆召回通过一份检入仓库的词表对中文分词，并以二元组（bigram）回退。
* **结果与缺口（F12）。** 工具结果反馈为注册表的先验；一个降级的结果把它的保留意见作为限制项带到发布；超时是 `ToolTimeout`。


```bash
python -m pytest tests/ -q          # 636 pass
```

## v0.5.1 — gate composition closure · v0.5.1 —— 门的组合闭合

A second review accepted the v0.5 premise and attacked the next layer: what happens where
two controls meet. Seven of its findings were real, two of them P0. They share a shape, and
it is not the v0.5 shape:

<!-- zh -->
第二轮评审接受了 v0.5 的前提，并攻击了下一层：当两个控制相遇时会发生什么。它的发现中有七项是真实的，其中两项为 P0。它们共享同一种形状，而这并不是 v0.5 的形状：


> v0.5 — *the mechanism exists and the main path does not call it.*
>
> v0.5.1 — **the mechanism exists, is called, and a second, shorter copy of it was
> hand-written somewhere else.**

<!-- zh -->
> v0.5 —— *机制存在，而主路径不调用它。*
>
> v0.5.1 —— **机制存在，也被调用了，但在别处又手写了一份更短的副本。**


| Closed | What was wrong |
| --- | --- |
| **One policy ceiling** (P0) | `Runner.run(policy=…)` minted the run envelope from whatever snapshot the caller passed, never compared to `TrustedKernel.policy`. A kernel forbidding public providers executed a run that reached one and released the output. A per-run policy is now refused unless the kernel's contains it (`clamp_policy=True` meets instead), by a new `PolicyLattice`. |
| **All destinations, not `[0]`** (P0) | `ToolGateway` gated on `manifest.destinations[0]`, so `PHI` + `(LOCAL_COMPUTE, PUBLIC_REMOTE)` was allowed — while `IsolatedExecutor` read the *same* tuple with `any(…)`, saw `PUBLIC_REMOTE` and opened the component's network reach. Every destination is checked; the decision names the most exposed one; approval records list all of them. |
| **Delegation uses the lattice** | `DelegationGateway` hand-checked four of the lattice's fourteen dimensions, so an `R1`/`SUGGEST` parent could delegate `R4_KERNEL`/`ACT`/unrestricted/999× budget and get `allowed=True`. It calls `AuthorityLattice.violations` now. |
| **Policy narrowing is a lattice** | `with_()` was documented as refusing any widening and compared three of twelve dimensions. `SUGGEST→ACT`, `require_isolated_tools True→False` and `tokens_hard 10→999` were all accepted. |
| **The sandbox contains the workdir** | `manifest.id` was validated only for being non-empty and was joined onto the sandbox root, so `id="../../escaped"` put the child's cwd outside it and an absolute id discarded the root. Ids are one path component, and containment is re-checked after resolution. |
| **Timeout kills the process group** | `start_new_session=True` created a group that nothing ever signalled: a grandchild outlived the timeout by seconds and went on writing files. `killpg` is sent now. |
| **Security walkers fail closed** | Past depth 8 the path walker returned `[]`, so nesting a payload deep enough made the filesystem boundary disappear. A walk that hit its cap now refuses, as `labels.walk_values` already did. |
| **Split brain closed** | The run envelope came from one policy while `OutputGate`, `broker.require_isolation` and `PersistenceGateway` came from another. `require_isolated_tools` travels on the envelope; the gate and the store take per-call ceilings that can only tighten. |

<!-- zh -->
| 已关闭 | 问题所在 |
| --- | --- |
| **单一策略上限**（P0） | `Runner.run(policy=…)` 用调用者传入的任何快照铸造运行信封（envelope），从不与 `TrustedKernel.policy` 比较。一个禁止公共提供方的内核，执行了一次触及公共提供方的运行并发布了输出。现在，除非内核的策略包含它，每次运行的策略都会被拒绝（`clamp_policy=True` 则改为取两者的交，meet），由新的 `PolicyLattice` 实现。 |
| **所有目的地，而非 `[0]`**（P0） | `ToolGateway` 只对 `manifest.destinations[0]` 设卡，所以 `PHI` + `(LOCAL_COMPUTE, PUBLIC_REMOTE)` 被放行 —— 而 `IsolatedExecutor` 用 `any(…)` 读取**同一个**元组，看到 `PUBLIC_REMOTE` 并打开了组件的网络可达范围。现在每一个目的地都会被检查；决策会指出暴露程度最高的那一个；审批记录会列出全部目的地。 |
| **委派使用权限格** | `DelegationGateway` 手工检查了权限格十四个维度中的四个，于是一个 `R1`/`SUGGEST` 的父级可以委派 `R4_KERNEL`/`ACT`/不受限/999 倍预算并得到 `allowed=True`。现在它调用 `AuthorityLattice.violations`。 |
| **策略收窄是一个格** | `with_()` 被文档描述为拒绝任何放宽，却只比较了十二个维度中的三个。`SUGGEST→ACT`、`require_isolated_tools True→False` 与 `tokens_hard 10→999` 都被接受了。 |
| **沙箱包含工作目录** | `manifest.id` 只被校验为非空，并被拼接到沙箱根目录上，因此 `id="../../escaped"` 会把子进程的 cwd 放到沙箱之外，而绝对路径 id 会丢弃根目录。现在 id 只能是单个路径分量，并且在解析之后会重新检查包含关系。 |
| **超时杀死进程组** | `start_new_session=True` 创建了一个进程组，却没有任何东西向它发过信号：一个孙进程在超时之后多活了数秒并继续写文件。现在会发送 `killpg`。 |
| **安全遍历器失败即关闭** | 深度超过 8 之后，路径遍历器返回 `[]`，因此只要把载荷嵌套得足够深，文件系统边界就消失了。现在命中上限的遍历会拒绝，正如 `labels.walk_values` 早已做的那样。 |
| **脑裂已关闭** | 运行信封来自一个策略，而 `OutputGate`、`broker.require_isolation` 与 `PersistenceGateway` 来自另一个。`require_isolated_tools` 现在随信封传递；门与存储接受只能收紧的每次调用上限。 |


Also: `requires_network` validation was a chained comparison that could never be true;
`StuckLoop` counted every run of a `Runner` in one bucket and refused three identical
top-level requests; `min_autonomy` was declared and never read (and defaulted to the
strongest requirement); an isolated component breaking the stdout JSON protocol was
silently accepted. See `docs/V5_1_GATE_COMPOSITION_CLOSURE.md`.

<!-- zh -->
另外：`requires_network` 校验是一个永远不可能为真的链式比较；`StuckLoop` 把一个 `Runner` 的所有运行都计入同一个桶，并拒绝了三个完全相同的顶层请求；`min_autonomy` 被声明却从未被读取（且默认取最强要求）；一个破坏 stdout JSON 协议的隔离组件被静默接受。见 `docs/V5_1_GATE_COMPOSITION_CLOSURE.md`。


The delegation property test that should have caught the lattice bypass was **vacuous**:
its child widened every dimension at once and `tokens_hard = 10**9` always exceeded the
parent strategy's `100_000`, so the first budget comparison refused every example and no
other dimension was ever the reason for a verdict. It is now one dimension at a time, with
a test asserting the properties actually reach their assertions — which immediately found a
dimension with zero coverage.

<!-- zh -->
那个本应抓住权限格绕过（lattice bypass）的委派属性测试是**空洞的**：它的子级一次性放宽了每一个维度，而 `tokens_hard = 10**9` 总是超过父策略的 `100_000`，于是第一次预算比较就拒绝了每一个样本，其他维度从未成为判决的理由。现在每次只放宽一个维度，并有一个测试断言这些属性确实抵达了各自的断言 —— 该测试立刻发现了一个覆盖率为零的维度。


## v0.5.1 — a bounded agent loop, through the kernel · v0.5.1 —— 一个有界的智能体循环，穿过内核

The same release adds the component both reviews named as the most valuable next one, under
the rule they both stated: **the loop must not bypass the trusted kernel.**

<!-- zh -->
同一个发布还加入了两个评审都指认为最有价值的下一步组件，遵循它们共同陈述的规则：**循环不得绕过可信内核。**


```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            typed Plan / PlanTask / TestSpec / RetryPolicy
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph — transient, deliberately NOT the WorkGraph
├── evaluator.py       structural / execution / evidence / goal
└── loop.py            AgentLoopController — plan, act, observe, evaluate, bounded
```

`validate_plan` used to record `"placeholder planner: no typed plan to validate"`, which was
honest and was also why the stage could enforce nothing: there is no dimension of
`"summarise the HFpEF evidence"` to compare against a risk ceiling. A plan is typed now and
each task carries the authority it intends to use, so a plan whose fourth step needs a
destination the run forbids is refused *at the plan*, not discovered at step four with three
steps' side effects already committed.

<!-- zh -->
`validate_plan` 过去会记录 `"placeholder planner: no typed plan to validate"`，这很诚实，也正是该阶段无法强制任何东西的原因：`"summarise the HFpEF evidence"` 没有任何维度可以与风险上限相比较。现在计划是有类型的，每个任务携带它打算使用的权限，因此一个第四步需要该运行所禁止的目的地的计划，会**在计划处**被拒绝，而不是在第四步才被发现、此时前三个步骤的副作用已经提交。


The loop holds no provider, no subprocess and no socket. It acts through exactly three
broker calls, and that is checked three ways: the dispatch has three branches; a test
balances the broker's counters against what the loop did; and a test parses `loop.py` and
fails if it ever imports a transport or a process spawner — because a behavioural test sees
the paths a test took, not the paths that exist, which is exactly how `IsolatedRunner`
shipped in v0.4 while `call_tool` ran everything in process.

<!-- zh -->
循环不持有提供方（provider）、不持有子进程、不持有套接字。它只通过恰好三次 broker 调用行动，而这一点用三种方式被检查：分发只有三个分支；一个测试把 broker 的计数器与循环实际所做的事对账；还有一个测试解析 `loop.py`，一旦它导入了任何传输层或进程派生器就失败 —— 因为行为测试看到的是测试走过的路径，而不是实际存在的路径，这正是 v0.4 中 `IsolatedRunner` 已经发布、而 `call_tool` 却在进程内运行一切的原因。


Termination is a controller rather than a repeat counter: `goal_satisfied`, `max_iterations`,
`budget_exhausted`, `deadline`, `no_progress`, `max_replans`, `plan_rejected`,
`policy_denied`, `escalated`, `unrecoverable_error` — always named, never inferred. Retries
are per task and are charged to the same budget as the first attempt; a policy *refusal* is
never retried, because it is an answer rather than a fault.

<!-- zh -->
终止性由一个控制器而非重复计数器决定：`goal_satisfied`、`max_iterations`、`budget_exhausted`、`deadline`、`no_progress`、`max_replans`、`plan_rejected`、`policy_denied`、`escalated`、`unrecoverable_error` —— 总是被指名，从不被推断。重试是按任务的，并且计入与首次尝试相同的预算；策略**拒绝**永不重试，因为它是一个答案，而不是一个故障。


What this was **not**, when it landed: a multi-agent runtime. No supervisor, no worker
pool, no fan-out and no parallelism — `docs/ROADMAP_AGENT_RUNTIME.md` said so in a table
rather than in prose, and the sections below add each of them in turn. Gates first, then
iteration — a loop multiplies whatever the gates get wrong.

<!-- zh -->
它落地时**不是**什么：一个多智能体运行时。没有监督者（supervisor）、没有工作池、没有扇出、没有并行 —— `docs/ROADMAP_AGENT_RUNTIME.md` 用一张表而不是散文说明了这一点，而以下各节则依次把它们逐个加上。先有门，再谈迭代 —— 循环会放大门做错的任何事。


### Licence provenance — the first BioScience convergence step · 许可证来源 —— BioScience 融合的第一步

`psh/licensing.py` adds the one dimension where BioScience-Harness's policy model is
stronger than this one: whether a capability's licence permits the **way** it is
integrated. The same licence gives different answers —

<!-- zh -->
`psh/licensing.py` 增加了 BioScience-Harness 的策略模型比本系统更强的那一个维度：一份能力的许可证是否允许它被集成的**方式**。同一份许可证会给出不同的答案 ——

```
vendor      copy the upstream implementation in     -> redistribution
native      call an independent equivalent          -> no upstream code at all
federated   invoke upstream in its own process      -> use, not redistribution
```

— so unlicensed code may be invoked and may not be copied, which a single allow/deny per
licence cannot say. A fixed table states what is permissible in principle (the same shape
as `labels.DEFAULT_CEILINGS`), and a profile narrows which classes and modes it permits at
all; `coding` does not permit vendoring, because that is the mode that produces code the
group then distributes.

<!-- zh -->
—— 因此未授权的代码可以被调用，但不可以被复制，而这是"每份许可证一个允许/拒绝"所无法表达的。一张固定表陈述了原则上允许什么（与 `labels.DEFAULT_CEILINGS` 形状相同），而一个 profile 收窄它究竟允许哪些类别与模式；`coding` 不允许 vendoring（内嵌上游实现），因为那正是会产生"本组随后还要分发"的代码的模式。


It is also the test of whether the lattice work paid for itself. Adding a governed
dimension cost naming it in `AuthorityLattice`, `PolicyLattice` and the property test's
dimension list — `restrict`, `with_`, the meet, delegation and `ToolGateway` govern it
without being told.

<!-- zh -->
它同时也是对"权限格这项工作是否值回成本"的检验。增加一个受治理的维度，成本只是在 `AuthorityLattice`、`PolicyLattice` 与属性测试的维度列表中为它命名 —— `restrict`、`with_`、meet、委派与 `ToolGateway` 无需被告知就会治理它。


### A planner that writes the plan · 一个会写出计划的规划器

`psh/runtime/planner.py` closes the last placeholder. `ModelPlanner` asks a model for a
typed `Plan`, through `ExecutionBroker` like any other model call, parses it strictly, and
feeds each refusal back so the next attempt can correct — bounded, because re-asking a
model forever is a loop rather than a correction.

<!-- zh -->
`psh/runtime/planner.py` 关闭了最后一个占位符。`ModelPlanner` 向模型请求一个有类型的 `Plan`，经由 `ExecutionBroker`，与其他任何模型调用一样，严格解析它，并把每一次拒绝反馈回去，使下一次尝试能够纠正 —— 这是有界的，因为无休止地重问模型是一个循环，而不是一次纠正。


The property worth stating: **a plan is untrusted input.** Its output is not an answer to
be checked but a program to be run, so a model that proposes a task holding
`PUBLIC_REMOTE` under a local-only run does not get it — `PlanValidator` refuses the plan
and the refusal becomes the next prompt. If a model could widen a run by writing a wider
plan, every control here would be reachable by asking for it.

<!-- zh -->
值得言明的性质：**计划是不可信输入。** 它的输出不是一份待检查的答案，而是一个待运行的程序；因此，一个在仅本地运行中提出持有 `PUBLIC_REMOTE` 的任务的模型不会得到它 —— `PlanValidator` 拒绝该计划，而该拒绝成为下一次提示。如果模型可以通过写一份更宽的计划来放宽一次运行，那么这里的一切控制都可以靠"开口要求"来触达。


### Parallel branches, and schemas the planner can act on · 并行分支，以及规划器可以据以行动的模式（schema）

Two properties a frontier runtime has and the v0.5.1 loop did not. An iteration's ready
set is independent by construction — every dependency of each task has already
succeeded — so with `LoopLimits(max_parallel=n)` it runs on a bounded pool of threads.
Nothing about the governance changes: every branch is still one of the three broker
calls, the gates and the budget governor are locked (the counters that tests and audits
read now take a lock too), a refusal in one branch is that branch's failure and its
descendants' block, and a bound one branch hits — an approval nobody can grant, a budget
— surfaces as the loop's termination after the branches already in flight have recorded
their own outcome. Sequential stays the default, because it is the easier behaviour to
reason about.

<!-- zh -->
一个前沿运行时拥有、而 v0.5.1 的循环没有的两条性质。一次迭代的就绪集（ready set）在构造上就是相互独立的 —— 每个任务的每一条依赖都已经成功 —— 因此在 `LoopLimits(max_parallel=n)` 下，它运行在一个有界的线程池上。治理没有任何变化：每个分支仍然是那三次 broker 调用之一，门与预算调控器被加锁（测试与审计所读取的计数器现在也上锁），某个分支中的拒绝是该分支的失败，也是其后代的阻塞，而某个分支撞上的边界 —— 无人能授予的审批、预算 —— 会在已在飞行中的分支记录完各自的结果之后，作为循环的终止条件浮现出来。串行仍是默认，因为它的行为更容易推理。


And the registry's disclosure gained its second level. `manifest_items` shows enough to
*choose* a capability; `schema_items` shows enough to *call* one — a connector's
operations with their arguments and an example payload, a native tool's parameters, or
the property names of a declared JSON schema — for the few candidates that survived
ranking, from the manifest and never from an invocation. `ModelPlanner` puts them in the
planning prompt, whose rules now say what a `payload` is, so a model can write
`{"operation": "symbol", "symbol": "TP53"}` instead of guessing. Each item carries the
manifest's description label, because a schema that arrived from an MCP server or a
catalogue is text this kernel did not write. `tests/test_parallel_and_schemas.py`.

<!-- zh -->
而注册表的披露有了第二层。`manifest_items` 展示足以**选择**一项能力的信息；`schema_items` 展示足以**调用**一项能力的信息 —— 一个连接器（connector）的操作及其参数与一个示例载荷、一个原生工具的形参，或一个已声明 JSON schema 的属性名 —— 只针对排名后存活下来的少数候选，且来自清单（manifest），绝不来自一次调用。`ModelPlanner` 把它们放进规划提示中，该提示的规则现在会说明 `payload` 是什么，于是模型可以写出 `{"operation": "symbol", "symbol": "TP53"}`，而不是靠猜。每个条目都携带清单的描述标签，因为来自 MCP 服务器或目录的 schema 是本内核没有写过的文本。`tests/test_parallel_and_schemas.py`。


### Memory the run may recall, and delegation the planner is told about · 运行可以召回的记忆，以及被告知给规划器的委派

The WorkGraph was already project memory with a governed write side: every node passes
`PersistenceGateway`, a claim is committed only after the release gate has ruled, and a
refused claim survives as a hash and a reason. What was missing was a read side that kept
those properties. `psh/context/memory.py` is it. `MemoryRetriever` reads nodes whose
`validation_status` is `verified` or `system` — a candidate is not trusted memory unless
an operator says so, and a rejected claim is excluded whatever the caller asks — ranks
them lexically against the objective, and returns `ContextItem(kind="memory")` carrying
the label the gateway stored, so a memory of PHI is PHI whatever its text looks like. Two
things are withheld at retrieval rather than left to the compiler: memory above the run's
own ceiling, and memory the model's destination may not receive. The second matters for
the loop, which refuses to run a task whose *upstream result* the destination may not see;
memory is optional context, so it is withheld and the task runs with a quieter prompt,
and the trace says so. Retrieval is scoped to the envelope's project — no project, no
memory — and it is a read: the module holds no path to `add`, `update` or `commit_node`,
and a structural test keeps it that way. `ModelPlanner(memory=…)`,
`AgentLoopController(memory=…)` and `Runner(memory=…)` compile what it returns into
their projections, where the label joins like any other item's. `tests/test_memory.py`.

<!-- zh -->
WorkGraph 早已是带有受治理写入侧的"项目记忆"：每个节点都要经过 `PersistenceGateway`，主张只有在发布门裁定之后才被提交，被拒绝的主张以哈希与理由的形式留存。缺的是一个能保持这些性质的读取侧。`psh/context/memory.py` 就是它。`MemoryRetriever` 读取 `validation_status` 为 `verified` 或 `system` 的节点 —— 除非操作员明确认可，候选不是可信记忆；被拒绝的主张无论调用者如何要求都被排除 —— 按字面（lexically）对照目标排名，并返回携带网关所存标签的 `ContextItem(kind="memory")`，因此一段 PHI 的记忆无论其文本看起来如何都是 PHI。有两样东西在检索时就被扣留，而不是留给编译器：高于该运行自身上限的记忆，以及模型的目的地不得接收的记忆。第二点对循环很重要：循环会拒绝运行一个其**上游结果**不得被目的地看到的任务；记忆是可选的上下文，因此它被扣留，任务以更安静的提示运行，而轨迹会说明这一点。检索被限定在信封的 project 之内 —— 没有 project 就没有记忆 —— 并且它是一次读取：该模块不持有通往 `add`、`update` 或 `commit_node` 的路径，并有结构性测试保持如此。`ModelPlanner(memory=…)`、`AgentLoopController(memory=…)` 与 `Runner(memory=…)` 把它返回的内容编译进各自的投影，标签与其他任何条目一样参与并（join）。`tests/test_memory.py`。


The roadmap's last yellow cell was that a plan *could* carry `kind="delegate"` tasks while
the planner was never told whether the loop had a backend to run them; a model that
guessed wrong got a `ContractViolation` at dispatch after the rest of the plan had spent
budget. The loop now states the fact on `LoopState.can_delegate`, the authority brief
says either "available: up to N child agent(s)" or "not available in this loop", the
prompt's rules say what a delegated objective must be, and a delegate task without a
backend is a refusal the planner feeds back for correction rather than a crash.
`tests/test_delegation_disclosure.py`.

<!-- zh -->
路线图最后一个黄色格子是：计划**可以**携带 `kind="delegate"` 任务，而规划器从未被告知循环是否有后端来运行它们；猜错的模型会在分发时得到一个 `ContractViolation`，而计划的其余部分已经花掉了预算。现在循环在 `LoopState.can_delegate` 上陈述该事实，权限简报要么说 "available: up to N child agent(s)"，要么说 "not available in this loop"，提示的规则说明了被委派的目标必须是什么，而一个没有后端的 delegate 任务是一次拒绝、由规划器反馈回来以作纠正，而不是一次崩溃。`tests/test_delegation_disclosure.py`。


### Checkpoint and resume, with one rule · 检查点与恢复，只有一条规则

`Runner`'s `checkpoint` stage wrote an audit event, so "checkpoint" named a record of
having finished rather than a state a run could continue from. `psh/runtime/checkpoint.py`
is the real thing, and its design is one sentence:

> **A resumed run re-meets its authority against the policy in force *now*.**

<!-- zh -->
`Runner` 的 `checkpoint` 阶段只写了一条审计事件，因此 "checkpoint" 命名的是"已完成的记录"，而不是一次运行可以从中继续的状态。`psh/runtime/checkpoint.py` 是真正的实现，其设计只有一句话：

> **恢复后的运行，会将其权限与"此刻"生效的策略重新求交。**


Restoring the envelope a run held is the obvious implementation and it is a hole. An
envelope is a grant, and a grant that outlives the policy that issued it is a capability
the kernel never agreed to — that is P0-1 arriving through a file instead of a keyword
argument. So resuming computes `AuthorityLattice.meet(stored, current_ceiling)`: never
wider than it was, never wider than today's policy, with every narrowed dimension named in
an audit event. Unfinished tasks are re-authorised one at a time and the resume is refused
if the policy no longer admits one; finished tasks are history and are not re-checked.

<!-- zh -->
恢复该运行当初持有的信封，是显而易见的实现，而它是一个漏洞。信封是一份授予（grant），而一份比签发它的策略活得更久的授予，是内核从未同意过的一项能力 —— 那就是 P0-1 通过一个文件而非一个关键字参数到来。因此恢复时计算 `AuthorityLattice.meet(stored, current_ceiling)`：从不比原先更宽，从不比今天的策略更宽，并且每一个被收窄的维度都在一条审计事件中被指名。未完成的任务被逐个重新授权，若策略不再允许其中某一个，恢复即被拒绝；已完成的任务属于历史，不再重新检查。


### Compaction: what is left out is summarised and said · 压缩：被略去的部分会被摘要，并被说明

The compiler filled its token budget by rank and discarded the rest, reporting a count. A
count tells the *caller* something was omitted and tells the model — the party that has to
answer around the gap — nothing at all. `psh/context/compaction.py` summarises the overflow
instead and shadows it, DeepSeek Harness's shape.

<!-- zh -->
编译器按排名填满它的 token 预算，丢弃其余部分，并报告一个计数。计数告诉了**调用者**有东西被省略，却对模型 —— 那个必须围绕这个缺口作答的一方 —— 什么都没说。`psh/context/compaction.py` 改为对溢出部分做摘要并遮蔽（shadow）它，这是 DeepSeek Harness 的形状。


The property that made this a kernel concern rather than a utility:

> **A summary carries the join of the labels it summarises.**

<!-- zh -->
使这件事成为内核关切而非一个工具的，是这条性质：

> **摘要携带它所摘要内容之标签的并（join）。**


`DEFAULT_SYSTEM_PROMPT` already tells the model "a summary of identifiable content is still
identifiable". That has to hold by construction. Deriving the label from the summary *text*
would mean a summary of PHI whose extract happened to omit the identifiers classified as
`INTERNAL` — measurably, in `test_the_label_comes_from_the_inputs_not_the_summary_text` —
and became permitted at a destination its sources could never reach. Laundering by
accident. So the label comes from the inputs, and a summary that may not reach the
destination is replaced by a bare count, which carries no content.

<!-- zh -->
`DEFAULT_SYSTEM_PROMPT` 已经告诉模型 "a summary of identifiable content is still identifiable"。这一点必须按构造成立。若从摘要**文本**推导标签，那么一段 PHI 的摘要只要其摘录恰好省略了标识符，就会被分类为 `INTERNAL` —— 在 `test_the_label_comes_from_the_inputs_not_the_summary_text` 中可测量地如此 —— 并因此在其来源永远无法抵达的目的地上变成被允许的。这是意外的洗白（laundering）。所以标签来自输入，而一个不得抵达目的地的摘要会被一个裸计数替代，后者不携带任何内容。


### Multi-agent: a supervisor that can only narrow · 多智能体：一个只能收窄的监督者

`psh/runtime/supervisor.py` and `subagent.py` add fan-out under one rule:

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

<!-- zh -->
`psh/runtime/supervisor.py` 与 `subagent.py` 在一条规则之下加入扇出：

> **监督者决定*做什么*。它从不决定*什么被允许*。**


`Supervisor.mint()` turns a request into arguments for `parent.restrict()` — the authority
lattice — and holds no comparison of its own; a structural test parses it and fails if one
appears. Children are child loops under the contract's envelope and return a
`SubagentResult` with no field for a transcript: claims, evidence, artifacts, a summary, and
a label that is the join of everything the child saw. A `WorkerPool` runs them concurrently
over the kernel, which is now locked for it — `BudgetGovernor`'s check-and-increment and the
broker's counters, the latter being the proof nothing bypassed the broker and therefore the
thing that must survive threads. Fan-in is an `AggregatedObservation` in which two children
disagreeing is a recorded `Conflict`, not two paragraphs concatenated.

It found one defect worth naming: `Budget.child()` bounded one child and its docstring
claimed it stopped fan-out multiplying a budget. Ten quarter-children are two and a half
parents. `BudgetLedger` sums fractions per parent, cumulatively.

<!-- zh -->
`Supervisor.mint()` 把一个请求转成 `parent.restrict()` —— 权限格 —— 的参数，自身不持有任何比较；一个结构性测试会解析它，一旦出现比较就失败。子级是契约信封之下的子循环，返回一个 `SubagentResult`，其中没有存放对话记录的字段：主张、证据、产物、一份摘要，以及一个"子级所见一切之并"的标签。`WorkerPool` 在内核之上并发运行它们，内核现在为它加锁 —— `BudgetGovernor` 的检查并自增，以及 broker 的计数器；后者是"没有任何东西绕过 broker"的证明，因此也是必须能在多线程下存活的东西。扇入（fan-in）是一个 `AggregatedObservation`，其中两个子级意见不一致会被记录为一条 `Conflict`，而不是把两段话拼在一起。

它发现了一个值得指名的缺陷：`Budget.child()` 只约束了一个子级，而它的文档字符串声称它阻止了扇出对预算的倍增。十个四分之一子级等于两个半父级。`BudgetLedger` 按父级累计求和。


### Durability: a child that stops answering is given up on, not waited for · 持久性：一个不再应答的子级会被放弃，而不是被等待

Measured first: a child whose tool never returned left the parent's `wait()` blocked
forever, and nothing recorded that it was stuck. Every child now holds a `WorkerLease` that
the loop renews before every task; `reap()` marks a silent child `STALLED`, cancels its
token, and the parent moves on. The thread is leaked knowingly — an in-process call that
never returns cannot be interrupted, and claiming otherwise would be the process-group
defect in another costume.

<!-- zh -->
先测量：一个工具永不返回的子级会让父级的 `wait()` 永远阻塞，而且没有任何东西记录它卡住了。现在每个子级都持有一个 `WorkerLease`，由循环在每项任务之前续期；`reap()` 把一个沉默的子级标记为 `STALLED`，取消它的令牌，父级继续前进。线程是被明知故犯地泄漏的 —— 一次永不返回的进程内调用无法被中断，声称可以就是进程组缺陷换了件外衣。

Leaking it found a second defect: the stalled child mid-append on the shared event store
while `kernel.close()` closed the connection from another thread was a **segmentation
fault**. `EventStore.close()` takes the write lock now, and later appends refuse cleanly.

<!-- zh -->
泄漏它又发现了第二个缺陷：当 `kernel.close()` 从另一个线程关闭连接时，那个停滞的子级正追加到共享的事件存储中途，这是一个**段错误**。现在 `EventStore.close()` 会取写锁，之后的追加会干净地拒绝。


Idempotency keys — `"<loop run id>:<task id>"`, stable across retries and across a resume
— let a side-effecting component recognise the replay that `resume()` deliberately creates.

<!-- zh -->
幂等键 —— `"<loop run id>:<task id>"`，在重试之间与一次恢复之间保持稳定 —— 让有副作用的组件能够识别 `resume()` 有意制造的重放（replay）。

### Interoperability: remote tools and agents, on the operator's terms · 互操作性：远程工具与智能体，按操作员的条款

`psh/protocols/mcp.py` and `a2a.py` admit MCP tools and A2A agents as components. MCP's
own documentation says its annotations are hints, not security guarantees; A2A's samples say
an `AgentCard` is untrusted. Both adapters take the protocols at their word — annotations
may tighten and never loosen, the destination class is the operator's, a card's `url` must
fall inside the operator's allowed hosts, and `input-required` is escalated rather than
answered — and the consequence is that **neither added a gate**. PHI to an MCP tool on a
public server is refused by the same `ToolGateway` check that refuses PHI to a public model;
a remote agent passes `DelegationGateway` for its authority and `ToolGateway` for its data.

<!-- zh -->
`psh/protocols/mcp.py` 与 `a2a.py` 把 MCP 工具与 A2A 智能体接纳为组件。MCP 自己的文档说它的注解是提示（hints），不是安全保证；A2A 的示例说 `AgentCard` 是不可信的。两个适配器都按协议自己的说法对待它们 —— 注解只能收紧、永不放松，目的地类别由操作员决定，卡片（card）的 `url` 必须落在操作员允许的主机范围内，`input-required` 被升级处理而不是被直接应答 —— 而后果是**两者都没有新增任何门**。发往公共服务器上某个 MCP 工具的 PHI，被那条拒绝"PHI 发往公共模型"的同一个 `ToolGateway` 检查所拒绝；一个远程智能体就其权限通过 `DelegationGateway`，就其数据通过 `ToolGateway`。


It found a defect older than either adapter: `manifest_items` rendered every description
into the model's context with the default `PUBLIC` label, so a server-supplied description
carrying PHI would have reached a public model. Descriptions are classified at ingress now
and the rendered item carries that label.

<!-- zh -->
它发现了一个比两个适配器都更古老的缺陷：`manifest_items` 把每一段描述都以默认的 `PUBLIC` 标签渲染进模型的上下文，于是一段由服务器提供、携带 PHI 的描述本可以抵达一个公共模型。现在描述在入口处就被分类，渲染出的条目携带该标签。


### Labels travel across the loop — an adversarial review of the runtime · 标签穿越整个循环 —— 对运行时的一次对抗性评审

The runtime was reviewed after it was written: candidate findings, an independent
false-positive filter per finding, a cut at confidence 8. Two survived, both reproduced,
both the same shape — the loop went *through* the kernel and handed it the wrong label. A
tool result labelled PHI reached the next model task as PUBLIC evidence, because the label
was recorded on the node and never carried into the prompt; the objective and the
planner's feedback were never classified at all, so a PHI objective reached a public
planner under a PUBLIC verdict while `LoopResult.label` correctly said PHI.

<!-- zh -->
运行时是在写完之后才被评审的：先收集候选发现，对每一项发现做独立的假阳性过滤，在置信度 8 处截断。两项存活下来，都可复现，且形状相同 —— 循环**穿过**了内核，却把错误的标签交给了它。一个标记为 PHI 的工具结果作为 PUBLIC 证据抵达了下一个模型任务，因为标签被记录在节点上却从未被带进提示；目标与规划器的反馈则根本没有被分类，于是一个 PHI 目标在 PUBLIC 的判定下抵达了公共规划器，而 `LoopResult.label` 却正确地写着 PHI。


Labels travel now — with results into the next projection and payload, with the objective
onto every item built from it, with a model-authored plan onto every task objective, with
feedback onto what it quotes, with a delegation onto the child. And the kernel no longer
trusts a projection: the broker classifies the rendered text and joins, so a projection can
be escalated at that boundary and never trusted downward, and both gateways enforce the
run's ceiling, which only `Runner._preflight` had compared before. Checkpoints obey the
persistence rules — a result the gateway would refuse is withheld and the task re-runs —
and a record without a hash is refused. `docs/RUNTIME_SECURITY_REVIEW.md` has the report,
the two findings that were filtered out, and what was done about them anyway.

<!-- zh -->
现在标签会随行 —— 随结果进入下一个投影与载荷，随目标落到由它构建的每一个条目上，随模型撰写的计划落到每一个任务目标上，随反馈落到它所引用的内容上，随一次委派落到子级上。而且内核不再信任投影：broker 对渲染后的文本重新分类并求并，因此投影可以在那道边界上被升级、却永远不会被向下信任；两个网关都强制该运行的上限，而在此之前只有 `Runner._preflight` 比较过它。检查点遵守持久化规则 —— 网关会拒绝的结果被扣留、任务重新运行 —— 而没有哈希的记录会被拒绝。`docs/RUNTIME_SECURITY_REVIEW.md` 载有该报告、两项被过滤掉的发现，以及尽管被过滤、仍然对它们做了什么。


### Convergence: BioScience's capability plane, under this kernel · 融合：BioScience 的能力平面（capability plane, CP），在本文内核之下

The seam the roadmap described is built, on the other side of it: `bioagent.psh` in
BioScience-Harness v2.4 admits BioScience components — the 2,567-row catalogue and 56
live-verified public biomedical sources — as components of this kernel, with the gate
dimensions derived conservatively from what each declares, one harness per domain in the
two-level registry, and an isolated mode that runs them in this kernel's child process.
A call crosses both kernels in order and neither can be skipped. This package gained
three data-licence ids in `licensing.py` and nothing else: the dependency points one way.
`BioScience-Harness/docs/V24_PSH_CONVERGENCE.md` has the design, the evidence and the
honest state; `BioScience-Harness/demo_convergence.py` runs a plan through both.

<!-- zh -->
路线图所描述的那道接缝已经建成，在它的另一侧：BioScience-Harness v2.4 中的 `bioagent.psh` 把 BioScience 的组件 —— 那份 2,567 行的目录与 56 个经过实时验证的公共生物医学来源 —— 接纳为本内核的组件，其门维度保守地从各自声明推导，两级注册表中每个领域一个 harness，并且有一个隔离模式在本文内核的子进程中运行它们。一次调用按顺序穿过两个内核，两者都无法被跳过。本包在 `licensing.py` 中新增了三个数据许可 id，别无其他：依赖只朝一个方向。`BioScience-Harness/docs/V24_PSH_CONVERGENCE.md` 载有设计、证据与诚实的状态；`BioScience-Harness/demo_convergence.py` 让一个计划穿过两者运行。


```bash
python -m pytest tests/ -q          # 636 pass
python -m compileall -q src         # clean
```

## v0.5 — enforcement closure · v0.5 —— 强制闭合

No new subsystems. This release closes the gap an external review measured between what the
package *implements* and what its *executing path* uses. The defects shared one shape — a
control that exists, a main path that does not call it, and a README describing the
control — and each is now closed with a test that drives the main path:

<!-- zh -->
没有新子系统。本次发布关闭的是外部评审测量到的、包**实现了什么**与它的**执行路径用了什么**之间的差距。这些缺陷形状相同 —— 一个控制存在、一条主路径不调用它、一份 README 描述着该控制 —— 而现在每一个都被一个驱动主路径的测试关闭：


| Closed | What was wrong |
| --- | --- |
| **Policy is a ceiling** | `PolicySnapshot.envelope()` read `kw.pop("risk", self.risk_ceiling)`, so the ceiling applied only to callers who declined to state a value. `R2`/`SUGGEST` policies minted `R4`/`ACT` envelopes on request. Every dimension is now checked against the ceiling by `AuthorityLattice`; widening raises, `clamp=True` narrows instead. |
| **One minting path** | `TrustedKernel.envelope()` never consulted `self.policy` at all — it minted from hard-coded defaults (`PHI`, `ACT_WITH_APPROVAL`, four destinations). It now delegates to the policy, so a `peer_review` kernel cannot hand out a network envelope. |
| **Isolation on the real path** | `IsolatedRunner` shipped in v0.4 and `ExecutionBroker.call_tool` ran every component with `component.invoke(...)` *inside the kernel process* — a tool could read `os.environ` and open its own socket. Components declaring `backend="subprocess"` now run through the runner; the two paths are counted separately, the audit event records which ran, and `require_isolated_tools` refuses the in-process one outright. |
| **Approvals key on the action** | A session approval was keyed on the string `"run shell"`, so approving `git push origin main` for the session also pre-approved `curl … | sh`. The key is now a digest over request kind, component, normalised argv (or payload digest), targets and risk. |
| **Grants cannot unset the boundary** | `build_child_environment` wrote the proxy variables and then applied caller grants over them, so `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` disabled the egress boundary from inside. Kernel-reserved names are applied last and refused as grants. |
| **No DNS rebinding, ports are capabilities** | The proxy validated a hostname and then handed the *name* to `create_connection`, which resolved it again. It now connects to the addresses the decision vetted, refuses names that do not resolve, treats `host` as ports 80/443 (`host:8443`, `host:*` to say otherwise), and derives the forwarded `Host:` from the vetted URI instead of the client's header. |
| **Filesystem containment after resolution** | Path checks were `fnmatch` over the payload's own string, so `/allowed/../secret` and a symlink out of `/allowed` both passed. Paths are now resolved (`..`, symlinks, `~`) and must land inside an allowed root; nested payloads are walked. |
| **External hooks are contained** | A `PreToolUse` command hook received the fully unwrapped payload and ran under a bare `subprocess.run` — PHI reaching a third-party process with unsupervised network access, through a door the broker does not watch. External hooks now run through the same isolated runner (no network by default) and receive a redacted view; in-process hooks are trusted and unchanged. |
| **The output gate is not English-only** | Clinical assertions in Chinese and reported statistics (`AUC`, odds ratio, `p < 0.001`, 敏感性/死亡率) were invisible to the gate, and CJK text split into a single "sentence". Both are recognised now. |

<!-- zh -->
| 已关闭 | 问题所在 |
| --- | --- |
| **策略是上限** | `PolicySnapshot.envelope()` 读取 `kw.pop("risk", self.risk_ceiling)`，因此上限只作用于那些拒绝声明取值的调用者。`R2`/`SUGGEST` 策略按请求铸造出 `R4`/`ACT` 信封。现在每个维度都由 `AuthorityLattice` 对照上限检查；放宽会抛错，`clamp=True` 则改为收窄。 |
| **单一铸造路径** | `TrustedKernel.envelope()` 完全不咨询 `self.policy` —— 它从硬编码的默认值（`PHI`、`ACT_WITH_APPROVAL`、四个目的地）铸造。现在它委派给策略，因此一个 `peer_review` 内核无法发放网络信封。 |
| **隔离位于真实路径** | `IsolatedRunner` 在 v0.4 就已发布，而 `ExecutionBroker.call_tool` 用 `component.invoke(...)` 在**内核进程内**运行每一个组件 —— 一个工具可以读取 `os.environ` 并打开自己的套接字。声明 `backend="subprocess"` 的组件现在通过 runner 运行；两条路径分别计数，审计事件记录实际运行的是哪一条，而 `require_isolated_tools` 直接拒绝进程内那条。 |
| **审批以动作为键** | 一次会话审批的键是字符串 `"run shell"`，因此为会话批准 `git push origin main` 也就预先批准了 `curl … | sh`。现在的键是一个摘要，覆盖请求类别、组件、规范化后的 argv（或载荷摘要）、目标与风险。 |
| **授予无法取消边界** | `build_child_environment` 先写代理变量，然后在其上应用调用者的授予，于是 `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` 从内部关闭了出口边界。内核保留名最后应用，并作为授予被拒绝。 |
| **无 DNS 重绑定，端口即能力** | 代理校验了主机名，然后把**名字**交给 `create_connection`，后者又解析了一次。现在它连接到决策所审查过的那些地址，拒绝无法解析的名字，把 `host` 视为端口 80/443（`host:8443`、`host:*` 另行说明），并从已审查的 URI 而非客户端的头部推导转发的 `Host:`。 |
| **解析之后的文件系统包含** | 路径检查是对载荷自身字符串的 `fnmatch`，因此 `/allowed/../secret` 与一个指向 `/allowed` 之外的符号链接都能通过。现在路径会被解析（`..`、符号链接、`~`），并且必须落在允许的根目录内；嵌套载荷会被遍历。 |
| **外部钩子被包含** | 一个 `PreToolUse` 命令钩子收到完全解包的载荷，并在一个裸 `subprocess.run` 下运行 —— PHI 抵达一个拥有无人监督网络访问权的第三方进程，走的是一扇 broker 不监视的门。现在外部钩子通过同一个隔离 runner 运行（默认无网络），并收到一个已脱敏的视图；进程内钩子是受信任的，保持不变。 |
| **输出门不是只懂英文** | 中文的临床断言与报告的统计量（`AUC`、比值比、`p < 0.001`、敏感性/死亡率）对门不可见，而 CJK 文本被切成单独一个 "sentence"。现在两者都能被识别。 |


Also: `TrustedKernel.close()` releases the WorkGraph connection as well as the event store;
`hypothesis` is a declared test dependency (the authority property tests silently skipped
without it); `live` tests are deselected by default in `pyproject.toml` rather than by
remembering a flag; AppleDouble `._*` sidecars are gone from the tree and ignored (they
contain NUL bytes and break `python -m compileall`).

<!-- zh -->
另外：`TrustedKernel.close()` 现在既释放 WorkGraph 连接也释放事件存储；`hypothesis` 是一个已声明的测试依赖（没有它，权限属性测试会静默跳过）；`live` 测试在 `pyproject.toml` 中默认被取消选择，而不再依赖记住某个标志；AppleDouble `._*` 伴随文件已从代码树中清除并被忽略（它们含有 NUL 字节，会破坏 `python -m compileall`）。


Run tests:

<!-- zh -->
运行测试：


```bash
python -m pytest tests/ -q          # `-m live` opts into network tests
python -m compileall -q src         # clean
```

## What is enforced, and what is not · 什么被强制，什么没有被强制

Enforced, with a test that drives the executing path:

<!-- zh -->
被强制的（每一项都有一个驱动执行路径的测试）：

* no value reaches a gateway unclassified, and a caller-supplied label is re-validated —
  a compiled projection included, whose rendered text the broker classifies and joins;
* nothing above a run's ceiling reaches a model or a tool, at the gate rather than in one
  caller's preflight;
* no envelope is wider than the policy that minted it, on any of the lattice's dimensions;
* every model call, tool call and delegation passes the broker, which records an event;
* output is quarantined and `released_output` stays `None` unless the release gate passed —
  for a loop's deliverable exactly as for a single pass, through one `Finalizer`;
* a checkpoint of a run that may not persist holds the plan's shape and none of its text;
* a side-effecting tool whose earlier attempt is in doubt is not re-run;
* a policy requirement this process cannot meet (the validated detector, OS isolation)
  refuses the kernel or the run before anything executes;
* a `backend="subprocess"` component cannot read the kernel's environment.

<!-- zh -->
* 没有任何取值未分类就抵达网关，并且调用者提供的标签会被重新校验 —— 包括编译后的投影，其渲染文本由 broker 分类并求并；
* 任何高于运行上限的东西都不会抵达模型或工具，且是在门处被拦，而不是在某个调用者的预检中；
* 没有任何信封比铸造它的策略更宽，在权限格的任一维度上；
* 每一次模型调用、工具调用与委派都经过 broker，broker 会记录一条事件；
* 输出被隔离，且除非发布门通过，`released_output` 保持为 `None` —— 对一次循环的交付物与对单次通过完全一样，都经由同一个 `Finalizer`；
* 一个不得持久化的运行的检查点，只保存计划的形状，不保存其中任何文本；
* 一个先前尝试存疑的有副作用工具不会被重新运行；
* 本进程无法满足的策略要求（已验证的检测器、操作系统隔离）会在任何东西执行之前拒绝内核或该运行；
* 一个 `backend="subprocess"` 的组件无法读取内核的环境。


**Not** enforced, stated plainly:

<!-- zh -->
**没有**被强制的，直说：

* a `backend="python"` component runs in the kernel process and is confined by nothing.
  This is the honest boundary: `require_isolated_tools=True` is how a policy refuses it.
* the egress proxy governs clients that honour proxy variables. A raw socket bypasses it.
  `SandboxBackend` is the seam for the OS layer (Seatbelt, bubblewrap+seccomp); only
  `NoSandbox` ships, and it says so in `describe()` and in the kernel's `IsolationReport`.
* the audit chain is tamper-evident, not tamper-proof; classification is a safety net, not
  certified de-identification; claim support is lexical; the planner is a placeholder.

<!-- zh -->
* 一个 `backend="python"` 的组件在内核进程中运行，不受任何东西限制。这就是诚实的边界：`require_isolated_tools=True` 是策略拒绝它的方式。
* 出口代理只治理遵守代理变量的客户端。裸套接字会绕过它。`SandboxBackend` 是操作系统层（Seatbelt、bubblewrap+seccomp）的接缝；目前只随附 `NoSandbox`，它在 `describe()` 与内核的 `IsolationReport` 中如实说明。
* 审计链是防篡改可见（tamper-evident）的，不是防篡改（tamper-proof）的；分类是一张安全网，不是经过认证的去标识化；主张支撑是字面的；规划器是占位符。


## Install · 安装

```bash
pip install -e .            # standalone; classification degrades and says so
pip install -e .[test]      # pytest + hypothesis
```

Run tests with `PYTHONPATH=src` (or `PYTHONPATH=../sable_pkg/src:src` for the composed
configuration) — editable installs may not survive a session restart.

<!-- zh -->
用 `PYTHONPATH=src` 运行测试（组合配置则用 `PYTHONPATH=../sable_pkg/src:src`）—— 可编辑安装（editable install）可能无法在一次会话重启后存活。


## Use · 使用

```python
from psh import get_profile
from psh.kernel import TrustedKernel
from psh.runtime import Runner

policy = get_profile("clinical_research").freeze()   # local only, PHI ceiling
kernel = TrustedKernel(policy=policy)
runner = Runner(kernel, model=local_model, model_invoke=my_provider, policy=policy)

result = runner.run("Summarise the HFpEF evidence", sources={"34449189": abstract})
if result.released_output is None:
    print("refused:", result.error, "| quarantined as", result.quarantine_ref)
```

The policy is a ceiling: `runner.run(..., risk=RiskTier.R4_KERNEL)` under this profile is
refused at the `policy_snapshot` stage, not honoured. Ask for less than the profile grants
and you get it; ask for more and you get a `PolicyDenied` naming the dimension.

<!-- zh -->
策略是一个上限：在此 profile 下 `runner.run(..., risk=RiskTier.R4_KERNEL)` 会在 `policy_snapshot` 阶段被拒绝，而不是被满足。要求少于 profile 所授予的，你会得到；要求更多，你会得到一个指名了具体维度的 `PolicyDenied`。


That applies to the *policy* as well as to the envelope. `runner.run(..., policy=wider)` is
refused, because until v0.5.1 it was the way to mint a run the kernel's own policy forbade:

<!-- zh -->
这一点同样适用于**策略**本身，而不只是信封。`runner.run(..., policy=wider)` 会被拒绝，因为在 v0.5.1 之前，它正是铸造一次"内核自身策略所禁止的运行"的途径：


```python
runner.run("…", policy=broader)              # PolicyDenied, naming each dimension
Runner(kernel, policy=broader, clamp_policy=True)   # narrowed to the meet instead
runner.run("…", policy=policy.with_(autonomy=Autonomy.OBSERVE))   # narrowing: honoured
```

`clinical_research` is deliberately unusable without a local model. That is the profile
working as intended.

<!-- zh -->
`clinical_research` 在没有本地模型时被刻意设计为不可用。那就是该 profile 按预期工作的样子。


## Earlier releases · 更早的发布

* **v0.5** — enforcement closure: nine controls moved onto the path that actually
  executes. Policy became a real ceiling at minting, isolation reached `call_tool`,
  approvals keyed on the action, filesystem checks resolved before comparing.
* **v0.4** — six enforcement patterns adopted from Codex (read from source) and Claude Code
  (official docs), attributed per pattern in `PATTERN_ATTRIBUTION.md`: declarative
  self-testing execution policy, approval-to-rule amendments, absolute-deny evaluation
  order, the shared hook JSON protocol, default-deny child environments, a kernel-owned
  egress proxy refusing private ranges even when allowlisted.
* **v0.2** — sixteen externally-found defects closed as invariants: mandatory ingress, deep
  labelling, one authority predicate, release before exposure, classified persistence,
  evidence provenance, real token/cost accounting.

<!-- zh -->
* **v0.5** —— 强制闭合：九项控制被移到实际执行的路径上。策略在铸造时成为真正的上限，隔离抵达了 `call_tool`，审批以动作为键，文件系统检查先解析再比较。
* **v0.4** —— 从 Codex（读取源码）与 Claude Code（官方文档）采纳了六个强制模式，每个模式的出处见 `PATTERN_ATTRIBUTION.md`：声明式的自测试执行策略、审批回写为规则修正、绝对拒绝优先的求值顺序、共享的钩子 JSON 协议、默认拒绝的子环境、内核自有的出口代理（即使被列入允许名单也拒绝私有地址段）。
* **v0.2** —— 十六个外部发现的缺陷作为不变量被关闭：强制入口、深度标签、单一权限谓词、先发布后暴露、分类持久化、证据溯源、真实的 token/成本核算。


## Scope · 范围

Research prototype. Not clinical-safe, not production-ready.

<!-- zh -->
研究原型。不是临床安全的，也不是生产就绪的。


`docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the review items this release closes, each with
its reproduction, and the ones it deliberately leaves open.
`docs/V5_ENFORCEMENT_CLOSURE.md` — the previous round.
`docs/ROADMAP_AGENT_RUNTIME.md` — what this is *not* yet: a governed agent runtime. The
planner is a placeholder, `Runner.run` is a single pass rather than a loop, and delegation
is a primitive rather than an orchestrator. That roadmap is deliberately separate from the
enforcement work, because adding a loop before the gates compose correctly multiplies
whatever the gates get wrong.

<!-- zh -->
`docs/V5_1_GATE_COMPOSITION_CLOSURE.md` —— 本次发布关闭的评审条目，每一条都附有复现，以及它有意保持开放的那些。
`docs/V5_ENFORCEMENT_CLOSURE.md` —— 上一轮。
`docs/ROADMAP_AGENT_RUNTIME.md` —— 它**还不是**什么：一个受治理的智能体运行时。规划器是占位符，`Runner.run` 是单次通过而不是循环，委派是一个原语而不是编排器。那份路线图被有意与强制工作分开，因为在门正确组合之前就加入循环，会放大门做错的任何事。


MIT licensed.

<!-- zh -->
MIT 许可证。

