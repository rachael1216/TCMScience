"""Evaluators and benchmarks as components, not a separate test directory.

BioMedArena's 155 benchmarks were extracted in v1 as catalogue rows. Exposing them
as components means any proposed upgrade can be scored old-versus-new through the
same registry the runtime already uses, instead of via a bespoke harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

from ..runtime.component import (ComponentManifest, LicenseSpec, Provider,
                                 RuntimeSpec, Validation)
from .pipeline import BenchmarkResult


def benchmark_components(rows: Iterable[dict], project: str = "BioMedArena",
                         license_spdx: str = "MIT") -> Iterator[ComponentManifest]:
    """Turn catalogue benchmark rows into evaluator components."""
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        yield ComponentManifest(
            id=f"{project.lower()}.benchmark.{name.lower().replace(' ', '-')}",
            kind="benchmark", name=name,
            description=str(row.get("description") or "")[:300],
            domain=str(row.get("domain") or ""),
            omics_type=str(row.get("omics_type") or "general"),
            provider=Provider(project=project,
                              source_path=str(row.get("source_paths") or "")),
            # A benchmark is a specification; the evaluator executes it.
            runtime=RuntimeSpec(backend="none"),
            license=LicenseSpec(spdx=license_spdx, integration_mode="vendor"),
            validation=Validation(),
        )


def _invoke_manifest(runtime: Any, manifest: ComponentManifest, spec: Any, **kwargs: Any):
    """Score a manifest through the runtime's full invocation path.

    This used to be a second, simplified execution chain
    (`resolve_manifest -> backend.invoke`), and it was wrong in two ways that
    both corrupted evolution results:

    * it handed the manifest to a backend directly, and `PythonBackend` resolves
      its entrypoint by *id* through the production loader — so a candidate
      carrying the incumbent's id was scored by executing the **incumbent**;
    * it accepted `spec` and never used it, so `PolicyKernel.authorize()` was
      never consulted and a candidate that production would DENY still ran.

    `Runtime.invoke_manifest()` closes both: it builds a scratch registry,
    resolver, loader and backend set around this manifest, and then goes through
    the ordinary resolve -> POLICY -> backend path. Keeping one execution chain
    is the point — a second one drifts from production the moment either changes.
    """
    from ..runtime.agentspec import AgentSpec

    return runtime.invoke_manifest(manifest, spec=spec or AgentSpec(name="evaluator"),
                                   **kwargs)


@dataclass
class SmokeTestEvaluator:
    """Scores a component by invoking it and checking the result shape.

    This is a real, if minimal, evaluator: it calls the component through the
    runtime and scores 1.0 only if the call actually SUCCEEDED — a component that
    merely resolves scores 0, so "it routed" can never look like "it works".
    """

    runtime: Any
    spec: Any
    call_kwargs: dict | None = None

    def score(self, manifest: ComponentManifest, benchmark: str = "smoke") -> BenchmarkResult:
        res = _invoke_manifest(self.runtime, manifest, self.spec, **(self.call_kwargs or {}))
        value = 1.0 if res.status.name == "SUCCEEDED" else 0.0
        return BenchmarkResult(benchmark=benchmark, score=value, n_cases=1,
                              detail={"status": res.status.value, "error": res.error})


@dataclass
class ShapeEvaluator:
    """Scores a dataset component on whether it returns usable tabular structure."""

    runtime: Any
    spec: Any
    min_columns: int = 1

    def score(self, manifest: ComponentManifest, benchmark: str = "dataset-shape") -> BenchmarkResult:
        res = _invoke_manifest(self.runtime, manifest, self.spec, nrows=3)
        if res.status.name != "SUCCEEDED" or not isinstance(res.value, dict):
            return BenchmarkResult(benchmark, 0.0, 1, {"status": res.status.value})
        shape = res.value.get("shape") or [0, 0]
        cols = len(res.value.get("columns") or [])
        rows_ok = shape[0] > 0
        cols_ok = cols >= self.min_columns
        score = (0.5 * rows_ok) + (0.5 * cols_ok)
        return BenchmarkResult(benchmark, float(score), 1,
                               {"shape": shape, "n_columns": cols,
                                "parsed_as": res.value.get("parsed_as")})
