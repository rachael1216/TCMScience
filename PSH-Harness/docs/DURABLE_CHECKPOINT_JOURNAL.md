# Durable checkpoint journal and conservative operation recovery

`JournalCheckpointStore` is an opt-in replacement for `CheckpointStore` in the
existing loop's `checkpoints=` argument. It stores append-only governed snapshots
in SQLite, with WAL and `synchronous=FULL`, rather than overwriting a snapshot.
It does not implement replay of every tool/model/event, workflow amendment or an
exactly-once distributed execution engine.

## Integration

```python
from psh.runtime import AgentLoopController, JournalCheckpointStore, OperationLedger, resume

# kernel, planner and registry are the application's existing configured components.
operations = OperationLedger("research-state/operations.sqlite")
try:
    with JournalCheckpointStore("research-state/checkpoints.sqlite") as journal:
        controller = AgentLoopController(kernel, planner=planner, registry=registry,
                                         checkpoints=journal, operations=operations)
        result = controller.run("research objective", kernel.policy.envelope())
        anchor = journal.anchor()  # retain externally if suffix-loss detection is needed
finally:
    operations.close()
```

After a restart, reopen **both** stores. Select the prior loop's checkpoint with
`journal.latest_for(loop_id)`, call `resume(checkpoint, kernel)`, then run the
controller with `resume_from=state` and `state.envelope`. Resume still narrows the
old authority against current policy, restores budget usage, and reclassifies
restored results. Redacted snapshots still require the original plan/objective
to be supplied through the existing `resume` API. Missing checkpoints return
`None`; the application must handle that before calling `resume`.

The journal stores what `capture()` gives it, including original evidence references,
result labels, withholding reasons and plan sensitivity floors. The loop already
uses governed capture: forbidden persistent content is withheld before save. Like
the file checkpoint store, this low-level store cannot authorize arbitrary caller
data. Do not hand it manually assembled snapshots containing ungoverned content.

## Integrity and ordering

Each record has a monotonically increasing sequence, schema version, checkpoint
identity, canonical serialized snapshot, prior record hash and SHA-256 hash.
Checkpoint payload hashes are checked independently of journal hashes. Appending
reads and verifies the chain and inserts under `BEGIN IMMEDIATE`, serializing
writers across connections/processes. Duplicate delivery of the same checkpoint
is idempotent; reuse of an ID with different contents is refused.

Readers verify the complete chain before returning any result. Invalid versions,
missing middle entries, changed contents or identity mismatches cause refusal;
there is no silent fallback to an older apparently good snapshot. `latest_for`
uses append order, not a caller's wall clock. Unsupported/non-finite JSON values
are refused rather than converted into lossy string representations.

`anchor()` returns `(record_count, head_hash)`. Passing that exact previously saved
head to `load`, `all` or `latest_for` as `expected_anchor=` detects suffix loss,
including an emptied journal. It also rejects a newer head: this is an exact-head
check, not a prefix-inclusion proof. Without an external anchor, removing a valid
suffix or rewriting the whole database cannot be detected reliably. Hashes are
tamper evidence, not cryptographic signatures.

The current implementation verifies all snapshots on each append/read, with O(n)
work per operation and O(n) read memory. It targets bounded prototype runs; it does
not claim efficient replay of unbounded journals. The append schema is version 1;
automatic upgrade migration is not implemented.

## Side-effect recovery

Checkpoint history is insufficient to determine whether an external operation
finished before the process crashed. Use the durable `OperationLedger` alongside
the journal. This increment hardens that ledger:

- `begin()` checks replay safety and records the attempt in one SQL transaction.
  Two connections cannot both admit the same uncertain non-idempotent operation.
- A recorded non-idempotent operation cannot become safe to replay merely because
  a new manifest declares it idempotent. Known invocation identity must also match.
- A non-idempotent tool's ordinary execution exception, as well as a timeout,
  leaves the outcome UNKNOWN. The tool may have changed the world before raising.
  Automatic retries are refused until the operation is reconciled.

Idempotent work can still run again; this is not an execution lease or a guarantee
that the provider implements idempotency correctly. Existing pre-dispatch policy
and budget refusals remain distinct from execution failures. Do not delete an
uncertain operation row to force a retry. Reconciliation must establish the actual
external outcome; this change does not add an operator reconciliation UI.

The loop still records checkpoint failures as audit events and continues running.
It does not promise crash recovery for progress that could not be checkpointed,
or an atomic transaction spanning provider effects, operation rows and snapshots.

## Verification

`tests/test_journal.py` covers durable reopen, duplicate delivery, clock skew,
corruption, unknown schema, suffix deletion with an anchor, rollback of an
uncommitted SQL transaction, concurrent connections, withheld content, resumed
tool execution retaining evidence references, atomic operation admission and
non-idempotent exceptions. It runs together with existing checkpoint, agent-loop
and durability suites in CI. These tests do not simulate physical power loss or
prove filesystem hardware durability.
