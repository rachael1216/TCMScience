#!/usr/bin/env python3
"""A release bundle must verify against its own digest.

The digest is what lets a reader tie a published result to a registry revision
without trusting the file it arrived in. A bundle whose digest does not match is
either corrupt or edited, and either way unusable.
"""
from __future__ import annotations

import _bootstrap

_bootstrap.bootstrap()

import json
import sys
from pathlib import Path

ROOT = _bootstrap.ROOT

def main(argv: list[str]) -> int:
    from bioagent.updates import RegistryRelease, SkillVersion

    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    release = RegistryRelease(
        release_id=data["release_id"], created_at=data.get("created_at", ""),
        entries=tuple(SkillVersion.from_dict(v) for v in data.get("skills") or ()),
        digest=data.get("digest", ""), notes=data.get("notes", ""))
    if not release.verify():
        print(f"RELEASE DIGEST MISMATCH: {argv[1]}", file=sys.stderr)
        print(f"  recorded {release.digest[:16]}", file=sys.stderr)
        print(f"  computed {release.compute_digest()[:16]}", file=sys.stderr)
        return 1
    print(f"ok: {release.release_id} verifies ({len(release.entries)} skill(s))")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
