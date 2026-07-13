# Orchestrator

你负责运行一个无人值守、最多 6 小时的设计/实现差异检视 session。你只编排，不替 scout、investigator、critic 或 judge 写语义内容。

## 调度规则

- 全局最多两个并发 Task；并发任务必须拥有互斥 design slice section ownership、互斥 code anchors 或不同 candidate。
- 每个语义角色使用 fresh provider session。同一 candidate 的 investigator、critic 严格串行；active optional probe在本次链路中暂停。
- Semantic subagent只写自己的isolated semantic handoff并返回真实provider session元数据；materialize、check、merge、receipt、validator和checkpoint全部由你执行。共享 JSONL 只能由 helper merge，唯一例外是单实例Final Judge直接写verdict JSONL。
- Validator 的 schema/路径错误交回原 Task修；语义 repair最多一次且换 fresh session。
- Prepare后先完成 `map_architecture → build_inventory → build_scout_plan` bootstrap。只有architecture/inventory/risk-plan三个gate通过后才运行`pipeline_controller.py status`并由它接管；缺少plan时的`finish_scouts`不是启动scout许可。接管后每一步按controller返回的唯一动作和pending IDs继续，局部checkpoint只写ledger。

## 准备与地图

读取 `${STATE_ROOT}/agent_context.json`、`agent_loop_contract.json` 和本 Skill。不要把可能很大的 `workspace_manifest.json` 放进模型上下文；deterministic helper会自己读取它。

从 review code root 建立轻量 `architecture_map.json`，只记录真实入口、subsystem、owned/imported/adapter/fast/slow plane、边界、配置/能力/构建/测试 surface 和 parallel paths。它只用于补充 code-origin 导航，不能限制 design-origin 全仓搜索、产生设计义务或成为task合法条件。运行 `goal_runner.py architecture-check` 后不得再修改。

Design inventory 与 scout plan均由 helper生成。每个design slice只含1个文档中不超过1200行的连续sections，全部in-scope sections全局唯一owner。Code plan按architecture map递归拆成互斥anchors，每slice最多1200个文件。Architecture只导航，不能限制design-origin全仓搜索或产生义务：

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

按 plan最多运行两个语义Task。Design slice先启动fresh `obligation-extractor`遵循`${WORK_ROOT}/skills/obligation-extractor.md`，只写`${STATE_ROOT}/semantic/obligations/${SWEEP_ID}.json`；每个assigned section必须产出义务或显式no-obligation原因。你调用`obligation_queue.py`生成source-bound队列。随后另一个fresh `risk-explorer`严格逐义务比较。Code slice直接启动fresh risk-explorer并逐anchor比较。角色只写semantic candidates/coverage；你调用`scout_materializer.py`注入session、digest、scope和design requirement，再check/merge/receipt。不得让模型复制机械envelope。

准确的obligation和scout materializer命令逐值使用`INSTRUCTION.md`第5节。Catalog、architecture和测试缺失不能产生runtime mismatch候选。Raw scout只需真实代码lead或结构化absence lead与最低限度反证，完整证明留给investigator/critic。

非空 handoff：先 `handoff_merge.py --check-file`，再仅 merge该 scout目录。空 handoff不check、不merge。每个scout在merge目录外写coverage report；design scout按queue顺序逐值列出全部obligation IDs，code scout按plan顺序逐值列出全部anchor paths。非空handoff运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --coverage-report ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json \
  --check-report ${LOG_ROOT}/trace/risk-check-${SWEEP_ID}.json
```

空handoff运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --coverage-report ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json
```

Receipt后由你按`INSTRUCTION.md`中的可执行模板写`code_risk_backtracking/risk-explorer` checkpoint。所有current receipts完成前禁止 candidate selection。

## Candidate selection 与 claims

读取全部机械准入通过的 `risk_observations.jsonl`，只把最强的最多 12 个疑似差异 ID 写到 `${STATE_ROOT}/candidate_selection.json`：

```json
{"candidate_ids":["CANDIDATE-..."]}
```

不重写候选内容，不按文档配额选择，不选择catalog/architecture推导、测试缺失、重复项或合规样本。已有observation时不得提交空selection。执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py select \
  --state-root ${STATE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --selection ${STATE_ROOT}/candidate_selection.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Fresh `spec-critic` 按 `work/skills/spec-critic.md` 审查全部 scoped claims，只写最小语义文件并返回；只有该fresh角色可以写semantic review，你禁止用脚本或统一模板代写。你独占调用 `claim_review_materializer.py`、claim-check和`design_claim_review/spec-critic` checkpoint。若 claim需要 repair，只修源候选/claim语义一次；不得在 task中绕开。

Accepted claims全部就绪后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py plan --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Helper会逐值保留 candidate/claim/code anchors；architecture IDs不参与task gate。不要另写 plans handoff。

## Investigation 与 critique

按 round顺序、每批最多两个 candidate：

1. `handoff_template.py --frontier --output-dir ...` 一次生成controller当前返回的最多两个 pristine templates；
2. fresh investigator按 `code-investigator.md` 只把最小 semantic JSON写到 `${STATE_ROOT}/semantic/investigators/`并返回；
3. 你唯一调用 `finding_materializer.py` 生成canonical完整 finding；
4. 你运行finding check、带report的typed merge和`task-lifecycle-check`；
5. 你按`INSTRUCTION.md`写`investigation/code-investigator` checkpoint；
6. fresh evidence critic只写candidate专属raw handoff并返回；
7. 你运行critic check、带report的typed merge和`critic_review/evidence-critic` checkpoint。

Finding与critic的准确check/merge命令及所有checkpoint参数逐值遵循`INSTRUCTION.md`第7节。Candidate merge目录只能有一个canonical finding JSON，semantic/report/trace全部在目录外。一个 candidate失败不能阻塞有效 peer；但不能把证据不足伪装成 provider/tool deferred。`dynamic_probe_selection.disposition`不得为`selected`，dynamic probe ledger保持空。

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

Fresh final judge是单写者例外：它只把每个finding的verdict写入`agent_review_verdicts.jsonl`并返回，不运行helper。你运行`goal_runner.py review`，按`INSTRUCTION.md`写final-judge checkpoint后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py finalize \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

`finalize`先写provisional report供final gate验真；只有命令返回0且final_gate trace passed，result才有效。若 gate失败，只读取 trace中最早的真实错误，修对应 artifact并重跑受影响 gate；不得把provisional文件当成功，不得重新做已经闭环的全部探索，也不得因时间或数量改写语义。
