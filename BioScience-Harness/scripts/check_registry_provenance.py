#!/usr/bin/env python3
"""Every stable entry must name a human who approved it, and when.

An unattributed approval is not an approval. The human review step is the entire
content of the governance claim, so its absence has to be fatal rather than a
warning.
"""
from __future__ import annotations

import _bootstrap

_bootstrap.bootstrap()

import sys
from pathlib import Path

ROOT = _bootstrap.ROOT

def main(argv: list[str]) -> int:
    from bioagent.updates import load_lockfile

    path = Path(argv[1]) if len(argv) > 1 else ROOT / "registry" / "skills.lock.yaml"
    versions = load_lockfile(path.read_text(encoding="utf-8"))
    problems = []
    for v in versions:
        if not v.approved_by:
            problems.append(f"{v.skill_id}@{v.version}: no approver")
        if not v.approved_at:
            problems.append(f"{v.skill_id}@{v.version}: no approval timestamp")
        if not v.content_hash:
            problems.append(f"{v.skill_id}@{v.version}: no content hash")
    if problems:
        print("PROVENANCE CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(versions)} entr(y/ies) carry an attributed approval")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
