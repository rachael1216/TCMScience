"""Command-line entry point: ``psh``.

The subcommands answer the operational questions a governed system creates. ``verify`` and
``why`` matter most: the first asks whether the audit chain has been altered, the second
asks why a decision was made — the query the WorkGraph exists to answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PSHConfig
from .kernel import TrustedKernel
from .profiles import get_profile, profile_names
from .workgraph import NodeKind, WorkGraph


def _config(args: argparse.Namespace) -> PSHConfig:
    kw = {}
    if getattr(args, "state_dir", None):
        kw["state_dir"] = Path(args.state_dir)
    return PSHConfig(**kw).ensure_dirs()


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _config(args)
    if not cfg.event_store.exists():
        print(f"no event store at {cfg.event_store}")
        return 1
    kernel = TrustedKernel(cfg)
    result = kernel.events.verify(expected_head=args.expected_head)
    print(f"event store: {cfg.event_store}")
    print(f"events:      {len(kernel.events)}")
    print(f"head hash:   {kernel.events.head_hash}")
    print(f"result:      {result!r}")
    if args.events:
        for event_type, count in kernel.events.events().items():
            print(f"  {event_type}: {count}")
    return 0 if result.intact else 2


def cmd_why(args: argparse.Namespace) -> int:
    """Explain a node: the review's motivating query."""
    cfg = _config(args)
    graph = WorkGraph(cfg.index_store)
    node = graph.get(args.node_id)
    if node is None:
        print(f"no such node: {args.node_id}")
        return 1
    for line in graph.why(args.node_id, max_depth=args.depth):
        print(line)
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    cfg = _config(args)
    graph = WorkGraph(cfg.index_store)
    projects = graph.nodes(kind=NodeKind.PROJECT)
    if not projects:
        print("no projects recorded yet")
        return 1
    target = args.project or projects[0].project_id
    print(graph.briefing(target))
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    cfg = _config(args)
    graph = WorkGraph(cfg.index_store)
    if args.kind:
        for node in graph.nodes(kind=args.kind, project_id=args.project or ""):
            print(f"{node.id}  {node.kind.value:11s} [{node.status:11s}] {node.title}")
        return 0
    print(json.dumps(graph.stats(), indent=2))
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    if args.name:
        profile = get_profile(args.name)
        print(f"{profile.name}: {profile.description}\n")
        print(f"  destinations   : {', '.join(d.name for d in profile.destinations)}")
        print(f"  data ceiling   : {profile.max_label.name}")
        print(f"  autonomy       : {profile.autonomy.value}")
        print(f"  risk tier      : {profile.risk.name}")
        print(f"  claim support  : {profile.require_claim_support}")
        print(f"  citation req.  : {profile.require_citation}")
        print(f"  network egress : {profile.permits_network}")
        print(f"  token ceiling  : {profile.budget.tokens_hard:,}")
        print(f"  cost ceiling   : ${profile.budget.usd_hard:.2f}")
        print(f"\n  why: {profile.rationale}")
        for note in profile.notes:
            print(f"  note: {note}")
        return 0
    for name in profile_names():
        profile = get_profile(name)
        net = "network" if profile.permits_network else "LOCAL ONLY"
        print(f"  {name:19s} {profile.max_label.name:22s} {net:11s} {profile.description}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _config(args)
    kernel = TrustedKernel(cfg)
    report = kernel.report()
    graph = WorkGraph(cfg.index_store)
    report["workgraph"] = graph.stats()
    print(json.dumps(report, indent=2))
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Classify a file locally. Nothing leaves the machine."""
    text = (Path(args.path).read_text(encoding="utf-8", errors="replace")
            if args.path != "-" else sys.stdin.read())
    kernel = TrustedKernel(_config(args))
    labeled = kernel.classify(text, origin="cli")
    print(f"sensitivity : {labeled.label.sensitivity.name}")
    print(f"categories  : {', '.join(labeled.label.categories) or '(none)'}")
    print(f"classifier  : {labeled.label.classifier}")
    print(f"shareable   : {labeled.label.shareable}")
    print()
    print("May this reach:")
    from .labels import Destination
    for destination in Destination:
        verdict = "yes" if labeled.permits(destination) else "NO"
        print(f"  {destination.name:16s} {verdict}")
    print()
    print(kernel.classifier.coverage_note())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psh",
        description="Physician-Scientist Harness: a governed control plane.")
    parser.add_argument("--state-dir", help="override the harness state directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify", help="verify the event store's hash chain")
    p.add_argument("--expected-head", help="externally anchored head hash")
    p.add_argument("--events", action="store_true", help="also print event-type counts")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("why", help="explain a WorkGraph node (decision, claim, artifact)")
    p.add_argument("node_id")
    p.add_argument("--depth", type=int, default=8)
    p.set_defaults(func=cmd_why)

    p = sub.add_parser("brief", help="print the cross-session project briefing")
    p.add_argument("--project", default="")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("graph", help="inspect the WorkGraph")
    p.add_argument("--kind", choices=[k.value for k in NodeKind])
    p.add_argument("--project", default="")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("profiles", help="list or describe work-mode profiles")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("status", help="print harness state as JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("classify", help="classify a file locally")
    p.add_argument("path", help="file to classify, or - for stdin")
    p.set_defaults(func=cmd_classify)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
