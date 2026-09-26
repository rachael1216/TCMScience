"""``bioagent doctor``: what this installation can actually do, before a run finds out.

The 2026-09-18 review's F12: readiness was implicit. A run discovered at step four that
the data lake was empty, that a backend was missing, that PSH was not importable or that
the classifier was the fallback. This module asks every question up front and returns
one report with a verdict and a remedy per problem. It never fetches, never writes and
never calls a network service: it reports the state of the machine it runs on.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import __version__

__all__ = ["diagnose", "render"]


def _psh_section() -> dict[str, Any]:
    try:
        import psh  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - the answer is "not importable, because"
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
    out: dict[str, Any] = {"available": True, "version": getattr(psh, "__version__", "")}
    # Through PSH's public door only: bioagent never imports psh.kernel (the boundary
    # the evolution pipeline enforces applies to the doctor as much as to a candidate).
    report = getattr(psh, "environment_report", None)
    if report is None:
        out["classifier"] = {"error": "this PSH has no environment_report (older than 0.5.3)"}
        out["isolation"] = {"error": "this PSH has no environment_report (older than 0.5.3)"}
        return out
    try:
        facts = report()
        out["classifier"] = facts.get("classifier", {})
        out["isolation"] = facts.get("isolation", {})
    except Exception as exc:  # noqa: BLE001
        out["classifier"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        out["isolation"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def _datasets_section(lake: Path) -> dict[str, Any]:
    from .acquisition import BulkDatasetProvider, acquisition_for
    from .runtime.registry import ComponentRegistry, Resolver

    registry = ComponentRegistry(BulkDatasetProvider().discover())
    present = {p.name for p in lake.iterdir()} if lake.is_dir() else set()
    resolver = Resolver(registry, dataset_probe=lambda n: n in present)
    counts = {"present": 0, "fetchable": 0, "blocked": 0}
    items = []
    for manifest in registry:
        resolution = resolver.resolve(manifest.id)
        state = ("present" if not resolution.missing_datasets
                 else "fetchable" if resolution.fetchable else "blocked")
        counts[state] += 1
        spec = acquisition_for(manifest)
        items.append({"id": manifest.id, "state": state, "host": spec.host if spec else ""})
    return {"data_lake": str(lake), "exists": lake.is_dir(), "total": len(items), **counts,
            "items": items}


def _connectors_section() -> dict[str, Any]:
    from .providers.public_apis import SOURCES

    return {"sources": len(SOURCES),
            "operations": sum(len(s.operations) for s in SOURCES),
            "hosts": sorted({s.host for s in SOURCES})}


def _tools_section(smoke: bool) -> dict[str, Any]:
    from .tools import DOMAINS, TOOLS, run_smoke

    out: dict[str, Any] = {"count": len(TOOLS), "domains": len(DOMAINS)}
    if smoke:
        failures = []
        for t in TOOLS:
            ok, message = run_smoke(t.name)
            if not ok:
                failures.append({"tool": t.name, "message": message[:200]})
        out["smoke"] = {"ran": len(TOOLS), "failed": failures}
    return out


def _backends_section(lake: Path) -> dict[str, Any]:
    from .psh.assembly import default_runtime

    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                              data_lake=lake, cache_dir=None)
    return runtime.backends.availability()


def diagnose(*, data_lake: str | Path | None = None, smoke: bool = False) -> dict[str, Any]:
    """One report: sections, problems with remedies, and a verdict."""
    from .config import data_lake_dir

    lake = Path(data_lake) if data_lake else data_lake_dir()
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "bioagent": __version__,
        "platform": sys.platform,
        "network": {"https_proxy": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
                    "no_proxy": bool(os.environ.get("NO_PROXY") or os.environ.get("no_proxy"))},
    }
    problems: list[dict[str, str]] = []

    def section(name: str, build: Any) -> None:
        try:
            report[name] = build()
        except Exception as exc:  # noqa: BLE001 - a broken section is a finding
            report[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            problems.append({"section": name, "severity": "blocking",
                             "problem": f"could not inspect {name}: {type(exc).__name__}",
                             "remedy": "reinstall the package (pip install -e .) and rerun"})

    section("backends", lambda: _backends_section(lake))
    section("datasets", lambda: _datasets_section(lake))
    section("connectors", _connectors_section)
    section("native_tools", lambda: _tools_section(smoke))
    section("psh", _psh_section)
    section("tcm", lambda: __import__("bioagent.tcm", fromlist=["default_knowledge"])
            .default_knowledge().stats())

    backends = report.get("backends") or {}
    python_backend = backends.get("python", {}) if isinstance(backends, dict) else {}
    if not python_backend.get("available", False):
        problems.append({"section": "backends", "severity": "blocking",
                         "problem": "the python backend is unavailable",
                         "remedy": python_backend.get("reason") or "reinstall the package"})
    for name, info in (backends.items() if isinstance(backends, dict) else ()):
        if name in ("python", "none", "http", "dataset") or info.get("available"):
            continue
        problems.append({"section": "backends", "severity": "degraded",
                         "problem": f"backend {name!r} is unavailable",
                         "remedy": info.get("reason") or "install its runtime"})
    datasets = report.get("datasets") or {}
    if isinstance(datasets, dict) and not datasets.get("exists"):
        problems.append({"section": "datasets", "severity": "degraded",
                         "problem": f"no data lake at {datasets.get('data_lake')}",
                         "remedy": "run `bioagent fetchable` and fetch what the work needs"})
    elif isinstance(datasets, dict) and datasets.get("blocked"):
        problems.append({"section": "datasets", "severity": "degraded",
                         "problem": f"{datasets['blocked']} dataset(s) are neither present nor fetchable",
                         "remedy": "they need a manual download; see their acquisition notes"})
    tools = report.get("native_tools") or {}
    if isinstance(tools, dict) and tools.get("smoke", {}).get("failed"):
        problems.append({"section": "native_tools", "severity": "blocking",
                         "problem": f"{len(tools['smoke']['failed'])} native tool(s) fail their smoke test",
                         "remedy": "see native_tools.smoke.failed"})
    psh_info = report.get("psh") or {}
    if not psh_info.get("available"):
        problems.append({"section": "psh", "severity": "degraded",
                         "problem": "PSH-Harness is not importable; runs are ungoverned",
                         "remedy": "pip install -e ../PSH-Harness (or add its src to PYTHONPATH)"})
    else:
        classifier = psh_info.get("classifier", {})
        if classifier and not classifier.get("validated"):
            problems.append({"section": "psh", "severity": "degraded",
                             "problem": "classification runs on PSH's built-in fallback detector",
                             "remedy": "install sable for the validated PHI detector; policies with "
                                       "require_validated_classifier will refuse to start"})
        isolation = psh_info.get("isolation", {})
        if isolation and not isolation.get("os_isolation"):
            problems.append({"section": "psh", "severity": "degraded",
                             "problem": "isolated tools run without an OS sandbox",
                             "remedy": "install a SandboxBackend (bubblewrap+seccomp on Linux, "
                                       "Seatbelt on macOS); policies with require_os_isolation "
                                       "will refuse to start"})
    if not report["network"]["https_proxy"]:
        problems.append({"section": "network", "severity": "info",
                         "problem": "no HTTPS proxy configured; connector calls go direct",
                         "remedy": "expected on a workstation; set HTTPS_PROXY behind an egress policy"})

    severities = {p["severity"] for p in problems}
    verdict = ("blocked" if "blocking" in severities else
               "degraded" if "degraded" in severities else "ready")
    report["problems"] = problems
    report["verdict"] = verdict
    return report


def render(report: dict[str, Any]) -> str:
    """The report as people read it. JSON is the other option."""
    lines = [f"bioagent {report.get('bioagent')} on Python {report.get('python')} "
             f"({report.get('platform')}) — verdict: {report.get('verdict', '?').upper()}"]
    backends = report.get("backends") or {}
    if isinstance(backends, dict) and "error" not in backends:
        ok = sorted(n for n, i in backends.items() if i.get("available"))
        missing = sorted(n for n, i in backends.items() if not i.get("available"))
        lines.append(f"backends   available: {', '.join(ok) or 'none'}"
                     + (f"; unavailable: {', '.join(missing)}" if missing else ""))
    datasets = report.get("datasets") or {}
    if isinstance(datasets, dict) and "error" not in datasets:
        lines.append(f"datasets   {datasets.get('present', 0)} present, "
                     f"{datasets.get('fetchable', 0)} fetchable, {datasets.get('blocked', 0)} blocked "
                     f"(lake: {datasets.get('data_lake')})")
    connectors = report.get("connectors") or {}
    if isinstance(connectors, dict) and "error" not in connectors:
        lines.append(f"connectors {connectors.get('sources')} sources / "
                     f"{connectors.get('operations')} operations")
    tools = report.get("native_tools") or {}
    if isinstance(tools, dict) and "error" not in tools:
        smoke = tools.get("smoke")
        lines.append(f"tools      {tools.get('count')} native tools in {tools.get('domains')} domains"
                     + (f"; smoke: {len(smoke.get('failed', []))} failed of {smoke.get('ran')}"
                        if smoke else ""))
    tcm = report.get("tcm") or {}
    if isinstance(tcm, dict) and "error" not in tcm:
        lines.append(f"tcm        {tcm.get('herbs')} herbs, {tcm.get('formulas')} formulas, "
                     f"{tcm.get('syndromes')} syndromes, {tcm.get('relations')} relations, "
                     f"{tcm.get('safety')} safety records")
    psh_info = report.get("psh") or {}
    if psh_info.get("available"):
        classifier = psh_info.get("classifier", {})
        isolation = psh_info.get("isolation", {})
        lines.append(f"psh        {psh_info.get('version')}; classifier "
                     f"{classifier.get('detector', '?')} "
                     f"({'validated' if classifier.get('validated') else 'fallback'}); "
                     f"sandbox {isolation.get('sandbox', '?')} "
                     f"({'OS isolation' if isolation.get('os_isolation') else 'no OS isolation'})")
    else:
        lines.append(f"psh        not available: {psh_info.get('reason', '')}")
    problems = report.get("problems") or []
    if problems:
        lines.append("")
        for p in problems:
            lines.append(f"[{p['severity']}] {p['section']}: {p['problem']}\n    remedy: {p['remedy']}")
    return "\n".join(lines)
