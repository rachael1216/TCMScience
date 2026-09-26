# ADR-0002 — Runtime, Skill, Source and Benchmark version independently · ADR-0002 —— 运行时、技能、来源与基准各自独立地版本化

- **Status:** Accepted
- **Date:** 2026-09-25
- **Related:** [ADR-0001](0001-three-registry-separation.md)

<!-- zh -->
- **状态：** 已接受
- **日期：** 2026-09-25
- **相关：** [ADR-0001](0001-three-registry-separation.md)

## Context · 背景

A published TCMScience result is a joint statement about four things: the
kernel that executed the run, the skills it resolved, the external data sources
it read, and the benchmark it was scored against. Today these are only loosely
versioned — `psh` and `bioagent` carry `__version__`, skill manifests carry
`version`/`api_version`, and connectors carry free-text licence strings with no
snapshot identity at all.

<!-- zh -->
一篇已发表的 TCMScience 结果，是对四件事的联合陈述：执行该次运行的（可信）内核、它所解析出的技能、它所读取的外部数据来源，以及它被评分所依据的基准。目前这四者的版本化都很松散——`psh` 与 `bioagent` 带有 `__version__`，技能清单（skill manifest）带有 `version`/`api_version`，而连接器（connector）只携带自由文本的许可字符串，完全没有快照标识。

External sources are the sharp edge. HERB, ETCM, BindingDB and openFDA change
without telling us. A network-pharmacology result produced in March and
reproduced in September is not the same experiment if the underlying database
moved, even though every line of our code is identical.

<!-- zh -->
外部来源是最锋利的那道刃。HERB、ETCM、BindingDB 与 openFDA 会在不通知我们的情况下发生变化。一个三月产出、九月复现的网络药理学（network pharmacology, **NP**）结果，如果底层数据库发生了变动，那它就不再是同一个实验，即便我们的代码一行未改。

A single project-wide version number would create the opposite problem: it
would force a new release for every skill addition, and would hide which axis
actually changed when two results disagree.

<!-- zh -->
而一个全项目统一的版本号会造成相反的问题：它会迫使每新增一个技能就发一次新版，并且在两个结果不一致时，掩盖了究竟是哪一个轴发生了变化。

## Decision · 决策

Version the four axes separately, and make every citable result name all four.

| Axis | Where it lives | Changes when |
| --- | --- | --- |
| **Runtime** | `psh.__version__`, `bioagent.__version__` | code changes |
| **Skill** | `registry/skills.lock.yaml` revision | a promotion is approved |
| **Source** | `registry/sources.lock.yaml` revision | a connector's snapshot moves |
| **Benchmark** | `benchmarks/registry/<season>.yaml` | a Season is cut |

<!-- zh -->
对四个轴分别版本化，并使每一个可被引用的结果都指明全部四个轴。

| Axis 轴 | Where it lives 存放位置 | Changes when 变更时机 |
| --- | --- | --- |
| **Runtime**<br>运行时 | `psh.__version__`、`bioagent.__version__` | 代码变更时 |
| **Skill**<br>技能 | `registry/skills.lock.yaml` 修订版本 | 有晋级被批准时 |
| **Source**<br>来源 | `registry/sources.lock.yaml` 修订版本 | 连接器的快照发生移动时 |
| **Benchmark**<br>基准 | `benchmarks/registry/<season>.yaml` | 一个基准赛季（Season）被切出时 |

A **run record** carries a `composite_version` naming all four. That string,
not any individual version, is what a leaderboard row and a paper table cite.

<!-- zh -->
一份**运行记录**（run record）携带一个 `composite_version`，其中指明全部四个轴。排行榜的一行与论文的一张表所引用的，是这个字符串，而不是任何一个单独的版本号。

### Source snapshots are pinned by content, not by date · 来源快照按内容固定，而非按日期固定

A `SourceCard` records a `snapshot_hash` over the normalised response of every
declared operation on a declared date. "HERB 2.0" is not a version; the hash is.
Connector results carry the card's hash so a downstream artifact can state
exactly which bytes produced it.

<!-- zh -->
`SourceCard`（来源卡）记录一个 `snapshot_hash`（快照哈希），它覆盖所有已声明操作在某个已声明日期上的规范化响应。"HERB 2.0" 不是版本；那个哈希才是。连接器的结果携带该来源卡的哈希，因此下游产物（artifact）可以精确说明是哪些字节产生了它。

## Consequences · 后果

**Positive**

- A result can be reproduced or refuted by anyone holding the four identifiers.
- Drift in an external database surfaces as a *changed source hash*, not as an
  unexplained change in output.
- Adding a skill does not force a Runtime release.

<!-- zh -->
**正面**

- 任何持有这四个标识符的人都可以复现或反驳一个结果。
- 外部数据库的漂移会表现为*来源哈希发生了变化*，而不是输出出现了无法解释的变化。
- 新增一个技能不会强制触发运行时（Runtime）发版。

**Negative / accepted costs**

- More bookkeeping: every artifact must thread four versions instead of one.
- Snapshot hashing costs a fetch per declared operation per refresh. This is
  amortised — snapshots refresh on the Source axis's own cadence, not per run.

<!-- zh -->
**负面 / 已接受的代价**

- 记账工作更多：每个产物都必须贯穿传递四个版本，而不是一个。
- 快照哈希的代价是每次刷新时对每个已声明操作各抓取一次。这一成本被摊薄了——快照按来源轴自身的节奏刷新，而不是每次运行都刷新。

## Compliance · 合规

- `bioagent.contracts.artifact.ResearchArtifact` requires `composite_version`.
- `bioagent.contracts.source_card.SourceCard` requires `snapshot_hash` and
  `snapshot_at` before a connector may be used in a *publishable* artifact.
- The Arena leaderboard refuses to render a row whose four versions are not all
  resolvable in the published registries.

<!-- zh -->
- `bioagent.contracts.artifact.ResearchArtifact`（科研产物）要求提供 `composite_version`。
- `bioagent.contracts.source_card.SourceCard`（来源卡）要求提供 `snapshot_hash` 与 `snapshot_at`，之后该连接器才可被用于*可发表的*产物。
- Arena 排行榜拒绝渲染四个版本无法在已发布注册表中全部解析的行。
