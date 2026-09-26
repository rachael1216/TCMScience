#!/usr/bin/env python3
"""A score must never be rendered without its decomposition.

The plan states it as a requirement, so it is checked mechanically rather than
left to a template: every leaderboard row must carry all eight dimensions, and a
row blocked from the trusted board must say why. A row showing only an aggregate
would be a design failure that looks like a formatting choice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Both trees: these scripts import bioagent, which imports psh.
for _entry in (ROOT / "BioScience-Harness" / "src", ROOT / "PSH-Harness" / "src"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


def main(argv: list[str]) -> int:
    from bioagent.benchmarks import SCORE_DIMENSIONS

    data_dir = Path(argv[1]) if len(argv) > 1 else ROOT / "arena" / "web" / "data"
    problems: list[str] = []

    lb = data_dir / "leaderboard.json"
    if not lb.is_file():
        print(f"no leaderboard.json in {data_dir}", file=sys.stderr)
        return 1
    doc = json.loads(lb.read_text(encoding="utf-8"))

    # The site reads `leaderboard.runs` (see arena/web/assets/app.js). The
    # checker originally looked for `rows`, which no data file has — so it
    # passed on every input including an empty one, and was decoration until a
    # real data file made the key mismatch visible.
    if "runs" not in doc:
        print("ARENA DATA CHECK FAILED:", file=sys.stderr)
        print("  - leaderboard.json has no `runs` key; the site reads `runs` and "
              "would render an empty table", file=sys.stderr)
        return 1
    rows = doc["runs"] or []

    # Every row must carry its full decomposition, and a blocked row must say
    # why. This is the rule the plan states as a requirement, and it is checked
    # mechanically rather than left to a template: a row showing only an
    # aggregate would be a design failure that looks like a formatting choice.
    for row in rows:
        rid = row.get("system") or row.get("run_id") or "?"
        scores = row.get("scores") or {}
        missing = [d for d in SCORE_DIMENSIONS if d not in scores]
        if missing:
            problems.append(f"{rid}: row is missing dimension(s) {missing}")
        if row.get("aggregate") is None and not row.get("placeholder"):
            problems.append(f"{rid}: row carries no aggregate")
        if row.get("board") in ("experimental",) and not row.get("blocking_reasons"):
            problems.append(
                f"{rid}: on the experimental board but names no blocking reason; a "
                "blocked row must say why it is blocked")

    # A dimension that is null must carry a reason, or the site renders a dash
    # with no explanation — which reads as "zero" rather than "not measured".
    for row in rows:
        rid = row.get("system") or row.get("run_id") or "?"
        reasons = row.get("score_reasons") or {}
        for dim in SCORE_DIMENSIONS:
            if (row.get("scores") or {}).get(dim) is None and dim not in reasons:
                problems.append(
                    f"{rid}: dimension {dim!r} is null with no stated reason")

    # The season's dimension list and the code's must agree, or the site would
    # render a column nothing scores.
    tracks = data_dir / "tracks.json"
    if tracks.is_file():
        declared = {d["key"] for d in
                    (json.loads(tracks.read_text(encoding="utf-8"))
                     .get("dimensions") or [])}
        if declared and declared != set(SCORE_DIMENSIONS):
            problems.append(
                f"tracks.json declares dimensions {sorted(declared)} but the scorer "
                f"produces {sorted(SCORE_DIMENSIONS)}")

    if problems:
        print("ARENA DATA CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(rows or [])} row(s), every score decomposed across "
          f"{len(SCORE_DIMENSIONS)} dimensions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
