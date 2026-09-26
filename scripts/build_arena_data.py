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

    # One writer per file. `runs.json`, `tracks.json` and `skills.json` are
    # produced by `run_demo_season.py` from real skill runs; this script writes
    # only `benchmarks.json`, which is season metadata from the registry.
    #
    # Both scripts used to write all four with *different shapes*, so whichever
    # ran last decided whether the site rendered. Three separate page crashes
    # came from that, and a shared-path convention is not a fix — one writer per
    # file is.
    lockfile = registry_dir / "skills.lock.yaml"
    versions = (load_lockfile(lockfile.read_text(encoding="utf-8"))
                if lockfile.is_file() else [])

    common = {
        "generated": True,
        "note": ("Generated from the registries by scripts/build_arena_data.py. "
                 "The Arena renders; it never scores."),
    }

    (out / "benchmarks.json").write_text(json.dumps({
        **common,
        "season": "season-1",
        "dimensions": [{"key": d, "label": d.replace("_", " ").title(),
                        "direction": "lower_is_better" if d in ("latency", "cost")
                                     else "higher_is_better"}
                       for d in SCORE_DIMENSIONS],
        # `id` and a *list* `metric_focus`: that is what the site reads
        # (`app.js` maps over metric_focus). This script previously wrote `key`
        # and a comma-joined string, which crashed the overview page — two
        # generators wrote the same file with different shapes and the later one
        # won. Shapes are now pinned by tests against the site's own reader.
        "season_label": "Season 1 (not yet evaluated)",
        "season_status": "designed",
        "tracks": [{"id": t, "name": t, "cases": 20,
                    "metric_focus": [m.strip() for m in f.split(",")]}
                   for t, f in TRACKS.items()],
        "gates": [{"id": {"GATE002": "G1", "GATE001": "G2", "GATE003": "G3",
                          "GATE004": "G4"}.get(c, c),
                   "code": c,
                   "name": GATE_DESCRIPTIONS[c].split(";")[0].split(":")[0][:60],
                   "rule": GATE_DESCRIPTIONS[c],
                   "board": "experimental"}
                  for c in GATES],
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
            # A list of objects with an `id` and a *list* `metric_focus`, matching
            # what `benchmarks.html` maps over. Every shape in these documents is
            # now pinned by `scripts/check_arena_contract.py`, because this file
            # and `run_demo_season.py` both write here and had drifted apart
            # three separate times before that check existed.
            "tracks": [{"id": t, "name": t, "cases": 20,
                        "metric_focus": [m.strip() for m in f.split(",")]}
                       for t, f in TRACKS.items()],
            # A list of objects, which is what benchmarks.html renders. A dict
            # here crashed the page, because the reader maps over the field.
            "splits": [
                {"name": "dev", "cases": 60,
                 "availability": "published with the release"},
                {"name": "hidden", "cases": 40,
                 "availability": "delivered to official evaluators"},
                {"name": "adversarial", "cases": 20,
                 "availability": "scored on its own axes"},
            ],
            "cases_published": False,
            "note": ("The cases and their gold labels are not published. Only their "
                     "digests are, so a reported score can be tied to a revision of "
                     "the case set without the set being disclosed."),
        }],
        "scoring": [{"dimension": d} for d in SCORE_DIMENSIONS],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    written = sorted(p.name for p in out.glob("*.json"))
    print(f"wrote {len(written)} document(s) to {out}")
    for name in written:
        print(f"  {name}")
    print(f"  {len(versions)} stable skill(s) from {lockfile.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
