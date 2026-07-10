# Goal-Agent 自验证记录

## 作品入口

评测平台加载仓库根目录的 `INSTRUCTION.md`，由运行中的 opencode 读取
`work/skill/SKILL.md`、`work/skills/*.md` 和 `work/tools/` 后执行模型驱动的
设计/实现一致性检视。目标代码与设计文档保持只读，正式结果写入 `/result`，
session、handoff 和证据校验轨迹写入 `/logs`。

## 本地验证方式

```bash
uv run --offline --with pytest python -m pytest -q work/tools/tests/test_agent_pipeline.py
opencode run --auto --file INSTRUCTION.md "按照入口完整执行，持续到 final gate 通过"
```

完整语义检视不是 helper-only pipeline：`prepare` 之后必须由 opencode 按
`INSTRUCTION.md` 执行 spec analyst → investigator → 按需 dynamic probe → evidence
critic → final judge 交接，再运行 `review`、`report` 和 `gate`。dynamic probe
只在 session 隔离副本中运行；环境或 baseline 失败只能记录为 inconclusive。

## 当前自验证结论

- 通用 helper 单元/集成测试已通过。
- 正式链路不包含 regex 结论器、协议 domain map、项目名分支或公开答案调用。
- task/finding/probe/critic 在进入共享 ledger 前经过类型与 session 校验；finding 同时提前回读真实设计/代码行。
- spec analyst 产物在 investigation 前经过 `design-check`，逐项校验 coverage、claim schema、probe oracle 和真实设计行。
- validator 要求 judge 逐值复制 investigator/critic 证据，并核对 claim → task → finding → critic → verdict。
- subagent 采用最多 2 个并发的有界批次，避免突发长上下文 stream 阻塞整批 handoff。
- 每个 claim 都包含只由设计形成的 probe oracle；实现只能映射接口，不能改写预期。
- 单点 probe 是可选证据增强，不是规则 fallback；测试失败不能单独确认 issue，测试通过作为反证交给 fresh critic。
- final gate 会检查目标树未被修改、设计行为覆盖、独立 critic、输出完整性和 6 小时时限。
- 自动测试共 32 项，覆盖设计产物前置 gate、handoff 类型污染拒绝、虚假引用前置拒绝、judge 证据改写拒绝、合法 probe 闭环、oracle 污染拒绝、环境失败强制 inconclusive、只读目标和通用性守卫。
- `skill-creator` quick validation 已通过；核心路径项目特定词扫描和 `git diff --check` 已通过。

完整 F-Stack 语义自验结果将在更新后的 opencode 单次运行完成后写入本节。运行中的评测产物
`issues.json`、`issues.jsonl`、`00-summary.md` 和单 issue 报告由 report 阶段生成。
