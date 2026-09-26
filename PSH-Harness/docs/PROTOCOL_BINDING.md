# Binding workflows to registered protocols · 把工作流绑定到已注册方案

An optional `TaskContract.protocol_binding` ties a statistical design to a
`ScientificLedger` protocol record and its expected content fingerprint:

<!-- zh -->
一个可选的 `TaskContract.protocol_binding` 把一份统计设计系到一条 `ScientificLedger` 方案记录及其预期内容指纹上：


```python
from psh.workflow import ProtocolBinding, ScientificCompiler

# registered is the Node returned by ledger.preregister(...).
# protocol is the Protocol used to construct the task's StatisticalDesign.
binding = ProtocolBinding(registered.id, protocol.fingerprint)
# Set protocol_binding=binding on that task's TaskContract.
compiler = ScientificCompiler(scientific_ledger=ledger)
compiled = compiler.compile(program, envelope, policy=kernel.policy)
```

The bound task must explicitly include `Destination.PERSISTENT` in destinations
and `Effect.PERSIST` in effects, alongside its other execution destinations and
effects. This is the existing ledger's conservative access requirement, including
for reads; the compiler does not grant missing authority. Binding resolution
itself writes no records. Under the current effect model, such tasks cannot be
declared PURE. The run and current ledger policy must also permit this access.

<!-- zh -->
被绑定的任务必须在其目的地中显式包含 `Destination.PERSISTENT`，在效果中显式包含 `Effect.PERSIST`，与其它的执行目的地与效果并列。这是既有账本的保守访问要求，对读取也是如此；编译器不会补授缺失的权限。绑定解析本身不写入任何记录。在当前的效果模型下，这类任务不能被声明为 PURE。该运行与当前账本策略也必须允许这次访问。


The resolver reads content and label in one WorkGraph transaction, enforces project
and sensitivity boundaries, checks the record content hash, reconstructs a typed
Protocol and checks its fingerprint. The compiler compares that fingerprint with
the binding and compares all protocol fields with `statistics.protocol`.

<!-- zh -->
解析器在一个 WorkGraph 事务中读取内容与标签，强制项目与敏感度边界，检查记录的内容哈希，重建一个有类型的 Protocol 并检查其指纹。编译器把该指纹与绑定相比较，并把所有方案字段与 `statistics.protocol` 相比较。


| Code | Reason for rejection |
| --- | --- |
| PROTOCOL101 | Bound task has no statistical design |
| PROTOCOL102 | No scientific ledger supplied to compiler |
| PROTOCOL103 | Record unavailable, wrong kind/project, invalid or unauthorized |
| PROTOCOL104 | Expected fingerprint differs from registered protocol |
| PROTOCOL105 | Analysis protocol differs from registered protocol |

<!-- zh -->
| 代码 | 拒绝原因 |
| --- | --- |
| PROTOCOL101 | 被绑定的任务没有统计设计 |
| PROTOCOL102 | 未向编译器提供科学账本 |
| PROTOCOL103 | 记录不可用、种类/项目错误、无效或未授权 |
| PROTOCOL104 | 预期指纹与已注册方案不一致 |
| PROTOCOL105 | 分析方案与已注册方案不一致 |


Error details do not echo record IDs, stored contents or underlying exceptions.
Use opaque IDs; serialized bindings still contain the supplied ID and fingerprint.
The registered sensitivity is joined into the task's input floor, propagated to
downstream tasks and included in bound-task fingerprints. Binding changes therefore
invalidate downstream tasks through existing amendment analysis. The program
fingerprint still describes submitted declarations, not mutable database state.

<!-- zh -->
错误详情不回显记录 ID、已存内容或底层异常。请使用不透明 ID；序列化后的绑定仍然包含所提供的 ID 与指纹。已注册的敏感度被并入任务的输入下限，传播到下游任务，并纳入被绑定任务的指纹。因此绑定变更会通过既有的修正分析使下游任务失效。程序指纹描述的仍然是所提交的声明，而不是可变的数据库状态。


Pass `scientific_ledger=ledger` also to `ScientificPlanner` and
`assess_amendment` when using bound programs. The planner resolves records on each
planning call under that call's envelope and current ledger policy. Contracts
without a binding preserve their previous wire format and hashes.

<!-- zh -->
在使用被绑定程序时，请把 `scientific_ledger=ledger` 同时传给 `ScientificPlanner` 与 `assess_amendment`。规划器在每次规划调用时，按该次调用的信封与当前账本策略解析记录。没有绑定的契约保持其先前的线上格式与哈希。


This is strict equality checking, not automatic amendment approval: a changed
analysis fails until the declared protocol and binding are intentionally updated
to an appropriate registered record. Existing observation/deviation recording
remains separate. StatisticalDesign fields outside Protocol (such as partitions
and multiplicity plans) are not registered by this binding, although they remain
part of the workflow fingerprint and statistical checks.

<!-- zh -->
这是严格的相等性检查，不是自动的修正批准：一项更改过的分析会一直失败，直到所声明的方案与绑定被有意更新为一条恰当的已注册记录。既有的观察/偏离记录仍然分开。Protocol 之外的 StatisticalDesign 字段（例如划分与多重性计划）不由此绑定注册，尽管它们仍是工作流指纹与统计检查的一部分。


Limits: registration timing is not proven, hashes are not signatures, execution
is not observed for adherence, and compilation is not an atomic transaction with
subsequent execution. Recompile before execution; normal runtime policy, evidence
and release gates remain required. A binding is not proof of scientific validity.

Run `python -m pytest -q tests/test_protocol_binding.py` for synthetic tests.

<!-- zh -->
限制：注册的时序不被证明，哈希不是签名，执行是否遵守方案不被观测，编译也不是与后续执行同一个原子事务。执行之前请重新编译；常规的运行时策略、证据门与发布门仍然必需。绑定不是科学有效性的证明。

运行 `python -m pytest -q tests/test_protocol_binding.py` 执行合成测试。

