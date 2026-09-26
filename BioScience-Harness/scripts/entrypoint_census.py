#!/usr/bin/env python3
"""Measure how many python entrypoints are real, instead of assuming they are.

Catalogue rows carry a source path, not an import path, so every python
component's `module:function` entrypoint is *derived*. A derivation can be
wrong, and a wrong one fails only at invocation time — the component still
resolves, still passes policy, and only then reports "import failed".

This reports the derivation's accuracy in three widening levels:

    CONSISTENT  the module path agrees with the component's own source_paths
    IMPORTABLE  importlib can find the module here
    CALLABLE    the module imports and the named function exists on it

CONSISTENT is checkable anywhere. IMPORTABLE and CALLABLE need the upstream
projects installed, so they are reported as "not measurable here" rather than
as failures on a machine that does not have them.

    python scripts/entrypoint_census.py
    python scripts/entrypoint_census.py --json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bioagent.config import catalogue_path  # noqa: E402
from bioagent.providers.catalogue import CatalogueProvider  # noqa: E402


def expected_suffix(source_path: str, project: str) -> str:
    """The dotted package tail a source path implies, independent of any deriver.

    Deliberately does not call `module_for_source`: a check that re-derives the
    value it is checking always passes. This reads the path directly, so it
    fails whenever the module drops packages the path contains.
    """
    rel = (source_path or "").split(";")[0].strip()
    if not rel.endswith(".py"):
        return ""
    parts = [p for p in Path(rel).with_suffix("").parts if p not in (".", "/")]
    if parts and parts[0].lower() in (project.lower(), project.lower().replace(" ", "-")):
        parts = parts[1:]
    while parts and parts[0].lower() in ("src", "lib", "python", "packages"):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def consistent(module: str, source_path: str, project: str) -> bool:
    """Does the module path end with the package structure the source path shows?

    A component whose source is `.../a/b/c.py` must import from a module ending
    in `a.b.c`. Keeping only the final segment is what let
    `biomni.tool.pharmacology` stand in for
    `biomni/tool/tool_description/pharmacology.py` — the module ends in
    `pharmacology`, but not in `tool_description.pharmacology`.
    """
    suffix = expected_suffix(source_path, project)
    if not suffix:
        return True          # nothing to check against
    return module == suffix or module.endswith("." + suffix)


def census(check_imports: bool = True) -> dict:
    import pandas as pd

    rows = pd.read_csv(catalogue_path()).to_dict("records")
    manifests = [m for m in CatalogueProvider(rows).discover()
                 if m.runtime.backend == "python"]
    out = {"python_components": len(manifests), "consistent": 0, "inconsistent": 0,
           "importable": 0, "callable": 0, "not_importable_here": 0,
           "malformed": 0, "examples_inconsistent": [], "examples_not_importable": []}
    for m in manifests:
        target = m.runtime.entrypoint
        if ":" not in target:
            out["malformed"] += 1
            continue
        module, _, func = target.partition(":")
        if consistent(module, m.provider.source_path, m.provider.project):
            out["consistent"] += 1
        else:
            out["inconsistent"] += 1
            if len(out["examples_inconsistent"]) < 8:
                out["examples_inconsistent"].append(
                    {"id": m.id, "entrypoint": target, "source": m.provider.source_path})
        if not check_imports:
            continue
        try:
            found = importlib.util.find_spec(module.split(".")[0]) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            found = False
        if not found:
            out["not_importable_here"] += 1
            if len(out["examples_not_importable"]) < 5:
                out["examples_not_importable"].append(module)
            continue
        out["importable"] += 1
        try:
            mod = importlib.import_module(module)
        except Exception:  # noqa: BLE001 - a failed import is the measurement
            continue
        if hasattr(mod, func):
            out["callable"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    ap.add_argument("--no-imports", action="store_true",
                    help="only check source-path consistency (no upstream projects needed)")
    args = ap.parse_args(argv)

    data = census(check_imports=not args.no_imports)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    n = data["python_components"]
    print(f"python-backed components: {n}")
    print(f"  CONSISTENT with source_paths : {data['consistent']} "
          f"({data['consistent'] / n:.1%})" if n else "")
    print(f"  inconsistent                 : {data['inconsistent']}")
    print(f"  malformed entrypoint         : {data['malformed']}")
    if not args.no_imports:
        print(f"  IMPORTABLE here              : {data['importable']}")
        print(f"  CALLABLE here                : {data['callable']}")
        print(f"  upstream not installed here  : {data['not_importable_here']} "
              "(not a defect: these projects are federated, not vendored)")
    for ex in data["examples_inconsistent"]:
        print(f"    inconsistent: {ex['entrypoint']}  <-  {ex['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
