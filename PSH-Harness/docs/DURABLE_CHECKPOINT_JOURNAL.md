# Durable checkpoint journal and conservative operation recovery · 持久检查点日志与保守的操作恢复

`JournalCheckpointStore` is an opt-in replacement for `CheckpointStore` in the
existing loop's `checkpoints=` argument. It stores append-only governed snapshots
in SQLite, with WAL and `synchronous=FULL`, rather than overwriting a snapshot.
It does not implement replay of every tool/model/event, workflow amendment or an
exactly-once distributed execution engine.

<!-- zh -->
`JournalCheckpointStore` 是现有循环 `checkpoints=` 参数中 `CheckpointStore` 的可选替代品。它把受治理的快照以只追加方式存入 SQLite，使用 WAL 与 `synchronous=FULL`，而不是覆盖一个快照。它不实现每一次工具/模型/事件的重放、工作流修正，也不是一个恰好一次的分布式执行引擎。


## Integration · 集成

```python
from psh.runtime import AgentLoopController, JournalCheckpointStore, OperationLedger, resume

# kernel, planner and registry are the application's existing configured components.
operations = OperationLedger("research-state/operations.sqlite")
try:
    with JournalCheckpointStore("research-state/checkpoints.sqlite") as journal:
        controller = AgentLoopController(kernel, planner=planner, registry=registry,
                                         checkpoints=journal, operations=operations)
        result = controller.run("research objective", kernel.policy.envelope())
        anchor = journal.anchor()  # retain externally if suffix-loss detection is needed
finally:
    operations.close()
```

After a restart, reopen **both** stores. Select the prior loop's checkpoint with
`journal.latest_for(loop_id)`, call `resume(checkpoint, kernel)`, then run the
controller with `resume_from=state` and `state.envelope`. Resume still narrows the
old authority against current policy, restores budget usage, and reclassifies
restored results. Redacted snapshots still require the original plan/objective
to be supplied through the existing `resume` API. Missing checkpoints return
`None`; the application must handle that before calling `resume`.

<!-- zh -->
重启之后，**两个**存储都要重新打开。用 `journal.latest_for(loop_id)` 选出先前循环的检查点，调用 `resume(checkpoint, kernel)`，然后以 `resume_from=state` 与 `state.envelope` 运行控制器。恢复仍然会对照当前策略收窄旧权限、还原预算用量并重新分类被还原的结果。脱敏后的快照仍然需要通过既有的 `resume` API 提供原来的计划/目标。缺失的检查点返回 `None`；应用必须在调用 `resume` 之前处理这种情况。


The journal stores what `capture()` gives it, including original evidence references,
result labels, withholding reasons and plan sensitivity floors. The loop already
uses governed capture: forbidden persistent content is withheld before save. Like
the file checkpoint store, this low-level store cannot authorize arbitrary caller
data. Do not hand it manually assembled snapshots containing ungoverned content.

<!-- zh -->
日志存储 `capture()` 给它的东西，包括原始证据引用、结果标签、扣留原因与计划敏感度下限。循环已经使用受治理的捕获：被禁止持久化的内容在保存之前就被扣留。与文件检查点存储一样，这个低层存储无法对任意的调用者数据授权。不要交给它手工拼装、含有未受治理内容的快照。


## Integrity and ordering · 完整性与顺序

Each record has a monotonically increasing sequence, schema version, checkpoint
identity, canonical serialized snapshot, prior record hash and SHA-256 hash.
Checkpoint payload hashes are checked independently of journal hashes. Appending
reads and verifies the chain and inserts under `BEGIN IMMEDIATE`, serializing
writers across connections/processes. Duplicate delivery of the same checkpoint
is idempotent; reuse of an ID with different contents is refused.

<!-- zh -->
每条记录都有一个单调递增的序号、模式版本、检查点身份、规范序列化的快照、前一条记录的哈希与一个 SHA-256 哈希。检查点载荷哈希独立于日志哈希被检查。追加时会读取并校验整条链，并在 `BEGIN IMMEDIATE` 之下插入，从而跨连接/进程串行化写入者。同一检查点的重复投递是幂等的；以不同内容复用同一个 ID 会被拒绝。


Readers verify the complete chain before returning any result. Invalid versions,
missing middle entries, changed contents or identity mismatches cause refusal;
there is no silent fallback to an older apparently good snapshot. `latest_for`
uses append order, not a caller's wall clock. Unsupported/non-finite JSON values
are refused rather than converted into lossy string representations.

<!-- zh -->
读取者在返回任何结果之前校验完整链。无效版本、缺失的中间条目、被改动的内容或身份不匹配都会导致拒绝；不存在静默回退到一个更旧、看起来完好的快照。`latest_for` 使用追加顺序，而不是调用者的墙上时钟。不受支持/非有限的 JSON 取值会被拒绝，而不是被转换成有损的字符串表示。


`anchor()` returns `(record_count, head_hash)`. Passing that exact previously saved
head to `load`, `all` or `latest_for` as `expected_anchor=` detects suffix loss,
including an emptied journal. It also rejects a newer head: this is an exact-head
check, not a prefix-inclusion proof. Without an external anchor, removing a valid
suffix or rewriting the whole database cannot be detected reliably. Hashes are
tamper evidence, not cryptographic signatures.

<!-- zh -->
`anchor()` 返回 `(record_count, head_hash)`。把这个先前保存的精确头部作为 `expected_anchor=` 传给 `load`、`all` 或 `latest_for`，可以检测后缀丢失，包括日志被清空。它也会拒绝更新的头部：这是精确头部检查，不是前缀包含性证明。没有外部锚点时，删除一个有效后缀或改写整个数据库无法被可靠地检测。哈希是防篡改可见性证据，不是密码学签名。


The current implementation verifies all snapshots on each append/read, with O(n)
work per operation and O(n) read memory. It targets bounded prototype runs; it does
not claim efficient replay of unbounded journals. The append schema is version 1;
automatic upgrade migration is not implemented.

<!-- zh -->
当前实现在每次追加/读取时校验所有快照，每次操作为 O(n) 工作量、读取内存为 O(n)。它的目标是有界的原型运行；它不声称能高效重放无界日志。追加模式为版本 1；自动升级迁移未实现。


## Side-effect recovery · 副作用恢复

Checkpoint history is insufficient to determine whether an external operation
finished before the process crashed. Use the durable `OperationLedger` alongside
the journal. This increment hardens that ledger:

- `begin()` checks replay safety and records the attempt in one SQL transaction.
  Two connections cannot both admit the same uncertain non-idempotent operation.
- A recorded non-idempotent operation cannot become safe to replay merely because
  a new manifest declares it idempotent. Known invocation identity must also match.
- A non-idempotent tool's ordinary execution exception, as well as a timeout,
  leaves the outcome UNKNOWN. The tool may have changed the world before raising.
  Automatic retries are refused until the operation is reconciled.

<!-- zh -->
检查点历史不足以判定一个外部操作在进程崩溃之前是否已经完成。请把持久化的 `OperationLedger` 与日志配合使用。本增量加固了该账本：

- `begin()` 在一个 SQL 事务中检查重放安全性并记录该次尝试。两个连接不能同时准入同一个不确定的非幂等操作。
- 一条已记录的非幂等操作，不能仅因为一份新清单声明它是幂等的就变得可以安全重放。已知的调用身份也必须匹配。
- 一个非幂等工具的普通执行异常，以及超时，都会让结局保持 UNKNOWN。该工具可能在抛出之前已经改变了世界。在操作被核对之前，自动重试会被拒绝。


Idempotent work can still run again; this is not an execution lease or a guarantee
that the provider implements idempotency correctly. Existing pre-dispatch policy
and budget refusals remain distinct from execution failures. Do not delete an
uncertain operation row to force a retry. Reconciliation must establish the actual
external outcome; this change does not add an operator reconciliation UI.

<!-- zh -->
幂等的工作仍然可以再次运行；这不是执行租约，也不是"提供方正确实现了幂等性"的保证。既有的分发前策略与预算拒绝仍然与执行失败相区别。不要为了强行重试而删除一条存疑的操作行。核对必须确立实际的外部结局；本变更没有加入操作员核对界面。


The loop still records checkpoint failures as audit events and continues running.
It does not promise crash recovery for progress that could not be checkpointed,
or an atomic transaction spanning provider effects, operation rows and snapshots.

<!-- zh -->
循环仍然把检查点失败记录为审计事件并继续运行。它不承诺对无法被检查点的进度做崩溃恢复，也不承诺一个横跨提供方效果、操作行与快照的原子事务。


## Verification · 验证

`tests/test_journal.py` covers durable reopen, duplicate delivery, clock skew,
corruption, unknown schema, suffix deletion with an anchor, rollback of an
uncommitted SQL transaction, concurrent connections, withheld content, resumed
tool execution retaining evidence references, atomic operation admission and
non-idempotent exceptions. It runs together with existing checkpoint, agent-loop
and durability suites in CI. These tests do not simulate physical power loss or
prove filesystem hardware durability.

<!-- zh -->
`tests/test_journal.py` 覆盖持久化重开、重复投递、时钟偏移、损坏、未知模式、带锚点的后缀删除、未提交 SQL 事务的回滚、并发连接、被扣留内容、保留证据引用的恢复后工具执行、原子操作准入与非幂等异常。它与既有的检查点、智能体循环与持久性测试套件一起在 CI 中运行。这些测试不模拟物理断电，也不证明文件系统硬件的持久性。

