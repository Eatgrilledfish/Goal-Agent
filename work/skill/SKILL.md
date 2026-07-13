# Goal-Agent 通用设计/实现一致性检视

本 Skill 由 `INSTRUCTION.md` 驱动。目标是从本次 supplied design 与代码仓中发现语义差异，生成可机器读取的 issue 和证据链；不修改目标代码，不依赖项目名、协议名、固定符号、正则规则或已知答案。

## 核心分工

- Orchestrator：发现输入、建轻量 architecture map、调度与调用确定性 helper；不代替专业角色写语义结论。
- Obligation Extractor：只读一个有界设计切片，把可实现的规范拆成source-bound原子义务，不读取代码或判断差异。
- Semantic Scout：逐条消费原子义务队列，或从互斥代码 anchors 反向检索设计，只输出有差异信号的原子候选。
- Spec Critic：只读设计，验证候选中的规范引用、强度、原子性和适用性。
- Code Investigator：沿候选锚点证明实际行为和反证，输出 contradiction/satisfied/uncertain。
- Evidence Critic：fresh session 独立挑战每个 finding。
- Final Judge：只将已经闭环的 finding 映射为 confirmed/probable/rejected。

并发上限为 2。只有互斥的 design section ownership、互斥 code anchors 或不同 candidate 可以并发；同一 candidate 的 investigation → critic 严格串行。Active optional probe在本次比赛链路中暂停。

## 不变量

1. 输入语义来自当前 design/code。禁止读取公开答案、旧 result 或以 issue 数量决定候选。
2. Design-to-code scout 搜索整个代码仓，不能被预先 architecture map 裁剪。Design plan由当前输入规模推导，每个slice硬上限1200行且只包含1个文档的连续range；大文档可拆成多个连续chunks，全部in-scope sections全局唯一owner。Code plan按architecture map中的实际scope递归拆成互斥primary anchors，每个code slice最多覆盖1200个文件；不得用粗粒度根目录替代必要的递归分片。
3. Design slice先由fresh extractor完整生成原子义务队列，再由另一个fresh scout逐义务对照代码。Scout只输出疑似不一致，零候选合法；receipt必须精确绑定queue中的每个obligation或plan中的每个anchor，所有current plan receipts完成前不能全局选择候选。
4. Catalog只定位/追溯正文，architecture只导航代码，二者都不能产生设计义务。测试缺失不是运行实现缺失；测试代码只作静态反证线索，本次链路不执行dynamic probe。
5. 模型只写语义字段，不复制session、sweep、digest、direction或architecture IDs。Helper从current plan与source-bound obligation queue注入机械envelope，并把requirement、source range、code anchors、direction 与 mismatch signal逐值投影到claim和task；orchestrator不得重写。
6. 搜索无命中、构建失败或环境失败不单独证明能力缺失。Raw scout可凭原子设计义务、真实代码lead或结构化absence lead和最低限度反证输出`uncertain`候选，每slice最多12条；不在raw阶段强制闭合完整入口、替代或补偿路径。能力缺失的入口、构建、注册、配置、邻近能力、依赖和误报闭环由investigator证明，并由critic独立挑战。
7. Schema/路径/行号错误在原角色内修；语义 repair 最多一次 fresh session。Bootstrap后Controller是current phase唯一真相源，角色checkpoint只追加ledger；只有 final gate 可关闭 session。
8. 目标代码与 supplied design 只读。比赛运行写入仅限`${STATE_ROOT}`、`${LOG_ROOT}` 与 `${RESULT_ROOT}`；不得修改`/work`，不得创建或读取 `opencode.json`。
9. Semantic subagent只写自己的isolated semantic handoff；orchestrator独占materialize、check、merge和共享ledger发布。单实例Final Judge直接写verdict JSONL是唯一明确例外。

## Architecture map

Architecture map 是冻结的代码导航辅助，不是设计来源、候选过滤器或task语义外键。保存为 `${STATE_ROOT}/architecture_map.json`：

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

只写真实存在的路径和关系。Imported、adapter、fast/slow 和 integration layer 不能因不是主实现而遗漏；`test_surfaces`只作导航，不能删除plane、ownership或candidate。地图遗漏不会阻止 design scout 在全仓找到代码。

## Model loop

Prepare后先严格执行 `map_architecture → build_inventory → build_scout_plan` 三个bootstrap动作以及各自validator；准确命令和architecture checkpoint见`INSTRUCTION.md`第4节。`risk-plan-check`通过前controller尚未接管，不能把缺少plan时的`finish_scouts`当成可调度scout。

Bootstrap完成后，每次准备下一动作时运行：

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

确定性 helper 只校验/物化 provenance、source authority、scope ownership和状态，不做 semantic ranking 或 verdict。全链路使用同一组`contract_mechanics`、`temporal_conditional`、`routing_capability` review vocabulary，不再维护另一套行为×lens矩阵。模型只在以下位置做判断：原子义务提取、逐义务/anchor差异候选、最多 12 个 candidate ID 排序、spec review、代码调查、反证和最终状态映射。

## Candidate 与调查

Design slice先严格遵循`work/skills/obligation-extractor.md`生成义务，再由`work/skills/risk-explorer.md`逐义务比较；code slice由同一scout逐anchor反查。候选全部完成后，orchestrator只写：

```json
{"candidate_ids":["按证据强度排序的 observation_id，最多12个"]}
```

只从规范正文且runtime实现证据成立的候选中选择。优先直接规范冲突、外部可观察行为、跨实现不对称、结构化能力缺失与精确代码位置；不做文档配额，不选择catalog/architecture推导、测试缺失、重复项或合规样本。`candidate_pipeline.py` 负责生成 lookup/claim/task。

Code Investigator 严格遵循 `work/skills/code-investigator.md`，只写最小语义文件；orchestrator调用`finding_materializer.py`复制冻结字段和源码 snippet，再check/merge并刷新task lifecycle。每个 finding 都必须由 fresh Evidence Critic 独立复核，不使用投票。本次运行所有finding的probe disposition均不得为`selected`。

## 输出和 gate

Report writer会在finalize内先写provisional文件供final gate验真；只有current traces、coverage、verdict validation 与 final gate全部通过后，这些文件才是有效最终结果。必须产生：

- `/result/issues.json`
- `/result/issues.jsonl`
- `/result/00-summary.md`
- `/result/01-*.md` 等单 issue 报告

每个 issue 至少包含：差异描述、设计证据和章节、代码证据和行号、原因、误报排除、置信度。Confirmed 只来自规范适用、代码可达、实际冲突和反证均闭环的 finding；证据未闭环用 probable，不得为达到数量降低标准。
