#!/usr/bin/env python3
"""Generate the Arena's JSON from the registries. Read-only, one writer.

The Arena computes nothing (`docs/adr/0004`). This script is the only thing that
writes `arena/web/data/*.json`, and every row it emits carries its score
decomposition — a leaderboard row showing only an aggregate is a design failure,
and `check_arena_data.py` fails the build rather than render one.

With no results published yet, the generated documents carry the *shape* and the
registries, and an explicit statement that scores are pending. Emitting fabricated
rows would be worse than emitting none.

Usage: build_arena_data.py --registry BioScience-Harness/registry --out arena/web/data
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Both trees: these scripts import bioagent, which imports psh.
for _entry in (ROOT / "BioScience-Harness" / "src", ROOT / "PSH-Harness" / "src"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


def _has_runs(path: Path) -> bool:
    """Whether a leaderboard document already holds measured rows.

    A build step must not silently discard measurements produced by
    `run_demo_season.py`. Both write here, so the rule is: fill an empty one,
    never overwrite a populated one.
    """
    if not path.is_file():
        return False
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("runs"))
    except (ValueError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="BioScience-Harness/registry")
    ap.add_argument("--out", default="arena/web/data")
    a = ap.parse_args(argv)

    from bioagent.benchmarks import GATES, GATE_DESCRIPTIONS, SCORE_DIMENSIONS, TRACKS
    from bioagent.updates import load_lockfile

    registry_dir = Path(a.registry)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    lockfile = registry_dir / "skills.lock.yaml"
    versions = (load_lockfile(lockfile.read_text(encoding="utf-8"))
                if lockfile.is_file() else [])

    common = {
        "generated": True,
        "note": ("Generated from the registries by scripts/build_arena_data.py. "
                 "The Arena renders; it never scores."),
    }

    (out / "tracks.json").write_text(json.dumps({
        **common,
        "season": "season-1",
        "dimensions": [{"key": d, "label": d.replace("_", " ").title(),
                        "direction": "lower_is_better" if d in ("latency", "cost")
                                     else "higher_is_better"}
                       for d in SCORE_DIMENSIONS],
        "tracks": [{"key": t, "metric_focus": f} for t, f in TRACKS.items()],
        "gates": [{"code": c, "description": GATE_DESCRIPTIONS[c]} for c in GATES],
        "aggregation": {
            "method": "weighted_harmonic_mean",
            "formula": "n / sum(1 / s_i) over the eight dimensions",
            "note": ("dominated by the weakest dimension, so a system must be good "
                     "at provenance and safety together, not good at one and absent "
                     "at the other")},
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # `runs`, not `rows`: the site reads `leaderboard.runs` (app.js). This file
    # previously wrote an empty `rows` list, which the site ignored and the
    # checker did not look at — so it rendered nothing and nothing complained.
    # It does not overwrite an existing `runs` document, because
    # `run_demo_season.py` produces real measurements into the same path and a
    # build step silently discarding them would be the same class of bug.
    leaderboard = out / "leaderboard.json"
    if not _has_runs(leaderboard):
        leaderboard.write_text(json.dumps({
            **common,
            "season": "season-1",
            "season_label": "Season 1 (not yet evaluated)",
            "runs": [],
            "boards": {"trusted": "Empty.", "experimental": "Empty."},
            "pending": ("No runs have been evaluated. Rows appear once the Runner "
                        "has published signed result bundles; this file is generated "
                        "from those bundles and never from a submission."),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print("  leaderboard.json already carries runs; left untouched")

    (out / "benchmarks.json").write_text(json.dumps({
        **common,
        "seasons": [{
            "season": "season-1", "status": "designed",
            "tracks": {t: {"cases": 20, "metric_focus": f} for t, f in TRACKS.items()},
            "splits": {"dev": 60, "hidden": 40, "adversarial": 20},
            "cases_published": False,
            "note": ("The cases and their gold labels are not published. Only their "
                     "digests are, so a reported score can be tied to a revision of "
                     "the case set without the set being disclosed."),
        }],
        "scoring": [{"dimension": d} for d in SCORE_DIMENSIONS],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "skills.json").write_text(json.dumps({
        **common,
        "stable": [v.as_dict() for v in versions],
        "candidate": [],
        "candidate_note": ("Candidates are produced by the monthly scout and are "
                           "never active until a reviewed PromotionDecision moves "
                           "one into the stable registry."),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "runs.json").write_text(json.dumps({
        **common, "runs": [],
        "pending": "No run traces have been published.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    written = sorted(p.name for p in out.glob("*.json"))
    print(f"wrote {len(written)} document(s) to {out}")
    for name in written:
        print(f"  {name}")
    print(f"  {len(versions)} stable skill(s) from {lockfile.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
