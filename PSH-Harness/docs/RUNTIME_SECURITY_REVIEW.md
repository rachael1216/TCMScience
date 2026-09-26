# Adversarial review of the v0.6–v0.9 runtime, and its closure

The agent runtime (`loop.py`, `planner.py`, `checkpoint.py`, `subagent.py`,
`supervisor.py`, the MCP and A2A adapters) was written on top of the v0.5.1 kernel under
one rule: **the loop must not bypass the TrustedKernel**. It does not. Every action it
takes is one of three broker calls, and a test counts them.

That rule was necessary and not sufficient. A second review — identification of candidate
findings, then an independent false-positive filter per finding, then a cut at
confidence 8 of 10 — was run against the runtime after it was finished. Four candidates
came back; two survived. Both are the same shape: the loop went *through* the kernel and
handed it the wrong label. A gate that is given PUBLIC rules PUBLIC.

Both were reproduced on the unfixed tree before anything was changed, and the
reproductions are now `tests/test_loop_labels.py`, which fails 23 of 24 on that tree.

---

# Vuln 1: Label laundering across loop tasks: `src/psh/runtime/loop.py` (`_dispatch`, `_projection`)

* Severity: High (confidence 9/10 after filtering)
* Description: `AgentLoopController` recorded the broker-assigned label of every task
  result on `TaskNode.label` and then never consulted it when building the next task's
  input. For a MODEL task, each upstream result became a `ContextItem(kind="evidence")`
  with the default label — PUBLIC — and `ExecutionBroker.call_model` took a
  `ContextProjection`'s label on trust. For a TOOL task the bare value was placed in the
  payload and ingress re-classified it from its text, which sees an identifier and does
  not see a count derived from a PHI cohort. Invariant (5) — a derived value carries the
  join of its sources — was violated on every edge of the execution graph.
* Exploit scenario (reproduced): a plan of `read` (TOOL returning
  `"Patient Alice Smith MRN 04851923 …"`) and `summ` (MODEL at `PUBLIC_REMOTE`, depending
  on `read`) terminated `goal_satisfied`; the public provider's prompt contained the MRN;
  the model gateway recorded `allowed=True, PUBLIC_REMOTE, label=PUBLIC`; and
  `LoopResult.label` said `phi [medical_record_number, name_cued]`. The kernel knew and
  the gate was never told. Tool variant: `cohort_count` given a PHI chart returned
  `{"readmissions": 3, "cohort": 12}` (broker label PHI); `public_uploader` at
  `PUBLIC_REMOTE` received it under an INTERNAL verdict.
* Fix: labels travel. `ExecutionGraph.labeled_results()` hands `_dispatch` each result
  wrapped in its label; evidence items carry it; tool payloads carry it where ingress
  joins it; a model task's result is labelled with what the model was shown joined with
  what it said; a delegate's result with what was handed over joined with what came back.
  An evidence item the compiler would drop for the destination is a refusal
  (`EgressDenied`, audited as `loop_task_refused`) rather than a quieter prompt: the
  upstream result *is* the task's input, and a task run without it would succeed at
  answering a different question.

# Vuln 2: Loop objective and planner feedback reach models unclassified: `src/psh/runtime/planner.py` (`_projection`), `loop.py` (`_projection`)

* Severity: High (confidence 8/10 after filtering)
* Description: `Runner.run` classifies the request at stage 1, labels its turn item with
  the result, and `_preflight` refuses a projection above the run's ceiling. The loop
  path did none of this. `ModelPlanner._projection` built the turn item from the
  objective and an instruction item from validator refusals and evaluator failures —
  which embed task result values and component error text — with the default label;
  `AgentLoopController._projection` did the same with each task's objective. Turn and
  instruction items are load-bearing (never dropped by the compiler), and no gate compared
  a label with `envelope.max_label`; only `Runner._preflight` did.
* Exploit scenario (reproduced): `ModelPlanner(..., model=<PUBLIC_REMOTE profile>)
  .plan(LoopState(objective="Summarise the chart of patient Alice Smith, MRN 04851923"))`
  delivered the MRN to the public provider; the gateway recorded
  `allowed=True, PUBLIC_REMOTE, label=PUBLIC` while `kernel.classify(objective)` said PHI.
  The same held for a task objective in a static plan, for a child loop's objective
  written by a parent plan's DELEGATE task or a supervisor, and for a replan whose
  feedback quoted a PHI tool result that had failed an `enum` check.
* Fix: the objective is classified once at loop ingress (`objective_label_for`) and its
  label is carried onto the planner's turn item; a model-authored plan's label — what the
  planner's model was shown joined with what it wrote — is recorded on
  `LoopState.plan_label` and every task objective carries it (a task objective is derived
  from that prompt); planner feedback carries the join of the labels of the graph it
  quotes; a refused attempt's text carries the label its call was given; a DELEGATE task
  hands its label to the child through the contract's projection, and
  `LocalSubagentBackend` seeds the child's objective label from it. `LoopResult.label`
  now joins the objective's label with the results', because a child asked about a PHI
  chart answers about that chart.

---

## The kernel-level closure

Fixing the callers would have fixed these two callers. The defect was that the broker had
one value it took on trust — a `ContextProjection`'s aggregate label — and the rule of
`call_tool` ("classify at the boundary, never trust the caller") did not apply to it.
Two changes to `kernel/egress.py` make the next mis-built projection a refusal rather
than a finding:

* **The broker re-classifies a projection.** `ExecutionBroker._reclassified` classifies
  the rendered text that will actually be sent and joins it with the claimed label. A
  projection can be escalated at that boundary and is never trusted downward. The
  `ModelCallResult` carries the label the gate ruled on, so anything built on a model's
  answer starts from the join.
* **Both gateways enforce the run's ceiling.** `ModelGateway.check` and
  `ToolGateway.check` refuse a label above `envelope.max_label`. Before, only
  `Runner._preflight` made that comparison, so any caller that reached the broker by
  another route — a loop, a planner, an adapter — executed under a ceiling no gate read.

One consequence surfaced immediately and is stated rather than hidden: the classifier
floors ordinary text at INTERNAL ("the user's own working material"), so a run whose
ceiling is PUBLIC can send nothing to a model or a tool. No shipped profile has a PUBLIC
ceiling. The verification model's envelope was stated as PUBLIC — a ceiling nothing read —
and now states what the verifier may lawfully receive under the policy, which is what the
gateway had been enforcing all along through `ModelProfile.may_receive`.

What the re-classification does and does not do: it catches verbatim content the lexical
classifier recognises. It does not see a derived value — a count, a paraphrase — and is
not meant to; that is what carrying labels is for. The two layers cover each other's
blind spot: carried labels for derivation, the rescan for a caller that forgot.

## The two findings that did not survive, and what was done anyway

**Checkpoint contents bypass the persistence gate** (confidence 6/10). Accurate as
mechanics — `capture()` wrote every succeeded result to disk with no ceiling, no label and
default permissions — but bounded: only an operator can wire a checkpoint store, and in
the default configuration the WorkGraph holds the same material by design. Fixed as
hygiene: a checkpoint is a durable write and obeys the persistence rules like any other.
A result above the persistence gateway's ceiling, one whose label does not permit
`PERSISTENT`, an unlabelled one, or any result of a run whose envelope withholds
`PERSISTENT` is **withheld** — the record names the task and the reason, an audit event
(`loop_checkpoint_withheld`) is written, and on resume the task runs again, which is the
case the idempotency key exists for. Labels are stored with results and restored joined
with a fresh classification, so a resumed graph never starts from PUBLIC and an editor who
lowers a stored label gains nothing. Files are written owner-only.

**Checkpoint integrity is vacuous** (false positive, 3/10). The content hash is unkeyed
and documented as such ("tamper-evident, not tamper-proof; same posture as the event
chain"), and any writer who can reach the checkpoint directory can already rewrite the
kernel's execution policy file next to it. The one real defect in the finding was fixed:
`Checkpoint.from_dict` refused nothing when the `hash` key was absent and hashed the
record for the editor. A record with no hash is now refused by `from_dict`, by
`CheckpointStore.load`, and is not listed by `all()`.

## Tests

`tests/test_loop_labels.py` — 24 tests, 23 of which fail on the unfixed tree:

* the two reproductions above, the tool variant, and "refused rather than run without
  its input";
* a model result labelled with what the model saw; a tool still receives bare values;
* the planner objective, the task objective, feedback that quotes a PHI result, a plan a
  model wrote from a PHI prompt (clean text, PHI label), a delegated objective;
* the broker re-classifying a mis-built projection and leaving a correct one unchanged;
  both gateways enforcing the run's ceiling;
* checkpoints storing and restoring labels, escalating a restored result by its text,
  withholding above the ceiling, withholding unlabelled results, withholding under a run
  that may not persist, refusing a hashless record, owner-only files;
* a structural test that refuses any `ContextItem(...)` built in the runtime without a
  stated label — the omission is the defect, so the omission itself fails;
* the broker-counting invariant, re-asserted: the fix added classification and no fourth
  way to act.

Three existing tests changed their setup, none their claim: two hand-built graphs now give
their succeeded nodes a label (the loop always does), and the gate-composition test's
narrow policy uses INTERNAL rather than PUBLIC so the isolation refusal it asserts is the
one that fires.

## Still open, stated plainly

* The rescan is lexical. A model shown PHI that paraphrases it without identifiers is
  labelled PHI by the carried label, not by the text — which is the design, and the
  reason `Runner`'s output-only classification of a model reply (pre-existing, unchanged)
  is weaker than the loop's join.
* `DelegationGateway` compares a contract's projection label with the child's ceiling
  and still does not read the objective text; the child classifies it at its own ingress.
  A remote agent's outbound message goes through `ToolGateway`, where ingress does.
* Resume re-authorises unfinished tasks and, as before, not finished ones. A task the
  current policy would refuse can still be presented as done by a checkpoint; what is
  built on its result is gated as everything else is.
* The default `ToolGateway.allowed_paths` (`state_dir/*`) lets a filesystem tool's
  payload name the kernel's own policy file, event store and secrets directory. Older
  than this runtime, and worth its own change.

## Addendum (v0.5.2): the read side of project memory

`kernel/persistence.py` closed the write side of the WorkGraph in v0.2 so that mis-labelled
content could not become "a standing invitation to include PHI in a future prompt". Until
v0.5.2 nothing read that memory back into a run except items a caller passed by hand, so
the write side's properties had never been tested against a reader. `context/memory.py`
is the reader, built under the same review posture, and these are the properties its tests
pin (`tests/test_memory.py`):

* **Validated knowledge only.** `verified` and `system` nodes are retrievable; `candidate`
  nodes are not unless an operator names that status; `REJECTED_CLAIM` is excluded even
  from a retriever told to recall it, and holds no text in any case.
* **Labels travel.** An item carries the label the gateway stored on its node. A PHI claim
  whose text a lexical scan would rate lower reaches the projection as PHI, and
  `LoopResult.label` joins it.
* **Two withholdings, both counted.** Memory above the run's `max_label` is withheld, and
  memory that may not reach the model's destination is withheld — at retrieval, so the
  loop's rule "an upstream result dropped for policy is a refusal" is never triggered by
  memory. Withheld memory is a quieter prompt; the retrieval trace records why.
* **Scoped.** Retrieval is by the envelope's project; with none, nothing is returned unless
  `cross_project=True` is set deliberately.
* **Read-only.** No `remember()` exists, because a helper that wrote model output into
  trusted memory would be the laundering path `commit_rejected` closes. A structural test
  parses the module and refuses any call to `add`, `update`, `link`, `commit_node`,
  `commit_raw` or `execute`.

What it does not do: ranking is lexical and deterministic, not semantic, because retrieval
on the critical path must not itself require egress; and memory is recalled per task
objective, not accumulated into a transcript, for the reason `ContextProjection` gives.
