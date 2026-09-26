"""Process-isolated tool execution behind a kernel-owned egress proxy.

This module raises psh's boundary from *in-process cooperation* to *process level*. It is the
direct response to my own audit's finding A1: any code inside the kernel process can call a
provider and the broker sees nothing. Both surveyed systems answer that finding the same way
— the operating system enforces the boundary — and this is the portable part of their answer.

Borrowed from Codex, with file paths:

* **Default-deny environment** — `codex-rs/core/src/exec_env.rs` ("env_clear() to ensure no
  unintended variables are leaked to the spawned process"), `hooks/src/engine/command_runner.rs`.
  A tool subprocess starts from an EMPTY environment and receives only what the run envelope
  grants. A scrub list misses the variable you did not think of; ``env_clear()`` does not.
* **Egress proxy as the network boundary** — `codex-rs/network-proxy/README.md`: a local proxy
  enforcing an allow/deny host policy, with private-range destinations refused unless
  explicitly allowlisted. `exec-server-protocol/src/network_policy.rs` has the same three-way
  Allow / Deny / Ask decision as execpolicy. Here: a kernel-owned HTTP CONNECT + plain-HTTP
  proxy on loopback; the subprocess's ``HTTP_PROXY``/``HTTPS_PROXY`` point at it; every
  decision is an event.
* **Process hardening** — `codex-rs/process-hardening/src/lib.rs` strips ``LD_PRELOAD`` /
  ``DYLD_*`` and disables core dumps pre-main. The env_clear above already excludes the
  loader variables; core-dump limits are applied via ``resource`` in the child's preexec.

What is enforced here, and what is not
--------------------------------------
Enforced and tested from inside this session:

* A tool subprocess cannot read the kernel's environment (credentials, proxy settings for
  anything else, ``PYTHONPATH``).
* A subprocess that honours proxy variables — every mainstream HTTP client does by default —
  is refused for a host outside the envelope's allowlist, and the refusal is an event.
* Private and loopback destinations are refused by default even through the proxy.
* The subprocess runs under a wall-clock limit, a memory limit and with core dumps disabled.

NOT enforced here, and stated plainly: a subprocess that opens a raw socket, or sets
``no_proxy``, bypasses the proxy. Codex closes that with Seatbelt (macOS) / bubblewrap +
seccomp (Linux) denying network to the process; Claude Code with the same two. Neither can be
installed or verified from inside this session, so ``SandboxBackend`` is an adapter interface
with a ``NoSandbox`` implementation that says so in ``describe()``. The proxy is a *policy*
layer; the OS sandbox is the *enforcement* layer, and only the first ships here.
"""

from __future__ import annotations

import ipaddress
import json
import os
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from ..contracts import PolicyDenied

__all__ = ["IsolationReport", "EgressProxy", "ProxyUnavailable", "HostDecision", "IsolatedRunner", "IsolatedResult",
           "SandboxBackend", "NoSandbox", "build_child_environment", "IsolatedExecutor",
           "IsolationUnavailable", "RESERVED_ENV"]


# ------------------------------------------------------------------ host policy

@dataclass(frozen=True, slots=True)
class HostDecision:
    host: str
    port: int
    allowed: bool
    reason: str
    #: The addresses this decision vetted. The connection is made to one of *these*, never
    #: by re-resolving the name — see ``_HostPolicy.decide``.
    addresses: tuple[str, ...] = ()
    at: float = field(default_factory=time.time)


#: Ports a bare hostname pattern permits. A pattern may name its own (``host:8443``) or
#: open all of them (``host:*``). "The run may reach api.example.org" and "the run may reach
#: anything api.example.org happens to be listening on" are different capabilities, and the
#: second is not what an allowlist entry is usually meant to grant.
DEFAULT_PORTS: frozenset[int] = frozenset({80, 443})

#: Resolve a hostname to the addresses a connection would use. Injectable so a unit test
#: never touches DNS: in v0.4 ``_HostPolicy.decide`` called ``getaddrinfo`` unconditionally,
#: so tests that looked purely local blocked on name resolution and could not run offline.
Resolver = Callable[[str], "Sequence[str]"]


def _system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0].split("%")[0] for info in infos))


def _is_private_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


def _split_pattern(pattern: str) -> tuple[str, str | None]:
    """Split ``host[:port]``, leaving bracketed IPv6 literals intact."""
    if pattern.startswith("["):
        host, _, rest = pattern.partition("]")
        return host[1:], (rest[1:] if rest.startswith(":") else None)
    host, sep, port = pattern.rpartition(":")
    if not sep or not (port.isdigit() or port == "*"):
        return pattern, None
    return host, port


class _HostPolicy:
    """Decides host:port reachability, and resolves the name exactly once while doing it.

    Two properties the v0.4 policy did not have:

    * **No DNS rebinding.** It validated the name, then handed the *name* to
      ``socket.create_connection``, which resolved it again. A resolver that answers with a
      public address the first time and ``169.254.169.254`` the second turns an allowlist
      into an SSRF primitive. The decision now carries the vetted addresses and the handler
      connects to one of them.
    * **Ports are part of the capability.** ``api.example.org`` grants 80 and 443, not every
      port the host is listening on; ``api.example.org:8443`` grants that one.

    A name that does not resolve is refused rather than allowed to fail later: "we could not
    check it" is not "it is fine".
    """

    def __init__(self, allowed_hosts: Iterable[str], *, allow_private: bool = False,
                 audit: Callable[..., Any] | None = None, run_id: str = "",
                 resolver: Resolver | None = None) -> None:
        import fnmatch

        self._patterns = tuple(_split_pattern(h.strip().lower()) for h in allowed_hosts if h.strip())
        self._fn = fnmatch.fnmatch
        self.allow_private = allow_private
        self._audit = audit
        self.run_id = run_id
        self._resolve = resolver or _system_resolver
        self.decisions: list[HostDecision] = []
        self._lock = threading.Lock()

    def _matches(self, host: str, port: int) -> bool:
        for pattern_host, pattern_port in self._patterns:
            if not self._fn(host, pattern_host):
                continue
            if pattern_port is None:
                if port in DEFAULT_PORTS:
                    return True
            elif pattern_port == "*" or pattern_port == str(port):
                return True
        return False

    def _port_named_by_a_matching_pattern(self, host: str) -> bool:
        return any(self._fn(host, ph) for ph, _ in self._patterns)

    def decide(self, host: str, port: int) -> HostDecision:
        h = host.lower().rstrip(".").strip("[]")
        addresses: tuple[str, ...] = ()
        if not self._patterns:
            d = HostDecision(h, port, False, "no network destinations are allowed for this run")
        elif not self._matches(h, port):
            reason = (f"{h}:{port} is not in the run's allowed hosts"
                      if not self._port_named_by_a_matching_pattern(h)
                      else f"{h} is allowlisted but not on port {port}")
            d = HostDecision(h, port, False, reason)
        else:
            try:
                addresses = tuple(self._resolve(h)) if not _is_ip_literal(h) else (h,)
            except OSError as exc:
                d = HostDecision(h, port, False, f"{h} does not resolve ({exc}); refused")
                addresses = ()
            else:
                if not addresses:
                    d = HostDecision(h, port, False, f"{h} resolves to no address; refused")
                elif not self.allow_private and any(_is_private_address(a) for a in addresses):
                    d = HostDecision(
                        h, port, False,
                        f"{h} resolves to a private/loopback address; refused even though "
                        "allowlisted (SSRF guard)", addresses=addresses)
                else:
                    d = HostDecision(h, port, True, "allowlisted", addresses=addresses)
        with self._lock:
            self.decisions.append(d)
        if self._audit is not None:
            self._audit("egress_proxy_decision", host=h, port=port, allowed=d.allowed,
                        reason=d.reason[:120], run_id=self.run_id)
        return d


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private(host: str, resolver: Resolver | None = None) -> bool:
    """True for loopback, link-local, private and reserved addresses, resolving names.

    Retained for callers that only want the predicate. The proxy itself uses
    ``_HostPolicy.decide``, which resolves once and keeps the answer.
    """
    if _is_ip_literal(host):
        return _is_private_address(host)
    try:
        addresses = (resolver or _system_resolver)(host)
    except OSError:
        return False  # unresolvable: not private, and the connect will fail on its own
    return any(_is_private_address(a) for a in addresses)


# ------------------------------------------------------------------- the proxy

#: Headers that belong to one hop and must never be forwarded upstream. ``Proxy-*`` carry
#: whatever the child used to talk to the kernel's proxy; the rest are connection state.
_HOP_BY_HOP = frozenset({
    "proxy-connection", "proxy-authorization", "proxy-authenticate", "connection",
    "keep-alive", "te", "trailer", "transfer-encoding", "upgrade"})


class _ProxyHandler(BaseHTTPRequestHandler):
    """HTTP CONNECT tunnelling plus plain absolute-URI HTTP forwarding."""

    protocol_version = "HTTP/1.1"
    policy: _HostPolicy  # set on the server class

    def log_message(self, *_: Any) -> None:  # quiet; decisions are events, not stderr
        pass

    def _refuse(self, decision: HostDecision) -> None:
        body = json.dumps({"refused": True, "host": decision.host, "reason": decision.reason}
                          ).encode()
        self.send_response(403, "Forbidden by egress policy")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_CONNECT(self) -> None:  # noqa: N802
        host, _, port = self.path.rpartition(":")
        try:
            port_i = int(port or 443)
        except ValueError:
            self.send_error(400, "CONNECT requires host:port")
            return
        decision = self.server.policy.decide(host, port_i)  # type: ignore[attr-defined]
        if not decision.allowed:
            self._refuse(decision)
            return
        try:
            upstream = self._connect(decision, port_i)
        except OSError as exc:
            self.send_error(502, f"upstream connect failed: {exc}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._pipe(self.connection, upstream)
        self.close_connection = True

    def _forward(self) -> None:
        parts = urlsplit(self.path)
        if not parts.hostname:
            self.send_error(400, "proxy requires an absolute URI")
            return
        port = parts.port or 80
        decision = self.server.policy.decide(parts.hostname, port)  # type: ignore[attr-defined]
        if not decision.allowed:
            self._refuse(decision)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            upstream = self._connect(decision, port)
        except OSError as exc:
            self.send_error(502, f"upstream connect failed: {exc}")
            return
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        req = [f"{self.command} {path} HTTP/1.1"]
        # The Host header is derived from the authority the policy actually vetted, never
        # copied from the client. A child that connects to an allowlisted address while
        # naming a different virtual host in Host: reaches a site the policy never approved
        # — the ordinary shape of a shared-hosting bypass.
        authority = parts.hostname + ("" if port in (80, 443) else f":{port}")
        req.append(f"Host: {authority}")
        for k, v in self.headers.items():
            if k.lower() in _HOP_BY_HOP or k.lower() == "host":
                continue
            req.append(f"{k}: {v}")
        req.append("Connection: close")
        upstream.sendall(("\r\n".join(req) + "\r\n\r\n").encode() + body)
        while True:
            chunk = upstream.recv(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
        upstream.close()
        # One exchange per connection. We told the upstream "Connection: close"; the client
        # side closes too, so the handler never blocks waiting for a keep-alive follow-up.
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = _forward  # type: ignore[assignment]

    @staticmethod
    def _connect(decision: HostDecision, port: int) -> socket.socket:
        """Connect to an address this decision vetted, never by re-resolving the name."""
        last: OSError | None = None
        for address in decision.addresses or (decision.host,):
            try:
                return socket.create_connection((address, port), timeout=15)
            except OSError as exc:      # try the next vetted address, as a client would
                last = exc
        raise last or OSError(f"no vetted address for {decision.host}")

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        sockets = [a, b]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    break
                for s in readable:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except OSError:
            return
        finally:
            for s in sockets:
                try:
                    s.close()
                except OSError:
                    pass


class _ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    policy: _HostPolicy


class ProxyUnavailable(RuntimeError):
    """The host refused to let the kernel bind a loopback listener.

    Some sandboxes (including the one this package was developed in) forbid listening
    sockets altogether. The runner treats this as *no network for the child*, never as
    "run without the proxy" — see IsolatedRunner.run.
    """


class EgressProxy:
    """A kernel-owned HTTP proxy on loopback that enforces the run's host allowlist."""

    def __init__(self, allowed_hosts: Iterable[str], *, allow_private: bool = False,
                 audit: Callable[..., Any] | None = None, run_id: str = "",
                 resolver: Resolver | None = None) -> None:
        self.policy = _HostPolicy(allowed_hosts, allow_private=allow_private, audit=audit,
                                  run_id=run_id, resolver=resolver)
        try:
            self._server = _ProxyServer(("127.0.0.1", 0), _ProxyHandler)
        except PermissionError as exc:
            raise ProxyUnavailable(
                f"cannot bind a loopback listener for the egress proxy: {exc}") from exc
        self._server.policy = self.policy
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "EgressProxy":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True,
                                        name="psh-egress-proxy")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "EgressProxy":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def decisions(self) -> list[HostDecision]:
        return list(self.policy.decisions)

    def stats(self) -> dict[str, Any]:
        ds = self.decisions
        return {"decisions": len(ds), "allowed": sum(d.allowed for d in ds),
                "refused": sum(not d.allowed for d in ds)}


class _DeadProxy:
    """Stands in for the proxy when no network is wanted and no listener can be bound.

    Proxy variables point at a closed loopback port, so a proxy-honouring client fails to
    connect to anything. Same limit as the real proxy: a raw socket is not confined.
    """

    address = "http://127.0.0.1:9"
    decisions: list[HostDecision] = []

    def __enter__(self) -> "_DeadProxy":
        return self

    def __exit__(self, *exc: object) -> None:
        pass


# ------------------------------------------------------------ child environment

#: Variables a child may inherit from the kernel when the envelope grants "inherit_locale".
_SAFE_INHERIT = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "HOME")

#: Names the kernel owns in a child environment. A grant may not set them, and they are
#: written after every other source so nothing can displace them. Three families, each for
#: its own reason: the proxy variables ARE the egress boundary for a proxy-honouring client;
#: the loader variables decide what code the child actually executes; ``PATH`` /
#: ``PYTHONPATH`` decide which binary or module a name resolves to.
RESERVED_ENV: frozenset[str] = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy",
    "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE", "IFS", "BASH_ENV", "ENV", "SHELLOPTS", "GLIBC_TUNABLES",
    "NODE_OPTIONS", "PERL5OPT", "RUBYOPT"})


def _is_reserved(name: str) -> bool:
    upper = name.upper()
    return (name in RESERVED_ENV or upper in RESERVED_ENV
            or upper.startswith(("LD_", "DYLD_")))


def build_child_environment(*, proxy_address: str | None, grants: Mapping[str, str] | None = None,
                            inherit: Sequence[str] = _SAFE_INHERIT,
                            workdir: Path | None = None,
                            strict_grants: bool = True) -> dict[str, str]:
    """Build a subprocess environment from nothing (Codex ``env_clear`` pattern).

    Nothing is inherited by default except the short safe list. Loader variables
    (``LD_PRELOAD``, ``LD_LIBRARY_PATH``, ``DYLD_*``) can never be inherited because they
    are not in the safe list — there is no scrub step to get wrong.

    **Order matters, and v0.4 had it backwards.** It wrote the proxy variables and then
    applied the caller's grants over the top, so a grant of

        HTTP_PROXY=http://evil:9999   NO_PROXY=*

    replaced the kernel's own egress configuration in the child — the grant mechanism
    disabling the boundary it was supposed to run behind. The kernel's reserved variables
    are therefore applied **last**, and a grant naming one is refused outright rather than
    silently dropped: a caller who asked to set ``LD_PRELOAD`` should hear "no", not
    discover later that it did nothing.
    """
    env: dict[str, str] = {}
    for key in inherit:
        if key in os.environ and not _is_reserved(key):
            env[key] = os.environ[key]

    # 1. caller grants, which may set anything that is not reserved.
    for key, value in (grants or {}).items():
        name = str(key)
        if _is_reserved(name):
            if strict_grants:
                raise PolicyDenied(
                    f"{name} is reserved by the kernel and cannot be granted to a child "
                    "process: it selects the binary that runs, the libraries it loads, or "
                    "the egress path it takes")
            continue
        env[name] = str(value)

    # 2. kernel-reserved variables, applied last so nothing above can displace them.
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin") if "PATH" in inherit else "/usr/bin:/bin"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    if workdir is not None:
        env["HOME"] = str(workdir)
        env["TMPDIR"] = str(workdir)
    if proxy_address:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY",
                    "all_proxy"):
            env[key] = proxy_address
        env["NO_PROXY"] = env["no_proxy"] = ""
    return env


# ------------------------------------------------------------- sandbox backend

class SandboxBackend(Protocol):
    """OS-level enforcement: the layer this session cannot install or verify."""

    def wrap(self, argv: Sequence[str], *, workdir: Path, allow_network: bool) -> list[str]:
        """Return an argv that runs ``argv`` inside the sandbox."""
        ...

    def describe(self) -> str:
        ...


class NoSandbox:
    """No OS sandbox. Says so, rather than pretending."""

    #: What this backend confines. A real backend states its own; the default for a
    #: backend that says nothing is to be believed (it was installed on purpose), and this
    #: one says no.
    os_isolation = False

    def wrap(self, argv: Sequence[str], *, workdir: Path, allow_network: bool) -> list[str]:
        return list(argv)

    def describe(self) -> str:
        return ("no OS sandbox: filesystem and raw-socket access are NOT confined. The egress "
                "proxy governs HTTP clients that honour proxy variables; a raw socket bypasses "
                "it. Install a SandboxBackend (Seatbelt on macOS, bubblewrap+seccomp on Linux) "
                "for physical enforcement.")


@dataclass(frozen=True, slots=True)
class IsolationReport:
    """What the configured isolation actually provides, stated so a policy can check it.

    The 2026-09-18 review's F08: ``IsolatedRunner`` runs a component in a child process
    with a clean environment behind the egress proxy, and with ``NoSandbox`` that is all
    it does — the child's filesystem and raw-socket access are the parent's. The README
    said so in prose; nothing in the system could refuse on it. This report is the
    machine-readable form: a policy that requires OS-level isolation
    (``require_os_isolation``) is refused by a kernel whose report says it has none,
    rather than run on a promise.
    """

    sandbox: str
    os_isolation: bool
    process_isolation: bool = True
    clean_environment: bool = True
    egress_proxy: bool = True
    raw_sockets_confined: bool = False
    filesystem_confined: bool = False
    memory_limited: bool = False
    describes: str = ""

    @property
    def sufficient_for_untrusted_code(self) -> bool:
        """Untrusted code needs the operating system on the kernel's side."""
        return self.os_isolation and self.raw_sockets_confined and self.filesystem_confined

    def as_dict(self) -> dict[str, Any]:
        return {"sandbox": self.sandbox, "os_isolation": self.os_isolation,
                "process_isolation": self.process_isolation,
                "clean_environment": self.clean_environment,
                "egress_proxy": self.egress_proxy,
                "raw_sockets_confined": self.raw_sockets_confined,
                "filesystem_confined": self.filesystem_confined,
                "memory_limited": self.memory_limited,
                "sufficient_for_untrusted_code": self.sufficient_for_untrusted_code,
                "describes": self.describes}


# ---------------------------------------------------------------- the runner

@dataclass(frozen=True, slots=True)
class IsolatedResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    egress_decisions: tuple[HostDecision, ...]
    sandbox: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class IsolatedRunner:
    """Run a command in a subprocess with a clean environment behind the egress proxy."""

    def __init__(self, *, sandbox: SandboxBackend | None = None,
                 audit: Callable[..., Any] | None = None) -> None:
        self.sandbox = sandbox or NoSandbox()
        self._audit = audit
        self.runs = 0

    def report(self) -> IsolationReport:
        """The isolation this runner provides. Derived from the backend, never assumed."""
        sandbox = self.sandbox
        os_isolation = bool(getattr(sandbox, "os_isolation", True))
        return IsolationReport(
            sandbox=type(sandbox).__name__, os_isolation=os_isolation,
            raw_sockets_confined=bool(getattr(sandbox, "confines_sockets", os_isolation)),
            filesystem_confined=bool(getattr(sandbox, "confines_filesystem", os_isolation)),
            memory_limited=sys.platform != "win32",
            describes=sandbox.describe() if hasattr(sandbox, "describe") else "")

    def run(self, argv: Sequence[str], *, workdir: Path, allowed_hosts: Iterable[str] = (),
            grants: Mapping[str, str] | None = None, timeout_s: float = 120.0,
            memory_mb: int | None = 2048, run_id: str = "", stdin: str | None = None,
            allow_private_hosts: bool = False) -> IsolatedResult:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        self.runs += 1
        hosts = list(allowed_hosts)
        try:
            proxy_cm: Any = EgressProxy(hosts, allow_private=allow_private_hosts,
                                        audit=self._audit, run_id=run_id)
        except ProxyUnavailable as exc:
            if hosts:
                # The run needs network and the kernel cannot govern it. Refusing is the only
                # honest answer: running unproxied would be the v0.3 bypass with extra steps.
                if self._audit is not None:
                    self._audit("isolated_run_refused", reason="egress proxy unavailable",
                                hosts=len(hosts), run_id=run_id)
                raise
            proxy_cm = _DeadProxy()  # no hosts wanted: point clients at a closed port
        with proxy_cm as proxy:
            env = build_child_environment(proxy_address=proxy.address, grants=grants,
                                          workdir=workdir)
            # ``bool(list(allowed_hosts))`` re-consumed the caller's iterable. The built-in
            # executor passes a list so the main path survived it, but any generator would
            # already have been drained by ``hosts = list(allowed_hosts)`` above, and the
            # sandbox would then be told the run wants no network while the proxy was
            # configured to allow some. ``hosts`` is the materialised copy; use it.
            wrapped = self.sandbox.wrap(argv, workdir=workdir, allow_network=bool(hosts))
            started = time.time()
            code, out, err, timed_out = _run_contained(
                wrapped, cwd=workdir, env=env, stdin=stdin, timeout_s=timeout_s,
                memory_mb=memory_mb)
            duration = time.time() - started
            result = IsolatedResult(
                argv=tuple(argv), exit_code=code, stdout=out[:200_000], stderr=err[:50_000],
                duration_s=duration, timed_out=timed_out,
                egress_decisions=tuple(proxy.decisions), sandbox=type(self.sandbox).__name__)
        if self._audit is not None:
            self._audit("isolated_run", argv0=argv[0] if argv else "", exit_code=code,
                        timed_out=timed_out, duration_s=round(duration, 3),
                        egress_allowed=sum(d.allowed for d in result.egress_decisions),
                        egress_refused=sum(not d.allowed for d in result.egress_decisions),
                        sandbox=result.sandbox, run_id=run_id)
        return result


def _run_contained(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str],
                   stdin: str | None, timeout_s: float,
                   memory_mb: int | None) -> tuple[int, str, str, bool]:
    """Run a command and, on timeout, kill **the whole process group**.

    ``subprocess.run(..., timeout=...)`` kills the process it started and nothing else. The
    child had already been given its own session by ``start_new_session=True``, so anything
    it spawned was reparented and kept running: a component that forks a worker and sleeps
    was reported as "exceeded its timeout and was killed" while its grandchild went on
    executing — and went on writing files — well after the run was recorded as over. A
    process group was created and then never used for the one job it exists to do.

    So the group is signalled, not the leader: SIGKILL to ``-pgid`` reaches every
    descendant that has not left the group. On Windows there is no process group to signal,
    so the fallback is ``Popen.kill()`` with the same reporting; a job object is the real
    answer there and is noted as a gap rather than pretended away.
    """
    # ``stdin=DEVNULL`` where the caller supplies none. ``subprocess.run(input=None)``
    # left the child inheriting the kernel's stdin, which an isolated component has no
    # business reading from and which makes a component that blocks on input hang the run
    # until its timeout rather than failing immediately.
    proc = subprocess.Popen(
        list(argv), cwd=str(cwd), env=dict(env), text=True,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=_preexec(memory_mb) if sys.platform != "win32" else None,
        start_new_session=True)
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout_s)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        # Drain whatever the child produced before it was killed. The pipes are closed by
        # the kill, so this returns rather than blocking for the grandchildren that may
        # still hold the write end — which is itself why the group kill has to come first.
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - a pipe held open anyway
            proc.kill()
            out, err = "", ""
        return 124, out or "", err or "", True


def _kill_process_group(proc: "subprocess.Popen[str]") -> None:
    """SIGKILL the child's process group, falling back to the child alone.

    The guard is not defensive padding. ``killpg`` on our OWN group would SIGKILL the
    kernel, every other isolated run in flight, and the process holding the audit chain —
    a timeout on one tool taking down the harness. That is only reachable if
    ``start_new_session=True`` did not take effect, which should not happen; "should not
    happen" is exactly the condition worth checking before sending SIGKILL to a group.
    """
    if sys.platform == "win32":  # pragma: no cover - platform specific
        proc.kill()
        return
    try:
        child_group = os.getpgid(proc.pid)
        if child_group == os.getpgid(0):
            raise PermissionError(
                "the child shares this process group; refusing to signal it")
        os.killpg(child_group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone, still in our group, or not ours to signal. Kill what we can name.
        try:
            proc.kill()
        except ProcessLookupError:  # pragma: no cover - raced with exit
            pass


def _preexec(memory_mb: int | None) -> Callable[[], None]:
    def fn() -> None:
        try:
            import resource

            # No core dumps (Codex process-hardening).
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if memory_mb:
                limit = memory_mb * 1024 * 1024
                for name in ("RLIMIT_AS", "RLIMIT_DATA"):
                    try:
                        resource.setrlimit(getattr(resource, name), (limit, limit))
                        break
                    except (ValueError, OSError, AttributeError):
                        continue
        except Exception:  # noqa: BLE001 - hardening is best-effort per platform
            pass
    return fn


# --------------------------------------------------- executing a component isolated

def _unwrap_child_result(parsed: Any) -> Any:
    """A child's one JSON value, or the shortfall it wrapped that value in.

    The protocol has one extension: a child that ran with a documented shortfall writes
    ``{"$psh": {"status": "degraded", "reason": "...", "value": ...}}`` and the kernel
    records a degraded result with that caveat. Anything else is the value itself.
    """
    if isinstance(parsed, dict) and set(parsed) == {"$psh"} and isinstance(parsed["$psh"], dict):
        from ..contracts import DegradedResult

        envelope = parsed["$psh"]
        if str(envelope.get("status", "")).lower() == "degraded":
            return DegradedResult(value=envelope.get("value"),
                                  reason=str(envelope.get("reason") or "")[:300])
    return parsed


class IsolationUnavailable(RuntimeError):
    """A component had to run isolated and could not.

    Raised rather than falling back to in-process execution. The fallback is what made the
    isolation claim untrue in v0.4: the mechanism existed, the main path did not use it, and
    nothing in the record said which had run.
    """


class IsolatedExecutor:
    """Executes a ``backend="subprocess"`` component through :class:`IsolatedRunner`.

    The protocol is deliberately tiny, because a component boundary that needs a framework
    is a component boundary people will route around:

    * the child receives one JSON object on **stdin** — ``{"tool", "run_id", "payload"}``;
    * it writes one JSON value on **stdout**, which becomes the tool result;
    * a non-zero exit is a ``ContractViolation`` carrying stderr, never a silent empty
      result.

    Network reach is the intersection of two declarations: the hosts the manifest asks for
    and whether the envelope permits a remote destination at all. A manifest can therefore
    narrow the run and never widen it, which is the same containment rule the authority
    lattice applies to envelopes.
    """

    def __init__(self, runner: "IsolatedRunner", *, workdir_root: Path,
                 secret_resolver: Callable[[str], str | None] | None = None) -> None:
        self.runner = runner
        self.workdir_root = Path(workdir_root)
        self.secret_resolver = secret_resolver
        self.executions = 0
        self._count_lock = threading.Lock()

    def allowed_hosts(self, manifest: Any, envelope: Any) -> list[str]:
        from ..labels import Destination

        remote = (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)
        if not any(envelope.permits_destination(d) for d in remote):
            return []
        if not any(d in remote for d in getattr(manifest, "destinations", ())):
            return []
        return [h for h in getattr(manifest, "allowed_hosts", ()) if h]

    def grants(self, manifest: Any) -> dict[str, str]:
        """Resolve only the secrets the manifest declared, and only if a resolver exists."""
        out: dict[str, str] = {}
        if self.secret_resolver is None:
            return out
        for name in getattr(manifest, "requires_secrets", ()) or ():
            value = self.secret_resolver(name)
            if value is not None:
                out[str(name)] = str(value)
        return out

    def _workdir_for(self, manifest: Any, envelope: Any) -> Path:
        """The component's working directory, proven to be inside the sandbox root.

        This was ``root / run_id / manifest.id`` with no check on either component, and
        ``manifest.id`` was validated only for being non-empty. ``id="../../escaped"``
        therefore put the child's ``cwd`` outside the sandbox root, and an absolute id
        discarded the root entirely, because ``Path("/a/b") / "/tmp/x"`` is ``/tmp/x`` —
        ``pathlib`` treats an absolute right-hand side as a replacement, not a suffix.

        Two layers, because either alone has a gap. ``ComponentManifest`` now constrains
        the id to a single safe path component, which stops the traversal at the point the
        manifest is built. And the assembled path is resolved and required to land inside
        the resolved root, which also covers a symlinked sandbox directory and a ``run_id``
        from some future caller that does not go through the same validation.
        """
        from ..contracts import _VALID_COMPONENT_ID

        component_id = str(getattr(manifest, "id", ""))
        run_id = str(getattr(envelope, "run_id", "") or "no_run")
        for part, what in ((component_id, "component id"), (run_id, "run id")):
            if not _VALID_COMPONENT_ID.fullmatch(part) or part in (".", ".."):
                raise IsolationUnavailable(
                    f"{what} {part!r} is not usable as a sandbox path component; refusing "
                    "to build a working directory from it")

        root = self.workdir_root.resolve(strict=False)
        candidate = (root / run_id / component_id).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise IsolationUnavailable(
                f"the working directory for component {component_id!r} resolves to "
                f"{candidate}, which is outside the sandbox root {root}") from exc
        return candidate

    def invoke(self, manifest: Any, payload: Any, envelope: Any) -> Any:
        import shlex

        from ..contracts import ContractViolation, ToolTimeout

        argv = shlex.split(manifest.entrypoint)
        if not argv:
            raise IsolationUnavailable(
                f"component {manifest.id!r} declares isolated execution but its entrypoint "
                "is empty")
        workdir = self._workdir_for(manifest, envelope)
        stdin = json.dumps({"tool": manifest.id, "run_id": getattr(envelope, "run_id", ""),
                            "payload": payload}, default=str)
        result = self.runner.run(
            argv, workdir=workdir, allowed_hosts=self.allowed_hosts(manifest, envelope),
            grants=self.grants(manifest), timeout_s=float(getattr(manifest, "timeout_s", 120.0)),
            memory_mb=int(getattr(manifest, "memory_mb", 2048) or 0) or None,
            run_id=getattr(envelope, "run_id", ""), stdin=stdin)
        with self._count_lock:
            self.executions += 1
        if result.timed_out:
            raise ToolTimeout(
                f"isolated component {manifest.id!r} exceeded its {manifest.timeout_s}s "
                "timeout and was killed")
        if result.exit_code == 124:
            # The child's own timeout (the convention ``timeout(1)`` set), reported as
            # what it is rather than as a generic non-zero exit.
            raise ToolTimeout(
                f"isolated component {manifest.id!r} reported a timeout: "
                f"{result.stderr.strip()[:300]}")
        if result.exit_code != 0:
            raise ContractViolation(
                f"isolated component {manifest.id!r} exited {result.exit_code}: "
                f"{result.stderr.strip()[:300]}")
        text = result.stdout.strip()
        if not text:
            return {}
        try:
            return _unwrap_child_result(json.loads(text))
        except json.JSONDecodeError as exc:
            # The protocol says "one JSON value on stdout". Wrapping unparseable output as
            # ``{"stdout": ...}`` made that sentence advisory: a component whose contract
            # promised a structured result could return a traceback, a progress log or a
            # half-written line, and the caller would receive a dict that satisfied no
            # schema and looked like a successful call. A contract that is silently
            # optional is not a contract, so this fails.
            raise ContractViolation(
                f"isolated component {manifest.id!r} must write one JSON value on stdout "
                f"and wrote {len(text)} characters that do not parse as JSON "
                f"({exc.msg} at position {exc.pos}); stderr: "
                f"{result.stderr.strip()[:200]!r}") from exc
