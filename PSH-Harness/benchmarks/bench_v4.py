"""v0.4 benchmarks: the cost of each borrowed mechanism, measured not asserted.

Numbers here go into the report. Two of them decide whether the mechanisms will actually stay
switched on: the per-call cost of decode-and-rescan classification (it runs on EVERY ingress),
and the process-isolation overhead per tool call (a 200 ms tax on every tool would be disabled
by the second week).
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

from psh.config import PSHConfig
from psh.kernel import TrustedKernel
from psh.kernel.execpolicy import DEFAULT_RULES, ExecPolicy
from psh.kernel.hooks import HookEvent, callable_hook
from psh.kernel.isolation import IsolatedRunner, _HostPolicy

RESULTS: dict = {}
PHI = "Patient Alice Cheng, MRN 04851923, DOB 03/14/1952, seen 07/02/2026."
RESEARCH = ("In EMPEROR-Preserved (NCT03057977) empagliflozin reduced the combined risk of "
            "cardiovascular death or hospitalization for heart failure (HR 0.79; 95% CI 0.69-0.90).")


def _ms(samples):
    return round(statistics.median(samples) * 1000, 4)


def bench_classification():
    k = TrustedKernel(PSHConfig(state_dir=Path(tempfile.mkdtemp())).ensure_dirs())
    cases = {
        "plain_research_600c": RESEARCH * 3,
        "plain_phi": PHI,
        "base64_phi": base64.b64encode(PHI.encode()).decode(),
        "hex_phi": PHI.encode().hex(),
        "structured_10_fields": {f"f{i}": RESEARCH[:60] for i in range(10)},
        "random_blob_800c": base64.b64encode(os.urandom(600)).decode(),
    }
    out = {}
    for name, value in cases.items():
        t = []
        for _ in range(200):
            s = time.perf_counter(); k.classify(value, origin="bench"); t.append(time.perf_counter() - s)
        out[name] = {"median_ms": _ms(t), "label": k.classify(value).label.sensitivity.name}
    # v0.3 comparison: classification WITHOUT the decode layer, on the same research text.
    base = []
    for _ in range(200):
        s = time.perf_counter(); k.classifier.classify_text(RESEARCH * 3); base.append(time.perf_counter() - s)
    out["v03_text_only_600c_ms"] = _ms(base)
    out["decode_layer_overhead_x"] = round(out["plain_research_600c"]["median_ms"] / max(_ms(base), 1e-6), 2)
    print(f"== classification: research 600c {out['plain_research_600c']['median_ms']}ms "
          f"(v0.3 text-only {out['v03_text_only_600c_ms']}ms, {out['decode_layer_overhead_x']}x); "
          f"base64 PHI -> {out['base64_phi']['label']} in {out['base64_phi']['median_ms']}ms")
    RESULTS["classification"] = out


def bench_execpolicy():
    policy = ExecPolicy()
    cmds = ["ls -la", "git status", "rm -rf /", "git push origin main", "python -m pytest",
            "some_unknown_tool --flag x", "chmod -R 777 .", "grep -rn foo ."]
    t = []
    for _ in range(300):
        for c in cmds:
            s = time.perf_counter(); policy.evaluate(c); t.append(time.perf_counter() - s)
    # Load-time validation cost: how long to validate every default rule's examples.
    s = time.perf_counter()
    for _ in range(50):
        ExecPolicy.from_json(ExecPolicy().to_json())
    load = (time.perf_counter() - s) / 50
    RESULTS["execpolicy"] = {"rules": len(DEFAULT_RULES), "evaluate_median_ms": _ms(t),
                             "load_and_selftest_ms": round(load * 1000, 3)}
    print(f"== execpolicy: {len(DEFAULT_RULES)} rules, evaluate {_ms(t)}ms, load+self-test {load*1000:.2f}ms")


def bench_hooks():
    k = TrustedKernel(PSHConfig(state_dir=Path(tempfile.mkdtemp())).ensure_dirs())
    for i in range(5):
        k.hooks.register(callable_hook(f"h{i}", HookEvent.PRE_TOOL_USE, lambda p: None))
    t = []
    for _ in range(300):
        s = time.perf_counter()
        k.hooks.dispatch(HookEvent.PRE_TOOL_USE, target="x", run_id="r", payload={"tool_input": {}})
        t.append(time.perf_counter() - s)
    RESULTS["hooks"] = {"hooks": 5, "dispatch_5_hooks_median_ms": _ms(t)}
    print(f"== hooks: 5 in-process hooks dispatched in {_ms(t)}ms (incl. 5 audit events)")


def bench_isolation():
    runner = IsolatedRunner()
    wd = Path(tempfile.mkdtemp())
    iso = []
    for _ in range(8):
        s = time.perf_counter()
        runner.run([sys.executable, "-c", "pass"], workdir=wd, timeout_s=30)
        iso.append(time.perf_counter() - s)
    import subprocess
    bare = []
    for _ in range(8):
        s = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
        bare.append(time.perf_counter() - s)
    env_t = []
    from psh.kernel.isolation import build_child_environment
    for _ in range(500):
        s = time.perf_counter(); build_child_environment(proxy_address="http://127.0.0.1:1"); env_t.append(time.perf_counter() - s)
    pol = _HostPolicy(["*.ncbi.nlm.nih.gov", "clinicaltrials.gov", "api.crossref.org"])
    hp = []
    for _ in range(300):
        s = time.perf_counter(); pol.decide("eutils.ncbi.nlm.nih.gov", 443); hp.append(time.perf_counter() - s)
    RESULTS["isolation"] = {
        "isolated_python_spawn_median_ms": _ms(iso), "bare_python_spawn_median_ms": _ms(bare),
        "isolation_overhead_ms": round((_ms(iso) - _ms(bare)), 2),
        "build_child_env_ms": _ms(env_t), "host_policy_decide_ms": _ms(hp),
        "proxy_listener_available_here": False,
        "note": "this sandbox forbids loopback listeners; the runner fell back to the dead-port proxy "
                "(no network). Spawn overhead therefore excludes proxy start-up; measure on a workstation."}
    print(f"== isolation: isolated spawn {_ms(iso)}ms vs bare {_ms(bare)}ms "
          f"(overhead {RESULTS['isolation']['isolation_overhead_ms']}ms); env build {_ms(env_t)}ms; "
          f"host decision {_ms(hp)}ms")


def bench_event_concurrency():
    k = TrustedKernel(PSHConfig(state_dir=Path(tempfile.mkdtemp())).ensure_dirs())
    n_threads, per = 8, 50
    s = time.perf_counter()
    def spam(n):
        for i in range(per):
            k.events.append(f"t{n}", run_id="r", detail={"i": i})
    ts = [threading.Thread(target=spam, args=(n,)) for n in range(n_threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    dur = time.perf_counter() - s
    total = len(k.events.records())
    RESULTS["event_concurrency"] = {"threads": n_threads, "appends": n_threads * per,
                                    "records": total, "lost": n_threads * per - total,
                                    "seconds": round(dur, 3),
                                    "appends_per_s": round(n_threads * per / dur),
                                    "chain_intact": k.events.verify().intact}
    print(f"== events: {n_threads}x{per} concurrent appends -> {total} records, "
          f"{RESULTS['event_concurrency']['lost']} lost, {RESULTS['event_concurrency']['appends_per_s']}/s, "
          f"intact={RESULTS['event_concurrency']['chain_intact']}")


def bench_signing():
    from psh.evidence import EvidenceRecord
    from psh.evidence.signing import EvidenceSigner
    signer = EvidenceSigner(key=b"k" * 32)
    rec = EvidenceRecord.from_text(identifier="1", text=RESEARCH, retrieved_by="x", retrieval_run="r")
    st, vt = [], []
    for _ in range(500):
        s = time.perf_counter(); signed = signer.sign(rec); st.append(time.perf_counter() - s)
        s = time.perf_counter(); signer.verify(signed); vt.append(time.perf_counter() - s)
    RESULTS["evidence_signing"] = {"sign_ms": _ms(st), "verify_ms": _ms(vt)}
    print(f"== signing: sign {_ms(st)}ms, verify {_ms(vt)}ms")


if __name__ == "__main__":
    bench_classification(); bench_execpolicy(); bench_hooks(); bench_isolation()
    bench_event_concurrency(); bench_signing()
    out = Path(__file__).parent / "v4_benchmark_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nwritten: {out}")
