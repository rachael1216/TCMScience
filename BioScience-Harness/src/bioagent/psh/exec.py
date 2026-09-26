"""Isolated entrypoint: run one BioScience component inside a PSH child process.

PSH's ``IsolatedExecutor`` runs a component's entrypoint with a clean environment (no
``PYTHONPATH``, no inherited secrets, the kernel's egress proxy as the only route out),
writes ``{"tool", "run_id", "payload"}`` as one JSON object on stdin and expects one JSON
value on stdout. This script is that entrypoint for bridged components::

    python exec.py --manifest /path/to/component.yaml     # the admitted manifest
    python exec.py --component public.connector.hgnc      # by id, from the default runtime

It bootstraps ``bioagent`` from its own location because the environment carries no
path, builds the smallest runtime that can serve the component, and reports failures on
stderr with a non-zero exit so the kernel records a contract violation rather than a
result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):                            # run as a script by the kernel
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", help="path to the admitted BioScience manifest (YAML)")
    group.add_argument("--component", help="component id in the packaged catalogue/sources")
    parser.add_argument("--profile", default="biomedical-research")
    args = parser.parse_args(argv)

    from bioagent.psh.arguments import ArgumentError, arguments_for
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec
    from bioagent.runtime.component import ComponentManifest
    from bioagent.status import ExecutionStatus

    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"invalid request on stdin: {exc}", file=sys.stderr)
        return 2
    payload = request.get("payload") if isinstance(request, dict) else None
    payload = payload if isinstance(payload, dict) else {}

    if args.manifest:
        bio = ComponentManifest.load(args.manifest)
        connector = bio.id.startswith("public.connector.")
        runtime = default_runtime(catalogue=False, public_apis=connector,
                                  extra_manifests=() if connector else (bio,))
    else:
        runtime = default_runtime()
        bio = runtime.registry.get(args.component)
        if bio is None:
            print(f"no component {args.component!r}", file=sys.stderr)
            return 2

    source = None
    if bio.id.startswith("public.connector."):
        from bioagent.providers.public_apis import BY_KEY
        source = BY_KEY.get(bio.id.rsplit(".", 1)[-1])

    try:
        kwargs = arguments_for(payload, source=source, component_id=bio.id)
    except ArgumentError as exc:
        print(f"ContractViolation: {exc}", file=sys.stderr)
        return 1
    spec = AgentSpec(name="psh-isolated", permission_profile=args.profile)
    key = payload.get("_psh_idempotency_key")
    bookkeeping = {"idempotency_key": str(key)} if key else {}
    try:
        result = runtime.invoke(bio.id, spec=spec, **bookkeeping, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exit code is the contract
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if result.status is ExecutionStatus.TIMEOUT:
        # 124 is what ``timeout(1)`` exits with; the kernel reads it as a ToolTimeout.
        print(f"TIMEOUT: {(result.error or 'no detail')[:300]}", file=sys.stderr)
        return 124
    if result.status not in (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED):
        # The kernel records a contract violation with this text; keep it bounded.
        print(f"{result.status.value}: {(result.error or 'no detail')[:300]}", file=sys.stderr)
        return 1
    if result.status is ExecutionStatus.DEGRADED:
        # The one extension of the one-JSON-value protocol: the value, wrapped with the
        # shortfall it ran under, so the kernel records a degraded result with a caveat.
        sys.stdout.write(json.dumps({"$psh": {"status": "degraded",
                                              "reason": (result.error or "")[:300],
                                              "value": result.value}}, default=str))
        return 0
    sys.stdout.write(json.dumps(result.value, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
