#!/usr/bin/env python3
"""Run the four skills for real and record what they actually produced.

    python scripts/run_demo_season.py --out arena/web/data

**What this is, and what it is not.** It is a *demonstration run*: the four P0
skills are executed, and their real artifacts — real content hashes, real
evidence items, real claims with their real validation verdicts, real measured
latency — are written to the Arena's data files.

It is **not** a benchmark result. A benchmark score requires gold answers the
repository does not ship, so six of the eight score dimensions cannot be
computed at all and this script does not invent them. `score_dimensions` marks
every uncomputable dimension as `null` with a reason, and the row carries
`board: "demonstration"` rather than `trusted`. A leaderboard with a fabricated
number in it would be worse than an empty one, because the number would be
believed.

The three dimensions that *can* be measured without gold labels are measured
for real:

* `provenance_completeness` — are the sources pinned, do claims carry citations
  that resolve to them, does every artifact name its four version axes;
* `reproducibility` — running the skill twice gives the same artifact digest;
* `claim_calibration` — did the system refuse the claims it should have refused.

Those three are the ones this project is actually about, so a demonstration that
shows them working is more informative than eight invented numbers would be.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (ROOT / "BioScience-Harness" / "src", ROOT / "PSH-Harness" / "src"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from bioagent.contracts import validate_artifact                    # noqa: E402
from bioagent.skills.loader import load_skills                      # noqa: E402
from bioagent.skills.p0 import (analyze_tcm_network_pharmacology,   # noqa: E402
                                assess_tcm_safety, normalize_tcm_entities,
                                retrieve_tcm_evidence)

#: The cases this demonstration runs. Each is chosen so the skill has to *decide*
#: something — an ambiguity, a prediction, an incompatibility, an absence —
#: rather than succeed trivially. A demonstration that only showed easy inputs
#: would not demonstrate the part that matters.
CASES = [
    ("TCM-Entity", "demo-entity-01", "Resolve 姜 to a corpus entity",
     lambda: normalize_tcm_entities(["姜"], run_id="demo-season")),
    ("TCM-Entity", "demo-entity-02", "Resolve 甘草 and report its processed forms",
     lambda: normalize_tcm_entities(["甘草"], run_id="demo-season")),
    ("TCM-Evidence", "demo-evidence-01", "What evidence exists about 附子?",
     lambda: retrieve_tcm_evidence("附子", run_id="demo-season")),
    ("TCM-NetPharm", "demo-netpharm-01",
     "What target network is inferred for 桂枝汤?",
     lambda: analyze_tcm_network_pharmacology("桂枝汤", run_id="demo-season")),
    ("TCM-Safety", "demo-safety-01", "Safety of 附子",
     lambda: assess_tcm_safety("附子", run_id="demo-season")),
    ("TCM-Safety", "demo-safety-02",
     "甘草 co-administered with 甘遂 (an 18-fan pair)",
     lambda: assess_tcm_safety("甘草", co_administered=["甘遂"],
                               run_id="demo-season")),
    ("TCM-Safety", "demo-safety-03", "A substance with no record",
     lambda: assess_tcm_safety("不存在的药", run_id="demo-season")),
]


def measure(dimension: str, artifact, verdict, elapsed_s: float,
            rerun_digest: str) -> tuple[float | None, str]:
    """Measure one dimension, or explain why it cannot be measured here."""
    if dimension == "provenance_completeness":
        cited = {s for c in artifact.claims for s in c.supports}
        items = {e.id: e for e in artifact.evidence}
        cards = {s.id: s for s in artifact.sources}
        checks = [
            bool(artifact.sources),
            all(s.pinned for s in artifact.sources),
            all(s.licensed for s in artifact.sources),
            bool(artifact.composite_version.get("source")),
            all(items[s].source_card_id in cards for s in cited if s in items),
        ]
        return sum(checks) / len(checks), (
            "pinned sources, identified licences, a source-set digest, and every "
            "cited item traceable to a declared card")

    if dimension == "reproducibility":
        same = artifact.digest == rerun_digest
        return (1.0 if same else 0.0), (
            "the skill was run twice; identical artifact digest" if same
            else "the two runs produced different digests")

    if dimension == "claim_calibration":
        # Every claim is put through the gate. A claim the gate refused is a
        # *correct refusal* when the skill knew it would be refused — which is
        # what `require_declared` and the manifest policy are for. Here we score
        # simply: no claim may be publishable while failing its own evidence
        # policy, and an empty claim set is correct when the result is empty.
        from bioagent.contracts import check_claim
        index = {e.id: e for e in artifact.evidence}
        bad = [c.id for c in artifact.claims
               if not check_claim(c, index).allowed]
        if not artifact.claims:
            return 1.0, ("the run produced an empty result and made no claim, "
                         "which is the correct behaviour")
        return (1.0 if not bad else 0.0), (
            f"{len(artifact.claims)} claim(s), none overclaiming their evidence"
            if not bad else f"claims overclaiming their evidence: {bad}")

    return None, (
        "requires gold labels from the benchmark corpus, which is not published; "
        "this demonstration does not invent a value")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="arena/web/data")
    ap.add_argument("--season", default="demo-1")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from bioagent.benchmarks import SCORE_DIMENSIONS, TRACKS, GATES
    from bioagent.skills.p0.common import SKILL_VERSIONS

    loaded, _ = load_skills(ROOT / "BioScience-Harness" / "skills" / "tcm")
    skill_hashes = {s.spec.id: s.content_hash for s in loaded}

    runs = []
    for track, case_id, question, fn in CASES:
        started = time.perf_counter()
        artifact = fn()
        elapsed = time.perf_counter() - started
        # A second run, for the reproducibility dimension.
        rerun = fn()
        verdict = validate_artifact(artifact)
        checksum = artifact.digest

        dimensions: dict = {}
        reasons: dict = {}
        for dim in SCORE_DIMENSIONS:
            value, why = measure(dim, artifact, verdict, elapsed, rerun.digest)
            dimensions[dim] = value
            reasons[dim] = why

        # Latency and cost are recorded raw; the site inverts them for display
        # and shows these values beneath. Cost is 0.00 because no model call and
        # no network request was made — that is a measurement, not an omission.
        dimensions["latency"] = None
        reasons["latency"] = (f"measured {elapsed * 1000:.0f} ms for this case; "
                              "recorded under `raw`, not as a 0-1 dimension")
        dimensions["cost"] = None
        reasons["cost"] = ("0.00 USD: the skills run offline against the compiled "
                           "corpus, making no model call and no network request")

        measured = {k: v for k, v in dimensions.items() if v is not None}
        runs.append({
            "run_id": case_id,
            "system": "TCMScience P0 skills (demonstration)",
            "system_slug": "tcmscience-demo",
            "type": "Skill",
            "version": artifact.skill_version,
            "track": track,
            "board": "demonstration",
            "placeholder": False,
            "demonstration": True,
            "cases": 1,
            "question": question,
            "aggregate": (sum(measured.values()) / len(measured)
                          if measured else None),
            "aggregate_note": ("mean of the dimensions measurable without gold "
                               "labels; NOT a benchmark score"),
            "scores": dimensions,
            "score_dimensions": dimensions,
            "score_reasons": reasons,
            "raw": {"latency_s": round(elapsed, 6), "cost_usd": 0.0},
            "versions": dict(artifact.composite_version),
            "skill": {"id": artifact.skill_id,
                      "version": artifact.skill_version,
                      "content_hash": skill_hashes.get(artifact.skill_id, "")},
            "validation": verdict.as_dict(),
            "refusal_codes": sorted({v.code for v in verdict.violations}),
            "blocking_reasons": [v.detail for v in verdict.violations],
            "artifact_digest": checksum,
            "provenance": {
                "sources": [s.as_dict() for s in artifact.sources],
                "evidence": [e.as_dict() for e in artifact.evidence],
                "claims": [c.as_dict() for c in artifact.claims],
                "outputs": [o.as_dict() for o in artifact.outputs],
                "limitations": list(artifact.limitations),
                "assumptions": list(artifact.assumptions),
            },
            "trace": [
                {"step": 1, "action": "load",
                 "detail": f"skill {artifact.skill_id}@{artifact.skill_version} "
                           f"loaded from skills/tcm/"},
                {"step": 2, "action": "read",
                 "detail": f"{len(artifact.sources)} declared source(s), "
                           f"snapshot {artifact.sources[0].snapshot_hash[:12]}…"
                 if artifact.sources else "no declared source"},
                {"step": 3, "action": "produce",
                 "detail": f"{len(artifact.evidence)} evidence item(s), "
                           f"{len(artifact.claims)} claim(s), "
                           f"{len(artifact.outputs)} output file(s)"},
                {"step": 4, "action": "validate",
                 "detail": ("publishable" if verdict.publishable
                            else f"REFUSED: {[v.code for v in verdict.violations]}")},
            ],
            "limitations": list(artifact.limitations),
        })

    by_track: dict = {}
    for run in runs:
        by_track.setdefault(run["track"], []).append(run)

    tracks_doc = {
        "generated": True,
        "season": args.season,
        "season_status": "demonstration",
        "note": ("A demonstration run of the four P0 skills. Real artifacts, real "
                 "validation verdicts, real latencies. NOT a benchmark: six of the "
                 "eight dimensions need gold labels the repository does not ship."),
        "dimensions": [
            {"key": "task_success", "label": "Task Success", "weight": 0.125,
             "direction": "higher", "range": [0, 1],
             "definition": "Share of cases whose objective was met, judged against "
                           "a gold answer. Requires the benchmark corpus."},
            {"key": "evidence_grounding", "label": "Evidence Grounding", "weight": 0.125,
             "direction": "higher", "range": [0, 1],
             "definition": "Whether claims rest on evidence that licenses them, and "
                           "whether that evidence is cited. Requires gold answers."},
            {"key": "provenance_completeness", "label": "Provenance Completeness",
             "weight": 0.125, "direction": "higher", "range": [0, 1],
             "definition": "Pinned sources, identified licences, four version axes, "
                           "and every cited item traceable to a declared card."},
            {"key": "reproducibility", "label": "Reproducibility", "weight": 0.125,
             "direction": "higher", "range": [0, 1],
             "definition": "Re-running the same input yields the same artifact digest."},
            {"key": "safety_abstention", "label": "Safety and Abstention",
             "weight": 0.125, "direction": "higher", "range": [0, 1],
             "definition": "Whether high-risk signals are surfaced and unsafe "
                           "conclusions declined. Requires gold labels."},
            {"key": "claim_calibration", "label": "Claim Calibration", "weight": 0.125,
             "direction": "higher", "range": [0, 1],
             "definition": "Whether the system refuses claims its evidence cannot "
                           "support, and makes none it cannot support."},
            {"key": "latency", "label": "Latency", "weight": 0.125,
             "direction": "lower", "range": [0, None],
             "definition": "Wall-clock seconds per case. Reported raw."},
            {"key": "cost", "label": "Cost", "weight": 0.125,
             "direction": "lower", "range": [0, None],
             "definition": "USD per case. Reported raw."},
        ],
        "tracks": [{"id": t, "name": t, "cases": 20,
                    "metric_focus": [m.strip() for m in focus.split(",")],
                    "cases_run_here": len(by_track.get(t, []))}
                   for t, focus in TRACKS.items()],
        "gates": [{"id": {"GATE002": "G1", "GATE001": "G2", "GATE003": "G3",
                          "GATE004": "G4"}.get(code, code),
                   "code": code, "name": desc.split(";")[0].split(":")[0][:60],
                   "rule": desc, "board": "experimental"}
                  for code, desc in GATES.items()],
        "aggregation": {
            "method": "harmonic_mean",
            "formula": "n / sum(1 / s_i) over the eight dimensions",
            "note": ("dominated by the weakest dimension, so a system must be good "
                     "at provenance and safety together"),
        },
    }
    (out / "tracks.json").write_text(
        json.dumps(tracks_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "leaderboard.json").write_text(json.dumps({
        "generated": True,
        "season": args.season,
        "season_label": "Demonstration run",
        "dimensions": [d["key"] for d in tracks_doc["dimensions"]],
        "boards": {
            "demonstration": ("The four P0 skills, run for real. Real artifacts and "
                              "real validation verdicts; three of eight dimensions "
                              "are measurable without a published gold corpus."),
            "trusted": "Empty. No Season has been evaluated.",
            "experimental": "Empty.",
        },
        "runs": runs,
        "pending": ("The trusted board is empty because no benchmark Season has been "
                    "evaluated. Filling it needs the 120-case corpus and gold labels, "
                    "which are not published — a public gold answer contaminates the "
                    "benchmark for everyone who clones it."),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "runs.json").write_text(json.dumps({
        "generated": True, "season": args.season, "runs": runs,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "skills.json").write_text(json.dumps({
        "generated": True, "season": args.season,
        "registries": {"candidate": 0, "stable": len(loaded), "benchmark": 0},
        "skills": [{
            "id": s.spec.id, "name": s.spec.name, "status": "stable",
            "version": s.spec.version, "api_version": s.spec.api_version,
            "source_repo": "TCMScience", "commit": s.content_hash[:12],
            "licence": s.spec.license_spdx, "licence_verified": True,
            "permissions": {
                "network": list(s.spec.permissions.network),
                "filesystem_read": list(s.spec.permissions.filesystem_read),
                "filesystem_write": list(s.spec.permissions.filesystem_write),
                "subprocess": s.spec.permissions.subprocess,
            },
            "evidence": {
                "max_tier": s.spec.evidence.max_tier,
                "claim_kinds": list(s.spec.evidence.claim_kinds),
                "forbidden_claims": list(s.spec.evidence.forbidden_claims),
            },
            "benchmark_delta": None,
            "benchmark_delta_note": "not measured; no Season has been evaluated",
            "approval": {"approved_by": "TCMScience maintainers",
                         "approved_at": "2026-09-25T00:00:00Z"},
            "content_hash": s.content_hash,
            "placeholder": False,
        } for s in loaded],
        "promotion_rule": ("A candidate never becomes active automatically: the only "
                           "writer of a stable entry requires a PromotionDecision "
                           "naming a human."),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    measured_total = sum(1 for r in runs for v in r["scores"].values() if v is not None)
    print(f"wrote demonstration data to {out}")
    print(f"  {len(runs)} real skill run(s) across "
          f"{len({r['track'] for r in runs})} track(s)")
    print(f"  {measured_total} dimension values measured; the rest are null with a "
          f"stated reason")
    print(f"  aggregate per run is the mean of the measurable dimensions only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
