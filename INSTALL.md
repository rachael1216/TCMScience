# Install and run

Three ways in, shortest first. Everything here works offline with no API key —
the TCM knowledge layer is a seed corpus compiled into the package.

---

## 1. Try it in 30 seconds, without installing anything

From the repository root, with nothing but Python 3.10+:

```bash
cd BioScience-Harness
python examples/run_skills.py
```

That runs the four P0 skills and prints what each one produced, including its
limitations and whether it passed the publication gate. It finds both trees on
`sys.path` itself, so there is no environment to set up. **If you only do one
thing, do this one.**

You should see five artifacts and a final line:

```
5/5 artifacts passed the publication gate
```

---

## 2. Install it, to use the library or the CLI

Two packages, and the second one is not optional — `bioagent` imports `psh`:

```bash
pip install -e "PSH-Harness[test]"          # the trusted kernel (+ pytest, hypothesis)
pip install -e "BioScience-Harness[dev]"   # the capability plane + the governance layer
```

The extras are what the test suites need. Drop them if you only want to run the
code — but note that PSH's property tests `importorskip("hypothesis")`, so without
`[test]` eleven of its test modules are silently skipped rather than run.

Then:

```bash
# what can this installation run?
python -m bioagent.cli skills --dir BioScience-Harness/skills/tcm

# run one skill
python -m bioagent.cli skill normalize-tcm-entities \
    --arg names=姜,白芍 --dir BioScience-Harness/skills/tcm

# the same thing as JSON, for a script
python -m bioagent.cli skill assess-tcm-safety \
    --arg subject=附子 --json --dir BioScience-Harness/skills/tcm
```

Nothing is installed globally; `-e` means the packages run from this checkout, so
edits take effect immediately.

---

## 3. Use it from Python

```python
from bioagent.skills.p0 import assess_tcm_safety
from bioagent.contracts import validate_artifact

artifact = assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="demo")

print(artifact.composite_version_string)
# runtime=psh-0.5.3+bioagent-0.2.6|skill=assess-tcm-safety@1.0.0|source=1982...|benchmark=unversioned

verdict = validate_artifact(artifact)
print(verdict.publishable, verdict.codes)   # True ()
```

The four entry points are:

| Skill | Call | Ask it |
| --- | --- | --- |
| `normalize-tcm-entities` | `normalize_tcm_entities(names)` | resolve herb/formula/syndrome names |
| `retrieve-tcm-evidence` | `retrieve_tcm_evidence(subject)` | what evidence exists |
| `analyze-tcm-network-pharmacology` | `analyze_tcm_network_pharmacology(formula_name)` | infer a target network |
| `assess-tcm-safety` | `assess_tcm_safety(subject, co_administered=[...])` | recorded safety information |

Each returns a `ResearchArtifact`. See [USAGE.md](USAGE.md) for what is in one and
how to read it.

---

## Running the tests

```bash
cd PSH-Harness          && PYTHONPATH=src python -m pytest -q                    # 958
cd BioScience-Harness   && PYTHONPATH=src:../PSH-Harness/src python -m pytest -q -m unit   # 844
```

`-m unit` selects the tier that needs no network and no data lake; there is an
`integration` tier that does, and it is not run by default.

## The CI checks, runnable locally

```bash
cd BioScience-Harness
python scripts/check_lockfile.py            # the pinned skills match the tree
python scripts/make_release.py --check      # packaging hygiene
cd .. && python scripts/build_arena_data.py # regenerate the Arena's JSON
```

---

## What is deliberately not installed

- **No benchmark cases.** They are withheld (see `.gitignore`), so `benchmarks/cases/`
  holds a README in a clone. The harness, the scorers and the schema are all here.
- **No results.** `arena/web/data/*.json` is generated and gitignored; the
  `*.example.json` files beside it carry placeholder rows so the site renders.
- **No live data connectors for the TCM skills.** They read the seed corpus in
  `bioagent.tcm`. A connector-backed variant would declare hosts in its
  `skill.yaml` and the compiler would derive network authority from them.
