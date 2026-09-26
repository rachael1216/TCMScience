"""HTTP backend — public REST and GraphQL data sources as components.

222 catalogue components declare the `http` backend and had nothing to run them.
This backend executes them, with the properties a shared scientific client needs:

* **per-host rate limiting** — public APIs publish limits (NCBI: 3 req/s without
  a key, Ensembl: 15 req/s); exceeding them gets everyone throttled.
* **bounded retries with backoff** on 429/5xx and transient socket errors.
* **response-size cap** so a mis-specified query cannot pull a 2 GB body into RAM.
* **on-disk response cache** keyed by the request hash, so replay is exact and
  repeated lookups are free.
* **honest statuses** — a network denial is `UNAVAILABLE` with the reason, a 4xx
  is `FAILED` with the body excerpt, a timeout is `TIMEOUT`; nothing is `ok`
  unless a 2xx body was parsed.

Only the standard library is used, so the backend works in any environment the
harness itself runs in.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..runtime.component import ComponentManifest
from ..status import ExecutionStatus
from .base import Backend

_USER_AGENT = "bioagent-harness/0.2 (+https://localhost; research use)"

#: Published or conservative per-host request rates (requests / second).
DEFAULT_RATES: Mapping[str, float] = {
    "eutils.ncbi.nlm.nih.gov": 3.0,
    "pubchem.ncbi.nlm.nih.gov": 5.0,
    "rest.ensembl.org": 15.0,
    "rest.uniprot.org": 10.0,
    "www.ebi.ac.uk": 10.0,
    "string-db.org": 1.0,
    "rest.kegg.jp": 3.0,
    "reactome.org": 5.0,
    "api.platform.opentargets.org": 5.0,
    "data.rcsb.org": 10.0,
    "clinicaltrials.gov": 3.0,
    "api.fda.gov": 4.0,          # 240/min without a key
    "gnomad.broadinstitute.org": 1.0,
    "mygene.info": 10.0,
    "myvariant.info": 10.0,
    # extended connector set (v2.4)
    "rest.genenames.org": 10.0,
    "api.ncbi.nlm.nih.gov": 5.0,
    "www.ncbi.nlm.nih.gov": 3.0,         # PubTator 3
    "api.genome.ucsc.edu": 5.0,
    "alphafold.ebi.ac.uk": 5.0,
    "gtexportal.org": 5.0,
    "www.encodeproject.org": 3.0,
    "api.cellxgene.cziscience.com": 3.0,
    "www.wikipathways.org": 3.0,
    "omnipathdb.org": 3.0,
    "dgidb.org": 3.0,
    "civicdb.org": 3.0,
    "www.cbioportal.org": 5.0,
    "api.gdc.cancer.gov": 5.0,
    "clinicaltables.nlm.nih.gov": 5.0,
    "rxnav.nlm.nih.gov": 10.0,
    "dailymed.nlm.nih.gov": 5.0,
    "id.nlm.nih.gov": 5.0,
    "api.crossref.org": 5.0,
    "api.openalex.org": 5.0,
    "api.biorxiv.org": 3.0,
    "ontology.jax.org": 5.0,
    "api.monarchinitiative.org": 5.0,
    "disease-ontology.org": 3.0,
    "bioregistry.io": 5.0,
    "resolver.api.identifiers.org": 5.0,
    "biit.cs.ut.ee": 2.0,
    "pantherdb.org": 2.0,
    "www.proteinatlas.org": 3.0,
    # Wikidata throttles shared cloud addresses to about one query a minute; declared with
    # margin so the limiter paces rather than the service refuses. Raise it with rates= on a
    # network Wikidata treats better.
    "query.wikidata.org": 1.0 / 90.0,
    "api.gbif.org": 5.0,
}


class _RateLimiter:
    """Token-bucket limiter per host; thread-safe."""

    def __init__(self, rates: Mapping[str, float], default_rps: float = 2.0) -> None:
        self._rates = dict(rates)
        self._default = default_rps
        self._next_ok: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> float:
        rps = self._rates.get(host, self._default)
        interval = 1.0 / max(rps, 0.01)
        with self._lock:
            now = time.monotonic()
            ready = self._next_ok.get(host, now)
            delay = max(0.0, ready - now)
            self._next_ok[host] = max(ready, now) + interval
        if delay > 0:
            time.sleep(delay)
        return delay


@dataclass
class HTTPRequest:
    """A fully specified request, hashable for caching."""

    url: str
    method: str = "GET"
    params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Any = None
    data: bytes | None = None
    accept: str = "application/json"

    @property
    def full_url(self) -> str:
        if not self.params:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return self.url + sep + urllib.parse.urlencode(
            {k: v for k, v in self.params.items() if v is not None}, doseq=True)

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    def key(self) -> str:
        """Cache key covering everything that changes the response.

        `accept` is sent on the wire but was missing here, so JSON and CSV
        representations of one URL collided on a single entry and the second
        caller silently got the first caller's format.
        """
        blob = json.dumps({"u": self.full_url, "m": self.method, "h": dict(self.headers),
                           "a": self.accept,
                           "j": self.json_body, "d": self.data.decode("latin1") if self.data else None},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


class HTTPBackend(Backend):
    """Executes `http`-backed components against public REST/GraphQL endpoints."""

    backend = "http"

    def __init__(self, *, cache_dir: Path | str | None = None, timeout_s: float = 30.0,
                 max_bytes: int = 64 * 1024 * 1024, max_retries: int = 3,
                 rates: Mapping[str, float] | None = None, default_rps: float = 2.0,
                 offline: bool = False) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.offline = offline
        self._limiter = _RateLimiter(rates or DEFAULT_RATES, default_rps)
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0, "bytes": 0}

    def available(self) -> bool:
        return not self.offline

    def unavailable_reason(self) -> str:
        return "backend constructed in offline mode" if self.offline else ""

    # ------------------------------------------------------------------ core
    def request(self, req: HTTPRequest, *, use_cache: bool = True) -> tuple[ExecutionStatus, Any, str, dict]:
        """Perform one request. Returns (status, parsed_value, error, meta)."""
        meta: dict[str, Any] = {"url": req.full_url, "method": req.method, "host": req.host,
                                "cached": False, "attempts": 0, "http_status": None}
        if self.offline:
            return ExecutionStatus.UNAVAILABLE, None, "backend is offline", meta

        cache_path = (self.cache_dir / f"{req.key()}.json.gz") if self.cache_dir else None
        if use_cache and cache_path and cache_path.exists():
            try:
                with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
                    rec = json.load(fh)
                self.stats["cache_hits"] += 1
                meta.update(cached=True, http_status=rec.get("http_status"))
                return ExecutionStatus.SUCCEEDED, rec["value"], "", meta
            except Exception:  # noqa: BLE001 - a corrupt cache entry is simply ignored
                pass

        body: bytes | None = None
        if req.json_body is not None:
            body = json.dumps(req.json_body).encode("utf-8")
        elif req.data is not None:
            body = req.data
        headers = {"User-Agent": _USER_AGENT, "Accept": req.accept, **dict(req.headers)}
        if req.json_body is not None:
            headers.setdefault("Content-Type", "application/json")

        last_err = ""
        for attempt in range(1, self.max_retries + 1):
            meta["attempts"] = attempt
            self._limiter.wait(req.host)
            self.stats["requests"] += 1
            try:
                r = urllib.request.Request(req.full_url, data=body, method=req.method, headers=headers)
                with urllib.request.urlopen(r, timeout=self.timeout_s) as resp:  # noqa: S310
                    meta["http_status"] = resp.status
                    ctype = resp.headers.get("Content-Type", "")
                    raw = self._read_capped(resp)
                self.stats["bytes"] += len(raw)
                value = self._parse(raw, ctype)
                if cache_path:
                    with gzip.open(cache_path, "wt", encoding="utf-8") as fh:
                        json.dump({"http_status": meta["http_status"], "value": value,
                                   "content_type": ctype, "fetched_at": time.time()}, fh, default=str)
                return ExecutionStatus.SUCCEEDED, value, "", meta
            except urllib.error.HTTPError as exc:
                meta["http_status"] = exc.code
                excerpt = ""
                try:
                    excerpt = exc.read(600).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                last_err = f"HTTP {exc.code} {exc.reason}: {excerpt[:300]}"
                if exc.code == 403 and ("sandbox" in excerpt.lower() or "proxy" in excerpt.lower()
                                        or "network policy" in excerpt.lower()):
                    return ExecutionStatus.UNAVAILABLE, None, f"network access denied for {req.host}: {last_err}", meta
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self.stats["retries"] += 1
                    time.sleep(min(8.0, 0.8 * (2 ** attempt)))
                    continue
                return ExecutionStatus.FAILED, None, last_err, meta
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                    http.client.IncompleteRead, http.client.RemoteDisconnected,
                    http.client.HTTPException) as exc:
                # IncompleteRead: the server closed a chunked stream early — a
                # transient the live tests hit on the first run; retry, never crash.
                reason = str(getattr(exc, "reason", exc))
                last_err = f"{type(exc).__name__}: {reason}"
                if "timed out" in reason.lower():
                    if attempt < self.max_retries:
                        self.stats["retries"] += 1
                        continue
                    return ExecutionStatus.TIMEOUT, None, last_err, meta
                if any(k in reason.lower() for k in ("refused", "proxy", "403", "network")):
                    return ExecutionStatus.UNAVAILABLE, None, f"cannot reach {req.host}: {last_err}", meta
                if attempt < self.max_retries:
                    self.stats["retries"] += 1
                    time.sleep(min(8.0, 0.8 * (2 ** attempt)))
                    continue
            except _TooLarge as exc:
                return ExecutionStatus.FAILED, None, str(exc), meta
            except Exception as exc:  # noqa: BLE001 - reported, never raised into the runtime
                return ExecutionStatus.FAILED, None, f"{type(exc).__name__}: {exc}", meta
        return ExecutionStatus.FAILED, None, last_err or "exhausted retries", meta

    def _read_capped(self, resp) -> bytes:
        chunks, total = [], 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_bytes:
                raise _TooLarge(f"response exceeded {self.max_bytes} bytes; refine the query")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse(raw: bytes, ctype: str) -> Any:
        text = raw.decode("utf-8", "replace")
        ct = ctype.lower()
        if "json" in ct or text[:1] in "{[":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        if "tab-separated" in ct or "tsv" in ct or ("\t" in text[:2000] and "\n" in text[:2000]):
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if lines:
                hdr = lines[0].lstrip("#").split("\t")
                rows = [dict(zip(hdr, ln.split("\t"))) for ln in lines[1:]]
                return {"columns": hdr, "rows": rows, "n_rows": len(rows), "format": "tsv"}
        if "xml" in ct or text.lstrip().startswith("<"):
            return {"format": "xml", "text": text[:200_000], "truncated": len(text) > 200_000}
        return {"format": "text", "text": text[:200_000], "truncated": len(text) > 200_000}

    # --------------------------------------------------------------- Backend
    def invoke(self, manifest: ComponentManifest, *, path: str = "", method: str = "GET",
               params: Mapping[str, Any] | None = None, json_body: Any = None,
               headers: Mapping[str, str] | None = None, accept: str = "application/json",
               graphql: str | None = None, variables: Mapping[str, Any] | None = None,
               use_cache: bool = True, **_: Any) -> Any:
        """Execute against the component's declared server (base URL).

        `graphql=` turns the call into a POST with {query, variables}. Any host
        actually contacted must be in `permissions.network`, which the policy
        kernel has already checked — this is a second, local guard.
        """
        t0 = time.perf_counter()
        base = (manifest.runtime.server or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            return self._result(manifest, ExecutionStatus.UNAVAILABLE, t0,
                                error=f"component declares no http(s) server (got {base!r})")
        url = base + ("/" + path.lstrip("/") if path else "")
        host = urllib.parse.urlsplit(url).hostname or ""
        allowed = {h.lower() for h in manifest.permissions.network}
        # Deny by default. `if allowed and ...` inverted the rule the docstring
        # states: a component declaring no hosts skipped the check entirely, so
        # an empty permissions.network was the most permissive setting there was
        # rather than the least. An undeclared endpoint is now refused.
        if not allowed:
            return self._result(manifest, ExecutionStatus.DENIED, t0,
                                error=("component declares no permissions.network; an http "
                                       f"component must declare the hosts it contacts (wanted {host})"))
        if host.lower() not in allowed:
            return self._result(manifest, ExecutionStatus.DENIED, t0,
                                error=f"host {host} not declared in permissions.network {sorted(allowed)}")
        if graphql is not None:
            req = HTTPRequest(url=url, method="POST",
                              json_body={"query": graphql, "variables": dict(variables or {})},
                              headers=dict(headers or {}), accept="application/json")
        else:
            req = HTTPRequest(url=url, method=method.upper(), params=dict(params or {}),
                              headers=dict(headers or {}), json_body=json_body, accept=accept)
        status, value, err, meta = self.request(req, use_cache=use_cache)
        if status is ExecutionStatus.SUCCEEDED and graphql is not None and isinstance(value, dict) \
                and value.get("errors"):
            status, err = ExecutionStatus.FAILED, f"graphql errors: {json.dumps(value['errors'])[:400]}"
        return self._result(manifest, status, t0, value=value, error=err or None, metadata=meta)


class _TooLarge(RuntimeError):
    pass


def hostname(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


_SLUG = re.compile(r"[^a-z0-9_.-]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")
