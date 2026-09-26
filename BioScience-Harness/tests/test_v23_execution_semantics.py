"""Regression tests for the v2.3 execution-semantics review.

The theme of that review: the declarative layer was largely right, and the
execution layer did not honour it. A manifest or a planner said what should
happen and the backend did something else — or something else's work was scored
as if it were this component's.

Each test below was confirmed failing against the reviewed code.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from bioagent.adapters.base import CallResult
from bioagent.backends.base import BackendRegistry
from bioagent.backends.concrete import ContainerBackend, DatasetBackend, PythonBackend
from bioagent.planners.base import (Critique, Plan, PlanStep, PlannerPlugin,
                                    merge_step_arguments)
from bioagent.runtime.agentspec import AgentSpec, RunReport, Runtime
from bioagent.runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                        Requirements, RuntimeSpec)
from bioagent.runtime.registry import ComponentRegistry, Loader, Resolver
from bioagent.status import ExecutionStatus, LifecycleState, RunOutcome, ScientificVerdict

@pytest.fixture(scope="module", autouse=True)
def probe_module():
    """A tiny importable module the python backend can resolve entrypoints in.

    Written to a temp directory rather than under `tests/`, so a partially
    cleaned-up run can never leave an untracked source file in the repository.
    """
    import sys

    probe_dir = Path(tempfile.mkdtemp(prefix="bioagent-probe-"))
    (probe_dir / "probe_impl.py").write_text(
        "def echo(**kw):\n"
        "    return {'called_with': kw}\n"
        "def incumbent_fn(**kw):\n"
        "    return {'which': 'incumbent'}\n"
        "def candidate_fn(**kw):\n"
        "    return {'which': 'candidate'}\n",
        encoding="utf-8")
    sys.path.insert(0, str(probe_dir))
    yield
    sys.path.remove(str(probe_dir))
    shutil.rmtree(probe_dir, ignore_errors=True)


def tool(cid="p.tool.x", fn="echo", **kw) -> ComponentManifest:
    return ComponentManifest(
        id=cid, kind="tool", name=kw.pop("name", cid.rsplit(".", 1)[-1]),
        runtime=RuntimeSpec(backend=kw.pop("backend", "python"),
                            entrypoint=kw.pop("entrypoint", f"probe_impl:{fn}"),
                            image=kw.pop("image", "")),
        license=LicenseSpec(spdx="MIT", integration_mode="vendor"), **kw)


def runtime_for(*manifests) -> Runtime:
    reg = ComponentRegistry(list(manifests))
    res = Resolver(reg)
    ld = Loader(reg, res)
    return Runtime(reg, BackendRegistry([PythonBackend(ld)]), resolver=res, loader=ld)


# ----------------------------------------------- PlanStep.arguments reach the backend
class _ArgPlanner(PlannerPlugin):
    name = "test-args"

    def plan(self, task, registry, *, max_steps=5):
        return Plan(task=task, planner=self.name, steps=[
            PlanStep(component_id="p.tool.x",
                     arguments={"path": "from-step", "params": {"q": "abc"}})])


def test_plan_step_arguments_reach_the_backend() -> None:
    """A planner's per-step arguments were computed and then thrown away.

    `PlanStep.arguments` had no consumer anywhere in the codebase, so a planner
    could decide to call `/lookup/id/ENSG…` and the runtime would invoke the
    component with nothing at all.
    """
    rt = runtime_for(tool())
    report = rt.run("t", AgentSpec(name="a", max_attempts=1), planner=_ArgPlanner())
    assert report.results[0].status is ExecutionStatus.SUCCEEDED
    assert report.results[0].value["called_with"] == {"path": "from-step",
                                                      "params": {"q": "abc"}}


def test_step_arguments_take_precedence_over_run_wide_defaults() -> None:
    """The planner's specific decision must not be overwritten by a global default."""
    merged = merge_step_arguments(
        PlanStep(component_id="x", arguments={"nrows": 100, "only": "step"}),
        {"nrows": 5, "shared": "run"})
    assert merged == {"nrows": 100, "only": "step", "shared": "run"}


def test_legacy_agent_loop_also_passes_step_arguments() -> None:
    from bioagent.core.planner import PlanStep as LegacyStep

    merged = merge_step_arguments(
        LegacyStep(capability="c", kind="tool", arguments={"a": 1}), {"b": 2})
    assert merged == {"a": 1, "b": 2}


# ------------------------------------------- candidate evaluation runs the candidate
def test_evaluating_a_candidate_does_not_execute_the_incumbent() -> None:
    """The defect that silently corrupted every self-evolution result.

    `PythonBackend` resolves entrypoints by `manifest.id` through the production
    loader, and a candidate normally carries the incumbent's id — so scoring a
    candidate ran the incumbent, and "the candidate improved" was the old version
    compared against itself.
    """
    rt = runtime_for(tool(fn="incumbent_fn"))
    candidate = tool(fn="candidate_fn")
    res = rt.invoke_manifest(candidate, spec=AgentSpec(name="eval"))
    assert res.status is ExecutionStatus.SUCCEEDED
    assert res.value == {"which": "candidate"}, "the incumbent was executed instead"
    # and the production registry is untouched by the evaluation
    assert rt.registry.get("p.tool.x").runtime.entrypoint == "probe_impl:incumbent_fn"


def test_candidate_evaluation_goes_through_the_policy_kernel() -> None:
    """Benchmarking used a second execution chain that never called authorize()."""
    rt = runtime_for(tool(fn="incumbent_fn"))
    candidate = tool(fn="candidate_fn")
    candidate.permissions = Permissions(network=("exfil.example.invalid",))
    res = rt.invoke_manifest(candidate,
                             spec=AgentSpec(name="eval", permission_profile="offline-analysis"))
    assert res.status is ExecutionStatus.DENIED
    assert "forbids network" in (res.error or "")


def test_smoke_evaluator_scores_the_candidate_not_the_incumbent() -> None:
    from bioagent.evolution.evaluators import SmokeTestEvaluator

    rt = runtime_for(tool(fn="incumbent_fn"))
    broken = tool(entrypoint="probe_impl:does_not_exist")
    ev = SmokeTestEvaluator(runtime=rt, spec=AgentSpec(name="eval"))
    assert ev.score(broken).score == 0.0, "scored the working incumbent instead"
    assert ev.score(tool(fn="candidate_fn")).score == 1.0


# --------------------------------------------- dependency lifecycle propagation
def test_a_parent_is_unavailable_when_a_required_component_is() -> None:
    """Registration is not usability: the check only asked whether a dep existed."""
    reg = ComponentRegistry()
    reg.add(tool(cid="p.tool.dep", requires=Requirements(python=("definitely_missing_xyz",))))
    reg.add(tool(cid="p.tool.parent", requires=Requirements(components=("p.tool.dep",))))
    res = Resolver(reg)

    assert res.resolve("p.tool.dep").state is LifecycleState.UNAVAILABLE
    parent = res.resolve("p.tool.parent")
    assert parent.state is LifecycleState.UNAVAILABLE, "parent claimed dependencies satisfied"
    assert "p.tool.dep" in parent.reason


def test_dependency_propagation_terminates_on_a_cycle() -> None:
    reg = ComponentRegistry()
    reg.add(tool(cid="p.tool.a", requires=Requirements(components=("p.tool.b",))))
    reg.add(tool(cid="p.tool.b", requires=Requirements(components=("p.tool.a",))))
    assert Resolver(reg).resolve("p.tool.a") is not None      # must not recurse forever


# ------------------------------------------------------- dataset probe is wired
def test_a_dataset_present_on_disk_resolves_and_reads() -> None:
    """The resolver's default probe answered False for everything.

    `Runtime` built its own Resolver without a `dataset_probe`, so the fallback
    `lambda _cid: False` made every dataset component permanently UNAVAILABLE
    even with the file sitting in the lake — and auto-fetch dead-ended, because
    the re-resolution after a successful download used the same always-False
    probe.
    """
    lake = Path(tempfile.mkdtemp()) / "lake"
    lake.mkdir(parents=True)
    (lake / "demo.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")

    m = ComponentManifest(
        id="p.dataset.demo", kind="dataset", name="demo.tsv",
        runtime=RuntimeSpec(backend="dataset"),
        requires=Requirements(datasets=("demo.tsv",)),
        license=LicenseSpec(spdx="MIT", integration_mode="native"))
    reg = ComponentRegistry([m])
    rt = Runtime(reg, BackendRegistry([DatasetBackend(lake)]))

    assert rt.resolver.resolve(m.id).state is not LifecycleState.UNAVAILABLE
    res = rt.invoke(m.id, spec=AgentSpec(name="a"), nrows=1)
    assert res.status is ExecutionStatus.SUCCEEDED, res.error


def test_dataset_probe_does_not_escape_the_lake() -> None:
    lake = Path(tempfile.mkdtemp()) / "lake"
    lake.mkdir(parents=True)
    outside = lake.parent / "secret.tsv"
    outside.write_text("x\n", encoding="utf-8")
    assert DatasetBackend(lake).has_dataset("../secret.tsv") is False


# ------------------------------------------------- http deny-by-default
def test_http_component_with_no_declared_hosts_is_denied() -> None:
    """An empty allowlist was the most permissive setting, not the least."""
    from bioagent.backends.http import HTTPBackend

    b = HTTPBackend(cache_dir=Path(tempfile.mkdtemp()))
    m = ComponentManifest(
        id="d.database.x", kind="database", name="x",
        runtime=RuntimeSpec(backend="http", server="https://127.0.0.1:9"),
        license=LicenseSpec(spdx="MIT", integration_mode="native"))
    res = b.invoke(m, path="/x")
    assert res.status is ExecutionStatus.DENIED
    assert "declares no permissions.network" in (res.error or "")


def test_http_cache_key_distinguishes_representations() -> None:
    """JSON and CSV of one URL collided on a single cache entry."""
    from bioagent.backends.http import HTTPRequest

    a = HTTPRequest(url="https://x.example/y", accept="application/json")
    b = HTTPRequest(url="https://x.example/y", accept="text/csv")
    assert a.key() != b.key()


# ------------------------------------------------- promotion gates
def _pipeline(registry, scores, **kw):
    from bioagent.evolution.pipeline import BenchmarkResult, EvolutionPipeline
    from bioagent.runtime.hmr import HotReloader

    def runner(manifest, benchmark):
        which = "candidate" if manifest.description == "candidate" else "incumbent"
        return BenchmarkResult(benchmark, scores[which][benchmark], 1)

    return EvolutionPipeline(registry, HotReloader(registry, Loader(registry)),
                             benchmark_runner=runner, **kw)


def test_a_regression_on_any_benchmark_blocks_promotion() -> None:
    """Only `bms[0]` was ever run, so a declared safety benchmark never gated."""
    from bioagent.evolution.pipeline import Proposal, ProposalState

    inc = tool(cid="p.tool.z")
    inc.description = "incumbent"
    inc.validation.benchmarks = ("accuracy", "safety")
    reg = ComponentRegistry([inc])

    cand = tool(cid="p.tool.z")
    cand.description = "candidate"
    cand.validation.benchmarks = ("accuracy", "safety")

    pipe = _pipeline(reg, {"candidate": {"accuracy": 0.9, "safety": 0.1},
                           "incumbent": {"accuracy": 0.5, "safety": 0.9}})
    out = pipe.submit(Proposal(proposal_id="p1", component=cand, rationale="r"))
    assert out.state is ProposalState.QUARANTINED
    assert "regression on safety" in out.stage_log[-1]["detail"]
    assert set(out.benchmark_scores) == {"accuracy", "safety"}, "not every benchmark ran"


def test_a_new_component_must_clear_an_absolute_floor() -> None:
    """`if incumbent is not None` guarded the score gate, so a new component
    scoring 0.0 on every benchmark was promoted unchecked."""
    from bioagent.evolution.pipeline import Proposal, ProposalState

    reg = ComponentRegistry()
    cand = tool(cid="p.tool.new")
    cand.description = "candidate"
    cand.validation.benchmarks = ("accuracy",)
    pipe = _pipeline(reg, {"candidate": {"accuracy": 0.0}, "incumbent": {"accuracy": 0.0}},
                     min_absolute_score=0.5)
    out = pipe.submit(Proposal(proposal_id="p2", component=cand, rationale="r"))
    assert out.state is ProposalState.QUARANTINED
    assert "below the required" in out.stage_log[-1]["detail"]


# ------------------------------------------------- verdict honesty
def test_steps_succeeding_is_not_a_scientific_finding() -> None:
    """The default critique reported ACCEPTED for "everything ran".

    A statistical test can run cleanly and return p = 0.83; a model can return
    an accuracy of 0.30. Both are successful executions of a negative result.
    """
    rt = runtime_for(tool())
    report = rt.run("t", AgentSpec(name="a", max_attempts=1), planner=_ArgPlanner())
    assert report.execution_outcome == RunOutcome.SUCCESS.value
    assert report.verdict == ScientificVerdict.INCONCLUSIVE.value
    assert report.ok is False
    assert report.summary()["scientific_verdict"] == ScientificVerdict.INCONCLUSIVE.value


def test_a_validator_may_still_establish_a_finding() -> None:
    rep = RunReport(
        task="t", spec=AgentSpec(name="a"), plan=Plan(task="t"),
        results=[CallResult(capability="c", adapter="a", status=ExecutionStatus.SUCCEEDED)],
        critique=Critique(True, "accuracy 0.94 >= threshold 0.90", verdict="ACCEPTED"))
    assert rep.verdict == ScientificVerdict.ACCEPTED.value
    assert rep.ok is True


# ------------------------------------------------- container honesty
def test_container_component_without_an_entrypoint_is_not_reported_successful() -> None:
    """`docker run <image>` ran the image default and was scored as this
    component's success, so every component sharing an image behaved alike."""
    b = ContainerBackend()
    m = tool(cid="c.tool.boxed", backend="container", entrypoint="", image="example/img:1")
    res = b.invoke(m)
    assert res.status is not ExecutionStatus.SUCCEEDED
    assert "no runtime.entrypoint" in (res.error or "")


def test_subprocess_backend_builds_its_call_from_the_manifest() -> None:
    """No planner path supplies `code=`, so requiring it made every subprocess
    capability unexecutable through the normal route."""
    from bioagent.backends.concrete import SubprocessBackend

    m = tool(cid="p.tool.sub", backend="subprocess", entrypoint="probe_impl:echo")
    code = SubprocessBackend._code_for(m, {"a": 1})
    assert code and "probe_impl" in code and "'echo'" in code


# ------------------------------------------------- download size gate
def test_size_gate_uses_the_largest_estimate_not_the_declared_one() -> None:
    """`expected_bytes or remote_size` let a manifest understate a huge file."""
    from bioagent.acquisition.downloader import DownloadError, Downloader

    root = Path(tempfile.mkdtemp())
    dl = Downloader(root, size_gate_bytes=1024)
    dl._remote_size = lambda url: 50 * 1024 * 1024        # server says 50 MB
    with pytest.raises(DownloadError, match="above the"):
        dl.fetch("https://example.invalid/big.bin", "big.bin", expected_bytes=10)


def test_streaming_aborts_when_no_size_is_advertised() -> None:
    """With neither size known the hint was 0 and the gate never applied."""
    from bioagent.acquisition.downloader import DownloadError, Downloader

    root = Path(tempfile.mkdtemp())
    dl = Downloader(root, size_gate_bytes=4096, chunk=1024)
    part = root / "x.part"

    class _Resp:
        status = 200
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"0" * 1024

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        with pytest.raises(DownloadError, match="aborted after"):
            dl._stream("https://example.invalid/x", part, None, max_bytes=4096)
    finally:
        urllib.request.urlopen = orig
    assert not part.exists()


# ------------------------------------------------- json is actually bounded
def test_json_lines_is_read_incrementally() -> None:
    from bioagent.adapters.datalake import DataLakeAdapter

    d = Path(tempfile.mkdtemp())
    (d / "big.jsonl").write_text(
        "".join(json.dumps({"i": i}) + "\n" for i in range(50_000)), encoding="utf-8")

    class _Cap:
        kind = "dataset"
        name = "big.jsonl"

    res = DataLakeAdapter(d).invoke(_Cap(), nrows=3)
    assert res.status is ExecutionStatus.SUCCEEDED
    assert res.value["parsed_as"] == "jsonl" and res.value["streamed"] is True
    assert res.value["shape"][0] == 3


def test_an_oversized_single_json_document_is_refused_not_parsed() -> None:
    from bioagent.adapters.datalake import DataLakeAdapter

    d = Path(tempfile.mkdtemp())
    big = d / "huge.json"
    big.write_text(json.dumps([{"i": i} for i in range(2000)]), encoding="utf-8")

    class _Cap:
        kind = "dataset"
        name = "huge.json"

    adapter = DataLakeAdapter(d)
    original = DataLakeAdapter.JSON_WHOLE_FILE_LIMIT
    DataLakeAdapter.JSON_WHOLE_FILE_LIMIT = 100          # force the limit
    try:
        res = adapter.invoke(_Cap(), nrows=3)
    finally:
        DataLakeAdapter.JSON_WHOLE_FILE_LIMIT = original
    assert res.status is ExecutionStatus.SUCCEEDED
    assert res.value["streamed"] is False
    assert "cannot be sliced without being parsed in full" in res.value["note"]


# ------------------------------------------------- catalogue entrypoints
def test_entrypoints_keep_intermediate_packages() -> None:
    """`Path(rel).stem` dropped every package between the root and the file."""
    from bioagent.providers.catalogue import module_for_source

    assert (module_for_source("Biomni", "Biomni/biomni/tool/tool_description/pharmacology.py")
            == "biomni.tool.tool_description.pharmacology")
    assert (module_for_source("Agentomics",
                              "Agentomics/src/agentomics/runtime/generate_final_reports.py")
            == "agentomics.runtime.generate_final_reports")
    assert (module_for_source("GeneAgent", "GeneAgent/apis/get_complex_for_gene_set.py")
            == "geneagent.apis.get_complex_for_gene_set")


def test_every_catalogue_entrypoint_agrees_with_its_source_path() -> None:
    """Census over the bundled catalogue, checked independently of the deriver."""
    import sys

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts"))
    from entrypoint_census import census

    data = census(check_imports=False)
    assert data["python_components"] > 500
    assert data["inconsistent"] == 0, data["examples_inconsistent"][:5]
    assert data["malformed"] == 0


# ------------------------------------------------- hmr keeps the real probe
def test_hot_reload_scratch_resolver_keeps_the_backend_probe() -> None:
    from bioagent.runtime.hmr import HotReloader

    reg = ComponentRegistry()
    res = Resolver(reg, backend_probe=lambda b: (False, "backend disabled in this runtime"))
    reloader = HotReloader(reg, resolver=res, loader=Loader(reg, res))
    out = reloader.swap(tool(cid="p.tool.hot"))
    assert not out.promoted, "candidate cleared the gate under the wrong environment assumptions"
    assert out.stage_failed == "dependencies"
    assert "disabled in this runtime" in (out.reason or "")
