# Adversarial review of the v0.6–v0.9 runtime, and its closure · v0.6–v0.9 运行时的对抗性评审及其收口

The agent runtime (`loop.py`, `planner.py`, `checkpoint.py`, `subagent.py`,
`supervisor.py`, the MCP and A2A adapters) was written on top of the v0.5.1 kernel under
one rule: **the loop must not bypass the TrustedKernel**. It does not. Every action it
takes is one of three broker calls, and a test counts them.

<!-- zh -->
智能体运行时（`loop.py`、`planner.py`、`checkpoint.py`、`subagent.py`、`supervisor.py`，以及 MCP 与 A2A 适配器）写在 v0.5.1 内核之上，遵循一条规则：**循环绝不能绕过可信内核（trusted kernel, TK）TrustedKernel**。它没有绕过。它采取的每一个动作都是三类 broker 调用之一，并且有测试对它们计数。

That rule was necessary and not sufficient. A second review — identification of candidate
findings, then an independent false-positive filter per finding, then a cut at
confidence 8 of 10 — was run against the runtime after it was finished. Four candidates
came back; two survived. Both are the same shape: the loop went *through* the kernel and
handed it the wrong label. A gate that is given PUBLIC rules PUBLIC.

<!-- zh -->
那条规则是必要的，但并不充分。运行时完成之后又对它做了第二轮评审——先识别候选发现，再对每条发现做独立的假阳性（false positive）过滤，最后在置信度 8/10 处截断。返回 4 个候选，2 个存活。两者形状相同：循环*穿过*了内核，却把错误的标签交给了它。一个被给予 PUBLIC 的门，只能裁定为 PUBLIC。

Both were reproduced on the unfixed tree before anything was changed, and the
reproductions are now `tests/test_loop_labels.py`, which fails 23 of 24 on that tree.

<!-- zh -->
在做出任何改动之前，两者都在未修复的代码树上复现过；这些复现现在就是 `tests/test_loop_labels.py`，它在该代码树上 24 项中失败 23 项。

---

# Vuln 1: Label laundering across loop tasks: `src/psh/runtime/loop.py` (`_dispatch`, `_projection`) · Vuln 1：跨循环任务的标签洗白：`src/psh/runtime/loop.py`（`_dispatch`、`_projection`）

* Severity: High (confidence 9/10 after filtering)
* Description: `AgentLoopController` recorded the broker-assigned label of every task
  result on `TaskNode.label` and then never consulted it when building the next task's
  input. For a MODEL task, each upstream result became a `ContextItem(kind="evidence")`
  with the default label — PUBLIC — and `ExecutionBroker.call_model` took a
  `ContextProjection`'s label on trust. For a TOOL task the bare value was placed in the
  payload and ingress re-classified it from its text, which sees an identifier and does
  not see a count derived from a PHI cohort. Invariant (5) — a derived value carries the
  join of its sources — was violated on every edge of the execution graph.
* Exploit scenario (reproduced): a plan of `read` (TOOL returning
  `"Patient Alice Smith MRN 04851923 …"`) and `summ` (MODEL at `PUBLIC_REMOTE`, depending
  on `read`) terminated `goal_satisfied`; the public provider's prompt contained the MRN;
  the model gateway recorded `allowed=True, PUBLIC_REMOTE, label=PUBLIC`; and
  `LoopResult.label` said `phi [medical_record_number, name_cued]`. The kernel knew and
  the gate was never told. Tool variant: `cohort_count` given a PHI chart returned
  `{"readmissions": 3, "cohort": 12}` (broker label PHI); `public_uploader` at
  `PUBLIC_REMOTE` received it under an INTERNAL verdict.
* Fix: labels travel. `ExecutionGraph.labeled_results()` hands `_dispatch` each result
  wrapped in its label; evidence items carry it; tool payloads carry it where ingress
  joins it; a model task's result is labelled with what the model was shown joined with
  what it said; a delegate's result with what was handed over joined with what came back.
  An evidence item the compiler would drop for the destination is a refusal
  (`EgressDenied`, audited as `loop_task_refused`) rather than a quieter prompt: the
  upstream result *is* the task's input, and a task run without it would succeed at
  answering a different question.

<!-- zh -->
* 严重性：高（过滤后置信度 9/10）
* 描述：`AgentLoopController` 把 broker 为每个任务结果分配的标签记录在 `TaskNode.label` 上，之后在构建下一个任务的输入时从不查阅它。对于一个 MODEL 任务，每个上游结果都变成一个 `ContextItem(kind="evidence")`，带着默认标签——PUBLIC——而 `ExecutionBroker.call_model` 直接信任 `ContextProjection` 的标签。对于一个 TOOL 任务，裸值被放进 payload，入口（ingress）从它的文本重新分类，而文本看到的只是一个标识符，看不到由 PHI 队列推导出来的计数。不变式 (5)——派生值携带其各来源的 join——在执行图的每一条边上都被违反。
* 利用场景（已复现）：一个由 `read`（TOOL，返回 `"Patient Alice Smith MRN 04851923 …"`）和 `summ`（MODEL，位于 `PUBLIC_REMOTE`，依赖 `read`）组成的计划以 `goal_satisfied` 终止；公开提供方的 prompt 中包含该 MRN；模型网关记录 `allowed=True, PUBLIC_REMOTE, label=PUBLIC`；而 `LoopResult.label` 写的是 `phi [medical_record_number, name_cued]`。内核知道，而门从未被告知。工具变体：给 `cohort_count` 一份 PHI 病历，它返回 `{"readmissions": 3, "cohort": 12}`（broker 标签为 PHI）；位于 `PUBLIC_REMOTE` 的 `public_uploader` 在 INTERNAL 裁定下收到了它。
* 修复：标签随行。`ExecutionGraph.labeled_results()` 把每个结果连同其标签一起交给 `_dispatch`；证据条目携带它；工具 payload 在入口对它做 join 的地方携带它；模型任务的结果，用它被展示的内容与它所说的内容做 join 后打标；委托（delegate）的结果，用交出去的内容与返回的内容做 join 后打标。编译器会因目的地而丢弃的证据条目，是一次拒绝（`EgressDenied`，审计为 `loop_task_refused`），而不是一个更安静的 prompt：上游结果*就是*该任务的输入，而剥离它运行的任务会成功地回答另一个问题。

# Vuln 2: Loop objective and planner feedback reach models unclassified: `src/psh/runtime/planner.py` (`_projection`), `loop.py` (`_projection`) · Vuln 2：循环目标与规划器反馈未分类即送达模型：`src/psh/runtime/planner.py`（`_projection`）、`loop.py`（`_projection`）

* Severity: High (confidence 8/10 after filtering)
* Description: `Runner.run` classifies the request at stage 1, labels its turn item with
  the result, and `_preflight` refuses a projection above the run's ceiling. The loop
  path did none of this. `ModelPlanner._projection` built the turn item from the
  objective and an instruction item from validator refusals and evaluator failures —
  which embed task result values and component error text — with the default label;
  `AgentLoopController._projection` did the same with each task's objective. Turn and
  instruction items are load-bearing (never dropped by the compiler), and no gate compared
  a label with `envelope.max_label`; only `Runner._preflight` did.
* Exploit scenario (reproduced): `ModelPlanner(..., model=<PUBLIC_REMOTE profile>)
  .plan(LoopState(objective="Summarise the chart of patient Alice Smith, MRN 04851923"))`
  delivered the MRN to the public provider; the gateway recorded
  `allowed=True, PUBLIC_REMOTE, label=PUBLIC` while `kernel.classify(objective)` said PHI.
  The same held for a task objective in a static plan, for a child loop's objective
  written by a parent plan's DELEGATE task or a supervisor, and for a replan whose
  feedback quoted a PHI tool result that had failed an `enum` check.
* Fix: the objective is classified once at loop ingress (`objective_label_for`) and its
  label is carried onto the planner's turn item; a model-authored plan's label — what the
  planner's model was shown joined with what it wrote — is recorded on
  `LoopState.plan_label` and every task objective carries it (a task objective is derived
  from that prompt); planner feedback carries the join of the labels of the graph it
  quotes; a refused attempt's text carries the label its call was given; a DELEGATE task
  hands its label to the child through the contract's projection, and
  `LocalSubagentBackend` seeds the child's objective label from it. `LoopResult.label`
  now joins the objective's label with the results', because a child asked about a PHI
  chart answers about that chart.

<!-- zh -->
* 严重性：高（过滤后置信度 8/10）
* 描述：`Runner.run` 在第 1 阶段对请求做分类，用结果给它的 turn item 打标，并且 `_preflight` 会拒绝高于本次运行上限的 projection。循环路径这些都没做。`ModelPlanner._projection` 用目标构建 turn item，用校验器拒绝与评估器失败构建 instruction item——其中内嵌了任务结果值和组件错误文本——都带默认标签；`AgentLoopController._projection` 对每个任务的目标也同样处理。turn item 与 instruction item 是承重的（编译器绝不丢弃），而没有任何门把标签与 `envelope.max_label` 做比较；只有 `Runner._preflight` 做了。
* 利用场景（已复现）：`ModelPlanner(..., model=<PUBLIC_REMOTE profile>).plan(LoopState(objective="Summarise the chart of patient Alice Smith, MRN 04851923"))` 把该 MRN 送达了公开提供方；网关记录 `allowed=True, PUBLIC_REMOTE, label=PUBLIC`，而 `kernel.classify(objective)` 判定为 PHI。同样的情形也出现在静态计划中的任务目标、由父计划的 DELEGATE 任务或 supervisor 写出的子循环目标，以及一次重规划（replan）中——该重规划的反馈引用了一个未通过 `enum` 检查的 PHI 工具结果。
* 修复：目标在循环入口处被分类一次（`objective_label_for`），其标签被携带到规划器的 turn item 上；模型撰写的计划的标签——即规划器模型被展示的内容与它所写内容的 join——记录在 `LoopState.plan_label` 上，每个任务目标都携带它（任务目标正是从那个 prompt 派生的）；规划器反馈携带它所引用的图的标签的 join；一次被拒绝的尝试，其文本携带该次调用被给予的标签；DELEGATE 任务通过契约的 projection 把它的标签交给子级，`LocalSubagentBackend` 由此为子级的目标标签播种。`LoopResult.label` 现在把目标的标签与各结果的标签做 join，因为一个被问及 PHI 病历的子级，回答的就是那份病历。

---

## The kernel-level closure · 内核层面的收口

Fixing the callers would have fixed these two callers. The defect was that the broker had
one value it took on trust — a `ContextProjection`'s aggregate label — and the rule of
`call_tool` ("classify at the boundary, never trust the caller") did not apply to it.
Two changes to `kernel/egress.py` make the next mis-built projection a refusal rather
than a finding:

<!-- zh -->
修好这些调用方，只会修好这两个调用方。缺陷在于 broker 有一个它直接信任的值——`ContextProjection` 的聚合标签——而 `call_tool` 的规则（“在边界处分类，绝不信任调用方”）并不适用于它。对 `kernel/egress.py` 的两处改动，让下一个构造错误的 projection 成为一次拒绝，而不是一条发现：

* **The broker re-classifies a projection.** `ExecutionBroker._reclassified` classifies
  the rendered text that will actually be sent and joins it with the claimed label. A
  projection can be escalated at that boundary and is never trusted downward. The
  `ModelCallResult` carries the label the gate ruled on, so anything built on a model's
  answer starts from the join.
* **Both gateways enforce the run's ceiling.** `ModelGateway.check` and
  `ToolGateway.check` refuse a label above `envelope.max_label`. Before, only
  `Runner._preflight` made that comparison, so any caller that reached the broker by
  another route — a loop, a planner, an adapter — executed under a ceiling no gate read.

<!-- zh -->
* **broker 会重新分类 projection。** `ExecutionBroker._reclassified` 对真正将被发送的渲染文本做分类，并与所声称的标签做 join。projection 可以在该边界被上调，且绝不会被向下信任。`ModelCallResult` 携带门所裁定的那个标签，因此任何建立在模型回答之上的东西都从该 join 出发。
* **两个网关都强制本次运行的上限（ceiling）。** `ModelGateway.check` 与 `ToolGateway.check` 拒绝高于 `envelope.max_label` 的标签。此前只有 `Runner._preflight` 做这项比较，因此任何经由其他路径到达 broker 的调用方——一个循环、一个规划器、一个适配器——都在一个没有任何门读取的上限之下执行。

One consequence surfaced immediately and is stated rather than hidden: the classifier
floors ordinary text at INTERNAL ("the user's own working material"), so a run whose
ceiling is PUBLIC can send nothing to a model or a tool. No shipped profile has a PUBLIC
ceiling. The verification model's envelope was stated as PUBLIC — a ceiling nothing read —
and now states what the verifier may lawfully receive under the policy, which is what the
gateway had been enforcing all along through `ModelProfile.may_receive`.

<!-- zh -->
有一个后果立刻浮现，我们把它写明而不是藏起来：分类器把普通文本的下限定为 INTERNAL（“用户自己的工作材料”），因此一次上限为 PUBLIC 的运行无法向模型或工具发送任何东西。已发布的 profile 中没有一个是 PUBLIC 上限。验证模型的 envelope 曾被写成 PUBLIC——一个无人读取的上限——现在它写明的是验证器依策略可以合法接收什么，而这正是网关一直以来通过 `ModelProfile.may_receive` 所强制的内容。

What the re-classification does and does not do: it catches verbatim content the lexical
classifier recognises. It does not see a derived value — a count, a paraphrase — and is
not meant to; that is what carrying labels is for. The two layers cover each other's
blind spot: carried labels for derivation, the rescan for a caller that forgot.

<!-- zh -->
重新分类做什么、不做什么：它能捕获词法分类器识别得出的逐字内容（verbatim content）。它看不到派生值——一个计数、一段转述——而且它本就不该看到；那正是携带标签的用途。这两层互相覆盖对方的盲区：携带标签负责派生，重扫负责遗忘的调用方。

## The two findings that did not survive, and what was done anyway · 两条未能存活的发现，以及无论如何仍然做了的事

**Checkpoint contents bypass the persistence gate** (confidence 6/10). Accurate as
mechanics — `capture()` wrote every succeeded result to disk with no ceiling, no label and
default permissions — but bounded: only an operator can wire a checkpoint store, and in
the default configuration the WorkGraph holds the same material by design. Fixed as
hygiene: a checkpoint is a durable write and obeys the persistence rules like any other.
A result above the persistence gateway's ceiling, one whose label does not permit
`PERSISTENT`, an unlabelled one, or any result of a run whose envelope withholds
`PERSISTENT` is **withheld** — the record names the task and the reason, an audit event
(`loop_checkpoint_withheld`) is written, and on resume the task runs again, which is the
case the idempotency key exists for. Labels are stored with results and restored joined
with a fresh classification, so a resumed graph never starts from PUBLIC and an editor who
lowers a stored label gains nothing. Files are written owner-only.

<!-- zh -->
**检查点内容绕过持久化门**（置信度 6/10）。就机制而言属实——`capture()` 把每一个成功结果写到磁盘，没有上限、没有标签，权限也是默认的——但影响有界：只有操作者才能接上一个检查点存储，而在默认配置下，WorkGraph 本来就按设计持有同样的材料。作为卫生问题修复：检查点是一次持久化写入，像其他任何持久化写入一样遵守持久化规则。一个高于持久化网关上限的结果、一个其标签不允许 `PERSISTENT` 的结果、一个没有标签的结果，或者一次其 envelope 撤回/不予写出（withheld）`PERSISTENT` 的运行中的任何结果，都会被**撤回**——记录写明任务与原因，写入一条审计事件（`loop_checkpoint_withheld`），在恢复时该任务重新运行，而这正是幂等键（idempotency key）为之而存在的场景。标签与结果一同存储，恢复时与一次新的分类做 join，因此被恢复的图绝不会从 PUBLIC 出发，而一个把已存标签改低的编辑者什么也得不到。文件以仅属主（owner-only）方式写入。

**Checkpoint integrity is vacuous** (false positive, 3/10). The content hash is unkeyed
and documented as such ("tamper-evident, not tamper-proof; same posture as the event
chain"), and any writer who can reach the checkpoint directory can already rewrite the
kernel's execution policy file next to it. The one real defect in the finding was fixed:
`Checkpoint.from_dict` refused nothing when the `hash` key was absent and hashed the
record for the editor. A record with no hash is now refused by `from_dict`, by
`CheckpointStore.load`, and is not listed by `all()`.

<!-- zh -->
**检查点完整性是空洞的（vacuous）**（假阳性，3/10）。内容哈希是无密钥的（unkeyed），文档也如此写明（“防篡改可发现（tamper-evident），而非防篡改（not tamper-proof）；与事件链同一姿态”），而且任何能触及检查点目录的写入者，本来就能改写它旁边内核的执行策略文件。该发现中唯一真正的缺陷已被修复：当 `hash` 键缺失时，`Checkpoint.from_dict` 什么都不拒绝，还为编辑者把记录哈希了一遍。现在没有哈希的记录会被 `from_dict` 拒绝、被 `CheckpointStore.load` 拒绝，也不会被 `all()` 列出。

## Tests · 测试

`tests/test_loop_labels.py` — 24 tests, 23 of which fail on the unfixed tree:

<!-- zh -->
`tests/test_loop_labels.py`——24 项测试，其中 23 项在未修复的代码树上失败：

* the two reproductions above, the tool variant, and "refused rather than run without
  its input";
* a model result labelled with what the model saw; a tool still receives bare values;
* the planner objective, the task objective, feedback that quotes a PHI result, a plan a
  model wrote from a PHI prompt (clean text, PHI label), a delegated objective;
* the broker re-classifying a mis-built projection and leaving a correct one unchanged;
  both gateways enforcing the run's ceiling;
* checkpoints storing and restoring labels, escalating a restored result by its text,
  withholding above the ceiling, withholding unlabelled results, withholding under a run
  that may not persist, refusing a hashless record, owner-only files;
* a structural test that refuses any `ContextItem(...)` built in the runtime without a
  stated label — the omission is the defect, so the omission itself fails;
* the broker-counting invariant, re-asserted: the fix added classification and no fourth
  way to act.

<!-- zh -->
* 上面两组复现、工具变体，以及“宁可拒绝，也不在没有其输入的情况下运行”；
* 一个用模型所看到的内容打标的模型结果；工具仍然接收裸值；
* 规划器目标、任务目标、引用了 PHI 结果的反馈、一个模型从 PHI prompt 写出的计划（文本干净、标签为 PHI）、一个被委托的目标；
* broker 对一个构造错误的 projection 重新分类，并让一个正确的 projection 保持不变；两个网关都强制本次运行的上限；
* 检查点存储与恢复标签、按文本上调一个被恢复的结果、在上限之上撤回、撤回无标签的结果、在一次不得持久化的运行下撤回、拒绝无哈希记录、仅属主文件；
* 一项结构性测试，拒绝运行时中任何未声明标签就构造的 `ContextItem(...)`——遗漏本身就是缺陷，因此遗漏本身即失败；
* broker 计数不变式被再次断言：本次修复增加了分类，没有增加第四种行动方式。

Three existing tests changed their setup, none their claim: two hand-built graphs now give
their succeeded nodes a label (the loop always does), and the gate-composition test's
narrow policy uses INTERNAL rather than PUBLIC so the isolation refusal it asserts is the
one that fires.

<!-- zh -->
三项既有测试改了设置，没有改它们的主张：两个手工构造的图现在给其成功的节点一个标签（循环总是这样做），而门组合测试所用的窄策略改用 INTERNAL 而不是 PUBLIC，这样它断言的隔离拒绝才会是实际触发的那一个。

## Still open, stated plainly · 仍然开放的问题，直说

* The rescan is lexical. A model shown PHI that paraphrases it without identifiers is
  labelled PHI by the carried label, not by the text — which is the design, and the
  reason `Runner`'s output-only classification of a model reply (pre-existing, unchanged)
  is weaker than the loop's join.
* `DelegationGateway` compares a contract's projection label with the child's ceiling
  and still does not read the objective text; the child classifies it at its own ingress.
  A remote agent's outbound message goes through `ToolGateway`, where ingress does.
* Resume re-authorises unfinished tasks and, as before, not finished ones. A task the
  current policy would refuse can still be presented as done by a checkpoint; what is
  built on its result is gated as everything else is.
* The default `ToolGateway.allowed_paths` (`state_dir/*`) lets a filesystem tool's
  payload name the kernel's own policy file, event store and secrets directory. Older
  than this runtime, and worth its own change.

<!-- zh -->
* 重扫是词法的。一个被展示了 PHI 的模型，若把 PHI 转述而不含标识符，它打上 PHI 标签靠的是携带的标签，而不是文本——这就是设计，也是为什么 `Runner` 对模型回复的仅输出分类（先于本次改动、未作修改）比循环的 join 更弱。
* `DelegationGateway` 把契约的 projection 标签与子级的上限作比较，仍然不读取目标文本；子级在自己的入口对它分类。远端智能体的出站消息经过 `ToolGateway`，那里的入口会做分类。
* 恢复会重新授权未完成的任务，与以前一样，不重新授权已完成的任务。一个当前策略会拒绝的任务，仍然可能被检查点呈现为已完成；建立在它的结果之上的东西，和其他一切一样受门控。
* 默认的 `ToolGateway.allowed_paths`（`state_dir/*`）允许文件系统工具的 payload 指名内核自己的策略文件、事件存储和密钥目录。这比本运行时更早，值得单独立项修改。

## Addendum (v0.5.2): the read side of project memory · 附录（v0.5.2）：项目记忆的读取侧

`kernel/persistence.py` closed the write side of the WorkGraph in v0.2 so that mis-labelled
content could not become "a standing invitation to include PHI in a future prompt". Until
v0.5.2 nothing read that memory back into a run except items a caller passed by hand, so
the write side's properties had never been tested against a reader. `context/memory.py`
is the reader, built under the same review posture, and these are the properties its tests
pin (`tests/test_memory.py`):

<!-- zh -->
`kernel/persistence.py` 在 v0.2 关闭了 WorkGraph 的写入侧，使被错误打标的内容不能变成“一份在未来 prompt 中纳入 PHI 的长期邀请”。直到 v0.5.2，除了调用方手动传入的条目之外，没有任何东西把那份记忆读回运行中，因此写入侧的各项性质从未针对一个读取者测试过。`context/memory.py` 就是那个读取者，按同样的评审姿态构建，下面是它的测试所钉住的性质（`tests/test_memory.py`）：

* **Validated knowledge only.** `verified` and `system` nodes are retrievable; `candidate`
  nodes are not unless an operator names that status; `REJECTED_CLAIM` is excluded even
  from a retriever told to recall it, and holds no text in any case.
* **Labels travel.** An item carries the label the gateway stored on its node. A PHI claim
  whose text a lexical scan would rate lower reaches the projection as PHI, and
  `LoopResult.label` joins it.
* **Two withholdings, both counted.** Memory above the run's `max_label` is withheld, and
  memory that may not reach the model's destination is withheld — at retrieval, so the
  loop's rule "an upstream result dropped for policy is a refusal" is never triggered by
  memory. Withheld memory is a quieter prompt; the retrieval trace records why.
* **Scoped.** Retrieval is by the envelope's project; with none, nothing is returned unless
  `cross_project=True` is set deliberately.
* **Read-only.** No `remember()` exists, because a helper that wrote model output into
  trusted memory would be the laundering path `commit_rejected` closes. A structural test
  parses the module and refuses any call to `add`, `update`, `link`, `commit_node`,
  `commit_raw` or `execute`.

<!-- zh -->
* **仅限已校验的知识。** `verified` 与 `system` 节点可被检索；`candidate` 节点不可，除非操作者指名该状态；`REJECTED_CLAIM` 即使对一个被要求召回它的检索器也被排除，而且它在任何情况下都不持有文本。
* **标签随行。** 一个条目携带网关存在其节点上的标签。一条 PHI 主张，若其文本被词法扫描评为更低，也会以 PHI 的身份到达 projection，且 `LoopResult.label` 会把它 join 进来。
* **两种撤回，都被计数。** 高于本次运行 `max_label` 的记忆会被撤回，不得抵达模型目的地的记忆也会被撤回——在检索时撤回，因此循环的规则“因策略被丢弃的上游结果即一次拒绝”绝不会被记忆触发。被撤回的记忆只是一个更安静的 prompt；检索轨迹会记录原因。
* **有作用域。** 检索按 envelope 的 project 进行；没有 project 时，除非刻意设置 `cross_project=True`，否则不返回任何东西。
* **只读。** 不存在 `remember()`，因为一个把模型输出写进可信记忆的辅助函数，正是 `commit_rejected` 所关闭的洗白路径。一项结构性测试解析该模块，并拒绝任何对 `add`、`update`、`link`、`commit_node`、`commit_raw` 或 `execute` 的调用。

What it does not do: ranking is lexical and deterministic, not semantic, because retrieval
on the critical path must not itself require egress; and memory is recalled per task
objective, not accumulated into a transcript, for the reason `ContextProjection` gives.

<!-- zh -->
它不做什么：排序是词法的、确定性的，而不是语义的，因为关键路径上的检索本身不得要求出网；记忆按任务目标召回，而不是累积成一份 transcript，理由与 `ContextProjection` 给出的一致。
