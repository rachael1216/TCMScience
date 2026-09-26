"""Adapter for the Biomni data lake (Apache-2.0 upstream; local files).

Reads the 76-file data lake that Biomni declares in ``env_desc.py`` and
downloads from its release bucket. This is a *vendor*-mode adapter: Biomni is
Apache-2.0, so we may read its manifest and load the files directly.

The datasets themselves carry their own upstream licenses (BindingDB, DepMap,
genebass, ...) and are not redistributed by this package.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .base import Adapter, AdapterError, CallResult


class DataLakeAdapter(Adapter):
    """Loads local data-lake files by capability name."""

    name = "biomni-datalake"
    integration_mode = "vendor"
    requires_network = False

    #: extensions this adapter knows how to load
    LOADABLE = {".parquet", ".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson"}

    def __init__(self, lake_dir: str | Path) -> None:
        self.lake_dir = Path(lake_dir)

    def available(self) -> bool:
        return self.lake_dir.is_dir()

    def can_handle(self, capability: Any) -> bool:
        kind = getattr(capability, "kind", None)
        if kind != "dataset":
            return False
        return (self.lake_dir / getattr(capability, "name", "")).exists()

    def path_for(self, capability: Any) -> Path:
        p = self.lake_dir / getattr(capability, "name", str(capability))
        if not p.exists():
            raise AdapterError(f"dataset not present in data lake: {p.name}")
        return p

    def invoke(self, capability: Any, *, nrows: int | None = 5,
               columns: list[str] | None = None, **_: Any) -> CallResult:
        """Load a bounded slice of the dataset.

        Parquet is read with pyarrow `iter_batches` so a head() request does not
        materialize the whole file (the v1 loader called `pd.read_parquet(...)`
        then `.head()`, peaking at 177 MB for a 69 MB file). JSON is parsed as
        JSON rather than handed to `read_csv`. Pickles are never auto-loaded,
        because unpickling executes arbitrary code.
        """
        from .base import CallResult  # local import keeps module import cheap
        from ..status import ExecutionStatus

        t0 = time.perf_counter()
        cap_name = getattr(capability, "name", str(capability))
        try:
            path = self.path_for(capability)
            suffix = path.suffix.lower()
            size = path.stat().st_size

            if suffix not in self.LOADABLE:
                value = {
                    "path": str(path), "size_bytes": size, "format": suffix.lstrip("."),
                    "loaded": False, "parsed_as": None,
                    "reason": "format not auto-loaded (pickle executes code on load; "
                              "ontology formats need a domain parser) - use the path directly",
                }
                return CallResult(capability=cap_name, adapter=self.name,
                                  status=ExecutionStatus.DEGRADED, value=value,
                                  duration_s=time.perf_counter() - t0)

            if suffix in (".json", ".jsonl", ".ndjson"):
                value = self._load_json(path, nrows)
            elif suffix == ".parquet":
                value = self._load_parquet(path, nrows, columns)
            else:
                value = self._load_delimited(path, suffix, nrows, columns)
            value.update({"path": str(path), "size_bytes": size,
                          "format": suffix.lstrip("."), "loaded": True})
            return CallResult(capability=cap_name, adapter=self.name,
                              status=ExecutionStatus.SUCCEEDED, value=value,
                              duration_s=time.perf_counter() - t0)
        except FileNotFoundError as exc:
            return CallResult(capability=cap_name, adapter=self.name,
                              status=ExecutionStatus.UNAVAILABLE,
                              error=f"{type(exc).__name__}: {exc}",
                              duration_s=time.perf_counter() - t0)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            return CallResult(capability=cap_name, adapter=self.name,
                              status=ExecutionStatus.FAILED,
                              error=f"{type(exc).__name__}: {exc}",
                              duration_s=time.perf_counter() - t0)

    # ------------------------------------------------------------- loaders
    #: Files above this are not parsed whole; JSON Lines streams instead and a
    #: single large JSON document is refused rather than silently loaded.
    JSON_WHOLE_FILE_LIMIT = 256 * 1024 * 1024

    @classmethod
    def _load_jsonl(cls, path: Path, nrows: int | None) -> dict:
        """Read the first `nrows` records of a JSON Lines file, and no more."""
        head: list = []
        want = nrows if nrows else None
        n_scanned = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n_scanned += 1
                try:
                    head.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if want is not None and len(head) >= want:
                    break
        cols: list[str] = []
        for r in head:
            if isinstance(r, dict):
                cols += [c for c in r if c not in cols]
        return {"parsed_as": "jsonl", "shape": [len(head), len(cols)],
                "columns": cols[:40], "head": head[:3],
                "n_records_total": None, "streamed": True,
                "note": "JSON Lines read incrementally; total record count not scanned"}

    @classmethod
    def _load_json(cls, path: Path, nrows: int | None) -> dict:
        """Parse JSON as JSON. Records-shaped payloads become a table preview.

        `nrows` bounds what is *returned*; for a single JSON document it cannot
        bound what is *read*, because the value is only well-formed once the
        whole text is parsed. Calling `json.load()` unconditionally therefore
        contradicted the bounded-slice contract — `nrows=5` against an 8 GB file
        parsed all 8 GB into memory and then sliced. JSON Lines streams properly
        and is used whenever the file is one; an oversized single document is
        refused with the reason instead.
        """
        if path.suffix.lower() in (".jsonl", ".ndjson") or cls._looks_like_jsonl(path):
            return cls._load_jsonl(path, nrows)
        size = path.stat().st_size
        if size > cls.JSON_WHOLE_FILE_LIMIT:
            return {"parsed_as": "json", "shape": [0, 0], "columns": [], "head": [],
                    "bytes": size, "streamed": False,
                    "note": (f"single JSON document of {size / 1e9:.2f} GB exceeds the "
                             f"{cls.JSON_WHOLE_FILE_LIMIT / 1e9:.2f} GB whole-file parse limit; "
                             "a JSON document cannot be sliced without being parsed in full — "
                             "convert to JSON Lines or Parquet for bounded reads")}
        with open(path, encoding="utf-8", errors="replace") as fh:
            obj = json.load(fh)
        records = None
        if isinstance(obj, list):
            records = obj
        elif isinstance(obj, dict):
            for key in ("records", "data", "rows", "items"):
                if isinstance(obj.get(key), list):
                    records = obj[key]
                    break
        if records is not None:
            head = records[: nrows] if nrows else records
            cols: list[str] = []
            for r in head:
                if isinstance(r, dict):
                    cols += [c for c in r if c not in cols]
            return {"parsed_as": "json", "shape": [len(head), len(cols)],
                    "columns": cols[:40], "head": head[:3], "n_records_total": len(records)}
        keys = list(obj)[:40] if isinstance(obj, dict) else []
        return {"parsed_as": "json", "shape": [1, len(keys)], "columns": keys,
                "head": [{k: obj[k] for k in keys[:5]}] if keys else [obj]}

    @staticmethod
    def _looks_like_jsonl(path: Path, probe_bytes: int = 65536) -> bool:
        """Detect JSON Lines from the first line without reading the file."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                first = fh.readline(probe_bytes).strip()
                if not first.startswith("{") or not first.endswith("}"):
                    return False
                second = fh.readline(probe_bytes).strip()
        except OSError:
            return False
        if not second:
            return False
        try:
            json.loads(first)
        except json.JSONDecodeError:
            return False
        return second.startswith("{")

    @staticmethod
    def _load_parquet(path: Path, nrows: int | None, columns: list[str] | None) -> dict:
        """Stream row batches instead of materializing the whole file."""
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        schema_names = [str(n) for n in pf.schema_arrow.names]
        total_rows = pf.metadata.num_rows if pf.metadata is not None else None
        if nrows is None:
            df = pf.read(columns=columns).to_pandas()
        else:
            batch_size = max(1, min(int(nrows), 10_000))
            try:
                batch = next(pf.iter_batches(batch_size=batch_size, columns=columns))
                df = batch.to_pandas().head(nrows)
            except StopIteration:
                import pandas as pd
                df = pd.DataFrame(columns=columns or schema_names)
        return {"parsed_as": "parquet", "shape": list(df.shape),
                "columns": [str(c) for c in df.columns[:40]],
                "head": df.head(min(3, len(df))).to_dict(orient="records"),
                "n_rows_total": total_rows, "streamed": nrows is not None}

    @staticmethod
    def _load_delimited(path: Path, suffix: str, nrows: int | None,
                        columns: list[str] | None) -> dict:
        """CSV/TSV/TXT via pandas with an explicit row bound."""
        import pandas as pd

        sep = "\t" if suffix in {".tsv", ".txt"} else ","
        df = pd.read_csv(path, sep=sep, nrows=nrows, usecols=columns, low_memory=False)
        return {"parsed_as": "delimited", "shape": list(df.shape),
                "columns": [str(c) for c in df.columns[:40]],
                "head": df.head(min(3, len(df))).to_dict(orient="records")}
