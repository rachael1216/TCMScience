"""End-to-end demo of the v2 harness, plus a gated evolution proposal.

Run:  PYTHONPATH=src python demo_harness.py

What it demonstrates, all against live code and real files:
  1. 2,567 catalogue rows -> component manifests with pinned provenance
  2. honest lifecycle resolution (READY vs UNAVAILABLE, with reasons)
  3. lazy loading: only the top-k components for one task are loaded
  4. the policy kernel denying an unlicensed vendor route
  5. a real dataset read through the DatasetBackend
  6. an event-sourced provenance graph, saved and replayed
  7. an evolution proposal that is PROMOTED, and one that is QUARANTINED
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from bioagent.backends.base import BackendRegistry
from bioagent.backends.concrete import (ContainerBackend, DatasetBackend, MCPBackend,
                                        NoneBackend, PythonBackend, SubprocessBackend)
from bioagent.config import REPO_ROOT, catalogue_path, data_lake_dir
from bioagent.evolution import EvolutionAgent, EvolutionPipeline, Proposal
from bioagent.evolution.evaluators import ShapeEvaluator, benchmark_components
from bioagent.planners import get_planner
from bioagent.policy import PolicyKernel
from bioagent.providers.catalogue import CatalogueProvider
from bioagent.runtime.agentspec import AgentSpec, Runtime
from bioagent.runtime.events import EventLog
from bioagent.runtime.hmr import HotReloader
from bioagent.runtime.component import ComponentManifest, Requirements
from bioagent.runtime.registry import ComponentRegistry, Loader, Resolver
from bioagent.status import LifecycleState
from bioagent.workspace import GitLayer, Workspace

REPOS = REPO_ROOT.parent / "repos"
OUT = REPO_ROOT / "demo_out"


def build_runtime() -> tuple[Runtime, ComponentRegistry, AgentSpec, Resolver, Loader]:
    rows = pd.read_csv(catalogue_path()).to_dict(orient="records")
    commits = {}
    man = REPO_ROOT / "data" / "repos_manifest.csv"
    if man.exists():
        mf = pd.read_csv(man)
        commits = {str(r["project"]).replace("PantheonOS_src", "PantheonOS")
                   .replace("K-Dense-scientific-agent-skills", "K-Dense"): str(r["head_commit"])[:8]
                   for _, r in mf.iterrows()}
    reg = ComponentRegistry(CatalogueProvider(rows, repos_root=REPOS, commits=commits).discover())
    for m in benchmark_components(
            [r for r in rows if r.get("kind") == "benchmark"]):
        reg.add(m)

    lake = data_lake_dir()
    present = {p.name for p in lake.iterdir()} if lake.is_dir() else set()
    resolver = Resolver(reg, dataset_probe=lambda cid: cid in present)
    loader = Loader(reg, resolver)
    backends = BackendRegistry([
        PythonBackend(loader), MCPBackend(), DatasetBackend(lake),
        SubprocessBackend(), ContainerBackend(), NoneBackend(),
    ])
    rt = Runtime(reg, backends, kernel=PolicyKernel(), resolver=resolver, loader=loader,
                 catalogue_version="unified-v2")
    spec = AgentSpec(name="biomedical-researcher", planner="heuristic", max_steps=4)
    return rt, reg, spec, resolver, loader


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rt, reg, spec, resolver, loader = build_runtime()
    print(f"[1] registry: {len(reg)} components from the catalogue + benchmarks")
    print(f"    backends: {json.dumps({k: v['available'] for k, v in rt.backends.availability().items()})}")

    # ---- 2. honest lifecycle census
    for m in list(reg):
        resolver.resolve(m.id)
    census = reg.state_census()
    print(f"[2] lifecycle census after resolution: {census}")

    # ---- 3. lazy loading for one task
    lazy = rt.lazy_set(spec)
    task = "drug target binding affinity evidence"
    chosen = lazy.acquire(task, candidates=30, top_k=4)
    print(f"[3] lazy set for {task!r}: retrieved {lazy.stats['retrieved']}, "
          f"policy-passed {lazy.stats['policy_passed']}, using {len(chosen)}")
    for m in chosen:
        print(f"      {m.id[:52]:<53} {m.runtime.backend:<10} {m.state.value}")

    # ---- 4. policy kernel denies an unlicensed vendor route
    unlicensed = next((m for m in reg
                       if m.license.spdx in ("NONE", "NOASSERTION") and m.kind == "tool"), None)
    if unlicensed:
        unlicensed.license.integration_mode = "vendor"   # pretend someone tried to vendor it
        res = rt.invoke(unlicensed.id, spec=spec)
        print(f"[4] policy gate on unlicensed vendor route: {res.status.value}")
        print(f"      {(res.error or '')[:96]}")

    # ---- 5+6. real run with event provenance
    report = rt.run(task, spec, step_kwargs={"nrows": 3})
    print(f"[5] execution: {report.execution_outcome} | verdict: {report.verdict} "
          f"(accepted={report.ok})")
    for r in report.results:
        detail = ""
        if r.status.name == "SUCCEEDED" and isinstance(r.value, dict):
            detail = f"shape={r.value.get('shape')} parsed_as={r.value.get('parsed_as')}"
        print(f"      [{r.status.value:<11}] {r.capability[:46]:<47}{detail}")
    ev_path = report.events.save(OUT / "harness_events.json")
    g = report.events.graph()
    print(f"[6] event graph: {g['n_nodes']} nodes, {g['n_edges']} edges, depth {report.events.depth()}")

    replay = EventLog.replay(
        ev_path,
        lambda cid, inputs: rt.invoke(cid, spec=spec, **inputs).value)
    print(f"    replay: {replay['n_reproduced']}/{replay['n_replayable']} reproduced, "
          f"{replay['n_skipped']} skipped (not executed originally)")

    # ---- 7. evolution: one promotion, one rejection
    # The workspace needs a writable .git; some sandboxes forbid creating .git
    # under a granted host path, so fall back to a temp root and say so.
    import tempfile
    ws_root = Path(os.environ.get("BIOAGENT_WORKSPACE", "")) if os.environ.get("BIOAGENT_WORKSPACE") \
        else Path(tempfile.mkdtemp(prefix="bioagent-ws-"))
    ws = Workspace(ws_root).init()
    git = None
    if GitLayer.available():
        try:
            git = GitLayer(ws.root).init()
            git.commit("workspace baseline")
        except Exception as exc:  # noqa: BLE001
            print(f"    (git unavailable in this workspace: {str(exc)[:70]})")
            git = None
    print(f"    workspace: {ws.root}  git={'yes' if git else 'no'}")
    dataset = next((m for m in reg if m.runtime.backend == "dataset"
                    and m.state is LifecycleState.READY), None)
    if dataset is None:
        print("[7] no READY dataset component; skipping evolution demo")
        return

    evaluator = ShapeEvaluator(runtime=rt, spec=spec)
    reloader = HotReloader(reg, resolver, loader,
                           smoke_runner=lambda m: (True, "smoke ok"))
    pipeline = EvolutionPipeline(reg, reloader,
                                benchmark_runner=lambda m, b: evaluator.score(m, b),
                                workspace=ws, git=git, min_improvement=0.0)
    agent = EvolutionAgent(reg)

    # 7a. a proposal with NO benchmark -> must be quarantined
    p_nobench = agent.propose(dataset.id, changes={"description": "tweaked, unproven"},
                              rationale="cosmetic change with no benchmark declared")
    r1 = pipeline.submit(p_nobench)
    print(f"[7a] proposal without benchmark: {r1.state.value}")
    print(f"      last gate: {r1.stage_log[-1]['stage']} -> {r1.stage_log[-1]['detail'][:70]}")

    # 7b. a benchmarked proposal that does NOT beat the incumbent -> rejected
    p_flat = agent.propose(
        dataset.id,
        changes={"description": f"{dataset.description[:70]} (no measurable change)",
                 "validation": {"smoke_test": "", "benchmarks": ["dataset-shape"],
                                "last_validated": ""}},
        rationale="cosmetic edit; benchmark declared but no improvement expected")
    r2 = pipeline.submit(p_flat)
    print(f"[7b] benchmarked but not better: {r2.state.value}")
    print(f"      {r2.stage_log[-1]['stage']}: {r2.stage_log[-1]['detail'][:78]}")

    # 7c. a proposal that genuinely improves a BROKEN component -> promoted.
    #     Start from a component whose declared dataset does not exist (score 0),
    #     then fix the name so it reads real data (score 1).
    broken = ComponentManifest.from_dict({
        **dataset.to_dict(),
        "id": "demo.dataset.broken_read",
        "name": "does_not_exist.parquet",
        "version": "1.0.0",
        "state": LifecycleState.DISCOVERED.value,
        "validation": {"smoke_test": "", "benchmarks": ["dataset-shape"], "last_validated": ""},
    })
    broken.requires = Requirements(datasets=("does_not_exist.parquet",))
    reg.add(broken)
    base_score = evaluator.score(broken, "dataset-shape")
    print(f"[7c] incumbent {broken.id} scores {base_score.score:.3f} "
          f"(status={base_score.detail.get('status')})")

    p_fix = agent.propose(
        broken.id,
        changes={"name": dataset.name, "version": "2.0.0",
                 "requires": {"python": [], "binaries": [], "datasets": [dataset.name],
                              "services": [], "components": []},
                 "validation": {"smoke_test": "", "benchmarks": ["dataset-shape"],
                                "last_validated": ""}},
        rationale=(f"the declared dataset does not exist, so every read fails; "
                   f"repoint at {dataset.name}, which is present in the lake"))
    r3 = pipeline.submit(p_fix)
    print(f"[7c] repair proposal: {r3.state.value}")
    for entry in r3.stage_log:
        print(f"      {entry['stage']:<10} ok={str(entry['ok']):<5} {entry['detail'][:66]}")
    print(f"      incumbent {r3.incumbent_score} -> candidate {r3.candidate_score}; "
          f"registry v{reloader.registry_version}; "
          f"component now {reg.get(broken.id).name} v{reg.get(broken.id).version}")
    if git:
        print(f"      git log: {[c['message'][:44] for c in git.log(3)]}")
    print(f"      proposals on disk: {[f for f in ws.list('proposals')][:4]}")

    json.dump({"run": report.summary(), "replay": {k: v for k, v in replay.items() if k != "steps"},
               "census": census,
               "proposals": [r1.to_dict(), r2.to_dict(), r3.to_dict()]},
              open(OUT / "harness_demo_summary.json", "w"), indent=2, default=str)
    print(f"\nwrote {OUT.name}/harness_events.json and {OUT.name}/harness_demo_summary.json")


if __name__ == "__main__":
    main()
