#!/usr/bin/env python3
"""Refuse a scout run that could reach production without a human decision.

The monthly job produces a candidate report. This script reads it and fails if
anything in it looks like an activation: a stable entry, a promoted flag, a
mutated lockfile. It exists because the rule it enforces is the whole point of
the update path, and a rule enforced only in prose is a rule that erodes the
first time a run is inconvenient.

Usage: check_no_autopromotion.py candidate-report.json
"""
from __future__ import annotations

import _bootstrap

_bootstrap.bootstrap()

import json
import sys
from pathlib import Path

#: Keys that would mean a run had promoted something. None may appear.
FORBIDDEN_KEYS = ("promoted", "activated", "stable_added", "lockfile_written")

def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))

    problems: list[str] = []

    found = report.get("note", "")
    if "Promotion requires" not in found:
        problems.append(
            "the report does not state that promotion requires a decision; the "
            "statement is part of the artifact, not a courtesy")

    for key in FORBIDDEN_KEYS:
        if _contains(report, key):
            problems.append(f"the report carries {key!r}, which means the run promoted")

    # A candidate marked eligible is a *proposal*. A candidate that is both
    # eligible and already in `stable` would mean the run did both.
    stable = {s.get("composite_id") for s in report.get("stable") or ()}
    for candidate in report.get("candidates") or ():
        cid = candidate.get("skill", {}).get("composite_id")
        if cid in stable:
            problems.append(
                f"{cid} appears as both a candidate and a stable entry; a scout run "
                "found a skill already promoted, which it cannot have done")

    if problems:
        print("AUTOPROMOTION CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    n = len(report.get("candidates") or ())
    print(f"ok: {n} candidate(s), none promoted")
    return 0

def _contains(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, key) for v in value)
    return False

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
