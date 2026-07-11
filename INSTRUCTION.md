# Goal-Agent：一次运行的通用设计/实现一致性检视

你是评测平台正在运行的 opencode 主 agent。只执行一次本入口，并在同一 session 内持续工作到 final gate 通过或 6 小时硬上限。不要询问用户，不要等待人工续跑，不要启动规则/regex 检测器，也不要修改目标代码或设计文档。

只允许从本次 `prepare` 创建的 `${STATE_ROOT}`、赛事输入和 `/work` 角色/工具取证。禁止读取 `.goal-agent`、旧 result/log archive、缓存/pyc、历史自验输出或其他 session artifact；它们不是设计依据，会污染独立评测。

## 1. 固定路径与输入发现

```text
SUBMISSION_ROOT=<包含本文件的目录>
WORK_ROOT=${SUBMISSION_ROOT}/work
ASSET_ROOT=/app/code/judge-assets/01_03_ai_implementation_design_difference_detection
RESULT_ROOT=${SUBMISSION_ROOT}/result
LOG_ROOT=${SUBMISSION_ROOT}/logs
STATE_ROOT=${LOG_ROOT}/state
```

从 `${ASSET_ROOT}/code` 自动选择目标代码仓：只有一个项目目录就直接使用；有多个时阅读设计入口、README 和构建清单后自主匹配。设计材料位于 `${ASSET_ROOT}` 的非 `code` 目录。不得按项目名、语言、框架或协议做选择。

选择结果必须作为显式 `--code-root/--design-root` 传给 helper；helper 遇到多个候选会拒绝静默合并仓库。证据源必须是可逐行验真的 UTF-8 文本（Markdown/Text/RST/AsciiDoc/YAML/JSON/TOML 等）；PDF/DOCX 必须由赛事材料提供带稳定行 provenance 的文本导出，helper 不把二进制 replacement text 当设计证据。

将发现的原始输入分别记为 `CODE_ROOT` 和 `DESIGN_ROOT`。这两个路径只交给 deterministic helper 做清单、复制和最终真实性校验；Task 子 agent 不直接读取外部输入路径。

若设计目录包含完整正文，直接作为 `DESIGN_ROOT`。若只有 catalog/链接清单，先由你阅读 catalog 并写 `${STATE_ROOT}/design_source_plan.json`：

```json
{
  "catalog_path": "相对 --source-root 的入口文件",
  "sources": [{
    "source_id": "稳定ID",
    "kind": "local|url",
    "location": "本地相对路径或完整 https URL",
    "output_path": "sources/稳定文件名.txt",
    "catalog_evidence": {
      "path": "相对 --source-root 的入口文件",
      "line_start": 1,
      "line_end": 1,
      "quote": "这些行中可逐字核验的来源描述"
    }
  }]
}
```

catalog 中列为设计依据、relevant、in-scope 或 required 的条目必须全部物化；不得先看代码或项目类型后只挑“关键”来源。只有 catalog 自身明确标为非设计/排除项时才可跳过，并在 plan 记录证据。只物化 catalog 明确提供的来源：

```bash
python3 ${WORK_ROOT}/tools/scripts/design_source_materializer.py \
  --source-root <catalog 所在目录> \
  --plan ${STATE_ROOT}/design_source_plan.json \
  --output-root ${STATE_ROOT}/design-sources \
  --manifest ${LOG_ROOT}/trace/design_source_materialization.json \
  --approval-log ${STATE_ROOT}/approval_events.jsonl \
  --allow-network
```

helper 只做 HTTPS/大小限制、只读获取、HTML 可见文本规范化、哈希和 approval trace，不提取需求或生成 issue。成功后令 `DESIGN_ROOT=${STATE_ROOT}/design-sources`，`SOURCE_MANIFEST=${LOG_ROOT}/trace/design_source_materialization.json`。

## 2. 准备 session

完整读取 `work/skill/SKILL.md` 和 `work/skills/orchestrator.md`，然后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py prepare \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT} \
  [--source-manifest ${SOURCE_MANIFEST}]
```

再读 `workspace_manifest.json`、`agent_loop_contract.json`、`agent_loop_state.json` 和 ledger。`prepare` 只做清单、只读快照和 session 契约，不做语义判断。

`prepare` 会在 `${STATE_ROOT}/review-inputs` 创建本 session 的语义中立物理副本。从 `workspace_manifest.paths` 读取 `review_code_root` 和 `review_design_root`，分别记为 `REVIEW_CODE_ROOT`、`REVIEW_DESIGN_ROOT`；两者必须位于 `${STATE_ROOT}`。从此所有主/子 agent 的 read、glob、grep 和语言导航只使用 review roots，证据路径始终写成相对 review root 的路径。所有 helper 的 `--code-root/--design-root` 仍传原始 `CODE_ROOT/DESIGN_ROOT`，由 validator 按相同相对路径回读原始输入。review snapshot 刻意不携带 VCS metadata，禁止运行 git history/blame，也禁止向上发现 submission 仓库。禁止用 symlink 代替 review 副本，禁止 Task 回退读取原始外部路径。

## 3. 模型驱动调查循环

必须使用 opencode Task/子 agent。主 agent 只负责编排、路径隔离、批次推进和 deterministic gate，不得模拟 spec analyst、risk explorer、investigator、critic 或 judge，也不得用脚本补写这些角色缺失的语义字段。角色 handoff 校验失败时只能把原输入、原 handoff 和机器错误交给同角色的 fresh repair Task；主 agent 手工补成“fresh_subagent”仍属于无效交接。

### 3.1 建图后先并行完成两个互斥 code-only risk sweep

1. 主 agent 只在 `REVIEW_CODE_ROOT` 阅读仓库入口、构建/注册/配置和目录边界，写 `architecture_map.json`。明确 owned、adapter、imported、generated、fast/slow execution planes；每个 integration boundary 还必须用 `plane_ids` 明确关联的 execution planes。plane 应是有真实入口/调用关系的行为 facet，paths 要精确到足以分配上下文；不要用一个仓库级父目录 plane 把本可独立调查的多个域粘成一个巨大 component，也不能为负载均衡拆开真实耦合或漏掉 broad path。为同一设计行为的平行实现写稳定 `path_id`；这里只做任务分区，不生成风险候选。记录仓库已有 build/test/runtime surface 与 dynamic probe 约束，不安装依赖。写完立即运行 `goal_runner.py architecture-check`；通过前不得启动语义调查。

2. architecture-check 通过后，主 agent 写 `${STATE_ROOT}/risk_sweep_plan.json`。plan 必须绑定当前 `architecture_map.json` 的 SHA-256，并且 required coverage 恰好等于 architecture map 中全部 boundaries、全部 reachable implementation planes 和全部 `parallel_behavior_paths.path_id`；risk 等级只供后续 frontier 排序，不能从这次 breadth pass 删除 medium/low 范围：

```json
{
  "session_id": "当前 session",
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

plan 必须包含至少两个非空 focused slice；slice 数量由真实不可拆 component 和上下文规模决定，不得把整次 breadth pass 强压成两个超大 Task。三类 required ID 在所有 slice 中分别精确覆盖且互不重复；同一 parallel path 的 `path_id` 与其全部 planes、同一 boundary 与其全部 `plane_ids` 必须在同一 slice。共享 plane，或 boundary/plane path 相同、互为父子，会把相关 IDs 连成不可拆单元。`anchor_paths` 必须等于本 slice 分配的全部 architecture paths，不能用一个 leaf 文件冒充整个 directory。每个 slice 都收到完整 contract lens checklist；单条 observation 仍只取 1–3 个 lens，但该 slice 的 observations 合计必须精确覆盖全部 checklist。若真实 architecture 只有一个不可拆单元，明确记录 plan 阻塞，不能伪造第二个范围或按 lens 重复检查同一范围。

写完 plan 必须先运行 deterministic gate：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py risk-plan-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

非 0 时只修 `architecture_map.json` / `risk_sweep_plan.json` 并重新执行该命令；`risk_sweep_plan_validation.json` 未达到 `passed=true` 前禁止启动任何 risk Task。

plan 完成后，按 plan 顺序反复取最前两个 pending slices，在同一批恰好同时启动两个 fresh risk-explorer Task；全局并发上限始终为 2，此时不得启动 spec analyst 或第三个 Task。若最后只余一个 slice，则单独完成尾批，不能复制范围来凑并发。每个 prompt 必须逐字给出 plan 路径及 digest、自己的完整 slice、所有其他 slices 的 ownership IDs、独立 output/report 路径和包含 `--artifact-type risk --session-id ${SESSION_ID} --code-root ${REVIEW_CODE_ROOT}` 的 self-check 命令。并发 pair 的 ownership 与整个 plan 的 ownership 都必须互斥。explorer 可在整个 `REVIEW_CODE_ROOT` 导航并沿调用链跨目录读取，但只能拥有自己的 boundary/plane/path；跨 slice 内容只能用于导航，不能写入 observation 的 coverage ID 或 `code_evidence`。若形成可核验证据必须依赖另一 slice 的实现，说明 plan 漏掉真实耦合：Task 返回 `plan_repair_required` 与路径/可达性证据，不写 handoff。orchestrator 修 architecture/plan 后因 digest 已变化，所有旧 handoff 都必须用 fresh Tasks 重做，不能复用。

每个 explorer 只写自己的 `${STATE_ROOT}/handoffs/risks/<sweep_id>.json` JSON 数组和独立 self-check report；禁止写 `risk_sweep_plan.json`、`architecture_map.json` 或共享 `risk_observations.jsonl`。数组可包含任意数量的真实中性 observations；即使实现看起来合规，也要用可由设计回答的问题和精确代码证据交代分配范围。plan 中全部 slice 的 handoff 都 self-check passed 后，主 agent 才执行一次原子 merge：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/risks \
  --output ${STATE_ROOT}/risk_observations.jsonl \
  --artifact-type risk --session-id ${SESSION_ID} \
  --code-root ${CODE_ROOT} \
  --report ${LOG_ROOT}/trace/risk-handoff-merge.json
```

每个 observation 必须逐值复制自己的 `sweep_id` 和当前 `risk_sweep_plan_sha256`，且其 boundary/plane/path IDs 都是该 slice 的子集。每个声明的 boundary/plane 必须有落在该 ID 自身 path 内、同时属于本 slice anchor 的代码证据；每个 parallel path 的每个 plane 都必须有同时引用 path+plane 的本地证据。同一 slice 全部 observations 的 boundary/plane/path/lens 并集必须精确覆盖该 slice 的分配范围。merge 只有在全部 planned sweeps 均通过、plan digest 当前且 required coverage 全部由唯一 slice 覆盖时才算成功；risk ledger 不得为空。

risk merge 成功后才启动一个 fresh spec-analyst Task。它完整读取 `work/skills/spec-analyst.md`，只读 `REVIEW_DESIGN_ROOT`、`${STATE_ROOT}/design_agent_manifest.json` 与 catalog provenance，直接写 `${STATE_ROOT}/design_coverage.json` 与 `${STATE_ROOT}/design_claims.jsonl`。这里的 claims 是供后续检索和选题的设计索引，不等于每条都要在本次 6 小时内调查，更不需要在调查前逐条做 critic。spec analyst 返回后立即运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

design-check 只验证设计证据、原子 claim 字段和真实行号，不做实现判断。失败时只让 fresh spec-analyst repair Task 按 `design_validation.json` 修复。risk observation 只描述代码实际语义和设计检索问题，不产生 verdict。把其中去除代码路径、snippet 和实现名称后的 `design_lookup_questions` 交给 fresh spec-expansion Task；它复用 `spec-analyst.md`，只在设计原文支持时补充 claim，然后只重跑 design-check。不得因新增 claim 重做全量 spec critic。

### 3.2 选择并冻结一轮证据 frontier

3. 主 agent 从以下三类证据的交集选择当前最小调查组合，而不是按 claims 文件顺序或候选数量选题：
   - 已验证 risk observation 与其实际 boundary/plane；
   - 外部可观察且原文适用的设计 claim；
   - capability surface 与构建、注册、入口、配置或邻近能力之间的对账缺口。

每个 task 只有一个 `claim_id`、一个独立行为分支和一组明确 execution planes。code-to-design task 必须引用与 task 共享 boundary/plane 的 risk observation；平行 planes 拆为不同 task并复用同一 `parallel_path_id`。能力缺失 task 在入队前必须先有 catalog/product scope 的正面设计证据，不能因为“仓库没搜到”而优先入队。

先写累计 `${STATE_ROOT}/claim_review_scope.json`：

```json
{
  "session_id": "当前 session",
  "round_id": "ROUND-...",
  "design_claims_sha256": "当前 design_claims.jsonl 的 SHA-256",
  "claim_ids": ["已有 task/finding 使用的 accepted claim 与本轮待审 claim 的去重并集"]
}
```

再启动 fresh spec-critic Task。它只读 scope、scope claims、同组 claim 摘要、这些 claims 所属设计文档组、`design_agent_manifest.json` 和可选的上一版 `design_claim_review.json`，禁止读取代码、risk、architecture、task、finding 或结果。仅当上一版 review 记录的 claims、coverage、manifest 三个 digest 与当前完全相同时，fresh Task 才可逐值复用旧 accepted claim review；它仍要深审本轮新增 claim 及受新增 scope 影响的 group，并完整重写累计 `design_claim_review.json`。任一设计 digest 变化时重审当前 scope，不复用旧项。随后运行 claim-check。任何 repair 都回到 fresh spec analyst/spec critic；orchestrator 不得改写 claim 或 review。已经被 task/finding 使用的 accepted claim 不得删除或改写；尚未建 task 的本轮待审 claim 若被要求拆分/替换，可以从 scope 移除并以新 ID 原子替换：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py claim-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

只有 scope 内 claim 全部 accept 后，才把本轮最多 4 个任务写入 `${STATE_ROOT}/handoffs/plans/<round_id>.json`，原子合并并运行 task-check。首轮只要求至少一个来自真实 risk observation 的高风险 boundary 锚点；其他 boundary、plane 和 mode 由每轮 coverage 形成后续小批次，不在首轮一次冻结：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/plans \
  --output ${STATE_ROOT}/investigation_tasks.jsonl --key task_id \
  --artifact-type task --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/task-handoff-merge.json
```

merge 或 task-check 失败时不得启动 investigator。每个 task 必须恰好属于一个 investigation round，每轮最多 4 个；当前 round 的 pending/in-progress frontier 未清空前，不得创建下一轮或插入 opportunistic task。`handoff_template.py` 只允许按 round 中的 task 顺序为最前两个 pending task 建模板，因此不能绕过已规划的高风险任务去先做更容易的候选。

### 3.3 调查整轮，再做 coverage

4. 按冻结顺序启动 fresh investigator Task。每个 Task 先读 `work/skills/code-investigator.md`，只在 review roots 即时搜索、读调用链、配置、构建、平行实现和测试；每批最多 2 个 Task。每项只写独立 handoff，聊天只返回路径。每个 finding 都要有静态设计/代码证据、至少两项反证和 `dynamic_probe_selection`；测试可用性不能降低静态证据要求。

每个 Task 启动前先生成 pristine template（`BATCH_ID` 和 `TASK_ID` 由本轮模型计划决定，不是固定项目值）：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_template.py \
  --tasks ${STATE_ROOT}/investigation_tasks.jsonl \
  --claims ${STATE_ROOT}/design_claims.jsonl --task-id ${TASK_ID} \
  --output ${STATE_ROOT}/handoff-templates/investigators/${TASK_ID}.json --force
```

`--force` 只重建与最终 handoff 分离的 pristine template，供 provider retry 使用，不覆盖 investigator 证据。失败 merge 的 repair Task 不生成新模板，直接复用已有 pristine template。Task prompt 必须包含 template 路径、最终 handoff 路径，以及下面的 self-check 命令；Task 只有在 self-check report passed 后才能返回：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --check-file ${STATE_ROOT}/handoffs/investigators/${TASK_ID}.json \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${REVIEW_CODE_ROOT} --design-root ${REVIEW_DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-check-${TASK_ID}.json
```

self-check 与主 merge 会逐值校验 template 预填的 identity、claim、expected behavior、design evidence 和 lenses，模型不得改写这些设计/计划拥有的字段。

每批结束后执行原子 merge；只有 report 的 `passed=true` 且本批 finding ID 全部包含在 `validated_ids` 中，才能进入下一批：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/investigators \
  --output ${STATE_ROOT}/investigation_findings.jsonl --key finding_id \
  --artifact-type finding --session-id ${SESSION_ID} \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --report ${LOG_ROOT}/trace/finding-merge-${BATCH_ID}.json
```

merge 返回非 0 时，**禁止启动下一批**；`investigator_batch_gate.json` 会同时锁住新的 template 创建。只为 report 的 `invalid_ids` 启动 fresh repair Task，给它原 handoff、pristine template、错误列表和相同 self-check；修复并重新 merge 到 passed 前不得推进。模板 helper 同时限制最多两个尚未通过 merge 的 finding。搜索命中或无命中本身不是结论。
   本批所有已创建 pristine template 的 finding ID 构成 expected set；merge report 必须同时给出 `expected_ids/missing_ids/validated_ids`。缺少任一 handoff 时 merge 必须失败且锁保持关闭，不能用部分批次的 `passed=true` 启动下一批。成功 merge 自动把 finding 关联的 task 转为 complete、刷新 task report/digest，并写结构化 session lifecycle event。
   Task 因 provider timeout、stream timeout 或工具错误返回且没有 handoff 时，只为缺失项启动一次 fresh 重试；不得重跑已有有效 handoff，也不得让整批永久等待。第二次仍失败则在 task 写 `status=deferred`、`defer_reason` 和 `defer_evidence={kind: provider_failure|tool_failure, attempts:[至少两项 attempt_id/outcome=failed/evidence]}`；merge/template gate 只对这种结构化失败退休 expected template，继续其他任务并由 coverage audit 保留缺口。这只是 session 恢复，不得用普通时间/portfolio 理由 deferred，不得改用规则/regex 或手工 verdict 兜底。
5. 本轮所有 investigator handoff 合并且 task 全部 complete/deferred 后，立即启动 fresh coverage Task，先读 `work/skill/SKILL.md` 与 `work/skills/coverage-critic.md`。输入只包含 workspace/contract/architecture/design/claims/scope/risks/tasks/findings/rounds，不读取 probe、critic 或 verdict。输出 `semantic_coverage.json` 与 `coverage_audit.json`，随后运行 coverage-check。

coverage 必须检查设计组、scope behavior、通用 lens、三种 exploration mode、high-risk boundary、parallel plane、未映射 risk 和未完成 task。`next_round_tasks` 只能来自具体证据缺口，不能来自“候选还不够”或某个数量目标。若不为空，先扩展累计 claim scope，再按 3.2 创建下一轮；不得运行中间 judge。

semantic coverage 的 investigated lens 必须引用声明该 lens 的 completed task 与直接 finding；不能借用无关 ID。每个 parallel path 必须由直接 task/finding 覆盖全部 plane。只有进入累计 claim scope 的 claim 才是本 session 的可执行 frontier；未入 scope 的 design index 不得被伪装成已调查，也不得因为 priority 标签自动制造无法在时限完成的全量工作。

每轮追加 `investigation_rounds.jsonl`，字段固定为 `round_id,session_id,strategy,exploration_modes,document_groups,architecture_boundaries,implementation_planes,lenses,claim_ids,task_ids,finding_ids,outcome,next_strategy`；每个交接用 `session_event.py` 写 checkpoint。候选和下一步必须由模型根据当前设计与代码证据选择；不得使用项目特例、固定文件/符号、关键词表、domain map、分数或公开答案。

在约 40% 时间点前，coverage 必须已对通用 lens 给出 investigated 或有证据的 inapplicable，并优先补齐集合/隐藏上限、时序/延迟/主动副作用、链/嵌套/重复元素、跨边界分派/所有权、导入与平行路径等代码风险；不能仅为凑 lens 标签创建宽泛任务。连续两个同类 finding 合规时切换文档组、plane 或 lens。

详细 artifact schema、证据标准和时间分配只以 `work/skill/SKILL.md` 为准。

### 3.4 coverage 闭环后才裁决候选

6. 只有 `coverage_validation.json` 同时 `passed=true`、`closed=true` 且 `next_round_tasks=[]` 后，才处理 `contradiction_supported|uncertain` candidates：
   - 对少量高价值、可观察、低成本且环境已有依赖的候选执行 design-grounded dynamic probe；baseline/环境失败或未证明目标路径触达时一律 inconclusive。
   - 对每个候选启动一个 fresh evidence-critic Task，独立重读设计/代码并做至少两项反证检查。无效 critic handoff 只能重跑该 critic；不得由 orchestrator 补字段。相同 evidence 只允许一个当前 critic，只有 finding/probe 新增证据后才能 revision。
   - critic 要求补证时，把问题变为新 investigation task，重新打开 coverage；不能先 judge。

7. coverage 仍闭环且所有候选都有有效 critic 后，对当前闭环 frontier 只启动一个 fresh final-judge Task。它只做最终状态映射与事实化表达，逐值复制 claim/finding/critic/probe 证据，不能引入新证据或改行号。judge 不接收任何目标 issue 数量，也不得以数量调整置信度。随后立即运行 review：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py review \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

review 失败时只按最早出错角色修复；若 task、finding、round、scope 或 architecture 改变，必须重新 coverage-check，确认仍 closed 后才重跑 judge。仅修复同一 finding 的 probe/critic handoff 不改变 coverage 输入，无需重复 coverage Task。review 通过后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py report \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py gate \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

若 gate 仅报告最终比赛配额未满足，下一轮仍只能从未触达 boundary/plane、未映射 risk、未覆盖 design behavior 或 critic 的具体拒绝原因中选题；禁止创建“容易证明缺失”的任务凑数。最后必须运行同参数的 `goal_runner.py finalize`；只有该命令返回 0 且 `final_gate.json.passed=true` 才能向平台回答完成。

所有角色和 repair 都使用统一 no-progress 规则：以 `角色 + artifact + 当前输入 digest + 错误摘要` 写入 `agent_run_ledger.jsonl`。同一签名第一次失败后只允许一次 fresh retry；第二次 provider/tool 失败或输出 digest、错误摘要均未变化时，将 session 写为 `blocked`、记录两次 attempt 证据并停止，不得第三次原样重试、等待旧会话、改用规则 fallback 或谎报完成。artifact 或任务范围有实质变化时才是新 attempt 签名；investigator 的 deferred 仍额外遵循 3.3 的结构化 retirement。每次启动 Task 前检查 `agent_loop_state.deadline_at`；剩余时间不足以完成该批时不再启动新 Task。只有 frontier 已 drain、coverage closed 且 critic/judge 齐全时才进入 review/report/gate，否则写 `status=blocked,current_phase=time_limit,stop_reason=insufficient_time_for_next_batch` 并停止，不能调用必然失败的 finalization。

成功条件只由最终 gate 判定；orchestrator 不使用 issue 数量选题或提前停止。只发布唯一且真实的 confirmed finding；每个 issue 有设计原文与章节、代码文件与行号、expected/actual、差异原因、功能影响、两项误报排除、dynamic validation disposition、独立 critic、置信度和 tool trace；JSON/JSONL/Markdown 与 `validated_issues.json` 逐项绑定；目标树哈希未变化；coverage 闭环；总时长不超过 6 小时。

最终必须生成：

```text
result/issues.json
result/issues.jsonl
result/00-summary.md
result/01-*.md
logs/trace/evidence_validation.json
logs/trace/final_gate.json
```
