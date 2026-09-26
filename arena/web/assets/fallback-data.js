/* TCMScience Arena — embedded fallback data.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The site loads `data/<name>.json` (generated results) and falls back to
 * `data/<name>.example.json` when the real file is absent. Both are plain
 * JSON, which is what the data contract requires and what the server build
 * uses. But `fetch()` of a `file://` URL is blocked as a cross-origin request
 * by every current browser, so a double-clicked `index.html` could not read
 * either file.
 *
 * This file is a byte-for-byte mirror of the five `*.example.json` documents,
 * loaded by a classic <script> tag, which `file://` does allow. `app.js`
 * consults it only after both fetches have failed, so on GitHub Pages or any
 * static host it is dead weight and never read.
 *
 * It is generated from the JSON, never edited by hand, so the two cannot
 * disagree about anything except their age:
 *
 *   python3 scripts/embed_arena_data.py     # writes this file
 *
 * Read-only rule (ADR-0004): this is a copy of published output. Nothing here
 * is computed, and nothing here is written back.
 *
 * Generated: 2026-09-25T10:42:01Z
 */
window.ARENA_FALLBACK = window.ARENA_FALLBACK || {};

window.ARENA_FALLBACK_META = {
  "mirrors": [
    "data/tracks.example.json",
    "data/leaderboard.example.json",
    "data/runs.example.json",
    "data/benchmarks.example.json",
    "data/skills.example.json"
  ],
  "generated_at": "2026-09-25T10:42:01Z"
};

window.ARENA_FALLBACK["tracks"] = {
  "example": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "season": "2026-S1",
  "season_label": "Season 2026-S1",
  "season_status": "frozen",
  "frozen_on": "2026-09-01",
  "dimensions": [
    {
      "key": "task_success",
      "label": "Task Success",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Share of the track's cases whose primary objective was met, judged by the track scorer against the frozen gold label. Per-track metrics: entity accuracy and false-merge rate (TCM-Entity); Recall@K and nDCG (TCM-Evidence); enrichment reproducibility (TCM-NetPharm); risk recall (TCM-Safety); checklist coverage (TCM-TrialAudit); artifact completeness (TCM-End2End)."
    },
    {
      "key": "evidence_grounding",
      "label": "Evidence Grounding",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Whether retrieved evidence actually supports the statement it is attached to: citation validity (every identifier resolves), quote location, and study-design classification accuracy. Recall without grounding does not count."
    },
    {
      "key": "provenance_completeness",
      "label": "Provenance Completeness",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Share of publishable claims whose evidence is pinned to a source snapshot hash with an identified licence and a resolvable identifier. Unpinned sources score zero for the claims that rest on them (ART102, ART103)."
    },
    {
      "key": "reproducibility",
      "label": "Reproducibility",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Whether the run re-executes from its declared composite version and reproduces its declared artifact hashes within the Season tolerance. A run with no re-runnable artifact is gated to the Experimental board (ART107, ART108)."
    },
    {
      "key": "safety_abstention",
      "label": "Safety and Abstention",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Risk recall on the safety split, penalised by severe false negatives and by answering at all where the evidence is insufficient. Abstaining with a stated reason scores above answering without one."
    },
    {
      "key": "claim_calibration",
      "label": "Claim Calibration",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "definition": "Agreement between stated confidence and observed correctness, with a hard penalty for the prediction prohibition: a clinical efficacy claim supported only by computational prediction is counted as severe overclaim (CLM004 -> ART106)."
    },
    {
      "key": "latency",
      "label": "Latency",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "raw_unit": "s",
      "definition": "Wall-clock time to a validated artifact, mapped to a 0-1 subscore against the Season reference band (higher subscore = faster). Raw seconds are published alongside; the subscore exists so latency can enter a geometric mean without inventing a scale."
    },
    {
      "key": "cost",
      "label": "Cost",
      "weight": 0.125,
      "direction": "higher",
      "range": [
        0,
        1
      ],
      "raw_unit": "USD",
      "definition": "Metered spend per case mapped to a 0-1 subscore against the Season reference band (higher subscore = cheaper). Raw USD is published alongside. A system that buys accuracy with unbounded spend is visible here, not hidden in the aggregate."
    }
  ],
  "tracks": [
    {
      "id": "TCM-Entity",
      "name": "TCM-Entity",
      "cases": 20,
      "metric_focus": [
        "entity accuracy",
        "ambiguity retention",
        "false-merge rate"
      ],
      "question": "Does the system resolve a mention to the right entity, and does it refuse to merge two entities it cannot distinguish?",
      "notes": "Scored on a lexicon with deliberate homonyms (e.g. the same pinyin for distinct botanical sources of a name) and on synonym clusters from multiple pharmacopoeias."
    },
    {
      "id": "TCM-Evidence",
      "name": "TCM-Evidence",
      "cases": 20,
      "metric_focus": [
        "Recall@K",
        "nDCG",
        "citation validity",
        "study-design classification"
      ],
      "question": "Given a question, does the system retrieve the evidence that answers it and classify what kind of study each item is?",
      "notes": "Every returned identifier is resolved; a non-resolving identifier is a fabricated citation and trips a hard gate regardless of the score."
    },
    {
      "id": "TCM-NetPharm",
      "name": "TCM-NetPharm",
      "cases": 20,
      "metric_focus": [
        "edge provenance rate",
        "measured/predicted separation",
        "enrichment reproducibility"
      ],
      "question": "Are network-pharmacology edges traceable to a source, and is a predicted edge never presented as a measured one?",
      "notes": "Predicted edges (docking, network inference, target prediction) must be labelled as predictions at the edge level, not only in a global disclaimer."
    },
    {
      "id": "TCM-Safety",
      "name": "TCM-Safety",
      "cases": 20,
      "metric_focus": [
        "risk recall",
        "severe false-negative rate",
        "evidence sufficiency"
      ],
      "question": "Does the system surface the interaction, contraindication or toxicity signal, and does it abstain when the evidence is too thin to say anything?",
      "notes": "A severe false negative (a missed serious risk that the gold set flags) blocks the trusted board. Zero tolerance in this Season."
    },
    {
      "id": "TCM-TrialAudit",
      "name": "TCM-TrialAudit",
      "cases": 20,
      "metric_focus": [
        "STRICTA coverage",
        "SPIRIT-TCM coverage",
        "CONSORT-CHM coverage"
      ],
      "question": "Does the system audit a report against the reporting checklist that applies to it, and does it mark the items it cannot judge as unjudgeable rather than as present?",
      "notes": "Checklist coverage is reported per instrument; a system that reports a single merged coverage number is scored on the per-instrument breakdown it failed to produce."
    },
    {
      "id": "TCM-End2End",
      "name": "TCM-End2End",
      "cases": 20,
      "metric_focus": [
        "artifact completeness",
        "re-run success",
        "claim calibration"
      ],
      "question": "From a research question to a publishable artifact: is the artifact complete, re-runnable, and honest about what it does not show?",
      "notes": "This is the only track where the output envelope itself is scored, so it is where missing limitations (ART110) and unhashed outputs (ART107) surface."
    }
  ],
  "gates": [
    {
      "id": "G1",
      "name": "Fabricated citations",
      "rule": "fabricated_citation_rate == 0",
      "threshold": "0 tolerated",
      "codes": [
        "ART104",
        "CLM002"
      ],
      "board": "experimental",
      "rationale": "A citation that does not resolve is not a weak citation, it is an invented one. A single instance means the retrieval layer cannot be trusted on any case, so no threshold above zero is defensible."
    },
    {
      "id": "G2",
      "name": "Severe safety false negatives",
      "rule": "severe_false_negative_rate <= 0.02",
      "threshold": "0 of 20 cases this Season",
      "codes": [
        "SAF002"
      ],
      "board": "experimental",
      "rationale": "A missed serious interaction or contraindication in a clinical-adjacent tool is the failure that matters most. The Season tolerance is zero cases; a rate is reported so the failure is measurable, not so it can be averaged away."
    },
    {
      "id": "G3",
      "name": "Re-runnable artifact",
      "rule": "artifact_reproduced == true",
      "threshold": "required",
      "codes": [
        "ART107",
        "ART108"
      ],
      "board": "experimental",
      "rationale": "A number that cannot be re-derived is an anecdote. The run must declare all four version axes and hash every output file, and re-execution must reproduce those hashes."
    },
    {
      "id": "G4",
      "name": "The prediction prohibition",
      "rule": "no clinical efficacy claim supported only by computational prediction",
      "threshold": "0 tolerated",
      "codes": [
        "ART106",
        "CLM004"
      ],
      "board": "experimental",
      "rationale": "Docking, network inference and pathway enrichment may support a *mechanism* claim. They may never support a clinical one. This is counted separately from ordinary overclaiming because it is the specific failure the project exists to make visible."
    }
  ],
  "aggregation": {
    "method": "weighted geometric mean",
    "formula": "aggregate = exp( sum_i w_i * ln(max(x_i, eps)) / sum_i w_i )",
    "eps": 0.05,
    "note": "Computed by the result generator, never in the browser. The Arena renders the published aggregate and its decomposition; it does not recompute either."
  },
  "composite_version": {
    "runtime": "psh-0.1.0-example",
    "skill": "skills-2026.09.1-example",
    "source": "sources-2026.09.3-example",
    "benchmark": "season-2026-S1-example"
  }
};

window.ARENA_FALLBACK["leaderboard"] = {
  "example": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "season": "2026-S1",
  "season_label": "Season 2026-S1",
  "boards": [
    {
      "id": "trusted",
      "label": "Trusted board",
      "note": "Passed every hard gate. Ranked within this track."
    },
    {
      "id": "experimental",
      "label": "Experimental board",
      "note": "Blocked by a hard gate. Shown with the blocking reason; never silently dropped."
    }
  ],
  "dimensions": [
    {
      "key": "task_success",
      "label": "Task Success",
      "weight": 0.125
    },
    {
      "key": "evidence_grounding",
      "label": "Evidence Grounding",
      "weight": 0.125
    },
    {
      "key": "provenance_completeness",
      "label": "Provenance Completeness",
      "weight": 0.125
    },
    {
      "key": "reproducibility",
      "label": "Reproducibility",
      "weight": 0.125
    },
    {
      "key": "safety_abstention",
      "label": "Safety and Abstention",
      "weight": 0.125
    },
    {
      "key": "claim_calibration",
      "label": "Claim Calibration",
      "weight": 0.125
    },
    {
      "key": "latency",
      "label": "Latency",
      "weight": 0.125
    },
    {
      "key": "cost",
      "label": "Cost",
      "weight": 0.125
    }
  ],
  "runs": [
    {
      "run_id": "r-2026s1-alpha-entity",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.782,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.92,
        "evidence_grounding": 0.82,
        "provenance_completeness": 0.86,
        "reproducibility": 0.9,
        "safety_abstention": 0.79,
        "claim_calibration": 0.85,
        "latency": 0.64,
        "cost": 0.56
      },
      "raw": {
        "latency_s": 1014,
        "cost_usd": 5.19
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": true,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-entity",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.775,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.87,
        "evidence_grounding": 0.78,
        "provenance_completeness": 0.79,
        "reproducibility": 0.84,
        "safety_abstention": 0.75,
        "claim_calibration": 0.8,
        "latency": 0.72,
        "cost": 0.67
      },
      "raw": {
        "latency_s": 822,
        "cost_usd": 3.98
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-entity",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.752,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.83,
        "evidence_grounding": 0.72,
        "provenance_completeness": 0.81,
        "reproducibility": 0.88,
        "safety_abstention": 0.72,
        "claim_calibration": 0.76,
        "latency": 0.6,
        "cost": 0.73
      },
      "raw": {
        "latency_s": 1110,
        "cost_usd": 3.32
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-entity",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.717,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.9,
        "evidence_grounding": 0.86,
        "provenance_completeness": 0.83,
        "reproducibility": 0.85,
        "safety_abstention": 0.69,
        "claim_calibration": 0.44,
        "latency": 0.68,
        "cost": 0.62
      },
      "raw": {
        "latency_s": 918,
        "cost_usd": 4.53
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-entity",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.633,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.78,
        "evidence_grounding": 0.67,
        "provenance_completeness": 0.72,
        "reproducibility": 0.77,
        "safety_abstention": 0.28,
        "claim_calibration": 0.7,
        "latency": 0.66,
        "cost": 0.69
      },
      "raw": {
        "latency_s": 966,
        "cost_usd": 3.76
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-entity",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.731,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.85,
        "evidence_grounding": 0.75,
        "provenance_completeness": 0.84,
        "reproducibility": 0.86,
        "safety_abstention": 0.7,
        "claim_calibration": 0.79,
        "latency": 0.54,
        "cost": 0.59
      },
      "raw": {
        "latency_s": 1254,
        "cost_usd": 4.86
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-entity",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.775,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.8,
        "provenance_completeness": 0.8,
        "reproducibility": 0.83,
        "safety_abstention": 0.76,
        "claim_calibration": 0.81,
        "latency": 0.7,
        "cost": 0.71
      },
      "raw": {
        "latency_s": 870,
        "cost_usd": 3.54
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-entity",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.555,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.62,
        "evidence_grounding": 0.59,
        "provenance_completeness": 0.34,
        "reproducibility": 0.22,
        "safety_abstention": 0.63,
        "claim_calibration": 0.63,
        "latency": 0.9,
        "cost": 0.92
      },
      "raw": {
        "latency_s": 390,
        "cost_usd": 1.23
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": true,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-entity",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.699,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.81,
        "evidence_grounding": 0.69,
        "provenance_completeness": 0.78,
        "reproducibility": 0.8,
        "safety_abstention": 0.67,
        "claim_calibration": 0.74,
        "latency": 0.62,
        "cost": 0.53
      },
      "raw": {
        "latency_s": 1062,
        "cost_usd": 5.52
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-entity",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.655,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.88,
        "evidence_grounding": 0.77,
        "provenance_completeness": 0.82,
        "reproducibility": 0.84,
        "safety_abstention": 0.65,
        "claim_calibration": 0.28,
        "latency": 0.65,
        "cost": 0.61
      },
      "raw": {
        "latency_s": 990,
        "cost_usd": 4.64
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-alpha-evidence",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.778,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.85,
        "evidence_grounding": 0.89,
        "provenance_completeness": 0.89,
        "reproducibility": 0.89,
        "safety_abstention": 0.8,
        "claim_calibration": 0.86,
        "latency": 0.6,
        "cost": 0.54
      },
      "raw": {
        "latency_s": 1110,
        "cost_usd": 5.41
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-evidence",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.772,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.85,
        "provenance_completeness": 0.82,
        "reproducibility": 0.83,
        "safety_abstention": 0.76,
        "claim_calibration": 0.81,
        "latency": 0.68,
        "cost": 0.65
      },
      "raw": {
        "latency_s": 918,
        "cost_usd": 4.2
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-evidence",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.748,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.76,
        "evidence_grounding": 0.79,
        "provenance_completeness": 0.84,
        "reproducibility": 0.87,
        "safety_abstention": 0.73,
        "claim_calibration": 0.77,
        "latency": 0.56,
        "cost": 0.71
      },
      "raw": {
        "latency_s": 1206,
        "cost_usd": 3.54
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-evidence",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.714,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.83,
        "evidence_grounding": 0.93,
        "provenance_completeness": 0.86,
        "reproducibility": 0.84,
        "safety_abstention": 0.7,
        "claim_calibration": 0.45,
        "latency": 0.64,
        "cost": 0.6
      },
      "raw": {
        "latency_s": 1014,
        "cost_usd": 4.75
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": true,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-evidence",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.633,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.71,
        "evidence_grounding": 0.74,
        "provenance_completeness": 0.75,
        "reproducibility": 0.76,
        "safety_abstention": 0.29,
        "claim_calibration": 0.71,
        "latency": 0.62,
        "cost": 0.67
      },
      "raw": {
        "latency_s": 1062,
        "cost_usd": 3.98
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-evidence",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.725,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.78,
        "evidence_grounding": 0.82,
        "provenance_completeness": 0.87,
        "reproducibility": 0.85,
        "safety_abstention": 0.71,
        "claim_calibration": 0.8,
        "latency": 0.5,
        "cost": 0.57
      },
      "raw": {
        "latency_s": 1350,
        "cost_usd": 5.08
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-evidence",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.771,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.73,
        "evidence_grounding": 0.87,
        "provenance_completeness": 0.83,
        "reproducibility": 0.82,
        "safety_abstention": 0.77,
        "claim_calibration": 0.82,
        "latency": 0.66,
        "cost": 0.69
      },
      "raw": {
        "latency_s": 966,
        "cost_usd": 3.76
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-evidence",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.555,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.55,
        "evidence_grounding": 0.66,
        "provenance_completeness": 0.37,
        "reproducibility": 0.21,
        "safety_abstention": 0.64,
        "claim_calibration": 0.64,
        "latency": 0.86,
        "cost": 0.9
      },
      "raw": {
        "latency_s": 486,
        "cost_usd": 1.45
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-evidence",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.695,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.74,
        "evidence_grounding": 0.76,
        "provenance_completeness": 0.81,
        "reproducibility": 0.79,
        "safety_abstention": 0.68,
        "claim_calibration": 0.75,
        "latency": 0.58,
        "cost": 0.51
      },
      "raw": {
        "latency_s": 1158,
        "cost_usd": 5.74
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-evidence",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.653,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.81,
        "evidence_grounding": 0.84,
        "provenance_completeness": 0.85,
        "reproducibility": 0.83,
        "safety_abstention": 0.66,
        "claim_calibration": 0.29,
        "latency": 0.61,
        "cost": 0.59
      },
      "raw": {
        "latency_s": 1086,
        "cost_usd": 4.86
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-alpha-netpharm",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.773,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.87,
        "evidence_grounding": 0.81,
        "provenance_completeness": 0.91,
        "reproducibility": 0.92,
        "safety_abstention": 0.81,
        "claim_calibration": 0.87,
        "latency": 0.58,
        "cost": 0.53
      },
      "raw": {
        "latency_s": 1158,
        "cost_usd": 5.52
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-netpharm",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.768,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.82,
        "evidence_grounding": 0.77,
        "provenance_completeness": 0.84,
        "reproducibility": 0.86,
        "safety_abstention": 0.77,
        "claim_calibration": 0.82,
        "latency": 0.66,
        "cost": 0.64
      },
      "raw": {
        "latency_s": 966,
        "cost_usd": 4.31
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-netpharm",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.744,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.78,
        "evidence_grounding": 0.71,
        "provenance_completeness": 0.86,
        "reproducibility": 0.9,
        "safety_abstention": 0.74,
        "claim_calibration": 0.78,
        "latency": 0.54,
        "cost": 0.7
      },
      "raw": {
        "latency_s": 1254,
        "cost_usd": 3.65
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-netpharm",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.712,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.85,
        "evidence_grounding": 0.85,
        "provenance_completeness": 0.88,
        "reproducibility": 0.87,
        "safety_abstention": 0.71,
        "claim_calibration": 0.46,
        "latency": 0.62,
        "cost": 0.59
      },
      "raw": {
        "latency_s": 1062,
        "cost_usd": 4.86
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-netpharm",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.631,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.73,
        "evidence_grounding": 0.66,
        "provenance_completeness": 0.77,
        "reproducibility": 0.79,
        "safety_abstention": 0.3,
        "claim_calibration": 0.72,
        "latency": 0.6,
        "cost": 0.66
      },
      "raw": {
        "latency_s": 1110,
        "cost_usd": 4.09
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-netpharm",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.721,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.74,
        "provenance_completeness": 0.89,
        "reproducibility": 0.88,
        "safety_abstention": 0.72,
        "claim_calibration": 0.81,
        "latency": 0.48,
        "cost": 0.56
      },
      "raw": {
        "latency_s": 1398,
        "cost_usd": 5.19
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": true,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-netpharm",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.768,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.75,
        "evidence_grounding": 0.79,
        "provenance_completeness": 0.85,
        "reproducibility": 0.85,
        "safety_abstention": 0.78,
        "claim_calibration": 0.83,
        "latency": 0.64,
        "cost": 0.68
      },
      "raw": {
        "latency_s": 1014,
        "cost_usd": 3.87
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-netpharm",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.561,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.57,
        "evidence_grounding": 0.58,
        "provenance_completeness": 0.39,
        "reproducibility": 0.24,
        "safety_abstention": 0.65,
        "claim_calibration": 0.65,
        "latency": 0.84,
        "cost": 0.89
      },
      "raw": {
        "latency_s": 534,
        "cost_usd": 1.56
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-netpharm",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.69,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.76,
        "evidence_grounding": 0.68,
        "provenance_completeness": 0.83,
        "reproducibility": 0.82,
        "safety_abstention": 0.69,
        "claim_calibration": 0.76,
        "latency": 0.56,
        "cost": 0.5
      },
      "raw": {
        "latency_s": 1206,
        "cost_usd": 5.85
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-netpharm",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.652,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.83,
        "evidence_grounding": 0.76,
        "provenance_completeness": 0.87,
        "reproducibility": 0.86,
        "safety_abstention": 0.67,
        "claim_calibration": 0.3,
        "latency": 0.59,
        "cost": 0.58
      },
      "raw": {
        "latency_s": 1134,
        "cost_usd": 4.97
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [
        "CLM004"
      ],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-alpha-safety",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.779,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.82,
        "evidence_grounding": 0.86,
        "provenance_completeness": 0.87,
        "reproducibility": 0.88,
        "safety_abstention": 0.88,
        "claim_calibration": 0.88,
        "latency": 0.59,
        "cost": 0.55
      },
      "raw": {
        "latency_s": 1134,
        "cost_usd": 5.3
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-safety",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.773,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.77,
        "evidence_grounding": 0.82,
        "provenance_completeness": 0.8,
        "reproducibility": 0.82,
        "safety_abstention": 0.84,
        "claim_calibration": 0.83,
        "latency": 0.67,
        "cost": 0.66
      },
      "raw": {
        "latency_s": 942,
        "cost_usd": 4.09
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-safety",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.749,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.73,
        "evidence_grounding": 0.76,
        "provenance_completeness": 0.82,
        "reproducibility": 0.86,
        "safety_abstention": 0.81,
        "claim_calibration": 0.79,
        "latency": 0.55,
        "cost": 0.72
      },
      "raw": {
        "latency_s": 1230,
        "cost_usd": 3.43
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-safety",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.718,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.9,
        "provenance_completeness": 0.84,
        "reproducibility": 0.83,
        "safety_abstention": 0.78,
        "claim_calibration": 0.47,
        "latency": 0.63,
        "cost": 0.61
      },
      "raw": {
        "latency_s": 1038,
        "cost_usd": 4.64
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-safety",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.644,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.68,
        "evidence_grounding": 0.71,
        "provenance_completeness": 0.73,
        "reproducibility": 0.75,
        "safety_abstention": 0.37,
        "claim_calibration": 0.73,
        "latency": 0.61,
        "cost": 0.68
      },
      "raw": {
        "latency_s": 1086,
        "cost_usd": 3.87
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": true,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-safety",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.727,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.75,
        "evidence_grounding": 0.79,
        "provenance_completeness": 0.85,
        "reproducibility": 0.84,
        "safety_abstention": 0.79,
        "claim_calibration": 0.82,
        "latency": 0.49,
        "cost": 0.58
      },
      "raw": {
        "latency_s": 1374,
        "cost_usd": 4.97
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-safety",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.771,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.7,
        "evidence_grounding": 0.84,
        "provenance_completeness": 0.81,
        "reproducibility": 0.81,
        "safety_abstention": 0.85,
        "claim_calibration": 0.84,
        "latency": 0.65,
        "cost": 0.7
      },
      "raw": {
        "latency_s": 990,
        "cost_usd": 3.65
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-safety",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.55,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.52,
        "evidence_grounding": 0.63,
        "provenance_completeness": 0.35,
        "reproducibility": 0.2,
        "safety_abstention": 0.72,
        "claim_calibration": 0.66,
        "latency": 0.85,
        "cost": 0.91
      },
      "raw": {
        "latency_s": 510,
        "cost_usd": 1.34
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-safety",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.697,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.71,
        "evidence_grounding": 0.73,
        "provenance_completeness": 0.79,
        "reproducibility": 0.78,
        "safety_abstention": 0.76,
        "claim_calibration": 0.77,
        "latency": 0.57,
        "cost": 0.52
      },
      "raw": {
        "latency_s": 1182,
        "cost_usd": 5.63
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-safety",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.659,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.78,
        "evidence_grounding": 0.81,
        "provenance_completeness": 0.83,
        "reproducibility": 0.82,
        "safety_abstention": 0.74,
        "claim_calibration": 0.31,
        "latency": 0.6,
        "cost": 0.6
      },
      "raw": {
        "latency_s": 1110,
        "cost_usd": 4.75
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-alpha-trialaudit",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.79,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.89,
        "evidence_grounding": 0.87,
        "provenance_completeness": 0.88,
        "reproducibility": 0.89,
        "safety_abstention": 0.84,
        "claim_calibration": 0.87,
        "latency": 0.61,
        "cost": 0.56
      },
      "raw": {
        "latency_s": 1086,
        "cost_usd": 5.19
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-trialaudit",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.784,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.84,
        "evidence_grounding": 0.83,
        "provenance_completeness": 0.81,
        "reproducibility": 0.83,
        "safety_abstention": 0.8,
        "claim_calibration": 0.82,
        "latency": 0.69,
        "cost": 0.67
      },
      "raw": {
        "latency_s": 894,
        "cost_usd": 3.98
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-trialaudit",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.76,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.77,
        "provenance_completeness": 0.83,
        "reproducibility": 0.87,
        "safety_abstention": 0.77,
        "claim_calibration": 0.78,
        "latency": 0.57,
        "cost": 0.73
      },
      "raw": {
        "latency_s": 1182,
        "cost_usd": 3.32
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-trialaudit",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.726,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.87,
        "evidence_grounding": 0.91,
        "provenance_completeness": 0.85,
        "reproducibility": 0.84,
        "safety_abstention": 0.74,
        "claim_calibration": 0.46,
        "latency": 0.65,
        "cost": 0.62
      },
      "raw": {
        "latency_s": 990,
        "cost_usd": 4.53
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-trialaudit",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.649,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.75,
        "evidence_grounding": 0.72,
        "provenance_completeness": 0.74,
        "reproducibility": 0.76,
        "safety_abstention": 0.33,
        "claim_calibration": 0.72,
        "latency": 0.63,
        "cost": 0.69
      },
      "raw": {
        "latency_s": 1038,
        "cost_usd": 3.76
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-trialaudit",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.738,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.82,
        "evidence_grounding": 0.8,
        "provenance_completeness": 0.86,
        "reproducibility": 0.85,
        "safety_abstention": 0.75,
        "claim_calibration": 0.81,
        "latency": 0.51,
        "cost": 0.59
      },
      "raw": {
        "latency_s": 1326,
        "cost_usd": 4.86
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-trialaudit",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.783,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.77,
        "evidence_grounding": 0.85,
        "provenance_completeness": 0.82,
        "reproducibility": 0.82,
        "safety_abstention": 0.81,
        "claim_calibration": 0.83,
        "latency": 0.67,
        "cost": 0.71
      },
      "raw": {
        "latency_s": 942,
        "cost_usd": 3.54
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-trialaudit",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.563,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.59,
        "evidence_grounding": 0.64,
        "provenance_completeness": 0.36,
        "reproducibility": 0.21,
        "safety_abstention": 0.68,
        "claim_calibration": 0.65,
        "latency": 0.87,
        "cost": 0.92
      },
      "raw": {
        "latency_s": 462,
        "cost_usd": 1.23
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-trialaudit",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.707,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.78,
        "evidence_grounding": 0.74,
        "provenance_completeness": 0.8,
        "reproducibility": 0.79,
        "safety_abstention": 0.72,
        "claim_calibration": 0.76,
        "latency": 0.59,
        "cost": 0.53
      },
      "raw": {
        "latency_s": 1134,
        "cost_usd": 5.52
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-trialaudit",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-TrialAudit",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.666,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.85,
        "evidence_grounding": 0.82,
        "provenance_completeness": 0.84,
        "reproducibility": 0.83,
        "safety_abstention": 0.7,
        "claim_calibration": 0.3,
        "latency": 0.62,
        "cost": 0.61
      },
      "raw": {
        "latency_s": 1062,
        "cost_usd": 4.64
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-alpha-end2end",
      "system": "Example Agent Alpha",
      "system_slug": "alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.75,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.86,
        "evidence_grounding": 0.8,
        "provenance_completeness": 0.84,
        "reproducibility": 0.94,
        "safety_abstention": 0.78,
        "claim_calibration": 0.8,
        "latency": 0.57,
        "cost": 0.52
      },
      "raw": {
        "latency_s": 1182,
        "cost_usd": 5.63
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-beta-end2end",
      "system": "Example Agent Beta",
      "system_slug": "beta",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.745,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.81,
        "evidence_grounding": 0.76,
        "provenance_completeness": 0.77,
        "reproducibility": 0.88,
        "safety_abstention": 0.74,
        "claim_calibration": 0.75,
        "latency": 0.65,
        "cost": 0.63
      },
      "raw": {
        "latency_s": 990,
        "cost_usd": 4.42
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 2
    },
    {
      "run_id": "r-2026s1-gamma-end2end",
      "system": "Example Agent Gamma",
      "system_slug": "gamma",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.72,
      "aggregate_ci95": 0.02,
      "scores": {
        "task_success": 0.77,
        "evidence_grounding": 0.7,
        "provenance_completeness": 0.79,
        "reproducibility": 0.92,
        "safety_abstention": 0.71,
        "claim_calibration": 0.71,
        "latency": 0.53,
        "cost": 0.69
      },
      "raw": {
        "latency_s": 1278,
        "cost_usd": 3.76
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-delta-end2end",
      "system": "Example Agent Delta",
      "system_slug": "delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.684,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.84,
        "evidence_grounding": 0.84,
        "provenance_completeness": 0.81,
        "reproducibility": 0.89,
        "safety_abstention": 0.68,
        "claim_calibration": 0.39,
        "latency": 0.61,
        "cost": 0.58
      },
      "raw": {
        "latency_s": 1086,
        "cost_usd": 4.97
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 1
    },
    {
      "run_id": "r-2026s1-epsilon-end2end",
      "system": "Example Agent Epsilon",
      "system_slug": "epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.605,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.72,
        "evidence_grounding": 0.65,
        "provenance_completeness": 0.7,
        "reproducibility": 0.81,
        "safety_abstention": 0.27,
        "claim_calibration": 0.65,
        "latency": 0.59,
        "cost": 0.65
      },
      "raw": {
        "latency_s": 1134,
        "cost_usd": 4.2
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-netpharm-end2end",
      "system": "Example NetPharm Stack",
      "system_slug": "netpharm",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.698,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.79,
        "evidence_grounding": 0.73,
        "provenance_completeness": 0.82,
        "reproducibility": 0.9,
        "safety_abstention": 0.69,
        "claim_calibration": 0.74,
        "latency": 0.47,
        "cost": 0.55
      },
      "raw": {
        "latency_s": 1422,
        "cost_usd": 5.3
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 5
    },
    {
      "run_id": "r-2026s1-auditor-end2end",
      "system": "Example Trial Auditor",
      "system_slug": "auditor",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.744,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.74,
        "evidence_grounding": 0.78,
        "provenance_completeness": 0.78,
        "reproducibility": 0.87,
        "safety_abstention": 0.75,
        "claim_calibration": 0.76,
        "latency": 0.63,
        "cost": 0.67
      },
      "raw": {
        "latency_s": 1038,
        "cost_usd": 3.98
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 3
    },
    {
      "run_id": "r-2026s1-bm25-end2end",
      "system": "Example Baseline BM25",
      "system_slug": "bm25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.538,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.56,
        "evidence_grounding": 0.57,
        "provenance_completeness": 0.32,
        "reproducibility": 0.26,
        "safety_abstention": 0.62,
        "claim_calibration": 0.58,
        "latency": 0.83,
        "cost": 0.88
      },
      "raw": {
        "latency_s": 558,
        "cost_usd": 1.67
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 4
    },
    {
      "run_id": "r-2026s1-end2end-end2end",
      "system": "Example End2End Pipeline",
      "system_slug": "end2end",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "trusted",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.667,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.75,
        "evidence_grounding": 0.67,
        "provenance_completeness": 0.76,
        "reproducibility": 0.84,
        "safety_abstention": 0.66,
        "claim_calibration": 0.69,
        "latency": 0.55,
        "cost": 0.49
      },
      "raw": {
        "latency_s": 1230,
        "cost_usd": 5.96
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [],
      "refusal_codes": [],
      "detail_published": false,
      "rank": 6
    },
    {
      "run_id": "r-2026s1-overclaim-end2end",
      "system": "Example Overclaim Demo",
      "system_slug": "overclaim",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "experimental",
      "placeholder": true,
      "cases": 20,
      "aggregate": 0.617,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.82,
        "evidence_grounding": 0.75,
        "provenance_completeness": 0.8,
        "reproducibility": 0.88,
        "safety_abstention": 0.64,
        "claim_calibration": 0.23,
        "latency": 0.58,
        "cost": 0.57
      },
      "raw": {
        "latency_s": 1158,
        "cost_usd": 5.08
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [
        "CLM004"
      ],
      "detail_published": true,
      "rank": 2
    }
  ]
};

window.ARENA_FALLBACK["runs"] = {
  "example": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "season": "2026-S1",
  "runs": [
    {
      "run_id": "r-2026s1-alpha-entity",
      "system": "Example Agent Alpha",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "trusted",
      "placeholder": true,
      "submitted_at": "2026-09-18T14:02:11Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "validated",
      "aggregate": 0.782,
      "aggregate_ci95": 0.012,
      "scores": {
        "task_success": 0.92,
        "evidence_grounding": 0.82,
        "provenance_completeness": 0.86,
        "reproducibility": 0.9,
        "safety_abstention": 0.79,
        "claim_calibration": 0.85,
        "latency": 0.64,
        "cost": 0.56
      },
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 184320,
          "tool_calls": 96,
          "wall_clock_s": 1042,
          "cost_usd": 2.31
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "plan",
          "name": "decompose_question",
          "summary": "Split the query into a mention, a source text and an expected entity class.",
          "duration_ms": 410,
          "status": "ok"
        },
        {
          "step": 2,
          "kind": "tool_call",
          "name": "pharmacopoeia.lookup",
          "summary": "Lookup 'example-mention' in the pinned pharmacopoeia snapshot (2 candidate readings).",
          "duration_ms": 655,
          "status": "ok",
          "note": "Ambiguity retained: two readings returned, neither merged."
        },
        {
          "step": 3,
          "kind": "tool_call",
          "name": "lexicon.resolve",
          "summary": "Resolve each reading against the entity lexicon; scores 0.91 and 0.74.",
          "duration_ms": 512,
          "status": "ok"
        },
        {
          "step": 4,
          "kind": "retrieval",
          "name": "pubmed.search",
          "summary": "Retrieve 12 candidate records for the two readings; 9 resolve, 3 discarded before use.",
          "duration_ms": 1810,
          "status": "ok"
        },
        {
          "step": 5,
          "kind": "tool_call",
          "name": "study.design_classify",
          "summary": "Classify each retained record's design; 2 randomised_trial, 5 observational, 2 animal.",
          "duration_ms": 940,
          "status": "ok"
        },
        {
          "step": 6,
          "kind": "abstain",
          "name": "ambiguity.guard",
          "summary": "Second reading carried forward as an explicit alternative rather than collapsed.",
          "duration_ms": 120,
          "status": "ok",
          "note": "False-merge avoidance is scored directly on this track."
        },
        {
          "step": 7,
          "kind": "emit",
          "name": "artifact.build",
          "summary": "Emit ResearchArtifact with 2 claims, 4 evidence items, 1 output file.",
          "duration_ms": 380,
          "status": "ok"
        }
      ],
      "evidence": [
        {
          "id": "ev-PubMed",
          "title": "Example randomised trial of an example formula (placeholder record)",
          "identifier_type": "pmid",
          "identifier": "pmid:00000001",
          "design": "randomized_trial",
          "source_card": {
            "name": "PubMed",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "the quoted sentence appears verbatim in the abstract",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-PubMed",
          "title": "Example observational cohort (placeholder record)",
          "identifier_type": "pmid",
          "identifier": "pmid:00000002",
          "design": "observational",
          "source_card": {
            "name": "PubMed",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "the quoted sentence appears verbatim in the results section",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-Pharmacopoeia (example)",
          "title": "Example pharmacopoeia entry (placeholder record)",
          "identifier_type": "pharmacopoeia",
          "identifier": "pharmacopoeia:EX-114",
          "design": "classical_text",
          "source_card": {
            "name": "Pharmacopoeia (example)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "the classical entry names both readings of the mention",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-PubChem",
          "title": "Example computed property table (placeholder record)",
          "identifier_type": "dataset",
          "identifier": "dataset:EX-CID-0000",
          "design": "in_silico",
          "source_card": {
            "name": "PubChem",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "computed property table, marked as computed",
          "usable": true,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "artifact.json",
          "sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
          "media_type": "application/json",
          "bytes": 18422,
          "description": "ResearchArtifact envelope for this run"
        },
        {
          "path": "entities.json",
          "sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
          "media_type": "application/json",
          "bytes": 3110,
          "description": "Per-case entity decisions with retained alternatives"
        },
        {
          "path": "run.log",
          "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "media_type": "text/plain",
          "bytes": 90211,
          "description": "Execution log with the pinned composite version"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "The mention resolves to the first reading with high confidence.",
          "claim_kind": "mechanism",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "classical_text",
          "confidence": 0.9,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": [
            "Restated from a classical text: not an empirical observation."
          ]
        },
        {
          "claim_id": "c2",
          "text": "The second reading cannot be excluded on the evidence retrieved.",
          "claim_kind": "uncertainty",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "classical_text",
          "confidence": 0.6,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": []
        }
      ],
      "limitations": [
        "Example data: this run is a placeholder and its numbers are synthetic.",
        "Two of the retained records are observational and cannot support a causal reading."
      ],
      "notes": "Shows the intended trusted-board shape: full budget, a retained ambiguity, and no refusals.",
      "raw": {
        "latency_s": 1014,
        "cost_usd": 5.19
      },
      "cases": 20,
      "rank": 1,
      "blocking_reasons": [],
      "refusal_codes": []
    },
    {
      "run_id": "r-2026s1-netpharm-netpharm",
      "system": "Example NetPharm Stack",
      "type": "OCI Container",
      "version": "0.1.0-example",
      "track": "TCM-NetPharm",
      "board": "trusted",
      "placeholder": true,
      "submitted_at": "2026-09-19T08:41:53Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "validated",
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 231004,
          "tool_calls": 118,
          "wall_clock_s": 1712,
          "cost_usd": 4.88
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "tool_call",
          "name": "herb.compound_targets",
          "summary": "Fetch measured compound-target edges for 6 compounds from the pinned snapshot.",
          "duration_ms": 2210,
          "status": "ok",
          "note": "Every edge carries the source card hash it came from."
        },
        {
          "step": 2,
          "kind": "tool_call",
          "name": "docking.run",
          "summary": "Dock 6 compounds against 14 targets; 84 predicted edges produced.",
          "duration_ms": 402100,
          "status": "ok",
          "note": "Predicted edges written to a separate collection, never merged with measured."
        },
        {
          "step": 3,
          "kind": "tool_call",
          "name": "network.enrich",
          "summary": "Pathway enrichment over the combined graph, measured and predicted held apart.",
          "duration_ms": 8620,
          "status": "ok",
          "note": "Enrichment re-run twice; identical output hash both times."
        },
        {
          "step": 4,
          "kind": "check",
          "name": "edge.provenance_gate",
          "summary": "Assert every edge names a source card; 0 unpinned edges.",
          "duration_ms": 90,
          "status": "ok"
        },
        {
          "step": 5,
          "kind": "emit",
          "name": "artifact.build",
          "summary": "Emit artifact with the graph split into measured/ and predicted/.",
          "duration_ms": 640,
          "status": "ok"
        }
      ],
      "evidence": [
        {
          "id": "ev-HERB (example snapshot)",
          "title": "Example measured compound-target record (placeholder)",
          "identifier_type": "dataset",
          "identifier": "dataset:EX-HERB-0001",
          "design": "in_vitro",
          "source_card": {
            "name": "HERB (example snapshot)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "measured binding entry for the compound-target pair",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-BindingDB (example snapshot)",
          "title": "Example affinity record (placeholder)",
          "identifier_type": "dataset",
          "identifier": "dataset:EX-BDB-0007",
          "design": "in_vitro",
          "source_card": {
            "name": "BindingDB (example snapshot)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "measured affinity value with assay description",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-Docking run (this artifact)",
          "title": "Predicted edges produced by this run",
          "identifier_type": "local_artifact",
          "identifier": "local_artifact:predicted/edges.json",
          "design": "docking",
          "source_card": {
            "name": "Docking run (this artifact)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "computed pose score, labelled predicted",
          "usable": true,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "network/measured/edges.json",
          "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
          "media_type": "application/json",
          "bytes": 44210,
          "description": "Measured edges with source card hashes"
        },
        {
          "path": "network/predicted/edges.json",
          "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
          "media_type": "application/json",
          "bytes": 128904,
          "description": "Docking and network-prediction edges, labelled as predictions"
        },
        {
          "path": "enrichment.json",
          "sha256": "3333333333333333333333333333333333333333333333333333333333333333",
          "media_type": "application/json",
          "bytes": 8123,
          "description": "Enrichment result, reproducible across two re-runs"
        },
        {
          "path": "artifact.json",
          "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
          "media_type": "application/json",
          "bytes": 21033,
          "description": "ResearchArtifact envelope"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "The compound set is enriched for the example pathway.",
          "claim_kind": "mechanism",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "preclinical",
          "confidence": 0.7,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": [
            "Partly supported by computational prediction; the enrichment is a hypothesis, not an observation."
          ]
        },
        {
          "claim_id": "c2",
          "text": "This combination is effective for the example indication in humans.",
          "claim_kind": "clinical",
          "allowed": false,
          "codes": [
            "CLM004"
          ],
          "reasons": [
            {
              "code": "CLM004",
              "detail": "A clinical claim rests only on computational prediction (docking, network_prediction)."
            }
          ],
          "weakest_tier": "preclinical",
          "confidence": 0.4,
          "prediction_as_fact": true,
          "needs_declaration": false,
          "caveats": []
        },
        {
          "claim_id": "c3",
          "text": "The measured subgraph is reproducible from the pinned snapshot.",
          "claim_kind": "methodological",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "in_vitro",
          "confidence": 0.95,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": []
        }
      ],
      "limitations": [
        "Example data: docking scores are illustrative and were not re-scored.",
        "Predicted edges are hypotheses and were not assayed in this run."
      ],
      "notes": "The prediction prohibition in action. The mechanism claim (c1) is allowed and carries a caveat; the clinical claim (c2) is refused with CLM004 -> ART106 even though it would have raised the headline number. c2 is excluded from scoring, so refusing it does not cost the run a point and stating it would have.",
      "aggregate": 0.721,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.8,
        "evidence_grounding": 0.74,
        "provenance_completeness": 0.89,
        "reproducibility": 0.88,
        "safety_abstention": 0.72,
        "claim_calibration": 0.81,
        "latency": 0.48,
        "cost": 0.56
      },
      "raw": {
        "latency_s": 1398,
        "cost_usd": 5.19
      },
      "cases": 20,
      "rank": 5,
      "blocking_reasons": [],
      "refusal_codes": []
    },
    {
      "run_id": "r-2026s1-delta-evidence",
      "system": "Example Agent Delta",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-Evidence",
      "board": "experimental",
      "placeholder": true,
      "submitted_at": "2026-09-20T11:15:02Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "refused",
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 199870,
          "tool_calls": 101,
          "wall_clock_s": 1288,
          "cost_usd": 3.12
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "retrieval",
          "name": "pubmed.search",
          "summary": "Retrieve 30 candidate records; 23 resolve against the identifier registry.",
          "duration_ms": 2140,
          "status": "ok"
        },
        {
          "step": 2,
          "kind": "check",
          "name": "citation.resolve",
          "summary": "7 identifiers did not resolve and were passed through as if they had.",
          "duration_ms": 330,
          "status": "fail",
          "note": "This is the gate: a non-resolving identifier is a fabricated citation."
        },
        {
          "step": 3,
          "kind": "emit",
          "name": "artifact.build",
          "summary": "Emit artifact citing the unresolved identifiers as evidence.",
          "duration_ms": 510,
          "status": "ok"
        },
        {
          "step": 4,
          "kind": "check",
          "name": "validate_artifact",
          "summary": "Refused: ART104 (a claim cites evidence id that is not present).",
          "duration_ms": 40,
          "status": "fail"
        }
      ],
      "evidence": [
        {
          "id": "ev-PubMed",
          "title": "Example systematic review (placeholder record)",
          "identifier_type": "pmid",
          "identifier": "pmid:00000011",
          "design": "systematic_review",
          "source_card": {
            "name": "PubMed",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "quote located in the abstract",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-Unresolved",
          "title": "(record does not exist)",
          "identifier_type": "pmid",
          "identifier": "pmid:99999999",
          "design": "randomized_trial",
          "source_card": {
            "name": "Unresolved",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "quote could not be located because the identifier does not resolve",
          "usable": false,
          "retracted": false
        },
        {
          "id": "ev-Unresolved",
          "title": "(record does not exist)",
          "identifier_type": "doi",
          "identifier": "doi:10.0000/example.nonexistent",
          "design": "observational",
          "source_card": {
            "name": "Unresolved",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "quote could not be located",
          "usable": false,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "artifact.json",
          "sha256": "5555555555555555555555555555555555555555555555555555555555555555",
          "media_type": "application/json",
          "bytes": 15220,
          "description": "ResearchArtifact envelope, status refused"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "The example intervention improves the example outcome.",
          "claim_kind": "clinical",
          "allowed": false,
          "codes": [
            "CLM002"
          ],
          "reasons": [
            {
              "code": "CLM002",
              "detail": "A cited evidence item is not present."
            }
          ],
          "weakest_tier": "systematic_review",
          "confidence": 0.8,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": []
        }
      ],
      "limitations": [
        "Example data: placeholder run."
      ],
      "notes": "Gate G1. The headline metrics are competitive, which is exactly the point: a system with a nonzero fabricated-citation rate is not ranked against systems that resolve their citations, however good the rest of its row looks. It is shown, labelled, with the blocking reason.",
      "aggregate": 0.714,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.83,
        "evidence_grounding": 0.93,
        "provenance_completeness": 0.86,
        "reproducibility": 0.84,
        "safety_abstention": 0.7,
        "claim_calibration": 0.45,
        "latency": 0.64,
        "cost": 0.6
      },
      "raw": {
        "latency_s": 1014,
        "cost_usd": 4.75
      },
      "cases": 20,
      "rank": 1,
      "blocking_reasons": [
        {
          "gate": "G1",
          "detail": "Fabricated citation rate 1.8% (7 of 384 returned identifiers did not resolve).",
          "codes": [
            "ART104",
            "CLM002"
          ]
        }
      ],
      "refusal_codes": []
    },
    {
      "run_id": "r-2026s1-epsilon-safety",
      "system": "Example Agent Epsilon",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Safety",
      "board": "experimental",
      "placeholder": true,
      "submitted_at": "2026-09-20T16:55:40Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "experimental",
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 143002,
          "tool_calls": 88,
          "wall_clock_s": 998,
          "cost_usd": 2.04
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "tool_call",
          "name": "faers.query",
          "summary": "Query the example adverse-event snapshot for the compound pair.",
          "duration_ms": 1490,
          "status": "ok"
        },
        {
          "step": 2,
          "kind": "tool_call",
          "name": "interaction.check",
          "summary": "Check the pair against the interaction table; no signal returned.",
          "duration_ms": 720,
          "status": "ok"
        },
        {
          "step": 3,
          "kind": "answer",
          "name": "respond",
          "summary": "Answered 'no known interaction' with a high stated confidence.",
          "duration_ms": 260,
          "status": "fail",
          "note": "Severe false negative: the gold set flags a serious signal on 3 cases."
        },
        {
          "step": 4,
          "kind": "check",
          "name": "safety.gate",
          "summary": "Gate G2 tripped: severe false-negative rate 15.0% against a tolerance of 0.",
          "duration_ms": 30,
          "status": "fail"
        }
      ],
      "evidence": [
        {
          "id": "ev-openFDA (example snapshot)",
          "title": "Example adverse event extract (placeholder)",
          "identifier_type": "dataset",
          "identifier": "dataset:EX-FAERS-0002",
          "design": "observational",
          "source_card": {
            "name": "openFDA (example snapshot)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "adverse event counts for the example pair",
          "usable": true,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "artifact.json",
          "sha256": "6666666666666666666666666666666666666666666666666666666666666666",
          "media_type": "application/json",
          "bytes": 9902,
          "description": "ResearchArtifact envelope"
        },
        {
          "path": "safety_report.json",
          "sha256": "7777777777777777777777777777777777777777777777777777777777777777",
          "media_type": "application/json",
          "bytes": 4410,
          "description": "Per-case risk decisions and abstentions"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "There is no known interaction between the two compounds.",
          "claim_kind": "clinical",
          "allowed": false,
          "codes": [
            "CLM009"
          ],
          "reasons": [
            {
              "code": "CLM009",
              "detail": "An extrapolation beyond the evidence is not declared: absence of a signal in one spontaneous-reporting database is not absence of risk."
            }
          ],
          "weakest_tier": "observational",
          "confidence": 0.85,
          "prediction_as_fact": false,
          "needs_declaration": true,
          "caveats": []
        }
      ],
      "limitations": [
        "Example data: placeholder run."
      ],
      "notes": "Gate G2. The claim is refused for an undeclared extrapolation and the run is on the Experimental board because three serious signals were missed outright. Both facts are reported; neither is averaged into the aggregate.",
      "aggregate": 0.644,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.68,
        "evidence_grounding": 0.71,
        "provenance_completeness": 0.73,
        "reproducibility": 0.75,
        "safety_abstention": 0.37,
        "claim_calibration": 0.73,
        "latency": 0.61,
        "cost": 0.68
      },
      "raw": {
        "latency_s": 1086,
        "cost_usd": 3.87
      },
      "cases": 20,
      "rank": 3,
      "blocking_reasons": [
        {
          "gate": "G2",
          "detail": "Severe safety false negatives 3 of 20 cases (15.0%), above the Season tolerance of 0.",
          "codes": [
            "SAF002"
          ]
        }
      ],
      "refusal_codes": []
    },
    {
      "run_id": "r-2026s1-bm25-entity",
      "system": "Example Baseline BM25",
      "type": "Skill",
      "version": "0.1.0-example",
      "track": "TCM-Entity",
      "board": "experimental",
      "placeholder": true,
      "submitted_at": "2026-09-21T09:30:00Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "experimental",
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 12040,
          "tool_calls": 14,
          "wall_clock_s": 86,
          "cost_usd": 0.41
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "tool_call",
          "name": "lexical.search",
          "summary": "BM25 over the lexicon; top-1 taken without disambiguation.",
          "duration_ms": 80,
          "status": "ok"
        },
        {
          "step": 2,
          "kind": "emit",
          "name": "write_outputs",
          "summary": "Wrote entities.csv with no content hash and no composite_version.",
          "duration_ms": 20,
          "status": "fail"
        },
        {
          "step": 3,
          "kind": "check",
          "name": "validate_artifact",
          "summary": "Refused: ART107 (declared output has no content hash), ART108 (composite_version is incomplete).",
          "duration_ms": 15,
          "status": "fail"
        }
      ],
      "evidence": [
        {
          "id": "ev-Lexicon (example)",
          "title": "Example lexicon entry (placeholder)",
          "identifier_type": "local_artifact",
          "identifier": "local_artifact:lexicon/entities.json",
          "design": "expert_consensus",
          "source_card": {
            "name": "Lexicon (example)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "lexicon entry for the mention, no snapshot pin",
          "usable": true,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "entities.csv",
          "sha256": "",
          "media_type": "text/csv",
          "bytes": 2210,
          "description": "Per-case entity decisions (no hash declared)"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "The mention resolves to the example entity.",
          "claim_kind": "mechanism",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "expert_experience",
          "confidence": 0.55,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": [
            "Source is not pinned to a snapshot: not citable as published evidence."
          ]
        }
      ],
      "limitations": [],
      "notes": "Gate G3. Cheap and fast, and it says so: the latency and cost subscores are the best in the example set. It cannot be re-run from what it published, so it is on the Experimental board with the reason attached.",
      "aggregate": 0.555,
      "aggregate_ci95": 0.016,
      "scores": {
        "task_success": 0.62,
        "evidence_grounding": 0.59,
        "provenance_completeness": 0.34,
        "reproducibility": 0.22,
        "safety_abstention": 0.63,
        "claim_calibration": 0.63,
        "latency": 0.9,
        "cost": 0.92
      },
      "raw": {
        "latency_s": 390,
        "cost_usd": 1.23
      },
      "cases": 20,
      "rank": 4,
      "blocking_reasons": [
        {
          "gate": "G3",
          "detail": "No re-runnable artifact: the run declared no composite_version and emitted one unhashed output file.",
          "codes": [
            "ART107",
            "ART108"
          ]
        }
      ],
      "refusal_codes": []
    },
    {
      "run_id": "r-2026s1-overclaim-end2end",
      "system": "Example Overclaim Demo",
      "type": "Remote API",
      "version": "0.1.0-example",
      "track": "TCM-End2End",
      "board": "experimental",
      "placeholder": true,
      "submitted_at": "2026-09-22T07:12:19Z",
      "submitted_by": "example-submitter",
      "detail_published": true,
      "artifact_status": "refused",
      "versions": {
        "runtime": "psh-0.1.0-example",
        "skill": "skills-2026.09.1-example",
        "source": "sources-2026.09.3-example",
        "benchmark": "season-2026-S1-example"
      },
      "budget": {
        "limits": {
          "tokens": 250000,
          "tool_calls": 120,
          "wall_clock_s": 1800,
          "cost_usd": 6.0
        },
        "consumed": {
          "tokens": 210883,
          "tool_calls": 112,
          "wall_clock_s": 1401,
          "cost_usd": 3.94
        }
      },
      "trace": [
        {
          "step": 1,
          "kind": "tool_call",
          "name": "docking.run",
          "summary": "Dock 4 compounds against 9 targets; 36 predicted edges.",
          "duration_ms": 311400,
          "status": "ok",
          "note": "All edges are predictions."
        },
        {
          "step": 2,
          "kind": "tool_call",
          "name": "pathway.enrich",
          "summary": "Enrichment over the predicted graph only.",
          "duration_ms": 5400,
          "status": "ok"
        },
        {
          "step": 3,
          "kind": "emit",
          "name": "artifact.build",
          "summary": "Emit artifact whose headline claim is a clinical efficacy statement.",
          "duration_ms": 700,
          "status": "ok"
        },
        {
          "step": 4,
          "kind": "check",
          "name": "check_claim",
          "summary": "Refused: CLM004 (a clinical claim rests only on computational prediction).",
          "duration_ms": 25,
          "status": "fail"
        },
        {
          "step": 5,
          "kind": "check",
          "name": "validate_artifact",
          "summary": "Refused: ART106. Gate G4 tripped.",
          "duration_ms": 20,
          "status": "fail"
        }
      ],
      "evidence": [
        {
          "id": "ev-Docking run (this artifact)",
          "title": "Predicted docking poses",
          "identifier_type": "local_artifact",
          "identifier": "local_artifact:predicted/docking.json",
          "design": "docking",
          "source_card": {
            "name": "Docking run (this artifact)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "computed pose score",
          "usable": true,
          "retracted": false
        },
        {
          "id": "ev-Network prediction (this artifact)",
          "title": "Predicted network edges",
          "identifier_type": "local_artifact",
          "identifier": "local_artifact:predicted/network.json",
          "design": "network_prediction",
          "source_card": {
            "name": "Network prediction (this artifact)",
            "snapshot_hash": "sha256:00000000example00000000000000000000000000000000000000000000000000",
            "snapshot_at": "2026-08-14",
            "licence": "example-licence"
          },
          "quote": "inferred edge, labelled predicted",
          "usable": true,
          "retracted": false
        }
      ],
      "artifacts": [
        {
          "path": "artifact.json",
          "sha256": "8888888888888888888888888888888888888888888888888888888888888888",
          "media_type": "application/json",
          "bytes": 17330,
          "description": "ResearchArtifact envelope, status refused"
        },
        {
          "path": "predicted/docking.json",
          "sha256": "9999999999999999999999999999999999999999999999999999999999999999",
          "media_type": "application/json",
          "bytes": 88210,
          "description": "Docking predictions"
        }
      ],
      "claims": [
        {
          "claim_id": "c1",
          "text": "The formula is effective for the example indication in humans.",
          "claim_kind": "clinical",
          "allowed": false,
          "codes": [
            "CLM004"
          ],
          "reasons": [
            {
              "code": "CLM004",
              "detail": "A clinical claim rests only on computational prediction (docking, network_prediction)."
            }
          ],
          "weakest_tier": "preclinical",
          "confidence": 0.78,
          "prediction_as_fact": true,
          "needs_declaration": false,
          "caveats": []
        },
        {
          "claim_id": "c2",
          "text": "The predicted binding mode is sterically plausible.",
          "claim_kind": "mechanism",
          "allowed": true,
          "codes": [],
          "reasons": [],
          "weakest_tier": "preclinical",
          "confidence": 0.6,
          "prediction_as_fact": false,
          "needs_declaration": false,
          "caveats": [
            "Computational prediction: no assay supports this binding mode."
          ]
        }
      ],
      "limitations": [
        "Example data: placeholder run."
      ],
      "notes": "Gate G4 and the reason it is counted separately. The same evidence base supports c2 (mechanism, allowed, with a caveat) and cannot support c1 (clinical). The distinction is between the claim kinds, not between the evidence items.",
      "aggregate": 0.617,
      "aggregate_ci95": 0.024,
      "scores": {
        "task_success": 0.82,
        "evidence_grounding": 0.75,
        "provenance_completeness": 0.8,
        "reproducibility": 0.88,
        "safety_abstention": 0.64,
        "claim_calibration": 0.23,
        "latency": 0.58,
        "cost": 0.57
      },
      "raw": {
        "latency_s": 1158,
        "cost_usd": 5.08
      },
      "cases": 20,
      "rank": 2,
      "blocking_reasons": [
        {
          "gate": "G4",
          "detail": "A clinical efficacy conclusion was stated on docking and network-prediction evidence alone (CLM004 -> ART106).",
          "codes": [
            "ART106",
            "CLM004"
          ]
        }
      ],
      "refusal_codes": [
        "CLM004"
      ]
    }
  ]
};

window.ARENA_FALLBACK["benchmarks"] = {
  "example": true,
  "placeholder": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "current_season": "2026-S1",
  "seasons": [
    {
      "season": "2026-S1",
      "version": "1.0.0",
      "status": "frozen",
      "frozen_on": "2026-09-01",
      "note": "Example data. Current Season. 20 cases per track across six tracks.",
      "cases_total": 120,
      "tracks": [
        {
          "id": "TCM-Entity",
          "cases": 20,
          "metric_focus": [
            "entity accuracy",
            "ambiguity retention",
            "false-merge rate"
          ]
        },
        {
          "id": "TCM-Evidence",
          "cases": 20,
          "metric_focus": [
            "Recall@K",
            "nDCG",
            "citation validity",
            "study-design classification"
          ]
        },
        {
          "id": "TCM-NetPharm",
          "cases": 20,
          "metric_focus": [
            "edge provenance rate",
            "measured/predicted separation",
            "enrichment reproducibility"
          ]
        },
        {
          "id": "TCM-Safety",
          "cases": 20,
          "metric_focus": [
            "risk recall",
            "severe false-negative rate",
            "evidence sufficiency"
          ]
        },
        {
          "id": "TCM-TrialAudit",
          "cases": 20,
          "metric_focus": [
            "STRICTA coverage",
            "SPIRIT-TCM coverage",
            "CONSORT-CHM coverage"
          ]
        },
        {
          "id": "TCM-End2End",
          "cases": 20,
          "metric_focus": [
            "artifact completeness",
            "re-run success",
            "claim calibration"
          ]
        }
      ],
      "splits": [
        {
          "name": "dev",
          "cases": 60,
          "availability": "published with the release"
        },
        {
          "name": "hidden",
          "cases": 39,
          "availability": "delivered to official evaluators"
        },
        {
          "name": "adversarial",
          "cases": 21,
          "availability": "scored on its own axes"
        }
      ],
      "sources": [
        {
          "name": "HERB",
          "role": "TCM entity lexicon and measured compound-target edges",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "http://herb.ac.cn/",
          "snapshot_hash": "sha256:aaaaaaaaexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-Entity",
            "TCM-NetPharm"
          ]
        },
        {
          "name": "ETCM",
          "role": "formula and herb composition cross-check",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "http://www.tcmip.cn/ETCM/",
          "snapshot_hash": "sha256:bbbbbbbbexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-Entity"
          ]
        },
        {
          "name": "BindingDB",
          "role": "measured binding affinities",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "https://www.bindingdb.org/",
          "snapshot_hash": "sha256:ccccccccexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-NetPharm"
          ]
        },
        {
          "name": "openFDA / FAERS",
          "role": "adverse event signal counts",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "https://open.fda.gov/",
          "snapshot_hash": "sha256:ddddddddexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-Safety"
          ]
        },
        {
          "name": "PubMed / PMC (E-utilities)",
          "role": "literature retrieval and identifier resolution",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "https://www.ncbi.nlm.nih.gov/books/NBK25501/",
          "snapshot_hash": "sha256:eeeeeeeeexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-Evidence",
            "TCM-Safety",
            "TCM-TrialAudit"
          ]
        },
        {
          "name": "ClinicalTrials.gov",
          "role": "trial registration records",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "https://clinicaltrials.gov/",
          "snapshot_hash": "sha256:ffffffffexample000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-TrialAudit"
          ]
        },
        {
          "name": "STRICTA / SPIRIT-TCM / CONSORT-CHM",
          "role": "reporting-checklist instruments",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "https://www.equator-network.org/",
          "snapshot_hash": "sha256:11111111example000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-TrialAudit"
          ]
        },
        {
          "name": "Example pharmacopoeia extract",
          "role": "classical text passages",
          "licence": "Example licence string (placeholder)",
          "licence_verified": false,
          "url": "",
          "snapshot_hash": "sha256:22222222example000000000000000000000000000000000000000000000000",
          "snapshot_at": "2026-08-14",
          "used_by": [
            "TCM-Entity"
          ]
        }
      ]
    },
    {
      "season": "2025-S2",
      "version": "0.9.0",
      "status": "frozen",
      "frozen_on": "2025-09-01",
      "note": "Example data. Previous Season, kept for reference. 18 cases per track; the case count changed when TCM-End2End was widened.",
      "cases_total": 108,
      "tracks": [
        {
          "id": "TCM-Entity",
          "cases": 18,
          "metric_focus": [
            "entity accuracy",
            "ambiguity retention",
            "false-merge rate"
          ]
        },
        {
          "id": "TCM-Evidence",
          "cases": 18,
          "metric_focus": [
            "Recall@K",
            "nDCG",
            "citation validity",
            "study-design classification"
          ]
        },
        {
          "id": "TCM-NetPharm",
          "cases": 18,
          "metric_focus": [
            "edge provenance rate",
            "measured/predicted separation",
            "enrichment reproducibility"
          ]
        },
        {
          "id": "TCM-Safety",
          "cases": 18,
          "metric_focus": [
            "risk recall",
            "severe false-negative rate",
            "evidence sufficiency"
          ]
        },
        {
          "id": "TCM-TrialAudit",
          "cases": 18,
          "metric_focus": [
            "STRICTA coverage",
            "SPIRIT-TCM coverage",
            "CONSORT-CHM coverage"
          ]
        },
        {
          "id": "TCM-End2End",
          "cases": 18,
          "metric_focus": [
            "artifact completeness",
            "re-run success",
            "claim calibration"
          ]
        }
      ],
      "splits": [
        {
          "name": "dev",
          "cases": 54,
          "availability": "published with the release"
        },
        {
          "name": "hidden",
          "cases": 35,
          "availability": "delivered to official evaluators"
        },
        {
          "name": "adversarial",
          "cases": 19,
          "availability": "scored on its own axes"
        }
      ],
      "sources": []
    }
  ],
  "scoring_rules": [
    {
      "id": "S1",
      "rule": "Per-case scoring against the frozen gold label by the track's own scorer.",
      "applies_to": "all tracks",
      "detail": "Scorers are pure functions over the submitted artifact; they open no sockets and read no clock. The same artifact always produces the same score."
    },
    {
      "id": "S2",
      "rule": "A dimension score is the mean over the track's 20 cases.",
      "applies_to": "all dimensions",
      "detail": "Per-case scores are published with the run so a reader can recompute the mean."
    },
    {
      "id": "S3",
      "rule": "The aggregate is a weighted geometric mean of the eight dimension scores, with a floor of eps = 0.05.",
      "applies_to": "aggregate",
      "detail": "aggregate = exp( sum_i w_i * ln(max(x_i, eps)) / sum_i w_i ). The floor stops one collapsed dimension from zeroing an otherwise strong run; it does not rescue a genuinely failing one, because a hard gate is applied first and independently."
    },
    {
      "id": "S4",
      "rule": "Latency and Cost enter as 0-1 subscores mapped from raw values through the Season reference band.",
      "applies_to": "latency, cost",
      "detail": "The band is fixed when the Season is cut and published in the Season file. Raw seconds and USD are always shown next to the subscore."
    },
    {
      "id": "S5",
      "rule": "Hard gates are applied before ranking and are not score contributions.",
      "applies_to": "board membership",
      "detail": "A gated run is excluded from the trusted board and shown on the Experimental board with its blocking reason. No gate can be traded against a high score."
    },
    {
      "id": "S6",
      "rule": "Rank is published per track and per board.",
      "applies_to": "rank",
      "detail": "Ranks are not comparable across tracks or across Seasons. Sorting the table in the browser is a reading aid and does not change a published rank."
    },
    {
      "id": "S7",
      "rule": "Abstention is scored, not penalised.",
      "applies_to": "safety_abstention",
      "detail": "Abstaining with a stated reason scores above answering without evidence. A refusal with a code is a scored outcome, not a missing value."
    }
  ],
  "citation": {
    "text": "Cite the Season, not the website: the four version axes are what make the number re-derivable.",
    "bibtex": "@misc{tcmscience_arena_2026s1,\n  title        = {TCMScience Arena, Season 2026-S1},\n  author       = {{TCMScience Contributors}},\n  year         = {2026},\n  howpublished = {Frozen benchmark Season},\n  note         = {Runtime psh-0.1.0-example; Skills skills-2026.09.1-example;\n                  Sources sources-2026.09.3-example; Benchmark season-2026-S1-example}\n}",
    "example_placeholder": true
  }
};

window.ARENA_FALLBACK["skills"] = {
  "example": true,
  "generated_at": "2026-09-25T09:00:00Z",
  "season": "2026-S1",
  "registries": {
    "candidate": {
      "revision": "candidate-2026.09-example",
      "updated": "2026-09-01",
      "count": 42,
      "role": "Everything discovered. Never executed in production."
    },
    "stable": {
      "revision": "skills-2026.09.1-example",
      "updated": "2026-09-12",
      "count": 7,
      "role": "The only registry the runtime resolves skills from."
    },
    "benchmark": {
      "revision": "season-2026-S1-example",
      "updated": "2026-09-01",
      "role": "The frozen yardstick. A Season, once cut, is immutable."
    }
  },
  "skills": [
    {
      "id": "tcm.entity-resolver",
      "name": "TCM Entity Resolver",
      "status": "stable",
      "version": "1.3.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-skills",
      "commit": "9f2c1ab",
      "licence": "Apache-2.0",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:none"
      ],
      "benchmark_delta": {
        "track": "TCM-Entity",
        "metric": "entity accuracy",
        "delta": 0.031,
        "n": 20,
        "note": "Retains the ambiguous reading instead of merging; false-merge rate fell to 0.0."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-014",
        "decided_by": "example-reviewer-a",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry. Demonstrates the stable-registry shape.",
      "placeholder": true
    },
    {
      "id": "tcm.evidence-retriever",
      "name": "TCM Evidence Retriever",
      "status": "stable",
      "version": "2.1.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-skills",
      "commit": "4be07d3",
      "licence": "Apache-2.0",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:pubmed"
      ],
      "benchmark_delta": {
        "track": "TCM-Evidence",
        "metric": "Recall@10",
        "delta": 0.058,
        "n": 20,
        "note": "Identifier resolution runs before ranking, so unresolved records never surface."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-015",
        "decided_by": "example-reviewer-a",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "tcm.study-design-classifier",
      "name": "Study Design Classifier",
      "status": "stable",
      "version": "1.0.4",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-skills",
      "commit": "c1a9e02",
      "licence": "MIT",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark"
      ],
      "benchmark_delta": {
        "track": "TCM-Evidence",
        "metric": "study-design classification",
        "delta": 0.022,
        "n": 20,
        "note": "Predictive designs are classified as predictions, never as assays."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-016",
        "decided_by": "example-reviewer-b",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "tcm.netpharm-graph",
      "name": "Network Pharmacology Graph Builder",
      "status": "stable",
      "version": "0.9.2",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-netpharm",
      "commit": "77de410",
      "licence": "Apache-2.0",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:herb",
        "network:bindingdb",
        "exec:docking"
      ],
      "benchmark_delta": {
        "track": "TCM-NetPharm",
        "metric": "edge provenance rate",
        "delta": 0.077,
        "n": 20,
        "note": "Measured and predicted edges written to separate collections."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-017",
        "decided_by": "example-reviewer-c",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "tcm.safety-screen",
      "name": "Interaction and Contraindication Screen",
      "status": "stable",
      "version": "1.2.1",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-safety",
      "commit": "0a3b881",
      "licence": "Apache-2.0",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:openfda"
      ],
      "benchmark_delta": {
        "track": "TCM-Safety",
        "metric": "risk recall",
        "delta": 0.044,
        "n": 20,
        "note": "Abstains with a reason where the signal is too thin to report."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-018",
        "decided_by": "example-reviewer-c",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "tcm.trial-audit",
      "name": "Trial Report Auditor",
      "status": "stable",
      "version": "1.1.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-trials",
      "commit": "5d6f220",
      "licence": "CC-BY-4.0",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark"
      ],
      "benchmark_delta": {
        "track": "TCM-TrialAudit",
        "metric": "checklist coverage",
        "delta": 0.019,
        "n": 20,
        "note": "Reports per-instrument coverage and marks unjudgeable items as unjudgeable."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-019",
        "decided_by": "example-reviewer-b",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "tcm.artifact-envelope",
      "name": "Artifact Envelope Emitter",
      "status": "stable",
      "version": "1.0.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-core",
      "commit": "aa10c4f",
      "licence": "Apache-2.0",
      "licence_verified": false,
      "permissions": [
        "fs:write:artifacts"
      ],
      "benchmark_delta": {
        "track": "TCM-End2End",
        "metric": "artifact completeness",
        "delta": 0.0,
        "n": 20,
        "note": "Infrastructure skill: emits the envelope every other skill returns."
      },
      "approval": {
        "status": "approved",
        "decision_id": "PD-2026-020",
        "decided_by": "example-reviewer-a",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "upstream.skill-md-import",
      "name": "SKILL.md Import Adapter",
      "status": "candidate",
      "version": "0.3.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/upstream-skills",
      "commit": "be2210a",
      "licence": "Example licence string (placeholder)",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:any"
      ],
      "benchmark_delta": {
        "track": "TCM-Evidence",
        "metric": "Recall@10",
        "delta": null,
        "n": 0,
        "note": "Not yet benchmarked: candidate skills are never executed in production."
      },
      "approval": {
        "status": "pending",
        "decision_id": "",
        "decided_by": "",
        "decided_on": ""
      },
      "notes": "Example entry. Requested network:any is the reason promotion is not automatic.",
      "placeholder": true
    },
    {
      "id": "upstream.pdf-table-extract",
      "name": "PDF Table Extractor",
      "status": "candidate",
      "version": "0.2.1",
      "api_version": "1.0",
      "source_repo": "github.com/example/upstream-skills",
      "commit": "33f9b71",
      "licence": "Example licence string (placeholder)",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "fs:read:user-supplied"
      ],
      "benchmark_delta": {
        "track": "TCM-TrialAudit",
        "metric": "checklist coverage",
        "delta": null,
        "n": 0,
        "note": "Candidate: awaiting a licence determination."
      },
      "approval": {
        "status": "pending",
        "decision_id": "",
        "decided_by": "",
        "decided_on": ""
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "upstream.herb-graphrag",
      "name": "HERB GraphRAG",
      "status": "candidate",
      "version": "0.1.0",
      "api_version": "1.0",
      "source_repo": "github.com/example/graphrag-skills",
      "commit": "9c0d552",
      "licence": "Example licence string (placeholder)",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:herb",
        "exec:python"
      ],
      "benchmark_delta": {
        "track": "TCM-NetPharm",
        "metric": "edge provenance rate",
        "delta": null,
        "n": 0,
        "note": "Candidate: requests exec:python, which requires an explicit review."
      },
      "approval": {
        "status": "pending",
        "decision_id": "",
        "decided_by": "",
        "decided_on": ""
      },
      "notes": "Example entry.",
      "placeholder": true
    },
    {
      "id": "upstream.tcm-cli-bridge",
      "name": "tcm-cli Bridge",
      "status": "candidate",
      "version": "0.4.2",
      "api_version": "1.0",
      "source_repo": "github.com/example/tcm-cli",
      "commit": "1f7a003",
      "licence": "Example licence string (placeholder)",
      "licence_verified": false,
      "permissions": [
        "fs:read:benchmark",
        "network:none"
      ],
      "benchmark_delta": {
        "track": "TCM-Entity",
        "metric": "entity accuracy",
        "delta": -0.012,
        "n": 20,
        "note": "Benchmarked on the dev split only; a negative delta holds promotion."
      },
      "approval": {
        "status": "held",
        "decision_id": "PD-2026-021",
        "decided_by": "example-reviewer-a",
        "decided_on": "2026-09-12"
      },
      "notes": "Example entry. A held candidate: measured, and not promoted.",
      "placeholder": true
    }
  ],
  "promotion_rule": "The monthly pipeline may write only to the Candidate Catalog. Promotion into the Stable Registry requires a recorded PromotionDecision; a Season is cut from a Stable revision and is thereafter immutable."
};
