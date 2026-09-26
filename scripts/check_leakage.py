#!/usr/bin/env python3
"""No held-out benchmark content may reach a published file.

A hidden split is only hidden while it stays hidden. This checks that no case
identifier, question or gold answer appears in any committed file or any Arena
document — the mechanical form of the promise the data card makes.

The check is a no-op on a normal clone, because the cases are not published:
:func:`load_held_out` finds nothing and there is nothing to leak. It becomes
meaningful in the two situations that matter — a maintainer running it against a
checkout that *does* hold the cases, and CI running it against generated Arena
documents before a deploy.

Usage:
    check_leakage.py --published <dir>...   # scan generated Arena documents
    check_leakage.py                        # scan the whole repository tree
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIRS = (ROOT / "BioScience-Harness" / "benchmarks" / "cases",
              ROOT / "BioScience-Harness" / "benchmarks" / "hidden")

#: Files that legitimately describe case *structure* without carrying content.
EXEMPT = {"README.md", "check_leakage.py"}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".hypothesis", "node_modules",
             ".venv", "build", "dist"}

SCANNED_SUFFIXES = (".json", ".py", ".html", ".js", ".md", ".yaml", ".yml",
                    ".tex", ".csv", ".txt")


def load_held_out() -> list[dict]:
    """Every held-out case present on disk.

    Empty on a published clone, which is the normal state — the cases are
    delivered out of band. Absence here is not a pass, it is the absence of
    anything to check, and the summary says so.
    """
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
                if isinstance(case, dict) and case.get("visibility") in (
                        "hidden", "adversarial"):
                    out.append(case)
    return out


def scan(targets: list[Path], needles: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for target in targets:
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = [p for p in target.rglob("*")
                     if p.is_file()
                     and not any(d in SKIP_DIRS for d in p.parts)
                     and p.name not in EXEMPT
                     and p.suffix in SCANNED_SUFFIXES]
        else:
            continue
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for needle, label in needles.items():
                if needle and needle in text:
                    try:
                        where = path.relative_to(ROOT)
                    except ValueError:
                        where = path
                    problems.append(f"{where} contains {label}")
    return problems


def main(argv: list[str]) -> int:
    held = load_held_out()
    if not held:
        print("ok: no held-out cases present in this tree, nothing to leak "
              "(they are withheld by design; see .gitignore)")
        return 0

    needles: dict[str, str] = {}
    for case in held:
        cid = str(case.get("id") or "")
        if cid:
            needles[cid] = f"held-out case id {cid!r}"
        question = str(case.get("question") or "")
        # Short questions would match ordinary prose and make the check useless.
        if len(question) > 30:
            needles[question] = f"held-out question for {cid!r}"
        for key, value in (case.get("gold") or {}).items():
            text = str(value)
            if len(text) > 20:
                needles[text] = f"gold {key!r} of held-out case {cid!r}"

    if "--published" in argv:
        index = argv.index("--published")
        raw = argv[index + 1:]
        targets = [Path(p) for p in raw] or [ROOT / "arena" / "web" / "data"]
    else:
        targets = [ROOT]

    problems = scan(targets, needles)
    if problems:
        print("LEAKAGE CHECK FAILED:", file=sys.stderr)
        for p in problems[:40]:
            print(f"  - {p}", file=sys.stderr)
        if len(problems) > 40:
            print(f"  (+{len(problems) - 40} more)", file=sys.stderr)
        return 1
    print(f"ok: {len(needles)} held-out needle(s) absent from {len(targets)} target(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
