# v0.5.1 — gate composition closure · 门组合闭合

This release answers a second external review of v0.5. That review accepted the v0.5
premise (controls exist and the executing path now uses them) and attacked the next layer:
**what happens where two controls meet.** Every finding it made is real, every one is
reproduced below, and every one is now closed with a test that fails on v0.5.

<!-- zh -->
本次发布回应的是针对 v0.5 的第二轮外部评审。该评审接受了 v0.5 的前提（控制机制存在，且执行路径现在确实使用它们），转而攻击下一层：**两个控制机制相遇之处会发生什么。** 它提出的每一条发现都成立，每一条都在下文复现，并且每一条现在都已由一条在 v0.5 上失败的测试闭合。


The findings share a shape, and it is worth naming because it is not the v0.5 shape:

<!-- zh -->
这些发现共享同一种形态，而它值得被命名，因为它并不是 v0.5 的形态：


> v0.5: *the mechanism exists and the main path does not call it.*
>
> v0.5.1: **the mechanism exists, is called — and a second, shorter copy of it was
> hand-written somewhere else.**

<!-- zh -->
> v0.5：*机制存在，而主路径不调用它。*
>
> v0.5.1：**机制存在、也被调用了 —— 但它的第二份、更短的副本，在别处被手工写了出来。**


`AuthorityLattice` defines authority containment over fourteen dimensions, and
`DelegationGateway` re-implemented four of them. `PolicySnapshot.envelope()` enforces a
ceiling, and `Runner.run()` minted envelopes from a policy nobody compared to the kernel's.
`labels.walk_values` treats a truncated walk as a reason to escalate, and the two egress
walkers treated it as a reason to pass. In each case the *unified* abstraction was correct
and the bypass was the duplicate beside it.

<!-- zh -->
`AuthorityLattice` 在十四个维度上定义权限包含关系（authority containment），而 `DelegationGateway` 重新实现了其中四个维度。`PolicySnapshot.envelope()` 强制施加一个策略上限，而 `Runner.run()` 却用一份从未被拿去与内核策略比对的策略铸造信封。`labels.walk_values` 把被截断的遍历当作升级的理由，而两个出网遍历器却把它当作放行的理由。在每一处，*统一*的抽象都是正确的，而绕过它的恰恰是它旁边那份重复实现。


So the fixes are mostly deletions. `DelegationGateway` lost its comparisons and calls
`AuthorityLattice.violations`. `PolicySnapshot.with_()` lost its three `if`s and calls a new
`PolicyLattice`. The rule is the one the authority module already stated:

<!-- zh -->
所以这些修复大多是在做删除。`DelegationGateway` 丢掉了它的那些比较，改为调用 `AuthorityLattice.violations`。`PolicySnapshot.with_()` 丢掉了它的三个 `if`，改为调用一个新的 `PolicyLattice`。这里遵循的规则，正是权限模块早已写下过的那一条：


> a check repeated at each call site will drift; a check called from each call site cannot.

<!-- zh -->
> 在每一处调用点重复书写的检查会漂移；从每一处调用点共同调用的检查不会。


## P0 · P0（最高优先级缺陷）

### 1. `Runner.run(policy=…)` bypassed the kernel's policy ceiling · 1. `Runner.run(policy=…)` 绕过了内核的策略上限

`src/psh/runtime/runner.py`

<!-- zh -->
实现位置：`src/psh/runtime/runner.py`


**Reproduction.** A kernel built with a local-only, `SUGGEST`, `R1` policy. A caller passes
a broader `PolicySnapshot` to `Runner`:

<!-- zh -->
**复现。** 一个按仅本地、`SUGGEST`、`R1` 策略构建的内核。调用方向 `Runner` 传入一份更宽泛的 `PolicySnapshot`：


```
kernel allows PUBLIC_REMOTE: False
selected run policy:         caller-broad
remote invokes:              1
refused_at:                  (none)
released:                    hello world
```


The kernel forbade public providers; the run reached one and released the result. `policy =
policy or self.policy` accepted whatever was passed and `policy.envelope(...)` minted from
it, so the envelope every gateway checked had been produced by a policy the kernel never
agreed to. A gateway checks the envelope it is given, and this envelope was honest about an
authority nobody had granted.

<!-- zh -->
内核禁止使用公共提供方；这次运行却访问到了一个，并放行了结果。`policy =
policy or self.policy` 接受了传进来的任何东西，而 `policy.envelope(...)` 又据此铸造信封，于是每一个网关所检查的那个信封，都是由一份内核从未同意过的策略产生的。网关只检查交给它的那个信封，而这份信封对于一份没有任何人授予过的权限，是如实描述的。


The same call produced a **split brain**, which is the more interesting half:

<!-- zh -->
同一次调用还产生了**分裂脑（split brain）**，而这是更有意思的那一半：


| consulted by | policy actually used |
| --- | --- |
| the run envelope | `Runner.run(policy=…)` |
| `OutputGate.require_support` | `kernel.policy`, at construction |
| `broker.require_isolation` | `kernel.policy`, at construction |
| `PersistenceGateway.max_label` | `kernel.policy`, at construction |

<!-- zh -->
| 由谁查阅 | 实际使用的策略 |
| --- | --- |
| the run envelope | `Runner.run(policy=…)` |
| `OutputGate.require_support` | `kernel.policy`，在构造时 |
| `broker.require_isolation` | `kernel.policy`，在构造时 |
| `PersistenceGateway.max_label` | `kernel.policy`，在构造时 |


So `result.policy_snapshot` named a policy that had governed one of the four.

<!-- zh -->
于是 `result.policy_snapshot` 所指向的那份策略，只治理了这四者当中的一个。


**Fix.** Two parts, because either alone leaves half the defect.

<!-- zh -->
**修复。** 分两部分，因为任何单独一部分都会让缺陷留下一半。


*Direction.* `Runner._effective_policy` refuses a per-run policy that is not contained by
`kernel.policy`, naming every dimension it exceeds, and `Runner(clamp_policy=True)` opts
into the meet instead. Both the constructor and `run()` go through it.

<!-- zh -->
*方向。* `Runner._effective_policy` 会拒绝任何不被 `kernel.policy` 包含的单次运行策略，并逐一列出它超出的每一个维度；而 `Runner(clamp_policy=True)` 则选择改为取两者的下确界（meet）。构造函数与 `run()` 都经过这条路径。


*Enforcement.* Containment makes the run's policy narrower — and narrower has to be
**enforced**, not merely recorded, or "declared policy is effective policy" is false again
in the safe direction. `require_isolated_tools` now travels on `RunEnvelope`, so the broker
reads it from the run rather than from its own construction argument, and
`AuthorityLattice` covers it so a delegate cannot drop it. `OutputGate.check` and
`PersistenceGateway.commit_node` take per-call ceilings that can only tighten (`or` and
`min`, never assignment).

<!-- zh -->
*强制。* 包含关系让这次运行的策略变得更窄 —— 而更窄必须被**强制执行**，不能只是被记录下来，否则"声明的策略就是生效的策略"这句话又会在安全方向上变成假的。`require_isolated_tools` 现在随 `RunEnvelope` 一起传递，因此 broker 是从这次运行上读取它，而不是从自己构造时的参数上读取；`AuthorityLattice` 也覆盖了它，所以被委托方无法把它丢掉。`OutputGate.check` 与 `PersistenceGateway.commit_node` 接受逐次调用的上限，而这些上限只能收紧（用 `or` 和 `min`，绝不使用赋值）。


### 2. Multi-destination components were gated on `destinations[0]` · 2. 多目的地组件只按 `destinations[0]` 过关

`src/psh/kernel/egress.py`

<!-- zh -->
实现位置：`src/psh/kernel/egress.py`


**Reproduction.** A component declaring `destinations=(LOCAL_COMPUTE, PUBLIC_REMOTE)`,
handed a PHI payload:

<!-- zh -->
**复现。** 一个声明了 `destinations=(LOCAL_COMPUTE, PUBLIC_REMOTE)` 的组件，被传入一份 PHI 载荷：


```
PHI permits PUBLIC_REMOTE?   False
ToolGateway allowed:         True
checked destination:         LOCAL_COMPUTE
```


The dangerous part is not the single wrong answer, it is that **the two halves of the
kernel read the same tuple differently**:

<!-- zh -->
危险之处不在于那一个错误答案，而在于**内核的两半对同一个元组读出了不同的结果**：


```
ToolGateway          -> destinations[0] -> LOCAL_COMPUTE -> PHI passes
IsolatedExecutor     -> any(d in remote) -> PUBLIC_REMOTE -> network opened
```


Either reading is defensible. Holding both at once means the gate permits PHI on the
strength of a destination the executor has already decided is not the only one.

<!-- zh -->
两种读法各自都说得通。同时持有两者，就意味着网关之所以放行 PHI，所依据的是一个执行器早已判定为并非唯一的目的地。


**Fix.** Every declared destination is checked; the decision names the **most exposed** one
rather than the first. Exposure order is stated explicitly (`_EXPOSURE_ORDER`) because the
enum's integer order is a declaration order — `PERSISTENT` is 5 and `PUBLIC_REMOTE` is 3,
and writing to the user's own disk is not more exposing than a public provider.
`manifest_destinations()` is the single reading of the tuple, used by the gate, the approval
request and the audit record, so they cannot diverge again. Approval targets list all
destinations: an approval keyed on `LOCAL_COMPUTE` for a component that also reaches
`PUBLIC_REMOTE` describes a different action from the one being approved.

<!-- zh -->
**修复。** 每一个被声明的目的地都会被检查；判定所指向的是**最暴露的那个**目的地，而不是第一个。暴露顺序被显式写出（`_EXPOSURE_ORDER`），因为枚举的整数顺序只是一种声明顺序 —— `PERSISTENT` 是 5，`PUBLIC_REMOTE` 是 3，而写入用户自己的磁盘并不比访问一个公共提供方更暴露。`manifest_destinations()` 是对该元组的唯一读法，网关、审批请求与审计记录都用它，因此它们不会再分叉。审批目标会列出全部目的地：一份以 `LOCAL_COMPUTE` 为键的审批，若其组件同时也会访问 `PUBLIC_REMOTE`，那它描述的就已经不是正在被审批的那个动作了。


## P1 · P1（高优先级缺陷）

### 3. `DelegationGateway` re-implemented the authority lattice, badly · 3. `DelegationGateway` 把权限格重新实现了一遍，而且实现得很糟

`src/psh/kernel/egress.py`

<!-- zh -->
实现位置：`src/psh/kernel/egress.py`


**Reproduction.** Parent `R1`/`SUGGEST`/`capabilities=["safe"]`/`denied=["blocked"]`/
`usd_hard=1`/`seconds_hard=10`/1 model call. Child identical except `R4_KERNEL`, `ACT`,
unrestricted capabilities, no denials, `usd_hard=999`, `seconds_hard=999`, 999 calls:

<!-- zh -->
**复现。** 父级为 `R1`/`SUGGEST`/`capabilities=["safe"]`/`denied=["blocked"]`/`usd_hard=1`/`seconds_hard=10`/1 次模型调用。子级与之一致，只是改为 `R4_KERNEL`、`ACT`、能力不受限制、没有拒绝项、`usd_hard=999`、`seconds_hard=999`、999 次调用：


```
DelegationGateway allowed = True
AuthorityLattice.violations = [capabilities, denied_capabilities, risk, autonomy,
                               budget.usd_hard, budget.seconds_hard,
                               budget.max_model_calls, budget.max_tool_calls,
                               budget.max_delegations]
```


**Fix.** The hand-written comparisons are gone. The gateway calls
`AuthorityLattice.violations(child, parent)` and refuses with the list. What remains is the
one check that is *not* an authority question — whether the projection's label fits the
child's ceiling, which is about data rather than about the relationship between two
envelopes.

<!-- zh -->
**修复。** 手写的比较已经删除。网关改为调用 `AuthorityLattice.violations(child, parent)`，并带着这份列表拒绝。保留下来的只有那一条*不属于*权限问题的检查 —— 即投影的标签是否落在子级的上限之内，这关乎数据，而不关乎两个信封之间的关系。


**Why the property test missed it.** `test_delegation_contract_cannot_exceed_its_parent_run`
built a child asking for the maximum on every dimension, including `tokens_hard = 10**9`,
while the parent strategy generated `tokens_hard <= 100_000`. Every example was therefore
refused by the first budget comparison and no other dimension was ever the *reason* for a
verdict. A conjunctive property on a lattice cannot distinguish "all dimensions enforced"
from "one dimension enforced": whichever fails earliest masks the rest.

<!-- zh -->
**属性测试为什么漏掉了它。** `test_delegation_contract_cannot_exceed_its_parent_run` 构造的子级在每一个维度上都要求最大值，包括 `tokens_hard = 10**9`，而父级策略生成的是 `tokens_hard <= 100_000`。因此每一个样例都被第一个预算比较拒绝，其他任何维度都从未成为判定的*理由*。在权限格上的一条合取式属性，无法区分"所有维度都被强制执行"与"只有一个维度被强制执行"：最早失败的那一个会把其余的全都遮住。


The property is now **one dimension at a time** — take a child identical to its parent,
widen exactly one field, assert the refusal names that field. And because the new test can
itself go vacuous, `test_every_lattice_dimension_is_actually_exercised` asserts that each
dimension produced at least one non-filtered example. That test caught a real hole on its
first run: `envelopes()` defaulted `require_isolated_tools` to `False`, so that dimension
had exactly zero coverage.

<!-- zh -->
现在的属性是**一次一个维度** —— 取一个与父级完全相同的子级，只放宽其中一个字段，断言拒绝信息点名了该字段。又因为新测试本身也可能变得空泛/无法证伪（vacuous），`test_every_lattice_dimension_is_actually_exercised` 会断言每个维度都至少产生了一个未被过滤掉的样例。该测试在第一次运行时就抓到了一个真实的漏洞：`envelopes()` 把 `require_isolated_tools` 默认为 `False`，导致那个维度的覆盖率恰好为零。


### 4. `PolicySnapshot.with_()` widened on nine of twelve dimensions · 4. `PolicySnapshot.with_()` 在十二个维度中的九个上放宽了策略

`src/psh/policy.py`

<!-- zh -->
实现位置：`src/psh/policy.py`


The docstring said "Return a narrowed copy. Widening is refused." The body compared
`max_data_label`, `allowed_destinations` and `risk_ceiling`. Accepted: `SUGGEST -> ACT`,
`require_isolated_tools True -> False`, `require_citation True -> False`, `tokens_hard
10 -> 999`, `usd_hard 1 -> 999`, `approval_required_at R1 -> R4`, `declassify_floor
SENSITIVE -> PUBLIC`, a widened `declassifiers` list, and an extended or removed deadline.

<!-- zh -->
文档字符串写的是 "Return a narrowed copy. Widening is refused."（返回一份收窄后的副本。放宽会被拒绝。）而函数体只比较了 `max_data_label`、`allowed_destinations` 和 `risk_ceiling` 三项。以下改动都被接受了：`SUGGEST -> ACT`、`require_isolated_tools True -> False`、`require_citation True -> False`、`tokens_hard 10 -> 999`、`usd_hard 1 -> 999`、`approval_required_at R1 -> R4`、`declassify_floor SENSITIVE -> PUBLIC`、被放宽的 `declassifiers` 列表，以及被延长或被删除的截止时间。


**Fix.** A new `PolicyLattice`, the policy-level counterpart of `AuthorityLattice`, with
every dimension in one table and the direction stated per group:

<!-- zh -->
**修复。** 新增一个 `PolicyLattice`，它是 `AuthorityLattice` 在策略层面的对应物，把所有维度收进同一张表，并按组写明方向：


* *permissions* narrow by getting smaller — data ceiling, destinations, risk, budget,
  deadline, declassifiers;
* *requirements* narrow by being turned **on** — a child may set `require_citation` and
  never clear it;
* *thresholds* narrow toward "more is checked" — `approval_required_at` lower,
  `declassify_floor` higher.

<!-- zh -->
* *许可类（permissions）* 通过变小而收窄 —— 数据上限、目的地、风险、预算、截止时间、解密器（declassifiers）；
* *要求类（requirements）* 通过被**打开**而收窄 —— 子级可以设置 `require_citation`，但绝不能清除它；
* *阈值类（thresholds）* 朝着"检查得更多"的方向收窄 —— `approval_required_at` 更低，`declassify_floor` 更高。


`with_()` is now one line. `Runner` uses the same lattice for the P0-1 containment check, so
"narrower policy" means the same thing in both places.

<!-- zh -->
`with_()` 现在只有一行。`Runner` 在 P0-1 的包含性检查中使用同一个权限格，因此"更窄的策略"在两处的含义完全相同。


### 5. `manifest.id` traversed out of the sandbox · 5. `manifest.id` 穿越出了沙箱

`src/psh/contracts.py`, `src/psh/kernel/isolation.py`

<!-- zh -->
实现位置：`src/psh/contracts.py`、`src/psh/kernel/isolation.py`


`workdir = root / run_id / manifest.id`, with `id` validated only for being non-empty:

<!-- zh -->
`workdir = root / run_id / manifest.id`，而 `id` 只被校验了"非空"这一件事：


```
id = "../../escaped"
workdir resolves to  /tmp/.../escaped
sandbox root         /tmp/.../sandbox
inside root:         False
```


An absolute id was worse: `Path("/a/b") / "/tmp/x"` is `/tmp/x` — `pathlib` treats an
absolute right-hand side as a replacement, not a suffix — so the root was discarded
entirely.

<!-- zh -->
绝对路径形式的 id 更糟：`Path("/a/b") / "/tmp/x"` 的结果是 `/tmp/x` —— `pathlib` 会把绝对路径的右侧当作替换，而不是后缀 —— 于是根目录被整个丢弃了。


**Fix.** Two layers, because either alone has a gap. `ComponentManifest` constrains the id
to one safe path component (`[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`, and not `.` or `..`), which
stops the traversal where the manifest is built. `IsolatedExecutor._workdir_for` resolves
the assembled path and requires it to land inside the resolved root, which also covers a
symlinked sandbox directory and a `run_id` from a future caller that does not go through
the manifest.

<!-- zh -->
**修复。** 两层，因为任何单独一层都有缺口。`ComponentManifest` 把 id 约束为单个安全的路径组成部分（`[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`，且不得为 `.` 或 `..`），从而在清单被构建之处就阻断穿越。`IsolatedExecutor._workdir_for` 则解析拼装后的路径，并要求它落在已解析的根目录之内，这同时也覆盖了被软链接的沙箱目录，以及来自未来某个不经过清单的调用方的 `run_id`。


### 6. A timeout killed the process, not the process group · 6. 超时杀死的是进程，而不是进程组

`src/psh/kernel/isolation.py`

<!-- zh -->
实现位置：`src/psh/kernel/isolation.py`


`subprocess.run(..., timeout=…, start_new_session=True)` created a process group and never
signalled it. Reproduction — a tool that spawns a worker and sleeps, with a 0.5 s timeout:

<!-- zh -->
`subprocess.run(..., timeout=…, start_new_session=True)` 创建了一个进程组（process group），却从未向它发过信号。复现 —— 一个会派生出 worker 然后休眠的工具，超时设为 0.5 s：


```
runner timed_out = True
grandchild alive  = True
1.8s later: grandchild wrote its file = True
```


`"exceeded its timeout and was killed"` was true only of the process the runner could name.

<!-- zh -->
`"exceeded its timeout and was killed"`（"已超时并被杀死"）这句话，只对 runner 能叫得出名字的那个进程成立。


**Fix.** `Popen` + `communicate(timeout=…)`, and on expiry `os.killpg(os.getpgid(pid),
SIGKILL)` — the group is signalled, not the leader, so every descendant that has not left
the group dies. Output is drained after the kill, because the pipes may be held open by
exactly the grandchildren the kill is for. Windows has no process group to signal; the
fallback is `Popen.kill()` and the gap is stated rather than pretended away.

<!-- zh -->
**修复。** 改用 `Popen` + `communicate(timeout=…)`，并在超时时执行 `os.killpg(os.getpgid(pid), SIGKILL)` —— 收到信号的是整个进程组，而不是组长进程，因此每一个尚未脱离该组的后代进程都会死亡。杀死之后还要把输出排空（drain），因为那些管道可能正被这次杀死所针对的孙进程持有而保持打开。Windows 没有可发信号的进程组；回退方案是 `Popen.kill()`，而这个缺口是被如实说明的，不是被假装不存在的。


### 7. Deep payloads were fail-open · 7. 深层载荷曾失败即放行（fail open）

`src/psh/kernel/egress.py`

<!-- zh -->
实现位置：`src/psh/kernel/egress.py`


```python
if _depth > 8:
    return []          # -> "no paths found" -> nothing to object to -> allowed
```


Nine levels of nesting around `{"target": "/definitely/outside"}` and the filesystem
boundary disappeared. `_flatten_text` had the same shape at depth 6, so a denied command
shape could be hidden the same way.

<!-- zh -->
在 `{"target": "/definitely/outside"}` 外面套上九层嵌套，文件系统边界就消失了。`_flatten_text` 在深度 6 处有同样的形态，因此一个本应被拒绝的命令形态可以用同样的方式藏起来。


**Fix.** Both walkers return a `_Scan` carrying `complete`, and the gate refuses a payload
it could not fully inspect. This matches what `psh.labels.walk_values` already did —
yield `_TRUNCATED`, and let `deep_label_of` escalate rather than assume the remainder was
clean. The caps are raised (depth 16, 20 000 nodes) precisely *because* the consequence is
now refusal: refusing an ordinary deeply structured payload would be its own defect.

<!-- zh -->
**修复。** 两个遍历器都返回一个携带 `complete` 的 `_Scan`，而网关会拒绝任何它无法完整检视的载荷。这与 `psh.labels.walk_values` 早已采取的做法一致 —— 产出 `_TRUNCATED`，并让 `deep_label_of` 升级处理，而不是假定剩下的部分是干净的。上限被提高（深度 16、20 000 个节点），恰恰是*因为*后果现在是拒绝：拒绝一份普通的、结构很深的载荷，本身就会是另一个缺陷。


## P2 · P2（较低优先级缺陷）

### 8. `requires_network` validation could never fire · 8. `requires_network` 的校验永远不会触发

```python
if self.requires_network and Destination.LOCAL_COMPUTE == self.destinations == ():
```


A chained comparison requiring an enum member to equal a tuple. `ComponentManifest(
requires_network=True, destinations=())` was accepted, and the gateway then defaulted it to
`LOCAL_COMPUTE` — so the audit record said "local" about a component that had declared it
needs the network. Now: a network-requiring component must name `PUBLIC_REMOTE` or
`TRUSTED_REMOTE`.

<!-- zh -->
一段链式比较，却要求一个枚举成员等于一个元组。`ComponentManifest(requires_network=True, destinations=())` 被接受了，随后网关又把它默认为 `LOCAL_COMPUTE` —— 于是审计记录对一个已经声明自己需要网络的组件写的是"本地"。现在：需要网络的组件必须点名 `PUBLIC_REMOTE` 或 `TRUSTED_REMOTE`。


### 9. `StuckLoop` refused ordinary repeated requests · 9. `StuckLoop` 拒绝了普通的重复请求

`signature = f"{envelope.task_id}:{hash(request) & 0xffffffff}"`, and `task_id` was `""` at
preflight — the envelope is minted at stage 2 and the task is created at stage 3. One bucket
counted every run a `Runner` had ever made, so three identical top-level requests tripped the
detector at the default threshold of 3. The dict was never cleared, and `hash()` of a `str`
is salted per process so signatures were not stable across runs.

<!-- zh -->
`signature = f"{envelope.task_id}:{hash(request) & 0xffffffff}"`，而 `task_id` 在预检（preflight）时是 `""` —— 信封在第 2 阶段铸造，任务却在第 3 阶段才创建。于是一个桶把所有曾经由某个 `Runner` 发起过的运行都算了进去，因此三次相同的顶层请求就在默认阈值 3 上触发了检测器。那张字典从未被清空，而且 `str` 的 `hash()` 是按进程加盐的，所以签名在不同运行之间并不稳定。


**Fix.** The envelope is rebound to the real task id after `bind`. A loop is a repeat within
one scope, so the scope is explicit: the caller's `loop_scope`, else the parent run for a
delegated run, else this run's task — which is unique per top-level `run()` and therefore
cannot collide. The digest is SHA-256, and the table is bounded.

<!-- zh -->
**修复。** 信封在 `bind` 之后被重新绑定到真实的任务 id 上。循环是同一个作用域内的重复，因此作用域被显式化：先用调用方的 `loop_scope`，若没有则对一次委托运行使用其父运行，若还没有则用本次运行的任务 —— 后者对每个顶层 `run()` 都是唯一的，因此不可能发生碰撞。摘要改用 SHA-256，并且表是有界的。


### 10. `min_autonomy` was declared and never read · 10. `min_autonomy` 被声明了，却从未被读取

`compatible_with()` never consulted it, so a component declaring `min_autonomy=ACT` was
reported compatible with an `OBSERVE` run. It is enforced now — and the default changed from
`ACT` to `OBSERVE`, because `ACT` is the *strongest* requirement and defaulting to it would
have made every manifest that never considered the field demand full autonomy. That default
is most of why the field could not be turned on.

<!-- zh -->
`compatible_with()` 从未查阅它，于是一个声明了 `min_autonomy=ACT` 的组件，被报告为与一次 `OBSERVE` 运行兼容。现在它被强制执行了 —— 同时默认值从 `ACT` 改为 `OBSERVE`，因为 `ACT` 是*最强*的要求，若以它为默认，就会让每一份从未考虑过该字段的清单都去要求完全自主。这个默认值正是该字段一直无法开启的主要原因。


### Also · 其他

* `IsolatedRunner.run` called `bool(list(allowed_hosts))` after already draining
  `allowed_hosts` into `hosts`. A list survived it; any generator would have told the
  sandbox the run wants no network while the proxy was configured to allow some.
* An isolated component whose stdout did not parse as JSON was wrapped as
  `{"stdout": …}`. The protocol says "writes one JSON value on stdout"; a contract that is
  silently optional is not a contract. It raises `ContractViolation` now.
* The stale `psh_pkg (2).tar.gz` (v0.4.0, carrying 187 AppleDouble `._*` files and
  `.hypothesis` caches) is removed from the tree.

<!-- zh -->
* `IsolatedRunner.run` 在已经把 `allowed_hosts` 排空进 `hosts` 之后，才去调用 `bool(list(allowed_hosts))`。列表还能挺过去；但换成任何生成器，都会告诉沙箱"这次运行不需要网络"，而代理却被配置为允许一部分网络。
* 一个 stdout 无法解析为 JSON 的隔离组件，会被包装成 `{"stdout": …}`。协议写的是"在 stdout 上写出一个 JSON 值"；一份可以被静默忽略的契约根本不是契约。现在它会抛出 `ContractViolation`。
* 陈旧的 `psh_pkg (2).tar.gz`（v0.4.0，携带 187 个 AppleDouble `._*` 文件与 `.hypothesis` 缓存）已从代码树中移除。


## Tests · 测试

```
230 -> 345 passing (296 after the defect fixes, before the loop and composition suites)
```


* `tests/test_review_v5_1.py` — 66 tests, one per reproduction above. **55 of 62 fail on
  v0.5** when the file is dropped into an unmodified tree; the 7 that pass there are the
  deliberate controls (ordinary narrowing still works, a properly narrowed child is still
  allowed, the non-timeout execution paths still work).
* `tests/test_authority_property.py` — the delegation property rewritten one dimension at a
  time, plus the anti-vacuity test that checks the properties are reaching their
  assertions.

<!-- zh -->
* `tests/test_review_v5_1.py` —— 66 条测试，上文每一条复现对应一条。当该文件被放进一棵未修改的代码树时，**62 条中有 55 条在 v0.5 上失败**；在那里通过的 7 条是有意设置的对照项（普通的收窄仍然有效，一个被正确收窄的子级仍然被允许，非超时的执行路径仍然工作）。
* `tests/test_authority_property.py` —— 把委托属性改写为一次一个维度，外加那条反空泛（anti-vacuity）测试，用来检查这些属性确实走到了它们的断言。


## Still open, stated plainly · 仍然敞开的问题，如实陈述

* The planner is still a placeholder and `validate_plan` still has no typed plan to
  validate. This release is enforcement, not runtime; see `docs/ROADMAP_AGENT_RUNTIME.md`.
* `backend="python"` components still run in the kernel process. `require_isolated_tools`
  is how a policy refuses them, and it now reaches the broker from the run as well as from
  the kernel — but the honest boundary is unchanged.
* Only `NoSandbox` ships. The egress proxy governs clients that honour proxy variables; a
  raw socket bypasses it. The process-group kill is a containment improvement, not a
  sandbox.
* Claim support is still lexical, and the audit chain is still tamper-evident rather than
  tamper-proof.
* The runtime built on this release was itself reviewed afterwards; two findings survived
  and are closed in `docs/RUNTIME_SECURITY_REVIEW.md`.

<!-- zh -->
* 规划器仍然是占位实现，`validate_plan` 仍然没有一份带类型的计划可供校验。本次发布做的是强制执行，不是运行时；参见 `docs/ROADMAP_AGENT_RUNTIME.md`。
* `backend="python"` 组件仍然在内核进程内运行（in-process）。策略通过 `require_isolated_tools` 来拒绝它们，而它现在既从内核、也从这次运行抵达 broker —— 但那条诚实的边界并没有改变。
* 只有 `NoSandbox` 随附发布。出网代理（egress proxy）管辖的是那些遵守代理变量的客户端；一个裸 socket 就能绕过它。按进程组杀死是一项围堵上的改进，而不是沙箱。
* 主张支撑（claim support）仍然是词法层面的，审计链也仍然是防篡改可发现（tamper-evident）而非防篡改（tamper-proof）。
* 建立在本版本之上的运行时随后自身也接受了评审；有两条发现存活下来，已在 `docs/RUNTIME_SECURITY_REVIEW.md` 中闭合。
