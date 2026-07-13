# Goal-Agent 通用一致性检视重构方案

## 目标与边界

Goal-Agent 由运行中的 OpenCode CLI 读取 `INSTRUCTION.md` 后执行。输入是任意代码仓与任意设计/RFC 文档，输出代码相对设计的不一致问题及可追溯证据链。目标代码和 supplied design 全程只读。

公开工程只用于开发侧自验证。开发 oracle 不得进入 `INSTRUCTION.md`、Skill、Agent prompt、运行脚本、fixture、排序规则或 verdict 逻辑。正式链路无人工参数、无 fallback、无 `opencode.json`，并受 6 小时总时限约束。

## 最近一次失败说明了什么

被停止的完整运行在 scout 阶段只召回开发 oracle 的一小部分，最终 finding 仍为零。Provider 和工具当时持续正常，退出是人工 SIGTERM，不是额度或模型中断。

失败同时发生在两个层面：

- 语义层：scout 的范围过大，并在发现线索前被要求完成接近最终调查深度的入口、补偿路径和误报闭环，导致大量合法空结果；
- 运行层：并发 risk merge 和 receipt 使用无锁读改写，真实发生过后写覆盖先写；最后一个 risk merge 又等待尚未写入的 receipt，形成顺序死锁。即使模型发现候选，也可能无法保留或发布。

因此不能用“再跑一次”或放宽最终 gate 解决。需要同时重构探索注意力、候选职责、共享发布和状态机。

## 根因

### 1. 形式覆盖不等于模型实际审阅

旧计划曾把数千至上万行规范或数万文件代码树交给一个 scout。Receipt 只能证明 Agent 声明读完分配 ID，不能证明它真正理解了所有义务。大范围内最容易漏掉时序、遍历、可选副作用、能力缺失和跨入口路由。

### 2. Raw discovery 与 final proof 混在一起

Scout 本应提出值得深查的可证伪线索，却被要求在提交前完成 investigator/critic 的工作。证据尚未闭环时，模型倾向返回 `[]`，后续深查阶段便没有候选可救。

### 3. 导航元数据成为了语义门槛

Architecture map 和 `test_surfaces` 是模型生成的导航信息，不是设计义务。旧逻辑曾用它们裁剪 plane、拒绝候选或阻断 task，地图遗漏会直接变成假阴性。

### 4. Source authority 与生成目录缺少稳定边界

Catalog 只应定位正文，不能成为规范证据。旧 materialization 重跑还可能把上一次遗留文件留在输出目录，使 stale source 被当作 current input。

### 5. 共享发布和全局状态有多个写者

并发 helper 对累计 JSONL 做无锁读改写；多个 helper 和局部 checkpoint 又都能覆盖全局 phase。结果是候选丢失、重复 finding、阶段回退和 trace 与 state 相互矛盾。

## 当前架构

```text
自动输入发现 + 只读快照
        ↓
模型建立轻量 architecture map（只导航）
        ↓
确定性 heading-aware inventory + 输入规模驱动 scout plan
        ↓
┌─────────────────────────┬─────────────────────────┐
│ design-to-code scouts   │ code-to-design scouts  │
│ ≤3500 行、≤2 个文档     │ ≤1200 个文件           │
│ 文档局部连续 section     │ 非重叠真实 code anchors │
│ 可搜索完整代码仓         │ 可检索完整设计 inventory│
└────────────┬────────────┴────────────┬────────────┘
             └── current handoff + receipt ──┘
                            ↓
                   模型选择最多 12 个 IDs
                            ↓
        candidate → claim → task 的确定性 provenance 投影
                            ↓
             fresh spec critic → investigator → evidence critic
                            ↓
             coverage → final judge → provisional report → final gate
```

### 输入与设计来源

- 模型读取当前 catalog/入口文档，选择当前设计正文；helper只验证路径、HTTPS、引用位置和 hash。
- Design-source bundle先在同父目录 staging 中完整构建；成功后整体替换，失败时保留上一份完整 bundle并写 failed manifest，不发布半成品。
- Catalog 的 informational 身份来自 source manifest 的显式 provenance，不再靠目录名猜测；普通用户文档即使位于名为 `catalog` 的目录也不会被错误降级。
- Catalog、architecture map、旧 result 和公开答案都不能作为设计义务证据。

### Inventory 与设计分片

- Inventory 按真实 Markdown/RFC 标题切分，每个 mechanical section最多约300行；标题只用于检索，不判断义务。
- 仅 required/in-scope 正文进入 design ownership；每个 section全局恰好一个 owner。
- Plan不固定项目专用 task 数。它依据当前总行数和文档 chunk 数计算最小初始 frontier，并强制每个 design slice不超过3500行、最多2个文档。
- 大文档可以拆成多个不重叠的连续 chunk；同一 slice内每个 document-local range必须连续。
- Design scout 可搜索完整代码仓，不受 architecture map裁剪。

### 代码分片

- Architecture map保留 owned/imported/adapter/generated/fast/slow、边界、配置和构建入口，但只负责导航。
- Code plan从 architecture 的真实 path scopes出发，移除父子重复 scope，再按实际文件数递归拆分；每个 slice约不超过1200个文件。
- Anchor全局不重叠。Boundary优先交给真正包含 boundary path 的 slice，而不是仅按模型写的 plane link归属。
- `test_surfaces`不能机械删除 plane、ownership或candidate；测试是否只是测试语义由模型判断。

### Scout 与候选职责

Scout 只输出有具体不一致信号的原子线索：设计原子义务、当前适用性、一个真实代码 lead或结构化能力缺失 lead，以及最低限度的反证检查。完整入口可达性、替代实现、配置/注册/构建补偿路径和最终误报排除由 investigator 与 fresh critic完成。

Raw observation允许 `uncertain`，但不允许合规样本、测试缺失、普通代码质量或纯安全猜测。每个 scout最多12个 observation；全局模型再按当前证据强度选择最多12个进入深查。Helper不使用关键词、regex、固定分数或项目专用规则排序。

能力缺失仍需一个真实起点，但该起点可以是相关入口、dispatch、registration、build/config或明确 unsupported 分支；不要求虚构“缺失发生的代码行”。Task会把该 lead冻结为 investigator starting point。

### 发布、并发与状态机

- 语义任务最多两个并发，且必须拥有互斥 section ownership、互斥 code anchors或不同 candidate。
- Semantic subagent只写 candidate 专属 handoff；orchestrator独占 materialize、check、merge、receipt、validator和checkpoint。
- 所有累计 JSONL 使用 `fcntl` sidecar lock；替换使用同目录唯一临时文件、flush/fsync和原子 replace。并发 scout不会再丢更新。
- Risk merge只验证当前 submitted slice，不等待全局 receipt；全局 closure由 receipt/controller/final gate判断，消除最后一个 scout 的顺序死锁。
- Finding merge完成后立即刷新并登记 current task-lifecycle deterministic trace。
- `pipeline_controller.py` 是 phase、pending IDs和唯一 next action的真相源。局部 session event只追加 ledger；handoff、verdict和report helper不再覆盖全局 state。
- Controller显式覆盖 `map_architecture → build_inventory → build_scout_plan` bootstrap，使用稳定 task-plan/lifecycle snapshot，并保留 hard deadline terminal状态。

### Probe 与最终输出

当前无人值守链路没有完整的 dynamic probe执行协议，因此 `selected` 被明确拒绝；investigator只能记录 `not_selected`、`not_suitable` 或 `environment_limited`。这避免生成一个永远无法完成的分支。单点测试以后只能在具备设计 oracle、隔离工作区、baseline和目标路径命中证明时恢复。

Report先作为 provisional文件写入；只有 final gate 验证设计/代码证据、critic、verdict、trace、只读完整性和机器可读输出全部通过后，`/result`才有效。

## 明确不做

- 不把开发 oracle、项目名、协议名、固定路径、symbol或固定 issue数写入运行逻辑；
- 不用 regex、关键词表、固定分数或规则答案代替模型语义审阅；
- 不加第二套 fallback、兼容流程、人工参数或人工等待；
- 不修改目标代码或 supplied design；
- 不因 issue 数不足制造候选，不把测试覆盖缺失冒充 runtime不一致；
- 不通过放松 evidence/final gate掩盖 raw recall问题。

## 验证顺序

1. 运行完整 pytest 与端到端 fixture；
2. 运行 `py_compile`、`git diff --check` 和运行资产硬编码扫描；
3. 用本地公开材料重建 inventory/plan，核对 section唯一 ownership、slice行数/文件数、anchor不重叠和设计/代码双入口机会；
4. 更新 `/result/output.md`，明确区分确定性验证与尚未执行的真实模型全量结果；
5. 只有上述 gate全部通过后，才推送并启动下一次后台 OpenCode 全量验证。

## 验收标准

- `/result/issues.json`、`issues.jsonl`、`00-summary.md`和单 issue报告完整生成；
- 每个 issue包含设计证据、代码证据、路径行号、原因、误报排除和置信度；
- 开发 oracle仅用于运行结束后的外部对照，运行资产没有专用逻辑；
- 公共工程的开发目标是完整召回，比赛最低指标仍为最终识别不少于4项；
- 误报率不高于50%，总时长不超过6小时；
- 不同语言、代码结构和设计文档形式仍走同一模型驱动流程。
