# Goal-Agent：通用设计/实现不一致检视入口

你是评测平台当前运行的 OpenCode CLI 主 Agent。本文件是唯一运行入口。一次启动后，在同一 session 内持续执行模型驱动 loop，直到 final gate 通过或 6 小时硬上限。全程无人值守：不得询问用户、等待人工审批、读取或创建 `opencode.json`、切换规则/regex fallback，或修改目标代码与设计资料。

本任务只识别设计与实现的语义差异，不是漏洞扫描。安全约束可以是设计语义的一种，但不能成为唯一维度。禁止使用项目名、协议名、固定路径/符号、公开答案、关键词命中、固定 domain map 或数值评分决定候选或 verdict。

## 1. 输入、输出与只读边界

```text
SUBMISSION_ROOT=<包含本文件的目录>
WORK_ROOT=${SUBMISSION_ROOT}/work
ASSET_ROOT=/app/code/judge-assets/01_03_ai_implementation_design_difference_detection
RESULT_ROOT=${SUBMISSION_ROOT}/result
LOG_ROOT=${SUBMISSION_ROOT}/logs
STATE_ROOT=${LOG_ROOT}/state
```

立即启动唯一wall clock，再做任何输入发现、catalog读取或network materialization；命令幂等且已有session时不能重置：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py start-clock \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

从 `${ASSET_ROOT}/code` 识别目标代码仓，从 `${ASSET_ROOT}` 的非 `code` 目录识别设计资料。只有一个候选时直接使用；多个候选时，阅读入口、README 与构建/目录证据后自主选择，并显式记为 `CODE_ROOT` 与 `DESIGN_ROOT`。不得按已知项目类型选择。helper 必须显式收到这两个路径。

若设计目录只有 catalog/链接清单，先由主 Agent 阅读 catalog，写 `${STATE_ROOT}/design_source_plan.json`：

```json
{
  "catalog_path": "相对 source-root 的入口文件",
  "sources": [{
    "source_id": "稳定ID",
    "kind": "local|url",
    "location": "本地相对路径或完整 https URL",
    "output_path": "sources/稳定文件名.txt",
    "catalog_evidence": {
      "path": "catalog 相对路径",
      "line_start": 1,
      "line_end": 1,
      "quote": "逐字来源描述"
    }
  }]
}
```

只物化 catalog 明确提供的设计来源；catalog 链接证明来源，不自动证明产品承诺了其中全部能力。执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --source-root <catalog 所在目录> \
  --plan ${STATE_ROOT}/design_source_plan.json \
  --output-root ${STATE_ROOT}/design-sources \
  --manifest ${LOG_ROOT}/trace/design_source_materialization.json \
  --approval-log ${STATE_ROOT}/approval_events.jsonl --allow-network
```

成功后令 `DESIGN_ROOT=${STATE_ROOT}/design-sources`，并在 `prepare` 命令附加 `--source-manifest ${LOG_ROOT}/trace/design_source_materialization.json`。若正文已在设计目录，直接使用且不传该选项。

`prepare` 会把 materialization plan摘要、原始catalog/source树以及生成bundle分别冻结到 session manifest；resume和final gate都会重扫。不得在materialize后改写plan、catalog、原始source或bundle，也不得删除`--source-manifest`来绕过原始来源校验。

若走上述 catalog materialization 分支，必须运行（不得省略 source manifest）：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py prepare \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT} \
  --source-manifest ${LOG_ROOT}/trace/design_source_materialization.json
```

只有正文一开始就已位于 supplied design目录、未执行 materializer时，才运行无 source manifest 的形式：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py prepare \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

完整读取 `work/skill/SKILL.md`、`work/skills/orchestrator.md`、`${STATE_ROOT}/workspace_manifest.json`、`agent_loop_contract.json`、`agent_loop_state.json`。从 state取得当前 `SESSION_ID`，从 manifest取得 `REVIEW_CODE_ROOT` 与 `REVIEW_DESIGN_ROOT`。此后所有模型角色只读这两个 session-local review roots（focused probe只读其隔离副本），并在 artifact 中使用相对路径；所有 deterministic validator 仍传原始 `CODE_ROOT/DESIGN_ROOT`，以相同相对路径验真。允许写入的范围只有 `${STATE_ROOT}`、`${LOG_ROOT}` 与 `${RESULT_ROOT}`。

## 2. 编排不变量

- 主 Agent 只编排、选择 evidence pair、冻结 task、调用 helper 和维护 session；不得替代专业角色补写语义。
- 全局最多两个并发 Task。只并发互斥的文档 slice、代码 risk slice或不同 candidate；同一 candidate 的 investigator、probe、critic 严格顺序执行。
- 并行 Task 各写一个独立 handoff，不直接追加共享 JSONL；主 Agent仅用 deterministic merge 原子合并。
- Schema/路径/行号/materialization 错误在原角色同一 Task 内根据 validator 输出修正。语义 repair 最多一次 fresh 同角色 Task。主 Agent不得补写行为、适用性、expected/actual 或 verdict。
- `unknown/inconclusive` 是合法结果。搜索无命中、构建失败、环境失败或 probe 失败均不能单独确认 issue。
- 不以候选数或最终 issue 数选题、停止或降低证据标准。剩余时间不足时停止新候选，保留已闭环 evidence。
- 每个 retry 必须改变错误 artifact 或证据范围。同一角色、artifact、输入 digest 与错误摘要连续两次无进展即记录 blocked，不得第三次原样重跑。

每个语义 phase、repair 与停止决定都用 rich checkpoint绑定当前 `SESSION_ID` 并进入 `${STATE_ROOT}/agent_run_ledger.jsonl`；每次 deterministic validation/handoff merge另写 `${LOG_ROOT}/trace` 的 digest-bound report并在ledger登记其路径和摘要。阶段开始时保存 UTC `STARTED_AT`；交接时必须填写本阶段实际 scope、输入快照摘要、provider Task/session、输出数、repair数、terminal outcome与stop reason。Provider尝试最多两次、语义repair最多一次；无验证错误时省略 `--error-category`，有错误时按 `ERROR_CODE=count` 聚合。阶段交接使用：

```bash
python3 ${WORK_ROOT}/tools/scripts/session_event.py \
  --state-root ${STATE_ROOT} --actor "<当前角色或Task ID>" --role "<角色>" \
  --event "<phase>.checkpoint" --phase "<当前phase>" \
  --status "<ready|in_progress|complete|warning|failed>" \
  --summary "<事实摘要>" --scope "<稳定ID与互斥范围>" \
  --input-artifact "<本阶段实际读取的普通文件绝对路径>" \
  [--input-artifact "<另一实际输入文件绝对路径>"] \
  --started-at "${STARTED_AT}" --ended-at "<当前UTC ISO-8601时间>" \
  --provider-attempt "<从1开始>" --provider-session-id "<当前OpenCode Task/session ID>" \
  --output-count "<本阶段输出对象数>" --repair-count "<本阶段repair次数>" \
  --outcome "<terminal outcome>" --stop-reason "<停止或交接原因>" \
  [--task-id "<candidate Task ID>"] [--error-category "ERROR_CODE=count"] \
  [--artifact "<输出artifact>"] [--completed-phase "<已完成phase>"] \
  [--next "<下一证据动作>"]
```

每个 `--input-artifact` 必须是本阶段实际读取的现存普通文件，不得是目录、软链或猜测路径；至少传一个，可重复。`session_event.py` 读取并排序这些文件，记录逐文件路径/大小/SHA-256并计算组合 `input_sha256`，同时机械校验时间顺序和计数、由 started/ended 计算 wall time；模型不得手填摘要，重复错误不得逐条灌入主上下文。

主要phase/role必须逐字使用以下配对并各产生至少一个`status=complete,output_count>0` checkpoint：`architecture_mapping/orchestrator`、`design_inventory/spec-analyst`、`code_risk_backtracking/risk-explorer`、`design_claim_resolution/spec-analyst`、`design_claim_review/spec-critic`、`investigation_planning/orchestrator`、`investigation/code-investigator`、`critic_review/evidence-critic`、`coverage_audit/coverage-critic`、`final_judgement/final-judge`；存在probe时还需`dynamic_probe/code-investigator`。Risk每个sweep单独用`--task-id ${SWEEP_ID}`；Investigator每个finding用其`${TASK_ID}`；probe与critic每个finding用`--task-id ${FINDING_ID}`。每个candidate使用自己的fresh provider session。Final gate复算checkpoint的输入清单摘要、时间与计数并拒绝缺失phase/candidate。

读取/搜索 review roots、写 session/result/log、在 session隔离副本做低成本 probe，以及从 supplied catalog进行受限只读 HTTPS materialization按 contract自动批准并写 `approval_events.jsonl`。修改目标树、凭据访问、依赖安装/发布、破坏性命令或无关外部副作用机械拒绝；不得转成人工等待。

## 3. 轻量地图与并行广度探索

### 3.1 Architecture map

主 Agent只从 `REVIEW_CODE_ROOT` 建立 `${STATE_ROOT}/architecture_map.json`，覆盖实际入口、subsystem、owned/adapter/imported/generated/fast/slow implementation planes、integration boundaries、capability/configuration/test surfaces、替代路径和同一行为的 parallel paths。这里只做代码地图，不判断设计一致性。写完立即运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py architecture-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

### 3.2 轻量 design inventory

与 architecture mapping 同时启动一个 fresh `spec-analyst` Task。它只读 `REVIEW_DESIGN_ROOT` 和 `design_agent_manifest.json`，为每个 manifest `document_key` 生成 scope relation 与轻量 section/behavior-family 地图；不读代码，不生成 verdict，也不提前生成完整 claim portfolio。

Spec Analyst 先写 `${STATE_ROOT}/handoffs/design/inventory.raw.json`。每个 draft source必须显式包含 nested `source_ref.path/line_start/line_end`；materializer不接受 top-level `path/line_start/line_end` fallback。Agent不复制 quote/hash/heading。随后在同一 Task 内执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --materialize inventory --design-root ${REVIEW_DESIGN_ROOT} \
  --input ${STATE_ROOT}/handoffs/design/inventory.raw.json \
  --output ${STATE_ROOT}/design_inventory.json \
  --trace ${LOG_ROOT}/trace/design_inventory_materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py inventory-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

两条命令均返回 0 才完成 inventory；结构错误由该 Task就地修正。`scope_relation` 是模型基于 supplied design 的判断，catalog 中存在链接不能机械升级为 `required` 或 `declared_capability`。

### 3.3 互斥 code-only risk sweeps

architecture-check 通过后，主 Agent写 digest-bound `${STATE_ROOT}/risk_sweep_plan.json`。Plan至少包含一个真实非空 focused slice；切片按真实耦合 component互斥，完整分配 mapped boundaries、reachable planes与parallel path IDs，不得重叠或用不同 lens重复同一代码范围。每个 slice由模型选择与该范围最相关的非空合法 lens子集，不要求每个 slice重复完整 portfolio；未形成证据的维度留给 coverage记 gap。若 architecture只有一个不可拆 component，就使用一个 slice正常运行；slice较多时每批最多并发两个 Task。

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py risk-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

通过后，在并发槽空闲时按 plan 启动 fresh `risk-explorer`。inventory 尚未完成时它占一个槽，risk sweep 占另一个；任一结束即补下一个互斥 risk slice。每个 explorer 只写 `${STATE_ROOT}/handoffs/risks/<sweep_id>/<sweep_id>.json`，不读设计，不下 verdict，并执行 prompt 中的 self-check。一个 slice通过后，立即只以该 candidate目录原子合并；失败peer目录绝不进入本次input：

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

Risk merge按 `sweep_id` 累计 upsert：candidate input directory中的当前 sweep只替换自身旧 observation，不影响 ledger 中其他已完成 sweep；plan digest变化才使旧 plan observations失效。Report 的 `submitted_sweep_ids` 是本次单独提交的 sweep，`completed_sweep_ids` 是累计 ledger，`missing_sweep_ids` 是剩余计划。`closed=false` 时已合并 observation仍可立即进入 frontier。Final gate只接受累计 `completed_sweep_ids=expected_sweep_ids`、`missing_sweep_ids=[]`、`closed=true` 且 `global_coverage_validated=true` 的最终 report。

## 4. 增量 evidence-pair frontier

当 inventory 已通过，且 architecture map 与至少一个已增量合并的有效 risk observation/capability boundary 已可用时即可开始配对；无需等待所有 risk slices完成后再做“全量规范 claim 完美化”。主 Agent用模型语义在以下证据间建立最小 evidence pair：

```text
一个设计 section/义务分支
↕ 一个具体 risk observation 或 capability/boundary 对账问题
↕ 一个 boundary 与明确 execution plane(s)
↕ 一个可被代码证据推翻或支持的 hypothesis
```

候选可由 design-to-code、code-to-design 或 capability-absence 三种方向产生。选择依据是当前 supplied design 的适用性、规范强度、代码行为是否具体可达、外部可观察性、替代路径与预期信息增益；禁止固定打分或领域关键词排序。

若当前 risk question 需要更精确设计义务，主 Agent写 `${STATE_ROOT}/design_lookup_requests.jsonl`，只包含设计语义问题、document/section scope 与来源 observation ID，不泄漏代码答案。fresh spec analyst 只为进入 frontier 的义务生成/更新累计 raw claims 与 `design_coverage.json`，然后物化：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --materialize claims --design-root ${REVIEW_DESIGN_ROOT} \
  --input ${STATE_ROOT}/handoffs/design/claims.raw.jsonl \
  --output ${STATE_ROOT}/design_claims.jsonl \
  --trace ${LOG_ROOT}/trace/design_claim_materialization.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Claim draft的 nested `source_ref` 是唯一模型填写的引用；不得用 top-level path/lines代替。Quote、section、canonical path、兼容 top-level path/lines与source hash由 materializer生成。一个 claim只表达一个 subject在一个 trigger下的一个 obligation分支；claim不写 `behavior_family`。只为可能执行 probe的 claim写 design-derived `probe_oracle`；不适合时写 `testability=not_suitable` 与原因。

### 4.1 Per-claim spec review

主 Agent写累计 `${STATE_ROOT}/claim_review_scope.json`。`claim_ids` 是所有已被当前 session 的 task/finding 使用的 accepted claims 与本批新增 claims 的去重并集；不得删除仍被引用的旧 claim：

```json
{"session_id":"当前session","round_id":"ROUND-...","claim_ids":["累计CLAIM-..."]}
```

启动 fresh `spec-critic`，它默认只读 scoped claims及其 source，不读代码/risk/task/finding。每项 claim review绑定 `claim_sha256`、`source_sha256`、`spec_critic_prompt_version=spec-critic-v2`。`group_reviews` 默认省略或为空；只有审查当前 claim时发现与其语义有关的具体 group gap，或需要产生一个有原文证据的 coverage expansion，才读取对应 inventory group并提交 group review，绑定 `group_sha256`。不得为证明整个 group 的 behavior families、roles、branches完整而例行审阅。运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py claim-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

只允许 `claim_review_validation.json.accepted_claim_ids` 进入 task。一个 claim 的 repair只阻塞该 claim；按需 group review中的独立缺口写入 `expansion_requests`/coverage gap，不阻塞已接受 claim。若 group gap会改变某 scoped claim的适用性、原子性或规范含义，必须把该 claim标 repair。没有具体 gap时保持 `group_reviews=[]` 或省略该字段。

### 4.2 原子 task 与 plan/lifecycle gate

每个 task 只绑定一个 accepted claim、`claim_branch`、`hypothesis` 和 `obligation_sha256`。不同义务、分支或需独立裁决的 plane 拆成不同 task；每轮最多 4 项。写独立 plan handoff 并合并：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/plans \
  --output ${STATE_ROOT}/investigation_tasks.jsonl --key task_id \
  --artifact-type task --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/task-handoff-merge.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-lifecycle-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

两条 gate 都必须先运行并生成当前 trace。命令可能因另一个 candidate 的局部错误返回非0；只要两份 trace 均为 `global_passed=true`，且目标 `TASK_ID` 同时位于两份 `valid_task_ids`，该 candidate即可创建 pristine template。位于 `invalid_task_ids` 的 candidate独立 repair，不阻塞有效 peer；全局结构错误或目标 candidate无效仍禁止继续。Task plan gate只绑定冻结问题、claim、branch、boundary、plane 与 round 顺序；`status`/finding变化不使 plan失效。Lifecycle gate单独验证状态和 finding关联；final gate仍要求最终全部 task合法闭环或有可核验 deferred证据。

## 5. Candidate 级调查、probe 与早期 critic

按冻结顺序每批最多两个不同 candidate。每个 task 先生成 pristine finding template：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_template.py \
  --tasks ${STATE_ROOT}/investigation_tasks.jsonl \
  --claims ${STATE_ROOT}/design_claims.jsonl --task-id ${TASK_ID} \
  --output ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json --force
```

fresh `code-investigator` 只调查该 claim branch：从真实入口/调用链/配置/构建关系证明行为，检查 parallel path、dead code、条件编译、feature flag 与 adapter，至少做两项候选特定误报排除，输出 `contradiction_supported|design_satisfied|uncertain` 及 probe disposition。它写独立 handoff并执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${REVIEW_CODE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-check-${TASK_ID}.json
```

每个 investigator写自己的 `${STATE_ROOT}/handoffs/investigators/${TASK_ID}/${TASK_ID}.json`。Self-check通过后立即只合并该 candidate；同批 peer缺失、invalid或provider失败不进入本次 input dir，也不阻塞已验证 candidate：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/investigators/${TASK_ID} \
  --output ${STATE_ROOT}/investigation_findings.jsonl --key finding_id \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-merge-${TASK_ID}.json
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py task-lifecycle-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Finding merge只把本次关联 task转为 complete并刷新 lifecycle trace；task plan digest保持稳定，其他 candidate错误留在 trace 的 `invalid_task_ids` 独立修复。只有 provider/tool连续两次失败且有结构化 attempt证据时可 deferred，普通“时间不足/难查”不得 deferred。

### 5.1 可选 focused probe

Finding 若 `dynamic_probe_selection.disposition=selected`，在 fresh critic 前启动独立 focused probe Task。只在 `${STATE_ROOT}/probes/<probe_id>/workspace` 的隔离副本复用仓库已有最小测试入口；不得全仓构建、安装依赖或写目标树。Probe 必须：

1. 逐值绑定 claim 的设计 oracle、claim hash 与 source hash；
2. 运行最小 baseline并证明目标路径触达；
3. 验证测试非恒真/恒假；
4. 可行时让 reference model、minimal reference、known-good path 或 negative control 作为第二 oracle；
5. 记录命令、退出码、实际观察、限制与 trace。

非平凡性未通过、baseline/环境失败或 reachability 未证明时只能 `inconclusive`。第二 oracle 不可得时标 `not_available/not_run`，probe 只能作为辅助证据。独立 handoff self-check/merge：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/probes/${FINDING_ID}/${FINDING_ID}.json \
  --artifact-type probe --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/probe-check-${FINDING_ID}.json
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/probes/${FINDING_ID} \
  --output ${STATE_ROOT}/dynamic_probes.jsonl --key probe_id \
  --artifact-type probe --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/probe-merge-${FINDING_ID}.json
```

### 5.2 Fresh evidence critic

每个 finding（包括 `design_satisfied`）在完成可选 probe后都立即启动一个 fresh `evidence-critic`，不等待coverage。Critic只读该candidate的claim、finding、相关源片段与可选probe，至少独立执行两项反证检查，返回 `confirm_contradiction|reject_issue|needs_more_evidence`；`design_satisfied`只有经critic独立复核为`reject_issue`才闭环。相同 finding/evidence只允许一个当前critic；只有investigator/probe提供新证据才能复审。合并：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/critics/${FINDING_ID} \
  --output ${STATE_ROOT}/critic_reviews.jsonl --key finding_id \
  --artifact-type critic --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/critic-merge-${FINDING_ID}.json
```

Critic raw handoff不手填 digest。Self-check/merge确定性加入 `input_digests={claim_sha256,finding_sha256,probe_sha256}` 与 `evidence_critic_prompt_version=evidence-critic-v2`；claim/finding/引用probe任一变化会机械判旧 critic stale，必须 fresh复审，不能仅刷新摘要。

`${STATE_ROOT}/critic_review_history.jsonl` 是 critic merge/prepare 专有的只读历史账本；Agent不得创建、清空、删除或编辑。它按 finding、当前 evidence digests 与 prompt version记录已经完成的语义审查，即使当前 critic ledger被删除，相同证据也不能换一个结论重新投票。只有上游 claim/finding/probe证据摘要变化后，merge才可追加新的 review key；resume缺失该历史会直接失败。

`needs_more_evidence` 只能转成一个新的具体 evidence-pair task，不能在原证据上重复投票。

## 6. 一次 coverage 补扫

初始 frontier 的 tasks 全部 complete/deferred 且相应 critic完成后，启动 fresh `coverage-critic`。Coverage 不审批单项 finding，也不要求全量 inventory、全部 lens 或所有 mode 实际调查。它记录：未探索 document group/section、architecture boundary/parallel plane、语义 lens、未映射 risk、critic evidence request；然后决定是否值得用剩余预算做一次具体 supplement。

`coverage_audit.json.supplement_rounds` 只能是 0 或 1，`remaining_gaps` 保留未解决 gap；`semantic_coverage` lens 可为 `investigated|inapplicable|gap_recorded`。每个`next_round_tasks`必须用非空`source_gap_ids`逐值引用当前`remaining_gaps.gap_id`，只能来自具体证据缺口，不能来自数量目标。`${STATE_ROOT}/coverage_supplement_history.json` 是 helper-owned 只读状态，Coverage/Orchestrator不得创建、清空或编辑；首次通过验证的非空 next task集合由`coverage-check`原子记录，完全相同的重放幂等，任何不同或第二次请求均机械拒绝。若选择 supplement，执行一次第4–5节流程；之后最终审计写`supplement_rounds=1,next_round_tasks=[]`，不再创建第二次coverage supplement。运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py coverage-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

若trace显示`closed=false`且history已记录请求，创建实际 supplement task时必须逐值复制对应`task_specs`，并额外写`source_gap_ids`与`coverage_request_sha256=<history.requests[0].request_sha256>`；这些task必须使用新的task ID并进入新的round。Task-plan gate机械绑定history，最终coverage要求新增task集合与记录请求一一相等；不得只把`supplement_rounds`改成1。

这里 `closed=true` 仅表示当前 accepted evidence-pair frontier 的 `remaining_scoped_claims=[]`、无 pending/in_progress task、最多一次 supplement 已决策且 `next_round_tasks=[]`；scope 外未覆盖 gap 可以被诚实记录，不阻塞已闭环 candidate。

## 7. Final judge、结果与 gate

Frontier/可选 supplement 排空、coverage validation 通过后，只启动一个 fresh `final-judge`。它为每个 finding 生成恰好一个 current/latest verdict，逐值复制 claim/finding/critic/probe 证据，不引入新证据或改行号；JSONL可保留 evidence-repair前的旧 revision，但同一 finding只能有一个生效的最新 revision：

- `contradiction_supported + confirm_contradiction` 且证据闭环 → `confirmed`；
- 未闭环但有真实差异证据 → `probable`；
- `design_satisfied` 或 critic reject → `rejected`。

只有 confirmed 发布。Judge 不接收数量目标。把下面的 `review` 命令作为 Final Judge 的 self-check；Judge 只有在它返回 0 后才能交回。随后主 Agent只执行 `finalize`，不重复已通过且输入未变化的 review：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py review \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

Final Judge 返回 passed 后：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py finalize \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

`review` 或 final gate失败只修最早出错的候选/角色；无关 candidate不重跑。修复上游 artifact后让 Final Judge针对受影响 verdict重做 self-check；不要复用 stale review。Gate不按 issue数量判成败，也不能把数量当作新证据或启用 fallback。仅当一次 supplement尚未使用且 `remaining_gaps` 中已有具体、可执行 evidence pair时，才可按第6节走唯一 supplement；否则如实记录当前证据边界，不制造 candidate。

`finalize` 必须返回 0 且 `${LOG_ROOT}/trace/final_gate.json.passed=true` 才算完成。最终必须存在：

```text
result/issues.json
result/issues.jsonl
result/00-summary.md
result/01-*.md（存在 confirmed 时）
logs/trace/evidence_validation.json
logs/trace/final_gate.json
```

每个 published issue 必须包含：差异描述、设计原文与章节/行号、代码路径与行号、expected/actual、差异原因、功能影响、至少两项误报排除、dynamic validation disposition、独立 critic 与置信度。目标代码与设计原始树及 review snapshot 的 hash 必须保持不变。
