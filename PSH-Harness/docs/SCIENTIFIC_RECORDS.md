# Scientific records and protocol deviations · 科学记录与方案偏离

`psh.scientist` adds structured hypotheses, preregistered protocols and observations
to WorkGraph. It is an append-only application API: recording results creates new
nodes, and never updates the original hypothesis or protocol. This is the second
increment of the Scientific Compiler roadmap, not an autonomous experiment runner.

<!-- zh -->
`psh.scientist` 把结构化的假设、预注册方案与观察加入 WorkGraph。它是一个只追加（append-only）的应用 API：记录结果会创建新节点，且永不更新原来的假设或方案。这是科学编译器路线图的第二个增量，不是一个自主实验运行器。


## Model · 模型

- `Hypothesis`: proposition, population, predictions, falsifiers and alternatives.
- `Protocol`: primary/secondary endpoints, exclusion criteria, statistical test,
  sample size assumptions, covariates, subgroup plan and stopping criteria.
- `Observation`: summary, artifact references and observed/negative/inconclusive outcome.
- `Deviation`: explicit reason and actual protocol, embedded atomically in the
  observation together with a field-by-field planned/actual comparison.

<!-- zh -->
上述四个条目依次译为：
- `Hypothesis`：命题、人群、预测、否证条件与备择解释。
- `Protocol`：主要/次要终点、排除标准、统计检验、样本量假设、协变量、亚组计划与停止准则。
- `Observation`：摘要、产物引用以及观察到的/阴性/不确定的结局。
- `Deviation`：明确的原因与实际执行的方案，与一份逐字段的"计划/实际"对照一起原子地嵌入该观察之中。


All record objects are frozen dataclasses using immutable tuples. A protocol hash
is a SHA-256 digest of canonical JSON; whitespace inside scientific text remains
significant. A stored record also hashes its schema version, type, contents and
source IDs. `read()` refuses altered bodies or unsupported schema versions.

<!-- zh -->
所有记录对象都是使用不可变元组的冻结 dataclass。方案哈希是规范 JSON 的 SHA-256 摘要；科学文本内部的空白仍然有意义。已存储的记录还会对它自己的模式版本、类型、内容与来源 ID 求哈希。`read()` 拒绝被改动的正文或不受支持的模式版本。


These hashes detect accidental edits, not an adversary who can rewrite the SQLite
database and recompute hashes. They are not signatures or timestamp attestations.
The application must actually register a protocol before data collection; the API
does not certify when an external experiment occurred. Artifact references are
recorded, not fetched or independently verified.

<!-- zh -->
这些哈希检测的是意外编辑，而不是一个能够改写 SQLite 数据库并重新计算哈希的对手。它们不是签名，也不是时间戳见证。应用必须在数据收集之前真正注册方案；该 API 不证明外部实验发生在何时。产物引用是被记录，而不是被获取或独立验证。


## Use · 使用

```python
from psh.scientist import ScientificLedger, Hypothesis, Protocol, Observation

# kernel is a TrustedKernel; project_id identifies an existing WorkGraph project.
ledger = ScientificLedger(kernel, project_id)
envelope = kernel.policy.envelope()
h = ledger.hypothesize(Hypothesis(
    "Fixture intervention changes endpoint", "fixture population",
    ("endpoint changes",), ("no endpoint change",), ("batch effect",),
), envelope)
p = Protocol("endpoint A", (), "prespecified exclusions", "permutation test",
             "simulation-based precision assumption", (), "none", "fixed sample size")
registration = ledger.preregister(h.id, p, envelope)
result = ledger.observe(registration.id, Observation(
    "No effect in synthetic fixture data", ("fixture:data-1",), "negative",
), p, envelope)
record = ledger.read(result.id, envelope)
```

If any of the eight protocol fields changes, `observe()` requires a nonempty
`deviation_reason`. The observation stores both protocol hashes, the actual
protocol, and changed fields. The original registration remains untouched. Each
observation is a distinct record; the caller should retain returned node IDs and
reconcile uncertain delivery before retrying. This API does not provide exactly-once
record delivery or automatically deduplicate scientifically distinct observations.

<!-- zh -->
如果八个方案字段中的任何一个发生变化，`observe()` 就要求一个非空的 `deviation_reason`。该观察会存储两个方案哈希、实际执行的方案以及发生变化的字段。原来的注册保持不动。每条观察都是一条独立的记录；调用者应当保留返回的节点 ID，并在重试之前对不确定的投递做核对。该 API 不提供恰好一次的记录投递，也不自动去重科学上不同的观察。


## Governance and persistence · 治理与持久化

Writes pass through `PersistenceGateway`, using the meet of the supplied run
envelope and current kernel policy. The ledger requires PERSISTENT authority and
enforces project membership and sensitivity ceilings. Source and project labels
are joined into each new record's label; callers can also supply `label=...` for
derived content whose sensitivity cannot be inferred from its text.

<!-- zh -->
写入经过 `PersistenceGateway`，使用所提供的运行信封与当前内核策略的交。账本要求 PERSISTENT 权限，并强制项目成员关系与敏感度上限。来源与项目的标签被并入每条新记录的标签；对于无法从其文本推断敏感度的派生内容，调用者也可以提供 `label=...`。


The gateway accepts an `inherited_label` floor and classifies reference and
provenance fields as well as title/body/metadata. It atomically inserts the node
and outgoing source edges with a WorkGraph savepoint. Failed edge insertion rolls
back the node and does not increment the gateway commit count. A reentrant graph
lock serializes transaction writes on the shared connection. Audit notification
occurs after the graph write; graph and audit databases are not one distributed
transaction, and an audit failure is not an exactly-once guarantee.

<!-- zh -->
网关接受一个 `inherited_label` 下限，并对引用与溯源字段以及 title/body/metadata 一并分类。它用一个 WorkGraph 保存点（savepoint）原子地插入节点与外出的来源边。边插入失败会回滚该节点，且不会增加网关的提交计数。一个可重入的图锁在共享连接上串行化事务写入。审计通知发生在图写入之后；图数据库与审计数据库不是同一个分布式事务，审计失败也不是一个恰好一次的保证。


Graph edges are `DERIVED_FROM`, so `lineage()` and `why()` can trace an observation
back to its protocol and hypothesis. They express derivation, not verified support.
All scientific records are stored as CANDIDATE, and no hypotheses or observations
are automatically promoted to verified knowledge. Even when explicitly selecting
these new node kinds, normal memory retrieval still applies its validation gate.

<!-- zh -->
图的边是 `DERIVED_FROM`，因此 `lineage()` 与 `why()` 可以把一条观察追溯回它的方案与假设。它们表达的是派生关系，不是经过验证的支撑。所有科学记录都存为 CANDIDATE，没有任何假设或观察会被自动晋级为已验证的知识。即使显式选择这些新节点种类，常规的记忆检索仍然会施加它的校验门。


WorkGraph itself remains a trusted low-level mutable store. The append-only
contract belongs to ScientificLedger; direct database/graph mutations are not
made impossible. Use the ledger for scientific records and retain normal kernel
egress controls when displaying or exporting returned contents.

<!-- zh -->
WorkGraph 本身仍然是一个可信的低层可变存储。只追加这一契约属于 ScientificLedger；直接的数据库/图变更是无法被禁止的（not made impossible）。请使用账本记录科学记录，并在展示或导出返回内容时保留常规的内核出口控制。


## Validation and remaining work · 验证与剩余工作

Run `python -m pytest -q tests/test_scientist_records.py` from PSH-Harness.
Tests cover all eight protocol fields, PHI inheritance, narrowed authority,
cross-project refusal, hash mismatch, durable reopening, atomic rollback,
concurrent write isolation and exclusion from verified memory.

<!-- zh -->
在 PSH-Harness 下运行 `python -m pytest -q tests/test_scientist_records.py`。测试覆盖全部八个方案字段、PHI 继承、收窄后的权限、跨项目拒绝、哈希不匹配、持久化重开、原子回滚、并发写入隔离，以及被排除在已验证记忆之外。


Not implemented here: hypothesis competition or ranking, scientific belief updates,
statistical validity checks, experiment execution, event-journal replay, remote
attestation, or automatic claim publication. This API can record scientifically
incorrect proposals; it preserves what was planned and changed so those proposals
can be reviewed.

<!-- zh -->
此处未实现：假设竞争或排序、科学信念更新、统计有效性检查、实验执行、事件日志重放、远程见证，或自动的主张发布。该 API 可以记录科学上不正确的提案；它保留当初计划了什么、又改动了什么，以便这些提案能够被审查。

