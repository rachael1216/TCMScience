"""Regression tests for the v2.2 trusted-core review.

Every test here was confirmed FAILING against the code as reviewed, and each one
pins one finding:

* **auto-fetch profile bypass** — an offline agent completed a download and was
  only then denied, so the bytes were already on disk.
* **filesystem capabilities declared but not enforced** — the profile carried
  `allow_filesystem_read/write` and `authorize()` never looked at them.
* **policy was not lineage-propagating** — a permissively licensed tool whose
  `requires` named a denied dataset was authorized on its own merits.
* **execution success reported as scientific acceptance** — a validator that ran
  cleanly and rejected the result still produced `outcome=SUCCESS, ok=True`.
* **container availability hardcoded** — the resolver asserted no container
  runtime existed regardless of the machine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bioagent.adapters.base import CallResult
from bioagent.backends.base import Backend, BackendRegistry
from bioagent.planners.base import Critique, Plan
from bioagent.policy import (PROFILES, AuthorizationRequest, DependencyContext,
                             PermissionProfile, PolicyKernel)
from bioagent.runtime.agentspec import AgentSpec, RunReport, Runtime
from bioagent.runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                        Requirements, RuntimeSpec)
from bioagent.runtime.registry import ComponentRegistry, Loader, Resolver, default_backend_probe
from bioagent.status import ExecutionStatus, LifecycleState, RunOutcome, ScientificVerdict


#: A host the "biomedical-research" profile allows and "offline-analysis" does not.
ALLOWLISTED_HOST = "ftp.ebi.ac.uk"


def dataset_manifest(tmp: Path, *, cid="toy.dataset.toy_tsv", name="toy.tsv",
                     host="localhost") -> ComponentManifest:
    """A dataset component whose acquisition URL is a local file:// (no network).

    The URL never leaves the machine, so any download that happens is proof the
    policy gate let it through rather than proof of connectivity.
    """
    import hashlib

    from bioagent.acquisition.sources import AcquisitionSpec

    body = "a\tb\n1\t2\n"
    src = tmp / "remote" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body, encoding="utf-8")
    acq = AcquisitionSpec(
        src.as_uri(), name, host, "CC0-1.0", "toy dataset", "general",
        expected_bytes=len(body.encode()),
        checksum="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    m = ComponentManifest(
        id=cid, kind="dataset", name=name,
        runtime=RuntimeSpec(backend="dataset"),
        requires=Requirements(datasets=(name,)),
        permissions=Permissions(network=(host,)),
        license=LicenseSpec(spdx="MIT", integration_mode="native"))
    m.inputs = {"acquisition": acq.__dict__}
    return m


# ------------------------------------------------- auto-fetch profile bypass
def test_auto_fetch_runs_under_the_spec_profile_not_a_hardcoded_one() -> None:
    """An offline-analysis agent must not reach the network through auto-fetch.

    `Runtime.fetch()` authorized with `profile="biomedical-research"` regardless
    of the spec, so the download completed and the *later* invoke was denied —
    the policy violation had already happened on disk.
    """
    from bioagent.acquisition.downloader import Downloader
    from bioagent.backends.concrete import DatasetBackend

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    # ALLOWLISTED_HOST is permitted by "biomedical-research" — the profile the
    # old code hardcoded — and forbidden by "offline-analysis". Without that
    # asymmetry the download would be blocked by the host allowlist and the test
    # would pass against the unfixed code.
    m = dataset_manifest(tmp, host=ALLOWLISTED_HOST)
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: (lake / n).exists())
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]), kernel=PolicyKernel(),
                 resolver=res, loader=Loader(reg, res), downloader=Downloader(lake))
    assert PolicyKernel().profile("biomedical-research").check_network(
        [ALLOWLISTED_HOST]).allowed, "test fixture no longer discriminates"

    spec = AgentSpec(name="offline", permission_profile="offline-analysis", auto_fetch=True)
    result = rt.invoke(m.id, spec=spec, nrows=1)

    assert not (lake / m.name).exists(), "offline profile still downloaded the file"
    assert not result.ok
    fetched = rt.fetch(m, profile="offline-analysis")
    assert fetched.status is ExecutionStatus.DENIED
    assert "forbids network" in (fetched.error or "")


def test_fetch_without_a_profile_fails_closed() -> None:
    """Forgetting to pass a profile must refuse the download, not widen it."""
    from bioagent.acquisition.downloader import Downloader
    from bioagent.backends.concrete import DatasetBackend

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    m = dataset_manifest(tmp, host=ALLOWLISTED_HOST)
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: False)
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]), kernel=PolicyKernel(),
                 resolver=res, loader=Loader(reg, res), downloader=Downloader(lake))

    assert rt.fetch(m).status is ExecutionStatus.DENIED
    assert not (lake / m.name).exists()


def test_auto_fetch_is_permitted_when_the_spec_profile_permits_the_host() -> None:
    """The fix must not block a fetch the agent's own profile actually allows."""
    from bioagent.acquisition.downloader import Downloader
    from bioagent.backends.concrete import DatasetBackend

    tmp = Path(tempfile.mkdtemp())
    lake = tmp / "lake"
    lake.mkdir()
    m = dataset_manifest(tmp)
    profiles = {**PROFILES, "lab": PermissionProfile(
        name="lab", allow_network=True, allowed_hosts=frozenset({"localhost"}),
        allow_filesystem_read=frozenset({"${tmp}"}),
        allow_filesystem_write=frozenset({"${tmp}"}))}
    reg = ComponentRegistry([m])
    res = Resolver(reg, dataset_probe=lambda n: (lake / n).exists())
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]),
                 kernel=PolicyKernel(profiles=profiles), resolver=res,
                 loader=Loader(reg, res), downloader=Downloader(lake))

    spec = AgentSpec(name="lab-agent", permission_profile="lab", auto_fetch=True)
    rt.invoke(m.id, spec=spec, nrows=1)
    assert (lake / m.name).exists(), "an explicitly permitted host was blocked"


# --------------------------------------------- filesystem capability checking
def test_declared_filesystem_write_outside_profile_roots_is_denied() -> None:
    kernel = PolicyKernel()
    auth = kernel.authorize(AuthorizationRequest(
        component_id="t.tool.writer", license_spdx="MIT", integration_mode="native",
        backend="subprocess", profile="biomedical-research",
        filesystem_write=("/etc/cron.d/agent",)))
    assert not auth.allowed
    assert "perm.fs_write.outside_root" in auth.denied_rules


def test_declared_filesystem_read_outside_profile_roots_is_denied() -> None:
    kernel = PolicyKernel()
    auth = kernel.authorize(AuthorizationRequest(
        component_id="t.tool.reader", license_spdx="MIT", integration_mode="native",
        backend="subprocess", profile="offline-analysis",
        filesystem_read=("/root/.ssh/id_rsa",)))
    assert not auth.allowed
    assert "perm.fs_read.outside_root" in auth.denied_rules


def test_parent_traversal_cannot_escape_an_allowed_root() -> None:
    prof = PermissionProfile(name="p", allow_filesystem_write=frozenset({"${tmp}"}))
    escape = str(Path(tempfile.gettempdir()) / ".." / ".." / "etc" / "passwd")
    assert not prof.check_filesystem([escape], mode="write").allowed
    inside = str(Path(tempfile.gettempdir()) / "run" / "out.csv")
    assert prof.check_filesystem([inside], mode="write").allowed


def test_profile_with_no_roots_grants_no_filesystem_access() -> None:
    prof = PermissionProfile(name="bare")
    assert prof.check_filesystem(["/tmp/x"], mode="read").decision.value == "DENY"
    assert prof.check_filesystem([], mode="read").allowed, "no request must stay allowed"


def test_writes_are_refused_on_a_backend_that_cannot_confine_them() -> None:
    """The in-process python backend can enforce no write boundary, so it is refused.

    Recording a filesystem permission that nothing enforces is what turned
    "policy-enforced" into "policy-declared"; the kernel now declines the
    combination and names the backends that can hold the boundary.
    """
    kernel = PolicyKernel()
    workspace_file = str(Path(tempfile.gettempdir()) / "out.csv")
    denied = kernel.authorize(AuthorizationRequest(
        component_id="t.tool.inproc", license_spdx="MIT", integration_mode="native",
        backend="python", profile="sandbox-only", filesystem_write=(workspace_file,)))
    assert not denied.allowed
    assert "perm.fs_confinement.unconfined_backend" in denied.denied_rules

    allowed = kernel.authorize(AuthorizationRequest(
        component_id="t.tool.subproc", license_spdx="MIT", integration_mode="native",
        backend="subprocess", profile="sandbox-only", filesystem_write=(workspace_file,)))
    assert allowed.allowed


def test_enforcement_report_distinguishes_mechanism_from_declaration() -> None:
    """The kernel must not let a caller mistake a gate for a sandbox."""
    report = PolicyKernel().enforcement_report("biomedical-research")
    caps = report["capabilities"]
    assert caps["filesystem_read"].startswith("declaration")
    assert caps["filesystem_write"].startswith("mechanism")
    assert report["lineage_propagating"] is True
    assert "python" in report["unconfined_backends"]


# ------------------------------------------------------ lineage-propagating policy
def test_a_tool_cannot_launder_access_to_a_denied_dataset() -> None:
    """The finding in its original shape: dataset DENY, consuming tool ALLOW."""
    kernel = PolicyKernel()
    dep = DependencyContext(
        component_id="ds.restricted", kind="dataset", license_spdx="MIT",
        integration_mode="native", network_hosts=("exfil.example.invalid",),
        path="tool.consumer -> ds.restricted")
    auth = kernel.authorize(AuthorizationRequest(
        component_id="tool.consumer", license_spdx="MIT", integration_mode="vendor",
        backend="python", profile="offline-analysis", dependencies=(dep,)))
    assert not auth.allowed
    assert any(r.startswith("lineage.") for r in auth.denied_rules)
    assert "ds.restricted" in auth.reason


def test_unlicensed_dependency_denies_a_permissively_licensed_parent() -> None:
    kernel = PolicyKernel()
    dep = DependencyContext(component_id="ds.norights", kind="dataset",
                            license_spdx="NONE", integration_mode="vendor",
                            path="tool.parent -> ds.norights")
    auth = kernel.authorize(AuthorizationRequest(
        component_id="tool.parent", license_spdx="MIT", integration_mode="vendor",
        backend="python", profile="biomedical-research", dependencies=(dep,)))
    assert not auth.allowed


def test_runtime_propagates_lineage_from_the_registry() -> None:
    """End to end: the runtime must build the closure, not just accept one."""
    reg = ComponentRegistry()
    ds = ComponentManifest(
        id="ds.restricted", kind="dataset", name="restricted",
        runtime=RuntimeSpec(backend="dataset"),
        license=LicenseSpec(spdx="MIT", integration_mode="native"),
        permissions=Permissions(network=("exfil.example.invalid",)))
    tool = ComponentManifest(
        id="tool.consumer", kind="tool", name="consumer",
        runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
        license=LicenseSpec(spdx="MIT", integration_mode="vendor"),
        requires=Requirements(datasets=("ds.restricted",)))
    reg.add(ds)
    reg.add(tool)
    res = Resolver(reg, dataset_probe=lambda _n: True)
    rt = Runtime(reg, BackendRegistry(), resolver=res, loader=Loader(reg, res))

    ctxs = res.dependency_contexts("tool.consumer")
    assert [c.component_id for c in ctxs] == ["ds.restricted"]

    spec = AgentSpec(name="a", permission_profile="offline-analysis")
    auth = rt.kernel.authorize(rt._authorization_request(tool, spec))
    assert not auth.allowed, "the consuming tool was authorized despite a denied dependency"


def test_transitive_dependencies_are_followed_and_cycles_terminate() -> None:
    reg = ComponentRegistry()
    for cid, deps in (("c.leaf", ()), ("c.mid", ("c.leaf",)), ("c.top", ("c.mid",))):
        reg.add(ComponentManifest(
            id=cid, kind="tool", name=cid,
            runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
            license=LicenseSpec(spdx="MIT", integration_mode="vendor"),
            requires=Requirements(components=deps)))
    res = Resolver(reg)
    assert {c.component_id for c in res.dependency_contexts("c.top")} == {"c.mid", "c.leaf"}

    # a cycle must not hang or recurse forever
    reg.add(ComponentManifest(
        id="c.leaf", kind="tool", name="c.leaf",
        runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
        license=LicenseSpec(spdx="MIT", integration_mode="vendor"),
        requires=Requirements(components=("c.top",))))
    assert {c.component_id for c in res.dependency_contexts("c.top")} == {"c.mid", "c.leaf"}


def test_a_missing_dependency_fails_closed() -> None:
    reg = ComponentRegistry()
    reg.add(ComponentManifest(
        id="tool.orphan", kind="tool", name="orphan",
        runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
        license=LicenseSpec(spdx="MIT", integration_mode="vendor"),
        requires=Requirements(datasets=("ds.absent",))))
    res = Resolver(reg)
    ctxs = res.dependency_contexts("tool.orphan")
    assert [c.component_id for c in ctxs] == ["ds.absent"]
    assert ctxs[0].license_spdx is None
    auth = PolicyKernel().authorize(AuthorizationRequest(
        component_id="tool.orphan", license_spdx="MIT", integration_mode="vendor",
        backend="python", profile="biomedical-research", dependencies=ctxs))
    assert not auth.allowed, "an unresolvable dependency must not be silently ignored"


def test_a_dataset_self_reference_is_not_treated_as_lineage() -> None:
    """Catalogue-derived datasets list their own name in `requires.datasets`."""
    reg = ComponentRegistry()
    reg.add(ComponentManifest(
        id="proj.dataset.toy", kind="dataset", name="toy.tsv",
        runtime=RuntimeSpec(backend="dataset"),
        license=LicenseSpec(spdx="MIT", integration_mode="native"),
        requires=Requirements(datasets=("toy.tsv",))))
    assert Resolver(reg).dependency_contexts("proj.dataset.toy") == ()


# ------------------------------- execution outcome vs scientific verdict
def _report(status: ExecutionStatus, critique: Critique | None) -> RunReport:
    return RunReport(task="t", spec=AgentSpec(name="a"), plan=Plan(task="t"),
                     results=[CallResult(capability="evaluator", adapter="python-backend",
                                         status=status, value={"score": 0.0})],
                     critique=critique)


def test_a_rejected_critique_cannot_report_an_accepted_run() -> None:
    """`CitationVerifier ran` and `the citations check out` are different claims."""
    rep = _report(ExecutionStatus.SUCCEEDED,
                  Critique(accepted=False, reason="validation failed: score 0.0"))
    assert rep.execution_outcome == RunOutcome.SUCCESS.value
    assert rep.verdict == ScientificVerdict.REJECTED.value
    assert rep.ok is False
    summary = rep.summary()
    assert summary["execution_outcome"] == RunOutcome.SUCCESS.value
    assert summary["scientific_verdict"] == ScientificVerdict.REJECTED.value
    assert summary["accepted"] is False


def test_an_accepted_critique_over_a_clean_run_is_accepted() -> None:
    rep = _report(ExecutionStatus.SUCCEEDED, Critique(accepted=True, reason="all steps succeeded"))
    assert rep.execution_outcome == RunOutcome.SUCCESS.value
    assert rep.verdict == ScientificVerdict.ACCEPTED.value
    assert rep.ok is True


def test_no_critique_is_inconclusive_not_accepted() -> None:
    rep = _report(ExecutionStatus.SUCCEEDED, None)
    assert rep.verdict == ScientificVerdict.INCONCLUSIVE.value
    assert rep.ok is False


def test_acceptance_cannot_outrun_a_failed_execution() -> None:
    rep = _report(ExecutionStatus.FAILED, Critique(accepted=True, reason="looks fine"))
    assert rep.execution_outcome == RunOutcome.FAILED.value
    assert rep.ok is False


def test_the_legacy_agent_report_separates_the_two_as_well() -> None:
    from bioagent.agent import RunReport as LegacyRunReport
    from bioagent.core.planner import Critique as LegacyCritique, Plan as LegacyPlan

    rep = LegacyRunReport(
        task="t", plan=LegacyPlan(task="t"),
        results=[CallResult(capability="e", adapter="a", status=ExecutionStatus.SUCCEEDED)],
        critique=LegacyCritique(accepted=False, reason="validation failed"))
    assert rep.execution_outcome == RunOutcome.SUCCESS.value
    assert rep.verdict == ScientificVerdict.REJECTED.value
    assert rep.ok is False
    assert rep.summary()["accepted"] is False


# ------------------------------------------------ container availability probe
def test_resolver_measures_container_availability_instead_of_asserting() -> None:
    reg = ComponentRegistry()
    reg.add(ComponentManifest(
        id="c.tool.boxed", kind="tool", name="boxed",
        runtime=RuntimeSpec(backend="container", image="example/img:1"),
        license=LicenseSpec(spdx="MIT", integration_mode="federated")))

    present = Resolver(reg, backend_probe=lambda b: (True, ""))
    assert present.resolve("c.tool.boxed").state is not LifecycleState.UNAVAILABLE

    absent = Resolver(reg, backend_probe=lambda b: (False, "no container runtime found"))
    res = absent.resolve("c.tool.boxed")
    assert res.state is LifecycleState.UNAVAILABLE
    assert "no container runtime found" in res.reason


def test_default_backend_probe_reflects_the_machine(monkeypatch) -> None:
    """The probe measures the machine — and "installed" is not "usable".

    It used to assert that a docker binary on PATH makes the container backend available.
    That is the defect: ``shutil.which`` finds the *client*, and a client with no runtime
    behind it cannot run anything. The three cases below are the ones that differ.
    """
    import subprocess as sp

    import bioagent.runtime.registry as reg_mod

    def set_machine(which, run):
        # The probe is memoised — it spawns a process and the resolver asks once per
        # component — so a test that changes the machine has to invalidate it.
        monkeypatch.setattr(reg_mod, "_CONTAINER_PROBE", None)
        monkeypatch.setattr(reg_mod.shutil, "which", which)
        monkeypatch.setattr(reg_mod.subprocess, "run", run)

    ok_run = lambda *a, **k: sp.CompletedProcess(a[0] if a else [], 0, "Server: ok", "")
    dead_run = lambda *a, **k: sp.CompletedProcess(
        a[0] if a else [], 1, "", "failed to connect to the docker API")
    has_docker = lambda name: "/usr/bin/docker" if name == "docker" else None

    # 1. CLI present and the runtime answers -> available.
    set_machine(has_docker, ok_run)
    assert default_backend_probe("container")[0] is True

    # 2. CLI present and NO runtime behind it -> unavailable, and the reason says which.
    set_machine(has_docker, dead_run)
    ok, reason = default_backend_probe("container")
    assert ok is False
    assert "not usable" in reason, reason

    # 3. No CLI at all -> unavailable, with the older message.
    set_machine(lambda name: None, ok_run)
    ok, reason = default_backend_probe("container")
    assert ok is False and "none found" in reason

    assert default_backend_probe("none")[0] is False
    assert default_backend_probe("python")[0] is True


def test_runtime_asks_its_own_backends_whether_a_mechanism_can_run() -> None:
    class DeadBackend(Backend):
        backend = "container"

        def available(self) -> bool:
            return False

        def unavailable_reason(self) -> str:
            return "container runtime disabled for this runtime"

        def invoke(self, manifest, **kwargs):  # pragma: no cover - never reached
            raise AssertionError("must not be invoked")

    reg = ComponentRegistry()
    reg.add(ComponentManifest(
        id="c.tool.boxed", kind="tool", name="boxed",
        runtime=RuntimeSpec(backend="container", image="example/img:1"),
        license=LicenseSpec(spdx="MIT", integration_mode="federated")))
    rt = Runtime(reg, BackendRegistry([DeadBackend()]))
    res = rt.resolver.resolve("c.tool.boxed")
    assert res.state is LifecycleState.UNAVAILABLE
    assert "disabled for this runtime" in res.reason


def test_a_declared_subprocess_permission_is_gated_on_any_backend() -> None:
    """A python-backend component that declares subprocess must still be gated.

    The check keyed only on `backend == "subprocess"`, so a component declaring
    `permissions.subprocess = True` on another backend was never examined.
    """
    kernel = PolicyKernel()
    auth = kernel.authorize(AuthorizationRequest(
        component_id="t.tool.shells_out", license_spdx="MIT", integration_mode="native",
        backend="python", profile="offline-analysis", subprocess=True))
    assert not auth.allowed
    assert "perm.subprocess.denied" in auth.denied_rules


def test_registry_name_lookup_survives_later_additions() -> None:
    """The lazy name index must be invalidated by every add(), quarantine included."""
    reg = ComponentRegistry()
    reg.add(ComponentManifest(
        id="p.dataset.one", kind="dataset", name="shared.tsv",
        runtime=RuntimeSpec(backend="dataset"),
        license=LicenseSpec(spdx="MIT", integration_mode="native")))
    assert [m.id for m in reg.by_name("shared.tsv")] == ["p.dataset.one"]

    reg.add(ComponentManifest(id="bad id with spaces", kind="tool", name="quarantined",
                              runtime=RuntimeSpec(backend="python", entrypoint="json:dumps")))
    reg.add(ComponentManifest(
        id="p.dataset.two", kind="dataset", name="shared.tsv",
        runtime=RuntimeSpec(backend="dataset"),
        license=LicenseSpec(spdx="MIT", integration_mode="native")))
    assert {m.id for m in reg.by_name("shared.tsv")} == {"p.dataset.one", "p.dataset.two"}
    assert [m.id for m in reg.by_name("quarantined")] == ["bad id with spaces"]
