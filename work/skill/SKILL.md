---
name: design-code-inconsistency
description: 由运行中的 OpenCode CLI 执行的通用设计文档与代码实现语义不一致检视；使用增量 evidence-pair frontier、候选级验证、独立反证和可审计证据链。
---

# 通用设计/代码不一致检视

## 1. 任务边界

- 设计资料定义应有行为，目标仓及其构建/配置定义实际行为；issue 是二者间可证明、适用于当前输入的语义差异。
- 这是通用一致性检查，不是漏洞扫描。安全、性能、协议、状态、数据、API、部署、并发等都只是可能的设计维度。
- OpenCode 模型负责 scope、义务、可达行为、矛盾、反证与置信度；helper 只负责 schema、路径/行号、quote/source hash、digest、session、merge、只读校验和报告。
- 禁止用项目名、固定文件/符号、协议/RFC 名、关键词/regex、规则表、分数或公开答案决定 candidate/verdict。
- 所有模型角色只读 session-local review roots；证据写相对路径。helper 对原始输入按相同路径逐行验真。
- 动态 probe 只增强一个 candidate 的证据，不替代静态设计/代码证据。环境或 baseline 失败一律 inconclusive。
- `unknown`、`inconclusive`、`rejected` 是正常状态。只发布严格闭环的 confirmed。

## 2. 执行模型

```text
prepare/read-only snapshot
  ├─ light design inventory (design-only)
  └─ architecture map → disjoint code-only risk sweeps
                 ↓
incremental evidence-pair frontier
  → on-demand claim materialization
  → per-claim/group spec review
  → atomic task-plan gate
  → investigator
  → optional focused probe
  → fresh evidence critic
                 ↓
one coverage decision / at most one supplement
  → final judge → review → report/final gate
```

全局最多两个并发 Task。互斥广度 slice 与不同 candidate 可并发；同一 candidate 的 investigator/probe/critic 不并发。每个并行 Task 只写自己的 handoff；共享 JSONL 由 deterministic merge 原子更新。结构错误在同一角色 Task 内修；语义 repair 最多一次 fresh 同角色 Task。主 Agent不得补写语义字段。

每个语义phase/Task/repair/stop用`session_event.py`写rich checkpoint；每个deterministic validation/merge写digest-bound JSON trace并在ledger登记。Checkpoint记录session ID、scope/实际输入文件摘要、started/ended、attempt、provider session、验证错误分类、repair与输出数量、terminal outcome和stop reason。每段开始先保存UTC时间，结束时传 `--role/--event/--scope`、至少一个可重复的`--input-artifact <本阶段实际读取的普通文件>`、`--started-at/--ended-at/--provider-attempt/--provider-session-id/--output-count/--repair-count/--outcome/--stop-reason`；helper读取并排序真实文件、记录逐文件摘要并计算组合`input_sha256`，不接受模型手填摘要。Provider最多尝试两次、语义repair最多一次；验证错误用可重复的 `--error-category ERROR_CODE=count` 聚合。读取 review roots、写 state/log/result、session隔离 probe和受限只读 catalog fetch自动批准并写 approval trace；目标树写入、依赖安装/发布、凭据/破坏性或无关外部副作用机械拒绝，比赛中不产生人工等待。

主要checkpoint逐字使用：`architecture_mapping/orchestrator`、`design_inventory/spec-analyst`、`code_risk_backtracking/risk-explorer`、`design_claim_resolution/spec-analyst`、`design_claim_review/spec-critic`、`investigation_planning/orchestrator`、`investigation/code-investigator`、`critic_review/evidence-critic`、`coverage_audit/coverage-critic`、`final_judgement/final-judge`；有probe时再加`dynamic_probe/code-investigator`。Complete checkpoint必须`output_count>0`。每个risk sweep用`sweep_id`、每个investigation用`task_id`、每个probe/critic用`finding_id`填写`--task-id`，且每个candidate使用独立fresh provider session。

## 3. Canonical artifacts

### 3.1 `architecture_map.json`

```json
{
  "session_id": "session-...",
  "repository_summary": "仓库职责与实际执行模型",
  "languages": ["从当前仓证据识别"],
  "entrypoints": [{"path":"...","purpose":"...","evidence":"..."}],
  "subsystems": [{"subsystem_id":"SUBSYSTEM-...","name":"...","paths":["..."],"role":"..."}],
  "implementation_planes": [{
    "plane_id":"PLANE-...",
    "kind":"owned|adapter|imported|generated|fast_path|slow_path|other",
    "paths":["..."],
    "reachable_evidence":"真实入口/调用/构建证据"
  }],
  "integration_boundaries": [{
    "boundary_id":"BOUNDARY-...","name":"...","paths":["..."],
    "plane_ids":["PLANE-..."],"risk":"high|medium|low","why":"..."
  }],
  "capability_surfaces": [{"surface_id":"CAPABILITY-...","paths":["..."],"declares_or_registers":"..."}],
  "configuration_surfaces": [{"path":"...","controls":"..."}],
  "alternate_execution_paths": [{"name":"...","paths":["..."],"trigger":"..."}],
  "test_surfaces": [{"path":"...","coverage":"...","available_command":"仓库已有命令或空字符串","evidence":"..."}],
  "probe_capabilities": {"isolated_copy_feasible":true,"available_runtime":[],"constraints":[]},
  "parallel_behavior_paths": [{"path_id":"PARALLEL-...","behavior":"同一设计行为","plane_ids":["PLANE-A","PLANE-B"],"evidence":"..."}]
}
```

Plane 是有真实可达入口且可分配调查上下文的行为 facet；不得用仓库父目录把多个独立行为粘为一个 plane，也不得拆开真正耦合路径。Boundary 的 `plane_ids` 非空。Parallel path 至少两个 plane。

### 3.2 `risk_sweep_plan.json` 与 `risk_observations.jsonl`

Plan 绑定当前 architecture digest，并让三类 ID 在至少一个真实非空 focused slice中精确覆盖、互不重叠。每个 slice只取与其互斥范围相关的非空合法 lens子集，不要求所有 slice重复完整 lens portfolio。单一不可拆 component使用一个 slice；多个 slices按顺序每批最多并发两个 Task：

```json
{
  "session_id":"session-...",
  "plan_id":"RISK-PLAN-001",
  "architecture_map_sha256":"64位小写SHA-256",
  "required_coverage":{
    "boundary_ids":["全部 BOUNDARY-..."],
    "plane_ids":["全部 reachable PLANE-..."],
    "parallel_path_ids":["全部 PARALLEL-..."]
  },
  "slices":[{
    "sweep_id":"RISK-SWEEP-01",
    "architecture_boundaries":["BOUNDARY-..."],
    "implementation_planes":["PLANE-..."],
    "parallel_path_ids":["PARALLEL-..."],
    "anchor_paths":["assigned architecture paths 的完整并集"],
    "review_lenses":["与本 slice 范围相关的非空 contract lens 子集"],
    "scope_rationale":"为什么范围互斥且内部耦合"
  }]
}
```

Risk observation 是 code-only 中性事实：

```json
{
  "observation_id":"RISK-...","session_id":"session-...",
  "sweep_id":"RISK-SWEEP-...","risk_sweep_plan_sha256":"当前plan SHA-256",
  "behavior_question":"需要设计回答的中性语义问题",
  "observed_code_behavior":"代码可证明的实际行为",
  "review_lenses":["1-3个contract lens"],
  "architecture_boundaries":["BOUNDARY-..."],
  "implementation_planes":["PLANE-..."],
  "parallel_path_ids":["PARALLEL-..."],
  "code_evidence":[{"file":"相对路径","line_start":1,"line_end":2,"symbol":"...","snippet":"逐字代码"}],
  "false_positive_checks":[{"question":"...","method":"...","target":"...","result":"..."}],
  "design_lookup_questions":["不包含代码路径/实现答案的设计问题"],
  "tool_trace":[{"seq":1,"kind":"code_search|code_navigation|code_read|reverse_check|config_read|build_read|analysis","tool":"...","target":"...","purpose":"...","result":"..."}]
}
```

每项至少两项 false-positive check；trace 至少含 search/navigation、code_read、reverse_check，且不得有 `design_read`。Observation 不含 claim、design evidence、assessment、status、recommendation 或 confidence。

每个 slice使用独占`${STATE_ROOT}/handoffs/risks/<sweep_id>/`，通过 self-check后立即只以该目录执行`handoff_merge.py --artifact-type risk`；失败peer目录不得进入本次input。Merge按 `sweep_id` 累计 upsert，当前 sweep只替换自身旧 observation；`submitted_sweep_ids`是本次单独提交项，`completed_sweep_ids`是累计ledger。`closed=false` 时已完成 observation已经可用于 frontier。最终 report必须满足 `completed_sweep_ids=expected_sweep_ids`、无 missing、`closed=true` 且 `global_coverage_validated=true`。

### 3.3 `design_inventory.json`

Spec Analyst 原始输入的每个 source对象必须显式包含 nested `source_ref.path/line_start/line_end`；materializer不接受 top-level path/lines fallback。Materializer生成 canonical top-level path/lines、source hash、quote、heading与group digest。最终格式：

```json
{
  "session_id":"session-...",
  "document_groups":[{
    "document_key":"manifest中的稳定ID",
    "members":["与manifest逐值相同的相对路径"],
    "scope_relation":"required|in_scope|relevant|informational|superseded|ambiguous",
    "scope_evidence":{
      "source_ref":{"path":"...","line_start":1,"line_end":2,"source_sha256":"..."},
      "path":"...","line_start":1,"line_end":2,"section":"materialized heading","quote":"逐字原文"
    },
    "sections":[{
      "section_id":"SECTION-...",
      "source_ref":{"path":"...","line_start":10,"line_end":80,"source_sha256":"..."},
      "path":"...","heading":"materialized heading","line_start":10,"line_end":80,
      "behavior_families":["模型按当前设计语义归纳的行为簇"],
      "ambiguities":[]
    }],
    "group_sha256":"排除本字段后的canonical JSON SHA-256"
  }]
}
```

Inventory 必须覆盖每个 manifest group，但不生成完整义务队列。Catalog link 只是 provenance；`required/in_scope` 与 capability commitment 需要 supplied design 的正面 scope evidence。

### 3.4 `design_lookup_requests.jsonl`、`design_coverage.json` 与 `design_claims.jsonl`

Lookup request 是 manager 给 design-only Agent 的最小问题：

```json
{
  "request_id":"LOOKUP-...","session_id":"session-...",
  "origin":"risk_observation|design_section|capability_reconciliation|critic_request",
  "origin_id":"稳定ID","document_keys":["..."],"section_ids":["..."],
  "question":"只描述待解析的设计语义","required_branch":"一个可证伪分支"
}
```

`design_coverage.json` 是 inventory 到已物化 claims 的轻量账本；未物化 section 是 gap，不是错误：

```json
{
  "session_id":"session-...",
  "document_groups":[{
    "document_key":"...","members":["..."],
    "disposition":"applicable|inapplicable|superseded|supporting",
    "evidence":"supplied design scope证据",
    "claim_ids":["当前已物化CLAIM-..."],
    "behavior_families":["inventory中已探索/待探索行为簇"]
  }]
}
```

Claim raw handoff 不写 quote/hash/section/path compatibility 字段；materializer 生成后最终每行为：

```json
{
  "claim_id":"CLAIM-稳定ID","session_id":"session-...","document_key":"...",
  "source_ref":{"path":"设计根相对路径","line_start":1,"line_end":3,"source_sha256":"materialized"},
  "document":"materialized filename","path":"materialized path","section":"materialized heading",
  "line_start":1,"line_end":3,"quote":"materialized exact lines",
  "subject":"义务主体","trigger":"单一触发条件","obligation":"单一义务分支",
  "exceptions":["设计明确例外"],"observable_result":"可观察结果",
  "normative_strength":"mandatory|recommended|optional|declared_capability|informational",
  "applicability":"当前组件/版本/场景为何适用",
  "ambiguities":[],
  "probe_oracle":{
    "testability":"candidate|not_suitable|unknown",
    "preconditions":["仅来自设计"],
    "stimulus":"candidate/unknown时的最小输入",
    "expected_observation":"candidate/unknown时的设计结果",
    "non_testable_reason":"not_suitable时必填"
  }
}
```

每个 claim只表达一个 subject、trigger、obligation branch，不写 `behavior_family`；行为簇只存在于 inventory sections。每个 raw claim必须有 nested `source_ref`，不能用最终 materialized top-level path/lines作为draft输入。`probe_oracle` 只在按需 claim中生成；`candidate|unknown` 的 preconditions至少一项，`not_suitable` 不影响静态调查。

### 3.5 `claim_review_scope.json` 与 `design_claim_review.json`

Scope 是当前增量批次，不绑定 whole-file claims digest：

```json
{"session_id":"session-...","round_id":"ROUND-...","claim_ids":["CLAIM-..."]}
```

Fresh Spec Critic 输出：

```json
{
  "session_id":"session-...","summary":"本批设计审查摘要",
  "input_digests":{
    "design_claims.jsonl":"审计用","design_coverage.json":"审计用",
    "design_inventory.json":"审计用","design_agent_manifest.json":"审计用",
    "claim_review_scope.json":"审计用"
  },
  "claim_reviews":[{
    "claim_id":"CLAIM-...","session_id":"session-...",
    "claim_sha256":"该claim canonical JSON SHA-256",
    "source_sha256":"claim.source_ref.source_sha256",
    "spec_critic_prompt_version":"spec-critic-v2",
    "quote_entailment":{"assessment":"entailed|not_entailed|ambiguous","rationale":"..."},
    "normative_strength":{
      "assessment":"correct|incorrect|ambiguous",
      "stated_strength":"mandatory|recommended|optional|declared_capability|informational",
      "recommended_strength":"上述值之一|undetermined","rationale":"..."
    },
    "atomicity":{"assessment":"atomic|bundled|ambiguous","obligations":["识别出的义务"],"rationale":"..."},
    "applicability":{"assessment":"supported|unsupported|ambiguous","rationale":"..."},
    "decision":"accept|repair","repair_actions":[]
  }],
  "group_reviews":[],
  "decision":"accept|repair"
}
```

`group_reviews` 可省略或为空，默认不做全组完整性证明。只有审查当前 scoped claim时发现会影响其语义的具体 group gap，或有原文证据支持一个 coverage expansion，才增加对应对象：

```json
{
    "document_key":"...","session_id":"session-...","group_sha256":"inventory group_sha256",
    "behavior_families":{"assessment":"complete|gaps_found|ambiguous","rationale":"...","missing_items":[]},
    "roles":{"assessment":"complete|gaps_found|ambiguous","rationale":"...","missing_items":[]},
    "branches":{"assessment":"complete|gaps_found|ambiguous","rationale":"...","missing_items":[]},
    "decision":"accept|repair","repair_actions":[]
}
```

每个 `missing_items[]`：

```json
{"description":"...","path":"...","section":"...","line_start":1,"line_end":2,"quote":"...","why_independent":"...","affected_claim_ids":[]}
```

不得提交三个 dimension都 `complete` 的例行 group review；至少一个 dimension必须有具体 missing item。Group gap默认是 non-blocking expansion signal；`affected_claim_ids=[]` 时 group decision仍 accept。只有 gap改变 scoped claim的适用性、原子性或含义时列出该 claim，并让对应 claim review repair。无关 claim或未审全组的变化不得使已接受 per-claim review失效。

### 3.6 `investigation_tasks.jsonl` 与 `investigation_rounds.jsonl`

一个 task = 一个 accepted claim 的一个 branch + 一个 falsifiable hypothesis：

```json
{
  "task_id":"TASK-...","session_id":"session-...","claim_id":"CLAIM-...",
  "claim_branch":"该claim的一个独立行为分支",
  "hypothesis":"实现与该义务可能存在何种可证伪差异",
  "obligation_sha256":"canonical SHA-256({claim_id,obligation})",
  "starting_points":["真实入口/边界"],
  "supporting_evidence_needed":["什么会支持假设"],
  "disconfirming_evidence_needed":["什么会推翻假设"],
  "review_lenses":["1-3个contract lens"],
  "exploration_mode":"contract中的完整mode字符串",
  "architecture_boundaries":["BOUNDARY-..."],
  "implementation_planes":["PLANE-..."],
  "parallel_path_ids":["PARALLEL-..."],
  "risk_observation_ids":["RISK-..."],
  "status":"pending|in_progress|complete|deferred",
  "defer_reason":"仅deferred时",
  "defer_evidence":{"kind":"provider_failure|tool_failure","attempts":[{"attempt_id":"...","outcome":"failed","evidence":"..."}]}
}
```

初始task不得写`coverage_request_sha256/source_gap_ids`。唯一supplement的实际task必须使用新ID/new round，逐值匹配history中的`task_specs`，并复制`source_gap_ids`和`coverage_request_sha256=<request_sha256>`；task-plan snapshot包含history，缺失、扩张或篡改绑定会使gate stale/失败。

`obligation_sha256` 是 UTF-8 canonical JSON（sort_keys、无空格）`{"claim_id":...,"obligation":...}` 的小写 SHA-256。旧 `question` 字段被直接拒绝，即使值等于 `hypothesis` 也不能出现。Task plan验证忽略 lifecycle字段；lifecycle单独验证 status/finding。

Round：

```json
{
  "round_id":"ROUND-001","session_id":"session-...","strategy":"证据策略",
  "exploration_modes":["..."],"document_groups":["..."],
  "architecture_boundaries":["BOUNDARY-..."],"implementation_planes":["PLANE-..."],
  "lenses":["..."],"claim_ids":["CLAIM-..."],"task_ids":["TASK-..."],
  "finding_ids":[],"outcome":"","next_strategy":""
}
```

每轮最多 4 task；同一 task恰属一轮。Lifecycle 更新只改 `status/defer_*` 及 round 的 `finding_ids/outcome/next_strategy`。

Task plan/lifecycle validator允许 candidate-local继续：两份 trace都必须 `global_passed=true`，且目标 task在各自 `valid_task_ids`；另一个 `invalid_task_ids` 不阻塞。每个 investigator使用独立 `${STATE_ROOT}/handoffs/investigators/<TASK_ID>/`，self-check通过后单 candidate原子 merge并只刷新其 lifecycle。并发 peer失败不得形成 batch-global barrier；final gate仍要求所有 task最终合法完成或有两次 provider/tool failure的结构化 deferred证据。

### 3.7 `investigation_findings.jsonl`

```json
{
  "finding_id":"FINDING-...","session_id":"session-...","task_id":"TASK-...","claim_id":"CLAIM-...",
  "hypothesis":"逐值复制task hypothesis","expected_behavior":"从claim逐值生成的模板字段",
  "observed_behavior":"代码/配置可证明的实际行为",
  "design_evidence":[{"document":"...","path":"...","section":"...","line_start":1,"line_end":2,"quote":"逐字原文"}],
  "code_evidence":[{"file":"...","line_start":1,"line_end":2,"symbol":"...","snippet":"逐字代码"}],
  "supporting_evidence":["支持假设的事实"],"disconfirming_evidence":["反证或限制"],
  "false_positive_checks":[{"question":"...","method":"...","target":"...","result":"..."}],
  "tool_trace":[{"seq":1,"kind":"design_read|code_search|code_navigation|code_read|reverse_check|test|config_read|build_read|analysis","tool":"...","target":"...","purpose":"...","result":"..."}],
  "dynamic_probe_selection":{"disposition":"selected|not_selected|not_suitable|environment_limited","reason":"..."},
  "assessment":"contradiction_supported|uncertain|design_satisfied",
  "review_lenses":["task lenses的子集"],
  "recommendation":"critic_review|probable|reject"
}
```

至少两项 candidate-specific false-positive check；trace 至少含 design_read、search/navigation、code_read、reverse_check。缺能力不能只靠全仓无命中，必须对账入口、构建、注册、配置、邻近能力和替代实现。

### 3.8 `dynamic_probes.jsonl`

只为 finding 中 selected 的最小单点 probe 写一行：

```json
{
  "probe_id":"PROBE-...","session_id":"session-...","finding_id":"FINDING-...","claim_id":"CLAIM-...",
  "oracle":{
    "source":"design_claim","claim_id":"CLAIM-...","claim_sha256":"当前claim canonical SHA-256",
    "source_sha256":"claim.source_ref.source_sha256",
    "preconditions":["逐值复制claim.probe_oracle"],
    "stimulus":"逐值复制claim.probe_oracle","expected_observation":"逐值复制claim.probe_oracle"
  },
  "oracle_validation":{
    "non_triviality":{"status":"passed|failed|not_run","method":"执行时必填","result":"..."},
    "secondary_oracle":{
      "kind":"reference_model|minimal_reference|known_good_path|negative_control|not_available",
      "status":"passed|failed|not_run","command":"可执行时必填","result":"..."
    },
    "evidence_role":"corroborating|auxiliary"
  },
  "selection_reason":"...",
  "isolation":{"kind":"session_copy","workspace":"state/probes下路径","command_cwd":"与workspace相同的绝对路径","original_target_unchanged":true},
  "baseline":{"status":"passed|failed|not_available","command":"执行时必填","result":"..."},
  "execution":{"status":"completed|environment_failed|not_executed","command":"完成时必填","exit_code":0,"observed":"...","target_reached":true},
  "interpretation":"supports_contradiction|disconfirms_contradiction|inconclusive",
  "limitations":[],
  "tool_trace":[{"seq":1,"kind":"build_read|test|analysis","tool":"...","target":"...","purpose":"...","result":"..."}]
}
```

Workspace 必须位于 `${STATE_ROOT}/probes`。Non-triviality 未 passed、baseline 未 passed、execution 未 completed 或 target 未 reached 时只能 inconclusive。可行时 second oracle 必须执行；不可得时 `kind=not_available,status=not_run,evidence_role=auxiliary`，不能让 probe 单独决定 verdict。

### 3.9 `critic_reviews.jsonl`

```json
{
  "review_id":"CRITIC-...","session_id":"session-...","finding_id":"FINDING-...","claim_id":"CLAIM-...",
  "decision":"confirm_contradiction|reject_issue|needs_more_evidence",
  "challenges":["至少两项具体替代解释"],
  "checks_performed":["至少两项critic实际执行的检查及结果"],
  "dynamic_probe_review":{
    "status":"not_run|supports_contradiction|disconfirms_contradiction|inconclusive",
    "probe_id":"PROBE-...或空字符串","oracle_validity":"...","environment_validity":"...",
    "reachability":"...","effect_on_decision":"..."
  },
  "review_context":"fresh_subagent","resolution":"挑战如何被解决或未解决","remaining_risks":[]
}
```

Critic raw handoff只含这些模型字段。相同 finding/evidence 只有一个当前 review；新 evidence才允许 revision。`needs_more_evidence` 必须给具体可执行问题。Self-check/merge另行确定性写入 `input_digests.claim_sha256/finding_sha256/probe_sha256` 和 `evidence_critic_prompt_version=evidence-critic-v2`；模型不得手填，任一输入摘要变化后旧 critic机械失效并需要 fresh复审。

`${STATE_ROOT}/critic_review_history.jsonl` 是 prepare/critic merge专有的只读历史账本，任何Agent不得创建、清空、删除或编辑。相同 evidence review key不能因当前critic ledger删除而改投；只有真实的新claim/finding/probe摘要才允许追加revision，resume缺失历史直接失败。

### 3.10 `semantic_coverage.json` 与 `coverage_audit.json`

Coverage 记录组合缺口，不审批已接受 candidate：

```json
{
  "session_id":"session-...",
  "lenses":[{
    "lens":"contract中的完整lens",
    "disposition":"investigated|inapplicable|gap_recorded",
    "evidence":"...","task_ids":["TASK-..."],"finding_ids":["FINDING-..."],
    "design_group_refs":["document_key"],"boundary_refs":["BOUNDARY-..."],
    "counterfactual":"inapplicable/gap的判断依据"
  }]
}
```

```json
{
  "session_id":"session-...",
  "design_documents_reviewed":["相对路径"],"claims_total":0,"claims_investigated":0,
  "rounds_completed":0,"exploration_modes_completed":["实际执行的mode"],
  "document_groups_total":0,"document_groups_accounted":0,
  "code_areas_reviewed":["..."],
  "architecture_boundaries":[{"boundary_id":"...","status":"investigated|gap_recorded","evidence":"..."}],
  "remaining_scoped_claims":[{"claim_id":"...","reason":"..."}],
  "deferred_claims":[{"claim_id":"...","task_id":"...","reason":"..."}],
  "false_positive_samples_rechecked":["FINDING-..."],
  "supplement_rounds":0,
  "remaining_gaps":[{
    "gap_id":"GAP-...",
    "kind":"inventory|claim_review_expansion|lens|architecture_boundary|parallel_path|exploration_mode|frontier_claim|critic_request|other",
    "ref_id":"对应稳定ID或相对路径","reason":"具体缺什么证据","evidence":"当前已知的缺口依据"
  }],
  "next_round_tasks":[{
    "claim_id":"CLAIM-...","claim_branch":"...","hypothesis":"...","obligation_sha256":"...",
    "exploration_mode":"contract mode","review_lenses":["1-3 lens"],
    "architecture_boundaries":["BOUNDARY-..."],"implementation_planes":["PLANE-..."],
    "parallel_path_ids":["PARALLEL-..."],"risk_observation_ids":["RISK-..."],
    "source_gap_ids":["当前remaining_gaps中的GAP-..."],
    "priority_reason":"该task填补哪个具体gap"
  }],
  "stop_reason":"为何补扫一次或为何剩余gap只记录"
}
```

`supplement_rounds` 只能 0/1。每个next task的非空`source_gap_ids`必须逐值引用本次`remaining_gaps`，不能只在自然语言中声称有gap。`coverage_supplement_history.json`由prepare/coverage validator独占写入，Agent只读：首次有效非空任务集合被原子记录，同请求重放幂等，不同或第二请求拒绝。初始 frontier 后最多一次 supplement；完成后写`supplement_rounds=1,next_round_tasks=[]`，已解决gap可移除但history保留。`closed=true` 要求当前 accepted frontier 的 `remaining_scoped_claims=[]`、无 pending/in_progress task、supplement 决策完成且 `next_round_tasks=[]`；允许 scope 外的 `remaining_gaps`、`gap_recorded` lens/boundary 存在。

Gap `kind/ref_id` 逐值绑定真实对象：claim review gap用 `claim_review_expansion + expansion_request_id`，lens/boundary/parallel path/mode/frontier/critic分别用对应稳定 ID，文档组/section用 `inventory`。`gap_recorded` lens不引用 task/finding；未调查 high-risk boundary或parallel path必须由对应 gap记账。

### 3.11 `agent_review_verdicts.jsonl`

Confirmed/probable：

```json
{
  "finding_id":"FINDING-...","session_id":"session-...","claim_id":"CLAIM-...",
  "status":"confirmed|probable","title":"事实标题","confidence":0.9,
  "severity":"critical|high|medium|low",
  "issue_type":"missing_behavior|contradictory_behavior|partial_implementation|wrong_boundary|invalid_state_transition|data_contract_mismatch|other",
  "design_evidence":[],"code_evidence":[],
  "expected_behavior":"逐值复制finding","actual_behavior":"逐值复制finding.observed_behavior",
  "inconsistency":"设计与实现如何冲突","impact":"触发条件与功能影响",
  "scope_applicability":"为何当前输入适用",
  "false_positive_checks":[],
  "dynamic_validation":{"status":"not_run|supports_contradiction|disconfirms_contradiction|inconclusive","probe_id":"或空","reason":"..."},
  "critic_review":{"review_id":"CRITIC-...","decision":"...","challenges":[],"resolution":"...","review_context":"fresh_subagent"},
  "tool_trace":[],"generalization_rationale":"结论只来自当前输入","agent_notes":"可选"
}
```

Rejected 至少为：

```json
{"finding_id":"FINDING-...","session_id":"session-...","status":"rejected","rejection_reason":"具体原因"}
```

每个 finding（含 `design_satisfied`）都必须在coverage前完成一次fresh critic；合规 finding也需要`reject_issue`独立复核。Final Judge为每个finding生成一条current/latest verdict；ledger可保留evidence-repair前的revision，但只取最新一条生效。`confirm_contradiction`只可映射confirmed，`reject_issue`映射rejected，只有`needs_more_evidence`可映射probable。Confirmed/probable的design/code evidence、expected/actual、false-positive checks与tool trace必须逐值复制finding，critic review逐值复制critic；不得在judge阶段生成新证据。最终报告只发布confirmed。

## 4. Evidence-pair 与判断标准

一个 frontier item 必须有：一个设计义务的一个 branch、一个具体 code risk/capability gap、一个 boundary/明确 plane 与一个 falsifiable hypothesis。不得把整个协议、服务、模块或多个状态分支打进一个 task。

Investigator 必须证明实际入口、调用链、配置/构建与可达行为；检查平行实现、dead code、条件编译、feature flag、adapter 和外部依赖。集合/容量要追终止与溢出行为；链/嵌套要追每次推进；时序要区分同步、延迟、重试和主动副作用；能力缺失要对账构建、注册、入口、配置和邻近能力；边界行为要追到最终 consumer。

Fresh Critic 尽力推翻 candidate：检查 design scope/版本、替代实现、配置补偿、可达性、生成/导入代码、probe oracle 和环境。MUST/SHOULD/MAY/声明能力按真实强度表达；可选或推荐差异不能虚构为强制违规。Catalog provenance 不自动成为 capability promise。

Confirmed 必须同时具备：可验证且适用的设计证据、可验证并证明可达行为/能力缺失的代码证据、明确 expected/actual 矛盾、至少两项候选特定反证排除、investigator/critic闭环、全部 identity/digest/session 一致。Probe 可缺省；probe failure 永远不能单独确认。

## 5. Loop 与时间

- 约 15–25 分钟：input/snapshot、inventory、architecture。
- 约 30–45 分钟：disjoint breadth exploration 与首批 evidence pairs。
- 约 45–75 分钟：首批 claim/investigator/critic，目标 90 分钟内出现首个闭环 candidate。
- 约 60–120 分钟：后续 candidates 与最多一次 coverage supplement。
- 预留 30–45 分钟：final judge、review、report、gate。
- 5.5 小时停止启动预计无法闭环的新 candidate；硬上限 6 小时。

Coverage 不能因数量目标创建 task。未覆盖 inventory/section/lens/boundary 可以作为 `remaining_gaps` 保留；已完成 candidate 不受无关 gap 阻塞。全文件 digest 只用于审计与最终完整性，不级联否定已接受的 per-claim review 或稳定 task plan。
