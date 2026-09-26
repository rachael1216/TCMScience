# Response to the 2026-09-18 architecture review · 对 2026-09-18 架构评审的回应

The review ("TCMScience 智能体架构与能力审查", 2026-09-18) accepted the v0.5.2 premise —
a trusted kernel with a bounded loop on top — and probed the merged tree with synthetic
data. Its finding was that the governance closed around a single `Runner` pass had not
closed around the loop, and that the capability plane had no representation of what a
traditional-medicine claim is. Twelve findings, F01–F12, four of them P0.

<!-- zh -->
该评审（“TCMScience 智能体架构与能力审查”，2026-09-18）接受了 v0.5.2 的前提——一个可信内核（trusted kernel, TK）加上其上的有界循环——并用合成数据探测了已合并的代码树。其发现是：围绕单次 `Runner` 运行闭合的治理，并未围绕循环闭合；而能力平面（capability plane, CP）对“中医药论断是什么”没有任何表示。共十二项发现，F01–F12，其中四项为 P0。


This document is the record: for each finding, the reproduction, the change, the test,
and what it does not claim. The probe that reproduced F01–F07, F09 and F10 against the
merged tree is the one the tests in `tests/test_review_2026_09_18.py` (PSH) and
`tests/test_tcm.py`, `tests/test_doctor.py`, `tests/test_psh_bridge.py` (BioScience)
are written from.

<!-- zh -->
本文是记录：对每一项发现，给出复现、改动、测试，以及它未主张什么。针对已合并的代码树复现 F01–F07、F09 与 F10 的那个探针，正是 `tests/test_review_2026_09_18.py`（PSH）与 `tests/test_tcm.py`、`tests/test_doctor.py`、`tests/test_psh_bridge.py`（BioScience）中的测试所写自的探针。


Versions: PSH-Harness 0.5.2 → **0.5.3**, BioScience-Harness 0.2.5 → **0.2.6**.

<!-- zh -->
版本：PSH-Harness 0.5.2 → **0.5.3**，BioScience-Harness 0.2.5 → **0.2.6**。


## Summary · 中文摘要

The reviewer probed the merged tree with synthetic data and found that the governance built around a single `Runner` pass did not cover the loop (`AgentLoopController`), and that the capability plane had no representation of the "traditional-medicine claim" itself. All twelve findings have been addressed, each one first reproduced, then fixed, then pinned down by a regression test:

<!-- zh -->
审查者用合成数据对已合并的代码树做了探测，发现围绕单次 `Runner` 运行建立的治理并没有
覆盖循环（`AgentLoopController`），能力平面也没有对“中医药论断”本身的表示。十二项发现
已全部处理，每一项都先复现、再修复、再以回归测试固定：


| ID | Finding | Resolution |
| --- | --- | --- |
| F01 (P0) | Loop results bypassed the release gate: the same sentence `Drug X cures every cancer.` was refused through Runner but came out `goal_satisfied` through the loop | `LoopResult` became an internal object; `Finalizer` makes the loop's deliverable go through the same quarantine, claim verification, release gate and claim commit as a single pass; the application receives only a `ReleasedResult`; `ResearchRunService` is the only door; plan-level `evidence_requirements` strings no longer satisfy a "claim support required" policy |
| F02 (P0) | Runner re-classified on the output text alone and dropped the context label: a count derived from a PHI chart was released as INTERNAL | The output's label in quarantine = context projection ∨ broker result label ∨ text scan |
| F03 (P0) | A run not allowed to persist still checkpointed the objective, the task text, the payload and the error text in full | Such a run writes only the plan shape (id, kind, component, dependency, binding), `redacted=True`; resume must supply the objective and a plan of the same shape again; the record carries the consumed budget |
| F04 (P0) | The fallback classifier did not recognise Chinese identifiers: 住院号, 身份证, mobile numbers and the like were judged INTERNAL | New Chinese rules; a clinical origin (ehr/emr/chart…) floors the label at PHI; `require_validated_classifier` can refuse to start on the fallback detector; a warning is issued when the policy admits PHI and a remote destination |
| F05 (P1) | The upstream result was passed to downstream tools only as a single `upstream` blob, which the bridge drops; `"$fetch.gene"` was taken as a literal | `InputBinding` (argument, source task, JSON pointer, type, cardinality); the validator refuses a literal that reads like a reference and names the binding it should have been; the upstream label is kept at resolution |
| F06 (P1) | The parent run could not see what its child tasks consumed; the bounds check itself consumed one model call | Consumption is charged to the run and to all of its ancestors, refused at any level's ceiling; `peek_model_call` reads without deducting |
| F07 (P1) | Schema checks were lax; "banana" against a manual criterion counted as the goal being met | A strict JSON schema check (an unknown keyword is refused); `goal_status` distinguishes verified / pending_manual / unverified; a claim-support policy refuses an unverified goal before it reaches the release gate; a modal is no longer taken as a claim's subject |
| F08 (P0 / untrusted code) | `NoSandbox` is not OS-level isolation, but there was only a paragraph saying so | `IsolationReport` states in data what the sandbox can confine; `require_os_isolation` is a "can only be opened" requirement on the lattice — a kernel that cannot meet it refuses to start or refuses that run policy |
| F09 (P1) | The idempotency ledger was in memory only; the bridge stripped the idempotency key; there was no operation ledger | SQLite-persisted `OperationLedger` (no task text and no results); a component with `idempotent=False` whose previous attempt has an unknown outcome is not replayed — the task fails with `OperationUnresolved` and the loop escalates; a timeout is recorded as unknown; the bridge passes the key to `Runtime.invoke` as the runtime's own keyword, neither as an entrypoint argument nor on the wire |
| F10 (P1) | Chinese queries produced zero retrieval terms | `psh.context.terms`: bilingual-lexicon segmentation + Chinese words + character-bigram fallback; shared by capability retrieval and memory recall; intents are matched against the expanded query |
| F11 (P1) | No TCM knowledge layer | `bioagent.tcm`: herbs / processed products (炮制) / formulas (君臣佐使) / syndrome patterns (证候) / classical passages / study evidence / relations (with evidence tier) / safety records (including 十八反); the claim kind determines the required evidence tier; outside the population and condition scope is judged extrapolation; a retracted study supports no claim; a study above expert experience must be retrievable (PMID/DOI/registry id); the seed contains no fabricated trial; 8 native tools |
| F12 (P1/P2) | `record_outcome` was never called; DEGRADED/TIMEOUT semantics were lost; there was no readiness report | The loop feeds tool outcomes back into the registry's priors; DEGRADED crosses the bridge as a `DegradedResult`, becomes a task caveat and a published limitation; TIMEOUT is a `ToolTimeout`; `bioagent doctor` reports the backends, datasets, connectors, tools, the TCM seed, PSH's detector and isolation report, the network proxy, and a remedy for every problem |

<!-- zh -->
| 编号 | 发现 | 处理 |
| --- | --- | --- |
| F01 (P0) | 循环结果绕过发布门：同一句“Drug X cures every cancer”经 Runner 被拒，经循环却 `goal_satisfied` | `LoopResult` 成为内部对象；`Finalizer` 让循环产物走与单次运行相同的隔离区、论断核验、发布门、论断提交；应用只拿到 `ReleasedResult`；`ResearchRunService` 为唯一入口；计划级 `evidence_requirements` 字符串不再满足“需要论断支持”的策略 |
| F02 (P0) | Runner 只按输出文本重新分类，丢掉了上下文标签：源自 PHI 病历的计数以 INTERNAL 放出 | 隔离区中的输出标签 = 上下文投影 ∨ 代理结果标签 ∨ 文本扫描 |
| F03 (P0) | 不允许持久化的运行，检查点仍完整写出目标、任务文本、载荷与错误文本 | 此类运行只写计划形状（id、类型、组件、依赖、绑定），`redacted=True`；恢复必须重新提供目标与形状一致的计划；记录携带已消耗预算 |
| F04 (P0) | 回退分类器不识别中文标识：住院号、身份证、手机号等被判为 INTERNAL | 新增中文规则；临床来源（ehr/emr/chart…）标签下限为 PHI；`require_validated_classifier` 可拒绝在回退检测器上启动；策略允许 PHI 且允许远程目的地时发出告警 |
| F05 (P1) | 上游结果只以 `upstream` 整块传给下游工具，桥会丢弃它；`"$fetch.gene"` 被当作字面量 | `InputBinding`（参数、来源任务、JSON 指针、类型、基数）；校验器拒绝形似引用的字面量并指出应写成的绑定；解析时保留上游标签 |
| F06 (P1) | 父运行看不到子任务消耗；边界检查本身消耗一次模型调用 | 消耗记入运行及其全部祖先，任一层触顶即拒绝；`peek_model_call` 只读不扣 |
| F07 (P1) | 模式检查宽松；人工判据的“banana”被算作目标达成 | 严格 JSON 模式检查（未知关键字即拒绝）；`goal_status` 区分 verified / pending_manual / unverified；需要论断支持的策略在进发布门之前先拒绝未验证目标；情态动词不再被当作论断主语 |
| F08 (P0/不可信代码) | `NoSandbox` 不是操作系统级隔离，但只有文字说明 | `IsolationReport` 以数据说明沙箱能限制什么；`require_os_isolation` 是格上的“只能打开”的要求，内核满足不了即拒绝启动或拒绝该运行策略 |
| F09 (P1) | 幂等账本只在内存；桥剥掉幂等键；没有操作账本 | SQLite 持久化 `OperationLedger`（不含任务文本与结果）；`idempotent=False` 的组件若上一次尝试结果不明则不重放，任务以 `OperationUnresolved` 失败并升级；超时记为 unknown；桥把键作为运行时自身关键字传给 `Runtime.invoke`，不进入入口点也不上线 |
| F10 (P1) | 中文查询产生零检索词 | `psh.context.terms`：双语词典分词 + 中文词 + 二元组回退；能力检索与记忆召回共用；意图匹配用扩展后的查询 |
| F11 (P1) | 缺少中医药知识层 | `bioagent.tcm`：药材/炮制品/方剂（君臣佐使）/证候/经典条文/研究证据/关系（带证据分级）/安全记录（含十八反）；论断种类决定所需证据等级；人群与病种范围之外判为外推；撤稿研究不支持任何论断；专家经验以上的研究必须可检索（PMID/DOI/注册号）；种子不含任何虚构试验；8 个原生工具 |
| F12 (P1/P2) | `record_outcome` 无人调用；DEGRADED/TIMEOUT 语义丢失；无就绪度报告 | 循环回填工具结果到注册表先验；DEGRADED 以 `DegradedResult` 跨桥、成为任务 caveat 与发布的 limitation；TIMEOUT 为 `ToolTimeout`；`bioagent doctor` 给出后端、数据集、连接器、工具、TCM 种子、PSH 检测器与隔离报告、网络代理，以及每个问题的补救办法 |


Still not done, and not claimed: the kernel still ships no OS sandbox (it offers only a report and a policy refusal); the TCM knowledge base is only a seed, with no clinical studies, and needs to be extended by the user with cited records; a public, reproducible real-task benchmark across the two kernels has not been established.

<!-- zh -->
仍未做到、也不声称做到的：内核仍不附带操作系统沙箱（只提供报告与策略拒绝）；中医药知识
库只是种子，没有临床研究，需要用户以带引用的记录扩展；跨两个内核的公开可复现真实任务基准
尚未建立。


---

## F01 — loop results bypassed the release gate (P0) · F01 — 循环结果绕过了发布门（P0）

**Reproduction.** Under a policy with `require_citation=True, require_claim_support=True`,
a one-task plan whose model returned `Drug X cures every cancer.` finished
`goal_satisfied` through `AgentLoopController.run`, with `LoopResult.results["t1"]`
readable and the output gate's counter unchanged. The same sentence through
`Runner.run` was refused at `release_gate`. Same policy, two entrances, two verdicts.

<!-- zh -->
**复现。** 在 `require_citation=True, require_claim_support=True` 的策略下，一个单任务计划，其模型返回了 `Drug X cures every cancer.`，却经 `AgentLoopController.run` 以 `goal_satisfied` 结束，且 `LoopResult.results["t1"]` 可读、输出门的计数器没有变化。同一句话经 `Runner.run` 则在 `release_gate` 被拒。同一策略，两个入口，两种裁决。


**Change.**

<!-- zh -->
**改动。**


* `runtime/finalize.py`: `Finalizer.finalize(result, envelope, sources=..., project_id=...)`
  performs the runner's stages 10–14 over the loop's deliverable: quarantine hold, evidence
  ingestion (records re-validated by signature; bare text untrusted), claim verification,
  the output gate with the policy's `require_citation` / `require_claim_support`, release,
  and claim commit under `PERSISTENT`. The evidence helpers moved here and `Runner` calls
  them, so the two paths share code and cannot drift.
* `ReleasedResult` is what leaves: status `released | refused | not_completed`, the
  released text (only on release), label, termination, `goal_status`, where and why it
  was refused (`refused_at`, `refusal_kind`), counts of claims checked / unsupported /
  uncited, committed claims, limitations, and the quarantine reference. A refusal carries
  no body text; `test_f01_a_refused_result_carries_counts_and_no_text` walks every string
  in the DTO.
* `ResearchRunService.run / resume`: the application-facing door — loop, then finalize.
* `PlanValidator._scientific_violations`: under `require_claim_support`, only a task with
  `evidence_required=True` counts; plan-level `evidence_requirements` strings are
  documentation. The reviewer's plan is now `plan_rejected` before it runs.
* `render_deliverable`: a loop's deliverable is the results of its terminal tasks.

<!-- zh -->
* `runtime/finalize.py`：`Finalizer.finalize(result, envelope, sources=..., project_id=...)` 在循环的交付物上执行 Runner 的第 10–14 阶段：隔离区（quarantine）扣留、证据摄入（记录按签名重新校验；裸文本不可信）、论断核验、带策略 `require_citation` / `require_claim_support` 的输出门、发布，以及 `PERSISTENT` 下的论断提交。证据辅助函数移到这里，由 `Runner` 调用，因此两条路径共用同一份代码，不会漂移。
* 离开的是 `ReleasedResult`：状态 `released | refused | not_completed`，已发布的文本（仅在发布时），标签，终止原因，`goal_status`，被拒的位置与原因（`refused_at`、`refusal_kind`），已检查 / 无支撑 / 无引用的论断计数，已提交论断，限制项，以及隔离区引用。被拒结果不携带正文文本；`test_f01_a_refused_result_carries_counts_and_no_text` 会遍历 DTO 中的每一个字符串。
* `ResearchRunService.run / resume`：面向应用的入口——先循环，再 finalize。
* `PlanValidator._scientific_violations`：在 `require_claim_support` 之下，只有带 `evidence_required=True` 的任务才算数；计划级 `evidence_requirements` 字符串只是文档。审查者的计划现在在运行之前就 `plan_rejected`。
* `render_deliverable`：循环的交付物是其终止任务的结果。


**Tests.** `test_f01_*` (7): validator tightening; the fabricated citation
`[PMID:99999999]` refused at `release_gate` by loop and runner alike; refused DTO carries
no text; the service returns only released results; a supported, signed claim is released
and committed; no `USER_OUTPUT` destination → `EgressDenied`; a loop that did not finish
releases nothing and never reaches the gate.

<!-- zh -->
**测试。** `test_f01_*`（7 个）：校验器收紧；伪造的引用 `[PMID:99999999]` 被循环与 Runner 一并在 `release_gate` 拒绝；被拒的 DTO 不携带文本；服务只返回已发布的结果；一条有支撑、有签名的论断被发布并提交；没有 `USER_OUTPUT` 目的地 → `EgressDenied`；未完成的循环不发布任何内容，也绝不触及发布门。


**Not claimed.** `AgentLoopController.run` still returns a `LoopResult` to callers that
want the internal view (parents, supervisors, resume). Governance is at the door, not in
the object.

<!-- zh -->
**未主张（Not claimed）。** `AgentLoopController.run` 仍会向需要内部视图的调用方（父运行、监督者、恢复）返回 `LoopResult`。治理在门口，而不在对象里。


## F02 — the runner dropped the projection's label (P0) · F02 — Runner 丢掉了投影的标签（P0）

**Reproduction.** A request containing a PHI chart and the question "how many patients
are in this cohort?"; the projection was PHI, the model answered `The cohort count is
17.`, and the gate saw INTERNAL — the label of the text alone.

<!-- zh -->
**复现。** 一个请求包含一份 PHI 病历，以及问题“how many patients are in this cohort?”；投影是 PHI，模型回答了 `The cohort count is 17.`，而门看到的是 INTERNAL——仅凭文本本身的标签。


**Change.** `Runner.run` stage 10 labels the quarantined output with
`fresh.merged_with(combine(projection.label, call.label))`: the join of what the model
was shown, what the broker recorded and what the text says. Derivation never lowers a
label, and this is where the rule is enforced for the single pass. (The loop already did
this at every edge after the v0.5.2 security review.)

<!-- zh -->
**改动。** `Runner.run` 的第 10 阶段用 `fresh.merged_with(combine(projection.label, call.label))` 给隔离区中的输出打标签：即模型被展示的内容、代理记录的内容与文本所述内容的并集。派生绝不降低标签，而在单次运行中，规则正是在这里被强制执行。（在 v0.5.2 安全评审之后，循环已经在每条边上这样做了。）


**Tests.** `test_f02_*` (2): the gate's retained verdict carries PHI; the quarantine hold
carries PHI.

<!-- zh -->
**测试。** `test_f02_*`（2 个）：门所保留的裁决携带 PHI；隔离区扣留物携带 PHI。


## F03 — checkpoints persisted task text a run was not allowed to write (P0) · F03 — 检查点持久化了运行不被允许写出的任务文本（P0）

**Reproduction.** A run without the `PERSISTENT` destination checkpointed with results
withheld — and the objective, the task objective and the payload (an MRN) written in
full to a file the envelope forbade.

<!-- zh -->
**复现。** 一个没有 `PERSISTENT` 目的地的运行做了检查点，结果被扣留——而目标、任务目标与载荷（一个 MRN）却被完整写入信封（envelope）所禁止的文件中。


**Change.** `checkpoint.capture` judges the whole record as a durable write. The
objective's label joined with the plan's is compared like a result's; a run that may not
persist (or whose text exceeds the store's ceiling) writes `objective=""`, the plan's
*shape* only (ids, kinds, components, dependencies, bindings; objectives `"withheld"`,
payloads `{}`, criteria descriptions `"withheld"`), error texts reduced to their class,
and `redacted=True` with `withheld` naming what was withheld. `resume` of a redacted
record requires `objective` and `plan` to be supplied and refuses a plan whose
`plan_shape` differs; a non-redacted record refuses supplied words. The record carries
the run's consumption (`usage`), restored into the governor on resume.

<!-- zh -->
**改动。** `checkpoint.capture` 把整条记录当作一次持久化写入来判定。目标标签与计划标签的合并结果，像结果的标签一样被比较；不允许持久化的运行（或其文本超过存储上限的运行）写出 `objective=""`，只写计划的*形状*（id、类型、组件、依赖、绑定；目标为 `"withheld"`、载荷为 `{}`、判据描述为 `"withheld"`），错误文本归约为其类别，并写出 `redacted=True`，由 `withheld` 指明被扣留的内容。对一条已脱敏记录执行 `resume`，需要提供 `objective` 与 `plan`，并拒绝 `plan_shape` 不同的计划；未脱敏的记录则拒绝外部提供的文字。记录携带该运行的消耗（`usage`），在恢复时回填进预算治理器。


**Tests.** `test_f03_*` (5): no file under the store contains the MRN, the name, the
complaint or the result; the redacted record's fields; a run that may persist still
records its words; resume without the words, with a wrong shape, and with the right
plan (the withheld task runs again); errors reduced to their class.

<!-- zh -->
**测试。** `test_f03_*`（5 个）：存储目录下没有任何文件包含该 MRN、姓名、主诉或结果；已脱敏记录的字段；允许持久化的运行仍然记录其文字；不带文字恢复、带错误形状恢复、带正确计划恢复（被扣留的任务重新运行）；错误归约为其类别。


## F04 — the fallback classifier had no Chinese cues (P0) · F04 — 回退分类器没有中文线索（P0）

**Reproduction.** `患者张三，住院号：12345678，出生日期：1980-01-01` → INTERNAL, below the
`PUBLIC_REMOTE` ceiling; the English counterpart → PHI.

<!-- zh -->
**复现。** `患者张三，住院号：12345678，出生日期：1980-01-01` → INTERNAL，低于 `PUBLIC_REMOTE` 上限；对应的英文样例 → PHI。


**Change.** `kernel/classify.py` fallback rules for 患者姓名 / 患者张三 forms, 住院号 / 病案号
/ 门诊号, 身份证号 (15/18 digits, with the bare 18-digit shape), mobile numbers, 出生日期,
住址, and `Patient name: 王小明`; Chinese field cues; a clinical origin (`clinical`, `ehr`,
`emr`, `his`, `patient_record`, `chart`) floors the label at PHI regardless of the scan.
`PolicySnapshot.require_validated_classifier` (a lattice requirement) refuses a kernel or
a run policy on the fallback; a kernel on the fallback whose policy admits PHI and a
remote destination warns at construction.

<!-- zh -->
**改动。** `kernel/classify.py` 为 患者姓名 / 患者张三 形式、住院号 / 病案号 / 门诊号、身份证号（15/18 位，含裸 18 位形式）、手机号、出生日期、住址，以及 `Patient name: 王小明` 增加回退规则；中文字段线索；临床来源（`clinical`、`ehr`、`emr`、`his`、`patient_record`、`chart`）无论扫描结果如何，都把标签下限设为 PHI。`PolicySnapshot.require_validated_classifier`（一项权限格（authority lattice）要求）拒绝在回退检测器上构建的内核或运行策略；若一个基于回退检测器的内核，其策略允许 PHI 与远程目的地，则在构造时告警。


**Tests.** `test_f04_*`: eight Chinese samples each hit their rule; five research
sentences about 患者 stay INTERNAL with no categories; English cues hold; the clinical
origin floor; the requirement refusal; the lattice dimension; the warning.

<!-- zh -->
**测试。** `test_f04_*`：八个中文样例各自命中其规则；五句关于 患者 的研究语句保持 INTERNAL 且无任何类别；英文线索仍然有效；临床来源下限；要求拒绝；权限格维度；告警。


**Not claimed.** A regex fallback is not a validated detector. An eleven-digit number
starting 13–19 is read as a mobile number even when it is a count, on purpose: the
fallback over-flags rather than under-flags, and `require_validated_classifier` is how a
clinical profile refuses to run on it at all.

<!-- zh -->
**未主张（Not claimed）。** 正则回退不是经验证的检测器。以 13–19 开头的十一位数字，即使它其实是一个计数，也会被读作手机号，这是刻意的：回退宁可过度标记，也不标记不足；而 `require_validated_classifier` 正是临床配置彻底拒绝在其上运行的方式。


## F05 — no typed data flow between tasks (P1) · F05 — 任务之间没有带类型的数据流（P1）

**Reproduction.** `payload={"symbol": "$fetch.gene"}` ran with the literal string; the
upstream result was offered only as `payload["upstream"]`, which the BioScience bridge
drops because no entrypoint argument is called that.

<!-- zh -->
**复现。** `payload={"symbol": "$fetch.gene"}` 以字面量字符串运行；上游结果只以 `payload["upstream"]` 的形式提供，而 BioScience 桥会丢弃它，因为没有任何入口点参数叫这个名字。


**Change.** `InputBinding(argument, source, pointer, expected_type, cardinality, unit,
required)` on `PlanTask.inputs`; `PlanTask` refuses a binding whose source is not a
dependency; the validator refuses an argument that is both literal and bound and a
payload literal that reads like a reference, naming the binding it should have been; the
planner prompt and parser carry `inputs`; `runtime/bindings.py` resolves each binding at
dispatch (RFC 6901 pointers, type and cardinality checks, `BindingError` naming keys and
never values) and the resolved value keeps the upstream result's label; a model task sees
bound values by name; bindings are part of a redacted checkpoint's shape.

<!-- zh -->
**改动。** 在 `PlanTask.inputs` 上引入 `InputBinding(argument, source, pointer, expected_type, cardinality, unit, required)`；`PlanTask` 拒绝来源不是依赖项的绑定；校验器拒绝既是字面量又被绑定的参数，以及形似引用的载荷字面量，并指出它本应写成的绑定；规划器提示词与解析器携带 `inputs`；`runtime/bindings.py` 在分发时解析每个绑定（RFC 6901 指针、类型与基数检查，`BindingError` 只指名键、绝不指名值），且解析后的值保留上游结果的标签；模型任务按名字看到绑定值；绑定是已脱敏检查点形状的一部分。


**Tests.** `test_f05_*` (10), including the reviewer's chain with the second tool
receiving `TP53` and the gate judging the payload under the PHI join.

<!-- zh -->
**测试。** `test_f05_*`（10 个），其中包括审查者的调用链：第二个工具收到 `TP53`，门在 PHI 并集之下判断载荷。


## F06 — the budget did not see its own tasks; the bounds check consumed a call (P1) · F06 — 预算看不到自己的任务；边界检查消耗了一次调用（P1）

**Reproduction.** Two $0.60 model tasks under a $1.00 run: each within its own child
ceiling, the run reading $0.00, $1.20 spent. And a two-step plan of tool calls under
`max_model_calls=1` stopped after the first tool with zero model calls made.

<!-- zh -->
**复现。** 一个 $1.00 的运行下有两个 $0.60 的模型任务：每个都在自己的子上限之内，而运行读到的消耗是 $0.00，实际已花 $1.20。还有一个 `max_model_calls=1` 下的两步工具调用计划，在零次模型调用的情况下于第一个工具之后停止。


**Change.** `BudgetGovernor` learns each envelope's lineage and ceiling; consumption is
charged to the run and every ancestor; a check is refused at any level's ceiling;
`remaining()` is the tightest level; the governor-wide aggregate sums roots only;
`restore()` carries a checkpoint's usage into a resumed run; `peek_model_call` reads
without reserving and the loop's bounds check uses it.

<!-- zh -->
**改动。** `BudgetGovernor` 掌握每个信封（envelope）的谱系与上限；消耗记入该运行及其每一个祖先；任一层触顶即拒绝检查；`remaining()` 取最紧的一层；治理器范围的聚合只对根求和；`restore()` 把检查点的用量带入恢复后的运行；`peek_model_call` 只读取而不预留，循环的边界检查使用它。


**Tests.** `test_f06a_*` (4), `test_f06b_*` (2).

<!-- zh -->
**测试。** `test_f06a_*`（4 个）、`test_f06b_*`（2 个）。


## F07 — lax schema checks and an unjudged goal counted as satisfied (P1) · F07 — 宽松的模式检查，以及未被判定的目标被算作达成（P1）

**Reproduction.** `check_output_schema(True, {"type": "integer"})` passed, as did `-5`
against `minimum: 0`, strings against integer items, extra keys against
`additionalProperties: false`; and a model answering `banana` to "return the HGNC ID and
UniProt accession" finished `goal_satisfied` under a manual criterion.

<!-- zh -->
**复现。** `check_output_schema(True, {"type": "integer"})` 通过了，`-5` 对 `minimum: 0`、字符串对整数项、多余键对 `additionalProperties: false` 也都通过了；还有一个模型在“return the HGNC ID and UniProt accession”之下回答 `banana`，却在人工判据下以 `goal_satisfied` 结束。


**Change.** A strict recursive schema checker (types incl. lists, enum/const, numeric
bounds, string length and pattern, array bounds and uniqueness, required / properties /
additionalProperties, anyOf / oneOf / allOf / not; unknown keywords refused rather than
ignored). `Evaluator.goal_report` returns unmet *and pending*; `Verdict.goal_status` and
`LoopResult.goal_status` are `verified | pending_manual | unverified`; the finalizer
refuses an unverified goal under a claim-support policy (or on request) before the gate.
The claim parser no longer takes a modal for a subject ("Empagliflozin may reduce ..."
parsed as being about "may"), which had refused a correctly cited, signed, hedged claim.

<!-- zh -->
**改动。** 严格递归的模式检查器（类型含列表、enum/const、数值边界、字符串长度与模式、数组边界与唯一性、required / properties / additionalProperties、anyOf / oneOf / allOf / not；未知关键字被拒绝而不是被忽略）。`Evaluator.goal_report` 返回未满足*与待定*；`Verdict.goal_status` 与 `LoopResult.goal_status` 取 `verified | pending_manual | unverified`；在主张支撑（claim support）策略之下（或应请求），finalizer 在进发布门之前先拒绝未验证的目标。论断解析器不再把情态动词当作主语（“Empagliflozin may reduce ...” 曾被解析为关于 “may” 的论断），此前这导致一条引用正确、有签名、有保留的论断被拒。


**Tests.** `test_f07_*` (5, one parametrised over 20 schema cases).

<!-- zh -->
**测试。** `test_f07_*`（5 个，其中一个以 20 个模式用例参数化）。


## F08 — `NoSandbox` is not OS isolation (P0 for untrusted code) · F08 — `NoSandbox` 不是操作系统级隔离（P0/不可信代码）

**Change.** `IsolationReport` (sandbox, os_isolation, process_isolation,
clean_environment, egress_proxy, raw_sockets_confined, filesystem_confined,
memory_limited, `sufficient_for_untrusted_code`) derived from the configured backend;
`PolicySnapshot.require_os_isolation` in `PolicyLattice.REQUIREMENTS`;
`TrustedKernel.check_requirements(policy)` refuses at construction, when a `Runner`
admits a per-run policy and when `ResearchRunService` is built with one; the kernel's
`report()` carries the isolation report and whether the classifier is validated;
`psh.environment_report()` is the public door for readiness tools outside the kernel.

<!-- zh -->
**改动。** `IsolationReport`（sandbox、os_isolation、process_isolation、clean_environment、egress_proxy、raw_sockets_confined、filesystem_confined、memory_limited、`sufficient_for_untrusted_code`）由所配置的后端推导得出；`PolicySnapshot.require_os_isolation` 进入 `PolicyLattice.REQUIREMENTS`；`TrustedKernel.check_requirements(policy)` 在构造时、在 `Runner` 接受一次运行级策略时，以及当 `ResearchRunService` 带着策略构建时拒绝；TK 的 `report()` 携带隔离报告，以及分类器是否经验证；`psh.environment_report()` 是 TK 之外就绪度工具的公开入口。


**Tests.** `test_f08_*` (5).

<!-- zh -->
**测试。** `test_f08_*`（5 个）。


**Not claimed.** No OS sandbox backend ships. The change is that the absence is a fact
the system states and a policy can refuse on, not a paragraph.

<!-- zh -->
**未主张（Not claimed）。** 不附带任何操作系统沙箱后端。改动在于：这一缺失成为系统陈述的事实、以及策略可以据以拒绝的事实，而不再是一段文字。


## F09 — replay safety was in memory and the bridge stripped the key (P1) · F09 — 重放安全只存在于内存，且桥剥掉了键（P1）

**Change.** `runtime/operations.py`: `OperationLedger` (SQLite, `synchronous=FULL`) keyed
by the loop's `<run id>:<task id>`; states RESERVED / RUNNING / SUCCEEDED / FAILED /
UNKNOWN; no task text and no results (component, attempts, a result digest, an error
class). The loop records every tool call before it runs and after it reports; a
component declaring `idempotent=False` whose earlier attempt is RUNNING, UNKNOWN or
SUCCEEDED-without-its-result is not re-run — the task fails with `OperationUnresolved`
and the loop escalates; `ToolTimeout` is recorded as unknown. `ResearchRunService` and
`LocalSubagentBackend` share the ledger with every child. The bridge hands the key to
`Runtime.invoke(idempotency_key=...)`, recorded on the `ToolCalled` event and the
result's metadata, never an entrypoint argument or a wire field; the isolated entrypoint
does the same.

<!-- zh -->
**改动。** `runtime/operations.py`：`OperationLedger`（SQLite，`synchronous=FULL`），以循环的 `<run id>:<task id>` 为键；状态为 RESERVED / RUNNING / SUCCEEDED / FAILED / UNKNOWN；不含任务文本与结果（只记组件、尝试次数、结果摘要、错误类别）。循环在每次工具调用运行之前与上报之后各记一次；一个声明 `idempotent=False` 的组件，若其更早的尝试处于 RUNNING、UNKNOWN 或 SUCCEEDED-但无其结果，则不再重跑——该任务以 `OperationUnresolved` 失败，循环随之升级；`ToolTimeout` 记为 unknown。`ResearchRunService` 与 `LocalSubagentBackend` 与每一个子级共用该账本。桥把幂等性（idempotency）键交给 `Runtime.invoke(idempotency_key=...)`，记录在 `ToolCalled` 事件与结果的元数据上，绝不作为入口点参数或线上字段；隔离的入口点也这样做。


**Tests.** `test_f09_*` (6) in PSH; the key, timeout and degraded tests in
`test_psh_bridge.py`.

<!-- zh -->
**测试。** PSH 中的 `test_f09_*`（6 个）；键、超时与降级测试在 `test_psh_bridge.py` 中。


**Not claimed.** The ledger records; it does not reconcile. Whoever owns the side
effect decides what an in-doubt operation did.

<!-- zh -->
**未主张（Not claimed）。** 账本只记录，不做对账。谁拥有该副作用（side effect），就由谁判定一次存疑操作究竟做了什么。


## F10 — Chinese queries produced no retrieval terms (P1) · F10 — 中文查询产生零检索词（P1）

**Change.** `psh/context/terms.py`: CJK runs split on function characters, segmented
against a checked-in bilingual lexicon (herbs, formulas, syndromes, diseases, study
designs, omics, pharmacology), each match contributing its English; the Chinese words
themselves; character bigrams for what the lexicon lacks. `CapabilityRegistry` and
`MemoryRetriever` share it; intents are matched against the expanded query.

<!-- zh -->
**改动。** `psh/context/terms.py`：CJK 串按虚词切分，再对照一份签入的双语词典（药材、方剂、证候、疾病、研究设计、组学、药理学）分词，每个命中贡献其英文；中文词本身；词典所缺的用字符二元组回退。`CapabilityRegistry` 与 `MemoryRetriever` 共用；意图匹配使用扩展后的查询。


**Tests.** `test_f10_*` (3): terms; a Chinese query resolves an English-described
capability first; memory crosses the language boundary both ways.

<!-- zh -->
**测试。** `test_f10_*`（3 个）：检索词；一条中文查询先解析出以英文描述的能力；记忆双向跨越语言边界。


## F11 — no TCM knowledge representation (P1) · F11 — 缺少中医药知识表示（P1）

**Change.** `bioagent.tcm` (see the BioScience README, v2.6): the typed model, the
evidence tiers and claim kinds, the knowledge base with disambiguation, applicability
(tier, population, condition, retraction), inherited safety and 十八反 compatibility, and
eight native tools. The seed cites only public-domain texts, the pharmacopoeia and a
textbook; a study above the expert tier cannot be added without a PMID, DOI or registry
id.

<!-- zh -->
**改动。** `bioagent.tcm`（见 BioScience README，v2.6）：带类型的模型，证据分级（evidence tier）与论断种类，带去歧义的知识库，适用性（等级、人群、病种、撤稿），继承的安全性与十八反兼容性，以及八个原生工具。种子只引用公有领域文本、药典与一本教科书；高于专家级的研究，没有 PMID、DOI 或注册号就不能加入。


**Tests.** `tests/test_tcm.py` (20).

<!-- zh -->
**测试。** `tests/test_tcm.py`（20 个）。


**Not claimed.** The seed is a seed: 23 herbs, 6 formulas, 8 syndromes, 9 passages, 14
relations, 14 safety records, no clinical studies. It demonstrates the model and the
scope rule; it is not a materia medica.

<!-- zh -->
**未主张（Not claimed）。** 种子只是种子：23 味药材、6 首方剂、8 个证候、9 条条文、14 条关系、14 条安全记录，没有临床研究。它演示的是模型与范围规则；它不是一部本草。


## F12 — readiness and telemetry (P1/P2) · F12 — 就绪度与遥测（P1/P2）

**Change.** The loop calls `CapabilityRegistry.record_outcome` for every tool task;
`DegradedResult` crosses the bridge and the child-process protocol, the broker records
the result as `degraded` with the shortfall as a warning, the loop carries it as a
caveat, the finalizer lists it as a limitation; `ToolTimeout` is its own class (a
`ContractViolation`, still retryable under the default policy); `bioagent doctor`
reports backends, datasets, connectors, tools, the TCM seed, PSH's detector and isolation,
the network proxy, and a verdict with a remedy per problem.

<!-- zh -->
**改动。** 循环为每一个工具任务调用 `CapabilityRegistry.record_outcome`；`DegradedResult` 跨过桥与子进程协议，代理把该结果记为 `degraded` 并把缺额作为告警，循环把它作为一条注意事项（caveat）携带，finalizer 把它列为一项限制（limitation）；`ToolTimeout` 自成一类（一种 `ContractViolation`，在默认策略下仍可重试）；`bioagent doctor` 报告后端、数据集、连接器、工具、TCM 种子、PSH 的检测器与隔离情况、网络代理，以及每个问题附带补救办法的结论。


**Tests.** `test_f12_*` (3) in PSH; `tests/test_doctor.py` (4).

<!-- zh -->
**测试。** PSH 中的 `test_f12_*`（3 个）；`tests/test_doctor.py`（4 个）。


## What the reviewer asked for first, and where it stands · 审查者最先要求的事，以及它们目前的进展

* **A unified governance loop** — done: one `Finalizer`, one door (`ResearchRunService`),
  one budget tree, one operation ledger.
* **A TCM evidence-scope model** — the foundation is done (`bioagent.tcm`,
  `Applicability`); the corpus is the open work.
* **A public, reproducible real-task benchmark** — open. The probe under
  `scratchpad/review/probes.py` is the shape of it: synthetic, offline, per finding.

<!-- zh -->
* **统一的治理循环**——已完成：一个 `Finalizer`、一个入口（`ResearchRunService`）、一棵预算树、一份操作账本。
* **中医药证据范围（evidence scope）模型**——基础已完成（`bioagent.tcm`、`Applicability`）；语料库是仍然开放的工作。
* **公开的、可复现的真实任务基准**——未完成（仍然开放）。`scratchpad/review/probes.py` 下的探针就是它的雏形：合成的、离线的、逐项发现对应。
