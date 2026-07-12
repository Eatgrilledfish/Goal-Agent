# Goal-Agent 通用设计/实现一致性检视

本 Skill 由 `INSTRUCTION.md` 驱动。目标是从本次 supplied design 与代码仓中发现语义差异，生成可机器读取的 issue 和证据链；不修改目标代码，不依赖项目名、协议名、固定符号、正则规则或已知答案。

## 核心分工

- Orchestrator：发现输入、建轻量 architecture map、调度与调用确定性 helper；不代替专业角色写语义结论。
- Semantic Scout：以设计文档组或互斥代码 anchors 为入口，读取规范、搜索代码，只输出有差异信号的原子候选。
- Spec Critic：只读设计，验证候选中的规范引用、强度、原子性和适用性。
- Code Investigator：沿候选锚点证明实际行为和反证，输出 contradiction/satisfied/uncertain。
- Evidence Critic：fresh session 独立挑战每个 finding。
- Final Judge：只将已经闭环的 finding 映射为 confirmed/probable/rejected。

并发上限为 2。只有互斥的 design document ownership、互斥 code anchors 或不同 candidate 可以并发；同一 candidate 的 investigation → optional probe → critic 严格串行。

## 不变量

1. 输入语义来自当前 design/code。禁止读取公开答案、旧 result 或以 issue 数量决定候选。
2. Design-to-code scout 搜索整个代码仓，不能被预先 architecture map 裁剪；code-to-design scout 的 primary anchors 必须互斥。
3. Scout 只输出疑似不一致，零候选合法；所有 scout receipts 完成前不能全局选择候选。
4. 候选、claim 和 task 的 requirement、source range、code anchors、direction 与 mismatch signal 由 helper 逐值投影，orchestrator 不得重写。
5. 搜索无命中、构建失败或环境失败不单独证明能力缺失。能力缺失需检查入口、构建、注册、配置、邻近能力和依赖。
6. Schema/路径/行号错误在原角色内修；语义 repair 最多一次 fresh session。局部 checkpoint 完成不等于全局完成，只有 final gate 可关闭 session。
7. 目标代码与 supplied design 只读。写入仅限 `/work` 交付工具自身、`${STATE_ROOT}`、`${LOG_ROOT}` 与 `${RESULT_ROOT}`；比赛运行时不得创建或读取 `opencode.json`。

## Architecture map

Architecture map 是代码导航辅助，不是候选过滤器。保存为 `${STATE_ROOT}/architecture_map.json`：

```json
{
  "session_id":"当前 session",
  "repository_summary":"事实摘要",
  "languages":["..."],
  "entrypoints":[{"path":"相对路径","purpose":"...","evidence":"..."}],
  "subsystems":[{"subsystem_id":"SUBSYSTEM-...","name":"...","paths":["..."],"role":"..."}],
  "implementation_planes":[{"plane_id":"PLANE-...","kind":"owned|adapter|imported|generated|fast_path|slow_path|other","paths":["..."],"reachable_evidence":"..."}],
  "integration_boundaries":[{"boundary_id":"BOUNDARY-...","name":"...","paths":["..."],"plane_ids":["PLANE-..."],"risk":"high|medium|low","why":"..."}],
  "capability_surfaces":[{"surface_id":"CAPABILITY-...","paths":["..."],"declares_or_registers":"..."}],
  "configuration_surfaces":[],
  "alternate_execution_paths":[],
  "test_surfaces":[],
  "parallel_behavior_paths":[{"path_id":"PARALLEL-...","behavior":"...","plane_ids":["PLANE-...","PLANE-..."],"evidence":"..."}],
  "probe_capabilities":{"isolated_copy_feasible":true,"available_runtime":[],"constraints":[]}
}
```

只写真实存在的路径和关系。Imported、adapter、fast/slow 和 integration layer 不能因不是主实现而遗漏；但地图遗漏不会阻止 design scout 在全仓找到代码。

## Model loop

每次准备下一动作时运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/pipeline_controller.py status --state-root ${STATE_ROOT}
```

按唯一 `next_action` 执行，不跳阶段：

```text
finish_scouts
→ select_candidates
→ review_claims
→ plan_investigations
→ finish_investigations
→ finish_critics
→ run_final
```

确定性 helper 只校验/物化 provenance，不做 semantic ranking 或 verdict。模型只在以下位置做判断：scout 候选、最多 12 个 candidate ID 排序、spec review、代码调查、反证和最终状态映射。

## Candidate 与调查

Semantic Scout 严格遵循 `work/skills/risk-explorer.md`。候选全部完成后，orchestrator 只写：

```json
{"candidate_ids":["按证据强度排序的 observation_id，最多12个"]}
```

优先直接规范冲突、外部可观察行为、跨 plane 不对称、结构化能力缺失与精确代码位置；不做文档配额，不选择合规样本。`candidate_pipeline.py` 负责生成 lookup/claim/task。

Code Investigator 严格遵循 `work/skills/code-investigator.md`，只写最小语义文件；`finding_materializer.py` 负责复制冻结字段和源码 snippet。每个 finding 都必须由 fresh Evidence Critic 独立复核，不使用投票。

## 输出和 gate

只有 current traces、coverage、verdict validation 与 final gate 全部通过才写最终结果。必须产生：

- `/result/issues.json`
- `/result/issues.jsonl`
- `/result/00-summary.md`
- `/result/01-*.md` 等单 issue 报告

每个 issue 至少包含：差异描述、设计证据和章节、代码证据和行号、原因、误报排除、置信度。Confirmed 只来自规范适用、代码可达、实际冲突和反证均闭环的 finding；证据未闭环用 probable，不得为达到数量降低标准。
