"""PSH ⊕ BioScience in one run. **Live network**: it queries HGNC and UniProt.

A plan over two public biomedical sources, executed by PSH's bounded agent loop, with
every call crossing both kernels: PSH classifies the payload and gates it against the
destination; BioScience resolves, authorises and executes; PSH labels the result as the
join of what went in and what came back. The last section shows the same connector
refusing a query that carries an identifier — the transport is never reached.

    PYTHONPATH=src:../PSH-Harness/src python demo_convergence.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PSH-Harness" / "src"))

from psh.capabilities import CapabilityRegistry  # noqa: E402
from psh.config import PSHConfig  # noqa: E402
from psh.contracts import Autonomy, EgressDenied, ModelProfile, RiskTier  # noqa: E402
from psh.kernel import TrustedKernel  # noqa: E402
from psh.labels import Destination, Sensitivity  # noqa: E402
from psh.policy import PolicySnapshot  # noqa: E402
from psh.runtime import (  # noqa: E402
    AgentLoopController, Criterion, Plan, PlanTask, StaticPlanner, TaskKind,
)

from bioagent.psh import BioScienceBridge, default_runtime  # noqa: E402

PHI_TEXT = "Patient Alice Smith MRN 04851923"


def main() -> int:
    state = Path(tempfile.mkdtemp(prefix="psh-convergence-"))
    policy = PolicySnapshot(
        profile_id="convergence-demo", max_data_label=Sensitivity.PHI,
        allowed_destinations=(Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL,
                              Destination.USER_OUTPUT, Destination.PERSISTENT,
                              Destination.PUBLIC_REMOTE),
        autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R2_CONSEQUENTIAL,
        require_claim_support=False)
    kernel = TrustedKernel(PSHConfig(state_dir=state).ensure_dirs(), policy=policy)

    # 1. The capability plane, admitted. Connectors only, to keep the demo quick; pass
    #    catalogue=True to admit the 2,567-row catalogue as well.
    bridge = BioScienceBridge(kernel, default_runtime(catalogue=False))
    admitted = bridge.admit_all()
    registry = CapabilityRegistry()
    placed = bridge.register_into(registry)
    print(f"admitted {len(admitted)} connectors into {placed['harnesses']} domain harnesses; "
          f"refused {len(bridge.refusals)}")

    # 2. Two-level retrieval: domains first, then capabilities within them.
    envelope = kernel.policy.envelope()
    query = "find the HGNC record and the UniProt entry for the TP53 gene"
    for candidate in registry.resolve(query, envelope, limit=4):
        print(f"  {candidate.score:5.2f}  {candidate.id:34s} {candidate.reason}")
    print(f"  domains narrowed to: {list(registry.last_trace.domain_narrowed_to)}")

    # 3. A plan the loop executes through the broker. The model is a local stub: the
    #    point is the tool calls and their labels, not the prose.
    plan = Plan(
        objective=query, produced_by="demo",
        tasks=(
            PlanTask(task_id="hgnc", objective="HGNC record for TP53", kind=TaskKind.TOOL,
                     component_id="public.connector.hgnc",
                     payload={"operation": "symbol", "symbol": "TP53"}),
            PlanTask(task_id="uniprot", objective="UniProt entries for TP53", kind=TaskKind.TOOL,
                     component_id="public.connector.uniprot",
                     payload={"operation": "search", "query": "gene:TP53 AND organism_id:9606",
                              "size": 2}),
            PlanTask(task_id="summary", objective="one line naming the HGNC id and the "
                     "UniProt accession", kind=TaskKind.MODEL,
                     dependencies=("hgnc", "uniprot"), destinations=(Destination.LOCAL_MODEL,)),
        ),
        completion_criteria=(Criterion(description="the summary names both identifiers"),))
    local_model = ModelProfile(id="local-stub", provider="local",
                               destination=Destination.LOCAL_MODEL, max_label=Sensitivity.PHI,
                               usd_per_1k_input=0.0, usd_per_1k_output=0.0)
    loop = AgentLoopController(
        kernel, planner=StaticPlanner(plan), registry=registry, model=local_model,
        model_invoke=lambda prompt: f"summary over {len(prompt)} chars of evidence")
    result = loop.run(query, envelope)

    print(f"\nloop: {result.summary()}")
    hgnc = result.results.get("hgnc") or {}
    docs = (hgnc.get("response") or {}).get("docs") or [{}]
    print(f"  hgnc     -> {docs[0].get('hgnc_id')} {docs[0].get('name')!r}")
    uni = result.results.get("uniprot") or {}
    accessions = [r.get("primaryAccession") for r in (uni.get("results") or [])]
    print(f"  uniprot  -> {accessions}")
    print(f"  summary  -> {result.results.get('summary')!r}")
    print(f"  result label: {result.label}")
    print("  gate decisions:")
    for d in kernel.tool_gateway.decisions:
        print(f"    {d.target:28s} allowed={d.allowed!s:5} {d.destination.name:14s} "
              f"label={d.label.sensitivity.name}")
    print(f"  broker: {json.dumps(kernel.broker.stats())}")

    # 4. The same connector, handed an identifier: refused before the transport.
    print("\nrefusal:")
    try:
        kernel.broker.call_tool(bridge.component("public.connector.hgnc"),
                                {"operation": "symbol", "symbol": PHI_TEXT}, envelope)
    except EgressDenied as exc:
        print(f"  EgressDenied: {str(exc)[:140]}")
    print(f"  bioscience runtime calls for hgnc: "
          f"{bridge.component('public.connector.hgnc').calls} (the refused one is not among them)")

    # 5. The same identifiable payload, to a native clinical calculator: it never leaves the
    #    machine, so it runs, and its result carries the PHI label onward.
    from psh.labels import DataLabel, Labeled

    local = BioScienceBridge(kernel, default_runtime(catalogue=False, public_apis=False))
    local.admit_all()
    phi = Labeled({"creatinine_mg_dl": 1.4, "age_years": 67, "sex": "female"},
                  DataLabel(Sensitivity.PHI, categories=("medical_record_number",)))
    result = kernel.broker.call_tool(local.component("native.tool.egfr_ckd_epi_2021"), phi, envelope)
    print(f"\nlocal calculator on the PHI payload: eGFR {result.value['egfr_ml_min_1_73m2']} "
          f"({result.value['kdigo_stage']}), result label {result.label.sensitivity.name}; "
          f"{len(local.manifests())} native tools admitted, all LOCAL_COMPUTE at the PHI ceiling")
    kernel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
