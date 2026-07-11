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

## 3. 强制模型驱动 handoff

必须使用 opencode Task/子 agent。Task 缺失是平台阻塞；禁止主 agent 模拟多个角色、手工填 verdict 或用规则兜底。

1. 主 agent 作为 orchestrator 只在 `REVIEW_CODE_ROOT` 阅读仓库入口、构建/注册/配置和目录边界，写 `architecture_map.json`。明确 owned、adapter、imported、generated、fast/slow execution planes，为同一设计行为的平行实现写稳定 `path_id`；这里只做架构地图和 risk-explorer 的任务分区，不由主 agent 生成风险候选。同时只依据仓库和当前环境证据记录已有 build/test/runtime surface 与 dynamic probe 约束，不安装依赖。写完立即运行 `goal_runner.py architecture-check`；失败时只修 architecture map，在通过前不得启动其他语义阶段。
2. 启动 fresh Task，要求它先完整读取 `work/skills/spec-analyst.md`，只读 `REVIEW_DESIGN_ROOT`、`${STATE_ROOT}/design_agent_manifest.json` 与 catalog provenance，禁止读取完整 workspace manifest、代码或原始外部设计路径。为避免长 handoff 被截断，Task 直接写 `${STATE_ROOT}/design_coverage.json` 与 `${STATE_ROOT}/design_claims.jsonl`，聊天只返回计数和路径。Task 返回后立刻运行下列 gate；返回非 0 时不得创建 investigation task，必须让 fresh spec-analyst repair Task 按 `logs/trace/design_validation.json` 重写两个 artifact 并再次校验：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py design-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

结构通过后启动 fresh spec-critic Task，完整读取 `work/skills/spec-critic.md`。它只读 `REVIEW_DESIGN_ROOT`、design claims/coverage 和 `${STATE_ROOT}/design_agent_manifest.json`，禁止读取完整 workspace manifest、architecture、代码、risk、tasks、findings、旧结果或公开答案；直接写 `${STATE_ROOT}/design_claim_review.json`。随后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py claim-check \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

只有 claim review 顶层 decision=`accept` 且 digest-bound validation passed 才能继续。任一 claim/group 为 repair 时，把 review 交给 fresh spec-analyst repair Task，重写 claims/coverage 后依次重跑 design-check、spec-critic、claim-check；禁止由 orchestrator 手工修 claim。通过后主 agent 再随机重读来源核验。claims 是面向差异检视的有界 portfolio，不是穷举规范的每句话；每个适用 behavior family 至少一个独立 claim。不同角色、分支、数量边界、时序阶段、主动/被动行为或首项/全部元素语义不能压成同一个 claim，也不能只抽容易验证的 MUST。每个 claim 的 `probe_oracle` 必须是 SKILL 定义的对象，且在不读代码的前提下由设计产生；不适合单点运行验证时明确标记，不得编造测试。

3. 与 spec analysis 独立启动 fresh `risk-explorer` Task。每个 Task 先完整读取 `work/skills/risk-explorer.md`，只接收 `REVIEW_CODE_ROOT`、architecture map 中分配给它的 boundary/planes 和通用 lens；禁止读取设计、claims、旧 findings 或公开答案。必须覆盖每个 high-risk boundary 和 parallel path plane；若两者都为空，仍分配至少一个最具外部行为的真实 boundary/plane。Task 先从入口、构建/注册/配置和主要可达目录反查地图完整性；返回 `architecture_repair_required` 时先修 map、重跑 architecture-check，再用 fresh Task 重做，不能让地图自身的遗漏变成永久盲区。每批最多两个 Task，每项只写 `${STATE_ROOT}/handoffs/risks/<sweep_id>.json`，并先用 `handoff_merge.py --check-file --artifact-type risk --code-root ${REVIEW_CODE_ROOT}` 自检。批次完成后原子合并；risk ledger 不得为空：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/risks \
  --output ${STATE_ROOT}/risk_observations.jsonl \
  --artifact-type risk --session-id ${SESSION_ID} \
  --code-root ${CODE_ROOT} \
  --report ${LOG_ROOT}/trace/risk-handoff-merge.json
```

risk observation 只描述代码实际语义和设计检索问题，不产生 verdict。orchestrator 只能把其中去除代码路径、snippet 和 implementation 名称后的 `design_lookup_questions` 交给 fresh spec-expansion Task；它不是新角色，必须完整复用 `work/skills/spec-analyst.md`，只读 `REVIEW_DESIGN_ROOT`、`design_agent_manifest.json` 和当前 `design_claims.jsonl/design_coverage.json`，仅在设计原文真正支持时补充原子 claim/behavior family，并完整重写这两个共享 artifact；仍禁止读取 risk 原文、architecture 或代码。任何补充后必须依次重新运行 `design-check`、fresh spec-critic 和 `claim-check`，旧 validation trace 不可复用。

4. 主 agent 把 claims、架构边界和已验证 risk observations 变成 `investigation_tasks.jsonl`。**每个 task 必须只有一个 `claim_id` 和一个可独立裁决的行为问题**；同一章节里的不同分支或平行 execution plane 要拆成不同 task，并在拆出的 task 的 `parallel_path_ids` 复用同一个 path ID，禁止一题打包多个 claim 后省略 `claim_id`。code-to-design task 必须写至少一个与其 boundary/plane 相交的 `risk_observation_ids`；只写 mode 名称不算执行。首轮不是按 priority 顺序抽前若干条：必须先形成一个跨设计组、跨 execution plane 的组合，使 8 个当前适用 lens 各有独立 task，并覆盖每个 `parallel_behavior_paths`、每个 high-risk boundary，以及仓库中存在的 adapter/glue/imported/fast/slow plane。优先调查适用且外部可见的 mandatory/recommended/optional 行为；能力缺失必须结合 catalog scope、产品角色、构建/注册/入口证据判断，不因“可选”自动确认或拒绝。将任务写入 `${STATE_ROOT}/handoffs/plans/<round_id>.json` 后先执行：

```bash
python3 ${WORK_ROOT}/tools/scripts/handoff_merge.py \
  --input-dir ${STATE_ROOT}/handoffs/plans \
  --output ${STATE_ROOT}/investigation_tasks.jsonl --key task_id \
  --artifact-type task --session-id ${SESSION_ID} \
  --report ${LOG_ROOT}/trace/task-handoff-merge.json
```

合并后立即运行同参数的 `goal_runner.py task-check`。merge 或 task-check 失败时不得启动 investigator；只修复机器报告指出的 task/path/risk/portfolio 契约。
5. 按不同 claim/边界启动 fresh investigator Task；每个 Task 先读 `work/skills/code-investigator.md`，只在 `REVIEW_CODE_ROOT`/`REVIEW_DESIGN_ROOT` 即时搜索、读调用链、配置、构建、平行实现和测试，并输出相对路径。为控制资源并让缺失 handoff 能按项恢复，**每批最多同时启动 2 个 Task**；一批取得完成事件或按恢复规则处理缺失 handoff 后，才启动下一批。每个 finding 都写 `dynamic_probe_selection`，但测试可用性不能降低静态证据要求。并行 Task 禁止写共享 JSONL：每项只写 `${STATE_ROOT}/handoffs/investigators/<task_id>.json`，聊天只返回路径。

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
6. 对 `contradiction_supported|uncertain` findings 做模型驱动的 dynamic probe triage。只选择高价值、可观察、低成本且当前环境已有依赖的少量候选；不是数量门槛，也不是 regex/rule fallback。对选中项启动 fresh Task，复用 `work/skills/code-investigator.md` 的 dynamic probe 流程：逐字继承 claim.probe_oracle，从 `REVIEW_CODE_ROOT` 复制到 `${STATE_ROOT}/probes/<probe_id>/workspace` 后复用已有 build/test/runtime 入口，禁止写 review snapshot 或原目标、联网安装依赖或调用可变外部系统。先跑最小 baseline；baseline/环境失败、未完成执行或无法证明目标路径已触达时一律 `inconclusive`。每项只写 `${STATE_ROOT}/handoffs/probes/<finding_id>.json`，批次结束后用 `handoff_merge.py` 合并到 `dynamic_probes.jsonl`，参数必须包含 `--artifact-type probe --session-id ${SESSION_ID} --report ${LOG_ROOT}/trace/probe-handoff-merge.json`。测试失败不能单独确认 issue；测试通过是反证但不自动证明全面一致。
7. 对每个 `contradiction_supported|uncertain` finding 启动新的 fresh critic Task。critic 先读 `work/skills/evidence-critic.md`，只接收 claim、finding、关联 probe（如有）和 review roots，不接收 investigator 的聊天推理；只在 review roots 独立重读设计/代码并至少做两项反证检查，同时写 `dynamic_probe_review`。每项只写 `${STATE_ROOT}/handoffs/critics/<finding_id>.json`，批次结束后用 `handoff_merge.py` 合并到 `critic_reviews.jsonl`，参数必须包含 `--artifact-type critic --session-id ${SESSION_ID} --report ${LOG_ROOT}/trace/critic-handoff-merge.json`。合并失败时只重做 report 的 `invalid_ids`。每个 finding 只允许一个当前有效 critic；不得因为数量不足而对相同证据重复找 critic。只有 investigator 补充了新的可核验证据后才允许 revision，且必须覆盖同一 handoff 路径、使用新 `review_id` 并通过 merge 原子替换 ledger 中该 `finding_id` 的当前行；禁止追加重复行。
8. 启动一个 fresh final-judge Task，先完整读取 `work/skills/final-judge.md`。明确传入 `${STATE_ROOT}/design_claims.jsonl`、`${STATE_ROOT}/investigation_findings.jsonl`、`${STATE_ROOT}/critic_reviews.jsonl`、`${STATE_ROOT}/dynamic_probes.jsonl`，唯一输出为 `${STATE_ROOT}/agent_review_verdicts.jsonl`；首次为每个 finding 写一行，repair 才可追加同 finding revision，聊天只返回计数和路径。它必须为每个 finding 恰好生成一个当前 verdict，不能让未发布候选无声消失；只从通过结构校验的 claim、finding、critic 和 probe 生成 verdict。`design_evidence`、`code_evidence`、`expected_behavior`、`false_positive_checks`、`tool_trace` 与 `critic_review` 必须逐值复制对应 handoff；`actual_behavior` 必须逐值等于 finding 的 `observed_behavior`。禁止在 judge 阶段改写、补造或换行号。只有 investigator=`contradiction_supported` 且 critic=`confirm_contradiction` 才能 confirmed；实现满足设计必须 rejected。judge 返回后立即运行 `goal_runner.py review`，在 review 通过前不得启动 coverage；`contradiction_supported|uncertain` finding 缺 critic、任何 finding 缺 verdict 都必须回到对应角色补齐。
9. 启动 fresh coverage Task，要求它完整读取 `work/skill/SKILL.md` 和 `work/skills/coverage-critic.md`。明确传入 `${STATE_ROOT}/workspace_manifest.json`、`agent_loop_contract.json`、`architecture_map.json`、`design_coverage.json`、`design_claims.jsonl`、`risk_observations.jsonl`、`investigation_tasks.jsonl`、`investigation_findings.jsonl`、`dynamic_probes.jsonl`、`critic_reviews.jsonl`、`agent_review_verdicts.jsonl` 和 `investigation_rounds.jsonl`；唯一输出为 `${STATE_ROOT}/semantic_coverage.json` 与 `${STATE_ROOT}/coverage_audit.json`。审计文档组、behavior families、execution planes、边界和三种 exploration mode，同时严格按主 SKILL 写 artifact。返回后立即运行 `goal_runner.py coverage-check`；失败时按 `coverage_validation.json` 修复，禁止拖到 final gate。audit 必须给出结构化 `next_round_tasks`（claim、lens、mode、boundary、plane、parallel path、risk observation 和证据问题）；有高价值缺口时主 agent 必须执行这些任务或记录具体证据限制，不能只把 audit 改写成停止说明。明显可低成本动态复核却全部无理由跳过时属于证据缺口；不可构建、硬件依赖或不适合运行验证不属于失败。

semantic coverage 的每个 investigated lens 必须引用真正声明该 lens 的 completed task 及其直接 finding，且 design group/boundary 引用必须能由这些 task/claim 关联证明；不能借用无关 ID。每个 `parallel_behavior_paths.path_id` 聚合的 completed tasks/findings 必须覆盖其全部 plane，拆分任务可以联合覆盖但无关任务不能借用。未调查 high claim 只有在 `deferred_claims` 关联一个具有上述两次失败证据的 deferred task 时才可关闭；“不在本轮 portfolio/时间不够”不能把 actionable high claim 变成完成。

每轮追加 `investigation_rounds.jsonl`，字段固定为 `round_id,session_id,strategy,exploration_modes,document_groups,architecture_boundaries,implementation_planes,lenses,claim_ids,task_ids,finding_ids,outcome,next_strategy`；每个交接用 `session_event.py` 写 checkpoint。候选和下一步必须由模型根据当前设计与代码证据选择；不得使用项目特例、固定文件/符号、关键词表、domain map、分数或公开答案。

在约 40% 时间点前，让当前适用的通用 lens 都有独立调查：集合/隐藏上限、时序/延迟/主动副作用、推荐/可选行为、能力完全缺失、链/嵌套/重复元素、跨边界分派/所有权、导入与平行路径、错误/状态/配置。连续两个同类 finding 合规时切换文档组、plane 或 lens。

详细 artifact schema、证据标准和时间分配只以 `work/skill/SKILL.md` 为准。

## 4. 校验、输出、继续循环

一轮 judge 后先运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py review \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

review 通过后执行第 9 步 coverage audit 与 `coverage-check`。只有 coverage-check 通过且没有必须执行的 `next_round_tasks` 时，才运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py report \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
python3 ${WORK_ROOT}/tools/scripts/goal_runner.py gate \
  --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} \
  --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} --state-root ${STATE_ROOT}
```

review 必须在 judge handoff 后立即运行，不能先做 coverage 或追求更多数量。失败时启动一次 fresh evidence-repair Task；它不是新角色，必须完整复用 `work/skills/final-judge.md` 的 repair mode，只接收 `evidence_validation.json`、当前 verdict、关联 claim/finding/critic/probe 和 review roots。只可为报错 finding 向 verdict JSONL 追加一个同 `finding_id` 的新 revision，validator 使用最后一行；修订值仍必须逐值复制已有 handoff。若原 finding/critic 本身错误则回到对应角色修复后重新合并，禁止主 agent 手工拼装。coverage 产生下一轮任务时，执行新 investigator → critic → judge → review 后再重做 coverage；gate 失败就按 `final_gate.json` 从最早缺口开始下一轮。最后必须运行同参数的 `goal_runner.py finalize`；只有该命令返回 0 且 `final_gate.json` 的 `passed=true` 才能向平台回答完成。只要未到 6 小时上限，就不得把 gate 失败、零 finding、候选耗尽或成熟项目当作完成。

所有角色和 repair 都使用统一 no-progress 规则：以 `角色 + artifact + 当前输入 digest + 错误摘要` 写入 `agent_run_ledger.jsonl`。同一签名第一次失败后只允许一次 fresh retry；第二次 provider/tool 失败或输出 digest、错误摘要均未变化时，将 session 写为 `blocked`、记录两次 attempt 证据并停止，不得第三次原样重试、等待旧会话、改用规则 fallback 或谎报完成。artifact 或任务范围有实质变化时才是新 attempt 签名；investigator 的 deferred 仍额外遵循第 5 步结构化 retirement。每次启动 Task 前检查 `agent_loop_state.deadline_at`，剩余时间不足以完成该批时不再启动新 Task，转入已有产物的 review/report/gate。

成功条件：至少 4 个唯一且真实的 confirmed finding；只发布 confirmed；每个 issue 有设计原文与章节、代码文件与行号、expected/actual、差异原因、功能影响、两项误报排除、dynamic validation disposition、独立 critic、置信度和 tool trace；JSON/JSONL/Markdown 必须逐项绑定 `validated_issues.json`，重复 finding 不能计数；目标树哈希未变化；coverage 闭环；总时长不超过 6 小时。

最终必须生成：

```text
result/issues.json
result/issues.jsonl
result/00-summary.md
result/01-*.md
logs/trace/evidence_validation.json
logs/trace/final_gate.json
```
