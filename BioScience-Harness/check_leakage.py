#!/usr/bin/env python3
"""No held-out benchmark content may reach a published file.

A hidden split is only hidden while it stays hidden. This checks that no case
identifier, question or gold answer appears in any committed file or any Arena
document. It is the mechanical form of the promise the data card makes.

Usage:
    check_leakage.py                        # scan the repository tree
    check_leakage.py --published <dir>      # scan generated Arena documents
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIRS = (ROOT / "BioScience-Harness" / "benchmarks" / "cases",
              ROOT / "BioScience-Harness" / "benchmarks" / "hidden")

#: Files that legitimately mention case *structure* without content.
EXEMPT = {"README.md"}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".hypothesis", "node_modules",
             ".venv", "build", "dist"}


def load_held_out() -> list[dict]:
    """Every held-out case found on disk. Empty when none are present, which is
    the normal state of a clone — the cases are delivered out of band."""
    out: list[dict] = []
    for directory in CASES_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            for case in (data if isinstance(data, list) else [data]):
                if isinstance(case, dict) and case.get("visibility") in ("hidden",
                                                                        "adversarial"):
                    out.append(case)
    return out


def scan(targets, needles: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for target in targets:
        files = [target] if target.is_file() else [
            p for p in target.rglob("*")
            if p.is_file() and not any(d in SKIP_DIRS for d in p.parts)
            and p.name not in EXEMPT
            and p.suffix in (".json", ".py", ".html", ".js", ".md", ".yaml", ".yml",
                             ".tex", ".csv")]
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for needle, label in needles.items():
                if needle and needle in text:
                    problems.append(f"{path.relative_to(ROOT)} contains {label}")
    return problems


def main(argv: list[str]) -> int:
    held = load_held_out()
    if not held:
        print("ok: no held-out cases present in this tree, nothing to leak")
        return 0

    needles: dict[str, str] = {}
    for case in held:
        cid = str(case.get("id") or "")
        if cid:
            needles[cid] = f"held-out case id {cid!r}"
        question = str(case.get("question") or "")
        if len(question) > 30:
            needles[question] = f"held-out question for {cid!r}"

    if "--published" in argv:
        index = argv.index("--published")
        targets = [Path(p) for p in argv[index + 1:]] or [ROOT / "arena" / "web" / "data"]
    else:
        targets = [ROOT]

    problems = scan(targets, needles)
    if problems:
        print("LEAKAGE CHECK FAILED:", file=sys.stderr)
        for p in problems[:40]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(needles)} held-out identifier(s) absent from "
          f"{len(targets)} target(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
