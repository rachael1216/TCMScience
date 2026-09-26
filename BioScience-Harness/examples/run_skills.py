#!/usr/bin/env python3
"""Run the four P0 skills end to end and print what each one decided.

    python examples/run_skills.py

Needs no network, no API key and no configuration: the TCM knowledge layer is a
seed corpus compiled into the package, so this is the shortest path from a fresh
clone to a working artifact.

This is also the best way to see the design in one sitting. Each skill is asked
for something it *could* overstate, and the output shows it declining:

* an ambiguous herb name, where it returns both candidates rather than picking;
* a formula whose network is entirely predicted, where the artifact says so and
  the claim stays a mechanism hypothesis;
* a herb combination that is an 十八反 incompatibility, where it reports the pair
  without calling it a clinical safety signal;
* a substance with no record at all, where it answers `unknown` rather than safe.

Every artifact is put through `validate_artifact` before it is printed, so the
`validation:` line is the real gate, not a summary written for this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `bioagent` and the sibling `psh` importable when run from a checkout
# without installing anything. `pip install -e .` makes this unnecessary.
_ROOT = Path(__file__).resolve().parents[1]
for _entry in (_ROOT / "src", _ROOT.parent / "PSH-Harness" / "src"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from bioagent.contracts import validate_artifact          # noqa: E402
from bioagent.skills.p0 import (analyze_tcm_network_pharmacology,  # noqa: E402
                                assess_tcm_safety, normalize_tcm_entities,
                                retrieve_tcm_evidence)


def report(title: str, question: str, artifact) -> bool:
    """Print one artifact and return whether the gate let it through."""
    verdict = validate_artifact(artifact)
    status = "publishable" if verdict.publishable else "REFUSED"
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")
    print(f"  asked:      {question}")
    print(f"  artifact:   {artifact.skill_id}@{artifact.skill_version}")
    print(f"  versions:   {artifact.composite_version_string}")
    print(f"  produced:   {len(artifact.sources)} source(s), "
          f"{len(artifact.evidence)} evidence item(s), "
          f"{len(artifact.claims)} claim(s), {len(artifact.outputs)} output file(s)")
    print(f"  validation: {status}")
    for violation in verdict.violations:
        print(f"      {violation}")

    if artifact.claims:
        print("  claims:")
        for claim in artifact.claims:
            print(f"      [{claim.claim_kind}] {claim.text}")
            print(f"        confidence {claim.confidence} — {claim.confidence_basis}")
    else:
        print("  claims:     none (the run produced an empty result, which is a "
              "legitimate answer)")

    print(f"  limitations ({len(artifact.limitations)}):")
    for limit in artifact.limitations:
        print(f"      - {limit}")
    return verdict.publishable


def main() -> int:
    results = []

    # 1. An ambiguous name. 「姜」 is 生姜 or 干姜 — different drugs.
    results.append(report(
        "1. Ambiguity is preserved, not resolved",
        "resolve 姜 and 白芍",
        normalize_tcm_entities(["姜", "白芍"], run_id="example")))

    # 2. A network whose edges are all predicted.
    results.append(report(
        "2. Prediction is kept apart from measurement",
        "what target network is inferred for 桂枝汤?",
        analyze_tcm_network_pharmacology("桂枝汤", run_id="example")))

    # 3. An 十八反 pair.
    results.append(report(
        "3. An incompatibility is reported as a pair",
        "what is recorded for 甘草 together with 甘遂?",
        assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="example")))

    # 4. Nothing on record — the answer that must not be "safe".
    results.append(report(
        "4. Absence of a record is not evidence of safety",
        "what is recorded for 不存在的药?",
        assess_tcm_safety("不存在的药", run_id="example")))

    # 5. Evidence retrieval, which reports rather than adjudicates.
    results.append(report(
        "5. Evidence is reported, not adjudicated",
        "what evidence exists about 附子?",
        retrieve_tcm_evidence("附子", run_id="example")))

    passed = sum(results)
    print(f"\n{'═' * 78}")
    print(f"{passed}/{len(results)} artifacts passed the publication gate")
    if passed != len(results):
        print("  (a refusal is a result too — see the violation codes above)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
