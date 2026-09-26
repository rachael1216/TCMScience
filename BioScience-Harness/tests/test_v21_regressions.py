"""Regression tests for the ten defects found in the v2 architectural audit.

Each test was confirmed FAILING against the unfixed v2 code before the fix was
written. Letters match the audit: K (security), A, L, G, E, B, C, D, F, H.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from bioagent.runtime.component import ComponentManifest, LicenseSpec, RuntimeSpec, Validation
from bioagent.runtime.registry import ComponentRegistry, Loader
from bioagent.status import LifecycleState
from bioagent.workspace import TrustBoundaryError, Workspace


def mk(cid="t.tool.a", backend="python", entrypoint="json:dumps", version="1.0.0", **kw):
    return ComponentManifest(
        id=cid, kind=kw.pop("kind", "tool"), name=kw.pop("name", cid.split(".")[-1]),
        version=version,
        runtime=RuntimeSpec(backend=backend, entrypoint=entrypoint, server=kw.pop("server", "")),
        license=LicenseSpec(spdx="MIT", integration_mode=kw.pop("mode", "vendor")), **kw)


# ---------------------------------------------------------------- K (security)
def test_K_traversal_inside_root_cannot_reach_immutable_plane() -> None:
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws").init()
    with pytest.raises(TrustBoundaryError):
        ws.write("memory/../kernel/policy.yaml", "allow: all")
    with pytest.raises(TrustBoundaryError):
        ws.write("skills/./../policies/x.yaml", "allow: all")
    assert not (ws.root / "kernel" / "policy.yaml").exists()


def test_K_symlink_out_of_mutable_plane_is_refused() -> None:
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws").init()
    link = ws.root / "skills" / "escape"
    link.symlink_to(ws.root / "kernel")
    with pytest.raises(TrustBoundaryError):
        ws.write("skills/escape/policy.yaml", "allow: all")


# ------------------------------------------------------------------------- A
def test_A_loader_does_not_grant_ready_without_evidence() -> None:
    m = mk("x.tool.m", backend="mcp", entrypoint="q", server="mcp-x", mode="native")
    reg = ComponentRegistry([m])
    Loader(reg).load(m.id)
    assert m.state is not LifecycleState.READY, "READY must be earned by a successful call"


# ------------------------------------------------------------------------- L
def test_L_requarantined_candidate_is_clean_after_promotion() -> None:
    from bioagent.runtime.hmr import HotReloader

    reg = ComponentRegistry([mk()])
    hr = HotReloader(reg, smoke_runner=lambda m: (True, "ok"))
    cand = mk(version="2.0.0", entrypoint="")          # fails schema -> QUARANTINED
    assert not hr.swap(cand).promoted
    cand.runtime.entrypoint = "json:loads"
    r = hr.swap(cand)
    assert r.promoted
    assert reg.get("t.tool.a").state is not LifecycleState.QUARANTINED
    assert reg.get("t.tool.a").blocking_reason == ""


# ------------------------------------------------------------------------- G
def test_G_lazy_set_excludes_components_whose_backend_cannot_run() -> None:
    from bioagent.backends.base import BackendRegistry
    from bioagent.backends.concrete import MCPBackend, PythonBackend
    from bioagent.runtime.hmr import LazyComponentSet

    reg = ComponentRegistry(
        [mk(f"z.tool.q{i}", backend="mcp", entrypoint="q", server="s", name=f"query_x_{i}",
            mode="native") for i in range(4)]
        + [mk("z.tool.py", name="query_x_py")])
    ld = Loader(reg)
    backends = BackendRegistry([PythonBackend(ld), MCPBackend()])   # mcp has no dispatcher
    lz = LazyComponentSet(reg, ld, backends=backends)
    chosen = lz.acquire("query x", top_k=3)
    assert chosen and all(c.runtime.backend != "mcp" for c in chosen)
    assert lz.stats.get("backend_unavailable", 0) >= 4


# ------------------------------------------------------------------------- E
def test_E_blocking_reason_cleared_on_recovery() -> None:
    m = mk()
    m.mark_unavailable("missing foo")
    m.state = LifecycleState.REGISTERED
    m.transition(LifecycleState.AVAILABLE)
    assert m.blocking_reason == ""


# ------------------------------------------------------------------------- B
def test_B_evolution_runs_a_real_test_stage_before_benchmark() -> None:
    from bioagent.evolution import EvolutionAgent, EvolutionPipeline
    from bioagent.evolution.pipeline import BenchmarkResult
    from bioagent.runtime.hmr import HotReloader

    reg = ComponentRegistry([mk()])
    calls: list[str] = []

    def smoke(m):
        calls.append("smoke")
        return False, "smoke test returned wrong shape"

    def bench(m, b):
        calls.append("bench")
        return BenchmarkResult(b, 1.0)

    pipe = EvolutionPipeline(reg, HotReloader(reg, smoke_runner=smoke),
                             benchmark_runner=bench, smoke_runner=smoke)
    p = EvolutionAgent(reg).propose("t.tool.a", changes={
        "validation": {"smoke_test": "smoke", "benchmarks": ["b1"], "last_validated": ""}},
        rationale="x")
    out = pipe.submit(p)
    stages = [s["stage"] for s in out.stage_log]
    assert "test" in stages, f"no test stage executed: {stages}"
    assert stages.index("test") < (stages.index("benchmark") if "benchmark" in stages else 99)
    assert out.state.value == "QUARANTINED" and calls == ["smoke"], calls


# ------------------------------------------------------------------------- C
def test_C_no_walrus_artefact_in_git_diff_call() -> None:
    from bioagent.evolution import pipeline

    src = inspect.getsource(pipeline)
    assert "check :=" not in src


# ------------------------------------------------------------------------- D
def test_D_manifest_yaml_round_trips() -> None:
    m = mk(description="a: tricky # value", version="1.2.3",
           validation=Validation(benchmarks=("b1", "b2")))
    m.permissions.network = ("rest.ensembl.org",)
    p = Path(tempfile.mkdtemp()) / "c.yaml"
    m.save(p)
    back = ComponentManifest.load(p)
    assert back.id == m.id and back.version == "1.2.3"
    assert back.description == "a: tricky # value"
    assert back.validation.benchmarks == ("b1", "b2")
    assert back.permissions.network == ("rest.ensembl.org",)
    assert back.runtime.entrypoint == "json:dumps"


# ------------------------------------------------------------------------- F
def test_F_http_backend_exists_and_reports_status() -> None:
    from bioagent.backends.concrete import HTTPBackend
    from bioagent.status import ExecutionStatus

    from bioagent.runtime.component import Permissions

    b = HTTPBackend(cache_dir=Path(tempfile.mkdtemp()))
    assert b.backend == "http" and b.available()
    m = mk("d.database.x", backend="http", entrypoint="", server="https://127.0.0.1:9/nope",
           kind="database", mode="native",
           # An http component must declare the hosts it contacts; this used to
           # be optional because an empty allowlist skipped the check.
           permissions=Permissions(network=("127.0.0.1",)))
    res = b.invoke(m, path="/x")
    assert res.status in (ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE,
                          ExecutionStatus.TIMEOUT)
    assert res.error


# ------------------------------------------------------------------------- H
def test_H_download_interface_exists_and_verifies_checksum() -> None:
    import hashlib

    from bioagent.acquisition import Downloader, DownloadError

    src = Path(tempfile.mkdtemp()) / "src.bin"
    src.write_bytes(b"0123456789" * 1000)
    good = "sha256:" + hashlib.sha256(src.read_bytes()).hexdigest()
    dl = Downloader(Path(tempfile.mkdtemp()))
    out = dl.fetch(src.as_uri(), "copy.bin", checksum=good)
    assert out.path.exists() and out.verified and out.bytes == 10_000
    with pytest.raises(DownloadError):
        dl.fetch(src.as_uri(), "bad.bin", checksum="sha256:" + "0" * 64)
    assert not (dl.root / "bad.bin").exists(), "failed verification must not leave the file"


# ------------------------------------------------------------ Phase 1: policy
def test_unlisted_host_denied_before_any_request() -> None:
    """A component touching a host outside the profile allowlist never reaches the network."""
    from bioagent.backends.base import BackendRegistry
    from bioagent.backends.http import HTTPBackend
    from bioagent.policy import PolicyKernel
    from bioagent.runtime.agentspec import AgentSpec, Runtime
    from bioagent.runtime.component import Permissions
    from bioagent.status import ExecutionStatus

    m = mk("p.connector.evil", backend="http", entrypoint="", kind="connector", mode="native",
           server="https://exfil.example.invalid", permissions=Permissions(network=("exfil.example.invalid",)))
    reg = ComponentRegistry([m])
    http = HTTPBackend(cache_dir=Path(tempfile.mkdtemp()))
    rt = Runtime(reg, BackendRegistry([http]), kernel=PolicyKernel())
    res = rt.invoke(m.id, spec=AgentSpec(name="t", permission_profile="biomedical-research"), path="/x")
    assert res.status is ExecutionStatus.DENIED
    assert http.stats["requests"] == 0, "request must not leave before the policy ruling"


def test_all_public_sources_are_allowlisted() -> None:
    from bioagent.policy import PROFILES
    from bioagent.providers.public_apis import SOURCES

    prof = PROFILES["biomedical-research"]
    for s in SOURCES:
        assert prof.check_network([s.host]).decision.value == "ALLOW", s.host


# ------------------------------------------------------- Phase 2: acquisition
def _offline_dataset(tmp: Path, name: str = "toy.tsv", body: str = "a\tb\n1\t2\n3\t4\n"):
    """A dataset component whose acquisition URL is a local file:// (no network)."""
    import hashlib

    from bioagent.acquisition.sources import AcquisitionSpec
    from bioagent.runtime.component import Permissions, Requirements

    src = tmp / "remote" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body)
    spec = AcquisitionSpec(src.as_uri(), name, "localhost", "CC0-1.0", "toy", "general",
                           expected_bytes=len(body.encode()),
                           checksum="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    m = mk(f"toy.dataset.{name.replace('.', '_')}", backend="dataset", entrypoint="", kind="dataset",
           mode="native", name=name, requires=Requirements(datasets=(name,)),
           permissions=Permissions(network=("localhost",)))
    m.inputs = {"acquisition": spec.__dict__}
    return m


def test_missing_acquirable_dataset_resolves_fetchable() -> None:
    from bioagent.runtime.registry import Resolver

    tmp = Path(tempfile.mkdtemp())
    m = _offline_dataset(tmp)
    reg = ComponentRegistry([m])
    r = Resolver(reg, dataset_probe=lambda n: False).resolve(m.id)
    assert r.state is LifecycleState.UNAVAILABLE and r.fetchable
    assert "bioagent fetch" in r.fetch_command and "FETCHABLE" in r.reason


def test_auto_fetch_downloads_verifies_and_reads() -> None:
    from bioagent.acquisition import Downloader
    from bioagent.backends import BackendRegistry, DatasetBackend
    from bioagent.policy import PermissionProfile, PolicyKernel, PROFILES
    from bioagent.runtime.agentspec import AgentSpec, Runtime
    from bioagent.runtime.registry import Resolver
    from bioagent.status import ExecutionStatus

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    m = _offline_dataset(tmp)
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: (lake / n).exists())
    profiles = {**PROFILES, "biomedical-research": PermissionProfile(
        name="biomedical-research", allow_network=True, allowed_hosts=frozenset({"localhost"}))}
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]), kernel=PolicyKernel(profiles=profiles),
                 resolver=res, loader=Loader(reg, res), downloader=Downloader(lake))
    # not permitted -> nothing downloaded
    r0 = rt.invoke(m.id, spec=AgentSpec(name="cautious"), nrows=2)
    assert r0.status is ExecutionStatus.UNAVAILABLE and not (lake / "toy.tsv").exists()
    # permitted -> fetched, verified, READY, read
    r1 = rt.invoke(m.id, spec=AgentSpec(name="fetcher", auto_fetch=True), nrows=2)
    assert r1.status is ExecutionStatus.SUCCEEDED and r1.value["shape"] == [2, 2]
    assert reg.get(m.id).state is LifecycleState.READY
    assert (lake / ".downloads.json").exists()


def test_auto_fetch_respects_size_limit_and_reports_why() -> None:
    from bioagent.acquisition import Downloader
    from bioagent.backends import BackendRegistry, DatasetBackend
    from bioagent.policy import PermissionProfile, PolicyKernel, PROFILES
    from bioagent.runtime.agentspec import AgentSpec, Runtime
    from bioagent.runtime.registry import Resolver

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    m = _offline_dataset(tmp, body="x\ty\n" + "1\t2\n" * 5000)
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: (lake / n).exists())
    profiles = {**PROFILES, "biomedical-research": PermissionProfile(
        name="biomedical-research", allow_network=True, allowed_hosts=frozenset({"localhost"}))}
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]), kernel=PolicyKernel(profiles=profiles),
                 resolver=res, loader=Loader(reg, res), downloader=Downloader(lake))
    r = rt.invoke(m.id, spec=AgentSpec(name="capped", auto_fetch=True, auto_fetch_max_bytes=100), nrows=1)
    assert not r.ok and "auto-fetch" in (r.error or "") and "exceeds" in (r.error or "")
    assert not (lake / m.name).exists()


def test_fetch_on_unlisted_host_is_denied_before_download() -> None:
    from bioagent.acquisition import Downloader
    from bioagent.backends import BackendRegistry, DatasetBackend
    from bioagent.policy import PolicyKernel
    from bioagent.runtime.agentspec import Runtime
    from bioagent.runtime.registry import Resolver
    from bioagent.status import ExecutionStatus

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    m = _offline_dataset(tmp)          # host "localhost" is NOT in the real profile
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: False)
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]), kernel=PolicyKernel(),
                 resolver=res, loader=Loader(reg, res), downloader=Downloader(lake))
    r = rt.fetch(m)
    assert r.status is ExecutionStatus.DENIED and not (lake / m.name).exists()
