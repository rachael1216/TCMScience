# Scientific workflow compiler — first increment · 科学工作流编译器 —— 第一个增量

`psh.workflow` adds a versioned scientific contract layer above the existing PSH
`Plan`, validator, execution loop and gateways. It implements a bounded first
increment of the Scientific IR proposal. It does **not** implement the complete
autonomous scientist architecture or establish superiority over another system.

<!-- zh -->
`psh.workflow` 在现有 PSH 的 `Plan`、校验器、执行循环与网关之上，增加了一个带版本的科学契约层。它实现了科学 IR（Scientific IR）提案中一个有界的第一个增量。它**并不**实现完整的自主科学家架构，也不确立对另一个系统的优越性。


## Run the offline example · 运行离线示例

From `PSH-Harness`, with Python 3.11 or newer:

<!-- zh -->
在 `PSH-Harness` 目录下，使用 Python 3.11 或更新版本：

```sh
python -m pip install -e ".[test]"
python examples/scientific_workflow.py
python -m pytest -q tests/test_scientific_workflow.py
```

The example uses synthetic metadata. It prints the compiled task order, content
fingerprint, incremental invalidation report, and an expected refusal to use
animal evidence for a clinical efficacy claim. It makes no biomedical assertions,
contacts no provider and executes no research tools.

<!-- zh -->
该示例使用合成元数据。它打印编译后的任务顺序、内容指纹、增量失效报告，以及一次预期中的拒绝：用动物证据支撑临床疗效主张。它不作任何生物医学断言、不联系任何提供方、不执行任何研究工具。


## Representation and execution · 表示与执行

For model-generated programs with bounded compilation/repair, see
[ScientificModelPlanner](SCIENTIFIC_MODEL_PLANNER.md). The existing
ScientificPlanner remains an adapter for caller-supplied programs.

<!-- zh -->
关于带界编译/修复的模型生成程序，见 [ScientificModelPlanner](SCIENTIFIC_MODEL_PLANNER.md)。现有的 ScientificPlanner 仍然是一个面向调用者提供程序的适配器。


`ScientificProgram(schema_version=1, plan=..., contracts=...)` wraps a `Plan` with
exactly one `TaskContract` per task. Existing plain plans continue to work.
Python construction and `to_dict()` / `from_dict()` provide the Python and JSON
frontends. Unknown scientific fields, versions and enum values are rejected;
unknown fields in embedded plans are also rejected by this frontend. This is a
declarative DAG representation, not an arbitrary Python script interpreter.

<!-- zh -->
`ScientificProgram(schema_version=1, plan=..., contracts=...)` 用一个 `Plan` 加上每个任务恰好一份 `TaskContract` 进行包装。现有的普通计划继续可用。Python 构造与 `to_dict()` / `from_dict()` 分别提供 Python 与 JSON 前端。未知的科学字段、版本与枚举值会被拒绝；内嵌计划中的未知字段也会被此前端拒绝。这是一种声明式 DAG 表示，不是任意 Python 脚本解释器。


Each contract describes a sensitivity floor, explicit effects, a side-effect
class, optional evidence/claim refinements, and optional
[statistical design checks](STATISTICAL_DESIGN.md). Task IDs and provenance references
must be opaque identifiers, not patient names or other sensitive content.

<!-- zh -->
每份契约描述一个敏感度下限（sensitivity floor）、显式效果（effect）、一个副作用类别，以及可选的证据/主张细化项与可选的[统计设计检查](STATISTICAL_DESIGN.md)。任务 ID 与溯源引用必须是不透明标识符，不能是患者姓名或其他敏感内容。


When an explicit `policy` is supplied, the compiler first intersects the run
envelope with `policy.ceiling()` using the shared AuthorityLattice. This prevents
an older, broader envelope from overriding tightened destinations, data ceilings,
budgets or other authority dimensions. Both validation passes and registered
protocol reads use that effective envelope. A broader policy never widens an
already restricted run. Tasks still inside the new limits may compile; tasks
that require revoked authority are refused. Without `policy`, the supplied run
envelope remains the authority boundary. This does not discover policy updates:
callers must supply the current snapshot (or update `ScientificPlanner.policy`).
Normal runtime admission and release checks remain mandatory.

<!-- zh -->
当提供了显式 `policy` 时，编译器首先用共享的 AuthorityLattice 将运行信封与 `policy.ceiling()` 求交。这防止一个更早、更宽的信封覆盖已收紧的目的地、数据上限、预算或其他权限维度。两轮校验与已注册协议的读取都使用那个生效信封。更宽的策略永远不会放宽一次已经受限的运行。仍在新限制之内的任务可以编译；需要已被撤销权限的任务会被拒绝。没有 `policy` 时，所提供的运行信封仍然是权限边界。这不会发现策略更新：调用者必须提供当前快照（或更新 `ScientificPlanner.policy`）。正常的运行时准入与发布检查仍然强制。


The compiler:

<!-- zh -->
编译器：

1. Takes an independent JSON snapshot and rejects non-finite/non-JSON values.
2. Runs the existing graph, authority, binding, budget and acceptance checks.
3. Propagates sensitivity in topological order through **all** dependencies.
4. Checks effects against explicit destinations and propagated labels.
5. Rejects unsafe automatic retry declarations and negative resource estimates.
6. Checks evidence design and population/intervention/outcome refinements.
7. Lowers the propagated labels into `PlanTask.input_sensitivity`, validates the
   resulting plan, and returns a `Compilation` with per-task content hashes.

<!-- zh -->
1. 取一份独立的 JSON 快照，并拒绝非有限/非 JSON 取值。
2. 运行现有的图、权限、绑定、预算与验收检查。
3. 按拓扑顺序让敏感度穿过**所有**依赖传播。
4. 对照显式目的地与传播后的标签检查效果。
5. 拒绝不安全的自动重试声明与负的资源估计。
6. 检查证据设计以及人群/干预/结局细化项。
7. 把传播后的标签降入 `PlanTask.input_sensitivity`，校验生成的计划，并返回一个带逐任务内容哈希的 `Compilation`。


`PlanTask.max_label` remains a ceiling; `input_sensitivity` is a floor. A task
cannot relabel a PHI-derived count as PUBLIC by omitting identifiers from its text.
The floor is joined at model, tool and delegation dispatch, serialized with the
plan, and included when comparing redacted checkpoint shapes. Old serialized
plans default to PUBLIC for this new field. Ordinary runtime ingress can still
raise sensitivity above the compiler's declaration.

<!-- zh -->
`PlanTask.max_label` 仍然是上限；`input_sensitivity` 是下限。一个任务不能通过在文本中省略标识符，就把一个由 PHI 推导出的计数重新标记为 PUBLIC。该下限在模型、工具与委派分发时被求并，随计划一起序列化，并在比较脱敏后的检查点形状时被纳入。对旧有的已序列化计划，这个新字段默认为 PUBLIC。普通的运行时入口仍然可以把敏感度抬升到编译器的声明之上。


To execute through the existing runtime:

<!-- zh -->
通过现有运行时执行：


```python
from psh.runtime import AgentLoopController
from psh.workflow import ScientificPlanner

loop = AgentLoopController(
    kernel,
    planner=ScientificPlanner(program, registry=registry, policy=kernel.policy),
    registry=registry,
    model=model_profile,
    model_invoke=model_invoke,
)
result = loop.run(program.plan.objective, kernel.policy.envelope())
```

`kernel`, `registry`, `model_profile`, and `model_invoke` are the application's
existing PSH components. The adapter compiles under the actual run envelope and
includes any inherited objective/plan label. The controller still invokes its
normal validator and broker. Evidence declarations do not bypass output release,
quarantine, evidence verification or memory admission.

<!-- zh -->
`kernel`、`registry`、`model_profile` 与 `model_invoke` 是应用已有的 PSH 组件。该适配器在实际运行信封之下编译，并纳入任何继承而来的目标/计划标签。控制器仍会调用它常规的校验器与 broker。证据声明不会绕过输出发布、隔离区、证据校验或记忆准入。


## Scientific scope checks · 科学范围检查

The initial conservative support matrix is:

<!-- zh -->
初始的保守支撑矩阵是：


| Claim | Admissible declared designs |
| --- | --- |
| Classical attribution | Classical text |
| Traditional use | Classical text, expert consensus |
| Mechanism | In vitro, animal |
| Association | Observational, randomized trial |
| Clinical efficacy | Randomized trial |
| Safety signal | Case report, observational, randomized trial |

<!-- zh -->
| 主张 | 可采信的已声明设计 |
| --- | --- |
| 经典归属 | 经典文本 |
| 传统用途 | 经典文本、专家共识 |
| 机制 | 体外（in vitro）、动物 |
| 关联 | 观察性研究、随机试验 |
| 临床疗效 | 随机试验 |
| 安全性信号 | 病例报告、观察性研究、随机试验 |


A systematic review inherits the designs of its underlying studies. A review
of animal experiments does not become clinical evidence. Mixed reviews fail when
any declared underlying design cannot support the claim; split the evidence into
appropriate scoped records if necessary. These are software admission rules for
this prototype, not a universal clinical evidence grading guideline.

<!-- zh -->
系统综述继承其底层研究的设计。一篇关于动物实验的系统综述不会因此成为临床证据。当任何已声明的底层设计无法支撑该主张时，混合型综述即失败；必要时请把证据拆分为恰当限定范围的记录。这些是本原型的软件准入规则，不是普适的临床证据分级指南。


Every evidence-producing task must supply nonempty provenance references.
Every claim must name direct dependency tasks that declare evidence. Population,
intervention and outcome must match after trimming whitespace and case folding.
There is no fuzzy synonym matching or automatic population generalization.
Evidence IDs and references survive serialization and participate in hashes.

<!-- zh -->
每一个产生证据的任务都必须提供非空的溯源引用。每一条主张都必须指名声明了证据的直接依赖任务。人群、干预与结局在去除空白与大小写折叠之后必须匹配。没有模糊同义词匹配，也没有自动的人群泛化。证据 ID 与引用在序列化之后仍然保留，并参与哈希计算。


This checks **declared compatibility**, not whether a cited source exists, whether
the source says what the declaration claims, bias, certainty, causality or whether
the planned task actually returns adequate evidence. An RCT declaration is not
proof of efficacy. Runtime evidence verification remains necessary. This layer
does not generate or release scientific prose.

<!-- zh -->
这检查的是**已声明的相容性**，而不是被引用的来源是否存在、来源是否说了声明所声称的内容，也不是偏倚（bias）、确定性、因果性，或所计划的任务是否真的返回了充分的证据。一份 RCT 声明不是疗效的证明。运行时的证据校验仍然必要。这一层不生成也不发布科学文本。


## Effects and retry semantics · 效果与重试语义

Effects map to existing PSH destinations: local read/compute, local model,
trusted/public remote, persistence and user output. Effects must cover exactly the
task's explicit destinations. They do not grant authority, inspect generated code,
restrict raw sockets or attest third-party tool behavior.

<!-- zh -->
效果映射到现有的 PSH 目的地：本地读取/计算、本地模型、可信/公共远程、持久化与用户输出。效果必须恰好覆盖该任务的显式目的地。它们不授予权限、不检查生成的代码、不限制裸套接字，也不为第三方工具的行为作证。


Automatic retries are admitted only for declared `pure`, `idempotent` or
`at_least_once` work. `at_most_once`, `non_repeatable` and `compensatable` work
cannot request automatic retries; a compensation declaration alone cannot make a
retry safe. PURE excludes remote calls, persistence and user output. This is a
preflight check: runtime tool manifests and the existing operation ledger still
determine whether an interrupted operation can safely be dispatched again.

<!-- zh -->
自动重试只对声明为 `pure`、`idempotent` 或 `at_least_once` 的工作开放。`at_most_once`、`non_repeatable` 与 `compensatable` 的工作不能请求自动重试；仅有一份补偿声明不足以让重试变得安全。PURE 排除远程调用、持久化与用户输出。这是一次预检：运行时工具清单与现有的操作账本仍然决定一个被中断的操作能否被安全地再次分发。


## Amendment analysis · 修正分析

`assess_amendment(old, new, envelope, completed=...)` validates both versions
under current authority, compares task contracts/payloads and their recursive
dependency fingerprints, and returns:

<!-- zh -->
`assess_amendment(old, new, envelope, completed=...)` 在当前权限之下校验两个版本，比较任务契约/载荷及其递归依赖指纹，并返回：


- `invalidated`: added or changed tasks and their affected descendants;
- `removed`: IDs present only in the previous program;
- `reusable_candidates`: unchanged, completed PURE tasks.

<!-- zh -->
- `invalidated`：新增或被更改的任务及其受影响的后代；
- `removed`：仅存在于前一程序中的 ID；
- `reusable_candidates`：未变更且已完成的 PURE 任务。


Changing the research objective, assumptions, acceptance criteria or other
plan-level context invalidates all tasks. Changing only `plan_id` does not.
Provenance changes invalidate downstream findings. Unknown completed IDs fail.
This API does not load results, mutate a running loop, or automatically reuse a
checkpoint. Candidate reuse still needs verified result digests, current policy
and labels, and matching model/tool/environment identity. Fingerprints identify
declared program content, not reproducible execution environments or signatures.

<!-- zh -->
更改研究目标、假设、验收标准或其他计划层级的上下文，会使所有任务失效。仅更改 `plan_id` 不会。溯源变更会使下游发现失效。未知的已完成 ID 会失败。此 API 不加载结果、不修改正在运行的循环，也不自动复用检查点。候选复用仍然需要已验证的结果摘要、当前策略与标签，以及相匹配的模型/工具/环境身份。指纹标识的是已声明的程序内容，而不是可复现的执行环境或签名。


## Coverage and remaining roadmap · 覆盖范围与剩余路线图

The test module includes unsafe scientific trajectories, transitive PHI flow,
arbitrary-chain label monotonicity, unsafe retries, amendment propagation,
serialization, checkpoint label shape, and execution through the real broker.
The repository's existing PSH CI automatically collects these tests.

<!-- zh -->
测试模块涵盖不安全的科学轨迹、传递性 PHI 流动、任意链路标签单调性、不安全重试、修正传播、序列化、检查点标签形状，以及通过真实 broker 的执行。仓库现有的 PSH CI 会自动收集这些测试。


| Proposal area | State after this increment |
| --- | --- |
| Scientific IR and compiler | Versioned task/evidence/claim contracts; Python/JSON frontend |
| Types, effects, information/evidence flow | Conservative declared-scope checks; runtime sensitivity floors |
| Workflow amendment | Change impact analysis; no live amendment or automatic cache reuse |
| Durable execution | Optional [checkpoint journal](DURABLE_CHECKPOINT_JOURNAL.md) and atomic operation admission; no per-event replay engine |
| Scientific World Model | Typed hypothesis/protocol/observation records and explicit deviations; see [scientific records](SCIENTIFIC_RECORDS.md). Competition remains future work |
| Statistical/causal compiler | Not implemented; resource checks are not statistical validation |
| Dynamic branches, loops, fan-out | Not implemented by this frontend |
| OS sandbox, network broker, secrets | Existing controls retained; no stronger isolation claim |
| PROV/RO-Crate, CWL/WES/TES | Future interoperability work |
| Capability evolution and Research Cockpit | Future work |

<!-- zh -->
| 提案领域 | 本增量之后的状态 |
| --- | --- |
| 科学 IR 与编译器 | 带版本的任务/证据/主张契约；Python/JSON 前端 |
| 类型、效果、信息/证据流 | 保守的已声明范围检查；运行时敏感度下限 |
| 工作流修正 | 变更影响分析；没有实时修正或自动缓存复用 |
| 持久执行 | 可选的[检查点日志](DURABLE_CHECKPOINT_JOURNAL.md)与原子操作准入；没有逐事件重放引擎 |
| 科学世界模型（Scientific World Model） | 有类型的假设/方案/观察记录与显式偏离；见[科学记录](SCIENTIFIC_RECORDS.md)。竞争仍是未来工作 |
| 统计/因果编译器 | 未实现；资源检查不是统计验证 |
| 动态分支、循环、扇出 | 此前端未实现 |
| 操作系统沙箱、网络 broker、机密（secrets） | 保留现有控制；不作更强的隔离声明 |
| PROV/RO-Crate、CWL/WES/TES | 未来的互操作性工作 |
| 能力演化与 Research Cockpit | 未来工作 |


Next implementation milestones should add pinned capability/environment manifests
before automatic reuse, evidence-preserving durable replay with fault injection,
and hypothesis competition over the typed scientific records.
Each milestone needs its own runtime integration and trajectory tests. This code
is an original implementation against PSH interfaces; no ZCode source was copied
or reviewed for this increment.

<!-- zh -->
接下来的实现里程碑应当在自动复用之前加入钉定的能力/环境清单、带故障注入的保全证据的持久重放，以及在有类型的科学记录之上的假设竞争。每个里程碑都需要各自的运行时集成与轨迹测试。这些代码是针对 PSH 接口的原创实现；本次增量没有复制或审阅任何 ZCode 源码。

