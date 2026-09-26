"""Capability registry — queryable index over the unified capability catalogue.

The registry is the single source of truth about what the agent can do. It is
built by static extraction from 16 upstream biomedical agent projects; every
row carries its provenance (contributing projects, licenses, source paths) and
an integration mode that decides HOW a capability may be invoked:

    vendor        - permissively licensed; implementation may be reused directly
    adapter-only  - no license granted upstream; may only be invoked in place

Nothing in this package copies upstream code. The registry stores metadata and
routes calls; adapters do the invoking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

# Columns the catalogue is required to provide.
REQUIRED_COLUMNS = (
    "name",
    "kind",
    "domain",
    "omics_type",
    "contributing_projects",
    "licenses",
    "integration_mode",
    "availability",
)

KINDS = ("tool", "skill", "database", "dataset", "software", "benchmark", "agent_role")


@dataclass(frozen=True)
class Capability:
    """One distinct capability, possibly implemented by several projects."""

    name: str
    kind: str
    domain: str
    omics_type: str
    contributing_projects: tuple[str, ...]
    licenses: tuple[str, ...]
    integration_mode: str
    availability: str
    signature: str = ""
    description: str = ""
    external_deps: tuple[str, ...] = field(default_factory=tuple)
    native_connectors: tuple[str, ...] = field(default_factory=tuple)
    source_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def redistributable(self) -> bool:
        """True when upstream licensing permits reusing the implementation."""
        return self.integration_mode == "vendor"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.kind}:{self.name} [{'/'.join(self.contributing_projects)}]"


def _split(value: object) -> tuple[str, ...]:
    """Parse a ';'-delimited catalogue cell into a tuple."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ()
    return tuple(p.strip() for p in text.split(";") if p.strip())


class CapabilityRegistry:
    """Queryable index over the unified capability catalogue.

    Parameters
    ----------
    frame:
        Catalogue as a DataFrame. Use :meth:`from_csv` / :meth:`from_parquet`
        to load one from disk.
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"catalogue is missing required columns: {missing}")
        self._frame = frame.reset_index(drop=True)
        self._by_name: dict[str, list[int]] = {}
        for idx, name in enumerate(self._frame["name"].astype(str)):
            self._by_name.setdefault(name.lower(), []).append(idx)

    # ------------------------------------------------------------------ load
    @classmethod
    def from_csv(cls, path: str | Path) -> "CapabilityRegistry":
        return cls(pd.read_csv(path))

    @classmethod
    def from_parquet(cls, path: str | Path) -> "CapabilityRegistry":
        return cls(pd.read_parquet(path))

    # ------------------------------------------------------------- accessors
    def __len__(self) -> int:
        return len(self._frame)

    @property
    def frame(self) -> pd.DataFrame:
        """The underlying catalogue (a copy, so callers cannot mutate state)."""
        return self._frame.copy()

    def _row_to_capability(self, row: pd.Series) -> Capability:
        return Capability(
            name=str(row["name"]),
            kind=str(row["kind"]),
            domain=str(row.get("domain", "") or ""),
            omics_type=str(row.get("omics_type", "") or ""),
            contributing_projects=_split(row.get("contributing_projects")),
            licenses=_split(row.get("licenses")),
            integration_mode=str(row["integration_mode"]),
            availability=str(row["availability"]),
            signature=str(row.get("signature", "") or ""),
            description=str(row.get("description", "") or ""),
            external_deps=_split(row.get("external_deps")),
            native_connectors=_split(row.get("native_connectors")),
            source_paths=_split(row.get("source_paths")),
        )

    def get(self, name: str) -> Capability | None:
        """Exact (case-insensitive) lookup by capability name."""
        idxs = self._by_name.get(str(name).lower())
        if not idxs:
            return None
        return self._row_to_capability(self._frame.iloc[idxs[0]])

    def find(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        omics_type: str | None = None,
        project: str | None = None,
        availability: str | None = None,
        redistributable_only: bool = False,
        limit: int = 20,
    ) -> list[Capability]:
        """Filter the catalogue, optionally ranking by relevance to ``query``.

        ``query`` is matched against name, description and domain. Filters
        compose; ``redistributable_only`` keeps only vendorable capabilities.
        """
        df = self._frame
        if kind:
            if kind not in KINDS:
                raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
            df = df[df["kind"] == kind]
        if omics_type:
            df = df[df["omics_type"] == omics_type]
        if project:
            df = df[df["contributing_projects"].astype(str).str.contains(project, case=False, na=False)]
        if availability:
            df = df[df["availability"] == availability]
        if redistributable_only:
            df = df[df["integration_mode"] == "vendor"]

        if query:
            df = self._rank(df, query)
        return [self._row_to_capability(r) for _, r in df.head(limit).iterrows()]

    @staticmethod
    def _rank(df: pd.DataFrame, query: str) -> pd.DataFrame:
        """Score rows by term overlap: name hits weigh more than description."""
        terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 1]
        if not terms:
            return df
        name = df["name"].astype(str).str.lower()
        desc = df["description"].astype(str).str.lower() if "description" in df else pd.Series("", index=df.index)
        domain = df["domain"].astype(str).str.lower()
        score = pd.Series(0.0, index=df.index)
        for t in terms:
            score += name.str.count(re.escape(t)) * 3.0
            score += domain.str.contains(re.escape(t), regex=True).astype(float) * 1.5
            score += desc.str.contains(re.escape(t), regex=True).astype(float) * 1.0
        out = df.assign(_score=score)
        return out[out["_score"] > 0].sort_values("_score", ascending=False)

    # ------------------------------------------------------------- summaries
    def counts(self, by: str = "kind") -> dict[str, int]:
        if by not in self._frame.columns:
            raise ValueError(f"no such column: {by}")
        return self._frame[by].value_counts().to_dict()

    def projects(self) -> list[str]:
        """Distinct upstream projects, splitting '+'-joined composite labels."""
        seen: set[str] = set()
        for cell in self._frame["contributing_projects"]:
            for part in _split(cell):
                seen.update(p.strip() for p in part.split("+") if p.strip())
        return sorted(seen)

    def license_summary(self) -> dict[str, int]:
        return self._frame["integration_mode"].value_counts().to_dict()

    def coverage(self) -> pd.DataFrame:
        """Capability counts per omics modality and kind."""
        return pd.crosstab(self._frame["omics_type"], self._frame["kind"])
