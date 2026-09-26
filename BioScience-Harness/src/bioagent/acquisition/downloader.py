"""Resumable, checksum-verified downloader (standard library only)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_UA = "bioagent-harness/0.2 (+https://localhost; research use)"


class DownloadError(RuntimeError):
    """Raised when a fetch cannot complete or fails verification."""


@dataclass
class DownloadResult:
    url: str
    path: Path
    bytes: int
    verified: bool
    checksum: str
    resumed: bool
    duration_s: float
    from_cache: bool = False


class Downloader:
    """Fetch files into `root` with resume, verification and atomic completion.

    * Partial data lives in `<name>.part`; the final name appears only after the
      checksum (or, absent one, the declared size) verifies.
    * A `Range` request resumes an interrupted `.part` when the server supports it.
    * `size_gate_bytes` refuses to start anything larger without `confirm=True`,
      so an agent cannot trigger a 6 GB pull as a side effect.
    * The environment handed to any subprocess is never consulted here; this
      module makes direct HTTPS calls and reads no credentials.
    """

    def __init__(self, root: Path | str, *, timeout_s: float = 60.0, chunk: int = 1 << 20,
                 max_retries: int = 4, size_gate_bytes: int = 512 * 1024 * 1024,
                 log: Callable[[str], None] | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.chunk = chunk
        self.max_retries = max_retries
        self.size_gate_bytes = size_gate_bytes
        self._log = log or (lambda s: None)
        self.manifest_path = self.root / ".downloads.json"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _hash(path: Path, algo: str = "sha256") -> str:
        h = hashlib.new(algo)
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return f"{algo}:{h.hexdigest()}"

    def _remote_size(self, url: str) -> int | None:
        """Content-Length via HEAD, falling back to a 1-byte ranged GET."""
        for method, hdrs in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
            try:
                req = urllib.request.Request(url, method=method, headers={"User-Agent": _UA, **hdrs})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
                    cr = r.headers.get("Content-Range")
                    if cr and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        return int(cr.rsplit("/", 1)[1])
                    cl = r.headers.get("Content-Length")
                    if cl and cl.isdigit() and method == "HEAD":
                        return int(cl)
            except (urllib.error.URLError, OSError):
                continue
        return None

    def _record(self, res: DownloadResult) -> None:
        rec = {}
        if self.manifest_path.exists():
            try:
                rec = json.loads(self.manifest_path.read_text())
            except json.JSONDecodeError:
                rec = {}
        rec[res.path.name] = {"url": res.url, "bytes": res.bytes, "checksum": res.checksum,
                              "verified": res.verified, "fetched_at": time.time()}
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        os.replace(tmp, self.manifest_path)

    # --------------------------------------------------------------------- api
    def fetch(self, url: str, name: str, *, checksum: str | None = None,
              expected_bytes: int | None = None, confirm: bool = False,
              force: bool = False) -> DownloadResult:
        """Download `url` to `root/name`, verifying against checksum or size."""
        t0 = time.perf_counter()
        final = self.root / name
        part = self.root / (name + ".part")
        if final.exists() and not force:
            if checksum:
                got = self._hash(final, checksum.split(":")[0])
                if got.lower() == checksum.lower():
                    return DownloadResult(url, final, final.stat().st_size, True, got, False,
                                          time.perf_counter() - t0, from_cache=True)
                self._log(f"{name}: existing file fails checksum; re-downloading")
            elif expected_bytes is None or final.stat().st_size == expected_bytes:
                return DownloadResult(url, final, final.stat().st_size, expected_bytes is not None,
                                      "", False, time.perf_counter() - t0, from_cache=True)

        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "file":
            src = Path(urllib.request.url2pathname(parsed.path))
            shutil.copyfile(src, part)
            remote_size = src.stat().st_size
        else:
            remote_size = self._remote_size(url)
            # The most pessimistic estimate wins. `expected_bytes or remote_size`
            # took the manifest's declared size in preference to the server's, so
            # a manifest claiming 10 MB waved through a 20 GB body, and with
            # neither known the hint was 0 and the gate never applied at all.
            size_hint = max(expected_bytes or 0, remote_size or 0)
            if size_hint > self.size_gate_bytes and not confirm:
                raise DownloadError(
                    f"{name} is {size_hint / 1e9:.2f} GB, above the {self.size_gate_bytes / 1e9:.2f} GB "
                    "gate; call fetch(..., confirm=True) to proceed")
            # A server that reports no size cannot be gated up front, so the cap
            # is also enforced mid-stream and the transfer aborts on the byte
            # that crosses it.
            self._stream(url, part, remote_size,
                         max_bytes=None if confirm else self.size_gate_bytes)

        got_bytes = part.stat().st_size
        if expected_bytes is not None and got_bytes != expected_bytes:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{name}: size {got_bytes} != expected {expected_bytes}")
        if remote_size is not None and got_bytes != remote_size and parsed.scheme != "file":
            part.unlink(missing_ok=True)
            raise DownloadError(f"{name}: size {got_bytes} != server-reported {remote_size}")

        got_sum = self._hash(part, checksum.split(":")[0] if checksum else "sha256")
        verified = False
        if checksum:
            if got_sum.lower() != checksum.lower():
                part.unlink(missing_ok=True)
                raise DownloadError(f"{name}: checksum mismatch (got {got_sum[:23]}..., expected {checksum[:23]}...)")
            verified = True
        elif expected_bytes is not None or remote_size is not None:
            verified = True  # size-verified only; recorded as such

        os.replace(part, final)   # atomic: the final name never holds partial data
        res = DownloadResult(url, final, got_bytes, verified, got_sum, False, time.perf_counter() - t0)
        self._record(res)
        return res

    def _stream(self, url: str, part: Path, remote_size: int | None,
                *, max_bytes: int | None = None) -> None:
        """Fetch `url` into `part`, aborting if it grows past `max_bytes`.

        The running cap is what makes the size gate real: a server that sends no
        Content-Length gives nothing to check before the transfer starts, and
        without it a 20 GB body downloaded in full and was rejected only
        afterwards, having already spent the disk and the bandwidth.
        """
        attempt = 0
        while True:
            attempt += 1
            have = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": _UA}
            resumed = False
            if have and remote_size and have < remote_size:
                headers["Range"] = f"bytes={have}-"
                resumed = True
            elif have and remote_size and have >= remote_size:
                return
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
                    if resumed and r.status != 206:
                        have = 0          # server ignored Range: start over
                    mode = "ab" if (resumed and r.status == 206) else "wb"
                    with open(part, mode) as fh:
                        done = have
                        while True:
                            block = r.read(self.chunk)
                            if not block:
                                break
                            fh.write(block)
                            done += len(block)
                            if max_bytes is not None and done > max_bytes:
                                fh.close()
                                part.unlink(missing_ok=True)
                                raise DownloadError(
                                    f"{part.name}: aborted after {done / 1e9:.2f} GB, above the "
                                    f"{max_bytes / 1e9:.2f} GB gate; call fetch(..., confirm=True) "
                                    "to proceed")
                            if remote_size and done % (32 << 20) < self.chunk:
                                self._log(f"{part.name}: {done / 1e6:.0f}/{remote_size / 1e6:.0f} MB")
                return
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
                    body = ""
                    try:
                        body = exc.read(300).decode("utf-8", "replace").lower()
                    except Exception:  # noqa: BLE001
                        pass
                    if "sandbox" in body or "proxy" in body or "policy" in body:
                        raise DownloadError(f"network access denied for {urllib.parse.urlsplit(url).hostname}") from exc
                if attempt >= self.max_retries:
                    raise DownloadError(f"{part.name}: {type(exc).__name__}: {exc}") from exc
                self._log(f"{part.name}: retry {attempt} after {type(exc).__name__}")
                time.sleep(min(10.0, 1.5 * 2 ** attempt))
