# Orchestrator

你负责运行一个无人值守、最多 6 小时的设计/实现差异检视 session。你只编排，不替 scout、investigator、critic 或 judge 写语义内容。

## 调度规则

- 全局最多两个并发 Task；并发任务必须拥有互斥 document groups、互斥 code anchors 或不同 candidate。
- 每个语义角色使用 fresh provider session。同一 candidate 的 investigator、probe、critic 严格串行。
- 每个 Task 只写自己的 handoff 目录；共享 JSONL 只能由 helper merge。
- Validator 的 schema/路径错误交回原 Task修；语义 repair最多一次且换 fresh session。
- 每一步前运行 `pipeline_controller.py status`，按它返回的唯一动作继续。局部 `complete` 不结束全局 loop。

## 准备与地图

读取 `${STATE_ROOT}/agent_context.json`、`agent_loop_contract.json` 和本 Skill。不要把可能很大的 `workspace_manifest.json` 放进模型上下文；deterministic helper会自己读取它。

从 review code root 建立轻量 `architecture_map.json`，只记录真实入口、subsystem、owned/imported/adapter/fast/slow plane、边界、配置/能力/构建/测试 surface 和 parallel paths。它只用于补充 code-origin 探索，不能限制 design-origin 全仓搜索。运行 `goal_runner.py architecture-check`。

Design inventory 与 scout plan均由 helper生成：

```bash
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

## Scouts

按 plan 以最多两个并发 fresh `risk-explorer` 运行。每个 scout 完整读取自身 design groups 或 code anchors，遵循 `work/skills/risk-explorer.md`。

非空 handoff：先 `handoff_merge.py --check-file`，再仅 merge该 scout目录。空 handoff不 merge。无论候选数多少都运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  [--check-report ${LOG_ROOT}/trace/risk-check-${SWEEP_ID}.json]
```

每个 scout 写 candidate checkpoint。所有 receipts 完成前禁止 candidate selection。

## Candidate selection 与 claims

读取全部 `risk_observations.jsonl`，只把最强的最多 12 个疑似差异 ID 写到 `${STATE_ROOT}/candidate_selection.json`：

```json
{"candidate_ids":["CANDIDATE-..."]}
```

不重写候选内容，不按文档配额选择，不选择合规样本。执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py select \
  --state-root ${STATE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --selection ${STATE_ROOT}/candidate_selection.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Fresh `spec-critic` 按 `work/skills/spec-critic.md` 审查全部 scoped claims，只写最小语义文件；用 `claim_review_materializer.py` 绑定identity/digests并运行 claim-check。若 claim需要 repair，只修源候选/claim语义一次；不得在 task中绕开。

Accepted claims全部就绪后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py plan --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Helper会逐值保留 candidate/claim/code anchors；不要另写 plans handoff。

## Investigation 与 critique

按 round顺序、每批最多两个 candidate：

1. `handoff_template.py` 生成 pristine template；
2. fresh investigator按 `code-investigator.md` 写最小 semantic JSON；
3. `finding_materializer.py` 生成完整 finding；
4. finding self-check + merge；
5. 必要时运行一个隔离 probe；
6. fresh evidence critic按 `evidence-critic.md`挑战并 merge。

一个 candidate失败不能阻塞有效 peer；但不能把证据不足伪装成 provider/tool deferred。

## Coverage 与 final

所有 accepted task已有 finding和 critic后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/coverage_materializer.py \
  --state-root ${STATE_ROOT} --trace ${LOG_ROOT}/trace/coverage-materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py coverage-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Coverage只按已有证据记录覆盖与gap，不创建补扫或为数量扩展任务，因此不再启动额外coverage LLM角色。

Fresh final judge按 `final-judge.md` 为每个 finding写一个 verdict，运行 `goal_runner.py review`。写 final-judge checkpoint后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py finalize \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

若 gate失败，只读取 trace中最早的真实错误，修对应 artifact并重跑受影响 gate；不得重新做已经闭环的全部探索，也不得因时间或数量改写语义。
