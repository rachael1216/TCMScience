"""Measure the rebuilt mechanics.

Every number in the report comes from here. The two that matter most are the overheads:
classification and taint tracking run on the critical path of every value entering the
system, so if they were expensive the governance layer would be bypassed in practice.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT.parent / "sable_pkg" / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from psh import (
    Autonomy, ComponentKind, ComponentManifest, ContextItem, Destination, ModelProfile,
    PSHConfig, RiskTier, RunEnvelope, Sensitivity, TrustedKernel,
)
from psh.capabilities import CapabilityRegistry
from psh.context import ContextCompiler
from psh.evidence import ClaimSupportVerifier
from psh.kernel.events import EventStore
from psh.runtime import Runner
from psh.workgraph import EdgeKind, NodeKind, WorkGraph

RESULTS: dict[str, object] = {}
NOTE = ("Patient: John Smith, MRN 04851923, DOB 03/14/1952, seen 07/02/2026. Admitted with "
        "decompensated heart failure, started on empagliflozin 10 mg daily. " * 4)
RESEARCH = ("Empagliflozin reduced the composite of cardiovascular death or heart-failure "
            "hospitalization (HR 0.79, 95% CI 0.69-0.90, p<0.001) in NCT03057951. " * 4)
ABSTRACT = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction, "
    "irrespective of the presence or absence of diabetes. The primary outcome occurred in "
    "415 of 2997 patients in the empagliflozin group and in 511 of 2991 patients in the "
    "placebo group (hazard ratio, 0.79; 95% confidence interval, 0.69 to 0.90; P<0.001). "
    "The effect was mainly related to a lower risk of hospitalization for heart failure.")


def _kernel(**kw):
    """A benchmark kernel whose policy states the authority the benchmarks exercise.

    Since v0.5 the policy is a ceiling: an envelope wider than it is refused at minting
    time. A benchmark that measures the public-remote gate therefore has to be run under a
    policy that permits a public destination — the point of the measurement is the gate's
    cost, not whether the run was allowed to ask.
    """
    from psh.contracts import Autonomy, RiskTier
    from psh.policy import PolicySnapshot

    policy = PolicySnapshot(
        profile_id="benchmark", max_data_label=Sensitivity.PHI,
        allowed_destinations=(Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL,
                              Destination.USER_OUTPUT, Destination.PERSISTENT,
                              Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE),
        autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL)
    return TrustedKernel(PSHConfig(state_dir=Path(tempfile.mkdtemp()), **kw).ensure_dirs(),
                         policy=policy)


def bench_classification():
    """Cost of labelling data on the critical path."""
    kernel = _kernel()
    out = {}
    for name, text in (("phi_note", NOTE), ("research_prose", RESEARCH)):
        timings = []
        for _ in range(200):
            t0 = time.perf_counter()
            kernel.classify(text, origin="bench")
            timings.append(time.perf_counter() - t0)
        label = kernel.classify(text, origin="bench").label
        out[name] = {
            "chars": len(text),
            "median_ms": round(statistics.median(timings) * 1000, 4),
            "p95_ms": round(sorted(timings)[int(len(timings) * 0.95)] * 1000, 4),
            "throughput_kb_per_s": round(len(text) / 1024 / statistics.median(timings), 1),
            "sensitivity": label.sensitivity.name,
            "categories": list(label.categories)}
    out["classifier"] = kernel.classifier.detector_name
    return out


def bench_taint():
    """Cost of propagating a label through a derivation chain."""
    kernel = _kernel()
    base = kernel.classify(NOTE, origin="bench")
    depths = {}
    for depth in (1, 10, 100, 1000):
        current = base
        t0 = time.perf_counter()
        for i in range(depth):
            current = current.derive(f"derived {i}", origin="bench")
        elapsed = time.perf_counter() - t0
        depths[f"depth_{depth}"] = {
            "total_ms": round(elapsed * 1000, 4),
            "us_per_derivation": round(elapsed / depth * 1e6, 3),
            "label_preserved": current.sensitivity.name}
    return depths


def bench_egress_gate():
    """Cost of an egress decision — run on every model and tool call."""
    kernel = _kernel()
    public = ModelProfile(id="pub", provider="cloud", destination=Destination.PUBLIC_REMOTE,
                          max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    phi = kernel.classify(NOTE, origin="bench")
    clean = kernel.classify(RESEARCH, origin="public_source")
    out = {}
    for name, value in (("refusal", phi), ("approval", clean)):
        timings = []
        for _ in range(2000):
            t0 = time.perf_counter()
            kernel.model_gateway.check(value, public)
            timings.append(time.perf_counter() - t0)
        out[name] = {"median_us": round(statistics.median(timings) * 1e6, 2),
                     "p95_us": round(sorted(timings)[int(len(timings) * 0.95)] * 1e6, 2)}
    out["decisions_recorded"] = len(kernel.model_gateway.decisions)
    return out


def bench_context_compiler():
    """Compilation on a realistically oversized candidate pool."""
    kernel = _kernel()
    compiler = ContextCompiler()
    envelope = kernel.envelope()
    items = [ContextItem(kind="instruction", content="You are a research assistant. " * 20)]
    for i in range(400):
        items.append(ContextItem(kind="memory", content=f"memory {i}: " + "detail " * 60,
                                 score=i % 7))
    for i in range(150):
        items.append(ContextItem(kind="evidence", content=f"evidence {i}: " + ABSTRACT[:300],
                                 score=i % 5))
    # 40 duplicates, to measure dedup
    items += [ContextItem(kind="memory", content="memory 3: " + "detail " * 60)] * 40
    items.append(ContextItem(kind="turn", content="What is the evidence in HFpEF?"))

    t0 = time.perf_counter()
    projection = compiler.compile(items=items, envelope=envelope, token_budget=8000,
                                 query="evidence for empagliflozin in HFpEF")
    elapsed = time.perf_counter() - t0
    raw_tokens = sum(i.tokens for i in items)
    # 40 exact duplicates of one item were added, so dedup should remove 39 of them and
    # keep every other distinct candidate.
    assert compiler.last_trace.after_dedup >= 500, (
        f"dedup collapsed distinct candidates: {compiler.last_trace.after_dedup} survived")
    return {"candidates": len(items), "raw_tokens": raw_tokens,
            "projection_tokens": projection.tokens, "budget": 8000,
            "reduction_ratio": round(1 - projection.tokens / raw_tokens, 4),
            "within_budget": projection.within_budget,
            "compile_ms": round(elapsed * 1000, 3),
            "trace": compiler.last_trace.as_dict(),
            "turn_retained": any(i.kind == "turn" for i in projection.items),
            "instruction_retained": any(i.kind == "instruction" for i in projection.items)}


def bench_hierarchical_retrieval():
    """Two-level retrieval versus flat retrieval over the same capability set.

    The review's claim is that a domain harness should cost ONE manifest of context rather
    than thousands of entries. This measures both the context saving and the latency.
    """
    kernel = _kernel()
    envelope = kernel.envelope(allowed_destinations=[
        Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE, Destination.USER_OUTPUT])

    def make_capability(i: int, domain: str) -> ComponentManifest:
        return ComponentManifest(
            id=f"{domain}.cap{i}", name=f"{domain} capability {i}", kind=ComponentKind.TOOL,
            description=f"performs {domain} operation {i} on datasets",
            domain=domain, intents=(f"{domain} task",), tags=(domain,),
            destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI)

    DOMAINS = ["omics", "imaging", "statistics", "literature", "writing", "coding"]
    PER_DOMAIN = 420          # 2,520 capabilities, near the review's 2,567 figure

    # --- flat: everything at top level -----------------------------------------
    flat = CapabilityRegistry()
    for domain in DOMAINS:
        for i in range(PER_DOMAIN):
            flat.register(make_capability(i, domain))
    t0 = time.perf_counter()
    flat_hits = flat.resolve("statistics task on a dataset", envelope, limit=8,
                             include_nested=False)
    flat_ms = (time.perf_counter() - t0) * 1000
    flat_considered = flat.last_trace.considered

    # --- hierarchical: one manifest per domain, capabilities nested -------------
    tiered = CapabilityRegistry()
    for domain in DOMAINS:
        tiered.register(ComponentManifest(
            id=f"{domain}-harness", name=f"{domain} harness", kind=ComponentKind.HARNESS,
            description=f"{domain} domain harness", domain=domain,
            intents=(f"{domain} task",), tags=(domain,),
            destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI))
        for i in range(PER_DOMAIN):
            tiered.register(make_capability(i, domain), under=f"{domain}-harness")
    t0 = time.perf_counter()
    tiered_hits = tiered.resolve("statistics task on a dataset", envelope, limit=8,
                                 domain_limit=2)
    tiered_ms = (time.perf_counter() - t0) * 1000

    flat_items = flat.manifest_items(flat_hits)
    tiered_items = tiered.manifest_items(tiered_hits)
    return {
        "total_capabilities": len(DOMAINS) * PER_DOMAIN,
        "flat": {"considered": flat_considered, "resolve_ms": round(flat_ms, 3),
                 "returned": len(flat_hits),
                 "top_hit": flat_hits[0].id if flat_hits else None},
        "hierarchical": {"considered": tiered.last_trace.considered,
                         "resolve_ms": round(tiered_ms, 3), "returned": len(tiered_hits),
                         "domains_selected": list(tiered.last_trace.domain_narrowed_to),
                         "top_hit": tiered_hits[0].id if tiered_hits else None},
        "candidates_examined_ratio": round(
            tiered.last_trace.considered / max(1, flat_considered), 4),
        "speedup": round(flat_ms / max(tiered_ms, 1e-9), 2),
        "context_tokens_flat": sum(i.tokens for i in flat_items),
        "context_tokens_hierarchical": sum(i.tokens for i in tiered_items),
        "registry": tiered.stats()}


def bench_claim_support():
    """Verification cost and accuracy on a labelled mini-corpus."""
    verifier = ClaimSupportVerifier()
    SUPPORTED = [
        "Empagliflozin reduced cardiovascular death or heart-failure hospitalization in HFpEF.",
        "The hazard ratio was 0.79.",
        "Empagliflozin lowered the risk of hospitalization for heart failure.",
    ]
    UNSUPPORTED = [
        "Empagliflozin cures type 2 diabetes and eliminates the need for insulin.",
        "Empagliflozin is contraindicated in all patients over 65.",
        "Empagliflozin reduced all-cause mortality by 87%.",
        "Empagliflozin always prevents hospitalization in every patient.",
        "Empagliflozin had no effect on heart-failure hospitalization.",
        "Metformin reduced fracture risk in postmenopausal women.",
    ]
    tp = sum(1 for s in SUPPORTED
             if verifier.verify(statement=s, identifier="1", source_text=ABSTRACT).supports)
    fp = sum(1 for s in UNSUPPORTED
             if verifier.verify(statement=s, identifier="1", source_text=ABSTRACT).supports)
    timings = []
    for _ in range(300):
        t0 = time.perf_counter()
        verifier.verify(statement=SUPPORTED[0], identifier="1", source_text=ABSTRACT)
        timings.append(time.perf_counter() - t0)
    return {"supported_cases": len(SUPPORTED), "correctly_supported": tp,
            "unsupported_cases": len(UNSUPPORTED), "false_supports": fp,
            "recall_on_supported": round(tp / len(SUPPORTED), 4),
            "specificity_on_unsupported": round(1 - fp / len(UNSUPPORTED), 4),
            "median_ms": round(statistics.median(timings) * 1000, 4),
            "abstract_chars": len(ABSTRACT)}


def bench_event_store():
    store = EventStore(Path(tempfile.mkdtemp()) / "events.db")
    n = 5000
    t0 = time.perf_counter()
    for i in range(n):
        store.append("tool_call", f"run_{i % 20}",
                     detail={"component_probe": f"c{i % 9}", "index": i})
    append_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    result = store.verify()
    verify_s = time.perf_counter() - t0
    return {"records": n, "append_ms_each": round(append_s / n * 1000, 4),
            "verify_seconds": round(verify_s, 4),
            "verify_records_per_s": int(n / verify_s), "intact": bool(result),
            "db_bytes": (store.path.stat().st_size)}


def bench_workgraph():
    graph = WorkGraph(Path(tempfile.mkdtemp()) / "index.db")
    project = graph.project("bench project")
    pid = project.project_id
    t0 = time.perf_counter()
    nodes = []
    for i in range(1000):
        node = graph.add(NodeKind.RUN if i % 3 else NodeKind.EVIDENCE, f"node {i}",
                         project_id=pid)
        nodes.append(node)
        if i:
            # Direction matters: X --derived_from--> Y means X was derived FROM Y, so the
            # NEW node points back at the previous one. An earlier version linked these
            # the other way round and lineage() correctly reported depth 0.
            graph.link(node, nodes[i - 1], EdgeKind.DERIVED_FROM)
    build_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    lineage = graph.lineage(nodes[-1].id)
    lineage_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    why = graph.why(nodes[-1].id, max_depth=8)
    why_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    briefing = graph.briefing(pid)
    briefing_s = time.perf_counter() - t0
    return {"nodes": 1001, "edges": 999,
            "build_ms_per_node": round(build_s / 1001 * 1000, 4),
            "lineage_depth_found": len(lineage),
            "lineage_ms": round(lineage_s * 1000, 3),
            "why_lines": len(why), "why_ms": round(why_s * 1000, 3),
            "briefing_ms": round(briefing_s * 1000, 3),
            "briefing_chars": len(briefing)}


def bench_trusted_path():
    """End-to-end overhead of the 13-stage path, and where the time goes."""
    kernel = _kernel()
    local = ModelProfile(id="local", provider="local", destination=Destination.LOCAL_MODEL,
                         max_label=Sensitivity.PHI, usd_per_1k_input=0.0,
                         usd_per_1k_output=0.0)
    answer = ("Empagliflozin reduced cardiovascular death or heart-failure hospitalization "
              "in HFpEF (PMID: 34449189).")
    runner = Runner(kernel, model=local, model_invoke=lambda prompt: answer)
    timings, per_stage = [], {}
    for i in range(30):
        t0 = time.perf_counter()
        result = runner.run(f"question variant {i}: evidence in HFpEF?",
                            sources={"34449189": ABSTRACT})
        timings.append(time.perf_counter() - t0)
        for stage in result.stages:
            per_stage.setdefault(stage.stage, []).append(stage.seconds)
    return {"runs": len(timings),
            "median_ms": round(statistics.median(timings) * 1000, 3),
            "p95_ms": round(sorted(timings)[int(len(timings) * 0.95)] * 1000, 3),
            "stages": len(per_stage),
            "median_ms_by_stage": {k: round(statistics.median(v) * 1000, 4)
                                   for k, v in per_stage.items()},
            "all_stages_ok": all(s.ok for s in result.stages)}


if __name__ == "__main__":
    for name, fn in (("classification", bench_classification),
                     ("taint_propagation", bench_taint),
                     ("egress_gate", bench_egress_gate),
                     ("context_compiler", bench_context_compiler),
                     ("hierarchical_retrieval", bench_hierarchical_retrieval),
                     ("claim_support", bench_claim_support),
                     ("event_store", bench_event_store),
                     ("workgraph", bench_workgraph),
                     ("trusted_path", bench_trusted_path)):
        RESULTS[name] = fn()
        print(f"[done] {name}", flush=True)
    out = Path(__file__).parent / "psh_benchmark_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(json.dumps(RESULTS, indent=2))
