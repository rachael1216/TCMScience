# Trusted tool retry facts

This increment addresses the non-idempotent retry counterexample in the supplied
architecture review. It is not a claim that every scientific trust boundary has
been completed. Review measurements and broader superiority claims have not been
adopted as independently verified project results.

## Enforced boundary

`TaskContract.side_effect` is a requested contract, not authority to override a
trusted component manifest. For a tool, ScientificCompiler now requires registry
`manifest.idempotent is True` for either:

- a `PURE` or `IDEMPOTENT` declaration (`EFFECT106` on mismatch/missing fact);
- more than one automatic attempt (`RETRY102` on mismatch/missing fact), even if
  the proposal says `AT_LEAST_ONCE`.

Existing contract-level retry restrictions still apply. A nonrepeatable contract
cannot acquire retries just because the manifest permits them. Ordinary one-shot,
nonrepeatable tasks remain supported. Tool task fingerprints include the resolved
trusted idempotency bit so amendment impact analysis notices a fact change.

Idempotency is necessary here, **not proof of purity**: it does not certify absence
of external effects, scientific correctness or deterministic results. The current
manifest has no complete purity attestation. Component registration remains a
trusted host operation; a lying trusted manifest is outside this check's guarantee.

## Runtime defense without a durable ledger

AgentLoopController independently reads the actual component's manifest before
dispatch. A tool exception can schedule an automatic retry only if that fact was
exactly `True`, not merely truthy. Missing/invalid idempotency facts are conservative.
Model retry behavior is unchanged. With an OperationLedger, uncertain effects
continue to be recorded as unknown and investigated through that ledger.

A locked controller-local claim set additionally refuses reuse of the same
nonrepeatable operation key during replans or another run call on that controller.
Upgrading the manifest afterward does not erase an earlier uncertain claim. A
claim is retained conservatively even if a later gate refuses the call before any
effect: safety takes precedence over guessing whether retry would be harmless.

The local claim set is **not durable**. A newly constructed controller or process
cannot recover it. Durable operation history still requires OperationLedger; this
patch does not make that ledger mandatory, add external-effect transactions, or
provide exactly-once execution. New task/run IDs or new dynamic visit namespaces
are different operations, not retries protected by the same key. Dynamic cycle
admission now applies additional default-deny checks documented in
`DYNAMIC_WORKFLOW.md`; an approval exception for unsafe repetition remains absent.

## Remaining review work

Still separate: mandatory scientific task roles and analysis modes; trusted
evidence resolution; durable compiled scientific contracts; the dynamic public
release-service sandbox hardening; approved nonrepeatable repetition; durable execution identity and safe
resume. No version bump, old-archive deletion or README marketing changes are
included in this safety patch.

## Tests

`tests/test_trusted_retry_facts.py` covers the contradictory PURE/IDEMPOTENT
proposal, AT_LEAST_ONCE with a non-idempotent tool, missing registry facts,
compiler/planner/dynamic preflight, plain-loop runtime protection with and without
an operation ledger, continued safe retries, controller-local claims and trusted
fact fingerprint changes. Tools are synthetic; no external side effects are sent.
