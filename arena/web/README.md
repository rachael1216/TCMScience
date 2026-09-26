# TCMScience Arena — static front end · TCMScience Arena —— 静态前端

A read-only evaluation surface for TCMScience. Plain HTML, one stylesheet, one
script. No build step, no npm, no framework, no CDN, no server code.

<!-- zh -->
TCMScience 的只读评测界面。纯 HTML、一份样式表、一个脚本。没有构建步骤，没有 npm，没有框架，没有 CDN，没有服务端代码。

```
arena/web/
  index.html          overview: what the Arena is, the governance thesis, at-a-glance
  leaderboard.html    filterable table, eight dimensions always decomposed
  benchmarks.html     the frozen benchmark registry: Seasons, sources, licences, rules
  skills.html         stable vs candidate skills, permissions, measured deltas
  submit.html         the three submission modes and the evaluation flow
  methods.html        scoring, gates, aggregation, the prediction prohibition, COI
  run.html            run detail, addressed as run.html?id=<run_id>
  compare.html        two systems side by side, per track
  assets/style.css    all styling; design tokens as CSS custom properties
  assets/app.js       all behaviour; no framework
  assets/fallback-data.js   mirror of data/*.example.json (see "Offline" below)
  data/*.example.json committed placeholder data
  data/*.json         generated results — gitignored, absent from a fresh clone
  runs/               reserved for published run bundles
```

## The read-only rule · 只读规则

This directory contains **no server-side code and no write path** (ADR-0004).

<!-- zh -->
本目录**不含任何服务端代码，也不含任何写入路径**（ADR-0004）。

The Arena renders published result bundles. It never computes a score, never
re-ranks, and never writes a registry. Every number on screen — dimension
scores, the aggregate, the rank, the board — is read from JSON as published by
the result generator.

<!-- zh -->
Arena 渲染已发布的结果包（result bundle）。它从不计算分数，从不重新排名，也从不写入注册表。屏幕上的每一个数字——各维度分数、汇总值、名次、榜单——都是从结果生成器所发布的 JSON 中读出的。

Two consequences worth stating explicitly, because they are easy to erode:

<!-- zh -->
有两点后果值得明确说明，因为它们很容易被侵蚀：

- **No form posts anywhere.** `submit.html` documents a pull request or issue
  process. There is no backend to accept a form, and adding one is a separate,
  later service that still does not render leaderboard numbers.
- **Filtering and sorting are presentation.** `applyFilters` and `sortRows` in
  `app.js` change which rows you see and in what order. They never rewrite a
  published `rank`, and the leaderboard caption says so.

<!-- zh -->
- **任何地方都没有表单提交。** `submit.html` 说明的是 pull request 或 issue 流程。不存在用于接收表单的后端；即便日后新增，那也是一个独立的、更晚期的服务，而且它仍然不会渲染排行榜数字。
- **筛选与排序只是呈现层。** `app.js` 中的 `applyFilters` 与 `sortRows` 改变的是你看到哪些行、以何种顺序看到。它们绝不会改写已发布的 `rank`，排行榜的说明文字也明确写出了这一点。

## The data contract · 数据契约

Five documents drive the site. Each is fetched as `data/<name>.json`, falling
back to `data/<name>.example.json` and then to the embedded mirror. Every
document carries `example: true` when it is placeholder data.

<!-- zh -->
五个文档驱动整个站点。每个都以 `data/<name>.json` 的形式抓取，失败时回退到 `data/<name>.example.json`，再回退到内嵌的镜像。当文档是占位数据时，都携带 `example: true`。

All scores are in `[0, 1]`, higher is better, three decimal places as published.
Latency and Cost are the exception to "raw is what you see": they are published
as 0–1 subscores **and** as raw seconds / USD, and the UI always shows both.

<!-- zh -->
所有分数都在 `[0, 1]` 区间内，越高越好，发布时保留三位小数。延迟（Latency）与成本（Cost）是「原始值即所见」的例外：它们既以 0–1 的子分数发布，**也**以原始秒数 / 美元发布，UI 始终同时展示两者。

### `tracks.json` · 数据文档

The Season, its dimensions, its tracks and its gates. Read by every page that
needs to know what a dimension is.

<!-- zh -->
基准赛季（benchmark Season）、其维度、其赛道（track）与其门槛（gate）。任何需要知道「某个维度是什么」的页面都会读取它。

```jsonc
{
  "example": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "season": "2026-S1",
  "season_label": "Season 2026-S1",
  "season_status": "frozen",
  "frozen_on": "2026-09-01",
  "dimensions": [
    {
      "key": "task_success",            // must match the keys in runs[].scores
      "label": "Task Success",          // the column header
      "weight": 0.125,                  // published; sums to 1 across dimensions
      "direction": "higher",
      "range": [0, 1],
      "raw_unit": "s",                  // optional, for latency/cost
      "definition": "…"                 // shown as the column tooltip and in methods
    }
    // … eight entries, in display order
  ],
  "tracks": [
    { "id": "TCM-Entity", "name": "TCM-Entity", "cases": 20,
      "metric_focus": ["entity accuracy", "…"],
      "question": "…", "notes": "…" }
  ],
  "gates": [
    { "id": "G1", "name": "Fabricated citations",
      "rule": "fabricated_citation_rate == 0",
      "threshold": "0 tolerated",
      "codes": ["ART104", "CLM002"],
      "board": "experimental",
      "rationale": "…" }
  ],
  "aggregation": {
    "method": "weighted geometric mean",
    "formula": "aggregate = exp( sum_i w_i * ln(max(x_i, eps)) / sum_i w_i )",
    "eps": 0.05,
    "note": "…"
  },
  "composite_version": { "runtime": "…", "skill": "…", "source": "…", "benchmark": "…" }
}
```

**Required by the renderer:** `dimensions[]` (the leaderboard's columns and the
methods page's tables are generated from it), `tracks[]`, `gates[]`.
`aggregation` is displayed, never executed.

<!-- zh -->
**渲染器所必需的字段：** `dimensions[]`（排行榜的列与方法页的表格都由它生成）、`tracks[]`、`gates[]`。`aggregation` 只被展示，从不被执行。

### `leaderboard.json` · 数据文档

One row per **system per track**. Sixty rows for ten systems across six tracks.
Every row carries its full decomposition; a row without one is a data error.

<!-- zh -->
每个**系统在每个赛道上**一行。十个系统、六个赛道共六十行。每一行都携带其完整分解；没有分解的行属于数据错误。

```jsonc
{
  "example": true,
  "generated_at": "…",
  "season": "2026-S1",
  "boards": [
    { "id": "trusted", "label": "Trusted board", "note": "…" },
    { "id": "experimental", "label": "Experimental board", "note": "…" }
  ],
  "dimensions": [ { "key": "task_success", "label": "Task Success", "weight": 0.125 } ],
  "runs": [
    {
      "run_id": "r-2026s1-alpha-entity",   // joins to runs.json and to run.html?id=
      "system": "Example Agent Alpha",
      "system_slug": "alpha",              // stable key used by compare.html
      "type": "Skill",                     // "Skill" | "OCI Container" | "Remote API"
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",                  // "trusted" | "experimental"
      "rank": 1,                           // published rank within track + board
      "cases": 20,
      "aggregate": 0.782,                  // published; never recomputed here
      "aggregate_ci95": 0.012,             // optional half-width
      "scores": { "task_success": 0.88, "…": 0.0 },   // all eight keys required
      "raw": { "latency_s": 1042, "cost_usd": 2.31 }, // required for latency/cost
      "versions": { "runtime": "…", "skill": "…", "source": "…", "benchmark": "…" },
      "blocking_reasons": [                // empty array for the trusted board
        { "gate": "G1", "detail": "…", "codes": ["ART104", "CLM002"] }
      ],
      "refusal_codes": ["CLM004"],         // optional; any code, shown as a chip
      "detail_published": true,            // whether a run bundle exists in runs.json
      "placeholder": true                  // optional; renders an "example" chip
    }
  ]
}
```

Rules the checker should enforce:

<!-- zh -->
校验器应当强制执行的规则：

- `scores` has exactly the keys of `dimensions`, all present.
- A row with a non-empty `blocking_reasons` has `board == "experimental"`, and
  vice versa. A gated row must never be dropped — it moves boards.
- `rank` is dense within `(track, board)`.
- A row whose four `versions` values do not all resolve in the published
  registries is not rendered at all (ADR-0002).

<!-- zh -->
- `scores` 的键与 `dimensions` 的键完全一致，且全部存在。
- `blocking_reasons` 非空的行，其 `board == "experimental"`，反之亦然。被门槛拦截的行绝不能被丢弃——它只是换一个榜单。
- `rank` 在 `(track, board)` 内是稠密的。
- 四个 `versions` 值无法在已发布注册表中全部解析的行，根本不会被渲染（ADR-0002）。

### `runs.json` · 数据文档

Full run bundles. `run.html?id=` reads this; a `run_id` present in
`leaderboard.json` but absent here still renders its decomposition and its four
version axes, with an explicit "trace not published" notice. It never invents
the missing sections.

<!-- zh -->
完整的运行包。`run.html?id=` 读取它；某个 `run_id` 出现在 `leaderboard.json` 中但在此处缺失时，页面仍会渲染其分解与四个版本轴，并给出明确的「轨迹未发布」提示。它绝不会凭空捏造缺失的部分。

```jsonc
{
  "example": true,
  "season": "2026-S1",
  "runs": [
    {
      "run_id": "r-2026s1-alpha-entity",
      "system": "…", "type": "…", "track": "…", "board": "…", "version": "…",
      "submitted_at": "2026-09-18T14:02:11Z",
      "submitted_by": "…",
      "artifact_status": "validated",   // draft | validated | refused | experimental
      "aggregate": 0.782, "aggregate_ci95": 0.012,
      "scores": { "…": 0.0 }, "raw": { "latency_s": 1042, "cost_usd": 2.31 },
      "versions": { "runtime": "…", "skill": "…", "source": "…", "benchmark": "…" },
      "blocking_reasons": [],
      "budget": {
        "limits":   { "tokens": 250000, "tool_calls": 120, "wall_clock_s": 1800, "cost_usd": 6.0 },
        "consumed": { "tokens": 184320, "tool_calls": 96,  "wall_clock_s": 1042, "cost_usd": 2.31 }
      },
      "trace": [
        { "step": 1, "kind": "tool_call",   // plan | tool_call | retrieval | check |
                                            // abstain | answer | emit | budget
          "name": "lexicon.resolve",
          "summary": "…", "note": "…",
          "duration_ms": 512,
          "status": "ok" }                  // anything other than "ok" renders as a failure
      ],
      "evidence": [
        { "id": "ev-1", "title": "…",
          "identifier_type": "pmid",        // pmid | pmcid | doi | nct | chictr | isrctn |
                                            // dataset | local_artifact | classical_passage |
                                            // pharmacopoeia | registry_record | user_supplied
          "identifier": "00000001",
          "design": "randomized_trial",     // an EMPIRICAL_DESIGNS or PREDICTIVE_DESIGNS value
          "source_card": { "name": "PubMed", "snapshot_hash": "sha256:…",
                           "snapshot_at": "2026-08-14", "licence": "…" },
          "quote": "…", "usable": true, "retracted": false }
      ],
      "artifacts": [
        { "path": "artifact.json", "sha256": "<64 hex>", "media_type": "application/json",
          "bytes": 18422, "description": "…" }   // empty sha256 renders the ART107 badge
      ],
      "claims": [
        { "claim_id": "c1", "text": "…",
          "claim_kind": "mechanism",        // mechanism | clinical | methodological |
                                            // uncertainty | recommendation
          "allowed": true,
          "codes": [],                      // CLM0xx
          "reasons": [ { "code": "CLM004", "detail": "…" } ],
          "weakest_tier": "preclinical",
          "confidence": 0.9,
          "prediction_as_fact": false,      // true renders the G4 callout
          "needs_declaration": false,
          "caveats": ["…"] }
      ],
      "limitations": ["…"],                 // empty renders the ART110 warning
      "notes": "…"
    }
  ]
}
```

`prediction_as_fact: true` or a `CLM004` code triggers the prediction-prohibition
callout on the run page, quoting the rule. `codes` are looked up in the published
table in `app.js`; an unknown code renders with a "not in the published table"
tooltip rather than being hidden.

<!-- zh -->
`prediction_as_fact: true` 或出现 `CLM004` 代码，会在运行页触发「禁止预测」提示框，并引用相应规则。`codes` 会在 `app.js` 中已发布的代码表里查找；未知代码会以「不在已发布表中」的提示气泡渲染，而不是被隐藏。

### `benchmarks.json` · 数据文档

```jsonc
{
  "example": true,
  "current_season": "2026-S1",
  "seasons": [
    { "season": "2026-S1", "version": "1.0.0", "status": "frozen",
      "frozen_on": "2026-09-01", "note": "…", "cases_total": 120,
      "tracks": [ { "id": "TCM-Entity", "cases": 20, "metric_focus": ["…"] } ],
      "splits": [ { "name": "dev", "cases": 60, "availability": "…" } ],
      "sources": [
        { "name": "HERB", "role": "…",
          "licence": "…", "licence_verified": false,   // false renders an "unverified" badge
          "url": "…", "snapshot_hash": "sha256:…", "snapshot_at": "2026-08-14",
          "used_by": ["TCM-Entity"] }
      ] }
  ],
  "scoring_rules": [ { "id": "S1", "rule": "…", "applies_to": "…", "detail": "…" } ],
  "citation": { "text": "…", "bibtex": "…", "example_placeholder": true }
}
```

`licence_verified` exists because a licence string is not evidence that the
licence permits the use. The flag is set by a human at Season cut, and the UI
renders it next to the string rather than assuming it. Only the current Season's
sources are listed.

<!-- zh -->
之所以存在 `licence_verified`，是因为一段许可字符串并不构成「该许可允许此种使用」的证据。该标志由人工在赛季切出（Season cut）时设置，UI 把它渲染在字符串旁边，而不是假定其成立。此处只列出当前赛季的来源。

### `skills.json` · 数据文档

```jsonc
{
  "example": true,
  "season": "2026-S1",
  "registries": {
    "candidate": { "revision": "…", "updated": "…", "count": 42, "role": "…" },
    "stable":    { "revision": "…", "updated": "…", "count": 7,  "role": "…" },
    "benchmark": { "revision": "…", "updated": "…",                   "role": "…" }
  },
  "skills": [
    { "id": "tcm.entity-resolver", "name": "TCM Entity Resolver",
      "status": "stable",               // "stable" | "candidate"  (also "rejected")
      "version": "1.3.0", "api_version": "1.0",
      "source_repo": "github.com/…", "commit": "9f2c1ab",
      "licence": "Apache-2.0", "licence_verified": false,
      "permissions": ["fs:read:benchmark", "network:pubmed"],
      "benchmark_delta": { "track": "TCM-Entity", "metric": "entity accuracy",
                           "delta": 0.031, "n": 20, "note": "…" },  // delta null = not measured
      "approval": { "status": "approved",   // approved | pending | held
                    "decision_id": "PD-2026-014", "decided_by": "…",
                    "decided_on": "2026-09-12" },
      "notes": "…", "placeholder": true }
  ],
  "promotion_rule": "…"
}
```

A `delta` of `null` renders as `not measured`, which is the point: a candidate is
never promoted on the strength of an unmeasured claim. Permission strings that
widen the runtime's authority (`network:*`, `exec:*`, `fs:write:*`) are rendered
with a warning glyph as well as a colour.

<!-- zh -->
`delta` 为 `null` 时会渲染为 `not measured`（未测量），这正是要点所在：候选技能绝不会凭借一项未经测量的主张而获得晋级。会拓宽运行时（runtime）权限的权限字符串（`network:*`、`exec:*`、`fs:write:*`）在渲染时除颜色外还带有警告图形符号。

## Regenerating the data · 重新生成数据

`data/*.json` is generated from the registries by
`scripts/build_arena_data.py`, which is the **only writer**. The site never
writes. Regeneration is a build step, not a live update: a leaderboard should
show finished, frozen results, not in-progress runs.

<!-- zh -->
`data/*.json` 由 `scripts/build_arena_data.py` 从注册表生成，该脚本是**唯一的写入者**。网站从不写入。重新生成是一个构建步骤，而不是实时更新：排行榜应当展示已完成的、冻结的结果，而不是进行中的运行。

After regenerating, mirror the placeholder set and re-check the script:

<!-- zh -->
重新生成之后，镜像占位数据集并重新检查脚本：

```sh
python3 scripts/embed_arena_data.py     # rewrites assets/fallback-data.js
node --check assets/app.js
```

## Offline and `file://` · 离线与 `file://`

Double-clicking `index.html` must work, and it does, but not through `fetch`:
`fetch()` of a `file://` URL is blocked as cross-origin by every current
browser. The load order in `app.js` is therefore:

<!-- zh -->
双击 `index.html` 必须能正常工作，而它确实可以，但不是通过 `fetch`：对 `file://` URL 调用 `fetch()` 会被所有现行浏览器以跨源为由拦截。因此 `app.js` 中的加载顺序是：

1. `data/<name>.json` — the generated results, on a server or GitHub Pages.
2. `data/<name>.example.json` — the committed placeholders, when the generated
   file is absent.
3. `window.ARENA_FALLBACK[name]` from `assets/fallback-data.js` — a byte-for-byte
   mirror of the example JSON, loaded by a classic `<script>` tag, which
   `file://` does allow.

<!-- zh -->
1. `data/<name>.json` —— 生成的结果，在服务器或 GitHub Pages 上时使用。
2. `data/<name>.example.json` —— 已提交的占位数据，在生成文件缺失时使用。
3. `assets/fallback-data.js` 中的 `window.ARENA_FALLBACK[name]` —— 与示例 JSON 逐字节一致的镜像，由经典 `<script>` 标签加载，而 `file://` 允许这种方式。

Step 3 is generated from step 2 and never edited by hand, so the two cannot
disagree except about their age. When any fallback is used, a banner appears at
the top of the page saying so: every row is a placeholder and every number is
synthetic, but the layout, the decomposition and the gate logic are the real
ones.

<!-- zh -->
第 3 步由第 2 步生成，绝不手工编辑，因此两者除新旧程度外不可能不一致。每当使用任何回退数据时，页面顶部会出现横幅加以说明：每一行都是占位数据，每个数字都是合成的，但版式、分解方式与门槛逻辑都是真实的。

## Citing · 引用

Cite the Season and the four version axes, not this website. The axes are what
make a number re-derivable by someone who has none of your code. The citation
block, with a placeholder BibTeX entry, is on `benchmarks.html`.

<!-- zh -->
请引用基准赛季（Season）与四个版本轴，而不是引用本网站。正是这些轴，使得一个没有任何你的代码的人也能重新推导出某个数字。带有占位 BibTeX 条目的引用块位于 `benchmarks.html`。

## Accessibility notes · 无障碍说明

- Real `<table>` semantics with `<th scope>`; `aria-sort` on sortable columns,
  applied to one column at a time.
- Filters are native `<select>`, `<input type="search">` and radio inputs, all
  keyboard-reachable; the table's scroll container is focusable with an
  `aria-label`.
- No information is carried by colour alone. Boards, gates, permissions,
  references and comparison leads all pair colour with a glyph and with text.
- Score bars are `aria-hidden`; the number beside them is the information.
- `--muted-foreground` (the reference token) is 4.4:1 on `--muted`, which fails
  AA for small text, so small print uses `--muted-foreground-strong` (7.1:1).
  `--secondary` (teal) is 2.4:1 on white and is used for fills and rules only;
  readable teal is `--secondary-ink`.

<!-- zh -->
- 使用真正的 `<table>` 语义与 `<th scope>`；可排序列上有 `aria-sort`，且一次只应用于一列。
- 筛选控件使用原生 `<select>`、`<input type="search">` 与单选按钮，全部可通过键盘到达；表格的滚动容器可聚焦，并带有 `aria-label`。
- 没有任何信息仅靠颜色传达。榜单、门槛、权限、参考文献与对比领先项都把颜色与图形符号及文字配对使用。
- 分数条是 `aria-hidden` 的；其旁的数字才是信息本身。
- `--muted-foreground`（参考色标）在 `--muted` 上的对比度为 4.4:1，小字号下未达 AA 标准，因此小字使用 `--muted-foreground-strong`（7.1:1）。`--secondary`（青绿色）在白色上的对比度为 2.4:1，仅用于填充与分隔线；可读的青色是 `--secondary-ink`。
