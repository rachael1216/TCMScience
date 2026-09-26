# Hidden split · 隐藏划分

Not published, by design.

<!-- zh -->
按设计不予发布。

The hidden split is what makes a leaderboard mean anything: a system that has
seen the answers is not being evaluated. `scripts/check_leakage.py` asserts that
no hidden case identifier, prompt or gold answer appears in any committed file,
in any published trace, or in any Arena JSON document.

<!-- zh -->
隐藏划分（hidden split）正是排行榜之所以有意义的原因：一个已经见过答案的系统并没有在接受评测。`scripts/check_leakage.py` 断言：任何隐藏用例的标识符、提示词或黄金答案（gold answer）都不会出现在任何已提交的文件、任何已发布的轨迹或任何 Arena JSON 文档中。

If you are an official evaluator, the split is delivered out of band and its
digest is published, so a reported result can be tied to a specific revision of
the cases without the cases being disclosed.

<!-- zh -->
如果你是官方评测方，该划分会以带外方式交付，而其摘要（digest）会被发布，因此被报告的结果可以关联到用例的某个特定修订版本，而无需披露用例本身。

This directory is ignored by `.gitignore` except for this file.

<!-- zh -->
除本文件外，本目录被 `.gitignore` 忽略。
