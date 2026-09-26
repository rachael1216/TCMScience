# Arena data

Five documents drive the site:

| Document | Drives |
| --- | --- |
| `tracks.json` | the Season, its eight dimensions, its six tracks, its hard gates, the aggregation rule |
| `leaderboard.json` | one row per system per track, each with its full score decomposition |
| `runs.json` | full run bundles: trace, evidence, budget, artifacts, claim verdicts |
| `benchmarks.json` | the frozen benchmark registry: Seasons, sources and licences, scoring rules, citation |
| `skills.json` | the candidate / stable / benchmark registries and their entries |

## `*.json` is generated and is not committed

`*.json` here is written by `scripts/build_arena_data.py` from the registries,
and is ignored because it carries leaderboard scores and run traces. The
generator, the schemas and the page templates are committed, so the site can be
rebuilt from a registry by anyone holding one. What is not published is the
result set itself.

## `*.example.json` is committed on purpose

A fresh clone has no generated files, and a site that renders nothing until you
run a generator is not demonstrable. The example set carries **placeholder rows
only** — no real scores, no real run traces — and exists so every page renders on
a fresh clone.

The site fetches `data/<name>.json` first and falls back to
`data/<name>.example.json`. Every example document carries `"example": true`,
and the site shows a banner naming the file it actually read.

```sh
cd arena/web
python3 scripts/embed_arena_data.py   # mirror the example set into assets/
node --check assets/app.js
```

The example set must never contain a real result. If a placeholder and a
published row could be confused, the placeholder is wrong, not the banner.

## The contract

The full field-by-field contract — what each document must contain, which keys
the renderer requires, and the invariants a checker should enforce — is in
[`../README.md`](../README.md#the-data-contract).

## Read-only

Nothing in `arena/web/` writes to this directory. The generator is the only
writer (ADR-0004).
