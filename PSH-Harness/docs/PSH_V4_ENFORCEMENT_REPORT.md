# psh v0.4 — Self-audit closure and borrowed enforcement

**What changed.** Two things, in this order. First, I turned the review method on my own code:
an adversarial self-audit of v0.3 found eleven vulnerabilities, several worse than anything the
four external reviews had found, and nine are now closed as tests. Second, the user asked that
Codex and Claude Code be leveraged as far as possible. Codex is open source and was read from
source (165 files, 2.3M chars); Claude Code is not, and was read from its official documentation
(20 pages, 1.37M chars). Six patterns were adopted with file- or page-level attribution; four
were deliberately not, with reasons. `PATTERN_ATTRIBUTION.md` is the full record.

**What did not change.** The positioning: a governance kernel, not an agent OS and not
clinical-safe. And the two-package structure: `sable` is untouched (139 offline tests still pass)
and composed through its adapter.

---

## 1. The self-audit

Eleven probes tripped on v0.3 (`handoff/self_audit_v03_baseline.json`). Triage before fixing
mattered, because they were not one class of defect: some were real bugs with in-process fixes,
one was a data-model gap, and two were architectural — properties no in-process code can
provide.

| Probe | Severity | v0.4 |
|---|---|---|
| `A1 direct_provider_call_bypasses_broker` | critical | **open — architectural** |
| `B_base64_evades` | medium | closed |
| `B_rot13_evades` | medium | closed |
| `B_hex_evades` | medium | closed |
| `B_split_across_fields_evades` | medium | closed |
| `C2_label_setattr_bypass` | high | **open — architectural** |
| `D1_anyone_can_declassify` | high | closed (re-probed on the real property) |
| `F1_budget_state_public` | low | closed (re-probed on the real property) |
| `G1_chain_breaks_under_concurrency` | high | closed |
| `J1_trust_is_a_string_claim` | high | closed |
| `K1_unknown_condition_extrapolation_passes` | medium | closed |

Two probes (`D1`, `F1`) still trip their *original* criteria after the fix, because those
criteria measured a proxy for the defect — whether a `Declassification` dataclass can be
constructed, whether an attribute named `state` exists. Re-probed on the property that matters:
a forged declassification is re-classified to PHI at ingress and counted, an unlisted principal is
refused, the budget snapshot is frozen and no live counter is reachable by a public name. I did
not edit the audit script to make it pass; the re-probe is recorded alongside the original.

**The worst finding was `G1`.** The event store read the chain head and then inserted, with no
lock and autocommit. Six concurrent writers lost five of every six records to `UNIQUE(seq)`
violations. An audit log that drops entries under load is not an audit log. Fixed with a
process-level lock around the read-insert pair *and* `BEGIN IMMEDIATE` with a busy timeout, so a
second process serialises at the database rather than racing the SELECT. Verified: 6 threads × 30
appends and 5 processes × 40 appends, zero lost, chain intact; 11,166 appends/s.

**The most instructive was `D1`.** While wiring the gated declassification path I found a
pre-existing `TrustedKernel.declassify` that recorded who did it and checked nothing — any
principal string lowered any label — and it shadowed the new method. Same duplicate-definition
trap that bit the v0.2 verifier. Removed; the policy now names permitted declassifiers (empty by
default) and a floor, and ingress honours a lowered label only when every declassification id on
it was issued by this kernel.

**`J1` reframed evidence trust.** `trusted = retrieved_by in TRUSTED_RETRIEVERS` was a string
comparison anyone could satisfy by typing the name. Trust now derives from an HMAC signature under
a kernel-held key; `from_text()` defaults `trusted=False` regardless of the retriever name; the
runner recomputes trust from the signature on every incoming record because a frozen dataclass
copied with `replace()` keeps the stale flag. Six existing tests had been getting trust for free
by naming the retriever. They now sign, which is what the real path does.

## 2. The two that stay open

`A1` (in-process code calls a provider; the broker sees nothing) and `C2`
(`object.__setattr__` defeats a frozen dataclass) have no in-process fix. Both surveyed systems
answer them the same way — the operating system enforces the boundary — and v0.4 adopts the
portable part of that answer (section 3.5–3.6): tool execution moves to a subprocess with a
default-deny environment behind a kernel-owned egress proxy. That raises the **tool** boundary
from cooperation to process level. It does not confine code running inside the kernel process
itself, and `test_in_process_bypass_is_a_documented_limit_not_a_closure` asserts the limit exists
so nobody mistakes it for closed.

## 3. Patterns adopted

Full attribution with file paths and page names is in `PATTERN_ATTRIBUTION.md`. Summary:

| # | Pattern | From | Landed in | Measured |
|---|---|---|---|---|
| 1 | Declarative execution policy with self-testing rules | Codex `execpolicy` | `kernel/execpolicy.py` | evaluate 0.0084 ms; load + self-test all 12 rules 0.062 ms |
| 2 | Approval writes back a rule | Codex `ReviewDecision::ApprovedExecpolicyAmendment` | `kernel/approvals.py` | — |
| 3 | `forbidden` absolute, first match, specificity irrelevant | Claude Code `permissions` | `execpolicy.py` | — |
| 4 | Typed hook lifecycle, one JSON protocol | Claude Code `hooks`; same shape in Codex `hooks/` | `kernel/hooks.py` | 5 hooks + 5 audit events 0.3869 ms |
| 5 | Default-deny environment | Codex `exec_env.rs` `env_clear()` | `kernel/isolation.py` | env build 0.0022 ms |
| 6 | Egress proxy as network boundary; private ranges refused even when allowlisted | Codex `network-proxy` | `kernel/isolation.py` | host decision 0.0342 ms; spawn overhead +1.47 ms |

**One recorded departure.** Claude Code evaluates deny → ask → allow with first match winning and
specificity irrelevant. psh keeps that for `forbidden` — nothing relaxes a forbidden rule — but
between `prompt` and `allow` the most specific rule wins (ties to the later rule). Under strict
ask-before-allow, an approval amendment could never stop a question: "ask before `git push`" plus
"allow `git push origin` from now on" would still ask forever. Codex's
`ApprovedExecpolicyAmendment` presupposes a prompted command *becomes* allowed, so where the two
sources disagree this follows Codex. The property that matters is unchanged and tested: a
narrower `allow` never reaches a command a `forbidden` rule refuses.

**Convergence worth noting.** Codex's own hook implementation consumes Claude Code's exact JSON
shape — `hook_event_name`, `hookSpecificOutput`, `permissionDecision`, and Codex's
`decision.behavior` variant. Two harnesses converging on one protocol is the strongest available
reason to adopt it rather than invent a third; psh's parser accepts both variants.

## 4. Patterns deliberately not adopted

| Pattern | Why not |
|---|---|
| Classifier-based auto mode (a model judges whether an action is safe) | A second model call on the critical path that needs its own egress decision. psh's position is that policy decisions must not themselves require an egress. |
| SOCKS5 + HTTPS MITM in the proxy | MITM means the kernel holds a CA key and terminates TLS for every tool — a large trust concentration for a single-user harness. CONNECT-level host policy covers the threat model. |
| Starlark policy language | Rules are dataclasses / JSON. A second language inside a small kernel is cost without benefit at this scale. |
| OS sandbox (Seatbelt / bubblewrap / seccomp) | Cannot be installed or verified from inside this session. `SandboxBackend` is an adapter; `NoSandbox.describe()` states that raw sockets are not confined. |

## 5. What the environment would not let me verify

This sandbox forbids **all** listening sockets — TCP loopback and Unix domain alike. The egress
proxy therefore could not bind here. Three consequences, each handled rather than hidden:

1. The runner **fails closed**: when a run wants network and no proxy can be bound, it raises
   `ProxyUnavailable` rather than running unproxied. Running unproxied would be the v0.3 bypass
   with extra steps. When no network is wanted, the child is pointed at a dead loopback port.
2. The proxy's request handler is tested over in-memory socketpairs with a threaded upstream:
   allow, refuse with reason, CONNECT refuse, `Proxy-Authorization` stripped before forwarding,
   every decision recorded. That driver found a real bug — the handler honoured HTTP keep-alive
   and blocked 15 s after every forwarded exchange. It now closes after each.
3. The two full child → proxy → upstream TCP tests are marked `needs_listener` and **skip here
   with a stated reason**. They run on an ordinary workstation. The spawn-overhead figure above
   therefore excludes proxy start-up, and `v4_benchmark_results.json` says so.

## 6. Bugs the process caught this round

1. Event chain lost records under concurrency (audit G1).
2. Ungated `declassify` shadowing the gated one — the audit finding, in plain sight.
3. Evidence trust by string comparison (J1), and six tests that depended on it.
4. `is_clinical_claim` exempted any sentence whose outcome was not in the vocabulary, so the
   population conflict `check_scope` correctly detected for a cystic-fibrosis claim against a
   sickle-cell trial was never consulted. An unrecognised outcome now widens the checks, not
   exempts the sentence.
5. Execution policy applied to every tool with an `args` key, prompting on a local summariser.
   Now scoped to command-executing components.
6. `urlsafe_b64decode` does not take `validate=`; the decode layer silently never ran for one
   alphabet.
7. Proxy handler keep-alive stall (above).
8. Under strict deny→ask→allow, amendments could never relax a prompt — a design conflict between
   the two sources, resolved and recorded (section 3).

## 7. Measured

| Mechanism | Figure |
|---|---|
| Classification, 600 chars, with decode-and-rescan | 0.3053 ms (text-only 0.141 ms; ×2.17) |
| Base64-encoded PHI detected | 0.1119 ms → PHI |
| Random 800-char blob | → SENSITIVE (uninspectable is not clean) |
| Execution policy evaluate / load+self-test | 0.0084 ms / 0.062 ms |
| Hook dispatch, 5 hooks | 0.3869 ms |
| Isolated spawn vs bare | 10.2715 ms vs 8.8001 ms (+1.47 ms, excl. proxy start) |
| Concurrent event appends | 400 → 400 records, 0 lost, 11,166/s, intact=True |
| Evidence sign / verify | 0.0038 / 0.0015 ms |

Suites: **170 psh tests pass, 2 skip (listener)**; sable 139 offline pass untouched. Run with
`PYTHONPATH=sable_pkg/src:psh_pkg/src`.

## 8. Boundaries, restated

- Tool execution is process-isolated; kernel-internal code is not. An OS sandbox is the next
  layer and the adapter for it exists.
- The proxy governs clients that honour proxy variables. A raw socket bypasses it.
- Classification decodes base64/hex/rot13 and recombines fragments within one payload. Fragments
  split across separate calls are not recombined; the coverage note says so.
- Signed evidence proves a record came through a registered capability. It does not stop a
  compromised capability from signing garbage — that is the correct boundary.
- Claude Code patterns are drawn from documentation, so they support claims about behaviour, not
  implementation.

## 9. Next

Sandboxing remains the largest gap and now has a defined seam (`SandboxBackend.wrap`). After it:
the planner behind the model gateway, then semantic entailment on top of structured claims.
