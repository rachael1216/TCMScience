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

    rows = doc.get("rows") or []
    for row in rows:
        rid = row.get("system") or row.get("run_id") or "?"
        scores = row.get("scores") or {}
        missing = [d for d in SCORE_DIMENSIONS if d not in scores]
        if missing:
            problems.append(f"{rid}: row is missing dimension(s) {missing}")
        if row.get("aggregate") is None and not row.get("placeholder"):
            problems.append(f"{rid}: row carries no aggregate")
        if row.get("board") == "experimental" and not row.get("blocking_reasons"):
            problems.append(
                f"{rid}: on the experimental board but names no blocking reason; a "
                "blocked row must say why it is blocked")

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
    print(f"ok: {len(rows)} row(s), every score decomposed across "
          f"{len(SCORE_DIMENSIONS)} dimensions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
