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
  "integration_boundaries": [{"boundary_id": "BOUNDARY-...", "name": "...", "paths": ["..."], "plane_ids": ["PLANE-..."], "risk": "high|medium|low", "why": "..."}],
  "capability_surfaces": [{"surface_id": "CAPABILITY-...", "paths": ["..."], "declares_or_registers": "..."}],
  "configuration_surfaces": [{"path": "...", "controls": "..."}],
  "alternate_execution_paths": [{"name": "...", "paths": ["..."], "trigger": "..."}],
  "test_surfaces": [{"path": "...", "coverage": "...", "available_command": "仓库已有且当前环境可执行的命令或空", "evidence": "文件/构建证据"}],
  "probe_capabilities": {"isolated_copy_feasible": true, "available_runtime": ["从当前环境取证"], "constraints": ["缺失依赖、硬件或外部服务"]},
  "parallel_behavior_paths": [{"path_id": "PARALLEL-...", "behavior": "同一设计行为", "plane_ids": ["PLANE-..."], "evidence": "为什么这些路径可达且需分别核对"}]
}
```

当仓库存在适配层、fast/slow path、核心/数据面、服务/存储、导入/自有代码、生成/手写代码等边界时，不能只调查其中一侧。仓库中随产品构建或被运行路径调用的导入代码属于检视面，不能仅因其来源于上游而排除。每个 plane 是有真实入口/调用关系的行为 facet，paths 精确到可分配上下文；不要用仓库级父目录把可独立行为粘成一个巨大 component，也不能为了并发拆开真实耦合或丢掉 broad scope。每个 integration boundary 的 `plane_ids` 必须非空且只引用已声明 plane，用于证明哪些执行面共同穿越该边界。同一设计行为存在多份实现时，为 `parallel_behavior_paths` 写稳定 `path_id`；后续可按 plane 拆多个 task，但每个 task 都写相同 `parallel_path_ids`，coverage 按该 ID 聚合直接 task/finding 证据。

### risk_sweep_plan.json

architecture-check 通过后、任何 risk Task 启动前，由 orchestrator 写一个绑定当前 architecture digest、包含至少两个 focused slices 的计划：

```json
{
  "session_id": "session-...",
  "plan_id": "RISK-PLAN-001",
  "architecture_map_sha256": "当前 architecture_map.json 的 SHA-256",
  "required_coverage": {
    "boundary_ids": ["全部 BOUNDARY-..."],
    "plane_ids": ["全部 reachable PLANE-..."],
    "parallel_path_ids": ["全部 PARALLEL-..."]
  },
  "slices": [{
    "sweep_id": "RISK-SWEEP-01",
    "architecture_boundaries": ["BOUNDARY-..."],
    "implementation_planes": ["PLANE-..."],
    "parallel_path_ids": ["PARALLEL-..."],
    "anchor_paths": ["分配 boundary/plane paths 的完整规范化并集"],
    "review_lenses": ["完整 contract portfolio lenses"],
    "scope_rationale": "为什么该切片是独立且不可再拆的执行范围"
  }, {
    "sweep_id": "RISK-SWEEP-02",
    "architecture_boundaries": ["BOUNDARY-..."],
    "implementation_planes": ["PLANE-..."],
    "parallel_path_ids": ["PARALLEL-..."],
    "anchor_paths": ["分配 boundary/plane paths 的完整规范化并集"],
    "review_lenses": ["完整 contract portfolio lenses"],
    "scope_rationale": "为什么该切片是独立且不可再拆的执行范围"
  }]
}
```

required coverage 由 architecture 唯一推导：全部 boundaries、全部 reachable planes 和全部 `parallel_behavior_paths.path_id`；risk 等级只用于后续选题排序。plan 包含至少两个非空 focused slices；三类 ID 在所有 slice 间分别互斥且并集等于 required coverage。一个 parallel path 与它的全部 planes、一个 boundary 与它的全部 `plane_ids` 必须同属一个 slice；共享 plane或相同/父子 architecture paths 会组成不可拆单元。`anchor_paths` 必须是分配 paths 的完整规范化并集，不能用 leaf 代表 broad directory。每个 slice 的 `review_lenses` 都等于完整 contract portfolio；单 observation 保持 1–3 lens，aggregate 精确覆盖全部。若所有 required ID 形成单一不可拆单元，不能用重复范围或不同 lens 伪造第二个 slice，必须报告 plan 阻塞。

任何 risk Task 启动前必须运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py risk-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

失败时只 repair architecture/plan 并重跑；只有 digest-bound `risk_sweep_plan_validation.json` 为 `passed=true` 才可按顺序启动 risk batches。

orchestrator 从 `sweep_id` 确定唯一的 handoff 与 self-check 路径，两者分别位于固定 state/log 子目录且不得相同。每批按 plan 顺序并发最多两个 risk explorer；它们可以读取整个 review code root 来追调用链，但只能拥有本 slice 的 boundary/plane/path，其他 slices 的实现只可作为导航上下文，不得进入 observation coverage IDs 或 `code_evidence`。任何需要跨 slice 证据才能成立的 observation 都说明 plan 遗漏耦合，必须返回 `plan_repair_required`；更新 architecture/plan 后用 fresh Tasks 重做全部 slices。

### risk_observations.jsonl

code-to-design mode 由 fresh `risk-explorer` 在不读设计的前提下生成语义热点，不产生 verdict：

```json
{
  "observation_id": "RISK-...",
  "session_id": "session-...",
  "sweep_id": "逐字复制本 slice 的 plan-declared RISK-SWEEP-...",
  "risk_sweep_plan_sha256": "当前 risk_sweep_plan.json 的 SHA-256",
  "behavior_question": "需要由设计回答的中性行为问题",
  "observed_code_behavior": "代码可证明的实际语义",
  "review_lenses": ["1-3 个 contract lens"],
  "architecture_boundaries": ["BOUNDARY-..."],
  "implementation_planes": ["PLANE-..."],
  "parallel_path_ids": ["PARALLEL-..."],
  "code_evidence": [{"file": "...", "line_start": 1, "line_end": 1, "symbol": "...", "snippet": "..."}],
  "false_positive_checks": [{"question": "...", "method": "...", "target": "...", "result": "..."}],
  "design_lookup_questions": ["不含代码路径的规范检索问题"],
  "tool_trace": [{"seq": 1, "kind": "code_search|code_navigation|code_read|reverse_check|config_read|build_read|analysis", "tool": "...", "target": "...", "purpose": "...", "result": "..."}]
}
```

每项必须逐值绑定所属 `sweep_id` 和当前 plan digest，并且 boundary/plane/path IDs 都是该 slice 的子集。每个声明的 boundary/plane 都要由该 ID 自身 path 内且位于 slice anchor 的 evidence 支撑；parallel path 的每个 plane 都要有同时引用 path+plane 的本地证据。每个 slice 写一个独立 JSON 数组 handoff，数组内 observation 数量不设固定上限；全部 observations 的 boundary/plane/path/lens 并集必须精确覆盖该 slice 的完整分配范围，即使实际行为合规也要用真实中性问题和精确代码证据交代。每项仍须有两项反查和 code search/navigation、code_read、reverse_check trace；禁止 design evidence、claim、assessment、recommendation、status 或 confidence。orchestrator 只把 `design_lookup_questions` 与通用 lens 交给 fresh spec expansion，不把代码路径/snippet 泄漏给设计角色。

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

Spec Analyst 返回后必须立即运行 `goal_runner.py design-check`。该 gate 逐项核对 session、coverage 的全部 manifest 文档组、claim 字段、probe oracle 对象和真实设计行；未通过时禁止规划 investigator。通过后的 `design_claims.jsonl` 是完整设计索引，不是必须逐条调查或逐条 critic 的工作队列。

不要把所有描述句都变成 claim，但也不能只抽容易验证的强制句。先 breadth pass 阅读入口、目录、摘要、范围和规范章节，为每个适用文档组列出行为簇；再 difference-oriented pass 覆盖外部可见行为、数据/数量约束、失败语义、状态转换、跨模块责任、并发/时序、推荐/可选副作用和明确支持的能力。每个已声明行为簇至少一个索引 claim，独立行为不得压成宽泛 claim。`priority` 表示设计重要性，只用于模型比较候选，不等于本 session 必须调查的全量清单。catalog 将文档列为 relevant/in-scope/required 时，其代表能力进入 capability 对账；代码缺少同名符号不能降级。`MAY`/可选项不自动构成强制违规，但可作为准确分类的 optional/capability 差异。`probe_oracle` 只从设计形成；无法可靠单点验证时标 `not_suitable`，不能编造测试。

### claim_review_scope.json 与 design_claim_review.json

每轮先由 orchestrator 从已验证 risk、设计索引、架构 capability/boundary 和未覆盖证据中选择最多 4 个 task 的最小调查 frontier，再写累计 scope。首轮至少包含一个由真实 risk observation 支撑的高风险 boundary 锚点；其余 boundary、plane、mode 由 coverage 分轮补齐，而不是一次冻结：

```json
{
  "session_id": "session-...",
  "round_id": "ROUND-...",
  "design_claims_sha256": "当前 design_claims.jsonl 的 SHA-256",
  "claim_ids": ["已有 task/finding 引用的 accepted claim 与本轮待审 claim 的去重并集"]
}
```

scope 不能删除或改写已有 task/finding 使用的 accepted claim；尚未创建 task 的本轮待审 claim 若被 critic 要求拆分或替换，可以从 scope 移除并以新 ID 原子替换。fresh spec critic 深审本轮新增 scope claims 及受影响 groups；只有上一版 review 的 claims、coverage、manifest 三个 digest 与当前完全相同时，才可逐值复用旧 accepted claim review，否则重审当前 scope。仍由 fresh Task 完整重写累计 review。不读代码、risk、architecture、tasks 或 findings。`design_claim_review.json.input_digests` 必须绑定 claims、coverage、design-agent manifest 和 scope 四个输入；claim reviews 恰好覆盖 scope claims，group reviews 恰好覆盖这些 claims 所属 document groups。任何 repair 回到 fresh spec analyst/spec critic，主 agent不得补写语义字段。task-check、handoff template 和 final gate 都拒绝未在当前 accepted scope 中的 claim。

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

task/finding 的 `review_lenses` 必须精确，最多三个。不能把 contract 的全部 lens 复制到一个普通 finding 并声称全覆盖。在约 40% 时间点前，coverage 必须把每个 lens 判为真实 investigated 或有设计/架构证据的 inapplicable；不能只为填标签创建宽泛 task。连续两个同一行为簇的合规 finding 后切换文档组、execution plane 或 lens。

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
  "remaining_scoped_claims": [{"claim_id": "CLAIM-...", "reason": "已进入累计 scope 但仍缺哪条可执行证据"}],
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

先广泛建立索引和代码风险面，再按冻结 frontier 深查：

1. architecture-check 后写并核验多 slice risk plan，按顺序每批并行完成两个 code-only risk sweeps；全部 handoff 原子合并后再启动 design breadth/index。
2. 从 `claim × risk boundary/plane`、外部设计义务和 capability surface 中选择本轮最小 frontier，先做 scoped spec critic，再建 task。
3. `design-to-code obligation tracing` 从被选 claim 追真实执行语义；`code-to-design risk backtracking` 从已验证代码 observation 反查设计；`capability-absence reconciliation` 对账构建、注册、入口、配置与邻近能力。
4. 每个 round 最多 4 个 task；按顺序全部 complete/deferred 前不追加新 round 或 opportunistic task。最多 2 个并发只是 batch size，不是只跑一批。
5. 每轮 investigator 完成后先做 coverage。只有具体 lens、boundary、parallel plane、scope claim、risk 或 exploration-mode 缺口才能生成 `next_round_tasks`。
6. coverage closed 后才做少量 design-grounded probe、fresh evidence critic，并对当前闭环 frontier 做一次 final judge。critic 要求补证会重新打开 coverage；只有 task/finding/scope/round 等 coverage 输入变化才重跑 coverage。

每类 handoff 合并时必须使用 `handoff_merge.py --artifact-type task|risk|finding|probe|critic --session-id <当前 session> --report <trace-path>`；finding 的 Task self-check 只传 review roots，orchestrator 原子 merge 再传原始 `--code-root` 与 `--design-root`，按相同相对路径二次逐行验真。risk 前置阶段按 plan 顺序每批并发最多两个 Task，每项只写唯一 JSON 数组和独立报告，全部 planned slices 都通过后才执行一次原子 merge。investigator 在写 finding 前使用 `handoff_template.py` 取得只复制 task/claim 元数据的语义中立模板，返回前用 `handoff_merge.py --check-file` 自检。所有 subagent 调用使用最多 2 个并发的有界批次；批次 merge report 未通过或本批 ID 未全部进入 `validated_ids` 时，`investigator_batch_gate.json` 锁住新的模板，只修复 report 的 `invalid_ids`。不得把整个 portfolio 一次性并发提交；结构或引用校验失败的对象不能进入共享 ledger，不重跑已经有效的 handoff。

pristine templates 同时是 investigator batch 的 expected membership：最多两个尚未合并 template，任一对应 handoff 缺失时 report 写 `expected_ids/missing_ids` 并保持 gate 失败。self-check 与主 merge 必须逐值保护 template 的 identity、claim、hypothesis、expected behavior、design evidence 和 lenses。成功 finding merge 自动把对应 task 转为 complete、刷新 task digest 并写 session lifecycle event；final gate 要求 design/task/risk/finding/critic/probe（若有）的 passed trace 能覆盖当前 ledger。

semantic coverage 不是 ID 清单：每个 investigated lens 的 task 必须 complete、声明该 lens，并与 finding/claim、design group 和 boundary 直接关联；每个平行行为路径按稳定 `path_id` 聚合 completed task/finding，所有声明 plane 都必须有直接证据。只有累计 claim scope 中的 claim 构成本 session 可执行 frontier；其中未调查项必须进入 `remaining_scoped_claims` 与 `next_round_tasks`，或由两次 provider/tool 失败的结构化 `defer_evidence` 关闭。索引中的未选 high claim 不会仅因标签自动变成全量任务。

发布计数按唯一、已验证的 finding ID 计算。`issues.json`、`issues.jsonl`、摘要和单 issue Markdown 必须与 `validated_issues.json` 精确绑定，复制行、改 ID 或手工增补不得通过 final gate。

coverage-critic 每个调查 round 都运行，与 confirmed 数量无关。它检查是否只读少数文档、是否把一条合规样本外推成整类合规、是否集中在核心目录、是否忽略导入/适配 plane、能力注册和跨边界行为。下一轮必须切换 exploration mode、设计文档组、架构边界或 lens 中至少一项；不能把项目成熟度或候选数量当作停止依据。

每轮更新 ledger，写清假设、动作、观察、失败搜索、下一步和停止原因。长任务恢复依赖这些事实记录，而不是压缩后的聊天记忆。

## 时间预算

- 约 15%：输入定位、architecture map 与设计索引；禁止在这里做全量 claim critic。
- 约 20%：code-only risk sweeps、spec expansion 与 scoped frontier planning。
- 约 45%：按冻结顺序完成 investigator rounds。
- 约 10%：每轮 coverage audit 与证据化补漏。
- 约 10%：按需单点 probe、candidate critic、当前闭环 frontier 的 final judge、report 和 gate。

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
