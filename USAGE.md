# Using the skills, and reading what they return

This is the "how do I actually use this" document. It assumes you have run
`python examples/run_skills.py` once (see [INSTALL.md](INSTALL.md)) and want to
know what the output means and where the boundaries are.

---

## What a skill is

A skill is a **pure function** from an input to a `ResearchArtifact`. It is not a
conversation, not an agent, and not a service — you call it, it returns a
document, and that document is either publishable or refused. There is no hidden
state and no session to manage.

Each skill has two files and one manifest:

```
BioScience-Harness/skills/tcm/normalize-tcm-entities/
  skill.yaml    the contract — inputs, outputs, permissions, evidence policy
  SKILL.md      documentation for humans; never parsed for authority
```

And one implementation, named by `skill.yaml`:

```
bioagent/skills/p0/entities.py : normalize_tcm_entities
```

The manifest is the authority. `SKILL.md` is attached to the artifact as
documentation and is **never** an input to compilation, permission derivation or
scoring — otherwise the security argument would reduce to a language model
reading a README. This is ADR-0003.

---

## Three ways to call a skill

### The CLI

```bash
python -m bioagent.cli skills --dir BioScience-Harness/skills/tcm     # list them
python -m bioagent.cli skill <id> --arg <name>=<value> ... --dir BioScience-Harness/skills/tcm
```

Every skill names its subject differently (`names`, `subject`, `formula_name`).
The CLI tells you which it wants rather than guessing:

```
$ python -m bioagent.cli skill assess-tcm-safety --dir BioScience-Harness/skills/tcm
skill 'assess-tcm-safety' needs --arg subject=<value>
  parameters: subject, co_administered, population, run_id
```

Comma-separated values become lists, which is the only structure the CLI
supports: `--arg names=姜,白芍`.

### Python

```python
from bioagent.skills.p0 import retrieve_tcm_evidence
artifact = retrieve_tcm_evidence("附子", run_id="my-run")
```

See [INSTALL.md](INSTALL.md) for the full table of entry points.

### Inside the governed runtime

A skill also compiles into a `ScientificProgram` that the trusted kernel
validates, which is how it reaches the gateways, budgets and audit chain:

```python
from bioagent.skills.loader import load_skill_dir
from bioagent.skills.compiler import compile_skill

loaded = load_skill_dir("BioScience-Harness/skills/tcm/assess-tcm-safety")
compiled = compile_skill(loaded.spec, envelope)   # envelope: a psh RunEnvelope
```

`compile_skill` **refuses** a skill that asks for authority the envelope does not
hold (`SKILL101`) rather than silently trimming it — a silent trim turns a
misdeclared skill into a mysteriously failing one. You need an `envelope` from a
`psh.policy.PolicySnapshot`; see `tests/test_skill_compiler.py` for a worked
example.

---

## Reading a `ResearchArtifact`

```
normalize-tcm-entities@1.0.0
  question:   resolve 2 TCM name(s) to corpus entities
  versions:   runtime=psh-0.5.3+bioagent-0.2.6|skill=...|source=1982...|benchmark=unversioned
  sources:    1   evidence: 1   claims: 1   outputs: 1
  validation: publishable
  claim [attribution] 1 of 2 queried names resolve to an entity in the seed corpus
  limitations (3):
      - ...
```

| Field | What it is, and why you should care |
| --- | --- |
| `versions` | **Four independent axes** (ADR-0002): runtime, skill, source, benchmark. A result is only reproducible if all four are named. The `source` axis is a hash over the corpus contents — swap the seed data and it moves. |
| `sources` | Pinned `SourceCard`s. A card is *pinned* only when it carries both a `snapshot_hash` and a `snapshot_at`. An unpinned source may not support a publishable claim. |
| `evidence` | Each item carries a **design** (`animal`, `docking`, `randomized_trial`, …), not just a rank. The design is what decides which claims it can license. |
| `claims` | Structured assertions with the population and outcome they *assert* against what their evidence *covers*. |
| `limitations` | **Always non-empty.** An artifact claiming no limitations is claiming to have none, which is never true — so the validator refuses one. |
| `validation` | The real gate. `publishable` or `REFUSED` with stable codes (`ART101`…`ART112`). |

Every output file is content-addressed: `artifact.outputs[0].sha256` is the
digest of the JSON the skill emitted, so a downstream consumer can verify it got
what the skill produced.

---

## What each skill will not do

The most useful thing to know about this system is where it declines. Each
refusal is deliberate, tested, and visible in the artifact's `limitations`.

| Skill | It refuses to |
| --- | --- |
| `normalize-tcm-entities` | **Choose between ambiguous names.** 「姜」 returns 生姜 *and* 干姜 as candidates; 「甘草」 additionally reports its processed forms. Picking one would be wrong roughly half the time and confident every time. |
| `retrieve-tcm-evidence` | **Adjudicate.** It labels design and reports scope, retraction state and coverage limits. Risk of bias, precision and consistency stay `NOT_ASSESSED` because the corpus carries nothing to judge them on. An empty result is reported as an empty result, not as "no effect". |
| `analyze-tcm-network-pharmacology` | **Mix prediction with measurement.** `predicted_targets` and `measured_targets` are separate keys — there is no combined list. Every edge is marked `directness = EXTRAPOLATED`, and the manifest forbids every clinical claim kind. |
| `assess-tcm-safety` | **Answer "safe".** The status is `risk_recorded`, `no_record` or `unknown`. Absence of a record is not evidence of safety, and the artifact says so in those words. 十八反 is checked as a *set* because it is a property of a pair. |

### Why the prediction rule matters

A docking result can support a **mechanism** claim. It can never support
efficacy, association or safety — those are statements about patients, and a
prediction is not an observation of one. This is enforced by the kernel's type
system, not by a prompt: in PSH's claim-support table, predictive designs appear
in exactly one row.

So this is allowed:

```python
# mechanism, from docking — the actual job of network pharmacology
claim_kind="mechanism",  evidence designs=["docking"]
```

and this is refused with `ART106` / `CLM004`:

```python
# efficacy, from docking — a category error, not a quality shortfall
claim_kind="efficacy",   evidence designs=["docking"]
```

---

## Where the data comes from

All four skills read the **TCM seed corpus** compiled into `bioagent.tcm`:
23 herbs, 5 processed forms, 6 formulas, 8 syndromes, 9 classical passages,
2 study records, 14 relations, 14 safety records. It is declared as a pinned
`SourceCard` in every artifact, hashed over its *contents*, so a resolution can
be traced to the corpus that produced it.

It is **illustrative, not exhaustive**, and every artifact says so. Notably it
contains **no clinical trial data** — every study record is at
`EXPERT_EXPERIENCE` or below — which is why `retrieve-tcm-evidence` cannot make
an efficacy claim and `assess-tcm-safety` cannot describe a 十八反
contraindication as a `safety_signal`.

Adding to it means editing `bioagent/tcm/knowledge.py` and bumping
`SEED_SNAPSHOT_AT` in `bioagent/skills/p0/common.py`; the corpus hash moves
automatically, and `scripts/check_lockfile.py` will tell you the lockfile needs
regenerating.

---

## Running the governance layer

```bash
# what would the monthly scan propose?  (never promotes)
python -m bioagent.cli scout --sources registry/skill_sources.yaml --skills skills

# cut a verifiable release from the lockfile
python -m bioagent.cli registry-release --release-id registry-1.0.0 --season season-1
```

The scout **cannot promote**, and that is structural rather than conventional:
`Registry.promote` is the only writer of a stable entry and it requires a
`PromotionDecision` naming a human. A scheduled run has no code path to one. See
`docs/adr/0001`.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `ModuleNotFoundError: No module named 'psh'` | Install the kernel: `pip install -e PSH-Harness`. The two packages are separate and `bioagent` imports `psh`. |
| `ModuleNotFoundError: No module named 'yaml'` | `pip install pyyaml`. It is a declared dependency, so this only happens on a very old install. |
| `no skills found under skills/tcm` | You are not in the repository root. Pass `--dir BioScience-Harness/skills/tcm`. |
| `skill '...' needs --arg <name>=<value>` | The skill wants a different argument name. The message lists its parameters. |
| An artifact comes back `REFUSED` | Read the violation codes. `ART101` means no sources, `ART106` means a clinical claim on prediction-only evidence, `ART110` means no limitations were stated. |
