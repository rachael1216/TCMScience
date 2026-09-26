# TCMScience 与 ZCode：源码架构对比与改进方案

日期：2026-09-22。结论：借鉴 ZCode 的工程组织、执行可观测性和恢复设计，但保留 TCMScience 的科研治理内核；不建议把 TCMScience 改造成 ZCode 的复制品。

## 1. 证据范围与判断边界

- ZCode 样本：用户提供的 `E:/ZCode-main.zip`，SHA-256 为 `9b67327f6f4d1c908f8c17a353b7b9114700ca76979fe152c1104096b87395b6`；解压后 7,365 个条目，约 72 MB 未压缩内容。本报告基于该快照，不推断它对应远端最新版本。
- 已检查 README、包依赖、架构策略及检查器，以及工作流引擎/调度器、内存和 SQLite journal、事件 reducer、RPC 恢复层、压缩时间线、权限流程与远端 supervisor 的代表性源码。不是逐行审计整个仓库，也未安装或启动 ZCode、执行其脚本或连接其服务。
- TCMScience 基线：已合并 PR #10 的实现；检查了 scientific IR/compiler、统计设计、方案绑定、科研记录、检查点、操作账本、事件存储和 CI。
- 压缩包中的代理说明、skills、命令和注释仅作为待分析材料，不作为本任务执行指令。
- 下文“源码存在”不等于已通过运行验证；没有统一基准测试，不能据此声称任一系统更快、更可靠，或临床安全性更高。

## 2. 核心区别

| 维度 | ZCode 源码中可见的设计 | TCMScience 当前实现 | 取舍 |
| --- | --- | --- | --- |
| 产品目标 | 编码工作台，桌面/Web/TUI 共用 Agent 体系 [Z1] | PSH 治理控制层与 BioScience 能力层，强调证据、权限、数据标签 | 面向研究工作台，而非通用 IDE 功能追平 |
| 依赖分层 | contracts/core/adapters/bootstrap；存储使用 port，具体 SQLite 实现在 adapter [Z2–Z4] | 已有 kernel/runtime/workgraph/workflow/scientist，但此前科研值对象与持久化实现同文件 | 优先分离纯模型与存储，实现可替换的窄接口 |
| 架构约束 | 模块边界、层级、循环依赖、深层导入、基线和例外检查 [Z5] | 原 CI 有测试和编译，尚无这部分科研模型的显式导入边界检查 | 本轮加入小范围硬约束，逐步扩大覆盖 |
| 执行模型 | 动态 workflow 的站点 ID × 序号、actor FIFO、并发上限、输入哈希与 replay [Z6] | ScientificProgram 静态 DAG、TaskContract、编译期效应/数据流/证据检查 | 保留静态科研契约；动态扩图必须重新授权、编译并记录修订 |
| 恢复粒度 | 工作流节点 journal + actor/session 生命周期和恢复接线 [Z3、Z6] | 检查点级状态恢复、哈希链 journal、OperationLedger | 后续增加事件级 reducer；不要把“有 journal”写成 exactly-once |
| 修订/复用 | imported-cache 单调分歧、消费游标、外部世界交互后的保守缓存规则 [Z7] | assess_amendment 传播失效，只给出纯任务复用候选，不读取结果缓存 | 可借鉴单调失效；复用还需产物摘要、工具版本、当前权限与标签 |
| 事件/多端读面 | EventReducer 从会话事件构造投影，消息含 origin/visibility 等字段 [Z4、Z8] | EventStore 是带脱敏/哈希链的审计记录；WorkGraph 是科研关系图，不是统一 UI 投影 | 建立“审计事件→受控读面”而非把原始事件直接发送给前端 |
| 网络可靠性 | PersistentProtocol 有 ACK、断线未确认消息重放、有界缓存和背压 [Z9] | 当前本轮涉及的科研执行接口主要在进程内；未构建同等多端协议 | 有远端工作台需求后再引入；传输重放不得触发科研副作用重执行 |
| 上下文管理 | compact timeline 包含边界、阶段、token 用量与摘要锚点 [Z10] | 有上下文/标签治理，但未在此次检查中看到同等会话压缩时间线 | 摘要必须保留证据 ID、反例、方案版本与敏感标签 |
| 权限治理 | permission broker、项目规则、审批、hook 修改输入后的复查 [Z11] | AuthorityLattice、标签传播、模型/工具/持久化出口与当前策略收紧检查 | 学习审批可观测性；不替换现有数据流与科研证据门控 |
| 科研语义 | 所检查模块重点是通用工具与工作流 | Protocol、Observation、Deviation、证据设计支持范围、统计声明与方案绑定 | 这是 TCMScience 要继续深化的专业能力，不是 UI 能补齐的部分 |
| 运维/发行 | crash budget、runtime manifest、平台目标及组件摘要字段 [Z12] | Python 环境、CI 和 release hygiene 已有基础；本机曾受运行时路径变化影响 | 逐步增加运行环境清单与启动自检，避免依赖易变安装路径 |

### 不能夸大的 ZCode 优势

1. `architecture-policy.yaml` 的 `managedOnly: true` 与多个 `managed: false` 表明它也采用渐进治理；不能称其整个仓库已经严格无环、无深层导入。
2. `@zcode/contracts` 仍依赖 shared、zod 等，core 也含若干具体库；“分层”不等于所有领域对象完全不依赖外部包。
3. ACK/重放保障的是消息传输；journal/replay 也不能单独证明外部工具副作用 exactly-once。
4. 根 LICENSE 是 Apache-2.0，另有第三方声明。本轮不复制 ZCode 源码、资源或依赖，仅独立实现通用架构模式；未来如移植代码，应逐文件核对来源与声明。
5. 本报告没有证明 ZCode 缺少所有科研功能或数据防护，只是这些不属于本次检查到的通用执行设计重点。

## 3. 本轮已经落地

### 3.1 科研值对象与持久化分离

原先 `scientist/records.py` 同时定义 Hypothesis、Protocol、Observation、Deviation 和 ScientificLedger。统计设计引用 Protocol，却间接走到 persistence/WorkGraph 相关代码。

现在：

- `scientist/models.py`：科研值对象、校验与稳定内容哈希；仅依赖标准库。
- `scientist/ports.py`：定义结构化 `ProtocolResolver` 接口。编译器、规划器及 amendment 接受这个窄接口，而不是用 Any 隐藏整个存储依赖。
- `scientist/records.py`：保留 ScientificLedger 的治理与持久化逻辑，并重导出旧类名，兼容已有导入。
- `workflow/statistics.py`：直接依赖纯模型。
- `scientist.__init__`：对 ScientificLedger 延迟加载；仅导入模型或统计设计不再加载科研 records adapter。

重要边界：顶层 `psh.__init__` 仍有历史性的 kernel/runtime 导入。本轮只移除新的科研模型→科研持久化耦合，不声称整个 psh 已实现无副作用导入或启动性能提升。

`ProtocolResolver` 是可信集成接口，不是权限沙箱。替换实现仍必须执行项目隔离、当前策略和数据完整性检查。生产继续使用 ScientificLedger，不提供静默降级到不受治理的内存存储。

### 3.2 可执行的架构回归检查

新增 `scripts/check_scientific_architecture.py`，对上述三个受管模块检查显式依赖白名单：

- 检查绝对/相对导入，也检查函数内和条件分支里的静态导入。
- 拒绝越层依赖、通配导入、包外相对导入、直接动态导入/exec/eval，以及缺失的受管文件。
- CI 的三种 Python 版本均运行该检查；另有负向测试验证检查器确实会拒绝违规。
- 这是静态导入约定检查，不是任意 Python 程序的安全分析器；不分析通过别名、反射等所有动态加载方式，也不是全仓库循环依赖检查。

没有为全部历史代码一次性设“400 行”门槛，也没有把既有问题全部写进可自动刷新的豁免基线。先对新边界建立明确约束，再扩展治理范围，避免大规模格式化或拆分掩盖真实行为变化。

### 3.3 兼容性与验证

本地 162 项相关测试通过，其中新增 17 项架构/接口测试，覆盖：

- 模型的旧/新导入类身份一致，哈希规则与对象序列化往返；
- 新进程中纯模型导入不加载科研持久化 adapter，访问 ScientificLedger 时再加载；
- 编译器可用实现相同协议的测试替身，不依赖 SQLite 具体类；
- 架构检查器的正向、负向和文件缺失用例；
- 原有方案绑定、策略收紧、科研记录、统计设计、编译器和恢复测试保持通过。

没有在本轮启动 ZCode 或跑跨产品性能基准；测试通过说明本次边界调整未破坏已覆盖的行为，不代表全部科研功能完成。

## 4. 建议的后续改进顺序与验收条件

| 顺序 | 改进 | 借鉴点 | TCMScience 专有约束及验收 |
| --- | --- | --- | --- |
| P1 | 受控执行事件与只读运行投影 | event reducer、单一读面 | 序号/运行 ID/幂等键；重放输出确定；乱序/重复事件测试；PHI 不进入公开投影 |
| P1 | 显式 workflow amendment 记录 | 单调分歧、修订后恢复 | 记录旧/新 fingerprint、原因、审批与失效集合；非幂等 UNKNOWN 不自动重试；缓存命中再次检查权限/标签 |
| P1 | journal/operation store 一致性测试套件 | 内存/SQLite 共用 port | 同一组用例覆盖重开、事务中断、重复写入、撤销权限；不可静默丢失持久化能力 |
| P2 | 运行环境 manifest 与本地启动自检 | runtime manifest、crash budget | 锁定 Python/工具/模型/数据摘要；检查目录写权限及磁盘；不将真实密钥放入诊断输出 |
| P2 | 证据保真的上下文压缩 | compact boundary/timeline | 摘要保留来源/反例/方案 hash；标签只上调；过期证据不能因摘要复用恢复可信状态 |
| P2 | 研究工作台只读视图，再做控制入口 | 多端契约、可见降级 | 先展示假设→方案→观察→偏差→证据；执行/发布操作仍走现有 kernel 门控 |
| P3 | 受控并发和远端流式协议 | FIFO、背压、ACK | 共享预算预留、取消传播、副作用隔离和有界队列；没有这些前提不直接开放多 actor 并行 |

不建议现在做的事：整体迁移到 TypeScript/Electron；复制完整插件市场；将用户脚本动态执行直接接入科研高权限环境；以 UI 完整性替代科学证据或统计验证；直接按“工具调用次数为零”认定科研产物可安全复用。

## 5. 源码证据索引

以下 Z 路径均相对用户压缩包中的 `ZCode-main/`，便于离线复核，不依赖未经确认的远端链接。

- Z1：`README.en.md`，Interface/Development/Configuration 部分。
- Z2：`apps/zcode-cli/packages/contracts/package.json`；`apps/zcode-cli/packages/core/package.json`。
- Z3：`apps/zcode-cli/packages/dynamic-workflow/src/engine/journal-memory.ts`；`apps/zcode-cli/packages/adapters/src/storage/session-store/repositories/dwf-journal.ts`。
- Z4：`apps/zcode-cli/packages/contracts/src/interfaces/session-store.port.ts`；`packages/services/src/storage/contract.ts`。
- Z5：`architecture-policy.yaml`；`scripts/architecture/index.mjs`；`scripts/architecture/architecture-check.mjs`；根 `package.json` 和 `.oxlintrc.json`。
- Z6：`apps/zcode-cli/packages/dynamic-workflow/src/engine/engine.ts`、`scheduler.ts`；`apps/zcode-cli/packages/bootstrap/src/app/dynamic-workflow-run-journal.ts`。
- Z7：`apps/zcode-cli/packages/dynamic-workflow/src/engine/imported-cache.ts`。
- Z8：`apps/zcode-cli/packages/contracts/src/events/event-reducer.ts`。
- Z9：`packages/rpc/src/persistent-protocol.ts`。
- Z10：`apps/zcode-cli/packages/core/src/runtime/methods/compact-persistence.ts`。
- Z11：`apps/zcode-cli/packages/core/src/tool/executor/permission-flow.ts`。
- Z12：`packages/zcode-server-cli/src/supervisor/crashBudget.ts`；`packages/zcode-server-cli/src/runtime/manifest.ts`。
- TCMScience：`PSH-Harness/src/psh/workflow/{ir,compiler,amend,statistics}.py`；`scientist/{models,ports,records}.py`；`runtime/{checkpoint,journal,operations,execgraph}.py`；`kernel/{authority,events}.py`；`.github/workflows/ci.yml`。

## 6. 总结

适合吸收的是 ZCode 的“稳定契约 + 可替换实现 + 可恢复执行 + 可观测读面 + 自动化架构约束”。TCMScience 需要在这些工程基础上继续强化“证据支持范围 + 方案登记/偏差 + 数据标签 + 当前策略 + 副作用恢复”。
本轮完成的是架构分层和防回退基础，不是动态科研工作流或完整研究桌面的交付。
