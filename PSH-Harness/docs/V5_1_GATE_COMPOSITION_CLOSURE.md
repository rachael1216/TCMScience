# v0.5.1 — gate composition closure

This release answers a second external review of v0.5. That review accepted the v0.5
premise (controls exist and the executing path now uses them) and attacked the next layer:
**what happens where two controls meet.** Every finding it made is real, every one is
reproduced below, and every one is now closed with a test that fails on v0.5.

The findings share a shape, and it is worth naming because it is not the v0.5 shape:

> v0.5: *the mechanism exists and the main path does not call it.*
>
> v0.5.1: **the mechanism exists, is called — and a second, shorter copy of it was
> hand-written somewhere else.**

`AuthorityLattice` defines authority containment over fourteen dimensions, and
`DelegationGateway` re-implemented four of them. `PolicySnapshot.envelope()` enforces a
ceiling, and `Runner.run()` minted envelopes from a policy nobody compared to the kernel's.
`labels.walk_values` treats a truncated walk as a reason to escalate, and the two egress
walkers treated it as a reason to pass. In each case the *unified* abstraction was correct
and the bypass was the duplicate beside it.

So the fixes are mostly deletions. `DelegationGateway` lost its comparisons and calls
`AuthorityLattice.violations`. `PolicySnapshot.with_()` lost its three `if`s and calls a new
`PolicyLattice`. The rule is the one the authority module already stated:

> a check repeated at each call site will drift; a check called from each call site cannot.

## P0

### 1. `Runner.run(policy=…)` bypassed the kernel's policy ceiling

`src/psh/runtime/runner.py`

**Reproduction.** A kernel built with a local-only, `SUGGEST`, `R1` policy. A caller passes
a broader `PolicySnapshot` to `Runner`:

```
kernel allows PUBLIC_REMOTE: False
selected run policy:         caller-broad
remote invokes:              1
refused_at:                  (none)
released:                    hello world
```

The kernel forbade public providers; the run reached one and released the result. `policy =
policy or self.policy` accepted whatever was passed and `policy.envelope(...)` minted from
it, so the envelope every gateway checked had been produced by a policy the kernel never
agreed to. A gateway checks the envelope it is given, and this envelope was honest about an
authority nobody had granted.

The same call produced a **split brain**, which is the more interesting half:

| consulted by | policy actually used |
| --- | --- |
| the run envelope | `Runner.run(policy=…)` |
| `OutputGate.require_support` | `kernel.policy`, at construction |
| `broker.require_isolation` | `kernel.policy`, at construction |
| `PersistenceGateway.max_label` | `kernel.policy`, at construction |

So `result.policy_snapshot` named a policy that had governed one of the four.

**Fix.** Two parts, because either alone leaves half the defect.

*Direction.* `Runner._effective_policy` refuses a per-run policy that is not contained by
`kernel.policy`, naming every dimension it exceeds, and `Runner(clamp_policy=True)` opts
into the meet instead. Both the constructor and `run()` go through it.

*Enforcement.* Containment makes the run's policy narrower — and narrower has to be
**enforced**, not merely recorded, or "declared policy is effective policy" is false again
in the safe direction. `require_isolated_tools` now travels on `RunEnvelope`, so the broker
reads it from the run rather than from its own construction argument, and
`AuthorityLattice` covers it so a delegate cannot drop it. `OutputGate.check` and
`PersistenceGateway.commit_node` take per-call ceilings that can only tighten (`or` and
`min`, never assignment).

### 2. Multi-destination components were gated on `destinations[0]`

`src/psh/kernel/egress.py`

**Reproduction.** A component declaring `destinations=(LOCAL_COMPUTE, PUBLIC_REMOTE)`,
handed a PHI payload:

```
PHI permits PUBLIC_REMOTE?   False
ToolGateway allowed:         True
checked destination:         LOCAL_COMPUTE
```

The dangerous part is not the single wrong answer, it is that **the two halves of the
kernel read the same tuple differently**:

```
ToolGateway          -> destinations[0] -> LOCAL_COMPUTE -> PHI passes
IsolatedExecutor     -> any(d in remote) -> PUBLIC_REMOTE -> network opened
```

Either reading is defensible. Holding both at once means the gate permits PHI on the
strength of a destination the executor has already decided is not the only one.

**Fix.** Every declared destination is checked; the decision names the **most exposed** one
rather than the first. Exposure order is stated explicitly (`_EXPOSURE_ORDER`) because the
enum's integer order is a declaration order — `PERSISTENT` is 5 and `PUBLIC_REMOTE` is 3,
and writing to the user's own disk is not more exposing than a public provider.
`manifest_destinations()` is the single reading of the tuple, used by the gate, the approval
request and the audit record, so they cannot diverge again. Approval targets list all
destinations: an approval keyed on `LOCAL_COMPUTE` for a component that also reaches
`PUBLIC_REMOTE` describes a different action from the one being approved.

## P1

### 3. `DelegationGateway` re-implemented the authority lattice, badly

`src/psh/kernel/egress.py`

**Reproduction.** Parent `R1`/`SUGGEST`/`capabilities=["safe"]`/`denied=["blocked"]`/
`usd_hard=1`/`seconds_hard=10`/1 model call. Child identical except `R4_KERNEL`, `ACT`,
unrestricted capabilities, no denials, `usd_hard=999`, `seconds_hard=999`, 999 calls:

```
DelegationGateway allowed = True
AuthorityLattice.violations = [capabilities, denied_capabilities, risk, autonomy,
                               budget.usd_hard, budget.seconds_hard,
                               budget.max_model_calls, budget.max_tool_calls,
                               budget.max_delegations]
```

**Fix.** The hand-written comparisons are gone. The gateway calls
`AuthorityLattice.violations(child, parent)` and refuses with the list. What remains is the
one check that is *not* an authority question — whether the projection's label fits the
child's ceiling, which is about data rather than about the relationship between two
envelopes.

**Why the property test missed it.** `test_delegation_contract_cannot_exceed_its_parent_run`
built a child asking for the maximum on every dimension, including `tokens_hard = 10**9`,
while the parent strategy generated `tokens_hard <= 100_000`. Every example was therefore
refused by the first budget comparison and no other dimension was ever the *reason* for a
verdict. A conjunctive property on a lattice cannot distinguish "all dimensions enforced"
from "one dimension enforced": whichever fails earliest masks the rest.

The property is now **one dimension at a time** — take a child identical to its parent,
widen exactly one field, assert the refusal names that field. And because the new test can
itself go vacuous, `test_every_lattice_dimension_is_actually_exercised` asserts that each
dimension produced at least one non-filtered example. That test caught a real hole on its
first run: `envelopes()` defaulted `require_isolated_tools` to `False`, so that dimension
had exactly zero coverage.

### 4. `PolicySnapshot.with_()` widened on nine of twelve dimensions

`src/psh/policy.py`

The docstring said "Return a narrowed copy. Widening is refused." The body compared
`max_data_label`, `allowed_destinations` and `risk_ceiling`. Accepted: `SUGGEST -> ACT`,
`require_isolated_tools True -> False`, `require_citation True -> False`, `tokens_hard
10 -> 999`, `usd_hard 1 -> 999`, `approval_required_at R1 -> R4`, `declassify_floor
SENSITIVE -> PUBLIC`, a widened `declassifiers` list, and an extended or removed deadline.

**Fix.** A new `PolicyLattice`, the policy-level counterpart of `AuthorityLattice`, with
every dimension in one table and the direction stated per group:

* *permissions* narrow by getting smaller — data ceiling, destinations, risk, budget,
  deadline, declassifiers;
* *requirements* narrow by being turned **on** — a child may set `require_citation` and
  never clear it;
* *thresholds* narrow toward "more is checked" — `approval_required_at` lower,
  `declassify_floor` higher.

`with_()` is now one line. `Runner` uses the same lattice for the P0-1 containment check, so
"narrower policy" means the same thing in both places.

### 5. `manifest.id` traversed out of the sandbox

`src/psh/contracts.py`, `src/psh/kernel/isolation.py`

`workdir = root / run_id / manifest.id`, with `id` validated only for being non-empty:

```
id = "../../escaped"
workdir resolves to  /tmp/.../escaped
sandbox root         /tmp/.../sandbox
inside root:         False
```

An absolute id was worse: `Path("/a/b") / "/tmp/x"` is `/tmp/x` — `pathlib` treats an
absolute right-hand side as a replacement, not a suffix — so the root was discarded
entirely.

**Fix.** Two layers, because either alone has a gap. `ComponentManifest` constrains the id
to one safe path component (`[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`, and not `.` or `..`), which
stops the traversal where the manifest is built. `IsolatedExecutor._workdir_for` resolves
the assembled path and requires it to land inside the resolved root, which also covers a
symlinked sandbox directory and a `run_id` from a future caller that does not go through
the manifest.

### 6. A timeout killed the process, not the process group

`src/psh/kernel/isolation.py`

`subprocess.run(..., timeout=…, start_new_session=True)` created a process group and never
signalled it. Reproduction — a tool that spawns a worker and sleeps, with a 0.5 s timeout:

```
runner timed_out = True
grandchild alive  = True
1.8s later: grandchild wrote its file = True
```

`"exceeded its timeout and was killed"` was true only of the process the runner could name.

**Fix.** `Popen` + `communicate(timeout=…)`, and on expiry `os.killpg(os.getpgid(pid),
SIGKILL)` — the group is signalled, not the leader, so every descendant that has not left
the group dies. Output is drained after the kill, because the pipes may be held open by
exactly the grandchildren the kill is for. Windows has no process group to signal; the
fallback is `Popen.kill()` and the gap is stated rather than pretended away.

### 7. Deep payloads were fail-open

`src/psh/kernel/egress.py`

```python
if _depth > 8:
    return []          # -> "no paths found" -> nothing to object to -> allowed
```

Nine levels of nesting around `{"target": "/definitely/outside"}` and the filesystem
boundary disappeared. `_flatten_text` had the same shape at depth 6, so a denied command
shape could be hidden the same way.

**Fix.** Both walkers return a `_Scan` carrying `complete`, and the gate refuses a payload
it could not fully inspect. This matches what `psh.labels.walk_values` already did —
yield `_TRUNCATED`, and let `deep_label_of` escalate rather than assume the remainder was
clean. The caps are raised (depth 16, 20 000 nodes) precisely *because* the consequence is
now refusal: refusing an ordinary deeply structured payload would be its own defect.

## P2

### 8. `requires_network` validation could never fire

```python
if self.requires_network and Destination.LOCAL_COMPUTE == self.destinations == ():
```

A chained comparison requiring an enum member to equal a tuple. `ComponentManifest(
requires_network=True, destinations=())` was accepted, and the gateway then defaulted it to
`LOCAL_COMPUTE` — so the audit record said "local" about a component that had declared it
needs the network. Now: a network-requiring component must name `PUBLIC_REMOTE` or
`TRUSTED_REMOTE`.

### 9. `StuckLoop` refused ordinary repeated requests

`signature = f"{envelope.task_id}:{hash(request) & 0xffffffff}"`, and `task_id` was `""` at
preflight — the envelope is minted at stage 2 and the task is created at stage 3. One bucket
counted every run a `Runner` had ever made, so three identical top-level requests tripped the
detector at the default threshold of 3. The dict was never cleared, and `hash()` of a `str`
is salted per process so signatures were not stable across runs.

**Fix.** The envelope is rebound to the real task id after `bind`. A loop is a repeat within
one scope, so the scope is explicit: the caller's `loop_scope`, else the parent run for a
delegated run, else this run's task — which is unique per top-level `run()` and therefore
cannot collide. The digest is SHA-256, and the table is bounded.

### 10. `min_autonomy` was declared and never read

`compatible_with()` never consulted it, so a component declaring `min_autonomy=ACT` was
reported compatible with an `OBSERVE` run. It is enforced now — and the default changed from
`ACT` to `OBSERVE`, because `ACT` is the *strongest* requirement and defaulting to it would
have made every manifest that never considered the field demand full autonomy. That default
is most of why the field could not be turned on.

### Also

* `IsolatedRunner.run` called `bool(list(allowed_hosts))` after already draining
  `allowed_hosts` into `hosts`. A list survived it; any generator would have told the
  sandbox the run wants no network while the proxy was configured to allow some.
* An isolated component whose stdout did not parse as JSON was wrapped as
  `{"stdout": …}`. The protocol says "writes one JSON value on stdout"; a contract that is
  silently optional is not a contract. It raises `ContractViolation` now.
* The stale `psh_pkg (2).tar.gz` (v0.4.0, carrying 187 AppleDouble `._*` files and
  `.hypothesis` caches) is removed from the tree.

## Tests

```
230 -> 345 passing (296 after the defect fixes, before the loop and composition suites)
```

* `tests/test_review_v5_1.py` — 66 tests, one per reproduction above. **55 of 62 fail on
  v0.5** when the file is dropped into an unmodified tree; the 7 that pass there are the
  deliberate controls (ordinary narrowing still works, a properly narrowed child is still
  allowed, the non-timeout execution paths still work).
* `tests/test_authority_property.py` — the delegation property rewritten one dimension at a
  time, plus the anti-vacuity test that checks the properties are reaching their
  assertions.

## Still open, stated plainly

* The planner is still a placeholder and `validate_plan` still has no typed plan to
  validate. This release is enforcement, not runtime; see `docs/ROADMAP_AGENT_RUNTIME.md`.
* `backend="python"` components still run in the kernel process. `require_isolated_tools`
  is how a policy refuses them, and it now reaches the broker from the run as well as from
  the kernel — but the honest boundary is unchanged.
* Only `NoSandbox` ships. The egress proxy governs clients that honour proxy variables; a
  raw socket bypasses it. The process-group kill is a containment improvement, not a
  sandbox.
* Claim support is still lexical, and the audit chain is still tamper-evident rather than
  tamper-proof.
* The runtime built on this release was itself reviewed afterwards; two findings survived
  and are closed in `docs/RUNTIME_SECURITY_REVIEW.md`.
