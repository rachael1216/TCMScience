# Bounded dynamic workflows and stage-event replay · 有界动态工作流与阶段事件重放

Applications should use `psh.workflow.DynamicResearchRunService` to execute
caller-authored graphs of `ScientificProgram` stages and obtain gated output.
`DynamicWorkflowController` and `DynamicResult` are internal execution interfaces,
retained as imports for compatibility. This is a bounded dynamic increment, not
the complete ZCode dynamic-workflow engine or a replacement for task checkpoints.

<!-- zh -->
应用应当使用 `psh.workflow.DynamicResearchRunService` 来执行由调用者编写的、由 `ScientificProgram` 阶段构成的图，并获得经过门的输出。`DynamicWorkflowController` 与 `DynamicResult` 是内部执行接口，为兼容性而保留为可导入项。这是一个有界的动态增量，不是完整的 ZCode 动态工作流引擎，也不是任务检查点的替代。


## Execution model · 执行模型

- `WorkflowStage` contains a scientific program and either `next_stage` or a
  `Branch`. `None` is the terminal edge.
- `Branch` reads a task result in the just-completed stage, optionally through
  the existing runtime JSON-pointer resolver. A type-strict JSON-scalar equality
  selects `if_true` or `if_false`. No `eval`, model-authored Python or routing
  callback is executed. Missing paths fail routing instead of silently choosing.
  Model results are strings; JSON text is **not** implicitly parsed into objects.
- Cycles are explicit. `max_visits` bounds total stage executions (1–1024),
  including repeats. A graph holds at most 256 stages. Each stage also obeys
  `LoopLimits`; implicit replanning is disabled. Cancellation is cooperative.
- Each stage remains an ordinary DAG: existing bounded independent-task
  concurrency and dependency joins work via `LoopLimits(max_parallel=...)`.
  Dynamic map expansion, live graph amendment and
  model generation of dynamic graphs are not implemented by this adapter.
- Stage inputs can pass selected fields from the immediately preceding successful
  visit into tool arguments. They do not read an arbitrary historical stage or
  inject context into model/delegate tasks; see the binding example below.
- The input graph is JSON-snapshotted before execution. All declared stages,
  even unselected ones, pass scientific preflight before dispatch. Selected
  stages compile again against current policy. Registered-protocol checks use
  the supplied `scientific_ledger`, as with `ScientificPlanner`.
- All visits retain the original envelope's run ID, budget and deadline. Tool
  operation keys gain a workflow-instance/visit namespace, so two intentionally
  repeated stage visits do not collide in `OperationLedger`. A repeated visit is
  a **new invocation**, not a retry or cache hit. Cyclic tool stages now also pass
  the repeat-safety checks below. Existing task retry restrictions remain.

<!-- zh -->
- `WorkflowStage` 包含一个科学程序，以及 `next_stage` 或一个 `Branch`。`None` 是终止边。
- `Branch` 读取刚完成阶段中某个任务的结果，可选地经由现有的运行时 JSON 指针解析器。一个类型严格的 JSON 标量相等比较选出 `if_true` 或 `if_false`。不执行 `eval`、不执行模型编写的 Python、也不执行路由回调。路径缺失会导致路由失败，而不是静默选择。模型结果是字符串；JSON 文本**不会**被隐式解析为对象。
- 循环是显式的。`max_visits` 限定阶段执行总次数（1–1024），包括重复。一个图最多有 256 个阶段。每个阶段也遵守 `LoopLimits`；隐式重规划被禁用。取消是协作式的。
- 每个阶段仍然是一个普通 DAG：既有的有界独立任务并发与依赖汇合通过 `LoopLimits(max_parallel=...)` 生效。动态映射展开、实时图修正与由模型生成动态图，此适配器均未实现。
- 阶段输入可以把紧邻的前一次成功访问中所选字段传入工具参数。它们不读取任意历史阶段，也不向模型/委派任务注入上下文；见下面的绑定示例。
- 输入图在执行之前被 JSON 快照。所有已声明的阶段，即使未被选中，也都会在分发前通过科学预检。被选中的阶段会对照当前策略再次编译。已注册协议检查使用所提供的 `scientific_ledger`，与 `ScientificPlanner` 相同。
- 所有访问都保留原信封的运行 ID、预算与截止时间。工具操作键获得一个工作流实例/访问命名空间，因此两次有意重复的阶段访问不会在 `OperationLedger` 中碰撞。一次重复访问是一个**新调用**，不是重试，也不是缓存命中。循环工具阶段现在也通过下面的重复安全检查。既有的任务重试限制仍然有效。


## Cycle admission and changing capability facts · 循环准入与变化中的能力事实

Before any stage dispatch or journal start, a Tarjan strongly-connected-component
pass identifies every declared cycle, including self-loops and both branch edges.
Declared unreachable cycles are included, even when `max_visits=1`; the pass is
deliberately conservative and does not try to prove predicates impossible.

<!-- zh -->
在任何阶段分发或日志启动之前，一次 Tarjan 强连通分量（strongly-connected-component）遍历会识别出每一个已声明的循环，包括自环与两条分支边。已声明但不可达的循环也被纳入，即使 `max_visits=1`；该遍历是有意保守的，不会去尝试证明某个谓词不可能成立。


For each tool task in a cyclic stage:

<!-- zh -->
对于一个循环阶段中的每个工具任务：


- `NON_REPEATABLE`, `AT_MOST_ONCE` and `COMPENSATABLE` contracts are rejected
  (`REPEAT101`). No compensation executor or per-visit approval exception exists.
- The registry manifest **and** the currently invocable component must report
  exactly `idempotent=True` (`REPEAT102`). A planner's PURE/IDEMPOTENT declaration
  is insufficient. Missing or merely truthy facts do not grant permission.
- `PURE`, `IDEMPOTENT` or `AT_LEAST_ONCE` contracts may pass only with that trusted
  fact and all existing scientific compilation, authority and budget checks.

<!-- zh -->
- `NON_REPEATABLE`、`AT_MOST_ONCE` 与 `COMPENSATABLE` 契约被拒绝（`REPEAT101`）。不存在补偿执行器，也不存在按访问的审批例外。
- 注册表清单**以及**当前可调用的组件都必须报告恰好 `idempotent=True`（`REPEAT102`）。规划器的 PURE/IDEMPOTENT 声明不充分。缺失的或仅仅是真值的（truthy）事实不授予许可。
- `PURE`、`IDEMPOTENT` 或 `AT_LEAST_ONCE` 契约只有在具备该可信事实、并通过所有既有科学编译、权限与预算检查时才能通过。


Delegation in cycles is rejected (`REPEAT103`): this controller has no repeat-safety
attestation for a delegated workflow. Ordinary model stages remain available in
bounded cycles under model budget/egress gates; this does not declare model calls
pure, reproducible or free of provider-side effects.

<!-- zh -->
循环中的委派被拒绝（`REPEAT103`）：该控制器没有对委派工作流的重复安全见证。普通模型阶段在有界循环中仍然可用，受模型预算/出口门约束；这并不声明模型调用是纯的、可复现的，或不含提供方侧副作用。


Checks run again before every cyclic stage visit. If capability facts no longer
permit repetition, the controller writes `ended(reason="repeat_refused")` before
starting that visit. During a cyclic stage, the existing loop also requires the
actual component's idempotency fact at each tool dispatch, so a downgrade between
two tasks cannot authorize the later tool just because preflight passed earlier.
A within-stage refusal produces the ordinary `stage_failed` outcome. These checks
assume trusted host-controlled registries; they are not an atomic registry version
pin or protection against malicious concurrent component replacement.

<!-- zh -->
在每一次循环阶段访问之前都会再次运行检查。若能力事实不再允许重复，控制器会在开始该次访问之前写入 `ended(reason="repeat_refused")`。在一个循环阶段内，现有循环在每次工具分发时也要求实际组件的幂等事实，因此两个任务之间的降级不能仅因为预检早先通过就为该后一个工具授权。阶段内的拒绝产生普通的 `stage_failed` 结局。这些检查假设注册表由可信主机控制；它们不是一个原子的注册表版本钉定，也不是对恶意并发组件替换的防护。


Nonrepeatable tools in acyclic stages still execute once normally. This pass does
not detect manually unrolled duplicate operations in separate acyclic stages or
different runs, and it does not prove a trusted manifest truthful. Idempotency
alone does not prove purity, clinical suitability or safety for different payloads.
There is no bypass flag for unsafe cyclic tools in this release. Future approved
repetition requires explicit host authority, bounded repeat policy and durable
effect tracking, not an extra model-authored field.

<!-- zh -->
无环阶段中的不可重复工具仍然正常执行一次。这次遍历不检测分散在不同无环阶段或不同运行中、被手工展开的重复操作，也不证明一份可信清单是诚实的。幂等性本身不证明纯度、临床适用性或对不同载荷的安全性。本次发布没有为不安全的循环工具提供绕过开关。未来对重复的批准需要明确的主机权限、有界的重复策略与持久的效果跟踪，而不是一个额外的模型编写字段。


New `repeat_refused` events require updated readers; older readers fail closed on
the unknown reason. Existing accepted history and workflow fingerprints are not
rewritten. Some previously accepted cyclic programs are now intentionally refused.

<!-- zh -->
新的 `repeat_refused` 事件需要更新后的读取器；更旧的读取器在遇到这个未知原因时会失败即关闭（fail closed）。既有的已接受历史与工作流指纹不会被改写。一些先前被接受的循环程序现在被有意拒绝。


## Usage · 用法

Here `screen_program` and `review_program` are previously constructed
`ScientificProgram` objects; `screen_program` includes a task named `decision`.

<!-- zh -->
这里 `screen_program` 与 `review_program` 是先前构造好的 `ScientificProgram` 对象；`screen_program` 包含一个名为 `decision` 的任务。


```python
from psh.workflow import (
    Branch, WorkflowStage, DynamicWorkflow, DynamicResearchRunService,
    RunEventJournal,
)

workflow = DynamicWorkflow(
    stages=(
        WorkflowStage("screen", screen_program,
            branch=Branch("decision", "review", "review", None)),
        WorkflowStage("review", review_program),
    ),
    entry="screen",
    max_visits=4,
)

# Use a NEW database for each execution. The path is chosen by the application,
# never by model-generated program content.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    service = DynamicResearchRunService(
        kernel, journal=journal, registry=registry,
        scientific_ledger=scientific_ledger,
        model=local_model_profile, model_invoke=invoke_local_model,
        operations=operation_ledger,
    )
    result = service.run(workflow, envelope=envelope, sources=authorized_sources)
    if result.ok:
        print(result.released_output)  # only this field carries released text
    anchor = journal.anchor()  # retain separately for suffix-loss detection

# Inspection after reopening: this does NOT invoke any model or tool.
with RunEventJournal("E:/Codex/TCMScience/runs/example/events.sqlite") as journal:
    state = journal.replay(expected_anchor=anchor,
                           fingerprint=workflow.fingerprint)
    print(state.phase, state.visits, state.reason, state.in_doubt)
```

`authorized_sources` is a caller-supplied mapping of citation identifiers to
retrieved evidence records; `{}` is valid when no evidence is supplied. This service
revalidates records and passes only trusted records to the finalizer. Bare text and
unsigned/tampered records cannot serve as release evidence through this entry.
This is stricter than the shared output gate's legacy provenance-caveat allowance.

<!-- zh -->
`authorized_sources` 是调用者提供的、从引用标识符到已检索证据记录的映射；当不提供证据时，`{}` 是合法的。该服务会重新校验记录，只把可信记录交给 finalizer。裸文本与未签名/被篡改的记录不能通过这个入口充当发布证据。这比共享输出门对遗留溯源保留意见的容许更严格。


## Application release boundary · 应用发布边界

`DynamicResearchRunService.run()` returns `ReleasedResult`, never `DynamicResult`
or stage result dictionaries. It runs the existing Finalizer: quarantine, evidence
verification, OutputGate, release and optional verified-claim persistence. Only
the final visited stage's terminal tasks are rendered as the candidate. There is
no extra model synthesis call; authors must explicitly provide a final reporting
stage when earlier findings need synthesis.

<!-- zh -->
`DynamicResearchRunService.run()` 返回 `ReleasedResult`，从不返回 `DynamicResult` 或阶段结果字典。它运行现有的 Finalizer：隔离区、证据校验、OutputGate、发布以及可选的已验证主张持久化。只有最后访问阶段的终止任务会被渲染为候选。没有额外的模型综合调用；当较早的发现需要综合时，作者必须显式提供一个最终报告阶段。


All visited stages must complete and have verified goal status before release.
The candidate inherits sensitivity from the whole observed execution, even if
intermediate payloads are omitted. Intermediate caveats become generic limitations
without raw task IDs or warning text. Service policy is intersected with current
kernel policy both for execution and for release; the original envelope is never
widened. A journal that says `completed` describes **execution**, not publication.
The existing quarantine/release audit records the separate publication outcome.

<!-- zh -->
所有被访问的阶段都必须完成，并且在发布之前目标状态必须为已验证。候选继承整个被观测执行的敏感度，即使中间载荷被省略。中间保留意见会变成泛化的限制项，不带原始任务 ID 或警告文本。服务策略在执行与发布两处都与当前内核策略求交；原信封永远不会被放宽。一份写着 `completed` 的日志描述的是**执行**，不是发布。既有的隔离/发布审计记录了单独的发布结果。


Failures return structural refusal metadata and no output; refused citation IDs
and raw exception messages are withheld. The service exposes no `last_result`
cache that could accidentally return a previous run's body. Metadata labels retain
sensitivity without caller-supplied rationale text. A result whose status is not
`released` must not be treated as a deliverable.

<!-- zh -->
失败会返回结构化的拒绝元数据且不返回输出；被拒绝的引用 ID 与原始异常消息被扣留。该服务不暴露任何可能意外返回上一次运行正文的 `last_result` 缓存。元数据标签保留敏感度，但不带调用者提供的理由文本。一个状态不是 `released` 的结果不得被当作交付物。


This is a safer application API, not a Python sandbox: trusted host code can still
import the low-level controller or inspect private objects and is responsible for
not publishing them. The existing finalizer's quarantine storage, evidence checker,
claim extraction and persistence semantics are reused, not redesigned here. There
is no automatic resume, additional scientific truth attestation or exactly-once
release/commit transaction.

<!-- zh -->
这是一个更安全的应用 API，不是一个 Python 沙箱：可信的主机代码仍然可以导入低层控制器或检视私有对象，并有责任不发布它们。既有 finalizer 的隔离存储、证据检查器、主张抽取与持久化语义被复用，此处没有重新设计。没有自动恢复、没有额外的科学真值见证，也没有恰好一次的发布/提交事务。


Persistence must be authorized by the original envelope **and** current policy.
The store's ceiling is also enforced. The controller conservatively propagates
declared and classified workflow sensitivity, registered-protocol sensitivity,
caller `input_label`, and all completed stage result labels. Routing indices
are derived information, not automatically public metadata. If a newly sensitive
result cannot be persisted, the controller stops without writing a branch or
dispatching another stage; the journal may deliberately remain `running`.
The journal adapter itself is a trusted low-level storage port, like the existing
checkpoint adapter, not a public persistence gateway. Its readers require
filesystem access appropriate to the run. There is no encryption/key management.

<!-- zh -->
持久化必须由原信封**以及**当前策略授权。存储的上限也被强制。控制器保守地传播已声明与已分类的工作流敏感度、已注册协议敏感度、调用者的 `input_label`，以及所有已完成阶段结果的标签。路由索引是派生信息，不是自动公开的元数据。如果一个新近变得敏感的结果无法被持久化，控制器会停止，不写入分支也不分发下一个阶段；日志可能有意保持为 `running`。日志适配器本身是一个可信的低层存储端口，与既有检查点适配器类似，不是公共持久化网关。它的读取器需要与本次运行相称的文件系统访问权限。没有加密/密钥管理。


## Stage-to-stage inputs · 阶段间输入

`StageInput` pairs a target tool task ID with the runtime's existing `InputBinding`.
Here `fetch_program` produces task `fetch`, whose result contains `hits`; the
`analyze` tool task in `analysis_program` receives those values as `records`:

<!-- zh -->
`StageInput` 把一个目标工具任务 ID 与运行时既有的 `InputBinding` 配对。这里 `fetch_program` 产生任务 `fetch`，其结果包含 `hits`；`analysis_program` 中的 `analyze` 工具任务接收这些值作为 `records`：


```python
from psh.runtime import InputBinding
from psh.workflow import StageInput

workflow = DynamicWorkflow(
    stages=(
        WorkflowStage("fetch_stage", fetch_program, next_stage="analysis_stage"),
        WorkflowStage("analysis_stage", analysis_program, inputs=(
            StageInput("analyze", InputBinding(
                argument="records", source="fetch", pointer="/hits",
                expected_type="object", cardinality="many",
            )),
        )),
    ),
    entry="fetch_stage",
)
```

The source task is resolved in the **immediately preceding successful visit**,
not the target DAG. The same task ID may appear in both stages. On a self-loop,
the second visit consumes the first visit's result, not the initial seed forever.
An entry stage cannot declare inputs because it has no preceding visit. Every
incoming predecessor must contain each declared source task; unknown sources,
duplicate target arguments, literal/intra-stage-input conflicts and runtime
reserved arguments (`upstream`, `_psh_*`) are rejected during graph construction.

<!-- zh -->
源任务是在**紧邻的前一次成功访问**中解析的，而不是在目标 DAG 中。同一个任务 ID 可以同时出现在两个阶段中。在自环上，第二次访问消费第一次访问的结果，而不是永远消费最初的种子值。入口阶段不能声明输入，因为它没有前一次访问。每一个入边前驱都必须包含每个已声明的源任务；未知来源、重复的目标参数、字面量/阶段内输入的冲突以及运行时保留参数（`upstream`、`_psh_*`）都会在图构造期间被拒绝。


All inputs for a stage resolve before its intent event or any target task runs.
Pointers, types and cardinality use the same resolver as intra-stage bindings:
`cardinality="many"` checks each element against `expected_type`. Optional missing
paths (`required=False`) omit the argument; present values of the wrong type
still fail. JSON text is not automatically parsed. Only JSON-compatible selected
values are copied into payloads; nonfinite numbers, non-string object keys and
custom objects are refused, rather than stringified. A copied payload never
shares mutable nested values with the previous stage's result.

<!-- zh -->
一个阶段的所有输入都在其意图事件或任何目标任务运行之前解析完毕。指针、类型与基数使用与阶段内绑定相同的解析器：`cardinality="many"` 会对照 `expected_type` 检查每一个元素。可选的缺失路径（`required=False`）会省略该参数；类型错误的已存在取值仍然会失败。JSON 文本不会被自动解析。只有 JSON 兼容的被选值会被复制进载荷；非有限数值、非字符串对象键与自定义对象会被拒绝，而不是被字符串化。被复制的载荷绝不会与上一阶段的结果共享可变的嵌套值。


The instantiated scientific program is compiled again before dispatch. Its
known sensitivity floor includes all previously observed data, even if the
selected field is only an innocuous-looking count. Selecting a smaller field
is not declassification. Tool invocation still uses the broker and current
policy, the shared run budget and operation ledger.

<!-- zh -->
被实例化的科学程序在分发之前会再次编译。它已知的敏感度下限包含所有先前观测到的数据，即使被选中的字段只是一个看起来无害的计数。选择更小的字段不是去分类（declassification）。工具调用仍然使用 broker 与当前策略、共享运行预算与操作账本。


Binding failure records `ended(reason="binding_failed")` while the next stage
is still `ready`; that stage has not started and does not increase visit count.
No values, field names or raw binding exceptions are copied into the event log.
Readers from before this increment reject the new termination reason; upgrade
readers before reading new histories. Workflows without inputs keep their
previous serialized shape and fingerprint. Histories do not contain bound
payloads and still cannot restore intermediate results or automatically resume.

<!-- zh -->
绑定失败会记录 `ended(reason="binding_failed")`，而下一个阶段仍为 `ready`；该阶段尚未开始，也不增加访问计数。任何取值、字段名或原始绑定异常都不会被复制进事件日志。本次增量之前的读取器会拒绝这个新的终止原因；在读取新历史之前请升级读取器。没有输入的工作流保持其先前的序列化形状与指纹。历史记录不包含被绑定的载荷，也仍然无法恢复中间结果或自动恢复。


## Replay semantics and failure boundaries · 重放语义与失败边界

Events are `started`, `stage_started`, `stage_finished`, `routed`, `ended`.
The pure reducer validates schema, ordering, stage indices, visit counts and
terminal conditions. Replay rebuilds stage control state, not task outputs or
scientific evidence. Only structural indices/status and a workflow fingerprint
are persisted; no prompt, result, branch comparison literal or error text.

<!-- zh -->
事件是 `started`、`stage_started`、`stage_finished`、`routed`、`ended`。纯 reducer 校验模式、顺序、阶段索引、访问计数与终止条件。重放重建的是阶段控制状态，不是任务输出或科学证据。只有结构性索引/状态与一个工作流指纹被持久化；没有提示、结果、分支比较字面量或错误文本。


SQLite WAL/FULL commits each event atomically. `BEGIN IMMEDIATE` plus an expected
sequence prevents concurrent writers from advancing the same state. The hash
chain detects altered/interior-deleted events; an external anchor detects suffix
loss. Hashes are not signatures and cannot defeat full database rewriting.
Every read/append verifies history, currently O(events), appropriate to the
bounded first implementation rather than a high-throughput streaming journal.

<!-- zh -->
SQLite WAL/FULL 会原子地提交每一个事件。`BEGIN IMMEDIATE` 加上一个预期序号，阻止并发写入者推进同一状态。哈希链检测被改动/被内部删除的事件；外部锚点检测后缀丢失。哈希不是签名，无法对抗整库改写。每次读取/追加都校验历史，目前是 O(事件数)，这适合有界的首个实现，而不是一个高吞吐的流式日志。


The intent event commits **before** a stage runs. If the process dies after that
commit but before stage completion commits, replay reports `in_doubt=True`.
It does not guess whether a tool or model ran. Failure to write an event stops
execution; unlike best-effort audit logging, it never silently falls back to RAM.
A completed stage whose route was not recorded remains in `routing`.

<!-- zh -->
意图事件在阶段运行**之前**提交。如果进程在该提交之后、阶段完成提交之前死亡，重放会报告 `in_doubt=True`。它不猜测工具或模型是否运行过。写入事件失败会停止执行；与尽力而为的审计日志不同，它从不静默回退到内存。一个已完成但路由未被记录的阶段会停留在 `routing`。


`run()` rejects every nonempty journal, including completed histories. There is
no automatic resume/redispatch from these events, no persisted result cache, no
exactly-once guarantee, and no atomic transaction with external tool effects.
Use the existing operation ledger for effect investigation and the existing
governed checkpoint APIs for their separate task-level recovery workflow.
This journal is authoritative for **stage scheduling history only**, not a
universal replacement for audit logs, task checkpoints or operation records.

<!-- zh -->
`run()` 拒绝任何非空日志，包括已完成的历史。没有从这些事件自动恢复/重新分发，没有持久化的结果缓存，没有恰好一次保证，也没有与外部工具效果构成原子事务。请使用现有的操作账本做效果调查，使用现有的受治理检查点 API 做它们各自独立的任务级恢复工作流。该日志只对**阶段调度历史**具有权威性，不是审计日志、任务检查点或操作记录的通用替代。


`DynamicResult.ok` means all visited stages met their loop completion criteria
and a terminal edge was reached. It does not certify scientific truth or clinical
validity. Per-stage goal status and caveats remain on `result.stages`.

<!-- zh -->
`DynamicResult.ok` 意味着所有被访问的阶段都达到了其循环完成准则，并抵达了一条终止边。它不为科学真值或临床有效性作保。逐阶段的目标状态与保留意见保留在 `result.stages` 上。


## Validation · 验证

`tests/test_dynamic_workflow.py` covers both branch choices, conditional and
bounded cycles, preflight rejection, persistent reopen/replay, crash windows,
current-policy and sensitivity checks, shared budget exhaustion, distinct
repeatable operation identities, cancellation, caller mutation isolation,
invalid transitions, stale writers, corruption, suffix anchors and fail-closed
storage errors. Tests use synthetic model callbacks and tools, not live providers.
`tests/test_stage_inputs.py` adds real brokered argument delivery, type/cardinality
and optional-path rules, cyclic latest-visit dataflow, payload-copy isolation,
graph validation, sensitive-source label propagation and replay of binding
failures. These are synthetic execution tests, not scientific-validity benchmarks.
`tests/test_cycle_safety.py` covers SCC topology, self/multi-stage/unreachable
cycles, the 256-stage bound, trusted fact and contract refusals, safe cycles,
acyclic nonrepeatable execution, changing facts between/within visits and refusal
replay. The original nonrepeatable-cycle test was replaced by a repeatable-key
test; unsafe cyclic execution is now tested as a refusal, not a supported path.
`tests/test_dynamic_service.py` covers terminal-only publication, inherited labels,
goal/caveat aggregation, current/original policy restrictions, clinical and fabricated
citation refusals, incomplete runs, storage/finalizer failures and stale-output
prevention. Synthetic callbacks are used rather than live model providers.

<!-- zh -->
`tests/test_dynamic_workflow.py` 覆盖两种分支选择、条件循环与有界循环、预检拒绝、持久化重开/重放、崩溃窗口、当前策略与敏感度检查、共享预算耗尽、可区分的可重复操作身份、取消、调用者变更隔离、非法转换、过期写入者、损坏、后缀锚点与失败即关闭的存储错误。测试使用合成模型回调与工具，而不是实时提供方。`tests/test_stage_inputs.py` 增加了真实的经 broker 的参数投递、类型/基数与可选路径规则、循环的最新访问数据流、载荷复制隔离、图校验、敏感来源标签传播与绑定失败的重放。这些是合成执行测试，不是科学有效性基准。`tests/test_cycle_safety.py` 覆盖 SCC 拓扑、自环/多阶段/不可达循环、256 阶段上限、可信事实与契约拒绝、安全循环、无环不可重复执行、访问之间/之内的能力事实变化与拒绝重放。原来的不可重复循环测试被一个可重复键测试取代；不安全的循环执行现在被测试为一次拒绝，而不是一条受支持的路径。`tests/test_dynamic_service.py` 覆盖仅终止阶段发布、继承的标签、目标/保留意见聚合、当前/原策略限制、临床与伪造引用拒绝、未完成运行、存储/finalizer 失败与陈旧输出防止。使用合成回调，而不是实时模型提供方。

