# ADR-0004 — The Arena is read-only, and ships static-first · ADR-0004 —— Arena 只读，并优先以静态方式交付

- **Status:** Accepted
- **Date:** 2026-09-25
- **Related:** [ADR-0001](0001-three-registry-separation.md), [ADR-0002](0002-independent-version-axes.md)

<!-- zh -->
- **状态：** 已接受
- **日期：** 2026-09-25
- **相关：** [ADR-0001](0001-three-registry-separation.md)、[ADR-0002](0002-independent-version-axes.md)

## Context · 背景

`TCMScience Arena` is the public evaluation surface: leaderboards, benchmark
documentation, a skill registry viewer, run traces and a submission flow. The
plan's §3.6 specifies a full MVP stack (Next.js + FastAPI + PostgreSQL + Redis +
Celery + MinIO) and defers Kubernetes to the production phase.

<!-- zh -->
`TCMScience Arena` 是公开的评测界面：排行榜、基准文档、技能注册表浏览器、运行轨迹（run trace）以及提交流程。规划的 §3.6 节规定了一套完整的 MVP 技术栈（Next.js + FastAPI + PostgreSQL + Redis + Celery + MinIO），并将 Kubernetes 推迟到生产阶段。

There are two independent questions here, and bundling them is the mistake:

1. **What does the Arena read?** — answered by the registry design.
2. **How is it served?** — a deployment detail.

<!-- zh -->
这里有两个彼此独立的问题，把它们捆绑在一起才是错误所在：

1. **Arena 读取什么？** —— 由注册表设计来回答。
2. **它如何被提供？** —— 这属于部署细节。

Building the full stack first would mean that until a queue, a database and an
object store are all running, *nothing* about the evaluation is publicly
visible — including benchmark definitions, scoring rules and methods, which
need no backend at all. It would also put a writable service in front of the
very artifacts whose immutability is the project's central claim.

<!-- zh -->
先建整套技术栈，意味着在队列、数据库和对象存储全部跑起来之前，关于评测的*任何内容*都无法公开可见——包括基准定义、评分规则与方法，而这些根本不需要任何后端。它还会把一个可写入的服务摆到那些产物之前，而产物的不可变性正是本项目的核心主张。

## Decision · 决策

### 1. The Arena performs no evaluation and holds no authority · Arena 不执行任何评测，也不持有任何权威

Evaluation happens in the Runner, under a `PolicySnapshot`, writing signed
result bundles into the Frozen Benchmark Registry. The Arena **renders**
published bundles. It never computes a score, never re-ranks, and never writes
a registry.

<!-- zh -->
评测发生在 Runner 中，处于某个 `PolicySnapshot` 之下，并把已签名的结果包（result bundle）写入冻结基准注册表。Arena **渲染**已发布的包。它从不计算分数，从不重新排名，也从不写入注册表。

This is what makes a leaderboard number trustworthy: the website cannot change
it, and compromising the website does not compromise a result.

<!-- zh -->
这正是排行榜上的数字之所以可信的原因：网站无法更改它，攻陷网站也不会危及结果的真实性。

### 2. Static-first deployment · 静态优先的部署

The MVP is a static site — HTML, CSS, a little vanilla JS — driven by JSON
documents generated from the registries. It runs from GitHub Pages, requires no
credentials, no database and no server process, and is trivially archivable
alongside the paper.

<!-- zh -->
MVP 是一个静态站点——HTML、CSS、少量原生 JS——由从注册表生成的 JSON 文档驱动。它运行在 GitHub Pages 上，不需要任何凭据、数据库或服务端进程，并且可以极容易地与论文一并归档。

A dynamic submission backend (FastAPI + Postgres + a container runner) is a
**separate, later** service. When it arrives it accepts submissions and writes
*run requests*; it still does not render leaderboard numbers from its own
database. The static front end keeps reading generated JSON, so promoting to
the dynamic stack does not change how a result is displayed or trusted.

<!-- zh -->
动态提交后端（FastAPI + Postgres + 容器运行器）是一个**独立的、更晚期的**服务。当它到来时，它接受提交并写入*运行请求*；它仍然不会从自己的数据库渲染排行榜数字。静态前端继续读取生成的 JSON，因此升级到动态技术栈不会改变结果的展示方式或可信方式。

### 3. Interface, not decoration · 是界面，不是装饰

Three commitments the site must keep, because they are what distinguishes this
from a generic medical QA leaderboard:

<!-- zh -->
网站必须坚守三项承诺，因为正是它们把本网站与一个通用的医学问答排行榜区分开来：

- **Every score is decomposed.** A row shows Task Success, Evidence Grounding,
  Provenance Completeness, Reproducibility, Safety and Abstention, Claim
  Calibration, Latency and Cost. A single aggregate number is available but
  never shown alone.
- **Hard gates are visible.** Systems blocked from the trusted board (nonzero
  fabricated-citation rate, severe safety false negatives, no reproducible
  artifact) appear on a clearly labelled *Experimental* board with the blocking
  reason, not silently dropped.
- **A run is inspectable.** `/runs/{id}` exposes the trace: tool calls, evidence
  sources, budget consumed, and the artifacts produced.

<!-- zh -->
- **每个分数都被分解。** 一行会展示任务成功率（Task Success）、证据支撑度（Evidence Grounding）、溯源完整性（Provenance Completeness）、可复现性（Reproducibility）、安全性与拒答（Safety and Abstention）、主张校准（Claim Calibration）、延迟与成本（Latency and Cost）。单一的汇总数字可以获取，但绝不单独展示。
- **硬门槛（hard gate）可见。** 被拦在可信榜之外的系统（伪造引用率非零、严重的安全假阴性、无可复现的产物）会出现在明确标注的*实验榜*（Experimental board）上，并附上拦截理由，而不是被悄悄丢弃。
- **一次运行可被检视。** `/runs/{id}` 暴露其轨迹：工具调用、证据来源、已消耗的预算，以及所产生的产物。

## Consequences · 后果

**Positive**

- The evaluation surface is publishable immediately and archivable with the
  paper.
- No writable public service exists in front of immutable results.
- The static/dynamic split is a deployment boundary, so the dynamic stack can
  be built without re-designing the front end.

<!-- zh -->
**正面**

- 评测界面可以立即发布，并可与论文一并归档。
- 在不可变的结果之前，不存在任何可写入的公共服务。
- 静态/动态的切分是一条部署边界，因此可以在不重新设计前端的情况下构建动态技术栈。

**Negative / accepted costs**

- Submission is manual (a PR or an issue) until the dynamic backend lands.
- Regenerating the JSON is a build step; the site cannot show live in-progress
  runs. Accepted — a leaderboard should show finished, frozen results.

<!-- zh -->
**负面 / 已接受的代价**

- 在动态后端落地之前，提交是手工的（一个 PR 或一个 issue）。
- 重新生成 JSON 是一个构建步骤；网站无法展示进行中的运行。这一代价被接受——排行榜应当展示已完成的、冻结的结果。

## Compliance · 合规

- `arena/web/` contains no server-side code and no write path.
- `arena/web/data/*.json` is generated by `scripts/build_arena_data.py` from the
  registries; the generator is the only writer.
- Any page rendering a score must render its decomposition. A row without a
  decomposition fails `scripts/check_arena_data.py`.

<!-- zh -->
- `arena/web/` 中不包含任何服务端代码，也不存在任何写入路径。
- `arena/web/data/*.json` 由 `scripts/build_arena_data.py` 从注册表生成；生成器是唯一的写入者。
- 任何渲染分数的页面都必须渲染其分解。缺少分解的行无法通过 `scripts/check_arena_data.py`。
