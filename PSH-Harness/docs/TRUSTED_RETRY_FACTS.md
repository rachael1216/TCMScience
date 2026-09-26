# Trusted tool retry facts · 可信工具重试事实

This increment addresses the non-idempotent retry counterexample in the supplied
architecture review. It is not a claim that every scientific trust boundary has
been completed. Review measurements and broader superiority claims have not been
adopted as independently verified project results.

<!-- zh -->
本增量针对所提供架构评审中的非幂等重试反例。它并不是在声称每一条科学信任边界都已完成。评审中的测量结果与更宽泛的优越性主张，并未被采纳为经独立验证的项目成果。


## Enforced boundary · 被强制的边界

`TaskContract.side_effect` is a requested contract, not authority to override a
trusted component manifest. For a tool, ScientificCompiler now requires registry
`manifest.idempotent is True` for either:

<!-- zh -->
`TaskContract.side_effect` 是一份请求式的契约，不是覆盖可信组件清单的权限。对于一个工具，ScientificCompiler 现在要求注册表 `manifest.idempotent is True`，适用于以下任一情形：


- a `PURE` or `IDEMPOTENT` declaration (`EFFECT106` on mismatch/missing fact);
- more than one automatic attempt (`RETRY102` on mismatch/missing fact), even if
  the proposal says `AT_LEAST_ONCE`.

<!-- zh -->
- 声明的类别是 `PURE` 或 `IDEMPOTENT`（不匹配/缺失该事实时为 `EFFECT106`）；
- 自动尝试次数多于一次（不匹配/缺失该事实时为 `RETRY102`），即使提案写着 `AT_LEAST_ONCE`。


Existing contract-level retry restrictions still apply. A nonrepeatable contract
cannot acquire retries just because the manifest permits them. Ordinary one-shot,
nonrepeatable tasks remain supported. Tool task fingerprints include the resolved
trusted idempotency bit so amendment impact analysis notices a fact change.

Idempotency is necessary here, **not proof of purity**: it does not certify absence
of external effects, scientific correctness or deterministic results. The current
manifest has no complete purity attestation. Component registration remains a
trusted host operation; a lying trusted manifest is outside this check's guarantee.

<!-- zh -->
契约层面的既有重试限制仍然适用。一份不可重复的契约不能仅因为清单允许就获得重试。普通的一次性、不可重复任务仍然受支持。工具任务指纹包含解析后的可信幂等位，因此修正影响分析会注意到事实变化。

在这里，幂等性是必要条件，**而不是纯度的证明**：它不保证没有外部效果、不保证科学正确性，也不保证结果确定性。当前清单没有完整的纯度见证。组件注册仍然是可信的主机操作；一份撒谎的可信清单不在本检查的保证范围之内。


## Runtime defense without a durable ledger · 没有持久账本时的运行时防御

AgentLoopController independently reads the actual component's manifest before
dispatch. A tool exception can schedule an automatic retry only if that fact was
exactly `True`, not merely truthy. Missing/invalid idempotency facts are conservative.
Model retry behavior is unchanged. With an OperationLedger, uncertain effects
continue to be recorded as unknown and investigated through that ledger.

<!-- zh -->
AgentLoopController 在分发之前独立读取实际组件的清单。一次工具异常只有在那个事实精确为 `True`（而不只是真值）时，才能安排一次自动重试。缺失/无效的幂等事实按保守处理。模型重试行为不变。有了 OperationLedger，不确定的效果继续被记录为未知，并通过该账本调查。


A locked controller-local claim set additionally refuses reuse of the same
nonrepeatable operation key during replans or another run call on that controller.
Upgrading the manifest afterward does not erase an earlier uncertain claim. A
claim is retained conservatively even if a later gate refuses the call before any
effect: safety takes precedence over guessing whether retry would be harmless.

<!-- zh -->
一个加锁的、控制器本地的认领集合（claim set）还会拒绝在同一次重规划或该控制器上的另一次 run 调用中复用同一个不可重复操作键。事后升级清单不会抹去先前的存疑认领。即使后续某个门在任何效果发生之前就拒绝了该调用，认领仍被保守保留：安全优先于去猜重试是否有害。


The local claim set is **not durable**. A newly constructed controller or process
cannot recover it. Durable operation history still requires OperationLedger; this
patch does not make that ledger mandatory, add external-effect transactions, or
provide exactly-once execution. New task/run IDs or new dynamic visit namespaces
are different operations, not retries protected by the same key. Dynamic cycle
admission now applies additional default-deny checks documented in
`DYNAMIC_WORKFLOW.md`; an approval exception for unsafe repetition remains absent.

<!-- zh -->
本地认领集合**不是持久的**。新构造的控制器或新进程无法恢复它。持久化的操作历史仍然需要 OperationLedger；本补丁没有让该账本成为强制，没有加入外部效果事务，也没有提供恰好一次执行。新的任务/运行 ID 或新的动态访问命名空间属于不同的操作，而不是由同一把键保护的重试。动态循环准入现在还施加额外的默认拒绝检查，记录在 `DYNAMIC_WORKFLOW.md` 中；对不安全重复的审批例外仍然不存在。


## Remaining review work · 评审中剩余的工作

Still separate: mandatory scientific task roles and analysis modes; trusted
evidence resolution; durable compiled scientific contracts; the dynamic public
release-service sandbox hardening; approved nonrepeatable repetition; durable execution identity and safe
resume. No version bump, old-archive deletion or README marketing changes are
included in this safety patch.

<!-- zh -->
仍待单独处理：强制的科学任务角色与分析模式；可信的证据解析；持久化的已编译科学契约；动态公共服务发布沙箱的加固；被批准的非重复性重复执行；持久的执行身份与安全恢复。本安全补丁不包含版本号提升、旧归档删除或 README 营销性修改。


## Tests · 测试

`tests/test_trusted_retry_facts.py` covers the contradictory PURE/IDEMPOTENT
proposal, AT_LEAST_ONCE with a non-idempotent tool, missing registry facts,
compiler/planner/dynamic preflight, plain-loop runtime protection with and without
an operation ledger, continued safe retries, controller-local claims and trusted
fact fingerprint changes. Tools are synthetic; no external side effects are sent.

<!-- zh -->
`tests/test_trusted_retry_facts.py` 覆盖互相矛盾的 PURE/IDEMPOTENT 提案、带非幂等工具的 AT_LEAST_ONCE、缺失的注册表事实、编译器/规划器/动态预检、带与不带操作账本的普通循环运行时保护、持续安全的重试、控制器本地认领，以及可信事实指纹变化。工具是合成的；不发送任何外部副作用。

