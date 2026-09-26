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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "BioScience-Harness" / "src"))


def main(argv: list[str]) -> int:
    from bioagent.benchmarks import SCORE_DIMENSIONS

    data_dir = Path(argv[1]) if len(argv) > 1 else ROOT / "arena" / "web" / "data"
    problems: list[str] = []

    lb = data_dir / "leaderboard.json"
    if not lb.is_file():
        print(f"no leaderboard.json in {data_dir}", file=sys.stderr)
        return 1
    doc = json.loads(lb.read_text(encoding="utf-8"))

    for row in doc.get("rows") or ():
        rid = row.get("system", "?")
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

    if problems:
        print("ARENA DATA CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(doc.get('rows') or ())} row(s), every score decomposed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
