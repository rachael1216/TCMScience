"""Component manifests — the single declarative unit the runtime composes.

A v1 capability was a catalogue row: a name, a description, some provenance. That
conflated four different things — being catalogued, being installed, being
loadable, and being executable — which is how the v1 report came to claim 621
capabilities were "routable" when nothing had ever been dispatched.

A `ComponentManifest` separates them. It declares what a component *is* (kind,
provider, version), how it would run (`runtime.backend` + entrypoint), what it
needs (`requires`), what it may touch (`permissions`), and how it is licensed —
and carries a `LifecycleState` that only ever advances through legal transitions.
Tools, skills, datasets, databases, planners, evaluators and agents all use this
one shape, so adding an ecosystem does not mean adding an `elif` branch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..status import LifecycleState, assert_transition

#: Component kinds the runtime understands.
KINDS = ("tool", "skill", "dataset", "database", "software", "benchmark",
         "agent", "planner", "evaluator", "memory", "workflow", "connector",
         "agent_role")

#: Execution backends a component may declare.
BACKENDS = ("python", "mcp", "http", "container", "dataset", "subprocess",
            "remote_agent", "none")


class _Pending(dict):
    """Placeholder for a nested value whose kind (map/list) is not yet known."""

    def __init__(self) -> None:
        super().__init__()
        self.items_list: list = []

    def append(self, v: Any) -> None:
        self.items_list.append(v)


def _finalize(obj: Any) -> Any:
    """Collapse _Pending placeholders into plain dicts/lists."""
    if isinstance(obj, _Pending):
        if obj.items_list and not dict(obj):
            return [_finalize(x) for x in obj.items_list]
        if not obj.items_list and not dict(obj):
            return {}
        return {k: _finalize(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _finalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finalize(x) for x in obj]
    return obj


class ManifestError(ValueError):
    """Raised when a manifest is structurally invalid."""


@dataclass
class Provider:
    """Where the component came from, pinned for reproducibility."""

    project: str = ""
    commit: str = ""
    repo: str = ""
    source_path: str = ""


@dataclass
class RuntimeSpec:
    """How the component would be executed."""

    backend: str = "none"
    entrypoint: str = ""     # python: "module:function"; mcp: tool name
    server: str = ""         # mcp/http: server or connector id
    image: str = ""          # container: image reference
    deterministic: bool = False

    def validate(self) -> list[str]:
        errs = []
        if self.backend not in BACKENDS:
            errs.append(f"runtime.backend {self.backend!r} not in {BACKENDS}")
        if self.backend == "python" and not self.entrypoint:
            errs.append("runtime.entrypoint required for the python backend")
        if self.backend == "mcp" and not (self.server or self.entrypoint):
            errs.append("runtime.server or entrypoint required for the mcp backend")
        if self.backend == "container" and not self.image:
            errs.append("runtime.image required for the container backend")
        return errs


@dataclass
class Requirements:
    """Everything that must be present before the component can run."""

    python: tuple[str, ...] = ()      # importable module names
    binaries: tuple[str, ...] = ()    # executables on PATH
    datasets: tuple[str, ...] = ()    # dataset component ids
    services: tuple[str, ...] = ()    # connector/service ids
    components: tuple[str, ...] = ()  # other component ids


@dataclass
class Permissions:
    """What the component is allowed to touch."""

    network: tuple[str, ...] = ()
    filesystem_read: tuple[str, ...] = ()
    filesystem_write: tuple[str, ...] = ()
    subprocess: bool = False


@dataclass
class LicenseSpec:
    spdx: str = "NONE"
    integration_mode: str = "federated"   # vendor | native | federated
    note: str = ""


@dataclass
class Validation:
    """How the component proves it works — required before promotion."""

    smoke_test: str = ""
    benchmarks: tuple[str, ...] = ()
    last_validated: str = ""


@dataclass
class ComponentManifest:
    """One composable unit. Everything in the system is one of these."""

    id: str
    kind: str
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    domain: str = ""
    omics_type: str = "general"
    provider: Provider = field(default_factory=Provider)
    runtime: RuntimeSpec = field(default_factory=RuntimeSpec)
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    requires: Requirements = field(default_factory=Requirements)
    permissions: Permissions = field(default_factory=Permissions)
    license: LicenseSpec = field(default_factory=LicenseSpec)
    validation: Validation = field(default_factory=Validation)
    offline_capable: bool = False
    native_connectors: tuple[str, ...] = ()
    signature: str = ""
    #: mutable runtime state — not authoritative when persisted
    state: LifecycleState = LifecycleState.DISCOVERED
    blocking_reason: str = ""

    # ------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Return a list of structural problems; empty means valid."""
        errs: list[str] = []
        if not self.id or " " in self.id:
            errs.append(f"id must be non-empty and space-free, got {self.id!r}")
        if self.kind not in KINDS:
            errs.append(f"kind {self.kind!r} not in {KINDS}")
        errs += self.runtime.validate()
        if self.license.integration_mode not in ("vendor", "native", "federated", "adapter-only"):
            errs.append(f"license.integration_mode {self.license.integration_mode!r} invalid")
        return errs

    def require_valid(self) -> "ComponentManifest":
        errs = self.validate()
        if errs:
            raise ManifestError(f"{self.id}: " + "; ".join(errs))
        return self

    # -------------------------------------------------------------- lifecycle
    def transition(self, new: LifecycleState, reason: str = "") -> "ComponentManifest":
        """Advance lifecycle state, refusing illegal transitions."""
        assert_transition(self.state, new)
        self.state = new
        if new in (LifecycleState.UNAVAILABLE, LifecycleState.QUARANTINED):
            self.blocking_reason = reason
        else:
            # any non-blocked state means the previous blocking reason is stale
            self.blocking_reason = ""
        return self

    def mark_recovered(self, state: LifecycleState = LifecycleState.AVAILABLE) -> "ComponentManifest":
        """Leave a blocked state directly (used by the resolver on re-check)."""
        self.state = state
        self.blocking_reason = ""
        return self

    def mark_unavailable(self, reason: str) -> "ComponentManifest":
        """Move to UNAVAILABLE from any state, recording why."""
        self.state = LifecycleState.UNAVAILABLE
        self.blocking_reason = reason
        return self

    @property
    def executable(self) -> bool:
        return self.state is LifecycleState.READY

    # ------------------------------------------------------------ serialize
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        d["native_connectors"] = list(self.native_connectors)
        for key in ("requires", "permissions"):
            v = d.get(key)
            if isinstance(v, dict):
                d[key] = {k: (list(vv) if isinstance(vv, tuple) else vv) for k, vv in v.items()}
        return d

    def to_yaml(self) -> str:
        """Serialize to a readable YAML subset without a yaml dependency."""
        def emit(obj: Any, indent: int = 0) -> list[str]:
            pad = "  " * indent
            lines: list[str] = []
            for k, v in obj.items():
                if isinstance(v, dict) and v:
                    lines.append(f"{pad}{k}:")
                    lines += emit(v, indent + 1)
                elif isinstance(v, dict):
                    lines.append(f"{pad}{k}: {{}}")
                elif isinstance(v, (list, tuple)):
                    if not v:
                        lines.append(f"{pad}{k}: []")
                    else:
                        lines.append(f"{pad}{k}:")
                        lines += [f"{pad}  - {json.dumps(str(i))}" for i in v]
                elif isinstance(v, bool):
                    lines.append(f"{pad}{k}: {str(v).lower()}")
                elif v is None or v == "":
                    lines.append(f'{pad}{k}: ""')
                else:
                    s = str(v)
                    risky = any(c in s for c in ':#{}[]|>&*!%@`"\n') or s != s.strip()
                    lines.append(f"{pad}{k}: {json.dumps(s) if risky else s}")
            return lines
        return "\n".join(emit(self.to_dict())) + "\n"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ComponentManifest":
        def sub(key: str, klass):
            raw = d.get(key) or {}
            if isinstance(raw, klass):
                return raw
            fields = set(klass.__dataclass_fields__)
            kw = {k: v for k, v in raw.items() if k in fields}
            for k, v in list(kw.items()):
                if isinstance(v, list):
                    kw[k] = tuple(v)
            return klass(**kw)

        state = d.get("state", LifecycleState.DISCOVERED)
        return cls(
            id=str(d["id"]), kind=str(d.get("kind", "tool")), name=str(d.get("name", "")),
            version=str(d.get("version", "0.1.0")), description=str(d.get("description", "")),
            domain=str(d.get("domain", "")), omics_type=str(d.get("omics_type", "general")),
            provider=sub("provider", Provider), runtime=sub("runtime", RuntimeSpec),
            inputs=dict(d.get("inputs") or {}), outputs=dict(d.get("outputs") or {}),
            requires=sub("requires", Requirements), permissions=sub("permissions", Permissions),
            license=sub("license", LicenseSpec), validation=sub("validation", Validation),
            offline_capable=bool(d.get("offline_capable", False)),
            native_connectors=tuple(d.get("native_connectors") or ()),
            signature=str(d.get("signature", "")),
            state=LifecycleState(state) if not isinstance(state, LifecycleState) else state,
            blocking_reason=str(d.get("blocking_reason", "")),
        )

    @staticmethod
    def _parse_yaml_subset(text: str) -> dict:
        """Parse the YAML subset emitted by `to_yaml` (nested maps, lists, scalars).

        Uses PyYAML when available; otherwise a small indentation parser that
        understands exactly what `to_yaml` writes, so a manifest always round-
        trips without a hard dependency.
        """
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except ImportError:
            pass
        root: dict = {}
        stack: list[tuple[int, Any]] = [(-1, root)]
        last_key: list[str | None] = [None]

        def scalar(v: str) -> Any:
            v = v.strip()
            if v.startswith('"'):
                return json.loads(v)
            if v in ("true", "false"):
                return v == "true"
            if v == "[]":
                return []
            if v == "{}":
                return {}
            if v == '""':
                return ""
            try:
                return int(v)
            except ValueError:
                try:
                    return float(v)
                except ValueError:
                    return v

        for raw in text.splitlines():
            if not raw.strip():
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            line = raw.strip()
            while stack and indent <= stack[-1][0]:
                stack.pop()
            container = stack[-1][1]
            if line.startswith("- "):
                # list item: the enclosing container is a _Pending placeholder
                if isinstance(container, (_Pending, list)):
                    container.append(scalar(line[2:]))
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            if val.strip() == "":
                # nested map or list follows; decide by peeking at the next line
                container[key] = None
                stack.append((indent, container))
                last_key[0] = key
                # placeholder replaced when the first child arrives
                container[key] = _Pending()
                stack.append((indent, container[key]))
            else:
                container[key] = scalar(val)
        return _finalize(root)

    @classmethod
    def from_yaml(cls, text: str) -> "ComponentManifest":
        return cls.from_dict(cls._parse_yaml_subset(text))

    @classmethod
    def load(cls, path: str | Path) -> "ComponentManifest":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_yaml(), encoding="utf-8")
        return p
