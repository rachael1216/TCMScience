#!/usr/bin/env python3
"""Assert the generated Arena data matches the shape the site actually reads.

    python scripts/check_arena_contract.py arena/web/data

**Why this exists.** Two generators write into `arena/web/data` —
`build_arena_data.py` (from the registries) and `run_demo_season.py` (from real
skill runs) — and they had drifted from the site's reader three separate times:

* `tracks[].metric_focus` was a comma-joined **string** where `index.html` maps
  over an array, which crashed the overview page;
* `seasons[].splits` was a **dict** where `benchmarks.html` iterates a list;
* `skills[].permissions` was a structured **object** where `skills.html` renders
  a list of short strings.

Each was found by loading the page in a browser and reading the error, which is
a slow and unreliable way to find a schema violation. This script is the fast
one: it states the contract in one place and fails the build when a generator
breaks it.

The contract is expressed as the *type* each field must have, because that is
precisely what went wrong every time — not missing data, but the wrong shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: `{"file": {"field.path": expected-type}}`. A path walks dicts by key and
#: lists by `[]`; a trailing `[]` means "every element of this list".
#:
#: Types are the JSON kinds the site's JavaScript requires, not Python types:
#: `"array"` is what `.map()` needs, `"object"` is what `for...in` needs.
CONTRACT: dict[str, dict[str, str]] = {
    "leaderboard.json": {
        "runs": "array",
        "runs[].run_id": "str",
        "runs[].system": "str",
        "runs[].track": "str",
        "runs[].board": "str",
        "runs[].scores": "object",
        "runs[].versions": "object",
        # A list: `leaderboard.html` maps over the boards to build its filter.
        "boards": "array",
        "boards[].id": "str",
        "boards[].label": "str",
    },
    "tracks.json": {
        "dimensions": "array",
        "dimensions[].key": "str",
        "dimensions[].label": "str",
        "tracks": "array",
        "tracks[].id": "str",
        # A list: `index.html` does `(track.metric_focus || []).map(...)`.
        "tracks[].metric_focus": "array",
        "gates": "array",
        "gates[].id": "str",
        "gates[].rule": "str",
    },
    "benchmarks.json": {
        "seasons": "array",
        "seasons[].splits": "array",
        "seasons[].splits[].name": "str",
        "seasons[].splits[].cases": "int",
        # A list: `benchmarks.html` maps over the season's tracks.
        "seasons[].tracks": "array",
        "seasons[].tracks[].id": "str",
        "seasons[].tracks[].metric_focus": "array",
    },
    "skills.json": {
        "skills": "array",
        "skills[].id": "str",
        "skills[].status": "str",
        "skills[].licence": "str",
        # A list of short strings: `skills.html` maps over it. The structured
        # object form crashed the page.
        "skills[].permissions": "array",
    },
    "runs.json": {
        "runs": "array",
        "runs[].run_id": "str",
        "runs[].provenance": "object",
    },
}

#: The example documents must satisfy the same contract: they are what a fresh
#: clone renders, so a shape error there breaks the site for everyone who has not
#: generated real data yet.
ALSO_CHECK_EXAMPLES = True


def _json_kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _walk(document, path: str):
    """Yield every value at ``path``. Raises KeyError if the path is absent."""
    if not path:
        yield document
        return
    head, _, rest = path.partition(".")
    if head.endswith("[]"):
        key = head[:-2]
        container = document[key] if key else document
        if not isinstance(container, list):
            raise KeyError(f"{key or '<root>'} is not a list")
        for item in container:
            yield from _walk(item, rest)
        return
    if head not in document:
        raise KeyError(head)
    yield from _walk(document[head], rest)


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    contract = CONTRACT.get(path.name)
    if contract is None:
        return problems
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [f"{path.name}: cannot read ({exc})"]

    for field, expected in contract.items():
        try:
            values = list(_walk(document, field))
        except (KeyError, TypeError) as exc:
            problems.append(f"{path.name}: `{field}` is missing ({exc})")
            continue
        for value in values:
            actual = _json_kind(value)
            if actual != expected and not (expected == "int" and actual == "float"):
                problems.append(
                    f"{path.name}: `{field}` is {actual}, the site reads it as "
                    f"{expected}")
                break
    return problems


def main(argv: list[str]) -> int:
    data_dir = Path(argv[1]) if len(argv) > 1 else Path("arena/web/data")
    if not data_dir.is_dir():
        print(f"no such directory: {data_dir}", file=sys.stderr)
        return 2

    suffixes = ("*.json",) if not ALSO_CHECK_EXAMPLES else ("*.json", "*.example.json")
    files: list[Path] = []
    for suffix in suffixes:
        files.extend(sorted(data_dir.glob(suffix)))
    # Avoid double-counting `x.json` matched twice by both globs.
    files = sorted(set(files))

    problems: list[str] = []
    checked = 0
    for path in files:
        name = path.name.replace(".example", "")
        if name not in CONTRACT:
            continue
        checked += 1
        problems.extend(check_file(path))

    if problems:
        print("ARENA CONTRACT CHECK FAILED:", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"  - {problem}", file=sys.stderr)
        print("\n  The site reads these fields with the type named above. A "
              "generator emitting a different shape renders a broken page, which "
              "is how all three of the shapes above were found.", file=sys.stderr)
        return 1

    n_fields = sum(len(CONTRACT[p.name.replace('.example', '')]) for p in files
                   if p.name.replace(".example", "") in CONTRACT)
    print(f"ok: {checked} document(s), {n_fields} field shape(s) match the site's reader")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
