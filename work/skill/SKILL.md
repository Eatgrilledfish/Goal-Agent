---
name: design-code-inconsistency
description: 由 opencode 执行的通用设计文档与代码实现语义不一致检视；使用可恢复 agent loop、角色交接、独立反证、按需单点动态验证和真实源文件证据。
---

# 通用设计/代码不一致检视

## 核心原则

- 设计文档定义应有行为，代码和运行配置定义实际行为。issue 是两者之间可证明的语义差异。
- 本工作只做设计/实现一致性检视，不做漏洞扫描、渗透测试或网安审计；结论使用功能与契约语言，不使用攻击面、CWE/CVE 等分类框架。
- helper 只负责结构清单、session、证据真实性校验、报告和 gate，不负责判断实现是否一致。
- 搜索和索引只用于取证。regex、关键词、文件名、项目名、分数或公开答案不得决定 verdict。
- context 按需加载。先读设计 claim，再围绕证据问题探索最小必要代码；需要时再扩大到调用链、配置和测试。review snapshot 不携带 VCS metadata，不能从父级 submission 仓库借用 history/blame。
- confirmed 需要 investigator 与 fresh-context critic 两个角色的交接，不允许同一段未经挑战的推理直接进入 final。
- dynamic probe 是按需证据增强层，不是第二套检测器。测试失败、构建失败或环境失败都不能单独确认不一致；无法可靠执行时保留静态语义流程并记录 inconclusive。
- 覆盖不是“读过文件”。一次完整审阅必须同时包含设计到代码的义务追踪、从高风险执行边界反查设计、以及设计能力与注册/构建/入口面的缺失对账。
- 设计入口若只是 catalog，先由模型决定实际设计源，再让 `design_source_materializer.py` 做受限复制/只读下载与哈希归档；helper 不得从 catalog 语法推断需求或候选。
- `prepare` 将原始输入物理复制到 session-local review roots。所有模型角色只在 review roots 读取、搜索和导航，并引用相对路径；helper 仍对原始输入逐行验真且 final gate 同时验证原始输入和 review 副本未变化。不得用 symlink、人工路径授权或运行时配置代替该隔离层。
- helper 只接受可逐行验真的文本设计证据；PDF/DOCX 若无带稳定行 provenance 的文本导出必须 preflight 失败，不能把二进制 replacement text 当 quote 来源。多个候选输入根必须由 orchestrator 语义选择后显式传入，helper 不静默聚合。

## 角色产物

### architecture_map.json

从仓库本身构建，不依赖预设技术栈：

```json
{
  "session_id": "session-...",
  "repository_summary": "项目职责与实际执行模型",
  "languages": ["从文件/构建证据识别"],
  "entrypoints": [{"path": "...", "purpose": "...", "evidence": "..."}],
  "subsystems": [{"subsystem_id": "SUBSYSTEM-...", "name": "...", "paths": ["..."], "role": "..."}],
  "implementation_planes": [{"plane_id": "PLANE-...", "kind": "owned|adapter|imported|generated|fast_path|slow_path|other", "paths": ["..."], "reachable_evidence": "..."}],
  "integration_boundaries": [{"boundary_id": "BOUNDARY-...", "name": "...", "paths": ["..."], "risk": "high|medium|low", "why": "..."}],
  "capability_surfaces": [{"surface_id": "CAPABILITY-...", "paths": ["..."], "declares_or_registers": "..."}],
  "configuration_surfaces": [{"path": "...", "controls": "..."}],
  "alternate_execution_paths": [{"name": "...", "paths": ["..."], "trigger": "..."}],
  "test_surfaces": [{"path": "...", "coverage": "...", "available_command": "仓库已有且当前环境可执行的命令或空", "evidence": "文件/构建证据"}],
  "probe_capabilities": {"isolated_copy_feasible": true, "available_runtime": ["从当前环境取证"], "constraints": ["缺失依赖、硬件或外部服务"]},
  "parallel_behavior_paths": [{"path_id": "PARALLEL-...", "behavior": "同一设计行为", "plane_ids": ["PLANE-..."], "evidence": "为什么这些路径可达且需分别核对"}]
}
```

当仓库存在适配层、fast/slow path、核心/数据面、服务/存储、导入/自有代码、生成/手写代码等边界时，不能只调查其中一侧。仓库中随产品构建或被运行路径调用的导入代码属于检视面，不能仅因其来源于上游而排除。同一设计行为存在多份实现时，为 `parallel_behavior_paths` 写稳定 `path_id`；后续可按 plane 拆多个 task，但每个 task 都写相同 `parallel_path_ids`，coverage 按该 ID 聚合直接 task/finding 证据。

### risk_observations.jsonl

code-to-design mode 由 fresh `risk-explorer` 在不读设计的前提下生成语义热点，不产生 verdict：

```json
{
  "observation_id": "RISK-...",
  "session_id": "session-...",
  "behavior_question": "需要由设计回答的中性行为问题",
  "observed_code_behavior": "代码可证明的实际语义",
  "review_lenses": ["1-3 个 contract lens"],
  "architecture_boundaries": ["BOUNDARY-..."],
  "implementation_planes": ["PLANE-..."],
  "code_evidence": [{"file": "...", "line_start": 1, "line_end": 1, "symbol": "...", "snippet": "..."}],
  "false_positive_checks": [{"question": "...", "method": "...", "target": "...", "result": "..."}],
  "design_lookup_questions": ["不含代码路径的规范检索问题"],
  "tool_trace": [{"seq": 1, "kind": "code_search|code_navigation|code_read|reverse_check|config_read|build_read|analysis", "tool": "...", "target": "...", "purpose": "...", "result": "..."}]
}
```

每项必须有精确代码证据、两项反查和 code search/navigation、code_read、reverse_check trace；禁止 design evidence、claim、assessment、recommendation、status 或 confidence。orchestrator 只把 `design_lookup_questions` 与通用 lens 交给 fresh spec expansion，不把代码路径/snippet 泄漏给设计角色。

### design_coverage.json

必须逐一覆盖 manifest 的全部 `document_key`：

```json
{
  "session_id": "session-...",
  "document_groups": [
    {
      "document_key": "manifest 中的 key",
      "members": ["文档路径"],
      "disposition": "applicable|inapplicable|superseded|supporting",
      "evidence": "来自设计或仓库的适用性证据",
      "claim_ids": ["CLAIM-..."],
      "behavior_families": ["该文档组实际覆盖的行为簇"]
    }
  ]
}
```

同 stem 的多格式文件可作为一个文档组，但不能静默丢弃。inapplicable/superseded/supporting 必须有证据。代码中没有对应实现不是 inapplicable 证据，它可能是应确认的能力缺失。

### design_claims.jsonl

每行：

```json
{
  "claim_id": "CLAIM-稳定ID",
  "session_id": "session-...",
  "document": "文档名",
  "path": "设计根目录内相对路径",
  "section": "章节",
  "line_start": 1,
  "line_end": 3,
  "quote": "可在这些行核验的原文",
  "behavior": "可验证的设计语义",
  "behavior_family": "集合处理|时序副作用|能力存在性|链式遍历|边界分派|状态/错误|其他",
  "normative_strength": "mandatory|recommended|optional|declared_capability|informational",
  "applicability": "适用组件、场景、版本或前提",
  "priority": "high|medium|low",
  "ambiguities": ["仍需解释的术语或范围"],
  "probe_oracle": {
    "testability": "candidate|not_suitable|unknown",
    "preconditions": ["仅来自设计的适用前提"],
    "stimulus": "若可执行，施加什么输入/事件",
    "expected_observation": "设计要求的可观察结果",
    "non_testable_reason": "not_suitable 时说明为何不能由单点运行验证"
  }
}
```

Spec Analyst 返回后必须立即运行 `goal_runner.py design-check`。该 gate 逐项核对 session、coverage 的全部 manifest 文档组、claim 字段、probe oracle 对象和真实设计行；未通过时禁止规划 investigator。

结构 gate 通过后由 fresh spec critic 只读设计、脱敏的 `design_agent_manifest.json` 与 claim artifacts，逐 claim 检查 quote 是否蕴含 behavior、normative strength 是否忠实、是否把多个可独立裁决的角色/分支/阶段/元素语义压成一条、applicability 是否有来源；逐文档组检查 behavior families 与独立设计分支是否漏抽。它不读完整 workspace manifest、代码、risk observations 或 findings。`design_claim_review.json` 必须覆盖当前全部 claim/document group 并绑定 claims、coverage、design-agent manifest digest；任何 `repair` 都回到 spec analyst，修复后重新执行 design-check 和 claim-check。

不要把所有描述句都变成 claim，但不能只抽样几个容易验证的强制句。先 breadth pass 阅读入口、目录、摘要、范围和规范章节，为每个适用文档组列出行为簇；再 difference-oriented pass 覆盖外部可见行为、数据/数量约束、失败语义、状态转换、跨模块责任、并发/时序、推荐/可选副作用和明确支持的能力。每个已声明行为簇至少一个 claim，独立行为不得被压成一个宽泛 claim。catalog 将文档列为 relevant/in-scope/required 时，其代表的能力默认进入 capability 对账；代码缺少同名符号不能降级。`MAY`/可选项不自动构成强制违规，但实现缺失仍可作为明确分类的 capability/optional-behavior gap；不得在 claim 阶段静默删除。`probe_oracle` 必须在不读代码的前提下从同一设计 claim 形成；非运行时、能力完全缺失、强环境依赖或无法产生可靠可观察结果的 claim 应标为 `not_suitable`，不能编造测试。

### investigation_tasks.jsonl

一个 task 只对应一个 `claim_id`、一个可独立裁决的行为和一组明确 execution planes。不同 claim、不同规范分支或需要分别裁决的平行 plane 必须拆成不同 task；禁止用一个宽问题打包多个义务。

```json
{
  "task_id": "TASK-稳定ID",
  "session_id": "session-...",
  "claim_id": "CLAIM-...",
  "question": "实现是否满足哪一个具体语义？",
  "starting_points": ["从仓库结构、入口或概念开始，不是假定答案"],
  "supporting_evidence_needed": ["需要看到什么"],
  "disconfirming_evidence_needed": ["什么会否定该 issue 假设"],
  "review_lenses": ["1-3 个与本任务实际证据问题相关的 contract lens"],
  "exploration_mode": "agent_loop_contract.coverage_contract.exploration_modes 中的值",
  "architecture_boundaries": ["BOUNDARY-..."],
  "implementation_planes": ["PLANE-..."],
  "parallel_path_ids": ["PARALLEL-..."],
  "risk_observation_ids": ["RISK-..."],
  "status": "pending|in_progress|complete|deferred",
  "defer_reason": "",
  "defer_evidence": {"kind": "provider_failure|tool_failure", "attempts": [{"attempt_id": "...", "outcome": "failed", "evidence": "具体运行证据"}]}
}
```

### investigation_findings.jsonl

```json
{
  "finding_id": "FINDING-稳定ID",
  "session_id": "session-...",
  "task_id": "TASK-...",
  "claim_id": "CLAIM-...",
  "hypothesis": "可能存在的差异",
  "expected_behavior": "从 claim 推导的应有行为",
  "observed_behavior": "从代码和配置推导的实际行为",
  "design_evidence": [],
  "code_evidence": [],
  "supporting_evidence": ["事实"],
  "disconfirming_evidence": ["反证或未解决信息"],
  "false_positive_checks": [],
  "tool_trace": [],
  "dynamic_probe_selection": {
    "disposition": "selected|not_selected|not_suitable|environment_limited",
    "reason": "基于可观察性、环境、成本与证据价值的解释"
  },
  "assessment": "contradiction_supported|uncertain|design_satisfied",
  "review_lenses": ["实际用于本 finding 的 lens"],
  "recommendation": "critic_review|probable|reject"
}
```

### dynamic_probes.jsonl

只为已选择并实际尝试的单点 probe 写一行。probe 文件、构建产物和运行目录只能位于 `${STATE_ROOT}/probes/<probe_id>/` 的目标仓库隔离副本中：

```json
{
  "probe_id": "PROBE-稳定ID",
  "session_id": "session-...",
  "finding_id": "FINDING-...",
  "claim_id": "CLAIM-...",
  "oracle": {
    "source": "design_claim",
    "preconditions": ["逐字继承 claim.probe_oracle"],
    "stimulus": "逐字继承 claim.probe_oracle",
    "expected_observation": "逐字继承 claim.probe_oracle"
  },
  "selection_reason": "为何该候选值得在剩余预算内动态复核",
  "isolation": {"kind": "session_copy", "workspace": "STATE_ROOT 下路径", "original_target_unchanged": true},
  "baseline": {"status": "passed|failed|not_available", "command": "仓库已有命令或空", "result": "可核验摘要"},
  "execution": {"status": "completed|environment_failed|not_executed", "command": "实际命令或空", "exit_code": 0, "observed": "实际输出/行为", "target_reached": true},
  "interpretation": "supports_contradiction|disconfirms_contradiction|inconclusive",
  "limitations": ["未覆盖范围、非确定性或环境限制"],
  "tool_trace": [{"seq": 1, "kind": "build_read|test|analysis", "tool": "...", "target": "...", "purpose": "...", "result": "..."}]
}
```

`baseline.status != passed`、`execution.status != completed` 或 `target_reached != true` 时，`interpretation` 必须是 `inconclusive`。不得联网安装依赖、调用外部可变系统或修改原目标来让 probe 成功。probe 失败只能加强已有静态矛盾；probe 通过是必须由 critic 解释的反证，不能自动证明全部路径一致。

### investigation_rounds.jsonl

每轮一行，禁止自创缩写字段：

```json
{
  "round_id": "ROUND-001",
  "session_id": "session-...",
  "strategy": "本轮与上一轮不同的证据策略",
  "exploration_modes": ["contract 中的 mode"],
  "document_groups": ["manifest document_key"],
  "architecture_boundaries": ["BOUNDARY-..."],
  "implementation_planes": ["PLANE-..."],
  "lenses": ["contract 中的完整 lens"],
  "claim_ids": ["CLAIM-..."],
  "task_ids": ["TASK-..."],
  "finding_ids": ["FINDING-..."],
  "outcome": "本轮事实结果",
  "next_strategy": "coverage 缺口或 finalize"
}
```

### critic_reviews.jsonl

```json
{
  "review_id": "CRITIC-稳定ID",
  "session_id": "session-...",
  "finding_id": "FINDING-...",
  "claim_id": "CLAIM-...",
  "decision": "confirm_contradiction|probable_contradiction|reject_issue|needs_more_evidence",
  "challenges": ["具体挑战"],
  "checks_performed": ["critic 自己执行的读取/搜索/测试"],
  "dynamic_probe_review": {
    "status": "not_run|supports_contradiction|disconfirms_contradiction|inconclusive",
    "probe_id": "PROBE-... 或空",
    "oracle_validity": "是否忠实来自 design claim",
    "environment_validity": "baseline/依赖是否足以解释结果",
    "reachability": "是否证明触达目标路径",
    "effect_on_decision": "该动态证据如何影响本次判断"
  },
  "review_context": "fresh_subagent",
  "resolution": "挑战是否解决及理由",
  "remaining_risks": []
}
```

critic 必须主动寻找最强替代解释，至少独立执行两项检查，覆盖其中两项：另一实现路径、编译/运行配置、调用路径可达性、文档版本与 scope、生成/导入代码与依赖边界、测试所证明的反例。实现满足设计时必须 `reject_issue`，不能用含糊的“approved”。

critic handoff 只能包含上述 critic schema，不能混入最终 issue/verdict 字段。相同 finding 和相同 evidence 只接受一个 critic 结论；数量不足不是再次寻找 critic 的理由。只有 investigator 产生新的可核验证据后才允许 revision；revision 仍以 `finding_id` 为 ledger 唯一键，原子替换同一路径的当前 critic 对象并使用新的 `review_id`，不得向 JSONL 追加重复 finding 行。旧版本只保留在 merge report/session trace 中。

### agent_review_verdicts.jsonl

confirmed/probable 每行必须包含：

```json
{
  "finding_id": "FINDING-...",
  "session_id": "session-...",
  "claim_id": "CLAIM-...",
  "status": "confirmed|probable",
  "title": "事实标题",
  "confidence": 0.9,
  "severity": "critical|high|medium|low",
  "issue_type": "missing_behavior|contradictory_behavior|partial_implementation|wrong_boundary|invalid_state_transition|data_contract_mismatch|other",
  "design_evidence": [{"document": "...", "path": "...", "section": "...", "line_start": 1, "line_end": 2, "quote": "..."}],
  "code_evidence": [{"file": "...", "line_start": 1, "line_end": 2, "symbol": "...", "snippet": "..."}],
  "expected_behavior": "设计要求",
  "actual_behavior": "代码实际行为",
  "inconsistency": "二者如何冲突",
  "impact": "触发条件与功能影响",
  "scope_applicability": "为何适用于此版本/路径",
  "false_positive_checks": [{"question": "...", "method": "...", "target": "...", "result": "..."}],
  "dynamic_validation": {"status": "not_run|supports_contradiction|disconfirms_contradiction|inconclusive", "probe_id": "PROBE-... 或空", "reason": "为何使用、跳过或如何解释"},
  "critic_review": {"review_id": "CRITIC-...", "decision": "confirm_contradiction", "challenges": ["..."], "resolution": "...", "review_context": "fresh_subagent"},
  "tool_trace": [{"seq": 1, "kind": "design_read|code_search|code_navigation|code_read|reverse_check|test|config_read|history_read|build_read|analysis", "tool": "...", "target": "...", "purpose": "...", "result": "..."}],
  "generalization_rationale": "结论只来自当前输入证据",
  "agent_notes": "可选"
}
```

至少两项 `false_positive_checks`；tool trace 至少包含 design_read、code search/navigation、code_read、reverse_check 四类。final judge 必须逐值复制 finding 的 design/code evidence、expected/observed behavior、false-positive checks 和 tool trace，并逐值复制 critic review；不得在 judge 阶段改写引用、行号、trace kind 或行为描述。每个 confirmed/probable 都必须有 `dynamic_validation` disposition，但 `not_run` 和 `inconclusive` 不妨碍静态证据充分的 issue；引用 probe 时必须与 critic 的 `dynamic_probe_review` 和 probe ledger 一致。rejected 至少包含 `finding_id`、`session_id`、`status=rejected` 和 `rejection_reason`。最终报告只发布 confirmed。

### semantic_coverage.json

逐一使用 loop contract 中的完整 lens 字符串：

```json
{
  "session_id": "session-...",
  "lenses": [
    {
      "lens": "collection completeness and hidden fixed bounds",
      "disposition": "investigated|inapplicable",
      "evidence": "为什么适用/不适用，以及实际调查了什么",
      "task_ids": ["TASK-..."],
      "finding_ids": ["FINDING-..."],
      "design_group_refs": ["document_key"],
      "boundary_refs": ["BOUNDARY-..."],
      "counterfactual": "若适用会在什么设计/架构证据中出现；为什么当前确实不适用"
    }
  ]
}
```

investigated lens 必须同时有相互关联的 task 和 finding，且两者的 `review_lenses` 包含该 lens。合规 finding 也可证明 lens 已调查，但必须 `assessment=design_satisfied`，不能发布为 issue。`inapplicable` 必须引用已核验的设计文档组与架构边界并写 counterfactual；“当前不是路由器/服务端/某种部署”之类单一配置结论，不能排除分派、所有权或其他路径中的同类语义。

task/finding 的 `review_lenses` 必须精确，最多三个。不能把 contract 的全部 lens 复制到一个普通 finding 并声称全覆盖。在约 40% 时间点前，应让当前适用 lens 各自至少有一项真实调查；连续两个同一行为簇的合规 finding 后切换文档组、execution plane 或 lens。

### coverage_audit.json

```json
{
  "session_id": "session-...",
  "design_documents_reviewed": ["相对路径"],
  "claims_total": 0,
  "claims_investigated": 0,
  "rounds_completed": 0,
  "exploration_modes_completed": ["三个 contract mode"],
  "document_groups_total": 0,
  "document_groups_accounted": 0,
  "code_areas_reviewed": ["目录/模块/边界"],
  "architecture_boundaries": [{"boundary_id": "...", "status": "investigated|deferred", "evidence": "..."}],
  "remaining_high_priority_claims": [{"claim_id": "CLAIM-...", "reason": "仍缺哪条可执行证据"}],
  "deferred_claims": [{"claim_id": "...", "task_id": "TASK-...", "reason": "与 task.defer_evidence 一致的具体运行缺口"}],
  "false_positive_samples_rechecked": ["FINDING-..."],
  "next_round_tasks": [{
    "claim_id": "CLAIM-...",
    "question": "下一轮证据问题",
    "exploration_mode": "contract mode",
    "review_lenses": ["1-3 个 lens"],
    "architecture_boundaries": ["BOUNDARY-..."],
    "implementation_planes": ["PLANE-..."],
    "parallel_path_ids": ["PARALLEL-..."],
    "risk_observation_ids": ["RISK-..."],
    "priority_reason": "为什么它填补真实缺口"
  }],
  "stop_reason": "为什么现在可以停止或必须继续"
}
```

## Loop 策略

先宽后深，但每轮由证据决定下一步：

1. 设计地图：逐文档组识别行为簇、适用范围和高风险 claims。
2. 仓库定向：读 README、构建/包清单和目录结构，理解入口、能力注册、适配/导入代码与所有执行 plane。
3. `design-to-code obligation tracing`：从高价值 claims 追到真实执行语义，不能按文件顺序只抽前几条，也不能以同一行为簇的一条合规样本替代其他 claim。
4. `code-to-design risk backtracking`：从高风险 boundary/plane 的集合终止条件、异步副作用、dispatch/ownership、链式解析、配置分支等语义热点反查其影响的设计 claim。热点由模型阅读上下文识别，搜索模式本身不产生 verdict。
5. `capability-absence reconciliation`：把设计能力表与构建、注册、入口、配置、邻近能力和文档声明对账；全局无命中只能是辅助证据。
6. `design-grounded dynamic probe`：对高价值、可观察、低成本候选按 oracle 独立性、环境基线、隔离副本和路径可达性做能力探测；不可执行就记录原因，绝不转为规则 fallback。
7. 反证与 critic 交接：确认前寻找替代实现、调用者、配置开关、测试和版本差异，并独立复核 probe oracle、环境和解释。
8. coverage 反馈：从未覆盖 lens、未触达高风险边界/平行 plane、catalog capability 和缺失探索模式生成结构化下一轮任务。

每类 handoff 合并时必须使用 `handoff_merge.py --artifact-type task|risk|finding|probe|critic --session-id <当前 session> --report <trace-path>`；finding 的 Task self-check 只传 review roots，orchestrator 原子 merge 再传原始 `--code-root` 与 `--design-root`，按相同相对路径二次逐行验真。investigator 在写 finding 前使用 `handoff_template.py` 取得只复制 task/claim 元数据的语义中立模板，返回前用 `handoff_merge.py --check-file` 自检。所有 subagent 调用使用最多 2 个并发的有界批次；批次 merge report 未通过或本批 ID 未全部进入 `validated_ids` 时，`investigator_batch_gate.json` 锁住新的模板，只修复 report 的 `invalid_ids`。不得把整个 portfolio 一次性并发提交；结构或引用校验失败的对象不能进入共享 ledger，不重跑已经有效的 handoff。

pristine templates 同时是 investigator batch 的 expected membership：最多两个尚未合并 template，任一对应 handoff 缺失时 report 写 `expected_ids/missing_ids` 并保持 gate 失败。self-check 与主 merge 必须逐值保护 template 的 identity、claim、hypothesis、expected behavior、design evidence 和 lenses。成功 finding merge 自动把对应 task 转为 complete、刷新 task digest 并写 session lifecycle event；final gate 要求 design/task/risk/finding/critic/probe（若有）的 passed trace 能覆盖当前 ledger。

semantic coverage 不是 ID 清单：每个 investigated lens 的 task 必须 complete、声明该 lens，并与所引 finding/claim、design group 和 boundary 直接关联；每个平行行为路径按稳定 `path_id` 聚合 completed task/finding，所有声明 plane 都必须有直接证据。未调查 high claim 只有关联 task 在两次 provider/tool 失败后携带结构化 `defer_evidence` 才可 deferred，普通 portfolio 取舍、环境泛称或任意理由不能关闭 actionable claim。

发布计数按唯一、已验证的 finding ID 计算。`issues.json`、`issues.jsonl`、摘要和单 issue Markdown 必须与 `validated_issues.json` 精确绑定，复制行、改 ID 或手工增补不得通过 final gate。

首轮 0 confirmed、所有 finding 被 reject、或 gate 失败时，必须触发 coverage-critic：检查是否只读了少数设计文档、是否把一条合规样本外推成整类合规、是否过度集中在核心目录、是否忽略导入/适配执行 plane、能力缺失和跨边界行为。只要仍在时间预算内，切换 exploration mode，并更换设计文档组、架构边界或审阅 lens 继续下一轮，不能把项目成熟度当作停止依据。

每轮更新 ledger，写清假设、动作、观察、失败搜索、下一步和停止原因。长任务恢复依赖这些事实记录，而不是压缩后的聊天记忆。

## 时间预算

- 约 10%：输入定位、设计与仓库地图。
- 约 55%：高优先级 claim 调查。
- 约 20%：critic、反证与按需单点 dynamic probe；不得为搭建完整环境挤占主调查预算。
- 约 10%：coverage audit 与补漏。
- 至少 5%：validator、证据修复、report、gate。

在 5.5 小时前停止新增低价值探索，为最终闭环保留时间；硬上限 6 小时。

## 证据标准

- quote 和 snippet 必须来自声明的真实行范围，validator 会逐项重读。
- “仓库里搜不到”只能作为辅助证据，不能单独证明功能缺失；必须同时给出相关入口、邻近实现、能力注册/构建边界或明确 unsupported 路径。
- 局部函数看起来不符合要求时，先追调用链和替代路径。
- 集合/选项/记录处理必须验证完整终止条件和所有元素，而不是确认循环存在；链式结构必须验证每一跳如何推进，而不是只检查第一个元素。
- 时序行为必须区分同步响应、延迟发送、重试和主动/非请求副作用；一种路径正确不能证明其他路径正确。
- 跨边界 lens 指任何分类、分派、所有权或执行 plane 变化，不等同于网络路由或某一部署模式。
- 设计含糊、版本不适用、代码可能由外部依赖实现或反证未解决时，降级 probable 或 rejected。
- 动态 probe 的 oracle 必须追溯到 claim；测试自身错误、基线失败、未证明路径可达、环境/依赖失败一律 inconclusive。测试结果不能取代真实设计原文和代码证据。
- final 只发布 confirmed。
