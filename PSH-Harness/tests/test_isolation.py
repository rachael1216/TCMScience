"""Process isolation behind the egress proxy.

Two layers of test, because the environment this was built in forbids ALL listening sockets:

* **Handler tests** drive the proxy's request handler over an in-memory socketpair, with the
  "upstream" also a socketpair served by a thread. They exercise the allow, refuse, SSRF and
  forwarding logic with no listener anywhere.
* **Subprocess tests** exercise the child-environment guarantees (empty env, grants, rlimits,
  timeout) and the FAIL-CLOSED behaviour when the proxy cannot bind.
* **End-to-end tests** (child -> proxy -> upstream over real TCP) are marked ``needs_listener``
  and skip with a stated reason where a loopback listener cannot be bound. They run on an
  ordinary workstation. The report states which of these ran.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from psh.kernel.isolation import (
    EgressProxy, IsolatedRunner, NoSandbox, ProxyUnavailable, _HostPolicy, _ProxyHandler,
    build_child_environment,
)

PHI = "Patient Alice Cheng, MRN 04851923"


def _can_listen() -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0)); s.listen(1)
        return True
    except OSError:
        return False
    finally:
        s.close()


needs_listener = pytest.mark.skipif(
    not _can_listen(), reason="this sandbox forbids loopback listeners; runs on a workstation")


# --------------------------------------------------------------- handler layer

def stub_resolver(mapping=None, default="93.184.216.34"):
    """A resolver a unit test controls, so no test in this file touches real DNS.

    The v0.4 policy called ``getaddrinfo`` from ``decide()``, which made "pure" handler
    tests depend on name resolution — slow with a resolver, hanging without one, and
    unrunnable in an air-gapped CI.
    """
    table = dict(mapping or {})

    def resolve(host):
        if host in table:
            return list(table[host])
        return [default]

    return resolve


class _FakeServer:
    def __init__(self, hosts, allow_private=False, resolver=None):
        self.policy = _HostPolicy(hosts, allow_private=allow_private,
                                  resolver=resolver or stub_resolver())


def _drive(server, raw_request: bytes, upstream_reply: bytes | None = None) -> bytes:
    """Run the proxy handler on one end of a socketpair; return everything it wrote back.

    If ``upstream_reply`` is given, socket.create_connection is patched to hand the handler
    the far end of a second socketpair that a thread answers with that reply.
    """
    client, handler_side = socket.socketpair()
    client.sendall(raw_request)
    # Handler-side reads must not block forever: a handler that waits for a keep-alive
    # follow-up is a bug this driver must surface, not hide.
    handler_side.settimeout(5)
    upstream_thread = None
    patch = None
    if upstream_reply is not None:
        up_handler, up_far = socket.socketpair()

        def serve():
            up_far.settimeout(3)
            try:
                up_far.recv(65536)          # the forwarded request
                up_far.sendall(upstream_reply)
            finally:
                up_far.close()

        upstream_thread = threading.Thread(target=serve, daemon=True)
        upstream_thread.start()
        patch = mock.patch("psh.kernel.isolation.socket.create_connection",
                           return_value=up_handler)
        patch.start()
    try:
        _ProxyHandler(handler_side, ("127.0.0.1", 12345), server)
    except (socket.timeout, TimeoutError) as exc:
        raise AssertionError("proxy handler blocked waiting for more input") from exc
    except OSError:
        pass  # peer closed; normal
    finally:
        if patch:
            patch.stop()
        try:
            handler_side.close()
        except OSError:
            pass
    client.settimeout(3)
    out = b""
    try:
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            out += chunk
    except OSError:
        pass
    client.close()
    return out


def test_handler_refuses_non_allowlisted_host_with_403_and_reason():
    out = _drive(_FakeServer(["api.allowed.org"]),
                 b"GET http://evil.example.com/x HTTP/1.1\r\nHost: evil.example.com\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403")
    assert b"not in the run" in out


def test_handler_refuses_connect_to_non_allowlisted_host():
    out = _drive(_FakeServer(["api.allowed.org"]),
                 b"CONNECT evil.example.com:443 HTTP/1.1\r\nHost: evil.example.com:443\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403")


def test_handler_forwards_allowlisted_plain_http_request():
    reply = b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nreached"
    out = _drive(_FakeServer(["api.allowed.org"]),
                 b"GET http://api.allowed.org/v1 HTTP/1.1\r\nHost: api.allowed.org\r\n\r\n",
                 upstream_reply=reply)
    assert out.startswith(b"HTTP/1.1 200") and out.endswith(b"reached")


def test_handler_strips_proxy_authorization_before_forwarding():
    """Whatever the child sent to authenticate to the PROXY must not reach the upstream."""
    captured = {}
    up_handler, up_far = socket.socketpair()

    def serve():
        up_far.settimeout(3)
        captured["req"] = up_far.recv(65536)
        up_far.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        up_far.close()

    threading.Thread(target=serve, daemon=True).start()
    with mock.patch("psh.kernel.isolation.socket.create_connection", return_value=up_handler):
        client, handler_side = socket.socketpair()
        client.sendall(b"GET http://api.allowed.org/ HTTP/1.1\r\nHost: api.allowed.org\r\n"
                       b"Proxy-Authorization: Basic abc\r\nX-Keep: yes\r\n\r\n")
        try:
            _ProxyHandler(handler_side, ("127.0.0.1", 1), _FakeServer(["api.allowed.org"]))
        except Exception:
            pass
        client.close(); handler_side.close()
    assert b"Proxy-Authorization" not in captured["req"]
    assert b"X-Keep: yes" in captured["req"]


def test_every_handler_decision_is_recorded():
    srv = _FakeServer(["api.allowed.org"])
    _drive(srv, b"GET http://evil.example.com/ HTTP/1.1\r\nHost: evil.example.com\r\n\r\n")
    assert len(srv.policy.decisions) == 1 and not srv.policy.decisions[0].allowed


# ---------------------------------------------------------------- host policy

def test_wildcard_host_patterns():
    pol = _HostPolicy(["*.ncbi.nlm.nih.gov", "clinicaltrials.gov"], resolver=stub_resolver())
    assert pol.decide("eutils.ncbi.nlm.nih.gov", 443).allowed
    assert pol.decide("clinicaltrials.gov", 443).allowed
    assert not pol.decide("evil.example.com", 443).allowed
    assert not pol.decide("ncbi.nlm.nih.gov.evil.example.com", 443).allowed


def test_private_and_metadata_addresses_refused_even_when_allowlisted():
    """Codex: private-range destinations refused even if allowlisted. 169.254.169.254 is the
    cloud-metadata SSRF classic."""
    pol = _HostPolicy(["169.254.169.254", "127.0.0.1", "localhost", "10.0.0.5"],
                      resolver=stub_resolver({"localhost": ["127.0.0.1"]}))
    for host in ("169.254.169.254", "127.0.0.1", "localhost", "10.0.0.5"):
        d = pol.decide(host, 80)
        assert not d.allowed and "private" in d.reason, host


def test_no_allowed_hosts_means_no_network():
    d = _HostPolicy([], resolver=stub_resolver()).decide("pubmed.ncbi.nlm.nih.gov", 443)
    assert not d.allowed and "no network destinations" in d.reason


# ----------------------------------------------------------- child environment

def test_build_child_environment_is_default_deny():
    os.environ["SECRET_TOKEN"] = "x"; os.environ["LD_PRELOAD"] = "/evil.so"
    os.environ["DYLD_INSERT_LIBRARIES"] = "/evil.dylib"; os.environ["NO_PROXY"] = "*"
    try:
        env = build_child_environment(proxy_address="http://127.0.0.1:1", grants={"OK": "1"})
    finally:
        for k in ("SECRET_TOKEN", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "NO_PROXY"):
            del os.environ[k]
    assert "SECRET_TOKEN" not in env
    assert not any(k.startswith(("LD_", "DYLD_")) for k in env)
    assert env["HTTP_PROXY"] == "http://127.0.0.1:1" and env["NO_PROXY"] == ""
    assert env["OK"] == "1"


def test_child_process_starts_from_an_empty_environment(tmp_path):
    """A real subprocess: the kernel's secrets and PHI in env never reach it."""
    os.environ["SECRET_TOKEN"] = "kernel-secret"; os.environ["PATIENT_NOTE"] = PHI
    try:
        res = IsolatedRunner().run(
            [sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
            workdir=tmp_path / "w", timeout_s=30)
    finally:
        del os.environ["SECRET_TOKEN"], os.environ["PATIENT_NOTE"]
    assert res.ok, res.stderr
    keys = json.loads(res.stdout)
    assert "SECRET_TOKEN" not in keys and "PATIENT_NOTE" not in keys and "PYTHONPATH" not in keys
    assert "04851923" not in res.stdout
    assert "HTTP_PROXY" in keys, "the child is pointed at a proxy even when no network is wanted"


def test_grants_are_the_only_way_in(tmp_path):
    res = IsolatedRunner().run(
        [sys.executable, "-c", "import os; print(os.environ.get('NCBI_API_KEY',''))"],
        workdir=tmp_path / "w", grants={"NCBI_API_KEY": "granted-key"}, timeout_s=30)
    assert res.stdout.strip() == "granted-key"


def test_timeout_is_enforced(tmp_path):
    res = IsolatedRunner().run([sys.executable, "-c", "import time; time.sleep(30)"],
                               workdir=tmp_path / "w", timeout_s=1.5)
    assert res.timed_out and res.exit_code == 124 and res.duration_s < 10


def test_core_dumps_disabled_in_child(tmp_path):
    res = IsolatedRunner().run(
        [sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_CORE))"],
        workdir=tmp_path / "w", timeout_s=30)
    assert res.stdout.strip() == "(0, 0)", res.stdout


def test_runner_fails_closed_when_network_wanted_but_proxy_unavailable(tmp_path):
    """If the kernel cannot govern egress it refuses to run, rather than running unproxied."""
    with mock.patch("psh.kernel.isolation.EgressProxy.__init__",
                    side_effect=ProxyUnavailable("bind refused")):
        with pytest.raises(ProxyUnavailable):
            IsolatedRunner().run([sys.executable, "-c", "print(1)"], workdir=tmp_path / "w",
                                 allowed_hosts=["api.example.org"], timeout_s=30)


def test_runner_without_proxy_and_without_hosts_points_child_at_dead_port(tmp_path):
    with mock.patch("psh.kernel.isolation.EgressProxy.__init__",
                    side_effect=ProxyUnavailable("bind refused")):
        res = IsolatedRunner().run(
            [sys.executable, "-c", "import os; print(os.environ['HTTP_PROXY'])"],
            workdir=tmp_path / "w", timeout_s=30)
    assert res.stdout.strip() == "http://127.0.0.1:9"


def test_no_sandbox_says_so():
    assert "NOT confined" in NoSandbox().describe()


def test_isolated_run_is_an_event(tmp_path):
    from psh.config import PSHConfig
    from psh.kernel import TrustedKernel

    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs())
    IsolatedRunner(audit=k.events.sink()).run([sys.executable, "-c", "print(1)"],
                                              workdir=tmp_path / "w", timeout_s=30, run_id="r1")
    assert "isolated_run" in [e.event_type for e in k.events.records()]


# ------------------------------------------------------------ end to end (TCP)

CHILD = r"""
import json, sys, urllib.request, urllib.error
try:
    with urllib.request.urlopen(sys.argv[1], timeout=10) as r:
        print(json.dumps({"status": r.status, "body": r.read().decode()[:60]}))
except urllib.error.HTTPError as e:
    print(json.dumps({"status": e.code, "body": e.read().decode()[:160]}))
"""


@pytest.fixture
def upstream():
    import http.server, socketserver

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = b'{"upstream": "reached"}'
            self.send_response(200); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@needs_listener
def test_e2e_allowlisted_host_reached_through_proxy(tmp_path, upstream):
    res = IsolatedRunner().run([sys.executable, "-c", CHILD, f"http://127.0.0.1:{upstream}/"],
                               workdir=tmp_path / "w",
                               allowed_hosts=[f"127.0.0.1:{upstream}"],
                               allow_private_hosts=True, timeout_s=30)
    out = json.loads(res.stdout)
    assert out["status"] == 200 and "reached" in out["body"]
    assert any(d.allowed for d in res.egress_decisions)


@needs_listener
def test_e2e_non_allowlisted_host_refused_at_proxy(tmp_path, upstream):
    res = IsolatedRunner().run([sys.executable, "-c", CHILD, f"http://127.0.0.1:{upstream}/"],
                               workdir=tmp_path / "w", allowed_hosts=["api.allowed.org"],
                               allow_private_hosts=True, timeout_s=30)
    out = json.loads(res.stdout)
    assert out["status"] == 403 and "not in the run" in out["body"]
    assert any(not d.allowed for d in res.egress_decisions)
