"""Tests for registry lookup, adapter dispatch, provenance replay and licensing."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from bioagent.config import catalogue_path, data_lake_dir, data_lake_available
import pytest

from bioagent import BioAgent, CapabilityRegistry, ProvenanceLog, SandboxExecutor
from bioagent.adapters import (
    Adapter,
    CallResult,
    DataLakeAdapter,
    FederatedProcessAdapter,
    LicenseError,
    NativeConnectorAdapter,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# per-test markers apply; lake-dependent tests carry @pytest.mark.integration
CATALOGUE = catalogue_path()


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_csv(CATALOGUE)


# ------------------------------------------------------------------ registry
def test_catalogue_loads_and_is_non_trivial(registry: CapabilityRegistry) -> None:
    assert len(registry) > 2000
    kinds = registry.counts("kind")
    for expected in ("tool", "skill", "database", "dataset", "benchmark"):
        assert kinds.get(expected, 0) > 0


def test_all_sixteen_projects_present(registry: CapabilityRegistry) -> None:
    projects = set(registry.projects())
    for p in ("Biomni", "OrigeneMCP", "K-Dense", "ClawBio", "PantheonOS",
              "BioMedArena", "STELLA", "GenoMAS", "AutoBA", "Agentomics",
              "CellAgent", "GeneAgent", "CRISPR-GPT", "Robin", "BioMedAgent"):
        assert p in projects, f"{p} missing from catalogue"


def test_lookup_and_filtering(registry: CapabilityRegistry) -> None:
    hits = registry.find("single cell clustering", kind="skill", limit=5)
    assert hits and all(h.kind == "skill" for h in hits)

    datasets = registry.find(kind="dataset", limit=200)
    assert len(datasets) >= 70
    assert all(d.kind == "dataset" for d in datasets)

    with pytest.raises(ValueError):
        registry.find(kind="not-a-kind")


def test_license_metadata_is_consistent(registry: CapabilityRegistry) -> None:
    modes = registry.license_summary()
    assert modes.get("vendor", 0) > 0
    assert modes.get("adapter-only", 0) > 0
    # every adapter-only capability must be flagged non-redistributable
    frame = registry.frame
    adapter_only = frame[frame.integration_mode == "adapter-only"]
    assert not adapter_only.empty
    assert (~adapter_only["redistributable"].astype(bool)).all()


# ------------------------------------------------------------------ adapters
def test_license_boundary_is_enforced_in_code() -> None:
    fed = FederatedProcessAdapter(name="OrigeneMCP", project_root=None)
    assert fed.integration_mode == "adapter-only"
    with pytest.raises(LicenseError):
        fed.assert_may_vendor()


def test_unavailable_upstream_fails_loudly_not_silently(registry: CapabilityRegistry) -> None:
    """An uninstalled upstream project must error, never fabricate a result."""
    fed = FederatedProcessAdapter(name="CellAgent", project_root="/nonexistent/path")
    assert not fed.available()
    cap = registry.find(kind="tool", project="CellAgent", limit=1)
    if cap:
        res = fed.invoke(cap[0], code="print(1)")
        assert res.ok is False
        assert "not installed" in (res.error or "")


def test_native_connector_routing(registry: CapabilityRegistry) -> None:
    native = NativeConnectorAdapter()
    frame = registry.frame
    routed = frame[frame["native_connectors"].astype(str).str.len() > 3]
    assert len(routed) > 500, "expected many capabilities to map to native connectors"
    cap = registry.get(str(routed.iloc[0]["name"]))
    assert cap is not None and native.can_handle(cap)
    res = native.invoke(cap)
    # Routing resolved but NOT executed -> RESOLVED, never a success.
    assert res.status.name == "RESOLVED" and not res.ok
    assert res.value["resolved_connector"].startswith("mcp-")


@pytest.mark.integration
def test_datalake_adapter_reads_real_files(registry: CapabilityRegistry) -> None:
    lake = data_lake_dir()
    adapter = DataLakeAdapter(lake)
    if not adapter.available():
        pytest.skip("data lake not present")
    parquet = [c for c in registry.find(kind="dataset", limit=200)
               if c.name.endswith(".parquet") and adapter.can_handle(c)]
    assert parquet, "no loadable parquet dataset found in the lake"
    res = adapter.invoke(parquet[0], nrows=3)
    assert res.ok and res.value["loaded"] and res.value["shape"][0] > 0


@pytest.mark.integration
def test_datalake_does_not_autoload_pickles(registry: CapabilityRegistry) -> None:
    """Pickle loading executes arbitrary code; adapter must return metadata only."""
    lake = data_lake_dir()
    adapter = DataLakeAdapter(lake)
    if not adapter.available():
        pytest.skip("data lake not present")
    pkl = [c for c in registry.find(kind="dataset", limit=200)
           if c.name.endswith(".pkl") and adapter.can_handle(c)]
    if not pkl:
        pytest.skip("no pickle datasets present")
    res = adapter.invoke(pkl[0])
    # DEGRADED: deliberately not loaded (unpickling executes code), and that is
    # now an explicit status rather than a bare ok=True.
    assert res.value["loaded"] is False and res.status.name == "DEGRADED"


# ----------------------------------------------------------------- executor
def test_sandbox_executor_isolates_and_reports() -> None:
    ex = SandboxExecutor(timeout_s=30)
    ok = ex.run("print(6 * 7)")
    assert ok.ok and "42" in ok.stdout

    bad = ex.run("raise ValueError('boom')")
    assert not bad.ok and "boom" in bad.stderr


def test_sandbox_executor_enforces_timeout() -> None:
    ex = SandboxExecutor(timeout_s=1.0)
    slow = ex.run("import time; time.sleep(10)")
    assert slow.timed_out and not slow.ok


# --------------------------------------------------------------- provenance
def test_provenance_records_and_hashes() -> None:
    log = ProvenanceLog(catalogue_version="test")
    log.record(capability="x", adapter="a", integration_mode="vendor",
               inputs={"n": 1}, output={"v": 2}, status="SUCCEEDED")
    assert len(log) == 1
    e = log.entries[0]
    assert e.input_hash.startswith("sha256:") and e.output_hash.startswith("sha256:")


def test_provenance_replay_detects_match_and_drift(tmp_path: Path) -> None:
    log = ProvenanceLog(catalogue_version="test")
    log.record(capability="cap1", adapter="a", integration_mode="vendor",
               inputs={"k": 1}, output={"result": 100}, status="SUCCEEDED")
    trace_path = log.save(tmp_path / "trace.json")
    trace = json.loads(trace_path.read_text())

    same = ProvenanceLog.replay(trace, lambda name, inputs: {"result": 100})
    assert same["fully_reproduced"] and same["n_reproduced"] == 1

    drifted = ProvenanceLog.replay(trace, lambda name, inputs: {"result": 999})
    assert not drifted["fully_reproduced"] and drifted["n_reproduced"] == 0


# ---------------------------------------------------------------- end-to-end
@pytest.mark.integration
def test_agent_runs_end_to_end_with_provenance(registry: CapabilityRegistry) -> None:
    if not data_lake_available():
        pytest.skip("data lake not present")
    lake = data_lake_dir()
    adapters = [DataLakeAdapter(lake), NativeConnectorAdapter()]
    agent = BioAgent(registry, adapters, catalogue_version="v1")
    report = agent.run("drug target binding affinity", max_steps=3)
    assert len(report.plan) > 0
    assert report.provenance is not None
    assert len(report.provenance) == len(report.all_results)
    summary = report.summary()
    assert summary["n_steps"] == len(report.plan)
    assert summary["outcome"] in ("SUCCESS", "PARTIAL_SUCCESS", "DEGRADED", "FAILED")


def test_agent_reports_missing_capability_without_crashing(registry: CapabilityRegistry) -> None:
    agent = BioAgent(registry, [NativeConnectorAdapter()])
    log = ProvenanceLog()
    res = agent.invoke("definitely_not_a_real_capability_xyz", log=log)
    assert not res.ok and "not found" in (res.error or "")
    assert len(log) == 1 and log.entries[0].status == "UNAVAILABLE"
