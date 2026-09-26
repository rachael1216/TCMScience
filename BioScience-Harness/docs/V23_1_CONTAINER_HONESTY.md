# v0.2.3.1 — container availability, measured rather than assumed · v0.2.3.1 — 容器可用性，实测而非假定

A review of BioScience-Harness reported `133 passed, 3 failed, 6 skipped` and noted that
while some failures were environmental (no Parquet engine, no container runtime), they
"also exposed a container validation ordering problem". They did. There were two defects,
and the second is the one the reviewer named.

<!-- zh -->
一份针对 BioScience-Harness 的评审报告给出 `133 passed, 3 failed, 6 skipped`，并指出其中一些失败虽然属于环境问题（没有 Parquet 引擎、没有容器运行时），但它们“还暴露了一个容器校验的编排/顺序（ordering）问题”。确实如此。共有两处缺陷，第二处正是评审者所指名的那一处。

With `pyarrow` installed the Parquet failures disappear, so the suite here reads
`135 passed, 7 skipped` — and the seventh skip is where the bug was hiding.

<!-- zh -->
装上 `pyarrow` 之后，Parquet 相关的失败就消失了，于是此处的测试套件读数为 `135 passed, 7 skipped` —— 而第七次跳过，正是缺陷藏身之处。

## 1. "Installed" was treated as "usable" · 1. 把“已安装”当成了“可用”

```python
def available(self) -> bool:
    return self.runtime_bin is not None          # shutil.which("docker")
```

`shutil.which` finds the **client binary**. It says nothing about whether a daemon is
reachable. On a machine with the docker CLI installed and no daemon running — a CI image, a
fresh workstation, the environment this was fixed in — the backend declared itself
available, `docker run` failed with *"failed to connect to the docker API"*, the return code
was non-zero, and the result was recorded as `FAILED`.

<!-- zh -->
`shutil.which` 找到的是**客户端二进制文件**。它对守护进程（daemon）是否可达一无所知。在一台装了 docker CLI 却没有守护进程运行的机器上 —— 一个 CI 镜像、一台刚装好的工作站、修复本问题时所处的那个环境 —— 容器后端（container backend）宣称自己可用，`docker run` 以 *"failed to connect to the docker API"* 失败，返回码非零，结果被记为 `FAILED`。

`FAILED` and `UNAVAILABLE` are not interchangeable in this package. `status.py` says so:

<!-- zh -->
在本软件包中，`FAILED` 与 `UNAVAILABLE` 不可互换。`status.py` 就是这么写的：

```
FAILED       # ran and errored
UNAVAILABLE  # cannot run here (missing dep/runtime/install)
```

and `ExecutionStatus.executed` is **True** for the first, **False** for the second. So the
misclassification asserted that upstream work had run when nothing ran at all — the same
shape of dishonesty the rest of this package is built to avoid.

<!-- zh -->
而 `ExecutionStatus.executed` 对前者为 **True**，对后者为 **False**。因此这一误分类断言了上游工作已经运行，而实际上什么都没运行 —— 这正是本软件包的其余部分被构建出来所要避免的同一种不诚实。

Measured, on a host with the CLI and no daemon:

<!-- zh -->
在一台装有 CLI、没有守护进程的主机上实测如下：

| | before | after |
| --- | --- | --- |
| healthy component, no daemon | `FAILED`, `executed=True` | `UNAVAILABLE`, `executed=False` |
| `EvolutionAgent.analyze_failures` | `{'FAILED': 1}` | `{'UNAVAILABLE': 1}` |

<!-- zh -->
| | 修复前 | 修复后 |
| --- | --- | --- |
| healthy component, no daemon | `FAILED`、`executed=True` | `UNAVAILABLE`、`executed=False` |
| `EvolutionAgent.analyze_failures` | `{'FAILED': 1}` | `{'UNAVAILABLE': 1}` |

The second row is the cost. `analyze_failures` feeds the self-evolution pipeline, so a
component that is entirely healthy was tallied as failing and could be proposed for a
rewrite because the *host* had no container daemon.

<!-- zh -->
代价出在第二行。`analyze_failures` 为自我演化（self-evolution）流水线提供输入，于是一个完全健康的组件被记为失败，并可能因为*主机*没有容器守护进程而被提议重写。

**Fix.** `probe_container_runtime()` measures both halves: the CLI must exist *and* the
runtime must answer (`<bin> info`). It is memoised — the resolver asks once per component
and probing spawns a process — with a 60s TTL, because an unbounded memo would recreate,
inside one process, exactly the defect `default_backend_probe`'s own comment describes:

<!-- zh -->
**修复。** `probe_container_runtime()` 对两半都做测量：CLI 必须存在，*并且* 运行时必须应答（`<bin> info`）。它做了记忆化（memoised）—— 解析器对每个组件只询问一次，而每次探针（probe）都会派生一个进程 —— 并带有 60 秒的 TTL（存活时间），因为一份无界的记忆化会在单个进程之内重新制造出 `default_backend_probe` 自己的注释所描述的那个缺陷：

> the previous resolver hardcoded "no runtime", so installing Docker changed nothing and
> container components stayed UNAVAILABLE forever

<!-- zh -->
> 先前的解析器把 “no runtime” 写死，于是安装 Docker 不能改变任何东西，容器组件永远停留在 UNAVAILABLE

A long-lived agent that starts before its daemon would be in that position for its whole
life. A daemon can also stop, so the positive answer expires on the same clock.

<!-- zh -->
一个在守护进程启动之前就启动的长驻代理，会在其整个生命周期里都处于那种境地。守护进程也可能停止，因此那个肯定的答案按同一时钟到期。

There was a second copy of the question, too. `ContainerBackend.available()` and
`default_backend_probe()` each answered "can this machine run containers?" with their own
`shutil.which`. Both now call the one probe: a question answered twice is a question that
will eventually be answered two different ways.

<!-- zh -->
这个问题还有第二份副本。`ContainerBackend.available()` 与 `default_backend_probe()` 各自用自己那份 `shutil.which` 回答“这台机器能运行容器吗？”。现在两者都调用同一个探针：一个被回答两次的问题，终将得到两种不同的答案。

**Defence in depth.** A daemon that dies mid-session still produces a non-zero exit at
`invoke()` time. `_runtime_did_not_start()` recognises the CLI's own "cannot connect"
messages and reports `UNAVAILABLE`, refreshing the probe so a stopped daemon is not
asserted as present for the rest of the process. A container that genuinely ran and errored
is still `FAILED` — there is a test for each direction, because laundering real component
errors into `UNAVAILABLE` would be the same defect pointing the other way.

<!-- zh -->
**防御纵深（defence in depth）。** 在会话中途死掉的守护进程，仍会在 `invoke()` 时产生非零退出码。`_runtime_did_not_start()` 能识别 CLI 自身的 “cannot connect” 消息并报告 `UNAVAILABLE`，同时刷新探针，使一个已停止的守护进程不会在该进程余下的时间里被断言为存在。真正运行过并出错的容器仍然是 `FAILED` —— 两个方向各有一个测试，因为把真实的组件错误洗成 `UNAVAILABLE`，就是同一个缺陷指向了另一边。

## 2. The ordering: a machine-independent defect diagnosed as a machine problem · 2. 编排顺序：一个与机器无关的缺陷被诊断为机器问题

`invoke()` checked host availability **before** the component's own declaration. A manifest
with no `runtime.entrypoint` is broken on every machine; host capability is true on some and
false on others. So the same manifest was diagnosed differently depending on where it ran:

<!-- zh -->
`invoke()` 在组件自身的声明**之前**先检查主机可用性。一份没有 `runtime.entrypoint` 的清单在任何机器上都是坏的；而主机能力在某些机器上为真、在另一些机器上为假。于是同一份清单会因运行位置不同而得到不同的诊断：

```
before, host WITHOUT a container CLI:  "no container runtime found (looked for docker, …)"
before, host WITH a container CLI:     "component declares no runtime.entrypoint, …"
```

A developer on the first host was sent to install Docker in order to discover that their
manifest was wrong.

<!-- zh -->
在第一种主机上的开发者被指引去安装 Docker，结果才发现自己的清单是错的。

**Fix.** What the *component* declares is checked first — entrypoint, then image, both
machine-independent — and host capability second. The diagnosis no longer depends on where
it was run:

<!-- zh -->
**修复。** 先检查*组件*声明了什么 —— 先是 entrypoint，然后是 image，两者都与机器无关 —— 其次才是主机能力。诊断结果不再取决于它在哪里运行：

```
after,  host WITHOUT a container CLI:  "component declares no runtime.entrypoint, …"
after,  host WITH a container CLI:     "component declares no runtime.entrypoint, …"
```

## 3. A test that disappeared exactly where the defect appeared · 3. 一个恰好在缺陷出现处消失的测试

```python
def test_container_backend_reports_unavailable_honestly() -> None:
    cb = ContainerBackend()
    if cb.available():
        pytest.skip("a container runtime is present on this machine")
```

On any machine with a container CLI installed the assertions never ran — and under defect 1
that was *every* machine with the CLI, daemon or not, which is precisely the machine the bug
lived on. A test that skips itself where the defect appears is not covering it.

<!-- zh -->
在任何装了容器 CLI 的机器上，这些断言从未运行 —— 而在缺陷 1 之下，那就是*每一台*装有 CLI 的机器，无论有没有守护进程，而这恰恰就是缺陷寄居的那种机器。一个在缺陷出现处把自己跳过的测试，并没有覆盖该缺陷。

It injects the probe now, so both branches are exercised everywhere.

<!-- zh -->
现在它把探针注入进来，因此两个分支在任何地方都会被走到。

## Result · 结果

```
135 passed,  7 skipped   (before — one skip concealing the defect)
140 passed,  6 skipped   (after)
```

The five new tests all fail against the unmodified tree. Still open and unchanged:
`analyze_failures` counts `UNAVAILABLE` alongside `FAILED`, which is its documented
behaviour — an unavailable capability is legitimately interesting to an evolution agent
(it might propose a pure-Python fallback). What was wrong was the status, not the policy,
and the `statuses` breakdown now lets a caller tell the two apart.

<!-- zh -->
这五个新测试在未修改的代码树上全部失败。仍然开放且未改变：`analyze_failures` 把 `UNAVAILABLE` 与 `FAILED` 一并计数，这是它已记录在案的行为 —— 一项不可用的能力对演化代理而言是合理值得关注的（它可能会提议一个纯 Python 的降级方案）。错的是状态，而不是策略；`statuses` 细分现在让调用方能够把两者区分开来。
