"""Execute every operation of every public source and record what actually happened.

The connector table is a list of claims about other people's services. This script is
how the claims stay honest: it renders each operation from its own example arguments,
sends it through the real ``HTTPBackend`` (rate limits, retries and size caps included),
and writes one row per operation to ``data/connector_live_verification.csv``. A source is
shipped because its rows say SUCCEEDED, and the date on the row says when.

    PYTHONPATH=src python scripts/verify_connectors.py                # everything
    PYTHONPATH=src python scripts/verify_connectors.py --only hgnc ols # a subset
    PYTHONPATH=src python scripts/verify_connectors.py --no-write      # report only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.backends.http import HTTPBackend  # noqa: E402
from bioagent.providers.public_apis import SOURCES, PublicAPIProvider, render_call  # noqa: E402

FIELDS = ("source", "source_name", "operation", "smoke", "status", "http_status", "attempts",
          "latency_ms", "result_hash", "error", "host", "license", "verified_at")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="source keys to verify")
    ap.add_argument("--out", default=str(ROOT / "data" / "connector_live_verification.csv"))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    manifests = {m.id.rsplit(".", 1)[-1]: m for m in PublicAPIProvider().discover()}
    backend = HTTPBackend(cache_dir=None, timeout_s=args.timeout, max_retries=2)
    rows: list[dict] = []
    failures = 0
    for source in SOURCES:
        if args.only and source.key not in args.only:
            continue
        manifest = manifests[source.key]
        for op in source.operations:
            rendered = render_call(source.key, op.name)
            t0 = time.perf_counter()
            result = backend.invoke(manifest, use_cache=False, **rendered)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            meta = result.metadata or {}
            digest = ""
            if result.value is not None:
                blob = json.dumps(result.value, sort_keys=True, default=str).encode()
                digest = "sha256:" + hashlib.sha256(blob).hexdigest()[:12]
            ok = result.status.value == "SUCCEEDED"
            failures += 0 if ok else 1
            rows.append({
                "source": source.key, "source_name": source.name, "operation": op.name,
                "smoke": op.name == source.smoke, "status": result.status.value,
                "http_status": meta.get("http_status") or "", "attempts": meta.get("attempts") or "",
                "latency_ms": latency_ms, "result_hash": digest,
                "error": (result.error or "")[:160].replace("\n", " "), "host": source.host,
                "license": source.license,
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            flag = "ok " if ok else "FAIL"
            print(f"{flag} {source.key:22s} {op.name:22s} {result.status.value:11s} "
                  f"{meta.get('http_status') or '-':>4} {latency_ms:6d}ms {(result.error or '')[:90]}")

    total = len(rows)
    print(f"\n{total - failures}/{total} operations succeeded across "
          f"{len({r['source'] for r in rows})} sources")
    if not args.no_write:
        out = Path(args.out)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
