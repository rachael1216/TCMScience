# Response to the 2026-09-18 architecture review

The review ("TCMScience 智能体架构与能力审查", 2026-09-18) accepted the v0.5.2 premise —
a trusted kernel with a bounded loop on top — and probed the merged tree with synthetic
data. Its finding was that the governance closed around a single `Runner` pass had not
closed around the loop, and that the capability plane had no representation of what a
traditional-medicine claim is. Twelve findings, F01–F12, four of them P0.

This document is the record: for each finding, the reproduction, the change, the test,
and what it does not claim. The probe that reproduced F01–F07, F09 and F10 against the
merged tree is the one the tests in `tests/test_review_2026_09_18.py` (PSH) and
`tests/test_tcm.py`, `tests/test_doctor.py`, `tests/test_psh_bridge.py` (BioScience)
are written from.

Versions: PSH-Harness 0.5.2 → **0.5.3**, BioScience-Harness 0.2.5 → **0.2.6**.

## 中文摘要

审查者用合成数据对已合并的代码树做了探测，发现围绕单次 `Runner` 运行建立的治理并没有
覆盖循环（`AgentLoopController`），能力平面也没有对“中医药论断”本身的表示。十二项发现
已全部处理，每一项都先复现、再修复、再以回归测试固定：

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

仍未做到、也不声称做到的：内核仍不附带操作系统沙箱（只提供报告与策略拒绝）；中医药知识
库只是种子，没有临床研究，需要用户以带引用的记录扩展；跨两个内核的公开可复现真实任务基准
尚未建立。

---

## F01 — loop results bypassed the release gate (P0)

**Reproduction.** Under a policy with `require_citation=True, require_claim_support=True`,
a one-task plan whose model returned `Drug X cures every cancer.` finished
`goal_satisfied` through `AgentLoopController.run`, with `LoopResult.results["t1"]`
readable and the output gate's counter unchanged. The same sentence through
`Runner.run` was refused at `release_gate`. Same policy, two entrances, two verdicts.

**Change.**

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

**Tests.** `test_f01_*` (7): validator tightening; the fabricated citation
`[PMID:99999999]` refused at `release_gate` by loop and runner alike; refused DTO carries
no text; the service returns only released results; a supported, signed claim is released
and committed; no `USER_OUTPUT` destination → `EgressDenied`; a loop that did not finish
releases nothing and never reaches the gate.

**Not claimed.** `AgentLoopController.run` still returns a `LoopResult` to callers that
want the internal view (parents, supervisors, resume). Governance is at the door, not in
the object.

## F02 — the runner dropped the projection's label (P0)

**Reproduction.** A request containing a PHI chart and the question "how many patients
are in this cohort?"; the projection was PHI, the model answered `The cohort count is
17.`, and the gate saw INTERNAL — the label of the text alone.

**Change.** `Runner.run` stage 10 labels the quarantined output with
`fresh.merged_with(combine(projection.label, call.label))`: the join of what the model
was shown, what the broker recorded and what the text says. Derivation never lowers a
label, and this is where the rule is enforced for the single pass. (The loop already did
this at every edge after the v0.5.2 security review.)

**Tests.** `test_f02_*` (2): the gate's retained verdict carries PHI; the quarantine hold
carries PHI.

## F03 — checkpoints persisted task text a run was not allowed to write (P0)

**Reproduction.** A run without the `PERSISTENT` destination checkpointed with results
withheld — and the objective, the task objective and the payload (an MRN) written in
full to a file the envelope forbade.

**Change.** `checkpoint.capture` judges the whole record as a durable write. The
objective's label joined with the plan's is compared like a result's; a run that may not
persist (or whose text exceeds the store's ceiling) writes `objective=""`, the plan's
*shape* only (ids, kinds, components, dependencies, bindings; objectives `"withheld"`,
payloads `{}`, criteria descriptions `"withheld"`), error texts reduced to their class,
and `redacted=True` with `withheld` naming what was withheld. `resume` of a redacted
record requires `objective` and `plan` to be supplied and refuses a plan whose
`plan_shape` differs; a non-redacted record refuses supplied words. The record carries
the run's consumption (`usage`), restored into the governor on resume.

**Tests.** `test_f03_*` (5): no file under the store contains the MRN, the name, the
complaint or the result; the redacted record's fields; a run that may persist still
records its words; resume without the words, with a wrong shape, and with the right
plan (the withheld task runs again); errors reduced to their class.

## F04 — the fallback classifier had no Chinese cues (P0)

**Reproduction.** `患者张三，住院号：12345678，出生日期：1980-01-01` → INTERNAL, below the
`PUBLIC_REMOTE` ceiling; the English counterpart → PHI.

**Change.** `kernel/classify.py` fallback rules for 患者姓名 / 患者张三 forms, 住院号 / 病案号
/ 门诊号, 身份证号 (15/18 digits, with the bare 18-digit shape), mobile numbers, 出生日期,
住址, and `Patient name: 王小明`; Chinese field cues; a clinical origin (`clinical`, `ehr`,
`emr`, `his`, `patient_record`, `chart`) floors the label at PHI regardless of the scan.
`PolicySnapshot.require_validated_classifier` (a lattice requirement) refuses a kernel or
a run policy on the fallback; a kernel on the fallback whose policy admits PHI and a
remote destination warns at construction.

**Tests.** `test_f04_*`: eight Chinese samples each hit their rule; five research
sentences about 患者 stay INTERNAL with no categories; English cues hold; the clinical
origin floor; the requirement refusal; the lattice dimension; the warning.

**Not claimed.** A regex fallback is not a validated detector. An eleven-digit number
starting 13–19 is read as a mobile number even when it is a count, on purpose: the
fallback over-flags rather than under-flags, and `require_validated_classifier` is how a
clinical profile refuses to run on it at all.

## F05 — no typed data flow between tasks (P1)

**Reproduction.** `payload={"symbol": "$fetch.gene"}` ran with the literal string; the
upstream result was offered only as `payload["upstream"]`, which the BioScience bridge
drops because no entrypoint argument is called that.

**Change.** `InputBinding(argument, source, pointer, expected_type, cardinality, unit,
required)` on `PlanTask.inputs`; `PlanTask` refuses a binding whose source is not a
dependency; the validator refuses an argument that is both literal and bound and a
payload literal that reads like a reference, naming the binding it should have been; the
planner prompt and parser carry `inputs`; `runtime/bindings.py` resolves each binding at
dispatch (RFC 6901 pointers, type and cardinality checks, `BindingError` naming keys and
never values) and the resolved value keeps the upstream result's label; a model task sees
bound values by name; bindings are part of a redacted checkpoint's shape.

**Tests.** `test_f05_*` (10), including the reviewer's chain with the second tool
receiving `TP53` and the gate judging the payload under the PHI join.

## F06 — the budget did not see its own tasks; the bounds check consumed a call (P1)

**Reproduction.** Two $0.60 model tasks under a $1.00 run: each within its own child
ceiling, the run reading $0.00, $1.20 spent. And a two-step plan of tool calls under
`max_model_calls=1` stopped after the first tool with zero model calls made.

**Change.** `BudgetGovernor` learns each envelope's lineage and ceiling; consumption is
charged to the run and every ancestor; a check is refused at any level's ceiling;
`remaining()` is the tightest level; the governor-wide aggregate sums roots only;
`restore()` carries a checkpoint's usage into a resumed run; `peek_model_call` reads
without reserving and the loop's bounds check uses it.

**Tests.** `test_f06a_*` (4), `test_f06b_*` (2).

## F07 — lax schema checks and an unjudged goal counted as satisfied (P1)

**Reproduction.** `check_output_schema(True, {"type": "integer"})` passed, as did `-5`
against `minimum: 0`, strings against integer items, extra keys against
`additionalProperties: false`; and a model answering `banana` to "return the HGNC ID and
UniProt accession" finished `goal_satisfied` under a manual criterion.

**Change.** A strict recursive schema checker (types incl. lists, enum/const, numeric
bounds, string length and pattern, array bounds and uniqueness, required / properties /
additionalProperties, anyOf / oneOf / allOf / not; unknown keywords refused rather than
ignored). `Evaluator.goal_report` returns unmet *and pending*; `Verdict.goal_status` and
`LoopResult.goal_status` are `verified | pending_manual | unverified`; the finalizer
refuses an unverified goal under a claim-support policy (or on request) before the gate.
The claim parser no longer takes a modal for a subject ("Empagliflozin may reduce ..."
parsed as being about "may"), which had refused a correctly cited, signed, hedged claim.

**Tests.** `test_f07_*` (5, one parametrised over 20 schema cases).

## F08 — `NoSandbox` is not OS isolation (P0 for untrusted code)

**Change.** `IsolationReport` (sandbox, os_isolation, process_isolation,
clean_environment, egress_proxy, raw_sockets_confined, filesystem_confined,
memory_limited, `sufficient_for_untrusted_code`) derived from the configured backend;
`PolicySnapshot.require_os_isolation` in `PolicyLattice.REQUIREMENTS`;
`TrustedKernel.check_requirements(policy)` refuses at construction, when a `Runner`
admits a per-run policy and when `ResearchRunService` is built with one; the kernel's
`report()` carries the isolation report and whether the classifier is validated;
`psh.environment_report()` is the public door for readiness tools outside the kernel.

**Tests.** `test_f08_*` (5).

**Not claimed.** No OS sandbox backend ships. The change is that the absence is a fact
the system states and a policy can refuse on, not a paragraph.

## F09 — replay safety was in memory and the bridge stripped the key (P1)

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

**Tests.** `test_f09_*` (6) in PSH; the key, timeout and degraded tests in
`test_psh_bridge.py`.

**Not claimed.** The ledger records; it does not reconcile. Whoever owns the side
effect decides what an in-doubt operation did.

## F10 — Chinese queries produced no retrieval terms (P1)

**Change.** `psh/context/terms.py`: CJK runs split on function characters, segmented
against a checked-in bilingual lexicon (herbs, formulas, syndromes, diseases, study
designs, omics, pharmacology), each match contributing its English; the Chinese words
themselves; character bigrams for what the lexicon lacks. `CapabilityRegistry` and
`MemoryRetriever` share it; intents are matched against the expanded query.

**Tests.** `test_f10_*` (3): terms; a Chinese query resolves an English-described
capability first; memory crosses the language boundary both ways.

## F11 — no TCM knowledge representation (P1)

**Change.** `bioagent.tcm` (see the BioScience README, v2.6): the typed model, the
evidence tiers and claim kinds, the knowledge base with disambiguation, applicability
(tier, population, condition, retraction), inherited safety and 十八反 compatibility, and
eight native tools. The seed cites only public-domain texts, the pharmacopoeia and a
textbook; a study above the expert tier cannot be added without a PMID, DOI or registry
id.

**Tests.** `tests/test_tcm.py` (20).

**Not claimed.** The seed is a seed: 23 herbs, 6 formulas, 8 syndromes, 9 passages, 14
relations, 14 safety records, no clinical studies. It demonstrates the model and the
scope rule; it is not a materia medica.

## F12 — readiness and telemetry (P1/P2)

**Change.** The loop calls `CapabilityRegistry.record_outcome` for every tool task;
`DegradedResult` crosses the bridge and the child-process protocol, the broker records
the result as `degraded` with the shortfall as a warning, the loop carries it as a
caveat, the finalizer lists it as a limitation; `ToolTimeout` is its own class (a
`ContractViolation`, still retryable under the default policy); `bioagent doctor`
reports backends, datasets, connectors, tools, the TCM seed, PSH's detector and isolation,
the network proxy, and a verdict with a remedy per problem.

**Tests.** `test_f12_*` (3) in PSH; `tests/test_doctor.py` (4).

## What the reviewer asked for first, and where it stands

* **A unified governance loop** — done: one `Finalizer`, one door (`ResearchRunService`),
  one budget tree, one operation ledger.
* **A TCM evidence-scope model** — the foundation is done (`bioagent.tcm`,
  `Applicability`); the corpus is the open work.
* **A public, reproducible real-task benchmark** — open. The probe under
  `scratchpad/review/probes.py` is the shape of it: synthetic, offline, per finding.
