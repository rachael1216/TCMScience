# Binding workflows to registered protocols

An optional `TaskContract.protocol_binding` ties a statistical design to a
`ScientificLedger` protocol record and its expected content fingerprint:

```python
from psh.workflow import ProtocolBinding, ScientificCompiler

# registered is the Node returned by ledger.preregister(...).
# protocol is the Protocol used to construct the task's StatisticalDesign.
binding = ProtocolBinding(registered.id, protocol.fingerprint)
# Set protocol_binding=binding on that task's TaskContract.
compiler = ScientificCompiler(scientific_ledger=ledger)
compiled = compiler.compile(program, envelope, policy=kernel.policy)
```

The bound task must explicitly include `Destination.PERSISTENT` in destinations
and `Effect.PERSIST` in effects, alongside its other execution destinations and
effects. This is the existing ledger's conservative access requirement, including
for reads; the compiler does not grant missing authority. Binding resolution
itself writes no records. Under the current effect model, such tasks cannot be
declared PURE. The run and current ledger policy must also permit this access.

The resolver reads content and label in one WorkGraph transaction, enforces project
and sensitivity boundaries, checks the record content hash, reconstructs a typed
Protocol and checks its fingerprint. The compiler compares that fingerprint with
the binding and compares all protocol fields with `statistics.protocol`.

| Code | Reason for rejection |
| --- | --- |
| PROTOCOL101 | Bound task has no statistical design |
| PROTOCOL102 | No scientific ledger supplied to compiler |
| PROTOCOL103 | Record unavailable, wrong kind/project, invalid or unauthorized |
| PROTOCOL104 | Expected fingerprint differs from registered protocol |
| PROTOCOL105 | Analysis protocol differs from registered protocol |

Error details do not echo record IDs, stored contents or underlying exceptions.
Use opaque IDs; serialized bindings still contain the supplied ID and fingerprint.
The registered sensitivity is joined into the task's input floor, propagated to
downstream tasks and included in bound-task fingerprints. Binding changes therefore
invalidate downstream tasks through existing amendment analysis. The program
fingerprint still describes submitted declarations, not mutable database state.

Pass `scientific_ledger=ledger` also to `ScientificPlanner` and
`assess_amendment` when using bound programs. The planner resolves records on each
planning call under that call's envelope and current ledger policy. Contracts
without a binding preserve their previous wire format and hashes.

This is strict equality checking, not automatic amendment approval: a changed
analysis fails until the declared protocol and binding are intentionally updated
to an appropriate registered record. Existing observation/deviation recording
remains separate. StatisticalDesign fields outside Protocol (such as partitions
and multiplicity plans) are not registered by this binding, although they remain
part of the workflow fingerprint and statistical checks.

Limits: registration timing is not proven, hashes are not signatures, execution
is not observed for adherence, and compilation is not an atomic transaction with
subsequent execution. Recompile before execution; normal runtime policy, evidence
and release gates remain required. A binding is not proof of scientific validity.

Run `python -m pytest -q tests/test_protocol_binding.py` for synthetic tests.
