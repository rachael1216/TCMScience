"""Execution isolation for generated analysis code.

Every surveyed project that executes LLM-written code does so in-process
(Biomni's execute node, AutoBA's CodeExecutor, GenoMAS's CodeExecutor), so a bad
generation can take down the agent or touch anything the agent can touch.

`HardenedExecutor` runs code in a separate interpreter with real OS resource
limits (address space, CPU, processes, file size), a scrubbed environment so
parent-process secrets do not leak, and a working-directory jail.

**What this does NOT provide.** Without a container runtime there is no kernel-
level filesystem or network isolation: code can still read files the user can
read and open sockets. `guarantees()` reports this honestly, and
`ContainerBackend` reports UNAVAILABLE rather than pretending otherwise. The v1
class was named `SandboxExecutor` while providing only a timeout — that name
overstated the protection, so the honest name is `HardenedExecutor` and
`SandboxExecutor` remains as a deprecated alias.
"""

from __future__ import annotations

import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Environment variables that are safe to forward to executed code.
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH",
                  "PYTHONHASHSEED", "PYTHONUNBUFFERED", "MPLBACKEND")


@dataclass
class ExecutionResult:
    """Outcome of one isolated code execution."""

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    duration_s: float
    workdir: str
    timed_out: bool = False
    limits_applied: dict = field(default_factory=dict)


def _probe_cache_dir() -> Path:
    """First writable of: $BIOAGENT_CACHE_DIR, ~/.cache/bioagent, <repo>/.cache, tempdir.

    Sandboxes frequently forbid writes under the home directory; a cache that
    silently fails to persist would make every fresh interpreter re-pay the probe.
    """
    candidates = []
    if os.environ.get("BIOAGENT_CACHE_DIR"):
        candidates.append(Path(os.environ["BIOAGENT_CACHE_DIR"]))
    candidates += [Path.home() / ".cache" / "bioagent",
                   Path(__file__).resolve().parents[3] / ".cache",
                   Path(tempfile.gettempdir()) / "bioagent-cache"]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".w"
            probe.write_text("1")
            probe.unlink()
            return c
        except OSError:
            continue
    return Path(tempfile.gettempdir())


class HardenedExecutor:
    """Runs Python source in a subprocess with OS resource limits.

    Parameters
    ----------
    timeout_s:
        Wall-clock limit.
    max_memory_mb:
        Address-space cap (RLIMIT_AS). None disables the cap.
    max_cpu_s:
        CPU-time cap (RLIMIT_CPU). Defaults to timeout_s.
    max_processes:
        Fork limit (RLIMIT_NPROC), blunting fork bombs.
    max_file_mb:
        Largest file the code may write (RLIMIT_FSIZE).
    scrub_env:
        When True (default), only `_ENV_ALLOWLIST` variables are forwarded, so
        credentials in the parent environment do not reach executed code.
    """

    def __init__(
        self,
        timeout_s: float = 60.0,
        workdir: str | Path | None = None,
        interpreter: str | None = None,
        max_output_chars: int = 20_000,
        max_memory_mb: int | None = 2048,
        max_cpu_s: float | None = None,
        max_processes: int | None = 64,
        max_file_mb: int | None = 512,
        scrub_env: bool = True,
    ) -> None:
        self.timeout_s = timeout_s
        self.interpreter = interpreter or sys.executable
        self.max_output_chars = max_output_chars
        self.max_memory_mb = max_memory_mb
        self.max_cpu_s = max_cpu_s if max_cpu_s is not None else timeout_s
        self.max_processes = max_processes
        self.max_file_mb = max_file_mb
        self.scrub_env = scrub_env
        self._explicit_workdir = Path(workdir) if workdir else None

    # ------------------------------------------------------------ guarantees
    _probe_cache: dict = {}

    @classmethod
    def probe_enforceable(cls) -> dict:
        """Empirically determine which rlimits this machine actually enforces.

        Cached per process. A limit counts as enforceable only if setting it in a
        child both succeeds AND changes behaviour; anything else is reported
        False, so a caller is never told a cap exists when it does not. On this
        machine the sandbox refuses to lower RLIMIT_AS/RLIMIT_DATA at all, so the
        memory cap is honestly reported as unenforced rather than assumed.
        """
        if cls._probe_cache:
            return dict(cls._probe_cache)
        # Disk cache keyed by (platform, python, executable): the probe costs ~1 s
        # of subprocess work per fresh interpreter and its answer only changes
        # when the machine or interpreter does.
        import hashlib as _hl
        import json as _json
        fp = _hl.sha256(f"{platform.platform()}|{sys.version}|{sys.executable}".encode()).hexdigest()[:16]
        cache_file = _probe_cache_dir() / f"rlimit_probe_{fp}.json"
        try:
            if cache_file.exists():
                cached = _json.loads(cache_file.read_text())
                if isinstance(cached, dict) and {"memory", "cpu", "file_size"} <= set(cached):
                    cls._probe_cache = dict(cached)
                    return dict(cached)
        except (OSError, ValueError):
            pass
        out: dict = {}
        checks = {
            "memory": ("RLIMIT_AS", 128, 1024 * 1024, "x = bytearray(600 * 1024 * 1024)"),
            "cpu": ("RLIMIT_CPU", 1, 1,
                    "s = 0\nwhile True:\n    s += 1"),
            "file_size": ("RLIMIT_FSIZE", 1, 1024 * 1024,
                          "open('probe.bin', 'wb').write(b'0' * (5 * 1024 * 1024))"),
        }
        for key, (rname, val, scale, work) in checks.items():
            if getattr(resource, rname, None) is None:
                out[key] = False
                continue
            code = (
                "import resource\n"
                f"w = resource.{rname}\n"
                f"v = {val} * {scale}\n"
                "applied = False\n"
                "for pair in ((v, v), (v, resource.getrlimit(w)[1]), (v, resource.RLIM_INFINITY)):\n"
                "    try:\n"
                "        resource.setrlimit(w, pair); applied = True; break\n"
                "    except Exception: continue\n"
                "import sys as _s\n"
                "print('APPLIED' if applied else 'NOTAPPLIED'); _s.stdout.flush()\n"
                f"{work}\n"
                "print('WORKCOMPLETED')\n"
            )
            try:
                with tempfile.TemporaryDirectory() as td:
                    proc = subprocess.run([sys.executable, "-I", "-c", code], cwd=td,
                                          capture_output=True, text=True, timeout=45)
                applied = "APPLIED" in proc.stdout and "NOTAPPLIED" not in proc.stdout
                blocked = "WORKCOMPLETED" not in proc.stdout
                # negative returncode == killed by signal (e.g. SIGXCPU = -24)
                killed = proc.returncode < 0            # terminated by signal
                out[key] = bool(blocked and (applied or killed))
            except Exception:
                out[key] = False
        out["processes"] = False  # not probed: fork-bomb probes are hostile to the host
        cls._probe_cache = dict(out)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(_json.dumps(out))
        except OSError:
            pass
        return dict(out)

    def effective_limits(self) -> dict:
        """Which configured caps are actually enforced on this machine."""
        enf = self.probe_enforceable()
        return {
            "memory_cap_mb": self.max_memory_mb if enf.get("memory") else None,
            "cpu_cap_s": self.max_cpu_s if enf.get("cpu") else None,
            "process_cap": self.max_processes if enf.get("processes") else None,
            "file_size_cap_mb": self.max_file_mb if enf.get("file_size") else None,
            "enforceable": enf,
        }

    def _preexec(self):  # pragma: no cover - runs in the child process
        """Post-fork hook: session detach only.

        Resource limits are applied by `_limit_prelude` inside the child, because
        setrlimit inside preexec_fn raises on darwin and subprocess converts that
        into an opaque SubprocessError — a silently unapplied limit is exactly the
        false guarantee this executor exists to avoid.
        """
        def apply():
            try:
                os.setsid()
            except OSError:
                pass
        return apply

    def guarantees(self) -> dict:
        """State exactly what this executor does and does not enforce here.

        Values come from an empirical probe, so a cap is never reported as
        active when the platform or sandbox refuses to apply it.
        """
        enf = self.probe_enforceable()
        eff = {
            "memory_cap_mb": self.max_memory_mb if enf.get("memory") else None,
            "cpu_cap_s": self.max_cpu_s if enf.get("cpu") else None,
            "process_cap": self.max_processes if enf.get("processes") else None,
            "file_size_cap_mb": self.max_file_mb if enf.get("file_size") else None,
        }
        return {
            "process_isolation": True,
            "wall_clock_timeout": True,
            # True only if at least one configured cap is genuinely enforced.
            "resource_limits": any(v is not None for v in eff.values()),
            "enforced": eff,
            "requested": {
                "memory_cap_mb": self.max_memory_mb, "cpu_cap_s": self.max_cpu_s,
                "process_cap": self.max_processes, "file_size_cap_mb": self.max_file_mb,
            },
            "enforceable_on_this_machine": enf,
            "environment_scrubbed": self.scrub_env,
            "cwd_jail": True,
            # Honest negatives — these require a container runtime.
            "container_isolation": False,
            "filesystem_isolation": False,
            "network": "not restricted (no container runtime available)",
            "platform": platform.system(),
        }

    def _limit_prelude(self) -> str:
        """Python source prepended to the child script to apply its own limits.

        Applying limits via preexec_fn is unreliable: on darwin setrlimit raises
        inside the post-fork hook (subprocess turns that into SubprocessError),
        and a swallowed exception there means limits silently never apply — the
        exact failure mode this executor exists to prevent. Setting them as the
        child's first statements is both portable and observable, and the child
        cannot skip them because they run before any supplied code.
        """
        parts = [
            "import resource as _r",
            "def _lim(_n, _v):",
            "    _w = getattr(_r, _n, None)",
            "    if _w is None or not _v: return False",
            "    try: _s, _h = _r.getrlimit(_w)",
            "    except Exception: return False",
            "    if _h != _r.RLIM_INFINITY: _v = min(_v, _h)",
            "    for _p in ((_v, _v), (_v, _h)):",
            "        try:",
            "            _r.setrlimit(_w, _p); return True",
            "        except Exception: continue",
            "    return False",
        ]
        if self.max_memory_mb:
            nb = int(self.max_memory_mb) * 1024 * 1024
            parts += [f"_lim('RLIMIT_AS', {nb})", f"_lim('RLIMIT_DATA', {nb})"]
        if self.max_cpu_s:
            parts.append(f"_lim('RLIMIT_CPU', {int(max(1, self.max_cpu_s))})")
        if self.max_processes:
            parts.append(f"_lim('RLIMIT_NPROC', {int(self.max_processes)})")
        if self.max_file_mb:
            parts.append(f"_lim('RLIMIT_FSIZE', {int(self.max_file_mb) * 1024 * 1024})")
        parts.append("del _lim, _r")
        return "\n".join(parts) + "\n"

    def _child_env(self) -> dict:
        if not self.scrub_env:
            return dict(os.environ)
        env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("MPLBACKEND", "Agg")
        return env

    # -------------------------------------------------------------------- run
    def run(self, code: str, *, extra_env: dict[str, str] | None = None) -> ExecutionResult:
        t0 = time.perf_counter()
        tmp_ctx = None
        if self._explicit_workdir:
            wd = self._explicit_workdir
            wd.mkdir(parents=True, exist_ok=True)
        else:
            tmp_ctx = tempfile.TemporaryDirectory(prefix="bioagent-exec-")
            wd = Path(tmp_ctx.name)
        script = wd / "step.py"
        script.write_text(self._limit_prelude() + code, encoding="utf-8")
        env = self._child_env()
        if extra_env:
            env.update(extra_env)
        env["TMPDIR"] = str(wd)
        limits = {k: v for k, v in self.guarantees().items()
                  if k in ("memory_cap_mb", "cpu_cap_s", "process_cap", "environment_scrubbed")}
        try:
            proc = subprocess.run(  # noqa: S603 - explicit interpreter, no shell
                [self.interpreter, "-I", str(script)],
                cwd=str(wd), capture_output=True, text=True,
                timeout=self.timeout_s, check=False, env=env,
                preexec_fn=self._preexec(),
            )
            return ExecutionResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout[: self.max_output_chars],
                stderr=proc.stderr[: self.max_output_chars],
                returncode=proc.returncode,
                duration_s=time.perf_counter() - t0,
                workdir=str(wd), limits_applied=limits,
            )
        except subprocess.TimeoutExpired as exc:
            def _dec(v):
                if isinstance(v, bytes):
                    return v.decode("utf-8", "replace")
                return v or ""
            return ExecutionResult(
                ok=False, stdout=_dec(exc.stdout)[: self.max_output_chars],
                stderr=f"timed out after {self.timeout_s}s",
                returncode=-1, duration_s=time.perf_counter() - t0,
                workdir=str(wd), timed_out=True, limits_applied=limits,
            )
        except MemoryError:
            return ExecutionResult(
                ok=False, stdout="", stderr="child exceeded memory limit",
                returncode=-1, duration_s=time.perf_counter() - t0,
                workdir=str(wd), limits_applied=limits,
            )
        finally:
            if tmp_ctx is not None and self._explicit_workdir is None:
                tmp_ctx.cleanup()


class SandboxExecutor(HardenedExecutor):
    """Deprecated alias. The v1 name overstated the isolation it provided."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
