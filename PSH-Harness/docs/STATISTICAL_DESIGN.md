# Declarative statistical design checks

`TaskContract.statistics` optionally contains a `StatisticalDesign`. The scientific
compiler rejects its violations before lowering a plan for execution. Legacy
contracts without this field retain their wire representation and fingerprints.
Plain runtime plans do not run these opt-in checks.

The design reuses `psh.scientist.Protocol`: primary endpoint, statistical test,
sample-size assumptions, exclusions, covariates, subgroup and stopping plans have
one representation. Constructing that protocol already requires nonempty primary
endpoint and other required text. It does not establish preregistration or link
automatically to a persisted ScientificLedger record. For opt-in strict linkage,
see [registered protocol bindings](PROTOCOL_BINDING.md).

```python
from psh.scientist import Protocol
from psh.workflow import StatisticalDesign

protocol = Protocol(
    primary_endpoint="synthetic endpoint", secondary_endpoints=(),
    exclusion_criteria="prespecified exclusions", statistical_test="declared test",
    sample_size_assumptions="declared power assumptions", covariates=(),
    subgroup_plan="no subgroups", stopping_criteria="fixed sample size",
)
design = StatisticalDesign(
    protocol=protocol, comparisons=2,
    multiplicity_plan="prespecified adjustment strategy",
    holdout=True, training_units=("unit-a",), evaluation_units=("unit-b",),
)
# Pass statistics=design to the relevant TaskContract.
assert design.violations() == ()
```

| Code | Rejected declaration |
| --- | --- |
| STAT101 | More than one comparison without a multiplicity plan |
| STAT102 | Holdout without both training and evaluation unit IDs |
| STAT103 | Training/evaluation unit overlap |
| STAT104 | Feature/model selection/evaluation unit overlap |
| STAT105 | Unit partitions supplied without declaring holdout |
| STAT106 | Repeated measures without a dependence plan |
| STAT107 | Time-to-event analysis without a censoring plan |

`comparisons` is the declared number of tests in the inferential family, not an
inferred endpoint count. A multiplicity plan may explain why no adjustment is
appropriate for an exploratory analysis; the compiler only checks its presence,
not the adequacy of the chosen strategy.

For one held-out split, use canonical, case-sensitive opaque IDs at the independent
unit level in a shared namespace. Include all preprocessing, tuning and feature
selection units in `selection_units`. Overlap with training is allowed; overlap
with evaluation is rejected. Duplicate/blank/padded IDs and non-array wire values
are rejected. Never put patient identifiers in these declarations. Diagnostics
do not echo unit IDs; serialized contracts still contain them and need appropriate
storage/access controls and sensitivity declarations.

These checks do not read datasets, resolve aliases, detect undisclosed leakage,
verify actual tool behavior, choose a statistical method, compute power, validate
model assumptions, or certify a scientific claim. Nested cross-validation and
multiple folds are not represented by this single-holdout contract. All design
changes affect task fingerprints and downstream amendment invalidation. Existing
evidence and release gates remain mandatory.

Run `python -m pytest -q tests/test_statistical_design.py` for synthetic tests.
