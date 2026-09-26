"""Regression tests for the eight blocker defects found in the v1 audit.

Each test names the defect it pins. All eight were reproduced empirically before
these tests were written; each must fail against unfixed code and pass after.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from bioagent import BioAgent, CapabilityRegistry, ProvenanceLog, SandboxExecutor
from bioagent.config import catalogue_path
from bioagent.adapters import (
    CallResult,
    DataLakeAdapter,
    FederatedProcessAdapter,
    NativeConnectorAdapter,
)

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = catalogue_path()
pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_csv(CATALOGUE)


# ---------------------------------------------------------------- BUG 1
def test_bug1_undispatched_route_is_not_success(registry: CapabilityRegistry) -> None:
    """A resolved-but-never-dispatched native route must NOT report success.

    v1: NativeConnectorAdapter returned ok=True with dispatched=False, so the
    demo reported 3/3 'successes' having executed nothing.
    """
    from bioagent.status import ExecutionStatus

    agent = BioAgent(registry, [NativeConnectorAdapter()])  # no dispatcher bound
    res = agent.invoke("ChEMBL")
    assert res.status is ExecutionStatus.RESOLVED, f"got {res.status}"
    assert not res.succeeded, "routing without dispatch must not count as success"


def test_bug1_run_with_zero_execution_is_not_accepted(registry: CapabilityRegistry) -> None:
    """A run whose every step merely resolved cannot be 'accepted'."""
    agent = BioAgent(registry, [NativeConnectorAdapter()])
    rep = agent.run("chembl compound bioactivity target", max_steps=3)
    if len(rep.plan) == 0:
        pytest.skip("planner selected no steps for this task")
    assert not rep.ok, "run with no executed step must not be accepted"
    assert rep.outcome in ("FAILED", "PARTIAL_SUCCESS", "DEGRADED")


def test_bug1_dispatcher_bound_does_succeed(registry: CapabilityRegistry) -> None:
    """With a real dispatcher the same call must reach SUCCEEDED."""
    from bioagent.status import ExecutionStatus

    calls: list[tuple] = []

    def dispatcher(connector: str, capability: str, **kw):
        calls.append((connector, capability))
        return {"rows": [{"molecule_chembl_id": "CHEMBL25"}]}

    agent = BioAgent(registry, [NativeConnectorAdapter(dispatcher=dispatcher)])
    res = agent.invoke("ChEMBL")
    assert res.status is ExecutionStatus.SUCCEEDED and res.succeeded
    assert calls, "dispatcher was never called"


# ---------------------------------------------------------------- BUG 2
def test_bug2_provenance_matches_results_across_retries(registry: CapabilityRegistry) -> None:
    """Provenance entries must correspond 1:1 with returned results.

    v1: a failing first attempt left 2 provenance entries but 0 results.
    """

    class AlwaysFails(NativeConnectorAdapter):
        name = "always-fails"

        def invoke(self, capability, **kw):
            from bioagent.status import ExecutionStatus

            return CallResult(
                capability=str(getattr(capability, "name", capability)),
                adapter=self.name,
                status=ExecutionStatus.FAILED,
                error="forced failure",
            )

    agent = BioAgent(registry, [AlwaysFails()])
    rep = agent.run("chembl compound bioactivity target", max_steps=2, max_attempts=2)
    assert len(rep.provenance) == len(rep.all_results), (
        f"provenance={len(rep.provenance)} results={len(rep.all_results)}"
    )
    attempts = {e.attempt for e in rep.provenance}
    assert attempts, "provenance entries must record their attempt number"


# ---------------------------------------------------------------- BUG 3
def test_bug3_license_policy_blocks_vendoring_in_call_path(registry: CapabilityRegistry) -> None:
    """The license boundary must be enforced during invocation, not only in a test.

    v1: assert_may_vendor() existed but was never called by any code path.
    """
    from bioagent.policy import LicensePolicy, PolicyDecision

    pol = LicensePolicy()
    d_none = pol.check(license_spdx="NONE", integration_mode="vendor")
    assert d_none.decision is PolicyDecision.DENY
    d_mit = pol.check(license_spdx="MIT", integration_mode="vendor")
    assert d_mit.decision is PolicyDecision.ALLOW
    d_fed = pol.check(license_spdx="NONE", integration_mode="federated")
    assert d_fed.decision is PolicyDecision.ALLOW


def test_bug3_agent_denies_unlicensed_vendor_execution(registry: CapabilityRegistry) -> None:
    """An adapter-only capability routed to a vendor backend must be DENIED."""
    from bioagent.status import ExecutionStatus

    class PretendVendor(NativeConnectorAdapter):
        name = "pretend-vendor"
        integration_mode = "vendor"

        def can_handle(self, capability) -> bool:
            return True

        def invoke(self, capability, **kw):
            return CallResult(capability=str(getattr(capability, "name", capability)),
                              adapter=self.name, status=ExecutionStatus.SUCCEEDED,
                              value={"leaked": True})

    frame = registry.frame
    unlicensed = frame[frame.integration_mode == "adapter-only"]["name"].iloc[0]
    agent = BioAgent(registry, [PretendVendor()])
    res = agent.invoke(str(unlicensed))
    assert res.status is ExecutionStatus.DENIED, f"got {res.status}"
    assert res.value is None, "denied call must not return upstream data"


# ---------------------------------------------------------------- BUG 4
def test_bug4_executor_enforces_limits_it_claims() -> None:
    """Every cap the executor REPORTS as enforced must actually be enforced.

    v1 provided only a timeout while being named `SandboxExecutor`. The fix is not
    "always cap memory" — this sandbox refuses to lower RLIMIT_AS at all — but
    "never claim a cap that does not hold". So the contract under test is
    agreement between `guarantees()['enforced']` and observed behaviour.
    """
    from bioagent.core.executor import HardenedExecutor

    ex = HardenedExecutor(timeout_s=45, max_memory_mb=128, max_cpu_s=2, max_file_mb=1)
    enforced = ex.guarantees()["enforced"]

    # at least one real limit must be in force, else this is not a hardened executor
    assert any(v is not None for v in enforced.values()), enforced

    if enforced["file_size_cap_mb"]:
        r = ex.run("open('big.bin', 'wb').write(b'0' * (5 * 1024 * 1024))\nprint('wrote')")
        assert not r.ok and "wrote" not in r.stdout, "file-size cap claimed but not enforced"

    if enforced["cpu_cap_s"]:
        r = ex.run("s = 0\nwhile True:\n    s += 1")
        assert not r.ok, "cpu cap claimed but not enforced"

    if enforced["memory_cap_mb"]:
        r = ex.run("x = bytearray(600 * 1024 * 1024)\nprint('allocated')")
        assert not r.ok, "memory cap claimed but not enforced"


def test_bug4_unenforceable_limits_are_not_claimed() -> None:
    """A cap the platform refuses must be reported as unenforced, not silently dropped."""
    from bioagent.core.executor import HardenedExecutor

    ex = HardenedExecutor(timeout_s=20, max_memory_mb=64)
    g = ex.guarantees()
    enf = g["enforceable_on_this_machine"]
    assert g["requested"]["memory_cap_mb"] == 64
    if not enf.get("memory"):
        assert g["enforced"]["memory_cap_mb"] is None, (
            "memory cap must not be reported as enforced when the platform refuses it"
        )


def test_bug4_executor_scrubs_environment() -> None:
    """Secrets in the parent environment must not reach executed code."""
    from bioagent.core.executor import HardenedExecutor

    os.environ["BIOAGENT_FAKE_SECRET"] = "super-secret-value"
    try:
        ex = HardenedExecutor(timeout_s=30)
        r = ex.run("import os; print(os.environ.get('BIOAGENT_FAKE_SECRET', 'ABSENT'))")
        assert "super-secret-value" not in r.stdout
        assert "ABSENT" in r.stdout
    finally:
        del os.environ["BIOAGENT_FAKE_SECRET"]


def test_bug4_executor_reports_isolation_guarantees() -> None:
    """The executor must state what it does and does not guarantee."""
    from bioagent.core.executor import HardenedExecutor

    g = HardenedExecutor(timeout_s=5).guarantees()
    assert g["container_isolation"] is False
    assert g["resource_limits"] is True
    assert "network" in g


# ---------------------------------------------------------------- BUG 5
def test_bug5_json_dataset_is_parsed_as_json() -> None:
    """.json was declared loadable but routed through read_csv."""
    d = tempfile.mkdtemp()
    payload = {"records": [{"gene": "TP53", "score": 0.9}, {"gene": "BRCA1", "score": 0.7}]}
    Path(d, "x.json").write_text(json.dumps(payload))

    class Cap:
        kind = "dataset"
        name = "x.json"

    r = DataLakeAdapter(d).invoke(Cap(), nrows=2)
    assert r.ok, r.error
    assert r.value["format"] == "json"
    assert r.value.get("parsed_as") == "json", f"got {r.value.get('parsed_as')}"


# ---------------------------------------------------------------- BUG 6
def test_bug6_parquet_is_streamed_not_fully_loaded() -> None:
    """A head() request must not materialize the whole parquet file."""
    import numpy as np

    d = tempfile.mkdtemp()
    p = Path(d, "big.parquet")
    pd.DataFrame({"a": np.arange(400_000), "b": np.random.rand(400_000)}).to_parquet(p)

    class Cap:
        kind = "dataset"
        name = "big.parquet"

    import tracemalloc

    tracemalloc.start()
    r = DataLakeAdapter(d).invoke(Cap(), nrows=3)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert r.ok, r.error
    assert r.value["shape"][0] == 3
    full = p.stat().st_size
    assert peak < full, f"peak {peak} >= file size {full}: file was fully loaded"


# ---------------------------------------------------------------- BUG 7
def test_bug7_no_absolute_paths_in_source() -> None:
    """No source or test file may hardcode a machine-specific absolute path."""
    # Build the needles at runtime so this detector is not its own match.
    needles = ("/" + "Users" + "/", "C:" + chr(92) + "Users")
    offenders = []
    targets = [f for sub in ("src", "tests") for f in (REPO / sub).rglob("*.py")]
    targets += [REPO / "demo_run.py"]
    for f in targets:
        if not f.exists() or f.name == Path(__file__).name:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        if any(n in txt for n in needles):
            offenders.append(str(f.relative_to(REPO)))
    assert not offenders, f"absolute paths found in: {offenders}"


# ---------------------------------------------------------------- BUG 8
@pytest.mark.skipif(os.environ.get("BIOAGENT_NESTED_PYTEST") == "1",
                    reason="guard against recursive pytest invocation")
def test_bug8_unit_tier_needs_no_data_lake() -> None:
    """The unit tier must pass without the 15GB lake present."""
    env = dict(os.environ)
    env["BIOAGENT_NESTED_PYTEST"] = "1"
    env["BIOAGENT_DATA_LAKE"] = str(Path(tempfile.mkdtemp()) / "definitely-absent")
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "unit", "--no-header", "-p", "no:cacheprovider",
         "--deselect", "tests/test_regressions.py::test_bug8_unit_tier_needs_no_data_lake",
         str(REPO / "tests")],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, f"unit tier failed without lake:\n{proc.stdout[-2500:]}"
