# v0.2.3.1 — container availability, measured rather than assumed

A review of BioScience-Harness reported `133 passed, 3 failed, 6 skipped` and noted that
while some failures were environmental (no Parquet engine, no container runtime), they
"also exposed a container validation ordering problem". They did. There were two defects,
and the second is the one the reviewer named.

With `pyarrow` installed the Parquet failures disappear, so the suite here reads
`135 passed, 7 skipped` — and the seventh skip is where the bug was hiding.

## 1. "Installed" was treated as "usable"

```python
def available(self) -> bool:
    return self.runtime_bin is not None          # shutil.which("docker")
```

`shutil.which` finds the **client binary**. It says nothing about whether a daemon is
reachable. On a machine with the docker CLI installed and no daemon running — a CI image, a
fresh workstation, the environment this was fixed in — the backend declared itself
available, `docker run` failed with *"failed to connect to the docker API"*, the return code
was non-zero, and the result was recorded as `FAILED`.

`FAILED` and `UNAVAILABLE` are not interchangeable in this package. `status.py` says so:

```
FAILED       # ran and errored
UNAVAILABLE  # cannot run here (missing dep/runtime/install)
```

and `ExecutionStatus.executed` is **True** for the first, **False** for the second. So the
misclassification asserted that upstream work had run when nothing ran at all — the same
shape of dishonesty the rest of this package is built to avoid.

Measured, on a host with the CLI and no daemon:

| | before | after |
| --- | --- | --- |
| healthy component, no daemon | `FAILED`, `executed=True` | `UNAVAILABLE`, `executed=False` |
| `EvolutionAgent.analyze_failures` | `{'FAILED': 1}` | `{'UNAVAILABLE': 1}` |

The second row is the cost. `analyze_failures` feeds the self-evolution pipeline, so a
component that is entirely healthy was tallied as failing and could be proposed for a
rewrite because the *host* had no container daemon.

**Fix.** `probe_container_runtime()` measures both halves: the CLI must exist *and* the
runtime must answer (`<bin> info`). It is memoised — the resolver asks once per component
and probing spawns a process — with a 60s TTL, because an unbounded memo would recreate,
inside one process, exactly the defect `default_backend_probe`'s own comment describes:

> the previous resolver hardcoded "no runtime", so installing Docker changed nothing and
> container components stayed UNAVAILABLE forever

A long-lived agent that starts before its daemon would be in that position for its whole
life. A daemon can also stop, so the positive answer expires on the same clock.

There was a second copy of the question, too. `ContainerBackend.available()` and
`default_backend_probe()` each answered "can this machine run containers?" with their own
`shutil.which`. Both now call the one probe: a question answered twice is a question that
will eventually be answered two different ways.

**Defence in depth.** A daemon that dies mid-session still produces a non-zero exit at
`invoke()` time. `_runtime_did_not_start()` recognises the CLI's own "cannot connect"
messages and reports `UNAVAILABLE`, refreshing the probe so a stopped daemon is not
asserted as present for the rest of the process. A container that genuinely ran and errored
is still `FAILED` — there is a test for each direction, because laundering real component
errors into `UNAVAILABLE` would be the same defect pointing the other way.

## 2. The ordering: a machine-independent defect diagnosed as a machine problem

`invoke()` checked host availability **before** the component's own declaration. A manifest
with no `runtime.entrypoint` is broken on every machine; host capability is true on some and
false on others. So the same manifest was diagnosed differently depending on where it ran:

```
before, host WITHOUT a container CLI:  "no container runtime found (looked for docker, …)"
before, host WITH a container CLI:     "component declares no runtime.entrypoint, …"
```

A developer on the first host was sent to install Docker in order to discover that their
manifest was wrong.

**Fix.** What the *component* declares is checked first — entrypoint, then image, both
machine-independent — and host capability second. The diagnosis no longer depends on where
it was run:

```
after,  host WITHOUT a container CLI:  "component declares no runtime.entrypoint, …"
after,  host WITH a container CLI:     "component declares no runtime.entrypoint, …"
```

## 3. A test that disappeared exactly where the defect appeared

```python
def test_container_backend_reports_unavailable_honestly() -> None:
    cb = ContainerBackend()
    if cb.available():
        pytest.skip("a container runtime is present on this machine")
```

On any machine with a container CLI installed the assertions never ran — and under defect 1
that was *every* machine with the CLI, daemon or not, which is precisely the machine the bug
lived on. A test that skips itself where the defect appears is not covering it.

It injects the probe now, so both branches are exercised everywhere.

## Result

```
135 passed,  7 skipped   (before — one skip concealing the defect)
140 passed,  6 skipped   (after)
```

The five new tests all fail against the unmodified tree. Still open and unchanged:
`analyze_failures` counts `UNAVAILABLE` alongside `FAILED`, which is its documented
behaviour — an unavailable capability is legitimately interesting to an evolution agent
(it might propose a pure-Python fallback). What was wrong was the status, not the policy,
and the `statuses` breakdown now lets a caller tell the two apart.
