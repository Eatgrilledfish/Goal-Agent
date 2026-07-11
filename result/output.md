# Goal-Agent 自验证记录

## 运行入口

正式评测只需让运行中的 OpenCode CLI 读取仓库根目录 `INSTRUCTION.md`。主 Agent由当前比赛目录自动识别代码仓与设计资料，随后按入口调用 `work/skill/SKILL.md`、角色定义及 `work/tools` 中的确定性 helper；不需要注册、人工参数、provider 配置或 `opencode.json`。

运行开始先建立不可重置的六小时 clock，再读取 catalog 或物化外部设计来源。模型负责文档适用性、设计义务、代码行为、反证和 verdict 等语义判断；helper只负责 source range物化、schema、身份/digest、candidate隔离、lifecycle、证据验真、只读完整性、报告和 final gate。运行链路没有项目名、协议名、关键词/regex、固定答案、评分表或 issue数量 fallback。

## 当前实现

1. `prepare` 冻结目标代码、设计 bundle 与 session-local review snapshot。Catalog 分支必须携带 materialization manifest；原 catalog/source 全树、plan 与生成 bundle分别快照，resume和final gate均复扫，遗漏 manifest不能创建一个可绕过来源校验的 session。
2. Architecture map与轻量 design inventory并行。Inventory只建立 document group、scope relation、section和behavior-family地图，不预生成全量 claim。
3. Risk plan依据真实耦合 component划分互斥 slice，全局最多并发两个 Task。每个 code-only explorer独占 candidate目录，按 `sweep_id` 增量合并，失败 peer不进入当前 merge。
4. 主 Agent用模型语义形成 design section/义务、code risk/capability、boundary/plane与可证伪 hypothesis组成的 evidence pair。只有进入 frontier 的义务才按需生成 claim；quote、heading、canonical path和source hash由 source ref确定性物化。
5. Fresh Spec Critic按 per-claim digest审查 entailment、strength、atomicity和applicability。无关 claim变化不使已接受 review失效；group gap进入coverage，不阻塞有效 claim。
6. 每个 task只绑定一个 claim branch、一个 hypothesis与一个 obligation digest。Task plan和lifecycle分离；retained peer的证据、template或上游绑定失效不会阻塞当前有效candidate，但最终 gate仍拒绝未修复 peer。
7. Investigator、可选 focused probe和Evidence Critic按candidate严格顺序执行。Probe只能在session副本运行，绑定设计oracle、baseline、non-triviality、secondary oracle与reachability。Critic历史由helper专有账本保存；相同evidence不能删除当前ledger后重新投票，只有新claim/finding/probe证据才允许revision。
8. Coverage在初始frontier后记录未覆盖section、boundary/parallel path、lens、mode、frontier和critic请求，最多产生一次由`source_gap_ids`绑定的supplement。请求由helper-owned历史与ledger事件冻结，不能清空、换题或只修改轮次计数。
9. 每个risk sweep、investigation、probe和critic均需candidate级rich checkpoint与独立provider session；相同输入/artifact/error第三次无进展重试会被gate拒绝。Final Judge统一形成每个finding的current/latest verdict，最终只发布证据闭环的confirmed，不设通用issue数量配额。
10. Development stage replay覆盖inventory、claims、claim-review、risk、plan、investigator、probe、critic、judge、coverage与gate。Catalog型gate replay会把原source/plan复制并重写到隔离replay中；带动态probe的coverage和完整gate也可真实本地回放。

## 自验证命令与结果

执行：

```bash
python3 -m pytest -q work/tools/tests --tb=short
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

结果：

```text
317 passed in 114.19s
test_stage_replay.py: 40 tests collected
py_compile: passed
git diff --check: passed
```

回归范围包括：

- raw inventory/claims的确定性quote物化、严格source range和损坏输入；
- per-claim review、non-blocking group gap与无关claim增量稳定性；
- 互斥risk slice、增量merge、原子task及plan/lifecycle分离；
- retained invalid peer、stale template和candidate独立merge；
- early critic、critic evidence/history、防重复投票与revision绑定；
- focused probe双oracle、隔离workspace、目标文件副本和finding→probe→critic顺序；
- coverage provenance、一次supplement请求/任务绑定及历史清空负例；
- candidate级fresh provider、rich trace、attempt/repair/no-progress限制；
- immutable clock单点篡改、trace缺失和前置产物后的重置负例；
- direct design与catalog materialization source/plan/bundle完整性；
- 普通、catalog及dynamic-probe coverage/gate真实隔离回放；
- final judge、confirmed-only报告、输出绑定、目标树和review snapshot只读gate。

静态检查确认：正式runtime入口、Skill、角色与production scripts中没有F-Stack、公开六项答案、指定RFC/协议名称或固定confirmed配额；仓库不存在`opencode.json`。本地比赛路径 `/app/code/judge-assets/01_03_ai_implementation_design_difference_detection/` 已指向本地题目材料，正式环境使用同一路径无需额外参数。

## 尚待完整模型运行验证

当前记录证明确定性契约、候选隔离、stage replay与本地回归通过，但不能替代运行中的OpenCode模型评测。重构后的完整F-Stack运行尚未启动，因此以下指标仍必须由提交并推送后的独立后台全量验证给出：

- 已知隐藏正例的最终confirmed召回是否不少于4；
- 最终误报率是否不高于50%；
- 首个confirmed、各阶段耗时及总墙钟是否满足目标和六小时上限；
- OpenCode是否在真实长session中遵循并发、handoff、fresh critic、checkpoint与approval契约；
- 真实目标代码、设计来源与review snapshot在完整运行前后是否保持不变；
- 与已保留历史baseline相比的召回、误报、首个confirmed时间和阶段耗时。

Risk/Investigator/Probe/Critic/Judge 的development replay会生成冻结的精确输入与prompt envelope，但不会在单元测试中伪装或替代真实LLM调用。纯PDF/DOCX设计资料目前需要平台提供同名UTF-8文本导出以获得稳定行号证据；Markdown、纯文本、RST、AsciiDoc、YAML、JSON、TOML以及catalog提供的HTML/UTF-8 URL可直接进入当前链路。
