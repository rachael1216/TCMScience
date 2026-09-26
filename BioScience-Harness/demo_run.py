"""End-to-end demo: plan a real biomedical task over the unified catalogue.

Task: find and inspect drug-target binding-affinity evidence.

The demo exercises the whole stack against live capabilities: the registry
retrieves candidates from 16 upstream projects, the planner orders them
preferring locally-available and permissively-licensed options, the data-lake
adapter reads real files from the 15.1 GB Biomni lake, the native-connector
adapter resolves routing for capabilities whose only upstream implementation is
unlicensed, and every call lands in a replayable provenance trace.
"""

from __future__ import annotations

import json
from pathlib import Path

from bioagent import BioAgent, CapabilityRegistry, ProvenanceLog, SandboxExecutor
from bioagent.adapters import DataLakeAdapter, FederatedProcessAdapter, NativeConnectorAdapter
from bioagent.config import catalogue_path, data_lake_dir

ROOT = Path(__file__).resolve().parent
LAKE = data_lake_dir()
TRACE = ROOT / "demo_provenance_trace.json"


def main() -> None:
    registry = CapabilityRegistry.from_csv(catalogue_path())
    print(f"registry: {len(registry)} capabilities from {len(registry.projects())} projects")
    print(f"  by kind: {registry.counts('kind')}")
    print(f"  licensing: {registry.license_summary()}")

    adapters = [
        DataLakeAdapter(LAKE),
        NativeConnectorAdapter(),
        # unlicensed upstreams are federated, never copied; not installed here
        FederatedProcessAdapter(name="OrigeneMCP", project_root=None),
    ]
    agent = BioAgent(registry, adapters, executor=SandboxExecutor(timeout_s=60),
                     catalogue_version="unified-v1")

    task = "drug target binding affinity evidence"
    print(f"\n=== task: {task}")
    report = agent.run(task, max_steps=4, step_kwargs={"nrows": 3})

    for step, res in zip(report.plan.steps, report.results):
        status = "ok " if res.ok else "ERR"
        detail = ""
        if res.ok and isinstance(res.value, dict):
            if res.value.get("loaded"):
                detail = f"shape={res.value['shape']} cols={len(res.value['columns'])}"
            elif "resolved_connector" in res.value:
                detail = f"-> {res.value['resolved_connector']}"
            elif res.value.get("loaded") is False:
                detail = f"metadata-only ({res.value.get('format')})"
        print(f"  [{status}] {step.capability[:48]:<49} via {res.adapter:<18}{detail}")
        if not res.ok:
            print(f"         {(res.error or '')[:110]}")

    print(f"\nsummary: {json.dumps(report.summary(), indent=2)}")

    # 1b. route a capability whose only upstream implementation is unlicensed
    print("\n=== native-connector routing (license-avoiding path)")
    frame = registry.frame
    unlicensed_but_native = frame[
        (frame.integration_mode == "adapter-only")
        & (frame.native_connectors.astype(str).str.len() > 3)
    ]
    print(f"  {len(unlicensed_but_native)} adapter-only capabilities have a native equivalent")
    for name in unlicensed_but_native["name"].head(4):
        res = agent.invoke(str(name), log=report.provenance)
        conn = res.value.get("resolved_connector") if res.ok and isinstance(res.value, dict) else None
        print(f"    {str(name)[:44]:<45} -> {conn}")

    # 2. sandboxed execution of generated analysis code
    print("\n=== sandboxed executor")
    code = "import pandas as pd; print('pandas', pd.__version__)"
    ex = agent.executor.run(code)
    print(f"  ok={ex.ok} stdout={ex.stdout.strip()[:60]!r} duration={ex.duration_s:.2f}s")

    # 3. provenance + replay
    path = agent.save_trace(report, TRACE)
    print(f"\n=== provenance written to {path.name} ({len(report.provenance)} steps)")

    def reinvoke(name: str, inputs: dict) -> object:
        return agent.invoke(name, **inputs).value

    replay = ProvenanceLog.replay(path, reinvoke)
    print(f"  replay: {replay['n_reproduced']}/{replay['n_steps']} steps reproduced "
          f"(fully={replay['fully_reproduced']})")
    for s in replay["steps"]:
        print(f"    step {s['step']}: {s['capability'][:44]:<45} reproduced={s['reproduced']}")


if __name__ == "__main__":
    main()
