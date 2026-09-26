# Declarative statistical design checks · 声明式统计设计检查

`TaskContract.statistics` optionally contains a `StatisticalDesign`. The scientific
compiler rejects its violations before lowering a plan for execution. Legacy
contracts without this field retain their wire representation and fingerprints.
Plain runtime plans do not run these opt-in checks.

<!-- zh -->
`TaskContract.statistics` 可选地包含一个 `StatisticalDesign`。科学编译器在为执行而下沉一个计划之前，会拒绝其中违反该设计的地方。没有此字段的遗留契约保持其线上表示与指纹不变。普通的运行时计划不会运行这些需显式开启（opt-in）的检查。


The design reuses `psh.scientist.Protocol`: primary endpoint, statistical test,
sample-size assumptions, exclusions, covariates, subgroup and stopping plans have
one representation. Constructing that protocol already requires nonempty primary
endpoint and other required text. It does not establish preregistration or link
automatically to a persisted ScientificLedger record. For opt-in strict linkage,
see [registered protocol bindings](PROTOCOL_BINDING.md).

<!-- zh -->
该设计复用 `psh.scientist.Protocol`：主要终点、统计检验、样本量假设、排除标准、协变量、亚组与停止计划共用一种表示。构造该方案本身就已要求非空的主要终点与其他必需文本。它不确立预注册，也不自动链接到一条已持久化的 ScientificLedger 记录。关于需显式开启的严格链接，见[已注册方案绑定](PROTOCOL_BINDING.md)。


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

<!-- zh -->
| 代码 | 被拒绝的声明 |
| --- | --- |
| STAT101 | 多于一次比较却没有多重性计划 |
| STAT102 | 声明了留出（holdout）却未同时给出训练与评估单元 ID |
| STAT103 | 训练/评估单元重叠 |
| STAT104 | 特征/模型选择/评估单元重叠 |
| STAT105 | 提供了单元划分却未声明留出 |
| STAT106 | 重复测量却没有依赖性计划 |
| STAT107 | 生存/事件时间分析却没有删失计划 |


`comparisons` is the declared number of tests in the inferential family, not an
inferred endpoint count. A multiplicity plan may explain why no adjustment is
appropriate for an exploratory analysis; the compiler only checks its presence,
not the adequacy of the chosen strategy.

<!-- zh -->
`comparisons` 是所声明的推论族（inferential family）中检验的次数，不是推断出来的终点数量。一份多重性计划可以解释为何对探索性分析而言不校正才是恰当的；编译器只检查它是否存在，而不检查所选策略是否充分。


For one held-out split, use canonical, case-sensitive opaque IDs at the independent
unit level in a shared namespace. Include all preprocessing, tuning and feature
selection units in `selection_units`. Overlap with training is allowed; overlap
with evaluation is rejected. Duplicate/blank/padded IDs and non-array wire values
are rejected. Never put patient identifiers in these declarations. Diagnostics
do not echo unit IDs; serialized contracts still contain them and need appropriate
storage/access controls and sensitivity declarations.

<!-- zh -->
对单次留出划分，请使用在独立单元层面、共享命名空间内规范的、区分大小写的不透明 ID。把所有预处理、调优与特征选择单元都放进 `selection_units`。与训练重叠是允许的；与评估重叠会被拒绝。重复/空白/带填充的 ID 与非数组的线上取值会被拒绝。绝不要在这些声明中放入患者标识符。诊断信息不会回显单元 ID；序列化后的契约仍然包含它们，需要恰当的存储/访问控制与敏感度声明。


These checks do not read datasets, resolve aliases, detect undisclosed leakage,
verify actual tool behavior, choose a statistical method, compute power, validate
model assumptions, or certify a scientific claim. Nested cross-validation and
multiple folds are not represented by this single-holdout contract. All design
changes affect task fingerprints and downstream amendment invalidation. Existing
evidence and release gates remain mandatory.

<!-- zh -->
这些检查不读取数据集、不解析别名、不检测未披露的泄漏、不验证工具的实际行为、不选择统计方法、不计算检验效能（power）、不校验模型假设，也不为科学主张作保。嵌套交叉验证与多折（multiple folds）不由此单留出契约表示。所有设计变更都会影响任务指纹与下游的修正失效判定。现有的证据门与发布门仍然强制。


Run `python -m pytest -q tests/test_statistical_design.py` for synthetic tests.

<!-- zh -->
运行 `python -m pytest -q tests/test_statistical_design.py` 执行合成测试。

