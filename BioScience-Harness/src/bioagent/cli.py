"""bioagent command line: fetch datasets, list what is fetchable, verify sources, doctor.

    python -m bioagent.cli fetch <component_id|filename> [--confirm]
    python -m bioagent.cli fetchable
    python -m bioagent.cli sources
    python -m bioagent.cli doctor [--json] [--smoke]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquisition import BulkDatasetProvider, Downloader, acquisition_for
from .config import data_lake_dir
from .runtime.registry import ComponentRegistry, Resolver


def _registry() -> ComponentRegistry:
    return ComponentRegistry(BulkDatasetProvider().discover())


def _cmd_scout(a) -> int:
    """Discover candidates and write a report.

    Deliberately cannot promote. There is no flag for it: the only writer of the
    stable registry is `Registry.promote`, which requires a `PromotionDecision`
    naming a human, and a scheduled CLI invocation is not one.
    """
    from .updates import Registry, Scout, load_sources, rank, score_candidate, to_candidate
    from .updates.scout import SourceError

    sources = []
    path = Path(a.sources)
    if path.is_file():
        try:
            sources = load_sources(path.read_text(encoding="utf-8"))
        except SourceError as exc:
            print(f"source registry refused: {exc}", file=sys.stderr)
            return 2
    elif a.skills:
        # A local scan needs no source registry; that is the CI path.
        pass
    else:
        print(f"no source registry at {path} and no --skills tree given", file=sys.stderr)
        return 2

    roots = [Path(s) for s in (a.skills or [])]
    report = Scout(sources, offline=True, local_roots=roots).run()
    scout = Scout(sources, offline=True, local_roots=roots)

    candidates = []
    for found in report.found:
        audit = scout.audit(found)
        score = score_candidate(found, report=audit)
        candidates.append(to_candidate(found, score, report=audit))

    ordered = rank(candidates)
    if a.eligible_only:
        ordered = tuple(c for c in ordered if c.eligible)

    registry = Registry()                       # read-only here; nothing is promoted
    payload = {
        "report": {"found": len(report.found),
                   "needs_adapter": len(report.needs_adapter),
                   "unreachable": len(report.unreachable)},
        "unreachable": [{"source": s, "reason": r} for s, r in report.unreachable],
        "needs_adapter": list(report.needs_adapter),
        "candidates": [c.as_dict() for c in ordered],
        "stable": [v.as_dict() for v in registry.stable_versions()],
        "note": ("Candidates only. Promotion requires a reviewed PromotionDecision and "
                 "is not performed by this command."),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    print(report.explain())
    for c in ordered:
        flag = "eligible" if c.eligible else "ELIMINATED"
        print(f"  {flag:<10} {c.spec.composite_id:<52} {c.score:5.1f}")
    return 0


def _cmd_registry_release(a) -> int:
    """Cut a release bundle from a lockfile. Refuses an unprovenanced registry."""
    from datetime import datetime, timezone

    from .updates import RegistryRelease, load_lockfile

    lockfile = Path(a.lockfile)
    if not lockfile.is_file():
        print(f"no lockfile at {lockfile}", file=sys.stderr)
        return 2
    try:
        versions = load_lockfile(lockfile.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        print(f"lockfile refused: {exc}", file=sys.stderr)
        return 2

    missing = [v.skill_id for v in versions if not v.approved_by]
    if missing:
        print(f"refusing to release: {missing} carry no approval attribution",
              file=sys.stderr)
        return 1

    created = a.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if a.season:
        versions = tuple(
            type(v)(**{**{f: getattr(v, f) for f in v.__dataclass_fields__},
                       "benchmark_version": a.season}) for v in versions)
    release = RegistryRelease(release_id=a.release_id, created_at=created,
                              entries=versions)
    release = type(release)(release_id=release.release_id,
                            created_at=release.created_at, entries=release.entries,
                            digest=release.compute_digest(),
                            notes=f"cut against {a.season or 'unversioned'}")
    text = json.dumps(release.as_dict(), indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0 if release.verify() else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bioagent")
    ap.add_argument("--dest", default=None, help="data directory (default: data lake dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download a fetchable dataset")
    f.add_argument("target", help="component id or filename")
    f.add_argument("--confirm", action="store_true", help="allow downloads above the size gate")
    sub.add_parser("fetchable", help="list datasets that can be fetched")
    sub.add_parser("sources", help="list public API connectors")
    d = sub.add_parser("doctor", help="report what this installation can do, and what it cannot")
    d.add_argument("--json", action="store_true", help="machine-readable report")
    d.add_argument("--smoke", action="store_true", help="also run every native tool's example")

    # -- governed skill updates ------------------------------------------
    sc = sub.add_parser("scout", help="discover and score candidate skills (never promotes)")
    sc.add_argument("--sources", default="registry/skill_sources.yaml",
                    help="declared source registry")
    sc.add_argument("--skills", action="append", default=[],
                    help="a directory tree to scan; repeatable")
    sc.add_argument("--out", default="", help="write the candidate report here")
    sc.add_argument("--eligible-only", action="store_true",
                    help="omit candidates eliminated by a hard condition")

    rr = sub.add_parser("registry-release",
                        help="cut a verifiable release from the stable lockfile")
    rr.add_argument("--lockfile", default="registry/skills.lock.yaml")
    rr.add_argument("--release-id", required=True)
    rr.add_argument("--season", default="")
    rr.add_argument("--created-at", default="")
    rr.add_argument("--out", default="")

    a = ap.parse_args(argv)

    if a.cmd == "scout":
        return _cmd_scout(a)
    if a.cmd == "registry-release":
        return _cmd_registry_release(a)

    dest = Path(a.dest) if getattr(a, "dest", None) else data_lake_dir()
    if a.cmd == "doctor":
        from .doctor import diagnose, render

        report = diagnose(data_lake=dest, smoke=a.smoke)
        print(json.dumps(report, indent=1, ensure_ascii=False, default=str) if a.json
              else render(report))
        return 0 if report["verdict"] != "blocked" else 1
    reg = _registry()
    if a.cmd == "fetchable":
        present = {p.name for p in dest.iterdir()} if dest.is_dir() else set()
        res = Resolver(reg, dataset_probe=lambda n: n in present)
        for m in reg:
            r = res.resolve(m.id)
            state = "present" if not r.missing_datasets else ("fetchable" if r.fetchable else "blocked")
            spec = acquisition_for(m)
            print(f"{state:<9} {m.id:<48} {spec.host if spec else ''}")
        return 0
    if a.cmd == "sources":
        from .providers.public_apis import SOURCES
        for s in SOURCES:
            print(f"{s.key:<15} {s.host:<36} {len(s.operations)} ops  {s.license}")
        return 0
    # fetch
    m = reg.get(a.target) or next((x for x in reg if x.name == a.target), None)
    if m is None:
        print(f"unknown dataset {a.target!r}; try `fetchable`", file=sys.stderr)
        return 2
    spec = acquisition_for(m)
    dl = Downloader(dest, log=lambda s: print(s, file=sys.stderr))
    try:
        out = dl.fetch(spec.url, spec.filename, checksum=spec.checksum,
                       expected_bytes=spec.expected_bytes, confirm=a.confirm)
    except Exception as exc:  # noqa: BLE001
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"path": str(out.path), "bytes": out.bytes, "verified": out.verified,
                      "checksum": out.checksum, "from_cache": out.from_cache}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
