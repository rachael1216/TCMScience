# Model-proposed scientific programs · 模型提出的科学程序

`ScientificModelPlanner` closes one gap between a research question and the
existing scientific compiler. Unlike `ScientificPlanner`, which takes a program
from its caller, it asks a model to propose a ScientificProgram and repairs
rejected proposals within a fixed attempt bound. It returns only a compiled Plan
to the existing AgentLoopController. It has no ordinary-Plan fallback.

<!-- zh -->
`ScientificModelPlanner` 弥合了研究问题与现有科学编译器之间的一个缺口。与从其调用者接收程序的 `ScientificPlanner` 不同，它请求模型提出一个 ScientificProgram，并在固定的尝试次数上限内修复被拒绝的提案。它只向现有的 AgentLoopController 返回一个编译后的 Plan。它没有"退回普通 Plan"的兜底路径。


```python
from psh.workflow import ScientificModelPlanner
from psh.runtime import AgentLoopController

planner = ScientificModelPlanner(
    kernel,
    model=planning_profile,
    model_invoke=planning_invoke,
    registry=capability_registry,
    scientific_ledger=scientific_ledger,  # optional unless bindings are used
    max_attempts=3,
)
loop = AgentLoopController(
    kernel, planner=planner, registry=capability_registry,
    model=execution_profile, model_invoke=execution_invoke,
)
result = loop.run(research_question, kernel.policy.envelope())
```

The names above are application-provided objects, not a ready-to-run provider
configuration. Every planning request uses ExecutionBroker.call_model, including
the supplied callback: normal model egress, labels, accounting and audit apply.
The planner does not own or call a provider directly. No live provider is needed
for the synthetic regression tests.

<!-- zh -->
上面的名字都是应用提供的对象，不是一份开箱即用的提供方配置。每一次规划请求都经由 ExecutionBroker.call_model，包括所提供的回调：常规的模型出口、标签、核算与审计都会生效。规划器不拥有、也不直接调用任何提供方。合成回归测试不需要任何实时提供方。


## Admission and repair · 准入与修复

1. Join objective, prior-plan and (when applicable) prior-result labels.
2. Refresh authority against the kernel's current policy before each request.
3. Build context through the existing ModelPlanner projection pipeline, including
   authorized registry schemas and optional labelled memory.
4. Parse a single JSON object with schema_version, plan and contracts. Reject
   duplicate keys, nonfinite numbers, trailing prose/fences, excessive character
   counts and malformed scientific wire values. The existing ScientificProgram
   parser/constructors define field semantics; some legacy Plan scalar coercions
   remain, so this is not an independently generated JSON Schema validator.
5. Compile under current policy and the joined input/output sensitivity. Reject
   delegation if the loop has no delegate backend.
6. Repair using diagnostic families/codes only, without copying raw rejected
   model text, task IDs or exception messages into correction prompts.

<!-- zh -->
1. 并入目标、先前计划以及（在适用时）先前结果的标签。
2. 在每次请求之前，对照内核当前策略刷新权限。
3. 通过现有的 ModelPlanner 投影管线构建上下文，其中包括已授权的注册表模式与可选的有标签记忆。
4. 解析一个包含 schema_version、plan 与 contracts 的单一 JSON 对象。拒绝重复键、非有限数值、尾随的散文/围栏、超限字符数与格式错误的科学线上取值。字段语义由现有的 ScientificProgram 解析器/构造器定义；一些遗留的 Plan 标量强制转换仍然存在，因此这不是一个独立生成的 JSON Schema 校验器。
5. 在当前策略与并入后的输入/输出敏感度之下编译。若循环没有委派后端，则拒绝委派。
6. 只使用诊断族/诊断码进行修复，不把被拒绝的模型原文、任务 ID 或异常消息复制进纠正提示。


Attempts default to three, may be configured from one to eight, and share the
run's model-call/token/cost limits. Provider, budget and egress failures propagate;
they are not treated as correctable Scientific IR errors. The response parser
defaults to 262,144 characters, but this does not limit provider-side generation
or memory already allocated by the model gateway. Configure provider output and
run budgets as well.

<!-- zh -->
尝试次数默认为三次，可配置为一到八次，并共享该运行的模型调用/token/成本上限。提供方、预算与出口故障会向上传播；它们不被当作可纠正的科学 IR 错误。响应解析器默认上限为 262,144 个字符，但这并不限制提供方侧的生成，也不限制模型网关已经分配的内存。请同时配置提供方的输出与运行预算。


`last_program` and `last_compilation` support in-memory inspection after success.
They are cleared at the start of each planning call, including a failed replan.
They may contain sensitive material: persistence/export still needs normal
governance. `attempts` is scoped to the current planning call; refusal entries
contain safe diagnostic codes rather than raw model replies. Instances are not
intended to be shared by concurrently running loops.

<!-- zh -->
`last_program` 与 `last_compilation` 支持在成功之后做内存内检视。它们在每次规划调用开始时被清空，包括失败的重规划。它们可能含有敏感材料：持久化/导出仍然需要常规治理。`attempts` 的作用域是当前这次规划调用；拒绝条目包含安全的诊断码，而不是模型原始回复。这些实例不打算由并发运行的循环共享。


## What this does not establish · 这不能确立什么

- Scientific declarations remain untrusted proposals. Evidence existence,
  execution conformance and scientific validity are not established by parsing.
- This adapter requires the compiler on its own path, but plain Plan users of
  AgentLoopController remain compatible. A separate mandatory ScientificRunMode
  for all entry points has not been implemented.
- Repair can propose a different plan; it is not approval of a changed registered
  protocol, proof of semantic goal equivalence, or authorization to weaken study
  requirements. Applications should supply required bindings/constraints and
  review proposed programs before consequential execution.
- Dynamic branches, loops, fan-out, live amendment, per-event execution replay,
  hypothesis tournaments and autonomous experimental feedback are not added.
- Tests use deterministic response callbacks and the real broker/loop. They do
  not measure live-model scientific quality or compare against another product.

<!-- zh -->
- 科学声明仍然是不可信的提案。证据是否存在、执行是否合规以及科学有效性，都不是解析所能确立的。
- 该适配器在自己的路径上要求编译器，但 AgentLoopController 的普通 Plan 用户仍然兼容。面向所有入口的、独立且强制的 ScientificRunMode 尚未实现。
- 修复可以提出一份不同的计划；它不是对已变更的注册方案的批准，不是语义目标等价性的证明，也不是放宽研究要求的授权。应用应当提供所需的绑定/约束，并在后果性执行之前审阅所提出的程序。
- 动态分支、循环、扇出、实时修正、逐事件执行重放、假设锦标赛与自主实验反馈均未加入。
- 测试使用确定性的响应回调与真实的 broker/循环。它们不衡量实时模型的科学质量，也不与另一个产品做比较。


Run `python -m pytest -q tests/test_scientific_model_planner.py` for regression tests.

<!-- zh -->
运行 `python -m pytest -q tests/test_scientific_model_planner.py` 执行回归测试。

