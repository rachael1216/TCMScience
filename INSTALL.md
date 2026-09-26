# Install and run · 安装与运行

Three ways in, shortest first. Everything here works offline with no API key —
the TCM knowledge layer is a seed corpus compiled into the package.

<!-- zh -->
三种进入方式，从最短的开始。这里的一切都能离线跑，不需要 API key —— TCM 知识层是编译进包里的种子语料库。

---

## 1. Try it in 30 seconds, without installing anything · 1. 30 秒试一下，什么都不用装

From the repository root, with nothing but **Python 3.11+**:

<!-- zh -->
在仓库根目录下，只需要 **Python 3.11+**：

```bash
cd BioScience-Harness
python examples/run_skills.py
```

That runs the four P0 skills and prints what each one produced, including its
limitations and whether it passed the publication gate. It finds both trees on
`sys.path` itself, so there is no environment to set up. **If you only do one
thing, do this one.**

<!-- zh -->
这会跑起四个 P0 技能，打印出每个技能产出了什么 —— 包括它的局限，以及它有没有通过发布门。它会自己在 `sys.path` 上找到两棵源码树，所以不需要配置任何环境。**如果只做一件事，就做这一件。**

You should see five artifacts and a final line:

<!-- zh -->
你应该会看到五个科研产物（research artifact, RA），以及最后一行：

```
5/5 artifacts passed the publication gate
```

---

## 2. Install it, to use the library or the CLI · 2. 安装它，以便使用库或 CLI

Two packages, and the second one is not optional — `bioagent` imports `psh`:

<!-- zh -->
两个包，第二个不是可选的 —— `bioagent` 会 import `psh`：

```bash
pip install -e "PSH-Harness[test]"          # the trusted kernel (+ pytest, hypothesis)
pip install -e "BioScience-Harness[dev]"   # the capability plane + the governance layer
```

The extras are what the test suites need. Drop them if you only want to run the
code — but note that PSH's property tests `importorskip("hypothesis")`, so without
`[test]` eleven of its test modules are silently skipped rather than run.

<!-- zh -->
这些 extras 是测试套件需要的东西。如果你只想跑代码，可以把它们去掉 —— 但注意，PSH 的性质测试会 `importorskip("hypothesis")`，所以不加 `[test]`，它的十一个测试模块会被静默跳过，而不是真的运行。

Then:

<!-- zh -->
然后：

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

<!-- zh -->
没有任何东西装到全局；`-e` 意味着这些包直接从当前这份 checkout 运行，所以改动立刻生效。

---

## 3. Use it from Python · 3. 在 Python 里使用

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

<!-- zh -->
四个入口是：

| Skill | Call | Ask it |
| --- | --- | --- |
| `normalize-tcm-entities` | `normalize_tcm_entities(names)` | resolve herb/formula/syndrome names |
| `retrieve-tcm-evidence` | `retrieve_tcm_evidence(subject)` | what evidence exists |
| `analyze-tcm-network-pharmacology` | `analyze_tcm_network_pharmacology(formula_name)` | infer a target network |
| `assess-tcm-safety` | `assess_tcm_safety(subject, co_administered=[...])` | recorded safety information |

<!-- zh -->
| Skill 技能 | Call 调用 | Ask it 用途 |
| --- | --- | --- |
| `normalize-tcm-entities` | `normalize_tcm_entities(names)` | 解析药材／方剂／证候名称 |
| `retrieve-tcm-evidence` | `retrieve_tcm_evidence(subject)` | 存在哪些证据 |
| `analyze-tcm-network-pharmacology` | `analyze_tcm_network_pharmacology(formula_name)` | 推断靶点网络 |
| `assess-tcm-safety` | `assess_tcm_safety(subject, co_administered=[...])` | 记录在案的安全性信息 |

Each returns a `ResearchArtifact`. See [USAGE.md](USAGE.md) for what is in one and
how to read it.

<!-- zh -->
每个都返回一个 `ResearchArtifact`。它里面有什么、该怎么读，见 [USAGE.md](USAGE.md)。

---

## Running the tests · 运行测试

```bash
cd PSH-Harness          && PYTHONPATH=src python -m pytest -q                    # 958
cd BioScience-Harness   && PYTHONPATH=src:../PSH-Harness/src python -m pytest -q -m unit   # 844
```

`-m unit` selects the tier that needs no network and no data lake; there is an
`integration` tier that does, and it is not run by default.

<!-- zh -->
`-m unit` 选中的是既不需要网络、也不需要数据湖的那一层；另有一个 `integration` 层需要它们，默认不跑。

## The CI checks, runnable locally · CI 检查，可在本地运行

```bash
cd BioScience-Harness
python scripts/check_lockfile.py            # the pinned skills match the tree
python scripts/make_release.py --check      # packaging hygiene
cd .. && python scripts/build_arena_data.py # regenerate the Arena's JSON
```

---

## What is deliberately not installed · 刻意不安装的内容

- **No benchmark cases.** They are withheld (see `.gitignore`), so `benchmarks/cases/`
  holds a README in a clone. The harness, the scorers and the schema are all here.
- **No results.** `arena/web/data/*.json` is generated and gitignored; the
  `*.example.json` files beside it carry placeholder rows so the site renders.
- **No live data connectors for the TCM skills.** They read the seed corpus in
  `bioagent.tcm`. A connector-backed variant would declare hosts in its
  `skill.yaml` and the compiler would derive network authority from them.

<!-- zh -->
- **不含基准用例。** 它们被刻意扣留（见 `.gitignore`），所以 clone 出来的仓库里 `benchmarks/cases/` 只有一份 README。评测框架、打分器和 schema 都在这里。
- **不含结果。** `arena/web/data/*.json` 是生成的，且已被 gitignore；紧挨着它的 `*.example.json` 文件里是占位行，好让站点能渲染出来。
- **TCM 技能没有实时数据连接器。** 它们读的是 `bioagent.tcm` 里的种子语料库。若换成连接器驱动的版本，它会在自己的 `skill.yaml` 里声明 hosts，编译器再据此推导出网络权限。
