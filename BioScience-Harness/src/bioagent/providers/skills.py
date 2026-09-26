"""Provider for SKILL.md-based ecosystems (K-Dense, ClawBio, PantheonOS).

These projects ship skills as directories with YAML frontmatter, so discovery is
a filesystem walk rather than an import. Skills carry no python entrypoint, so
they are declared with the `none` backend and reach READY only when a host agent
executes them — which the manifest states rather than implies.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                 Provider as ProvBlock, Requirements, RuntimeSpec,
                                 Validation)
from .base import Provider

_FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter reader (flat keys plus one nested level)."""
    m = _FM.match(text)
    if not m:
        return {}
    out: dict = {}
    stack: list[tuple[int, dict]] = [(0, out)]
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent < stack[-1][0]:
            stack.pop()
        target = stack[-1][1] if stack else out
        if line.startswith("- "):
            key = getattr(parse_frontmatter, "_last_key", None)
            if key:
                target.setdefault(key, [])
                if isinstance(target[key], list):
                    target[key].append(line[2:].strip().strip('"\''))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip(); v = v.strip().strip('"\'')
            parse_frontmatter._last_key = k
            if not v:
                child: dict = {}
                target[k] = child
                stack.append((indent + 2, child))
            else:
                target[k] = v
    return out


class SkillDirectoryProvider(Provider):
    """Discovers skills from a repo containing SKILL.md files."""

    def __init__(self, name: str, root: Path | str, license_spdx: str = "MIT",
                 commit: str = "") -> None:
        self.name = name
        self.root = Path(root)
        self.license_spdx = license_spdx
        self.commit = commit

    def available(self) -> bool:
        return self.root.is_dir()

    def discover(self) -> Iterator[ComponentManifest]:
        if not self.available():
            return
        for p in sorted(self.root.rglob("SKILL.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(text)
            nm = str(fm.get("name") or p.parent.name)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
            rel = str(p.relative_to(self.root))
            cid = f"{self.name.lower()}.skill.{re.sub(r'[^a-z0-9_.-]+','-',nm.lower())}"
            yield ComponentManifest(
                id=cid, kind="skill", name=nm,
                version=str(meta.get("version") or "0.1.0"),
                description=" ".join(str(fm.get("description", "")).split())[:400],
                domain=(str(tags[0]) if tags else "general"),
                provider=ProvBlock(project=self.name, commit=self.commit, source_path=rel),
                # A skill is a specification for a host agent, not a callable.
                runtime=RuntimeSpec(backend="none"),
                requires=Requirements(),
                permissions=Permissions(),
                license=LicenseSpec(spdx=str(fm.get("license") or self.license_spdx),
                                    integration_mode="vendor"),
                validation=Validation(smoke_test=str(meta.get("demo_data") or "")),
                offline_capable=False,
            )
