"""Assemble a BioScience ``Runtime`` the bridge can drive, from what ships in the package.

``demo_harness.py`` shows the canonical assembly and needs pandas and a repos checkout;
this is the same wiring with the standard library only, so the isolated entrypoint can
build it in a child process and a test can build it in milliseconds.
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..config import REPO_ROOT, catalogue_path, data_lake_dir

__all__ = ["default_runtime", "load_catalogue_rows", "load_verification"]

VERIFICATION_CSV = REPO_ROOT / "data" / "connector_live_verification.csv"


def load_catalogue_rows(path: str | Path | None = None) -> list[dict[str, str]]:
    """The capability catalogue as rows, without pandas."""
    target = Path(path) if path else catalogue_path()
    with Path(target).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_verification(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-source summary of the last live verification: ok/total, date, median latency.

    Absent or unreadable means "unverified", never "verified".
    """
    target = Path(path) if path else VERIFICATION_CSV
    if not target.is_file():
        return {}
    per_source: dict[str, dict[str, Any]] = {}
    latencies: dict[str, list[float]] = {}
    try:
        with target.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = row.get("source") or ""
                entry = per_source.setdefault(key, {"ok": 0, "total": 0, "verified_at": "",
                                                    "smoke_ok": False})
                entry["total"] += 1
                if row.get("status") == "SUCCEEDED":
                    entry["ok"] += 1
                    if str(row.get("smoke")).lower() == "true":
                        entry["smoke_ok"] = True
                entry["verified_at"] = max(entry["verified_at"], row.get("verified_at") or "")
                try:
                    latencies.setdefault(key, []).append(float(row.get("latency_ms") or 0))
                except ValueError:
                    pass
    except (OSError, csv.Error):
        return {}
    for key, entry in per_source.items():
        values = [v for v in latencies.get(key, []) if v > 0]
        entry["latency_ms"] = statistics.median(values) if values else 0
    return per_source


def default_runtime(*, catalogue: bool = True, public_apis: bool = True,
                    native_tools: bool = True,
                    catalogue_rows: Iterable[Mapping[str, Any]] | None = None,
                    extra_manifests: Iterable[Any] = (), cache_dir: str | Path | None = None,
                    data_lake: str | Path | None = None, kernel: Any = None,
                    http_timeout_s: float = 30.0) -> Any:
    """A runtime over the packaged catalogue, the public sources and the native tools,
    with every backend."""
    from ..backends.base import BackendRegistry
    from ..backends.concrete import (ContainerBackend, DatasetBackend, MCPBackend, NoneBackend,
                                     PythonBackend, SubprocessBackend)
    from ..backends.http import HTTPBackend
    from ..policy import PolicyKernel
    from ..providers.catalogue import CatalogueProvider
    from ..providers.public_apis import PublicAPIProvider
    from ..runtime.agentspec import Runtime
    from ..runtime.registry import ComponentRegistry, Loader, Resolver, default_backend_probe

    manifests: list[Any] = []
    if catalogue:
        rows = list(catalogue_rows) if catalogue_rows is not None else load_catalogue_rows()
        manifests.extend(CatalogueProvider(rows).discover())
    if public_apis:
        manifests.extend(PublicAPIProvider().discover())
    if native_tools:
        from ..tools import NativeToolProvider
        manifests.extend(NativeToolProvider().discover())
    manifests.extend(extra_manifests)
    registry = ComponentRegistry(manifests)

    lake = Path(data_lake) if data_lake else data_lake_dir()
    present = {p.name for p in lake.iterdir()} if lake.is_dir() else set()
    # The resolver is built before the runtime (the python backend needs the loader, the
    # loader needs the resolver), so its backend probe is a late binding that the runtime
    # fills in once it exists: the answer must come from the backends actually registered.
    probe: dict[str, Any] = {"fn": default_backend_probe}
    resolver = Resolver(registry, dataset_probe=lambda cid: cid in present,
                        backend_probe=lambda backend: probe["fn"](backend))
    loader = Loader(registry, resolver)
    backends = BackendRegistry([
        PythonBackend(loader), MCPBackend(), DatasetBackend(lake), SubprocessBackend(),
        ContainerBackend(), NoneBackend(),
        HTTPBackend(cache_dir=cache_dir, timeout_s=http_timeout_s),
    ])
    runtime = Runtime(registry, backends, kernel=kernel or PolicyKernel(), resolver=resolver,
                      loader=loader, catalogue_version="unified-v2")
    probe["fn"] = runtime._backend_probe
    return runtime
