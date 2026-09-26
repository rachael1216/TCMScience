# Arena data · Arena 数据

Five documents drive the site:

<!-- zh -->
五个文档驱动整个站点：

| Document | Drives |
| --- | --- |
| `tracks.json` | the Season, its eight dimensions, its six tracks, its hard gates, the aggregation rule |
| `leaderboard.json` | one row per system per track, each with its full score decomposition |
| `runs.json` | full run bundles: trace, evidence, budget, artifacts, claim verdicts |
| `benchmarks.json` | the frozen benchmark registry: Seasons, sources and licences, scoring rules, citation |
| `skills.json` | the candidate / stable / benchmark registries and their entries |

<!-- zh -->
| Document 文档 | Drives 驱动的页面内容 |
| --- | --- |
| `tracks.json` | 基准赛季（benchmark Season）、其八个维度、六个赛道、硬门槛（hard gate）以及聚合规则 |
| `leaderboard.json` | 每个系统在每个赛道上各一行，每行带有完整的分数分解 |
| `runs.json` | 完整的运行包：轨迹、证据、预算、产物、主张判定 |
| `benchmarks.json` | 冻结基准注册表：各赛季、来源与许可、评分规则、引用信息 |
| `skills.json` | 候选 / 稳定 / 基准三类注册表及其条目 |

## `*.json` is generated and is not committed · `*.json` 由生成而来，不纳入提交

`*.json` here is written by `scripts/build_arena_data.py` from the registries,
and is ignored because it carries leaderboard scores and run traces. The
generator, the schemas and the page templates are committed, so the site can be
rebuilt from a registry by anyone holding one. What is not published is the
result set itself.

<!-- zh -->
此处的 `*.json` 由 `scripts/build_arena_data.py` 从注册表写入，因携带排行榜分数与运行轨迹而被忽略（gitignore）。生成器、schema 与页面模板都已提交，因此任何持有一份注册表的人都能重建该站点。未予发布的，是结果集本身。

## `*.example.json` is committed on purpose · `*.example.json` 是刻意提交的

A fresh clone has no generated files, and a site that renders nothing until you
run a generator is not demonstrable. The example set carries **placeholder rows
only** — no real scores, no real run traces — and exists so every page renders on
a fresh clone.

<!-- zh -->
全新的克隆没有任何生成文件，而一个在你运行生成器之前什么都渲染不出来的站点是无法演示的。示例数据集**只包含占位行**——没有真实分数，没有真实运行轨迹——它的存在是为了让每个页面在全新克隆中都能渲染。

The site fetches `data/<name>.json` first and falls back to
`data/<name>.example.json`. Every example document carries `"example": true`,
and the site shows a banner naming the file it actually read.

<!-- zh -->
站点会优先抓取 `data/<name>.json`，失败时回退到 `data/<name>.example.json`。每个示例文档都携带 `"example": true`，站点会显示一条横幅，指明它实际读取的是哪个文件。

```sh
cd arena/web
python3 scripts/embed_arena_data.py   # mirror the example set into assets/
node --check assets/app.js
```

The example set must never contain a real result. If a placeholder and a
published row could be confused, the placeholder is wrong, not the banner.

<!-- zh -->
示例数据集绝不能包含真实结果。如果占位数据与已发布的行可能被混淆，那么错的是占位数据，而不是那条横幅。

## The contract · 契约

The full field-by-field contract — what each document must contain, which keys
the renderer requires, and the invariants a checker should enforce — is in
[`../README.md`](../README.md#the-data-contract).

<!-- zh -->
逐字段的完整契约——每个文档必须包含什么、渲染器要求哪些键、以及校验器应当强制执行哪些不变量——见 [`../README.md`](../README.md#the-data-contract)。

## Read-only · 只读

Nothing in `arena/web/` writes to this directory. The generator is the only
writer (ADR-0004).

<!-- zh -->
`arena/web/` 中没有任何东西会写入本目录。生成器是唯一的写入者（ADR-0004）。
