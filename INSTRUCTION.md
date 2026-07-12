# Goal-Agent 比赛运行入口

你是评测平台正在运行的 OpenCode CLI 主 Agent。目标是对本次提供的任意设计/RFC 文档和任意代码仓执行语义一致性检视，输出代码相对于设计的不一致列表与证据链。不要修改目标代码或 supplied design。

这不是漏洞扫描。安全约束只是可能的设计语义之一；应覆盖行为、状态、时序、容量、遍历、能力、配置、错误、路由/所有权和并行实现等所有设计维度。

禁止使用项目名、协议名、固定路径/符号、regex规则、关键词命中、已知 issue或公开答案作为检测/verdict逻辑。允许并且必须使用从当前输入动态读出的领域术语导航。系统必须由模型读取设计、探索代码、提出候选、调查和反证；helper只负责路径、schema、hash、provenance和状态机。

一次启动后无人值守运行，直到 final gate通过或 6 小时硬截止。不得询问用户、等待人工审批、创建/读取`opencode.json`、切换规则 fallback，或手工填写运行参数。局部 agent完成不等于全局完成。

## 1. 固定路径与只读边界

```bash
SUBMISSION_ROOT=<INSTRUCTION.md 所在目录>
WORK_ROOT=${SUBMISSION_ROOT}/work
ASSET_ROOT=/app/code/judge-assets/01_03_ai_implementation_design_difference_detection
RESULT_ROOT=${SUBMISSION_ROOT}/result
LOG_ROOT=${SUBMISSION_ROOT}/logs
STATE_ROOT=${LOG_ROOT}/state
```

立即启动唯一 wall clock：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py start-clock \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

从 `${ASSET_ROOT}/code` 自动发现唯一代码项目目录作为 `CODE_ROOT`；从 `${ASSET_ROOT}` 中除 `code` 外的唯一设计目录作为初始 `DESIGN_ROOT`。若某侧有多个候选，主 Agent阅读 README/入口/目录事实后自主选择，不按已知项目类型选择。

运行期允许写入的范围只有 `${STATE_ROOT}`、`${LOG_ROOT}`、`${RESULT_ROOT}`。模型角色只读 prepare生成的 `REVIEW_CODE_ROOT` 与 `REVIEW_DESIGN_ROOT`；deterministic validator仍使用原始 `CODE_ROOT`/`DESIGN_ROOT`验真。禁止依赖安装、发布、凭据访问和对目标树的任何写操作。

## 2. 设计来源与 prepare

若 supplied design 已包含正文，直接 prepare。若只有 catalog/链接清单，主 Agent只根据 catalog逐字出现的 local path 或 HTTPS URL写 `${STATE_ROOT}/design_source_plan.json`：

```json
{
  "catalog_path":"相对设计目录的入口文件",
  "sources":[{
    "source_id":"稳定ID",
    "kind":"local|url",
    "location":"catalog 中逐字出现的本地相对路径或 HTTPS URL",
    "output_path":"sources/稳定文件名.txt",
    "catalog_evidence":{"path":"catalog相对路径","line_start":1,"line_end":1,"quote":"逐字原文"}
  }]
}
```

只物化 catalog 明确列出的来源：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --source-root <catalog所在目录> \
  --plan ${STATE_ROOT}/design_source_plan.json \
  --output-root ${STATE_ROOT}/design-sources \
  --manifest ${LOG_ROOT}/trace/design_source_materialization.json \
  --approval-log ${STATE_ROOT}/approval_events.jsonl --allow-network
```

成功后令 `DESIGN_ROOT=${STATE_ROOT}/design-sources`，并运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py prepare \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT} \
  --source-manifest ${LOG_ROOT}/trace/design_source_materialization.json
```

正文一开始就在 supplied design目录时，运行同一命令但不传 `--source-manifest`。

Prepare后完整读取：

- `${WORK_ROOT}/skill/SKILL.md`
- `${WORK_ROOT}/skills/orchestrator.md`
- `${STATE_ROOT}/agent_context.json`
- `${STATE_ROOT}/agent_loop_contract.json`
- `${STATE_ROOT}/agent_loop_state.json`

不要把可能很大的 `workspace_manifest.json` 读进模型上下文。从 `agent_context.json` 取得 `SESSION_ID`、`REVIEW_CODE_ROOT` 和 `REVIEW_DESIGN_ROOT`。

## 3. 运行架构

最多并发两个 fresh subagent。并发范围必须互斥：不同 design document ownership、不同 primary code anchors或不同 candidate。同一 candidate 的 investigator → optional probe → critic严格串行。每个 agent写独立 handoff，只有helper能merge共享ledger。

每次决定下一步前执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/pipeline_controller.py status --state-root ${STATE_ROOT}
```

控制器顺序为：

```text
finish_scouts → select_candidates → review_claims → plan_investigations
→ finish_investigations → finish_critics → run_final
```

不得跳过前置条件。每个语义 phase和candidate用 `session_event.py` 写 rich checkpoint，绑定真实输入/输出文件、时间、provider session、scope ID、输出数和repair数。每个 fresh角色最多两个provider attempt、最多一次语义repair。相同输入/artifact/error连续两次无进展后不要第三次原样重跑。只有 final gate可把全局state标为complete。

## 4. 轻量地图与确定性索引

主 Agent从 `REVIEW_CODE_ROOT` 建立 `${STATE_ROOT}/architecture_map.json`，遵循 `work/skill/SKILL.md` schema。地图覆盖真实入口、subsystem、owned/imported/adapter/generated/fast/slow plane、integration boundary、配置/能力/构建/测试 surface和parallel paths；不判断一致性，也不能限制 design-to-code 全仓搜索。

运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py architecture-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --materialize auto-inventory --design-root ${REVIEW_DESIGN_ROOT} \
  --input ${STATE_ROOT}/design_agent_manifest.json \
  --output ${STATE_ROOT}/design_inventory.json \
  --trace ${LOG_ROOT}/trace/design_inventory_materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py inventory-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/scout_plan_builder.py --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py risk-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Inventory和scout plan不由模型手写。Plan会把全部 in-scope design groups分配给互斥的 design-to-code scouts，并把架构代码按不重叠 top-level ownership分配给 code-to-design scouts。

## 5. 双向 Semantic Scouts

按 plan最多并发两个 fresh `risk-explorer`，完整遵循 `${WORK_ROOT}/skills/risk-explorer.md`：

- design-to-code scout独占文档组，逐段提炼可观察要求，并可搜索整个代码仓；
- code-to-design scout独占primary code anchors，并可从完整design inventory动态检索规范；
- 只输出 direct conflict、结构化能力缺失、cross-plane mismatch或有证据的uncertain；
- 合规实现不输出，零候选合法。

非空 `<SWEEP_ID>.json` 先check再merge：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --artifact-type risk --session-id ${SESSION_ID} --code-root ${REVIEW_CODE_ROOT} \
  --report ${LOG_ROOT}/trace/risk-check-${SWEEP_ID}.json
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/risks/${SWEEP_ID} \
  --output ${STATE_ROOT}/risk_observations.jsonl \
  --artifact-type risk --session-id ${SESSION_ID} --code-root ${CODE_ROOT} \
  --report ${LOG_ROOT}/trace/risk-merge-${SWEEP_ID}.json
```

然后每个scout（包括空数组）都记录receipt：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  [--check-report ${LOG_ROOT}/trace/risk-check-${SWEEP_ID}.json]
```

每个scout写 `code_risk_backtracking/risk-explorer` complete checkpoint；scope/task ID等于SWEEP_ID。全部receipts完成前禁止选择候选。

## 6. 候选、规范审查与任务

主 Agent阅读全部已验证 observations，只写 `${STATE_ROOT}/candidate_selection.json`：

```json
{"candidate_ids":["按证据强度排序的 observation_id，最多12个"]}
```

优先规范直接冲突、外部可观察行为、跨plane不对称、结构化能力缺失、精确代码位置和反证后的信息增益。不做文档配额，不选择合规样本，不重写候选事实。

执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py select \
  --state-root ${STATE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --selection ${STATE_ROOT}/candidate_selection.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

启动一个 fresh `spec-critic`，只读 scoped claims和设计原文，遵循 `${WORK_ROOT}/skills/spec-critic.md` 写最小语义审查，并用 `claim_review_materializer.py` 生成完整review后运行 claim-check。不要重新运行spec analyst；claim已由candidate逐值物化。规范语义需repair时最多修一次源候选/claim，不能在task中改问题。

全部 claims接受后：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py plan --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Task的claim、hypothesis、direction、code starting points和候选ID均由helper冻结，主 Agent不得另写plan handoff。

## 7. 调查与独立反证

按 round顺序每批最多两个不同candidate。每项：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_template.py \
  --tasks ${STATE_ROOT}/investigation_tasks.jsonl \
  --claims ${STATE_ROOT}/design_claims.jsonl --task-id ${TASK_ID} \
  --output ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json --force
```

Fresh investigator遵循 `${WORK_ROOT}/skills/code-investigator.md`，只写最小semantic JSON，再运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/finding_materializer.py \
  --input ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.semantic.json \
  --template ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json \
  --code-root ${REVIEW_CODE_ROOT} \
  --output ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --trace ${LOG_ROOT}/trace/finding-materialize-${TASK_ID}.json
```

完整finding通过 `handoff_merge.py --check-file --artifact-type finding` 后，仅merge该candidate目录；注意semantic input不能留在merge目录，可放candidate子目录外或merge前移至trace目录。Merge会更新task lifecycle。

必要且有design-derived oracle时可做一个隔离单点probe；不做全量测试。无论是否probe，每个finding都启动fresh evidence critic，遵循 `${WORK_ROOT}/skills/evidence-critic.md`，check并merge critic。不要使用多个critic投票。

## 8. Coverage、Final Judge 与输出

全部 accepted tasks都有finding和critic后，使用确定性coverage materializer记录已有直接覆盖和具体gap；不创建supplement、不为数量新增任务：

```bash
python3 ${WORK_ROOT}/tools/scripts/coverage_materializer.py \
  --state-root ${STATE_ROOT} --trace ${LOG_ROOT}/trace/coverage-materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py coverage-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

若materializer报告尚有unfinished task，返回对应candidate，不得伪造deferred。

启动fresh Final Judge，遵循 `${WORK_ROOT}/skills/final-judge.md`，为每个finding生成一个current verdict并运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py review \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

写 `final_judgement/final-judge` checkpoint后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py finalize \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

必须最终生成：

```text
/result/issues.json
/result/issues.jsonl
/result/00-summary.md
/result/01-*.md
```

每个issue必须包含差异描述、设计/RFC原文证据和章节、代码行为证据和文件行号、差异原因、误报排除和置信度。Confirmed只来自设计适用、代码可达、expected/actual冲突和fresh critic反证均闭环的finding；证据不足用probable或rejected。

若gate失败，只修trace指出的最早真实artifact并重跑受影响步骤。不得重新进行已完成的全量探索，不得通过改status、删ledger、降低证据标准或填充issue数量绕过gate。
