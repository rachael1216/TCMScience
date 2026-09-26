# Scientific records and protocol deviations

`psh.scientist` adds structured hypotheses, preregistered protocols and observations
to WorkGraph. It is an append-only application API: recording results creates new
nodes, and never updates the original hypothesis or protocol. This is the second
increment of the Scientific Compiler roadmap, not an autonomous experiment runner.

## Model

- `Hypothesis`: proposition, population, predictions, falsifiers and alternatives.
- `Protocol`: primary/secondary endpoints, exclusion criteria, statistical test,
  sample size assumptions, covariates, subgroup plan and stopping criteria.
- `Observation`: summary, artifact references and observed/negative/inconclusive outcome.
- `Deviation`: explicit reason and actual protocol, embedded atomically in the
  observation together with a field-by-field planned/actual comparison.

All record objects are frozen dataclasses using immutable tuples. A protocol hash
is a SHA-256 digest of canonical JSON; whitespace inside scientific text remains
significant. A stored record also hashes its schema version, type, contents and
source IDs. `read()` refuses altered bodies or unsupported schema versions.

These hashes detect accidental edits, not an adversary who can rewrite the SQLite
database and recompute hashes. They are not signatures or timestamp attestations.
The application must actually register a protocol before data collection; the API
does not certify when an external experiment occurred. Artifact references are
recorded, not fetched or independently verified.

## Use

```python
from psh.scientist import ScientificLedger, Hypothesis, Protocol, Observation

# kernel is a TrustedKernel; project_id identifies an existing WorkGraph project.
ledger = ScientificLedger(kernel, project_id)
envelope = kernel.policy.envelope()
h = ledger.hypothesize(Hypothesis(
    "Fixture intervention changes endpoint", "fixture population",
    ("endpoint changes",), ("no endpoint change",), ("batch effect",),
), envelope)
p = Protocol("endpoint A", (), "prespecified exclusions", "permutation test",
             "simulation-based precision assumption", (), "none", "fixed sample size")
registration = ledger.preregister(h.id, p, envelope)
result = ledger.observe(registration.id, Observation(
    "No effect in synthetic fixture data", ("fixture:data-1",), "negative",
), p, envelope)
record = ledger.read(result.id, envelope)
```

If any of the eight protocol fields changes, `observe()` requires a nonempty
`deviation_reason`. The observation stores both protocol hashes, the actual
protocol, and changed fields. The original registration remains untouched. Each
observation is a distinct record; the caller should retain returned node IDs and
reconcile uncertain delivery before retrying. This API does not provide exactly-once
record delivery or automatically deduplicate scientifically distinct observations.

## Governance and persistence

Writes pass through `PersistenceGateway`, using the meet of the supplied run
envelope and current kernel policy. The ledger requires PERSISTENT authority and
enforces project membership and sensitivity ceilings. Source and project labels
are joined into each new record's label; callers can also supply `label=...` for
derived content whose sensitivity cannot be inferred from its text.

The gateway accepts an `inherited_label` floor and classifies reference and
provenance fields as well as title/body/metadata. It atomically inserts the node
and outgoing source edges with a WorkGraph savepoint. Failed edge insertion rolls
back the node and does not increment the gateway commit count. A reentrant graph
lock serializes transaction writes on the shared connection. Audit notification
occurs after the graph write; graph and audit databases are not one distributed
transaction, and an audit failure is not an exactly-once guarantee.

Graph edges are `DERIVED_FROM`, so `lineage()` and `why()` can trace an observation
back to its protocol and hypothesis. They express derivation, not verified support.
All scientific records are stored as CANDIDATE, and no hypotheses or observations
are automatically promoted to verified knowledge. Even when explicitly selecting
these new node kinds, normal memory retrieval still applies its validation gate.

WorkGraph itself remains a trusted low-level mutable store. The append-only
contract belongs to ScientificLedger; direct database/graph mutations are not
made impossible. Use the ledger for scientific records and retain normal kernel
egress controls when displaying or exporting returned contents.

## Validation and remaining work

Run `python -m pytest -q tests/test_scientist_records.py` from PSH-Harness.
Tests cover all eight protocol fields, PHI inheritance, narrowed authority,
cross-project refusal, hash mismatch, durable reopening, atomic rollback,
concurrent write isolation and exclusion from verified memory.

Not implemented here: hypothesis competition or ranking, scientific belief updates,
statistical validity checks, experiment execution, event-journal replay, remote
attestation, or automatic claim publication. This API can record scientifically
incorrect proposals; it preserves what was planned and changed so those proposals
can be reviewed.
