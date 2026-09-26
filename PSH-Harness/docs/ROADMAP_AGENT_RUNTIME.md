# From control plane to governed agent runtime · 从控制平面到受治理的代理运行时

Two reviews asked a question the enforcement work does not answer: **how much of an agent
runtime is actually here?** Their conclusion was blunt and correct —

<!-- zh -->
两次评审提出了一个强制实施工作并未回答的问题：**这里实际上有多少代理运行时？** 他们的结论直率而正确——

> PSH-Harness is a *Governed Agent Control Plane + Execution Broker*, not a multi-agent
> runtime. It is "single governed execution + a delegation primitive", not "a recursive
> multi-agent loop".

<!-- zh -->
> PSH-Harness 是一个*受治理的代理控制平面 + 执行代理*（Governed Agent Control Plane + Execution Broker），而不是一个多代理运行时。它是"单次受治理执行 + 一个委派原语"，而不是"一个递归的多代理循环"。

This document is the honest map, what v0.5.1 added against it, and the order to build the
rest in. It is deliberately separate from `V5_1_GATE_COMPOSITION_CLOSURE.md`, because the
sequencing matters: **a loop multiplies whatever the gates get wrong.** Every defect the
second review found would have fired once per iteration rather than once per run. Gates
first, then iteration.

<!-- zh -->
本文档是那张诚实的图谱，是 v0.5.1 相对于它增加了什么，以及构建其余部分的顺序。它有意与 `V5_1_GATE_COMPOSITION_CLOSURE.md` 分开，因为先后次序很重要：**循环会把门弄错的任何东西成倍放大。** 第二次评审发现的每一个缺陷，都会按每次迭代触发一次，而不是每次运行触发一次。先做门，再做迭代。

## 1. Where it actually stands · 1. 它实际处在什么位置

Measured against the executing path, not the README.

<!-- zh -->
衡量基准是实际执行路径，而不是 README。

| Mechanism | v0.5 | v0.5.1 | Note |
| --- | --- | --- | --- |
| Single governed execution pipeline | ✅ | ✅ | `Runner.run`, 16 stages |
| Typed plan | ❌ placeholder | ✅ | `runtime/plan.py` |
| Model-backed planner | ❌ | ✅ | `runtime/planner.py`, validator-driven correction |
| Plan validation | ❌ `"no typed plan to validate"` | ✅ | `runtime/plan_validator.py`, five families |
| Plan/act/observe/evaluate loop | ❌ | ✅ | `runtime/loop.py`, bounded |
| Tool-use loop integrated with the runtime | ❌ | ✅ | `TaskKind.TOOL` through the broker |
| Iterative replanning | ❌ | ✅ | bounded by `max_replans` |
| Loop termination controller | ❌ repeat counter | ✅ | ten named `Termination` reasons |
| Retry / backoff | ❌ config only | ✅ | `RetryPolicy` per task, charged to the budget |
| Layered evaluator | ❌ | ✅ | structural / execution / evidence / goal |
| Acceptance tests executed | ❌ declarative | ✅ | `ACCEPTANCE_CHECKS`, unknown kind = plan error |
| Output-schema validation | ❌ declarative | ✅ | `check_output_schema` |
| Delegation contract | ✅ | ✅ | `DelegationContract` |
| Child authority narrowing | ✅/🟡 | ✅ | gateway now defers to `AuthorityLattice` |
| Automatic subagent selection | ❌ | ✅ | the planner is told whether delegation is available and for how many children (`LoopState.can_delegate`, the authority brief); a delegate task with nowhere to go is corrected, not crashed |
| Fan-out / fan-in | ❌ | ✅ | `runtime/supervisor.py`: `WorkerPool`, `Reducer` → `AggregatedObservation` |
| Supervisor / worker pool | ❌ | ✅ | proposes only; `restrict()` authorises; Σ child fractions ≤ 1 |
| Heartbeat / lease | ❌ | ✅ | `WorkerLease` / `LeaseRegistry`; a silent child is `STALLED`, not waited for |
| Idempotency keys | ❌ | ✅ | `runtime/idempotency.py`; stable across retries and resume |
| Cancellation propagation | 🟡 interface | ✅ | `WAIT` / `CASCADE` / `DETACH`, cooperative token |
| Checkpoint / resume | 🟡 audit event | ✅ | `runtime/checkpoint.py`, authority re-met on resume |
| Parallel execution | ❌ | ✅ | supervisor: threads over one locked kernel, bounded by `max_concurrency`; loop: independent ready tasks on a bounded pool, `LoopLimits(max_parallel)` |
| Progressive disclosure of tool schemas | ❌ | ✅ | `CapabilityRegistry.schema_items`: summaries to choose, schemas to call, for the ranked few |
| A2A / MCP | ❌ | ✅ | `protocols/mcp.py`, `protocols/a2a.py`; no SDK, no new gate |
| Context compaction | ❌ | ✅ | `context/compaction.py`, label is the join of the sources |
| Project memory recall | ❌ | ✅ | `context/memory.py`: verified nodes only, labels carried, withheld above the run ceiling or for the destination, read-only by structural test |
| Persistent WorkGraph, provenance, quarantine, audit | ✅ | ✅ | the package's strongest layer |

<!-- zh -->
| 机制 | v0.5 | v0.5.1 | 说明 |
| --- | --- | --- | --- |
| 单次受治理的执行流水线 | ✅ | ✅ | `Runner.run`，16 个阶段 |
| 类型化计划 | ❌ 占位实现 | ✅ | `runtime/plan.py` |
| 模型驱动的规划器 | ❌ | ✅ | `runtime/planner.py`，由校验器驱动的纠正 |
| 计划校验 | ❌ `"no typed plan to validate"` | ✅ | `runtime/plan_validator.py`，五个族（families） |
| 计划/行动/观察/评估循环 | ❌ | ✅ | `runtime/loop.py`，有界 |
| 与运行时集成的工具使用循环 | ❌ | ✅ | `TaskKind.TOOL` 经由执行代理 |
| 迭代式重规划 | ❌ | ✅ | 由 `max_replans` 约束 |
| 循环终止控制器 | ❌ 重复计数器 | ✅ | 十种具名的 `Termination` 原因 |
| 重试 / 退避 | ❌ 仅有配置 | ✅ | 每个任务一个 `RetryPolicy`，计入预算 |
| 分层评估器 | ❌ | ✅ | 结构 / 执行 / 证据 / 目标 |
| 验收测试被执行 | ❌ 仅声明式 | ✅ | `ACCEPTANCE_CHECKS`，未知类型 = 计划错误 |
| 输出模式（schema）校验 | ❌ 仅声明式 | ✅ | `check_output_schema` |
| 委派契约 | ✅ | ✅ | `DelegationContract` |
| 子代理权限收窄 | ✅/🟡 | ✅ | 网关现在服从 `AuthorityLattice` |
| 自动子代理选择 | ❌ | ✅ | 规划器会被告知委派是否可用、可用于多少个子代理（`LoopState.can_delegate`，权限简报）；一个无处可去的委派任务会被纠正，而不是崩溃 |
| 扇出 / 扇入 | ❌ | ✅ | `runtime/supervisor.py`：`WorkerPool`、`Reducer` → `AggregatedObservation` |
| Supervisor / 工作池 | ❌ | ✅ | 只做提议；`restrict()` 授权；Σ 子代理份额 ≤ 1 |
| 心跳 / 租约 | ❌ | ✅ | `WorkerLease` / `LeaseRegistry`；一个沉默的子代理被标记为 `STALLED`，而不是被一直等待 |
| 幂等键 | ❌ | ✅ | `runtime/idempotency.py`；在重试与恢复之间保持稳定 |
| 取消传播 | 🟡 接口 | ✅ | `WAIT` / `CASCADE` / `DETACH`，协作式令牌 |
| 检查点 / 恢复 | 🟡 审计事件 | ✅ | `runtime/checkpoint.py`，恢复时权限重新做 meet |
| 并行执行 | ❌ | ✅ | supervisor：在一个加锁内核之上的线程，由 `max_concurrency` 约束；loop：彼此独立的就绪任务跑在一个有界池上，`LoopLimits(max_parallel)` |
| 工具模式（schema）的渐进式披露 | ❌ | ✅ | `CapabilityRegistry.schema_items`：摘要用于选择，模式用于调用，只针对排序靠前的那几个 |
| A2A / MCP | ❌ | ✅ | `protocols/mcp.py`、`protocols/a2a.py`；没有 SDK，没有新增的门 |
| 上下文压缩 | ❌ | ✅ | `context/compaction.py`，标签是各来源标签的并（join） |
| 项目记忆召回 | ❌ | ✅ | `context/memory.py`：只召回已验证节点，标签随行，超出本次运行上限或针对该目的地时被扣留，由结构测试保证只读 |
| 持久化 WorkGraph、溯源、隔离区、审计 | ✅ | ✅ | 本包最强的一层 |

So the honest summary is now: **a bounded, governed agent loop with governed, durable
fan-out, and remote tools and agents admitted on the operator's terms.** Children run
concurrently through one locked kernel, a supervisor that can only narrow, a reducer that
records disagreement, leases so a child that stops answering is given up on, and MCP/A2A
adapters that add no execution path the gates do not already cover — and, since v0.5.2,
project memory that a later run recalls under the same gateway's labels, and a planner
that is told when it may delegate. Still absent: distributed workers and A2A polling for
long-running remote tasks.

<!-- zh -->
所以诚实的总结现在是：**一个有界、受治理的代理循环，带有受治理且持久的扇出，以及按操作者条件接纳的远程工具与代理。** 子代理通过一个加锁内核并发运行，经过一个只能收窄的 supervisor，经过一个记录分歧的 reducer，租约让一个不再应答的子代理被放弃，还有 MCP/A2A 适配器——它们没有增加任何门尚未覆盖的执行路径；并且自 v0.5.2 起，还有后续运行能在同一网关的标签下召回的项目记忆，以及一个会被告知何时可以委派的规划器。仍然缺失：分布式工作进程，以及针对长时间运行远程任务的 A2A 轮询。

## 2. What v0.5.1 added, and the one rule it was built under · 2. v0.5.1 增加了什么，以及它遵循的那一条规则

```
psh/runtime/
├── runner.py          the single governed pass (unchanged)
├── plan.py            Plan, PlanTask, TestSpec, Criterion, RetryPolicy, RetryBudget
├── plan_validator.py  graph / authority / dataflow / budget / scientific
├── execgraph.py       ExecutionGraph, TaskState — transient, NOT the WorkGraph
├── evaluator.py       four layers, three deterministic
└── loop.py            AgentLoopController, LoopState, Termination
```

The rule, which the review states and which is the whole reason this was built here rather
than adopted from elsewhere:

<!-- zh -->
这条规则由评审提出，也正是"它在这里被构建、而不是从别处采纳"的全部原因：

> **The LoopController must never bypass the TrustedKernel.**

<!-- zh -->
> **LoopController 绝不能绕过 TrustedKernel。**

```
        AgentLoopController                 what most agent runtimes are
                 |                                     |
          ExecutionBroker                              |-- calls a provider
                 |                                     |-- spawns a subprocess
          TrustedKernel                                |-- opens a socket
                 |                                     '-- spawns an agent
    model / tool / delegate
```

It is enforced three ways, not asserted once:

<!-- zh -->
它以三种方式被强制执行，而不是被断言一次：

1. `_dispatch` has exactly three branches, each a broker call.
2. `test_the_loop_cannot_act_except_through_the_broker` balances the broker's counters
   against what the loop did.
3. `test_the_loop_module_holds_no_route_to_the_outside_world` parses `loop.py` and fails if
   it ever imports `subprocess`, `socket`, `urllib`, `asyncio`, `os` or a HTTP client — a
   behavioural test sees the paths a test took; this sees the paths that *exist*. That
   distinction is exactly how `IsolatedRunner` shipped in v0.4 while `call_tool` ran
   everything in process.

<!-- zh -->
1. `_dispatch` 恰好有三个分支，每一个都是一次执行代理调用。
2. `test_the_loop_cannot_act_except_through_the_broker` 把执行代理的计数器与循环实际做过的事情对账。
3. `test_the_loop_module_holds_no_route_to_the_outside_world` 解析 `loop.py`，一旦它导入了 `subprocess`、`socket`、`urllib`、`asyncio`、`os` 或某个 HTTP 客户端就失败——行为测试看到的是测试走过的路径；这一测试看到的是*存在*的路径。这一区别正是 `IsolatedRunner` 在 v0.4 发布、而 `call_tool` 却把所有东西都跑在进程内的原因。

Three design decisions worth stating because they differ from the review's sketch:

<!-- zh -->
有三个设计决策值得说明，因为它们与评审的草图不同：

**`ExecutionGraph` is not the `WorkGraph`.** The review recommended separating them and it
is right: a scheduling structure whose nodes go `RUNNING` and `FAILED` and get retried
cannot be the same object as a provenance record, or a retry rewrites history. Outcomes
reach the WorkGraph through the persistence gateway; task states never leave memory except
as audit events and, when a checkpoint store is wired, as checkpoints written under the
persistence rules (`docs/RUNTIME_SECURITY_REVIEW.md`).

<!-- zh -->
**`ExecutionGraph` 不是 `WorkGraph`。** 评审建议把二者分开，这是对的：一个其节点会进入 `RUNNING` 与 `FAILED`、并会被重试的调度结构，不能与一条溯源记录是同一个对象，否则一次重试就会改写历史。结果通过持久化网关进入 WorkGraph；任务状态绝不离开内存，除非作为审计事件，以及当接入了检查点存储时、按持久化规则写下的检查点（`docs/RUNTIME_SECURITY_REVIEW.md`）。

**Task authority is `restrict()`, not a new comparison.** `task_envelope()` narrows the run
envelope with the existing lattice, and the *validator returns the envelopes the loop then
uses*. Recomputing them at execution time would put a second construction path beside the
validated one — the precise shape of defect this release spent its time closing.

<!-- zh -->
**任务权限是 `restrict()`，而不是一次新的比较。** `task_envelope()` 用既有的权限格（authority lattice）收窄运行信封，而*校验器返回的信封，正是循环随后使用的那些信封*。在执行时重新计算它们，会在已校验的构造路径旁边放下第二条构造路径——这正是本版本花时间去关闭的那种缺陷形态。

**Three deterministic evaluator layers before the model-shaped one.** A single "LLM critic:
looks good?" is both the least reliable check and the one that gets asked first. Schema,
execution state and evidence are pure functions of loop state; only the goal layer may
consult a model, and when it does it goes through the broker.

<!-- zh -->
**在模型形态的那一层之前，先有三层确定性评估器。** 单个"LLM 裁判：看起来还行？"既是最不可靠的检查，又是最先被问到的那个检查。模式（schema）、执行状态与证据都是循环状态的纯函数；只有目标层可以咨询模型，而它一旦这么做，就要经过执行代理。

## 3. What to build next, in order · 3. 接下来按顺序要构建什么

The ordering is by engineering dependency, not by appeal. In particular **do not start with
MCP or A2A**: both are adapters onto a runtime, and adapting an incomplete runtime means
building the adapter twice.

<!-- zh -->
顺序由工程依赖决定，而不是由吸引力决定。尤其是**不要从 MCP 或 A2A 开始**：两者都是落到某个运行时之上的适配器，而为一个不完整的运行时做适配，意味着要把适配器建两遍。

### v0.6 — finish the single-agent runtime · v0.6 — 完成单代理运行时

* **A real planner.** ~~`StaticPlanner` takes the plan from the caller, which is honest but
  is not planning.~~ **Done** (`psh/runtime/planner.py`). `ModelPlanner` emits a typed
  `Plan` through the broker, with PydanticAI-style validator-driven correction: each
  refusal is fed back as the next attempt's input, bounded by `max_attempts`. The load-
  bearing test is that an *escalating* plan is refused rather than obeyed — a planner's
  output is a program, not an answer, so trusting it because a model produced it would make
  every control in the package reachable by asking.
* **Context compaction.** ~~Absent entirely.~~ **Done** (`psh/context/compaction.py`),
  in DeepSeek Harness's shape — summarise and shadow rather than truncate. It landed in the
  compiler rather than in a session, because this runtime has no shared transcript to
  compact: `ContextProjection` belongs to one worker, so the place the loss actually
  occurred was the compiler silently discarding over-budget items and reporting a count.

  The property that made it a kernel concern: **a summary carries the join of the labels it
  summarises.** Deriving it from the summary text instead would let a summary of PHI whose
  extract omits the identifiers classify as `INTERNAL` — measured, not hypothesised — and
  reach a destination its sources could not. The default summariser is extractive and
  deterministic, because compression on the critical path must not itself require egress.
* **Checkpoint / resume for real.** ~~`LoopState` was written to be the thing a checkpoint
  copies.~~ **Done** (`psh/runtime/checkpoint.py`). The load-bearing rule held: a resumed
  run computes `AuthorityLattice.meet(stored, current_ceiling)`, so it is never wider than
  it was *or* than the policy in force, and every narrowed dimension is audited. Restoring
  the stored envelope would have been P0-1 through a file rather than a keyword argument —
  the same defect's second vector, which is the argument for the meet living in one place
  and every entry point going through it. Checkpoints are content-hashed and refused if
  they do not verify; unfinished tasks are re-authorised individually and finished ones are
  not re-checked.

  Building it surfaced a defect in `task_envelope`: `risk` was met with `min()` while
  `max_label` and `autonomy` were passed through unclamped, so a ceiling applied on one
  dimension and refused on the next. The distinction is now explicit — `max_risk`,
  `max_label` and `autonomy` are *self-imposed ceilings* and meet the run's;
  `destinations` and `capability_requirements` are *required reach* and are refused if the
  run does not hold them.

<!-- zh -->
* **一个真正的规划器。** ~~`StaticPlanner` 从调用方那里取得计划，这很诚实，但并不是规划。~~ **已完成**（`psh/runtime/planner.py`）。`ModelPlanner` 通过执行代理产出一个类型化的 `Plan`，带有 PydanticAI 风格的、由校验器驱动的纠正：每一次拒绝都会作为下一次尝试的输入被反馈回去，并由 `max_attempts` 约束。承重的测试是：一个*升级式的*计划会被拒绝，而不是被服从——规划器的输出是一个程序，不是一个答案；因此，因为它是模型产出的就信任它，会让本包中每一个控制点都可以靠"开口要"而抵达。
* **上下文压缩（compaction）。** ~~完全缺失。~~ **已完成**（`psh/context/compaction.py`），采用 DeepSeek Harness 的形态——做摘要并遮蔽（summarise and shadow），而不是截断。它落在编译器里而不是某个会话里，因为本运行时没有可压缩的共享对话记录：`ContextProjection` 属于单个工作进程，所以丢失实际发生的位置，是编译器默默丢弃超预算条目并报出一个计数。

  使它成为可信内核（trusted kernel, TK）关切的性质是：**一份摘要携带它所摘要内容各标签的并（join）。** 改从摘要文本推导标签，就会让一份 PHI 摘要——其摘录省略了标识符——被归类为 `INTERNAL`（这是实测结果，不是假设），并抵达其来源本来无法抵达的目的地。默认摘要器是抽取式且确定性的，因为关键路径上的压缩本身不得要求出网。
* **真正的检查点 / 恢复。** ~~`LoopState` 当初就是按"检查点所要复制的东西"来写的。~~ **已完成**（`psh/runtime/checkpoint.py`）。承重的规则守住了：一次恢复的运行会计算 `AuthorityLattice.meet(stored, current_ceiling)`，因此它绝不会比原先更宽，也绝不会比生效中的策略更宽，并且每一个被收窄的维度都会被审计。恢复存储下来的信封，就会是"经由文件而不是关键字参数实现的 P0-1"——同一缺陷的第二个载体，这正是让 meet 只存在于一处、并让每个入口都经过它的理由。检查点按内容做哈希，校验不通过就被拒绝；未完成的任务被逐个重新授权，已完成的任务不再复查。

  构建它暴露了 `task_envelope` 中的一个缺陷：`risk` 用 `min()` 来做 meet，而 `max_label` 与 `autonomy` 却被不加钳制地放过，于是上限在一个维度上生效、在下一个维度上被拒绝。这一区分现在是显式的——`max_risk`、`max_label` 与 `autonomy` 是*自我施加的上限*，与本次运行的上限做 meet；`destinations` 与 `capability_requirements` 是*必需的可达范围*，如果本次运行不持有它们就会被拒绝。

### v0.7 — multi-agent — **done** (`runtime/subagent.py`, `runtime/supervisor.py`) · v0.7 — 多代理 — **已完成**（`runtime/subagent.py`、`runtime/supervisor.py`）

`Supervisor`, `WorkerPool`, fan-out/fan-in, `Reducer`, child lifecycle and cancellation
propagation, under the one rule:

<!-- zh -->
`Supervisor`、`WorkerPool`、扇出/扇入、`Reducer`、子代理生命周期与取消传播，都在同一条规则之下：

> **A supervisor decides *what* to do. It never decides what is *allowed*.**

<!-- zh -->
> **supervisor 决定*做什么*。它绝不决定什么被*允许*。**

```
Supervisor --propose--> TrustedKernel --authorize--> Worker
```

The supervisor holds no way to construct a `RunEnvelope`. `mint()` turns a request into
arguments for `parent.restrict()` — the lattice — and a request for more than the parent
holds raises from inside that call. A structural test parses `mint()` and fails if it ever
compares anything against the parent: the supervisor is *contained*, not trusted, which is
the stronger property and the cheaper one. Children are `AgentLoopController`s under the
contract's envelope, returning a `SubagentResult` — claims, evidence, artifacts, a summary,
and a label that is the join of everything the child saw. There is no field for a
transcript (Grok Build's model, and `ContextProjection`'s argument applied one level up).

<!-- zh -->
supervisor 不持有任何构造 `RunEnvelope` 的途径。`mint()` 把一个请求转成传给 `parent.restrict()`——即权限格——的参数，而一个索取超出父级所持有内容的请求，会从那一次调用内部抛出。一个结构测试解析 `mint()`，一旦它拿任何东西与父级比较就失败：supervisor 是被*约束住的（contained）*，而不是被信任的；这是更强的性质，也是更便宜的那一个。子代理是处于契约信封之下的 `AgentLoopController`，返回一个 `SubagentResult`——主张、证据、产物、一份摘要，以及一个"子代理所见一切之并"的标签。没有为对话记录留字段（Grok Build 的模型，也就是把 `ContextProjection` 的论证向上应用一层）。

Three things it surfaced:

<!-- zh -->
它暴露了三件事：

* **`Budget.child()` never bounded siblings.** Its docstring says it "stops a delegation
  tree from multiplying a budget by fanning out". Ten quarter-children are two and a half
  parents, measured. `BudgetLedger` sums fractions per parent run, cumulatively — a finished
  child spent its slice, so releasing it would allow children forever, one at a time.
* **The kernel was not safe to share between threads.** `BudgetGovernor`'s checks are
  compare-then-increment; forty trials at the tightest GIL switch interval could not race
  them, and a ceiling that holds because of scheduler timing is not a ceiling. Locked, along
  with the broker's counters — which are the proof nothing bypassed the broker, and would be
  worthless under concurrency exactly when they matter.
* **Cancellation needs a policy, not a flag.** `WAIT` / `CASCADE` / `DETACH`, per child, on
  a cooperative token the loop polls at the same point it checks every other bound. A parent
  being cancelled does not always mean killing the five-hour analysis.

<!-- zh -->
* **`Budget.child()` 从未约束住兄弟节点。** 它的 docstring 说它"阻止一棵委派树通过扇出把预算成倍放大"。十个四分之一子代理就是两个半父级——这是实测的。`BudgetLedger` 按每个父级运行累计求和份额——一个已完成的子代理已经花掉了它的那一份，所以释放它就会允许子代理永远一个接一个地来。
* **内核在跨线程共享时并不安全。** `BudgetGovernor` 的检查是先比较后自增；在最紧的 GIL 切换间隔下做四十次试验都没能让它们竞争出问题，而一个因为调度器时序才守住的上限并不是上限。已加锁，执行代理的计数器同样加锁——那些计数器是"没有任何东西绕过执行代理"的证明，而在并发之下，它们恰恰会在最要紧的时候变得一文不值。
* **取消需要一项策略，而不是一个标志位。** `WAIT` / `CASCADE` / `DETACH`，按子代理设置，作用于一个协作式令牌，循环在检查其他每一个边界时的同一个点上轮询它。父级被取消，并不总是意味着要杀掉那个五小时的分析。

Fan-in is `AggregatedObservation`: facts with who asserted them, evidence, *conflicts*
(detected with the existing `ClaimSupportVerifier` — polarity and overlap, not a new model
of contradiction), what is unresolved, and a label that is the join. Two children
disagreeing is a recorded state.

<!-- zh -->
扇入是 `AggregatedObservation`：事实及其断言者、证据、*冲突*（用既有的 `ClaimSupportVerifier` 检测——极性与重叠，而不是一套新的矛盾模型）、尚未解决的内容，以及一个做并（join）得到的标签。两个子代理意见不一致，是一种被记录下来的状态。

### v0.8 — durability — **done** (`runtime/subagent.py`, `runtime/idempotency.py`) · v0.8 — 持久性 — **已完成**（`runtime/subagent.py`、`runtime/idempotency.py`）

Checkpoint/resume landed in v0.6 and cancellation policy in v0.7, so what remained was
leases and idempotency, and both were built after measuring the defect they close.

<!-- zh -->
检查点/恢复在 v0.6 落地，取消策略在 v0.7 落地，所以剩下的是租约与幂等性；两者都是在测量了它们所关闭的缺陷之后才构建的。

**A hung child was invisible.** A child whose tool never returned — a dead socket, a
deadlocked driver — left the parent's `wait()` blocked forever, and `dispatch(wait=True)`
passed no timeout. Nothing anywhere recorded that the child was stuck. Cooperative
cancellation cannot help; the child is inside a call it will never come back from. So the
signal is the *absence* of a heartbeat: every child holds a `WorkerLease`, the loop beats at
every bounds check and before every task (per task, so a long plan of short steps is never
mistaken for a hang), and `reap()` marks a silent child `STALLED`, cancels its token so it
stops if it ever wakes, and lets the parent proceed. The thread is leaked knowingly. What is
*not* claimed: that the thread was stopped. An in-process tool that never returns cannot be
interrupted from outside, and saying "killed" about it would be the process-group defect in
another costume.

<!-- zh -->
**一个卡住的子代理是不可见的。** 一个工具永不返回的子代理——死掉的 socket、死锁的驱动——会让父级的 `wait()` 永远阻塞，而 `dispatch(wait=True)` 没有传任何超时。没有任何地方记录下这个子代理卡住了。协作式取消帮不上忙；子代理正身处一个它永远不会从中返回的调用里。所以信号是心跳的*缺席*：每个子代理持有 `WorkerLease`，循环在每一次边界检查时、以及每个任务之前打一次心跳（按任务打，因此一个由短步骤组成的长计划绝不会被误判为挂起），而 `reap()` 把一个沉默的子代理标记为 `STALLED`，取消它的令牌——这样它万一醒来就会停下——并让父级继续。这个线程是被知情地泄漏掉的。*没有*被声称的是：这个线程被停止了。一个永不返回的进程内工具无法从外部中断，而对它说"已被杀掉"，会是换了一身装扮的进程组缺陷。

**Leaking that thread crashed the process.** The first end-to-end test segfaulted: the
stalled child, mid-append on the shared event store, while the parent's `kernel.close()`
closed the sqlite connection from another thread. `check_same_thread=False` permits
sharing; it does not make closing safe. `EventStore.close()` takes the write lock now —
so it waits for a writer in flight and makes every later one fail cleanly — and a late
child's final audit write ends quietly. The regression test reproduces the segfault 3/3
against the old `close()`.

<!-- zh -->
**泄漏那个线程让进程崩溃了。** 第一个端到端测试出现了段错误：卡住的子代理正在对共享事件存储做追加，而与此同时父级的 `kernel.close()` 从另一个线程关闭了 sqlite 连接。`check_same_thread=False` 允许共享；它并没有让关闭变得安全。`EventStore.close()` 现在会取写锁——因此它会等待正在写入的写者，并让之后每一个写者干净地失败——而一个迟到的子代理的最后一次审计写入会安静地结束。回归测试针对旧的 `close()` 复现了 3/3 的段错误。

**Idempotency keys** are `"<loop run id>:<task id>"`, the same on every attempt and —
because `resume()` preserves the run id through the meet — the same after a crash. That is
the case they exist for: `resume()` turns a task that was RUNNING into RETRYABLE because
what it did is unknown, and a component with side effects would do them again unless it
recognises the key. The loop cannot decide replay semantics for a component, so
`IdempotencyLedger` is something a component *uses*, not something the kernel imposes.

<!-- zh -->
**幂等键**是 `"<loop run id>:<task id>"`，在每一次尝试上都相同，并且——因为 `resume()` 通过 meet 保住了运行 id——在崩溃之后也相同。那正是它们存在的场景：`resume()` 会把一个原本处于 RUNNING 的任务变成 RETRYABLE，因为它做过什么无人知晓，而一个带有副作用的组件，除非能识别这个键，否则会把副作用再做一遍。循环无法为一个组件决定重放语义，所以 `IdempotencyLedger` 是组件*使用*的东西，而不是内核强加的东西。

### v0.9 — interoperability — **done** (`protocols/mcp.py`, `protocols/a2a.py`) · v0.9 — 互操作性 — **已完成**（`protocols/mcp.py`、`protocols/a2a.py`）

`MCPToolAdapter` for agent↔tool, `A2AAgentAdapter` for agent↔agent. Both under one rule:

<!-- zh -->
`MCPToolAdapter` 用于 agent↔tool，`A2AAgentAdapter` 用于 agent↔agent。两者都在同一条规则之下：

```
MCP server / remote AgentCard
        |
     adapter
        |
  IngressClassifier        <- external metadata is untrusted input
        |
  ComponentManifest / RemoteAgent
        |
  ToolGateway / DelegationGateway
        |
    TrustedKernel
```

MCP's own documentation says its tool annotations are **hints, not security guarantees**,
and A2A's samples say an `AgentCard` and everything a remote agent returns are untrusted.
Both adapters take the protocols at their word, and the consequence is the property worth
stating: **neither added a gate.** An MCP tool is a `ComponentManifest` whose destination
is the operator's, so PHI to it is refused by the same `ToolGateway` check that refuses PHI
to a public model. A remote agent is a component *and* a delegation, so it passes
`DelegationGateway` for its authority and `ToolGateway` for its data — two gates built for
local work, and the remote case is where they are most obviously necessary.

<!-- zh -->
MCP 自己的文档说，它的工具注解是**提示，不是安全保证**；而 A2A 的示例说，`AgentCard` 以及远程代理返回的一切都是不可信的。两个适配器都照协议自己的话去对待它们，其后果正是那个值得说明的性质：**两者都没有新增一道门。** 一个 MCP 工具是一个 `ComponentManifest`，其目的地由操作者指定，所以发给它的 PHI 会被拒绝——拒绝它的正是那道拒绝把 PHI 发给公共模型的同一个 `ToolGateway` 检查。一个远程代理既是一个组件*又*是一次委派，因此它要通过 `DelegationGateway` 接受权限检查、通过 `ToolGateway` 接受数据检查——这是两道为本地工作而建的门，而远程场景恰恰是它们最明显必要的地方。

The rules, per adapter:

<!-- zh -->
逐适配器的规则：

* **MCP.** Annotations may tighten and never loosen: `destructiveHint` raises the risk and
  requires approval; `readOnlyHint` changes nothing, because a server that can lie about
  being read-only can lie. The only way to relax a default is an operator `override`,
  recorded as the operator's decision. The destination class is stated at adapter
  construction, never inferred. An `isError` reply is a `ContractViolation`, not a result
  with an odd shape. Runtime bookkeeping (`_psh_*` keys) is stripped before the wire.
* **A2A.** The card grants nothing: `skills` become retrieval metadata, `capabilities`
  are recorded as claims, and the `url` must fall inside the operator's `allowed_hosts` or
  the card is refused. `input-required` is escalated, never answered — a remote agent asking
  a question is asking a human. A non-terminal reply is not a result. The reply's label is
  the join of what was sent and what came back, classified.
* **Neither imports an SDK.** Each takes a transport callable and the protocol's own JSON
  shapes, so the official client, a stdio subprocess and a test double share one seam and
  the trust boundary lives here rather than inside a library's session object.

<!-- zh -->
* **MCP。** 注解可以收紧、绝不能放松：`destructiveHint` 会提高风险并要求审批；`readOnlyHint` 不改变任何东西，因为一个能在"只读"上撒谎的服务器就是会撒谎。放松某个默认值的唯一途径是操作者的 `override`，并记录为操作者的决定。目的地类别在适配器构造时声明，绝不推断。`isError` 回复是一个 `ContractViolation`，而不是一个形状古怪的结果。运行时簿记（`_psh_*` 键）在上线之前被剥除。
* **A2A。** 卡片不授予任何东西：`skills` 变成检索元数据，`capabilities` 被记录为主张（claims），而 `url` 必须落在操作者的 `allowed_hosts` 之内，否则卡片被拒绝。`input-required` 会被升级上报，绝不代为回答——一个远程代理在提问，就是在问人。非终态的回复不是一个结果。回复的标签是"所发送内容与所返回内容之并"，并经过分类。
* **两者都不导入 SDK。** 各自接收一个传输 callable 与协议自身的 JSON 形状，因此官方客户端、一个 stdio 子进程和一个测试替身共用同一个接缝，信任边界存在于这里，而不是活在某个库的会话对象内部。

It surfaced one defect that predates both adapters. `CapabilityRegistry.manifest_items`
rendered every description into the model's context with the **default `PUBLIC` label**,
and the compiler's destination filter reads the label — so a description containing PHI
would have been compiled into a public model's context. Harmless while every description
was written by an operator; not harmless the moment one arrives from a server. Adapters now
classify descriptions at ingress and the registry labels the rendered item accordingly;
the regression test fails against the unpatched registry.

<!-- zh -->
它暴露了一个早于两个适配器的缺陷。`CapabilityRegistry.manifest_items` 把每一条描述都用**默认的 `PUBLIC` 标签**渲染进模型的上下文，而编译器的目的地过滤器会读取这个标签——因此一条含 PHI 的描述本会被编译进一个公共模型的上下文。当每一条描述都由操作者撰写时这无害；而一旦有一条从服务器到来，就绝非无害。适配器现在在入口处对描述分类，注册表据此为渲染出的条目打标签；该回归测试在未打补丁的注册表上会失败。

What is *not* claimed: A2A polling. The transport is synchronous and a task that comes back
`working` is escalated rather than waited on.

<!-- zh -->
*没有*被声称的是什么：A2A 轮询。传输是同步的，一个返回 `working` 的任务会被升级上报，而不是被等待。

## 4. What to borrow, and what not to · 4. 该借鉴什么，不该借鉴什么

The reviews' survey, reduced to the decisions:

<!-- zh -->
两次评审的调研，归结为这些决定：

| Take from | What | Why not wholesale |
| --- | --- | --- |
| LangGraph | checkpointer, thread-scoped state vs. cross-thread store, `Send` map-reduce | its state model has no authority dimension |
| Google ADK | workflow graph, fan-out/fan-in, nested workflows | same |
| PydanticAI | typed output + validator + retry, *layered* retry budgets | `max_retries` as one number is the thing to avoid |
| Microsoft Agent Framework | `Handoff`, `Concurrent`, Magentic manager | manager-grants-tools is the anti-pattern here |
| OpenAI Agents SDK | the loop shape, sessions, handoff input filters | — |
| Temporal / Prefect | durable scheduling, cancellation semantics, task lifecycle | heavyweight for a research prototype; take the semantics |
| DeepSeek Harness | pluginised agent loop, durable session event log, **compaction** | — |
| Grok Build | subagent = independent child session returning a *summary*; worktree isolation; folder trust | — |
| Codex | execpolicy `allow/prompt/forbidden`, approval→amendment, sandbox/exec seams | already adopted; see `PATTERN_ATTRIBUTION.md` |

<!-- zh -->
| 借鉴自 | 借鉴什么 | 为何不能整体照搬 |
| --- | --- | --- |
| LangGraph | checkpointer、线程作用域状态与跨线程存储的对比、`Send` map-reduce | 它的状态模型没有权限这一维度 |
| Google ADK | 工作流图、扇出/扇入、嵌套工作流 | 同上 |
| PydanticAI | 类型化输出 + 校验器 + 重试，*分层*重试预算 | 把 `max_retries` 当成一个数字，正是要避免的那种做法 |
| Microsoft Agent Framework | `Handoff`、`Concurrent`、Magentic manager | "管理者授予工具"在这里是反模式 |
| OpenAI Agents SDK | 循环的形态、会话、handoff 输入过滤器 | — |
| Temporal / Prefect | 持久化调度、取消语义、任务生命周期 | 对研究原型而言过于笨重；取其语义即可 |
| DeepSeek Harness | 插件化的代理循环、持久会话事件日志、**压缩** | — |
| Grok Build | 子代理 = 返回一份*摘要*的独立子会话；worktree 隔离；文件夹信任 | — |
| Codex | execpolicy 的 `allow/prompt/forbidden`、审批→修正、sandbox/exec 接缝 | 已经采纳；见 `PATTERN_ATTRIBUTION.md` |

Two of these are worth calling out as directly applicable rather than aspirational:

<!-- zh -->
其中有两条值得特别指出，它们是可直接应用的，而不是愿景式的：

**Grok's subagent model.** A child gets its own context window and returns a *summary*, not
its transcript. That is the same argument `ContextProjection` already makes — compiled
context belongs to one worker — applied to delegation, and it is what stops a parent's
window from accumulating every child's raw output. `DelegationContract` should return
`SubagentResult(summary, artifacts, claims, evidence)`.

<!-- zh -->
**Grok 的子代理模型。** 一个子代理获得自己的上下文窗口，并返回一份*摘要*，而不是它的对话记录。这与 `ContextProjection` 已经提出的论证是同一条——编译后的上下文属于单个工作进程——只不过应用到了委派上；正是它阻止了父级的窗口累积每一个子代理的原始输出。`DelegationContract` 应当返回 `SubagentResult(summary, artifacts, claims, evidence)`。

**Grok's folder trust.** Repository-local hooks, skills and instructions go through a trust
gate. This package governs runtime authority well and has no notion of *where a
configuration came from*. A `SourceTrust` axis — `SYSTEM` / `USER` / `TRUSTED_PROJECT` /
`UNTRUSTED_PROJECT` / `REMOTE` — would stop a cloned research repository's `AGENTS.md` from
influencing policy. That matters as much for shared data repositories as for code.

<!-- zh -->
**Grok 的文件夹信任。** 仓库本地的 hooks、skills 与 instructions 要经过一道信任门。本包在运行时权限上治理得很好，却完全没有"某个配置*来自哪里*"的概念。一个 `SourceTrust` 轴——`SYSTEM` / `USER` / `TRUSTED_PROJECT` / `UNTRUSTED_PROJECT` / `REMOTE`——会阻止一个克隆来的研究仓库的 `AGENTS.md` 影响策略。这对共享的数据仓库，与对代码同样重要。

And one to decline: **do not adopt another framework's runtime wholesale.** The reviews'
own conclusion is the right one — loop and multi-agent orchestration are commoditising,
while "a child's authority cannot float up, sensitive data cannot cross a boundary, a
scientific claim needs evidence, and the whole run is recoverable and auditable" is not.
That is the part worth keeping, and it is the part a drop-in runtime would replace.

<!-- zh -->
还有一条要拒绝：**不要整体采纳另一个框架的运行时。** 评审自己的结论就是对的——循环与多代理编排正在商品化，而"子代理的权限不能上浮，敏感数据不能跨越边界，一项科学主张需要证据，以及整次运行可恢复、可审计"并没有商品化。那才是值得保留的部分，也正是即插即用式运行时会替换掉的部分。

## 5. Convergence with BioScience-Harness · 5. 与 BioScience-Harness 的收敛

The two packages are complementary rather than competing:

<!-- zh -->
这两个包是互补的，而不是竞争的：

> **PSH** — can the agent do this? &nbsp;&nbsp;**BioScience** — what can the agent actually do?

<!-- zh -->
> **PSH** — 代理能不能做这件事？&nbsp;&nbsp;**BioScience** — 代理实际上能做什么？

The recommended shape, and the thing *not* to do:

<!-- zh -->
推荐的形态，以及*不*该做的事：

```
             Agent Runtime  (loop, planner, supervisor, workers)
                    |  proposes
         PSH TrustedKernel  (authority, labels, budget, approval, audit, evidence)
                    |  authorizes
    BioScience Capability Plane  (registry, resolver, backends, acquisition, evolution)
```

Do **not** merge the two `Runtime`s, the two `Policy` objects or the two `Manifest`s. Keep
PSH's kernel as the immutable base; keep BioScience's registry, resolver, backends,
acquisition and evolution pipeline as the capability layer; write the orchestration layer
once, on top. Concretely:

<!-- zh -->
**不要**合并两个 `Runtime`、两个 `Policy` 对象或两个 `Manifest`。把 PSH 的内核保留为不可变的基础；把 BioScience 的注册表、解析器、后端、获取与演化流水线保留为能力层（capability plane, CP）；编排层只写一次，写在其上。具体地说：

* **One policy, PSH's.** BioScience's genuinely distinct contribution there is not its
  permission model but its **licence and integration provenance** (`MIT`, `Apache-2.0`,
  `NOASSERTION`, `GPL`, vendor / native / federated). That belongs as a *new dimension* of
  the PSH policy — and `PolicyLattice` is now the place to add it, which is most of why it
  exists.

  **Done** (`psh/licensing.py`). It is the first convergence step and it was deliberately
  taken first, because it is also the test of whether the lattice work paid for itself.
  The dimension asks "is this licence acceptable **for this integration mode**", which a
  single allow/deny per licence cannot express: unlicensed code may be *invoked* and may
  not be *copied*. So it is a fixed table — the same shape as `labels.DEFAULT_CEILINGS` —
  plus two subset dimensions a profile narrows.

  The cost of adding it is the number worth recording. Naming it in
  `AuthorityLattice.violations`/`meet`, in `PolicyLattice`, and in the property test's
  dimension list. That is all. `restrict`, `with_`, the meet, delegation and `ToolGateway`
  govern it without being told, because each defers to the one predicate; the
  anti-vacuity test confirmed both new dimensions immediately got real coverage. Before
  v0.5.1 the same change would have meant editing four hand-written comparisons and
  hoping they agreed.

  It did surface one thing: `WorkProfile.freeze()` did not carry the new fields, so a
  profile could declare a licence posture no gate would see. That is the *third* appearance
  of one defect — v0.1 dropped `require_citation` between the profile and the output gate,
  and `freeze()` exists because of it. A hand-written mapping that nothing checks is total
  will keep doing this, so `test_every_profile_field_reaches_the_snapshot` now checks it.
* **Both audit stores, not one.** PSH's hash chain is security truth; BioScience's causal
  event DAG is scientific provenance. They answer different questions and should not be
  flattened into one table.
* **Self-evolution, quarantined.** BioScience's propose → smoke → benchmark → regression →
  promote pipeline is the most forward-looking thing in either package, and under PSH it
  needs one hard boundary: an evolution agent may write `components/`, `skills/`,
  `prompts/`, `planners/` and may **never** write `kernel/`, `authority/`, the policy
  evaluator, the audit chain, the release gate or the sandbox. A component that can modify
  the thing enforcing policy is not governed by it — which is `contracts.py`'s opening
  argument, applied to the agent that edits the repository.

  **Done** (`bioagent.evolution.boundary`). `EvolutionPipeline.submit()` has a `boundary`
  stage that quarantines a proposal whose entrypoint, source path or declared writes land
  in the trusted plane, before its smoke test runs.

* **The bridge itself** — **done** (`bioagent.psh`, BioScience v2.4; design in
  `BioScience-Harness/docs/V24_PSH_CONVERGENCE.md`). A BioScience manifest becomes a PSH
  manifest with the gate dimensions derived conservatively; a call crosses both kernels
  in order; one harness per domain sits at the top of this package's two-level registry;
  `isolate=True` runs components in this kernel's child process. The dependency points one
  way — `bioagent.psh` imports `psh`, never the reverse, and never `psh.kernel` — and this
  package gained nothing but three data-licence ids in `licensing.py`.

<!-- zh -->
* **一套策略，用 PSH 的。** BioScience 在那里真正独特的贡献不是它的权限模型，而是它的**许可证与集成溯源**（`MIT`、`Apache-2.0`、`NOASSERTION`、`GPL`、vendor / native / federated）。那应当作为 PSH 策略的一个*新维度*——而 `PolicyLattice` 现在正是添加它的地方，这也正是它存在的主要理由。

  **已完成**（`psh/licensing.py`）。这是收敛的第一步，而且是有意先做的，因为它同时也是"权限格这项工作是否值回票价"的检验。这个维度问的是"该许可证**对于这种集成模式**是否可接受"，而"一个许可证一个允许/拒绝"的写法表达不了这一点：无许可证的代码可以被*调用*，不可以被*复制*。所以它是一张固定表——与 `labels.DEFAULT_CEILINGS` 同一形状——外加一个 profile 可以收窄的两个子集维度。

  添加它的成本才是值得记下的数字。在 `AuthorityLattice.violations`/`meet` 中、在 `PolicyLattice` 中、以及在性质测试的维度列表里为它命名。就这些。`restrict`、`with_`、meet、委派与 `ToolGateway` 在无人告知的情况下就治理它，因为每一个都服从那一个谓词；反空泛（anti-vacuity）测试证实两个新维度立刻获得了真实覆盖。在 v0.5.1 之前，同样的改动意味着要编辑四处手写的比较，并指望它们彼此一致。

  它确实暴露了一件事：`WorkProfile.freeze()` 没有携带新字段，因此一个 profile 可以声明一种没有任何门会看到的许可证姿态。这是同一个缺陷的*第三次*出现——v0.1 在 profile 与输出门之间丢掉了 `require_citation`，而 `freeze()` 正因它而存在。一个没人检查、却声称是全量的手写映射会一直这样干下去，所以 `test_every_profile_field_reaches_the_snapshot` 现在会检查它。
* **两个审计存储都要，不是一个。** PSH 的哈希链是安全真相；BioScience 的因果事件 DAG 是科学溯源。它们回答的是不同的问题，不应当被压平成一张表。
* **自我演化，置于隔离区。** BioScience 的 propose → smoke → benchmark → regression → promote 流水线是两个包里最有前瞻性的东西，而在 PSH 之下它需要一条硬边界：一个演化代理可以写 `components/`、`skills/`、`prompts/`、`planners/`，并且**绝不**可以写 `kernel/`、`authority/`、策略评估器、审计链、发布门或沙箱。一个能修改"执行策略的那个东西"的组件，不受它的治理——这正是 `contracts.py` 开篇的论证，只不过应用到了那个编辑仓库的代理身上。

  **已完成**（`bioagent.evolution.boundary`）。`EvolutionPipeline.submit()` 有一个 `boundary` 阶段，会在一个提案的 smoke 测试运行之前，把入口点、源路径或声明的写入落在可信执行平面（trusted execution plane, TEP）之内的提案隔离掉。

* **桥接本身**——**已完成**（`bioagent.psh`，BioScience v2.4；设计见 `BioScience-Harness/docs/V24_PSH_CONVERGENCE.md`）。一份 BioScience 清单变成一份 PSH 清单，门维度按保守方式推导；一次调用按顺序穿过两个内核；每个领域一个 harness 位于本包两级注册表的顶端；`isolate=True` 在本内核的子进程中运行组件。依赖是单向的——`bioagent.psh` 导入 `psh`，绝不反过来，也绝不导入 `psh.kernel`——而本包除了 `licensing.py` 里的三个数据许可证 id 之外什么也没得到。

BioScience's test suite was reported as 133 passed / 3 failed / 6 skipped; the
container-validation ordering bug was closed in its v2.3.1 before convergence started,
and the BioScience unit tier stands at 543 passed / 2 skipped with the bridge, native-toolkit and dataset tests included.

<!-- zh -->
BioScience 的测试套件据报告为 133 passed / 3 failed / 6 skipped；容器校验顺序的 bug 在收敛开始之前已在其 v2.3.1 中关闭，而 BioScience 的单元层级目前是 543 passed / 2 skipped，其中包含桥接、native-toolkit 与数据集测试。

## 6. Naming the honest state · 6. 说出诚实的状态

v0.5.1 is:

<!-- zh -->
v0.5.1 是：

> a **policy-enforced control plane** for biomedical scientific agents, with a **bounded,
> governed single-agent loop** on top of it.

<!-- zh -->
> 一个面向生物医学科学代理的**策略强制的控制平面**，其之上是一个**有界、受治理的单代理循环**。

It is not a multi-agent operating system, and the row in §1 that reads "Supervisor / worker
pool ❌" is the reason. The target the reviews describe — *typed planner → task graph →
loop controller → supervisor → delegation contract → worker pool → observation → evaluator
→ retry/replan → fan-in → evidence verification → release* — is reachable from here, and
§3 is the order to reach it in. What should not happen is the reverse: claiming the
destination while the middle rows are empty. That is the failure mode both reviews were
written to catch, and it has now cost two releases to correct.

<!-- zh -->
它不是一个多代理操作系统，而 §1 中那一行"Supervisor / worker pool ❌"正是原因。评审所描述的目标——*类型化规划器 → 任务图 → 循环控制器 → supervisor → 委派契约 → 工作池 → 观察 → 评估器 → 重试/重规划 → 扇入 → 证据核验 → 发布*——从这里是可以抵达的，而 §3 就是抵达它的顺序。不应当发生的是相反的情况：中间那些行还是空的，却宣称已经到达终点。那正是两次评审被写出来要抓的失败模式，而它现在已经耗费了两个版本来纠正。

## 7. v0.5.3 — after the third review · 7. v0.5.3 — 第三次评审之后

The 2026-09-18 review measured the loop against the single pass and found the loop
short: its result never met the release gate, its checkpoints wrote what its envelope
forbade, its tasks could not pass a value to each other except as a blob, its budget did
not see its own tasks, its evaluation accepted "banana", and its retrieval could not read
Chinese. `docs/REVIEW_RESPONSE_2026-09-18.md` is the finding-by-finding record.

<!-- zh -->
2026-09-18 的评审把循环与单次执行相比对，发现循环不合格：它的结果从未经过发布门，它的检查点写下了它的信封所禁止的东西，它的任务除了以 blob 的形式之外无法相互传递值，它的预算看不到它自己的任务，它的评估接受了"banana"，而它的检索读不了中文。`docs/REVIEW_RESPONSE_2026-09-18.md` 是逐条发现的记录。

The order of work was the review's: the P0 batch (one release path, inherited labels,
redacted checkpoints, Chinese PHI cues), then the execution-closure batch (input
bindings, the budget tree, strict evaluation, the operation ledger, bilingual terms),
then the foundations (the isolation report, the TCM knowledge layer, the doctor). Each
batch landed with its probe reproduced first and its regression tests second.

<!-- zh -->
工作顺序是评审给出的：P0 批次（一条发布路径、继承的标签、脱敏的检查点、中文 PHI 线索），然后是执行闭合批次（输入绑定、预算树、严格评估、操作账本、双语术语），再然后是地基（隔离报告、中医知识层、doctor）。每个批次落地时，都先复现其探针，其次才写回归测试。

What §6 said still holds, with one line added: the runtime is a governed loop *whose
deliverable is judged by the same policy as a single pass*, and whose restarts know what
they did. It is still not a multi-tenant system and still ships no OS sandbox.

<!-- zh -->
§6 说过的话仍然成立，只加一句：本运行时是一个受治理的循环，*其交付物由与单次执行相同的策略来评判*，并且它的重启知道自己做过什么。它仍然不是一个多租户系统，也仍然不附带任何 OS 沙箱。
