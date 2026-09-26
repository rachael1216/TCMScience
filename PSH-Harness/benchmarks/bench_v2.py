"""v0.2 benchmarks: transformation coverage and the cost of mandatory classification.

A reviewer made a fair criticism of the v0.1 benchmark set. Reporting "taint preserved to
depth 1000" measured repeated application of one operation — ``Labeled.derive`` — which was
never the risky path. Taint was lost at *transformation boundaries*: a plain dict, a JSON
round trip, a tool return, a database write. Depth 1000 on the one operation that already
worked proved nothing about the ones that did not.

So this file measures **transformation coverage**: the fraction of boundary kinds across
which a label actually survives. It also measures what closing these invariants costs,
because mandatory classification on every ingress is not free and the overhead should be
stated rather than assumed.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from psh import *
from psh.config import PSHConfig
from psh.kernel import TrustedKernel
from psh.labels import deep_label_of, unwrap_deep
from psh.policy import PolicySnapshot
from psh.runtime import Runner

RESULTS: dict = {}
PHI = "Patient Alice Cheng, MRN 04851923, DOB 03/14/1952"


def _kernel(**kw):
    return TrustedKernel(PSHConfig(state_dir=Path(tempfile.mkdtemp())).ensure_dirs(), **kw)


def _time(fn, n=200):
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return {"median_ms": round(statistics.median(samples) * 1000, 4),
            "p95_ms": round(sorted(samples)[int(n * 0.95)] * 1000, 4)}


def bench_transformation_coverage() -> dict:
    """Does a label survive each kind of boundary a real payload crosses?

    Each case starts from PHI-labelled data, applies one transformation, and asks whether the
    result is still PHI. A case that answers "no" is a laundering path.
    """
    kernel = _kernel()
    labelled = kernel.classify(PHI, origin="note")
    cases: dict[str, bool] = {}

    # 1. derivation (the only boundary v0.1 measured)
    cases["derive"] = labelled.derive("summary text").label.sensitivity is Sensitivity.PHI

    # 2-5. container nesting
    cases["dict_value"] = deep_label_of({"q": labelled}).sensitivity is Sensitivity.PHI
    cases["nested_dict"] = deep_label_of({"a": {"b": {"c": labelled}}}).sensitivity is Sensitivity.PHI
    cases["list_element"] = deep_label_of([1, {"d": [labelled]}]).sensitivity is Sensitivity.PHI
    cases["tuple_in_set"] = deep_label_of({"s": frozenset([labelled])}).sensitivity is Sensitivity.PHI

    # 6. raw text nested in a plain container (no Labeled anywhere)
    cases["raw_nested_text"] = kernel.classify({"o": {"note": PHI}}).sensitivity is Sensitivity.PHI

    # 7. JSON serialization round trip — how tool and MCP payloads travel
    revived = json.loads(json.dumps(unwrap_deep({"note": labelled})))
    cases["json_round_trip"] = kernel.classify(revived).sensitivity is Sensitivity.PHI

    # 8. tool execution boundary
    class _Tool:
        @property
        def manifest(self):
            return ComponentManifest(id="t", name="t", kind=ComponentKind.TOOL,
                                     max_label=Sensitivity.PHI,
                                     destinations=(Destination.LOCAL_COMPUTE,))
        def invoke(self, payload, envelope):
            return {"summary": "elderly patient with heart failure"}

    out = kernel.broker.call_tool(_Tool(), labelled, kernel.envelope())
    cases["tool_return"] = out.label.sensitivity is Sensitivity.PHI

    # 9. persistence boundary
    from psh.workgraph import NodeKind
    node = kernel.persistence.commit_node(kind=NodeKind.EVIDENCE, title=f"Note: {PHI}",
                                          principal="bench", source_run="r1")
    cases["persistence_write"] = node.label.sensitivity is Sensitivity.PHI

    # 10. retrieval boundary — reading it back must not lose the label
    reloaded = kernel.graph.get(node.id)
    cases["persistence_read"] = reloaded.label.sensitivity is Sensitivity.PHI

    # 11. ingress on an unlabelled value
    cases["raw_ingress"] = kernel.ingress.ensure(PHI).label.sensitivity is Sensitivity.PHI

    # 12. ingress on a WRONGLY labelled value
    mislabelled = Labeled(value=PHI, label=DataLabel(Sensitivity.PUBLIC, shareable=True))
    cases["mislabelled_ingress"] = (
        kernel.ingress.ensure(mislabelled).label.sensitivity is Sensitivity.PHI)

    covered = sum(cases.values())
    print(f"\n== transformation coverage: {covered}/{len(cases)} boundaries preserve the label")
    for name, ok in cases.items():
        print(f"   {'PASS' if ok else 'LOST'}  {name}")
    return {"boundaries": len(cases), "preserved": covered,
            "coverage": round(covered / len(cases), 4), "cases": cases,
            "v01_comparison": ("v0.1 measured 1 boundary (derive) to depth 1000; it lost "
                               "the label at dict nesting, JSON, tool return and "
                               "persistence")}


def bench_classification_overhead() -> dict:
    """What mandatory ingress classification costs per call.

    The honest question raised by making classification unconditional: every model and tool
    call now re-classifies rather than trusting a supplied label.
    """
    kernel = _kernel()
    short, long = PHI, PHI * 40
    labelled = kernel.classify(short, origin="note")

    raw = _time(lambda: kernel.ingress.ensure(short, origin="bench"))
    pre = _time(lambda: kernel.ingress.ensure(labelled, origin="bench"))
    big = _time(lambda: kernel.ingress.ensure(long, origin="bench"), n=100)
    nested = _time(lambda: kernel.ingress.ensure(
        {"a": {"b": [{"c": short}]}, "d": list(range(50))}, origin="bench"), n=100)

    print(f"\n== ingress overhead: raw {raw['median_ms']}ms | already-labelled "
          f"{pre['median_ms']}ms | {len(long)}ch {big['median_ms']}ms | "
          f"nested {nested['median_ms']}ms")
    return {"raw_value": raw, "already_labelled": pre, "long_text_1900ch": big,
            "nested_container": nested,
            "note": ("an already-labelled value costs the same as a raw one, by design: "
                     "re-classification is what closes the mislabelled-payload hole, so "
                     "there is deliberately no fast path for a caller-supplied label")}


def bench_authority_check() -> dict:
    """The cost of one AuthorityLattice containment check across all dimensions."""
    from psh.kernel.authority import AuthorityLattice

    parent = RunEnvelope(budget=Budget(usd_hard=10.0, max_tool_calls=100))
    child = parent.restrict(risk=RiskTier.R0_TRIVIAL)
    subset = _time(lambda: AuthorityLattice.is_subset(child, parent), n=500)
    restrict = _time(lambda: parent.restrict(risk=RiskTier.R0_TRIVIAL), n=500)
    print(f"\n== authority: is_subset {subset['median_ms']}ms | "
          f"restrict {restrict['median_ms']}ms across "
          f"{len(AuthorityLattice.BUDGET_DIMENSIONS) + 7} dimensions")
    return {"is_subset": subset, "restrict": restrict,
            "dimensions": len(AuthorityLattice.BUDGET_DIMENSIONS) + 7}


def bench_release_path() -> dict:
    """End-to-end control-plane overhead of the 16-stage path, and the refusal path."""
    ABSTRACT = ("Empagliflozin reduced the combined risk of cardiovascular death or "
                "hospitalization for heart failure in HFpEF (hazard ratio, 0.79).")
    local = ModelProfile(id="local", provider="local", destination=Destination.LOCAL_MODEL,
                         max_label=Sensitivity.PHI)

    kernel = _kernel()
    good = Runner(kernel, model=local, policy=kernel.policy,
                  model_invoke=lambda p: "Empagliflozin reduced hospitalization "
                                         "for heart failure (PMID: 34449189).")
    # Each iteration asks a distinct question. Repeating an identical request would trip the
    # stuck-loop detector from iteration 3 onward — correct behaviour that would otherwise be
    # measured as a refusal rate.
    ok_samples, statuses = [], []
    for i in range(20):
        start = time.perf_counter()
        result = good.run(f"what was the effect in cohort {i}?",
                          sources={"34449189": ABSTRACT})
        ok_samples.append(time.perf_counter() - start)
        statuses.append(result.status)
    released = result.released_output is not None
    assert all(s == "ok" for s in statuses), f"a benign run was refused: {set(statuses)}"

    kernel2 = _kernel()
    bad = Runner(kernel2, model=local, policy=kernel2.policy,
                 model_invoke=lambda p: "Empagliflozin cures pancreatic cancer "
                                        "(PMID: 34449189).")
    bad_result = bad.run("cure?", sources={"34449189": ABSTRACT})

    print(f"\n== release path: {round(statistics.median(ok_samples) * 1000, 3)}ms median "
          f"over {len(result.stages)} stages | released={released} | "
          f"refusal withholds output={bad_result.released_output is None} | "
          f"quarantine retained={bool(bad_result.quarantine_ref)}")
    return {"stages": len(result.stages),
            "median_ms": round(statistics.median(ok_samples) * 1000, 3),
            "p95_ms": round(sorted(ok_samples)[18] * 1000, 3),
            "released_on_pass": released,
            "withheld_on_refusal": bad_result.released_output is None,
            "quarantine_ref_retained": bool(bad_result.quarantine_ref),
            "note": ("control-plane overhead only: no provider latency is included, since "
                     "every model call in this benchmark is a local stub")}


def bench_claim_support() -> dict:
    """The regression suite for claim support. Deliberately not called sensitivity."""
    kernel = _kernel()
    from psh.evidence import EvidenceRecord

    ABSTRACT = ("Empagliflozin reduced the combined risk of cardiovascular death or "
                "hospitalization for heart failure in patients with HFpEF. The primary "
                "outcome occurred in 415 of 2997 patients in the empagliflozin group and "
                "511 of 2991 in the placebo group (hazard ratio, 0.79; 95% CI, 0.69 to "
                "0.90; P<0.001).")
    record = EvidenceRecord.from_text(identifier="34449189", text=ABSTRACT,
                                      retrieved_by="sable.pubmed_fetch",
                                      retrieval_run="bench", retracted=False)
    faithful = [
        "Empagliflozin reduced hospitalization for heart failure in HFpEF.",
        "The hazard ratio was 0.79.",
        "The primary outcome occurred in 415 of 2997 patients on empagliflozin.",
    ]
    fabricated = [
        "Empagliflozin cures pancreatic cancer.",
        "Empagliflozin is contraindicated in all patients over 65.",
        "Empagliflozin reduced all-cause mortality by 87%.",
        "The hazard ratio was 0.31.",
        "Empagliflozin eliminated the need for insulin in all participants.",
        "Empagliflozin had no effect on heart-failure hospitalization.",
    ]
    tp = sum(kernel.verifier.verify_record(statement=s, record=record).supports
             for s in faithful)
    fp = sum(kernel.verifier.verify_record(statement=s, record=record).supports
             for s in fabricated)
    print(f"\n== claim support: {tp}/{len(faithful)} faithful accepted, "
          f"{fp}/{len(fabricated)} fabricated accepted")
    return {"faithful_total": len(faithful), "faithful_accepted": tp,
            "fabricated_total": len(fabricated), "fabricated_accepted": fp,
            "note": ("a 9-case regression suite, not a measure of sensitivity or "
                     "specificity: those words imply a representative sample from a "
                     "defined population, and these are hand-written cases")}


if __name__ == "__main__":
    RESULTS["transformation_coverage"] = bench_transformation_coverage()
    RESULTS["classification_overhead"] = bench_classification_overhead()
    RESULTS["authority_check"] = bench_authority_check()
    RESULTS["release_path"] = bench_release_path()
    RESULTS["claim_support"] = bench_claim_support()
    out = Path(__file__).parent / "v2_benchmark_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nwritten: {out}")
