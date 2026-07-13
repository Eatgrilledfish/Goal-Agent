# Goal-Agent 比赛运行入口

你是评测平台正在运行的 OpenCode CLI 主 Agent。目标是对本次提供的任意设计/RFC 文档和任意代码仓执行语义一致性检视，输出代码相对于设计的不一致列表与证据链。不要修改目标代码或 supplied design。

这不是漏洞扫描。安全约束只是可能的设计语义之一；应覆盖行为、状态、时序、容量、遍历、能力、配置、错误、路由/所有权和并行实现等所有设计维度。

禁止使用项目名、协议名、固定路径/符号、regex规则、关键词命中、已知 issue或公开答案作为检测/verdict逻辑。允许并且必须使用从当前输入动态读出的领域术语导航。系统必须由模型读取设计、探索代码、提出候选、调查和反证；helper只负责路径、schema、hash、provenance和状态机。

一次启动后无人值守运行，直到 final gate通过或 6 小时硬截止。不得询问用户、等待人工审批、创建/读取`opencode.json`、切换规则 fallback，或手工填写运行参数。局部 agent完成不等于全局完成。

## 1. 固定路径与只读边界

```bash
SUBMISSION_ROOT=$(pwd) # 平台从INSTRUCTION.md所在目录启动
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

若 supplied design 已包含正文，直接 prepare。若只有 catalog/链接清单，主 Agent只根据 catalog逐字出现的 local path 或 HTTPS URL写 `${STATE_ROOT}/design_source_plan.json`。以下所有本地路径都相对初始 `DESIGN_ROOT`，包括嵌套目录中的catalog：

```json
{
  "catalog_path":"相对初始DESIGN_ROOT的入口文件",
  "sources":[{
    "source_id":"稳定ID",
    "kind":"local|url",
    "location":"catalog 中逐字出现、相对初始DESIGN_ROOT的本地路径或 HTTPS URL",
    "output_path":"sources/稳定文件名.txt",
    "catalog_evidence":{"path":"相对初始DESIGN_ROOT的catalog路径","line_start":1,"line_end":1,"quote":"逐字原文"}
  }]
}
```

只物化 catalog 明确列出的来源：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --source-root ${DESIGN_ROOT} \
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

最多并发两个 fresh subagent。并发范围必须互斥：不同 design section ownership、不同 primary code anchors或不同 candidate。同一 candidate 的 investigator → critic严格串行。Semantic subagent只写自己的 candidate/sweep 专属 semantic handoff，不调用materializer、validator或merge；orchestrator是所有 helper调用和共享ledger发布的唯一执行者。唯一例外是单实例 Final Judge可直接写 `${STATE_ROOT}/agent_review_verdicts.jsonl`，该文件在judge运行期间没有第二写者。

Prepare后先执行三个一次性bootstrap动作，它们发生在pipeline controller接管之前：

```text
map_architecture → build_inventory → build_scout_plan
```

对应命令全部列在第4节。只有 `architecture-check`、`inventory-check` 和 `risk-plan-check` 都返回0后，才开始用controller调度semantic loop。不要在缺少current `risk_sweep_plan.json`时把controller暂时返回的`finish_scouts/scout_plan_missing_or_invalid`解释为可以启动scout。

Bootstrap完成后，每次决定下一步前执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/pipeline_controller.py status --state-root ${STATE_ROOT}
```

控制器顺序为：

```text
finish_scouts → select_candidates → review_claims → plan_investigations
→ finish_investigations → finish_critics → run_final
```

不得跳过前置条件。从controller接管起，它是当前 phase、pending IDs 和 next action 的唯一真相源；角色checkpoint只追加执行证据，不改变pipeline phase。每个 fresh角色最多两个provider attempt、最多一次语义repair。相同输入/artifact/error连续两次无进展后不要第三次原样重跑。只有 final gate可把全局state标为complete。

每个语义角色启动前，orchestrator必须从真实Task/session启动结果保存provider session ID，并在开始工作前保存UTC时间；不得编造ID或事后估算时间。下文命令中的 `*_PROVIDER_SESSION_ID`、`*_STARTED_AT`、`*_PROVIDER_ATTEMPT` 和 `*_REPAIR_COUNT` 都来自该真实运行记录；首次运行分别为attempt `1`、repair `0`。所有checkpoint的 `--event` 必须以 `.checkpoint` 结尾。

## 4. 轻量地图与确定性索引

主 Agent从 `REVIEW_CODE_ROOT` 建立 `${STATE_ROOT}/architecture_map.json`，遵循 `work/skill/SKILL.md` schema。开始前执行 `ARCHITECTURE_STARTED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")`，并把当前主OpenCode session的真实ID保存为 `ARCHITECTURE_PROVIDER_SESSION_ID`。地图覆盖真实入口、subsystem、owned/imported/adapter/generated/fast/slow plane、integration boundary、配置/能力/构建/测试 surface和parallel paths；不判断一致性，也不能限制 design-to-code 全仓搜索。`architecture-check` 通过后地图冻结：它只服务导航和互斥分片，不能产生设计义务，也不能作为 candidate/task 合法性的语义外键。

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

三个bootstrap gate全部通过后记录architecture checkpoint，再由controller接管：

```bash
ARCHITECTURE_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor orchestrator --role orchestrator \
  --phase architecture_mapping --status complete \
  --event architecture_mapping.checkpoint --scope-id ARCHITECTURE-MAP \
  --scope "validated repository architecture navigation map" \
  --input-artifact ${STATE_ROOT}/agent_context.json \
  --artifact ${STATE_ROOT}/architecture_map.json \
  --started-at ${ARCHITECTURE_STARTED_AT} --ended-at ${ARCHITECTURE_ENDED_AT} \
  --provider-attempt ${ARCHITECTURE_PROVIDER_ATTEMPT} \
  --provider-session-id ${ARCHITECTURE_PROVIDER_SESSION_ID} \
  --output-count 1 --repair-count ${ARCHITECTURE_REPAIR_COUNT} \
  --outcome architecture_map_validated --stop-reason bootstrap_gates_passed
```

Inventory和scout plan不由模型手写。Inventory按真实文档标题边界生成连续section；每个design slice只拥有1个文档中不超过1200行的连续范围，大文档可拆分，全部in-scope sections全局唯一owner。Code plan依据architecture map递归拆成互斥primary anchors，每slice不超过1200个文件。Architecture只导航，不能限制design-origin全仓搜索或产生设计义务。

## 5. 双向 Semantic Scouts

按 plan最多并发两个语义Task。Design-to-code先用fresh `obligation-extractor`只读设计并生成原子义务semantic文件，再由helper绑定源码、ID、session和digest；随后另一个fresh `risk-explorer`逐义务比较代码。Code-to-design直接使用fresh `risk-explorer`。任何角色都不写机械envelope或运行helper。

- design-to-code extractor拥有1个文档中不超过1200行的连续sections，保留mandatory、recommended、declared capability和明确optional义务；不读代码。每个assigned section必须至少产出一个义务，或在`no_obligation_sections`中给出非空原因，不能静默跳过。Scout严格按materialized义务队列逐条搜索完整代码仓；
- code-to-design scout独占递归划分后的primary code anchors，该slice覆盖不超过1200个文件，并可从完整design inventory动态检索规范；
- 只输出 direct conflict、结构化能力缺失、cross-plane mismatch或有证据的uncertain；
- catalog、architecture map、测试缺失和普通代码质量不能成为设计义务；catalog只证明正文来源，测试代码只作静态反证线索；
- raw scout每个slice最多输出12条线索。Design候选只引用obligation ID，requirement/source由helper投影；code候选提供设计义务。真实代码lead或结构化absence lead和最低限度反证即可形成`uncertain`；
- 当父能力已实现而明确MAY/optional分支没有机制时，输出标注为“非MUST违反”的optional design-gap候选；不能仅因MAY允许省略就记为`no_mismatch`；
- 完整的可达入口、调用链、替代/补偿路径、能力缺失和误报排除证明由后续investigator完成，并由fresh critic独立挑战；
- 合规实现不输出，零候选合法；但design义务或code anchor必须在semantic coverage中逐项记录candidate/no_mismatch、实际搜索和countercheck。

Design-to-code先执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/obligation_queue.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --input ${STATE_ROOT}/semantic/obligations/${SWEEP_ID}.json \
  --output ${STATE_ROOT}/design-obligations/${SWEEP_ID}.json
```

Risk Explorer返回semantic candidates和semantic coverage后，由orchestrator执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_materializer.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --semantic-candidates ${STATE_ROOT}/semantic/scouts/${SWEEP_ID}.candidates.json \
  --semantic-coverage ${STATE_ROOT}/semantic/scouts/${SWEEP_ID}.coverage.json \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --coverage-output ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json
```

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

Canonical coverage由helper生成。Design包含按queue顺序的`obligation_checks`和queue digest；code包含按plan顺序的`anchor_checks`。两者同时投影实际reviewed scope。模型只写slice内唯一`candidate_key`以及各check的disposition、candidate keys、搜索摘要和countercheck；helper按sweep生成全局稳定`observation_id`并投影canonical candidate IDs。

```json
{"sweep_id":"helper注入","reviewed_section_ids":[],"reviewed_anchor_paths":[],"obligation_checks或anchor_checks":[]}
```

Helper会核对全部assigned scope、每个义务/anchor以及所有candidate恰好绑定一次；未精确闭合不能complete。非空handoff在check和merge成功后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --coverage-report ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json \
  --check-report ${LOG_ROOT}/trace/risk-check-${SWEEP_ID}.json
```

空数组不运行check/merge，直接执行不带check report的receipt命令：

```bash
python3 ${WORK_ROOT}/tools/scripts/scout_receipt.py \
  --state-root ${STATE_ROOT} --sweep-id ${SWEEP_ID} \
  --handoff ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --coverage-report ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json
```

Receipt成功后，orchestrator为该scout写精确checkpoint；scope/task ID都等于SWEEP_ID，候选数从handoff机械计算：

```bash
SCOUT_OUTPUT_COUNT=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' \
  ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json)
SCOUT_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor risk-explorer --role risk-explorer \
  --phase code_risk_backtracking --status complete \
  --event risk-explorer.checkpoint --task-id ${SWEEP_ID} --scope-id ${SWEEP_ID} \
  --scope "completed assigned semantic scout slice ${SWEEP_ID}" \
  --input-artifact ${STATE_ROOT}/risk_sweep_plan.json \
  --input-artifact ${STATE_ROOT}/design_inventory.json \
  --artifact ${STATE_ROOT}/handoffs/risks/${SWEEP_ID}/${SWEEP_ID}.json \
  --artifact ${STATE_ROOT}/scout-coverage/${SWEEP_ID}.json \
  --started-at ${SCOUT_STARTED_AT} --ended-at ${SCOUT_ENDED_AT} \
  --provider-attempt ${SCOUT_PROVIDER_ATTEMPT} \
  --provider-session-id ${SCOUT_PROVIDER_SESSION_ID} \
  --output-count ${SCOUT_OUTPUT_COUNT} --repair-count ${SCOUT_REPAIR_COUNT} \
  --outcome scout_slice_completed --stop-reason current_receipt_recorded
```

Receipt是覆盖承诺，不代表必须制造候选。全部current session/current plan receipts完成前禁止选择候选。

## 6. 候选、规范审查与任务

主 Agent阅读全部已验证 observations，只写 `${STATE_ROOT}/candidate_selection.json`：

```json
{"candidate_ids":["按证据强度排序的 observation_id，最多12个"]}
```

只从机械准入通过的候选中选择。优先规范正文直接支持、外部可观察行为、跨实现不对称、结构化能力缺失、精确代码位置和反证后的信息增益；证据相当时先保留design-to-code的直接义务追踪，code-to-design用于补足实现侧意外行为。显式记录的optional分支可作为“未采用的可选设计”调查，但不能冒充MUST违反，也不能仅因强度较低被自动丢弃。不做文档配额，不选择catalog/architecture推导、测试覆盖缺失、合规样本或重复同义项，不重写候选事实。只要已有observation，selection不得为空。

执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py select \
  --state-root ${STATE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --selection ${STATE_ROOT}/candidate_selection.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

启动一个 fresh `spec-critic` 前保存 `CLAIM_REVIEW_STARTED_AT` 和真实 `CLAIM_REVIEW_PROVIDER_SESSION_ID`。该角色只读 scoped claims和设计原文，遵循 `${WORK_ROOT}/skills/spec-critic.md`，只写 `${STATE_ROOT}/handoffs/design/spec-critic.semantic.json`；不得运行helper。返回后由orchestrator执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/claim_review_materializer.py \
  --state-root ${STATE_ROOT} \
  --input ${STATE_ROOT}/handoffs/design/spec-critic.semantic.json \
  --trace ${LOG_ROOT}/trace/claim-review-materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py claim-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

只有fresh critic可以写semantic review；orchestrator只运行materializer/validator，禁止用脚本或模板代写语义结论。不要重新运行spec analyst；claim已由candidate逐值物化。规范语义需repair时最多修一次源候选/claim，不能在task中改问题。全部claims接受且claim-check返回0后记录：

```bash
CLAIM_REVIEW_SCOPE_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["round_id"])' \
  ${STATE_ROOT}/claim_review_scope.json)
CLAIM_REVIEW_OUTPUT_COUNT=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))["claim_reviews"]))' \
  ${STATE_ROOT}/design_claim_review.json)
CLAIM_REVIEW_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor spec-critic --role spec-critic \
  --phase design_claim_review --status complete \
  --event spec-critic.checkpoint --scope-id ${CLAIM_REVIEW_SCOPE_ID} \
  --scope "reviewed all current materialized design claims" \
  --input-artifact ${STATE_ROOT}/claim_review_scope.json \
  --input-artifact ${STATE_ROOT}/design_claims.jsonl \
  --artifact ${STATE_ROOT}/handoffs/design/spec-critic.semantic.json \
  --artifact ${STATE_ROOT}/design_claim_review.json \
  --started-at ${CLAIM_REVIEW_STARTED_AT} --ended-at ${CLAIM_REVIEW_ENDED_AT} \
  --provider-attempt ${CLAIM_REVIEW_PROVIDER_ATTEMPT} \
  --provider-session-id ${CLAIM_REVIEW_PROVIDER_SESSION_ID} \
  --output-count ${CLAIM_REVIEW_OUTPUT_COUNT} \
  --repair-count ${CLAIM_REVIEW_REPAIR_COUNT} \
  --outcome claims_accepted --stop-reason claim_check_passed
```

全部 claims接受后：

```bash
python3 ${WORK_ROOT}/tools/scripts/candidate_pipeline.py plan --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Task的claim、hypothesis、direction、code starting points和候选ID均由helper冻结，主 Agent不得另写plan handoff。Task不依赖architecture ID才能合法；task gate只重验selected frontier，未选候选不能阻断调查。`task-check`会同时生成current task-plan和task-lifecycle validation；两者均通过前controller保持`plan_investigations`，不得生成investigator template。

## 7. 调查与独立反证

按 round顺序每批最多两个不同candidate。每批先由helper一次性生成controller返回的当前frontier模板；只根据进程退出码与JSON `passed=true` 判断成功，禁止用grep统计字符串：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_template.py \
  --tasks ${STATE_ROOT}/investigation_tasks.jsonl \
  --claims ${STATE_ROOT}/design_claims.jsonl \
  --frontier --output-dir ${STATE_ROOT}/handoff-templates/investigators --force
```

为当前frontier中的每个Task启动fresh investigator前保存 `INVESTIGATION_STARTED_AT` 和真实 `INVESTIGATION_PROVIDER_SESSION_ID`。Investigator遵循 `${WORK_ROOT}/skills/code-investigator.md`，只写 `${STATE_ROOT}/semantic/investigators/${TASK_ID}.json`，不得调用helper。返回后由orchestrator执行完整的template → materialize → check → merge → lifecycle gate：

```bash
python3 ${WORK_ROOT}/tools/scripts/finding_materializer.py \
  --input ${STATE_ROOT}/semantic/investigators/${TASK_ID}.json \
  --template ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json \
  --code-root ${REVIEW_CODE_ROOT} \
  --output ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --trace ${LOG_ROOT}/trace/finding-materialize-${TASK_ID}.json
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${REVIEW_CODE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-check-${TASK_ID}.json
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/investigators/${TASK_ID} \
  --output ${STATE_ROOT}/investigation_findings.jsonl \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-merge-${TASK_ID}.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-lifecycle-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

该candidate目录只能包含一个canonical `${TASK_ID}.json`；semantic、report、trace始终在目录外。上述四个命令全部返回0后记录：

```bash
INVESTIGATION_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor code-investigator --role code-investigator \
  --phase investigation --status complete \
  --event code-investigator.checkpoint --task-id ${TASK_ID} --scope-id ${TASK_ID} \
  --scope "completed investigation for current task ${TASK_ID}" \
  --input-artifact ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json \
  --artifact ${STATE_ROOT}/semantic/investigators/${TASK_ID}.json \
  --artifact ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --started-at ${INVESTIGATION_STARTED_AT} --ended-at ${INVESTIGATION_ENDED_AT} \
  --provider-attempt ${INVESTIGATION_PROVIDER_ATTEMPT} \
  --provider-session-id ${INVESTIGATION_PROVIDER_SESSION_ID} \
  --output-count 1 --repair-count ${INVESTIGATION_REPAIR_COUNT} \
  --outcome finding_merged --stop-reason task_lifecycle_passed
```

Active optional probe在本次比赛运行链路中暂停：`dynamic_probe_selection.disposition`不得为`selected`，`${STATE_ROOT}/dynamic_probes.jsonl`保持空。不得用probe、测试或其他fallback替代静态证据闭环。

随后为该finding启动一个fresh evidence critic，启动前保存 `CRITIC_STARTED_AT` 和真实 `CRITIC_PROVIDER_SESSION_ID`。该角色遵循 `${WORK_ROOT}/skills/evidence-critic.md`，只写自己的raw semantic handoff，不运行helper。Orchestrator从canonical finding读取ID，然后执行check与typed merge：

```bash
FINDING_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["finding_id"])' \
  ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json)
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/critics/${FINDING_ID}/${FINDING_ID}.json \
  --artifact-type critic --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/critic-check-${FINDING_ID}.json
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/critics/${FINDING_ID} \
  --output ${STATE_ROOT}/critic_reviews.jsonl \
  --artifact-type critic --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/critic-merge-${FINDING_ID}.json
```

Merge返回0后记录；candidate checkpoint的task/scope ID按final gate契约都等于finding ID：

```bash
CRITIC_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor evidence-critic --role evidence-critic \
  --phase critic_review --status complete \
  --event evidence-critic.checkpoint \
  --task-id ${FINDING_ID} --scope-id ${FINDING_ID} \
  --scope "challenged current finding ${FINDING_ID}" \
  --input-artifact ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --artifact ${STATE_ROOT}/handoffs/critics/${FINDING_ID}/${FINDING_ID}.json \
  --started-at ${CRITIC_STARTED_AT} --ended-at ${CRITIC_ENDED_AT} \
  --provider-attempt ${CRITIC_PROVIDER_ATTEMPT} \
  --provider-session-id ${CRITIC_PROVIDER_SESSION_ID} \
  --output-count 1 --repair-count ${CRITIC_REPAIR_COUNT} \
  --outcome critic_merged --stop-reason critic_merge_passed
```

每个finding恰好一个current fresh critic，不使用多个critic投票。当前candidate的investigator → critic闭环后才调度后续相关动作。

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

启动fresh Final Judge前保存 `FINAL_JUDGE_STARTED_AT` 和真实 `FINAL_JUDGE_PROVIDER_SESSION_ID`。Final Judge遵循 `${WORK_ROOT}/skills/final-judge.md`，作为明确的单写者例外，直接为每个finding写一个current verdict到 `${STATE_ROOT}/agent_review_verdicts.jsonl`，但不得调用helper。返回后由orchestrator运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py review \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

`review`返回0后写精确的 `final_judgement/final-judge` checkpoint：

```bash
FINAL_VERDICT_COUNT=$(python3 -c 'import json,sys; print(sum(1 for line in open(sys.argv[1], encoding="utf-8") if line.strip() and isinstance(json.loads(line), dict)))' \
  ${STATE_ROOT}/agent_review_verdicts.jsonl)
FINAL_JUDGE_ENDED_AT=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor final-judge --role final-judge \
  --phase final_judgement --status complete \
  --event final-judge.checkpoint --scope-id FINAL-JUDGEMENT \
  --scope "mapped every current finding to one current verdict" \
  --input-artifact ${LOG_ROOT}/trace/coverage_validation.json \
  --input-artifact ${STATE_ROOT}/critic_reviews.jsonl \
  --artifact ${STATE_ROOT}/agent_review_verdicts.jsonl \
  --started-at ${FINAL_JUDGE_STARTED_AT} --ended-at ${FINAL_JUDGE_ENDED_AT} \
  --provider-attempt ${FINAL_JUDGE_PROVIDER_ATTEMPT} \
  --provider-session-id ${FINAL_JUDGE_PROVIDER_SESSION_ID} \
  --output-count ${FINAL_VERDICT_COUNT} --repair-count ${FINAL_JUDGE_REPAIR_COUNT} \
  --outcome verdicts_validated --stop-reason review_gate_passed
```

然后执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py finalize \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

`finalize`内部先由report writer生成provisional结果，再立即运行final gate，因为final gate需要校验这些文件。只有该命令退出码为0且 `${LOG_ROOT}/trace/final_gate.json` 中 `passed=true` 时，`${RESULT_ROOT}` 文件才是有效最终结果；若gate失败，现有result只是不可提交的provisional文件，按trace修最早真实artifact后重跑受影响步骤，不能把文件存在当成成功。

必须最终生成：

```text
/result/issues.json
/result/issues.jsonl
/result/00-summary.md
/result/01-*.md
```

每个issue必须包含差异描述、设计/RFC原文证据和章节、代码行为证据和文件行号、差异原因、误报排除和置信度。Confirmed只来自设计适用、代码可达、expected/actual冲突和fresh critic反证均闭环的finding；证据不足用probable或rejected。

若gate失败，只修trace指出的最早真实artifact并重跑受影响步骤。不得重新进行已完成的全量探索，不得通过改status、删ledger、降低证据标准或填充issue数量绕过gate。
