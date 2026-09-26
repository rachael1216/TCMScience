"""PSH ⊕ BioScience: the capability plane under the trusted kernel.

Every test here makes one of three claims. The bridge derives the PSH manifest that the
gates need, from what the BioScience manifest declares, with the conservative reading
of every dimension. A call crosses both kernels in order and neither can be skipped.
And the trusted plane is immutable from inside: nothing in ``bioagent`` reaches into
``psh.kernel``, and a self-evolution proposal that targets it is quarantined before it is
tested.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

psh = pytest.importorskip("psh", reason="PSH-Harness is not importable here")

from psh.capabilities import CapabilityRegistry  # noqa: E402
from psh.config import PSHConfig  # noqa: E402
from psh.contracts import (  # noqa: E402
    Autonomy, CapabilityUnavailable, ComponentKind, ContractViolation, EgressDenied,
    PolicyDenied, RiskTier,
)
from psh.kernel import TrustedKernel  # noqa: E402
from psh.labels import Destination, Sensitivity  # noqa: E402
from psh.licensing import classify_license  # noqa: E402
from psh.policy import PolicySnapshot  # noqa: E402

import bioagent  # noqa: E402
from bioagent.backends.http import HTTPBackend  # noqa: E402
from bioagent.evolution.boundary import BoundaryViolation, KernelBoundary  # noqa: E402
from bioagent.evolution.pipeline import EvolutionAgent, EvolutionPipeline  # noqa: E402
from bioagent.providers.public_apis import BY_KEY, PublicAPIProvider  # noqa: E402
from bioagent.psh import (  # noqa: E402
    BioScienceBridge, BridgeRefused, HostPolicy, bridge_manifest, default_runtime,
    normalise_spdx,
)
from bioagent.runtime.agentspec import AgentSpec  # noqa: E402
from bioagent.runtime.component import (  # noqa: E402
    ComponentManifest, LicenseSpec, Permissions, Provider, RuntimeSpec,
)
from bioagent.runtime.hmr import HotReloader  # noqa: E402
from bioagent.runtime.registry import ComponentRegistry  # noqa: E402
from bioagent.status import ExecutionStatus  # noqa: E402

PHI_TEXT = "Patient Alice Smith MRN 04851923 admitted with chest pain"
OPEN = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
        Destination.PERSISTENT, Destination.PUBLIC_REMOTE)


def policy(**kw):
    params = dict(profile_id="bridge_bench", max_data_label=Sensitivity.PHI,
                  allowed_destinations=OPEN, autonomy=Autonomy.ACT,
                  risk_ceiling=RiskTier.R3_CLINICAL, require_claim_support=False)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=policy())
    yield k
    k.close()


@pytest.fixture
def runtime(tmp_path):
    """Connectors only: the catalogue is not needed to prove the crossing."""
    return default_runtime(catalogue=False, public_apis=True, native_tools=False,
                           cache_dir=None, data_lake=tmp_path / "no-lake")


CONNECTORS = {m.id.rsplit(".", 1)[-1]: m for m in PublicAPIProvider().discover()}


def local_tool(cid="local.tool.echo", **kw) -> ComponentManifest:
    params = dict(id=cid, kind="tool", name="echo", description="returns its arguments",
                  runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
                  license=LicenseSpec(spdx="MIT", integration_mode="native"))
    params.update(kw)
    return ComponentManifest(**params)


class FakeTransport:
    """Stands in for the network: records requests, returns a canned body."""

    def __init__(self, body=None):
        self.requests = []
        self.body = body if body is not None else {"id": "ENSG00000141510", "display_name": "TP53"}

    def __call__(self, backend, req, *, use_cache=True):
        self.requests.append(req)
        return (ExecutionStatus.SUCCEEDED, self.body, "",
                {"cached": False, "attempts": 1, "http_status": 200})


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(HTTPBackend, "request",
                        lambda self, req, use_cache=True: fake(self, req, use_cache=use_cache))
    return fake


# ============================================ manifest derivation

def test_a_connector_is_a_public_remote_tool_with_a_deidentified_ceiling():
    m = bridge_manifest(CONNECTORS["ensembl"], operations=BY_KEY["ensembl"].operations)
    assert m.id == "public.connector.ensembl"
    assert m.kind is ComponentKind.TOOL
    assert m.destinations == (Destination.PUBLIC_REMOTE,)
    assert m.max_label is Sensitivity.RESEARCH_DEIDENTIFIED
    assert m.requires_network and m.allowed_hosts == ("rest.ensembl.org",)
    assert m.license_spdx == "Apache-2.0" and m.integration_mode == "native"
    assert m.risk_tier is RiskTier.R1_ROUTINE and not m.mutates and m.idempotent
    assert m.input_schema["required"] == ["operation"]
    assert "gene_lookup" in m.input_schema["properties"]["operation"]["enum"]
    assert m.provenance["bio_backend"] == "http"


def test_a_trusted_host_is_the_operators_decision_not_the_sources():
    trusted = HostPolicy(trusted_hosts=frozenset({"rest.ensembl.org"}))
    m = bridge_manifest(CONNECTORS["ensembl"], host_policy=trusted)
    assert m.destinations == (Destination.TRUSTED_REMOTE,)
    assert bridge_manifest(CONNECTORS["ensembl"]).destinations == (Destination.PUBLIC_REMOTE,)


def test_a_local_tool_keeps_the_local_ceiling_until_it_declares_a_host():
    local = bridge_manifest(local_tool())
    assert local.destinations == (Destination.LOCAL_COMPUTE,)
    assert local.max_label is Sensitivity.PHI
    assert not local.requires_network and local.min_autonomy is Autonomy.OBSERVE

    networked = bridge_manifest(local_tool(permissions=Permissions(network=("api.example.org",))))
    assert networked.destinations == (Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE)
    assert networked.max_label is Sensitivity.RESEARCH_DEIDENTIFIED
    assert networked.requires_network

    writer = bridge_manifest(local_tool(permissions=Permissions(filesystem_write=("out/",))))
    assert writer.mutates and writer.risk_tier is RiskTier.R2_CONSEQUENTIAL
    assert Destination.PERSISTENT in writer.destinations
    assert writer.min_autonomy is Autonomy.ACT_WITH_APPROVAL and not writer.idempotent


def test_licence_strings_are_normalised_conservatively():
    assert normalise_spdx("Public-domain (US Gov)") == "US-Gov-Public-Domain"
    assert classify_license("US-Gov-Public-Domain") == "permissive"
    assert normalise_spdx("CC-BY-SA-3.0") == "CC-BY-SA-3.0"
    assert classify_license("CC-BY-SA-3.0") == "copyleft"
    assert normalise_spdx("Academic use free; commercial requires license") == ""
    assert normalise_spdx("Mixed per source") == ""
    assert classify_license("") == "none"
    # The raw string survives in provenance whatever the normalisation did.
    kegg = bridge_manifest(CONNECTORS["kegg"])
    assert kegg.license_spdx == "" and "commercial" in kegg.provenance["data_license"]


def test_a_permissive_only_run_excludes_restricted_sources_and_keeps_open_ones(kernel):
    strict = kernel.policy.envelope(allowed_license_classes=("permissive",))
    ok, _ = bridge_manifest(CONNECTORS["ensembl"]).compatible_with(strict)
    assert ok
    ok, why = bridge_manifest(CONNECTORS["kegg"]).compatible_with(strict)
    assert not ok and "licence" in why


def test_an_unsafe_id_is_sanitised_and_a_collision_is_refused(kernel, runtime):
    bridge = BioScienceBridge(kernel, runtime)
    odd = local_tool(cid="weird:id/x")
    admitted = bridge.admit(odd)
    assert admitted.id == "weird-id-x"
    twin = local_tool(cid="weird:id:x")                     # sanitises to the same id
    with pytest.raises(BridgeRefused, match="shadow"):
        bridge.admit(twin)


def test_metadata_only_components_are_not_admitted(kernel, runtime):
    bridge = BioScienceBridge(kernel, runtime)
    meta = local_tool(cid="cat.tool.metadata", runtime=RuntimeSpec(backend="none"))
    with pytest.raises(BridgeRefused, match="no execution backend"):
        bridge.admit(meta)


# ============================================ the crossing

def test_phi_never_reaches_a_public_connector(kernel, runtime, transport):
    bridge = BioScienceBridge(kernel, runtime)
    bridge.admit(CONNECTORS["ensembl"])
    component = bridge.component("public.connector.ensembl")
    with pytest.raises(EgressDenied):
        kernel.broker.call_tool(component, {"operation": "gene_lookup", "symbol": PHI_TEXT},
                                kernel.policy.envelope())
    assert transport.requests == [], "the transport was reached with PHI"
    assert component.calls == 0, "the BioScience runtime was reached with PHI"


def test_a_clean_call_crosses_both_kernels_in_order(kernel, runtime, transport):
    from bioagent.runtime.events import EventLog

    events = EventLog()
    bridge = BioScienceBridge(kernel, runtime, events=events)
    bridge.admit(CONNECTORS["ensembl"])
    component = bridge.component("public.connector.ensembl")
    before = kernel.broker.stats()

    result = kernel.broker.call_tool(component, {"operation": "gene_lookup", "symbol": "TP53",
                                                 "_psh_idempotency_key": "run:task"},
                                     kernel.policy.envelope())

    assert result.value["display_name"] == "TP53"
    assert result.label.sensitivity >= Sensitivity.INTERNAL          # never PUBLIC by default
    # PSH: the gate saw it, the broker counted it, the audit chain has it.
    assert kernel.broker.stats()["tool_calls"] == before["tool_calls"] + 1
    assert any(e.event_type == "tool_call" for e in kernel.events.records())
    assert any(e.event_type == "bioscience_component_admitted" for e in kernel.events.records())
    # BioScience: resolution, its own policy ruling and the invocation were logged.
    kinds = {e.event_type for e in events.events}
    assert {"ComponentResolved", "PolicyChecked", "ToolCalled"} <= kinds
    assert component.last_status is ExecutionStatus.SUCCEEDED
    # The wire saw the rendered operation, not the PSH bookkeeping key.
    assert "lookup/symbol/homo_sapiens/TP53" in transport.requests[0].url
    assert "_psh_idempotency_key" not in transport.requests[0].full_url


def test_a_bioscience_denial_is_a_psh_policy_refusal(kernel, runtime, transport):
    offline = AgentSpec(name="offline", permission_profile="offline-analysis")
    bridge = BioScienceBridge(kernel, runtime, spec=offline)
    bridge.admit(CONNECTORS["ensembl"])
    component = bridge.component("public.connector.ensembl")
    with pytest.raises(PolicyDenied, match="BioScience policy refused"):
        kernel.broker.call_tool(component, {"operation": "gene_lookup", "symbol": "TP53"},
                                kernel.policy.envelope())
    assert transport.requests == []
    assert component.last_status is ExecutionStatus.DENIED


def test_an_unrunnable_component_is_capability_unavailable(kernel, tmp_path):
    ghost = local_tool(cid="ghost.tool.x",
                       runtime=RuntimeSpec(backend="python", entrypoint="no_such_module_x:f"))
    runtime = default_runtime(catalogue=False, public_apis=False, extra_manifests=(ghost,),
                              data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime)
    bridge.admit(ghost)
    with pytest.raises(CapabilityUnavailable):
        kernel.broker.call_tool(bridge.component("ghost.tool.x"), {"x": 1},
                                kernel.policy.envelope())


def test_operation_contract_violations_are_named(kernel, runtime, transport):
    bridge = BioScienceBridge(kernel, runtime)
    bridge.admit(CONNECTORS["ensembl"])
    component = bridge.component("public.connector.ensembl")
    envelope = kernel.policy.envelope()
    with pytest.raises(ContractViolation, match="needs an 'operation'"):
        kernel.broker.call_tool(component, {"symbol": "TP53"}, envelope)
    with pytest.raises(ContractViolation, match="no operation"):
        kernel.broker.call_tool(component, {"operation": "nope"}, envelope)
    with pytest.raises(ContractViolation, match="requires"):
        kernel.broker.call_tool(component, {"operation": "gene_lookup"}, envelope)
    assert transport.requests == []


def test_a_local_python_tool_runs_in_process_and_upstream_is_not_a_keyword(kernel, tmp_path):
    echo = local_tool()
    runtime = default_runtime(catalogue=False, public_apis=False, extra_manifests=(echo,),
                              data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime)
    bridge.admit(echo)
    result = kernel.broker.call_tool(bridge.component("local.tool.echo"),
                                     {"obj": {"a": 1}, "upstream": {"prev": "x"}},
                                     kernel.policy.envelope())
    assert json.loads(result.value) == {"a": 1}


# ============================================ retrieval: two levels

def test_domain_harnesses_make_the_catalogue_retrievable_in_two_levels(kernel, runtime):
    bridge = BioScienceBridge(kernel, runtime)
    admitted = bridge.admit_all()
    assert len(admitted) == len(CONNECTORS) and bridge.refusals == []
    registry = CapabilityRegistry()
    stats = bridge.register_into(registry)
    assert stats["harnesses"] >= 8 and stats["components"] == len(admitted)
    top = registry.stats()
    assert top["harnesses"] == stats["harnesses"]
    assert top["nested_capabilities"] == len(admitted)

    envelope = kernel.policy.envelope()
    hits = registry.resolve("look up the UniProt entry for TP53 protein", envelope, limit=5)
    assert any(c.id == "public.connector.uniprot" for c in hits), [c.id for c in hits]
    assert registry.last_trace.domain_narrowed_to
    assert registry.component("public.connector.uniprot") is bridge.component(
        "public.connector.uniprot")


def test_a_local_only_run_sees_no_public_connectors(tmp_path, runtime):
    local_only = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "local").ensure_dirs(),
        policy=policy(allowed_destinations=(Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL,
                                            Destination.USER_OUTPUT)))
    try:
        bridge = BioScienceBridge(local_only, runtime)
        bridge.admit_all()
        registry = CapabilityRegistry()
        bridge.register_into(registry)
        hits = registry.resolve("uniprot protein entry", local_only.policy.envelope(), limit=10)
        assert all(not c.id.startswith("public.connector.") for c in hits)
        assert registry.last_trace.policy_excluded > 0
    finally:
        local_only.close()


def test_descriptions_are_classified_at_admission(kernel, tmp_path):
    leaky = local_tool(cid="leaky.tool.x", description=f"reads the chart of {PHI_TEXT}")
    runtime = default_runtime(catalogue=False, public_apis=False, extra_manifests=(leaky,),
                              data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime)
    manifest = bridge.admit(leaky)
    assert manifest.provenance["description_sensitivity"] == "PHI"
    registry = CapabilityRegistry()
    bridge.register_into(registry, harnesses=False)
    items = registry.manifest_items(registry.resolve("chart", kernel.policy.envelope()))
    assert items and items[0].label.sensitivity is Sensitivity.PHI


# ============================================ isolation

def test_an_isolated_component_runs_in_a_kernel_child_process(kernel, tmp_path):
    echo = local_tool()
    runtime = default_runtime(catalogue=False, public_apis=False, extra_manifests=(echo,),
                              data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime, isolate=True)
    manifest = bridge.admit(echo)
    assert manifest.backend == "subprocess" and "exec.py" in manifest.entrypoint
    assert (kernel.config.state_dir / "bioscience" / "local.tool.echo.yaml").is_file()

    before = kernel.broker.stats()
    result = kernel.broker.call_tool(bridge.component("local.tool.echo"), {"obj": {"b": 2}},
                                     kernel.policy.envelope())
    assert json.loads(result.value) == {"b": 2}
    assert kernel.broker.stats()["isolated_tool_calls"] == before["isolated_tool_calls"] + 1
    assert bridge.component("local.tool.echo").calls == 0, "the parent process never ran it"


def test_a_policy_requiring_isolation_refuses_the_in_process_bridge(tmp_path, runtime):
    strict = TrustedKernel(PSHConfig(state_dir=tmp_path / "strict").ensure_dirs(),
                           policy=policy(require_isolated_tools=True))
    try:
        bridge = BioScienceBridge(strict, runtime)          # isolate=False
        bridge.admit(CONNECTORS["hgnc"])
        with pytest.raises(PolicyDenied, match="process-isolated"):
            strict.broker.call_tool(bridge.component("public.connector.hgnc"),
                                    {"operation": "symbol", "symbol": "TP53"},
                                    strict.policy.envelope())
    finally:
        strict.close()


# ============================================ the trusted plane is immutable

def test_bioagent_never_reaches_into_the_kernel_internals():
    root = Path(bioagent.__file__).parent
    offenders = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("psh.kernel"):
                    offenders.append(f"{source.relative_to(root)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("psh.kernel"):
                        offenders.append(f"{source.relative_to(root)}:{node.lineno}")
    assert offenders == [], f"bioagent imports psh.kernel internals: {offenders}"


def test_the_boundary_names_every_way_into_the_trusted_plane():
    boundary = KernelBoundary()
    bad = ComponentManifest(
        id="evil.tool.x", kind="tool", name="x",
        runtime=RuntimeSpec(backend="python", entrypoint="psh.kernel.egress:ExecutionBroker"),
        provider=Provider(source_path="PSH-Harness/src/psh/kernel/egress.py"),
        permissions=Permissions(filesystem_write=("../psh/policy.py", "bioagent/psh/x.py")))
    problems = boundary.violations(bad)
    assert len(problems) == 4, problems
    with pytest.raises(BoundaryViolation):
        boundary.check(bad)
    assert boundary.violations(local_tool()) == []


def test_a_proposal_that_targets_the_kernel_is_quarantined_before_it_is_tested():
    incumbent = local_tool(cid="cap.tool.a", version="1.0.0")
    registry = ComponentRegistry([incumbent])
    smoke_ran = []
    reloader = HotReloader(registry, smoke_runner=lambda m: smoke_ran.append(m.id) or (True, "ok"))
    pipeline = EvolutionPipeline(registry, reloader)
    proposal = EvolutionAgent(registry).propose(
        "cap.tool.a", changes={"runtime": {"backend": "python",
                                           "entrypoint": "psh.kernel.output_gate:OutputGate"}},
        rationale="speed up release by skipping the gate")
    out = pipeline.submit(proposal)
    assert out.state.value == "QUARANTINED"
    assert out.stage_log[-1]["stage"] == "boundary"
    assert "trusted plane" in out.stage_log[-1]["detail"]
    assert smoke_ran == [], "a boundary violation must not spend a smoke test"


# ============================================ native tools: PHI stays local

def test_native_tools_are_local_components_with_the_local_ceiling(kernel, tmp_path):
    from bioagent.tools import TOOLS

    runtime = default_runtime(catalogue=False, public_apis=False, native_tools=True,
                              data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, runtime)
    admitted = bridge.admit_all()
    assert len(admitted) == len(TOOLS) and bridge.refusals == []
    for manifest in admitted:
        assert manifest.destinations == (Destination.LOCAL_COMPUTE,)
        assert manifest.max_label is Sensitivity.PHI
        assert not manifest.requires_network and not manifest.mutates
        assert manifest.license_spdx == "MIT" and manifest.integration_mode == "native"
        assert manifest.idempotent
    registry = CapabilityRegistry()
    stats = bridge.register_into(registry)
    from bioagent.tools import DOMAINS
    assert stats["harnesses"] == len(DOMAINS) == 12     # one per toolkit domain
    hits = registry.resolve("estimate kidney function eGFR from creatinine", kernel.policy.envelope())
    assert any(c.id == "native.tool.egfr_ckd_epi_2021" for c in hits), [c.id for c in hits]


def test_a_phi_payload_runs_locally_and_is_refused_remotely(kernel, runtime, transport, tmp_path):
    """The label model's point, in one test: the same identifiable payload reaches a local
    calculator and never reaches a public connector."""
    from psh.labels import DataLabel, Labeled

    local_runtime = default_runtime(catalogue=False, public_apis=False, native_tools=True,
                                    data_lake=tmp_path / "no-lake")
    bridge = BioScienceBridge(kernel, local_runtime)
    bridge.admit_all()
    calculator = bridge.component("native.tool.egfr_ckd_epi_2021")
    phi = Labeled({"creatinine_mg_dl": 1.4, "age_years": 67, "sex": "female"},
                  DataLabel(Sensitivity.PHI, categories=("medical_record_number",)))
    envelope = kernel.policy.envelope()

    result = kernel.broker.call_tool(calculator, phi, envelope)
    assert result.value["kdigo_stage"] == "G3b"                  # 41.2 mL/min/1.73 m²
    assert result.label.sensitivity is Sensitivity.PHI            # the result inherits PHI

    remote = BioScienceBridge(kernel, runtime)
    remote.admit(CONNECTORS["hgnc"])
    with pytest.raises(EgressDenied):
        kernel.broker.call_tool(remote.component("public.connector.hgnc"),
                                Labeled({"operation": "symbol", "symbol": "TP53"}, phi.label),
                                envelope)
    assert transport.requests == []


# ------------------------------------------------- 2026-09-18 review: F09 and F12

class StatusBackend:
    """Stands in for the python backend and answers with a chosen ExecutionStatus."""

    backend = "python"

    def __init__(self, status, value=None, error=None):
        self.status, self.value, self.error = status, value, error
        self.kwargs: list[dict] = []

    def available(self):
        return True

    def unavailable_reason(self):
        return ""

    def handles(self, manifest):
        return manifest.runtime.backend == self.backend

    def rebind(self, loader):
        return self

    def invoke(self, manifest, **kwargs):
        from bioagent.adapters.base import CallResult

        self.kwargs.append(dict(kwargs))
        return CallResult(capability=manifest.id, adapter="status-stub", status=self.status,
                          value=self.value, error=self.error)


def admitted_local(kernel, runtime, backend, events=None):
    runtime.backends.register(backend)
    manifest = local_tool()
    runtime.registry.add(manifest)
    bridge = BioScienceBridge(kernel, runtime, events=events)
    psh_manifest = bridge.admit(manifest)
    return bridge.component(psh_manifest.id)


def test_the_idempotency_key_reaches_the_runtime_and_never_the_entrypoint(kernel, runtime):
    """F09: the bridge stripped ``_psh_idempotency_key`` with the other bookkeeping, so a
    side-effecting backend had no way to recognise a replay. It travels on its own now."""
    from bioagent.runtime.events import EventLog

    events = EventLog()
    backend = StatusBackend(ExecutionStatus.SUCCEEDED, value={"ok": True})
    component = admitted_local(kernel, runtime, backend, events=events)
    result = kernel.broker.call_tool(component, {"x": 1, "_psh_idempotency_key": "run:task"},
                                     kernel.policy.envelope())
    assert result.value == {"ok": True}
    assert backend.kwargs == [{"x": 1}], "the entrypoint saw only its own arguments"
    called = [e for e in events.events if e.event_type == "ToolCalled"]
    assert called and called[-1].detail.get("idempotency_key") == "run:task"


def test_a_bioscience_timeout_is_a_psh_tool_timeout(kernel, runtime):
    from psh.contracts import ToolTimeout

    backend = StatusBackend(ExecutionStatus.TIMEOUT, error="no reply in 30s")
    component = admitted_local(kernel, runtime, backend)
    with pytest.raises(ToolTimeout, match="timed out"):
        kernel.broker.call_tool(component, {"x": 1}, kernel.policy.envelope())
    assert isinstance(ToolTimeout("x"), ContractViolation)


def test_a_degraded_result_keeps_its_caveat_across_the_bridge(kernel, runtime):
    """F12: DEGRADED used to come back as a plain success; the shortfall now travels."""
    from psh.contracts import DegradedResult

    backend = StatusBackend(ExecutionStatus.DEGRADED, value={"n": 3},
                            error="served from the cached snapshot")
    component = admitted_local(kernel, runtime, backend)
    raw = component.invoke({"x": 1}, kernel.policy.envelope())
    assert isinstance(raw, DegradedResult) and raw.value == {"n": 3}
    result = kernel.broker.call_tool(component, {"x": 1}, kernel.policy.envelope())
    assert result.value == {"n": 3}
    assert result.status == "degraded" and not result.ok
    assert result.warnings == ("served from the cached snapshot",)
    assert component.last_status is ExecutionStatus.DEGRADED


def test_the_isolated_entrypoint_reports_a_degraded_result_and_its_own_timeout(tmp_path):
    """The child process side of the same two facts: one JSON value, or one wrapped value."""
    import io
    from contextlib import redirect_stdout, redirect_stderr

    from bioagent.psh import exec as isolated

    manifest = local_tool()
    path = tmp_path / "echo.yaml"
    path.write_text(manifest.to_yaml() if hasattr(manifest, "to_yaml") else "")
    if not path.read_text():
        pytest.skip("manifests are not serialisable to YAML in this build")

    def run(status, value=None, error=None, payload=None):
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO(json.dumps({"tool": "x", "run_id": "r", "payload": payload or {}}))
        import bioagent.psh.assembly as assembly

        original = assembly.default_runtime

        def patched(**kw):
            runtime = original(**kw)
            runtime.backends.register(StatusBackend(status, value=value, error=error))
            return runtime

        assembly.default_runtime = patched
        try:
            with redirect_stdout(out), redirect_stderr(err):
                old_stdin, sys.stdin = sys.stdin, stdin
                try:
                    code = isolated.main(["--manifest", str(path)])
                finally:
                    sys.stdin = old_stdin
        finally:
            assembly.default_runtime = original
        return code, out.getvalue(), err.getvalue()

    code, out, _ = run(ExecutionStatus.DEGRADED, value={"n": 3}, error="partial page")
    assert code == 0
    assert json.loads(out) == {"$psh": {"status": "degraded", "reason": "partial page",
                                        "value": {"n": 3}}}
    code, out, err = run(ExecutionStatus.TIMEOUT, error="no reply")
    assert code == 124 and "TIMEOUT" in err and out == ""
