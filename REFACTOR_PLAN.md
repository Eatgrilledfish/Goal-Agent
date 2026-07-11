# Goal-Agent 设计与实现一致性检查流程重构方案

## 1. 文档目的

本文档定义 Goal-Agent 的下一阶段重构方案。目标是在不改变比赛入口、不引入项目特例、不降低证据标准的前提下，将当前“全量规范结构化后再调查”的串行流程，调整为“轻量索引、增量候选、候选级验证”的模型驱动流程。

本文档是实现和验收依据，不是比赛运行入口。比赛入口仍为根目录 `INSTRUCTION.md`，运行时仍由 OpenCode CLI 读取指令并执行。

## 2. 重构结论

需要重构，但不重写整个系统，也不替换 OpenCode CLI。

保留：

- OpenCode CLI 驱动的模型 loop。
- 主 Agent 编排和专业 Subagent 分工。
- 目标代码仓只读。
- 设计证据、代码证据和反证组成的证据链。
- 独立 evidence critic 与 final judge。
- sessions、handoffs、tracing、digest、no-progress 和最终 gate。
- `/result` 中的机器可读输出和单 issue 报告。

重构：

- 全量设计 claim 生成与全局门禁。
- quote、行号和 Schema 的职责划分。
- claim review 的快照绑定粒度。
- frontier 的候选粒度。
- coverage、critic 和 judge 的执行时机。
- 单点测试在证据链中的位置。
- 本地调试必须全量运行的问题。

不做：

- 不增加 F-Stack、RFC 编号、协议名、文件名或已知答案检测规则。
- 不使用 regex、关键词表、固定 domain map 或数值评分代替语义判断。
- 不新增人工参数、人工审批等待或运行时 fallback。
- 不增加 `opencode.json` 或比赛环境专属 provider 配置。
- 不为了兼容旧流程同时维护两套运行链路。
- 不修改被审阅的目标代码仓。

## 3. 当前基线与根因

本次全量运行截至当前的可观测数据中：

- 风险探索并发阶段墙钟时间约 29 分钟。
- 设计规范提取和修复约 53 分钟。
- 首次设计修复后的 93 个错误中，72 个是 quote 与声明行范围不一致，21 个是 coverage 结构错误。
- 21 个 coverage 错误中，19 个来自“catalog 中出现文档来源，就强制生成 `declared_capability` claim”。
- 完整索引最终生成 104 个 claims；实际调查任务只引用了其中很少一部分。
- 4 个 Investigator 的两批并发墙钟时间合计约 15 分钟。
- 后续仍因 claim group completeness、whole-file digest 和 coverage gate 触发多轮 critic/repair。

根因不是代码调查过慢，而是以下四类流程问题。

### 3.1 全量规范完美化成为前置阻塞

系统在知道哪些设计义务与代码风险真正相关之前，就要求所有适用文档组生成完整、原子、可测试、带精确引文的 claims。大部分 claims 在本次运行中不会被调查，但仍消耗生成、修复和验证预算。

### 3.2 模型承担了字节级证据搬运

模型同时填写路径、行号和原文 quote。三者只要有一个细微偏差，就会触发 repair Task。字节级复制属于确定性工作，不应由模型反复完成。

### 3.3 局部语义与全局完整性耦合

一个当前 claim 即使已经具备可靠 entailment、normative strength、atomicity 和 applicability，只要同文档组的其他 role、branch 或 behavior family 存在 gap，整个 claim review 仍会返回 repair。

### 3.4 全文件 digest 放大局部变化

增加或修复一个尚未使用的 claim 会改变整个 `design_claims.jsonl` digest，使已接受 review、scope、task gate 和后续模板失效，无法真正增量推进。

## 4. 设计原则

### 4.1 模型负责语义，工具负责不变量

模型负责：

- 设计的适用范围和规范语义。
- 代码的实际可达行为。
- 设计与代码之间是否存在矛盾或能力缺失。
- 候选选择、反证检查和置信度判断。

确定性工具负责：

- JSON/JSONL Schema。
- 路径和行号存在性。
- 从源文件物化 quote、section 和 source hash。
- ID、digest、session 和 handoff 绑定。
- 并发写隔离、去重和原子 merge。
- 目标代码仓未被修改。
- 最终输出文件完整性。

工具不得补写 `behavior`、`applicability`、`expected_behavior`、`actual_behavior`、`contradiction_reason` 等语义字段。

### 4.2 渐进披露

Agent 首先读取轻量地图，随后只按当前任务加载相关设计片段、代码路径和证据。不得向每个 Agent 注入全部 claims、全部日志和全部历史 handoff。

### 4.3 候选级隔离

一个候选的失败、歧义或格式问题只影响该候选。已经通过的 claim、task 和 finding 不因无关候选变化而失效。

### 4.4 广度并发，深度顺序

- 可以并发：互不重叠的文档章节探索、代码执行平面探索、不同候选的独立调查。
- 不应并发：同一候选的 Investigator 与 critic、同一因果链的多个重复调查、共享同一输出文件的任务。
- provider 并发上限保持 2；是否启动下一批由模型根据当前 frontier 和剩余时间决定。

### 4.5 `Unknown` 是合法内部状态

证据不足、设计歧义、环境不可运行或无法证明目标路径触达时，候选进入 `unknown`/`inconclusive`，不强制修成 confirmed，也不写入最终 issue 列表。

### 4.6 最终质量优先于中间形式

最终 issue 必须通过严格证据 gate。中间产物只保留支持可靠 handoff、恢复和审计所需的最小约束，不为统一格式阻塞已具备证据的其他候选。

## 5. 目标流程

```text
输入发现与只读快照
          ↓
文档轻量 inventory + 代码 architecture map
          ↓
  ┌───────┴────────┐
  │                │
设计义务探索      代码风险探索
按文档章节分区    按边界/执行平面分区
  │                │
  └───────┬────────┘
          ↓
增量 evidence-pair frontier
          ↓
候选级设计 claim 物化与独立 spec review
          ↓
原子 Investigator
          ↓
必要时执行单点 probe/test
          ↓
独立 evidence critic
          ↓
confirm / reject / unknown
          ↓
一次 coverage 补扫
          ↓
final judge、去重、报告和最终 gate
```

### 5.1 输入发现与快照

保持零参数自动发现：

- 比赛路径：`/app/code/judge-assets/01_03_ai_implementation_design_difference_detection/`
- 本地路径仅通过外部软链模拟比赛路径，不能写入比赛运行逻辑。
- `code/` 为目标代码根。
- `Difference/` 及其中 catalog/benchmark 指向设计资料。

运行开始时记录目标仓文件摘要；结束时再次校验，任何目标代码变化都使最终 gate 失败。

### 5.2 轻量设计 inventory

全量设计阶段只生成文档地图，不生成完整调查队列。每个文档组至少记录：

```json
{
  "document_key": "稳定文档组 ID",
  "members": ["设计根内相对路径"],
  "scope_relation": "required|in_scope|relevant|informational|superseded|ambiguous",
  "scope_evidence": {
    "path": "catalog 或设计文档路径",
    "line_start": 1,
    "line_end": 3
  },
  "sections": [
    {
      "section_id": "稳定章节 ID",
      "path": "设计根内相对路径",
      "heading": "章节标题",
      "line_start": 10,
      "line_end": 80,
      "behavior_families": ["模型根据设计语义归纳的行为簇"],
      "ambiguities": []
    }
  ]
}
```

`scope_relation` 由设计 Agent 根据 catalog 语义判断。确定性 validator 只检查来源是否真实，不能因为 catalog 中存在链接就自动推导产品能力承诺。

### 5.3 代码风险探索

保留当前 architecture map 和互斥 risk slices，但 risk observation 只承担：

- 记录代码真实可达行为、限制、状态变化和边界分派。
- 给出设计可以回答的检索问题。
- 标明对应 architecture boundary、execution plane 和 parallel path。
- 给出精确代码证据和已检查的替代路径。

Risk Agent 不读取设计，不产生 verdict。

### 5.4 设计义务探索

设计 Agent 按互斥文档组/章节读取设计，只提取规范性且可证伪的最小 rule IR：

```json
{
  "source_ref": {
    "path": "设计根内相对路径",
    "line_start": 1,
    "line_end": 3,
    "source_sha256": "由工具计算"
  },
  "subject": "义务主体",
  "trigger": "触发条件",
  "obligation": "必须、应该、允许或声明支持的行为",
  "exceptions": ["设计中明确的例外"],
  "observable_result": "可观察结果",
  "normative_strength": "mandatory|recommended|optional|declared_capability|informational",
  "ambiguities": []
}
```

设计 Agent 不读代码，不根据代码现状改写规范。

### 5.5 Evidence-pair frontier

Manager 将代码风险与设计义务增量配对。一个 frontier item 必须满足：

```text
一个 risk observation
        ↕
一个设计义务的一个独立分支
        ↕
一个 boundary / 明确 execution planes
        ↕
一个可证伪假设
```

候选选择由模型完成，依据包括：

- 设计适用性的正面证据。
- 规范强度。
- 代码风险是否具体且可达。
- 是否存在外部可观察结果。
- 是否已经检查替代实现、配置和并行路径。
- 是否属于能力、构建、注册或入口对账缺口。
- 继续调查预计能够增加什么信息。

不得使用固定分数、协议关键词、F-Stack 文件名或公开答案排序。

### 5.6 Claim 按需物化

只有进入 frontier 的义务才生成完整 claim。初始 inventory 中不为所有条目生成详细 `probe_oracle`。

模型填写 `source_ref` 和语义字段；确定性 materializer 从源文件生成：

- exact quote。
- section heading。
- source hash。
- canonical path。

Spec Agent 在同一 Task 内执行结构 self-check 并修正 Schema、路径和行号问题。随后由 fresh spec critic 独立判断：

- quote 是否蕴含该行为。
- normative strength 是否正确。
- 是否为原子义务。
- 对当前项目是否适用。
- 是否存在足以阻止调查的歧义。

结构错误不需要 fresh semantic repair；语义 repair 最多一次。

### 5.7 原子 Investigator

每个 task 只检查一个 claim 的一个行为分支。不得将整个协议域、多个状态分支或多个独立义务打包进同一 task。

Investigator 必须：

- 从真实入口、调用链、配置和构建关系证明实际行为。
- 检查同功能的平行实现路径。
- 检查 dead code、条件编译、feature flag 和 adapter。
- 给出至少两项候选特定的误报排除。
- 输出 `contradiction_supported|design_satisfied|uncertain`。
- 选择 `probe_selected|probe_not_needed|probe_not_suitable|environment_limited`。

### 5.8 候选级 critic

Investigator 完成后立即启动对应 evidence critic，不再等待全局 coverage closed。

Critic 只读取该候选的设计片段、相关代码证据、finding 和可选 probe，不读取已知答案或其他候选结论。它必须尝试推翻候选，并返回：

- `confirm_contradiction`
- `reject_issue`
- `needs_more_evidence`

同一 finding、同一 evidence 只允许一次 critic。只有 Investigator 或 probe 产生新证据后才能复审。

### 5.9 Coverage 的新职责

Coverage 不再审批每个候选，也不阻塞已经确认的候选。它只回答：

- 哪些 in-scope 文档组没有被探索。
- 哪些高风险 architecture boundary 或 parallel plane 没有证据。
- 哪些通用设计语义维度缺少有效候选。
- 是否存在被 risk observation 指向、但尚未解析的设计问题。
- 是否值得在剩余预算中补充下一批候选。

默认最多执行一次覆盖补扫。只有出现新的、具体的证据缺口，模型才创建下一批 task；不得为了候选数量或标签完整性创建任务。

### 5.10 Final judge 与结果

Final judge 对所有 finding 产生一个最终状态：

- `confirmed`
- `probable`
- `rejected`

只有满足以下条件的 `confirmed` 写入 `/result/issues.json`、`issues.jsonl` 和 Markdown 报告：

- 设计证据可验证且适用。
- 代码证据可验证且证明真实可达行为或能力缺失。
- 二者之间有明确矛盾解释。
- false-positive checks 充分。
- Investigator 与 evidence critic 结论闭环。
- session、claim、task、finding、critic 和 verdict 身份一致。

最终 issue 字段继续满足比赛要求：

- 不一致描述。
- RFC/设计证据。
- 代码实现证据。
- 代码文件和行号。
- 设计文档章节位置。
- 不一致原因。
- 误报排除说明。
- 置信度。

## 6. 门禁重新划分

| 阶段 | 硬门禁 | 语义判断者 | 失败影响范围 |
|---|---|---|---|
| 输入/快照 | 路径、只读根、文件摘要 | Orchestrator 确认范围 | 整个 session |
| Inventory | 文档成员、来源位置、section 范围 | Design Agent 判断 scope | 当前文档 slice |
| Risk | slice ownership、代码证据、plan digest | Risk Agent 描述风险 | 当前 risk slice |
| Claim | Schema、source ref、materialized quote | Spec critic 判断语义 | 当前 claim |
| Task | 身份、原子分支、boundary/plane 合法性 | Frontier planner 选择 | 当前 candidate |
| Finding | 路径、行号、证据和 handoff 完整性 | Investigator 判断实际行为 | 当前 finding |
| Probe | 命令、隔离目录、目标路径触达 | Probe Agent 解释结果 | 当前 finding |
| Critic | finding/claim/evidence 绑定 | Evidence critic 反证 | 当前 finding |
| Final | 全部 digest、证据、结果文件、目标仓未变 | Final judge 裁决 | 当前 issue 或最终 gate |

以下内容改为软门禁或 coverage 信号：

- 文档组是否已经枚举全部行为分支。
- 当前 session 是否调查全部 design index。
- 某个未使用 claim 是否包含详细 probe oracle。
- 同组其他 claim 是否完整。
- 未入 frontier 的低价值观察是否被调查。

## 7. Digest 与增量审查

### 7.1 Per-claim digest

每个 claim review 绑定：

- `claim_sha256`
- `source_sha256`
- `spec_critic_prompt_version`
- `session_id`

新增无关 claim 不得使已接受 claim review 失效。

### 7.2 Per-group digest

文档组 coverage review 单独绑定 `group_sha256`。Group gap 写入 coverage audit，不进入当前 claim 的 accept 条件，除非 gap 会改变该 claim 的适用性、原子性或规范含义。

### 7.3 Task plan 与 lifecycle 分离

- Task plan validation 验证冻结问题、claim、boundary、plane 和顺序。
- Task lifecycle validation 验证 pending/in-progress/complete/deferred 状态及 finding 关联。
- 新 finding 只刷新 lifecycle，不使稳定 task plan 过期。
- Final gate 同时要求二者有效。

### 7.4 全文件 digest

完整 JSON/JSONL digest 继续记录在 trace 和 final gate 中，用于产物完整性与审计，但不用于使所有局部 review 级联失效。

## 8. 单点测试与动态 Probe

### 8.1 定位

单点测试是候选级证据增强器，不是全局前置条件，也不替代静态证据。

适合：

- 明确输入输出行为。
- 数量、边界和重复元素处理。
- 状态转换。
- 路由、分派和可观察副作用。
- 链式、嵌套和扩展元素处理。
- 已有可复用测试框架的模块。

不适合或可能受限：

- 完全缺失的能力。
- 无法可靠构建的仓库。
- 依赖真实网络、硬件或外部服务的路径。
- 无法证明测试触达目标实现的情形。
- 设计本身存在歧义的 oracle。

### 8.2 执行规则

- 只在临时副本或隔离构建目录运行。
- 不修改目标代码仓。
- 每个候选只生成最小测试或 probe。
- 必须记录构建命令、运行命令、退出状态和输出。
- 必须证明目标实现路径被触达。
- baseline、依赖或环境失败时结果为 `inconclusive`。
- 测试通过只能反驳当前候选，不能证明整个能力完全符合设计。
- 测试失败必须回溯到明确设计义务和实际执行路径。

### 8.3 双重 oracle

对于适合的候选，采用 CASCADE 类双重检查：

1. 从设计义务生成测试。
2. 验证测试本身不是恒真或恒假。
3. 目标实现未通过测试。
4. 一个设计导出的参考模型、最小参考实现、已知正确路径或负向控制通过同一测试。
5. Evidence critic 独立检查测试 oracle 和目标路径触达。

无法建立可靠第二 oracle 时，probe 只能作为辅助证据。

## 9. Agent、Handoff 与并发契约

### 9.1 角色

保留现有角色，避免继续拆分：

- Orchestrator/Manager
- Risk Explorer
- Spec Analyst
- Spec Critic
- Code Investigator
- Coverage Critic
- Evidence Critic
- Final Judge

不为 Schema 修复、quote 修复、去重或报告格式新增模型角色。

### 9.2 所有权

- 每个并行 Agent 只写一个独立 handoff 文件。
- 并行 Agent 不追加共享 JSONL。
- Manager 只做确定性 merge，不补写语义。
- 每个 slice、claim、task、finding 和 critic 有稳定 ID。
- 同一 artifact 的修复最多一次 fresh retry。

### 9.3 并发

- 最大并发数：2。
- Risk Explorer 的 architecture scope 必须互斥。
- Design Explorer 的 document/section scope 必须互斥。
- 不并发运行同一候选的 Investigator 与 critic。
- 不为同一证据启动多个投票式 critic。
- 一批完成后立即合并，不等待无完成事件的旧 session。

## 10. Sessions、Tracing 与 Approval Flow

### 10.1 Session

每次比赛运行创建唯一 session，所有 artifact 绑定 session ID。恢复只从当前 session 的 ledger 和有效 checkpoint 继续，不复用其他 session 的语义产物。

### 10.2 Trace

每个 Agent/工具阶段至少记录：

- role、artifact、scope 和输入 digest。
- started_at、ended_at、wall time。
- provider attempt/session ID。
- 输出对象数量。
- validation 错误分类和数量。
- repair 次数。
- terminal outcome 与 stop reason。

错误应按错误码聚合，例如：

```text
QUOTE_RANGE_MISMATCH: 72 claims
CAPABILITY_SCOPE_UNPROVEN: 19 document groups
CLAIM_GROUP_GAP: 1 group
```

Trace 保留完整 ID 列表和少量样本，避免主 Agent只看到数百条重复字符串。

### 10.3 Approval Flow

比赛全自动运行，不产生人工等待：

- 读取目标仓、搜索、静态分析和在隔离目录运行测试：按策略自动允许。
- 写 `/logs`、session state 和 `/result`：允许。
- 修改目标仓或写出授权范围：机械拒绝。
- 网络访问、外部依赖或高成本 probe：只有在环境已允许且剩余预算足够时由模型选择；不能等待人工批准。

## 11. 本地调试与 Stage Replay

禁止将“每次修改后全量跑 3–6 小时”作为主要调试方式。

使用现有 `work/tools/scripts/stage_replay.py` 建立以下回放层：

1. `inventory`：文档分组、scope relation 和 section map。
2. `claims`：source ref、quote materialization、Schema 和原子 claim。
3. `claim-review`：单 claim entailment、strength、atomicity、applicability。
4. `plan`：evidence-pair frontier 与原子 task。
5. `investigator`：单个 task 的代码调查和反证。
6. `critic`：单个 finding 的独立裁决。
7. `coverage`：未覆盖范围与补扫决策。
8. `gate`：最终结果绑定和目标仓只读校验。

每次重构先运行对应 stage replay；相关单元测试和 replay 通过后，才启动一次完整 OpenCode 验证。

比赛运行仍然只有一个零参数全自动入口。Stage replay 是开发验证能力，不是比赛 fallback。

## 12. Eval 方案

### 12.1 数据集隔离

已知 F-Stack 6 个 issue 只存在于开发侧隐藏 oracle，不得出现在：

- `INSTRUCTION.md`
- `work/skill/SKILL.md`
- Subagent prompt
- runtime fixture
- 关键词、路径、RFC 或 symbol 检测逻辑
- frontier 排序规则

### 12.2 Eval 组成

- F-Stack 隐藏正例。
- 非 F-Stack、不同语言和不同文档形式的正例。
- 行为偏差、能力缺失、边界错误、状态错误和并行路径差异。
- 困难负例：可选行为、等价实现、条件编译、死代码、文档歧义和不适用规范。
- 证据破坏例：相似但无关代码、错误章节、只有名称匹配、缺少可达调用链。

### 12.3 指标

- issue-level recall。
- issue-level precision / false-positive rate。
- 设计引文可验证率。
- 代码位置可验证率。
- 因果链完整率。
- 候选到 confirmed 的转化率。
- 首个 confirmed issue 用时。
- 每阶段 wall time、token、tool call 和 retry。
- 同一输入多次运行的稳定性。
- 目标代码仓只读校验通过率。

不能只计算最终 issue 数量；需要区分候选发现失败、设计解析失败、代码调查失败、critic 拒绝和基础设施失败。

## 13. 实施计划

### P0：消除当前主要耗时

#### P0-1 Schema-first、quote materialization 与 Task 内 self-check

修改：

- `work/skills/spec-analyst.md`
- `INSTRUCTION.md`
- `work/skill/SKILL.md`
- `work/tools/scripts/design_artifact_validator.py`
- 相关 tests/fixtures

完成条件：

- Spec Agent 获得自包含 Schema 和 self-check 命令。
- 模型不再复制 exact quote。
- quote 从 source ref 确定性物化。
- 结构错误在原 Task 内修复。
- 主 Agent不补写语义。

#### P0-2 轻量 inventory 与按需 claim

修改：

- Spec Analyst 输入输出契约。
- Design validator 的全量 claim 要求。
- Catalog scope relation 语义。
- Coverage 对未物化义务的处理。

完成条件：

- 全量阶段不再为每个设计行为生成详细 claim/oracle。
- Catalog 链接不自动等于能力承诺。
- 风险命中和明确 capability scope 可以增量生成 claim。
- 能力缺失仍是一等候选类型。

#### P0-3 原子 evidence-pair frontier

修改：

- `INSTRUCTION.md`
- `work/skills/orchestrator.md`
- task schema/validator
- handoff template

完成条件：

- 每个 task 只绑定一个 claim branch 和一个假设。
- 宽泛协议域任务被 validator 拒绝或由 planner 拆分。
- 任务选择保持模型驱动，不引入数值评分和项目特例。

#### P0-4 Per-claim review 与 non-blocking group gap

修改：

- `claim_review_validator.py`
- task gate 和 final gate 的 review 绑定。
- fixtures 和 stage replay。

完成条件：

- 无关 claim 变化不使旧 review 失效。
- Group gap 进入 coverage，不阻塞已接受 claim。
- 一个 claim repair 不阻塞同批其他已接受候选。

### P1：改进证据闭环和恢复效率

#### P1-1 早期 evidence critic

- 每个 finding 完成后立即 critic。
- Coverage 不再是 critic 的全局前置条件。
- Final judge 仍在最终阶段统一裁决。

#### P1-2 候选级动态 probe

- 先支持已有测试框架、低成本、可证明路径触达的候选。
- 结果区分 supported、refuted 和 inconclusive。
- 不自动尝试全仓构建或全量测试。

#### P1-3 Task plan/lifecycle gate 分离

- Finding merge 不再使稳定 task plan validation 过期。
- Batch template 只依赖 task plan 和当前 lifecycle 状态。

#### P1-4 Stage replay 完整化

- 每个主要角色至少一个正例、负例和损坏 artifact fixture。
- 全量运行只在阶段回放通过后执行。

### P2：后续维护性优化，非下一轮全量运行前置

- 用单一 canonical schema 生成角色文档、validator 和 fixture，减少三套契约漂移。
- Final gate 复用已验证 report，避免重复实现同一 coverage 逻辑。
- 建立 trace 汇总工具，按阶段显示耗时、错误类别和重试。
- 根据跨项目 eval 结果决定是否需要进一步调整 Agent 数量。

## 14. 时间预算目标

以下是重构后的工程目标，不是无验证保证：

- 输入与地图：15–25 分钟。
- 广度探索：30–45 分钟。
- 首批候选级 claim、Investigator 和 critic：45–75 分钟。
- 首个 confirmed：目标 90 分钟内。
- 后续候选和一次 coverage 补扫：60–120 分钟。
- Final judge、报告和 gate：预留 30–45 分钟。
- 完整运行：目标 3–4 小时，硬上限 6 小时。

时间不足时不得启动预计无法完成的新候选；已经完成证据闭环的 confirmed 仍进入最终统一裁决。不得通过规则 fallback、手工答案或降低证据要求补数量。

## 15. 验收标准

### 15.1 功能

- OpenCode CLI 仅根据 `INSTRUCTION.md` 自动启动。
- 无新增人工参数或 provider 配置。
- 输入路径自动发现。
- 目标代码仓保持不变。
- `/result/issues.json`、`issues.jsonl`、`00-summary.md` 和单 issue 报告生成成功。
- 每个 issue 满足比赛证据字段要求。

### 15.2 流程

- quote mismatch 不再触发 fresh semantic Agent。
- 新增无关 claim 不使已接受 review 失效。
- Group coverage gap 不阻塞已接受 claim 的 Investigator。
- 一个候选失败不阻塞其他候选。
- 同一候选只有新证据时才能重新 critic。
- Coverage 不为凑数量创建 task。
- 最终 gate 仍严格拒绝无证据 confirmed。

### 15.3 比赛指标

- F-Stack 隐藏已知 issue 最终识别不少于 4 个。
- 误报率不高于 50%。
- 总检视时长不超过 6 小时。
- 不针对 F-Stack 或已知 6 个答案编程。
- 非 F-Stack 正负样本不得出现明显退化。

### 15.4 开发质量

- 所有单元测试通过。
- 对应 stage replay 通过。
- `git diff --check` 通过。
- 目标仓只读回归测试通过。
- 完整 OpenCode 运行前保留上一版本 baseline，完成后比较召回、误报、首个 confirmed 时间和各阶段耗时。

## 16. 推荐实施顺序

严格按以下顺序推进：

1. 保存当前全量运行作为 baseline。
2. 实现 P0-1，并只跑 claims replay。
3. 实现 P0-2，并跑 inventory/claims replay。
4. 实现 P0-3，并跑 plan 和单 Investigator replay。
5. 实现 P0-4，并跑 claim-review、plan 和 gate replay。
6. 运行全部本地单元测试。
7. 实现 P1-1 与最小 probe 契约。
8. 通过隐藏正负样本阶段评测。
9. 最后运行一次完整 OpenCode 验证。
10. 对照 baseline 决定是否提交；不得只看最终 issue 数量。

## 17. 参考依据

- OpenAI, [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/), 2026-02-11。核心启示：使用地图和渐进披露，机械约束不变量而非微观实现。
- OpenAI, [Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/), 2026-01-23。核心启示：模型通过工具反馈循环推进，长任务需要明确终止条件与上下文管理。
- OpenAI Agents SDK, [Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)。核心启示：模型编排与代码编排可以混合，独立任务才适合并发。
- OpenAI Agents SDK, [Tracing](https://openai.github.io/openai-agents-python/tracing/)。核心启示：generation、tool、handoff 和 guardrail 应进入同一可审计 trace。
- Anthropic, [Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents), 2024-12-19。核心启示：从最简单可组合流程开始，Evaluator-Optimizer 只用于评价标准清晰且迭代有可测收益的任务。
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system), 2025-06-13。核心启示：多 Agent 适合边界明确的广度探索，但成本和协调复杂度显著增加。
- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), 2025-09-29。核心启示：每个 Agent 只应接收当前任务所需的最小高信号上下文。
- Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), 2026-01-09。核心启示：确定性 grader 与模型 grader 分工，评价结果而不是固定路径，并允许 Unknown。
- Kiecker et al., [CASCADE: Detecting Inconsistencies between Code and Documentation with Automatic Test Generation](https://arxiv.org/abs/2604.19400), 2026-04-21。核心启示：文档生成测试配合第二 oracle 可用于候选级误报抑制。
- Alor et al., [Evaluating the Use of LLMs for Documentation to Code Traceability](https://arxiv.org/abs/2506.16440), 2025-06-19。核心启示：LLM 适合语义 trace discovery，但名称假设、phantom links 和过度概括需要独立证据验证。
