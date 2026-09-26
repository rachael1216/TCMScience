# TCMScience Arena — static front end

A read-only evaluation surface for TCMScience. Plain HTML, one stylesheet, one
script. No build step, no npm, no framework, no CDN, no server code.

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

## The read-only rule

This directory contains **no server-side code and no write path** (ADR-0004).

The Arena renders published result bundles. It never computes a score, never
re-ranks, and never writes a registry. Every number on screen — dimension
scores, the aggregate, the rank, the board — is read from JSON as published by
the result generator.

Two consequences worth stating explicitly, because they are easy to erode:

- **No form posts anywhere.** `submit.html` documents a pull request or issue
  process. There is no backend to accept a form, and adding one is a separate,
  later service that still does not render leaderboard numbers.
- **Filtering and sorting are presentation.** `applyFilters` and `sortRows` in
  `app.js` change which rows you see and in what order. They never rewrite a
  published `rank`, and the leaderboard caption says so.

## The data contract

Five documents drive the site. Each is fetched as `data/<name>.json`, falling
back to `data/<name>.example.json` and then to the embedded mirror. Every
document carries `example: true` when it is placeholder data.

All scores are in `[0, 1]`, higher is better, three decimal places as published.
Latency and Cost are the exception to "raw is what you see": they are published
as 0–1 subscores **and** as raw seconds / USD, and the UI always shows both.

### `tracks.json`

The Season, its dimensions, its tracks and its gates. Read by every page that
needs to know what a dimension is.

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

### `leaderboard.json`

One row per **system per track**. Sixty rows for ten systems across six tracks.
Every row carries its full decomposition; a row without one is a data error.

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

- `scores` has exactly the keys of `dimensions`, all present.
- A row with a non-empty `blocking_reasons` has `board == "experimental"`, and
  vice versa. A gated row must never be dropped — it moves boards.
- `rank` is dense within `(track, board)`.
- A row whose four `versions` values do not all resolve in the published
  registries is not rendered at all (ADR-0002).

### `runs.json`

Full run bundles. `run.html?id=` reads this; a `run_id` present in
`leaderboard.json` but absent here still renders its decomposition and its four
version axes, with an explicit "trace not published" notice. It never invents
the missing sections.

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

### `benchmarks.json`

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

### `skills.json`

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

## Regenerating the data

`data/*.json` is generated from the registries by
`scripts/build_arena_data.py`, which is the **only writer**. The site never
writes. Regeneration is a build step, not a live update: a leaderboard should
show finished, frozen results, not in-progress runs.

After regenerating, mirror the placeholder set and re-check the script:

```sh
python3 scripts/embed_arena_data.py     # rewrites assets/fallback-data.js
node --check assets/app.js
```

## Offline and `file://`

Double-clicking `index.html` must work, and it does, but not through `fetch`:
`fetch()` of a `file://` URL is blocked as cross-origin by every current
browser. The load order in `app.js` is therefore:

1. `data/<name>.json` — the generated results, on a server or GitHub Pages.
2. `data/<name>.example.json` — the committed placeholders, when the generated
   file is absent.
3. `window.ARENA_FALLBACK[name]` from `assets/fallback-data.js` — a byte-for-byte
   mirror of the example JSON, loaded by a classic `<script>` tag, which
   `file://` does allow.

Step 3 is generated from step 2 and never edited by hand, so the two cannot
disagree except about their age. When any fallback is used, a banner appears at
the top of the page saying so: every row is a placeholder and every number is
synthetic, but the layout, the decomposition and the gate logic are the real
ones.

## Citing

Cite the Season and the four version axes, not this website. The axes are what
make a number re-derivable by someone who has none of your code. The citation
block, with a placeholder BibTeX entry, is on `benchmarks.html`.

## Accessibility notes

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
