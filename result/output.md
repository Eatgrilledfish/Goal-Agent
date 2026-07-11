# Goal-Agent 自验证记录

## 作品入口

评测平台加载根目录 `INSTRUCTION.md`，由运行中的 OpenCode 主 agent 读取
`work/skill/SKILL.md`、角色定义和 deterministic helpers，执行通用设计/实现语义
一致性检视。目标代码与设计文档只读，结果写入 `/result`，session、handoff 与验证
轨迹写入 `/logs`。正式链路不需要 `opencode.json` 或人工参数。

## 本地验证命令

```bash
python3 -m pytest -q work/tools/tests
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

当前结果：`107 passed in 21.42s`；脚本编译与 diff whitespace 检查通过。

开发期单阶段回放（不属于比赛入口，也不会调用 LLM）：

```bash
python3 work/tools/scripts/stage_replay.py claims \
  --source-state logs/state --replay-root .agent-work/replays/old-claims \
  --run-local --force
python3 work/tools/scripts/stage_replay.py risk \
  --source-state logs/state --replay-root .agent-work/replays/old-risk --force
```

冻结旧 session 的 claims gate 在约 0.5 秒内稳定定位 36 个上游 claim/coverage
契约缺口；risk replay 只暴露 architecture、contract 中的 lens 和代码 review root，
不暴露设计、旧 finding 或结果。测试还验证了一个完整 session 可复制到隔离目录并
重新执行真实 final gate，结果通过。

## 当前实现结论

- helper 只负责输入物化、schema/reference/lifecycle 校验、证据逐行验真、报告和 gate；
  不用 regex、关键词、项目名、协议表或公开答案决定 issue。
- `design_agent_manifest.json` 是设计侧脱敏输入；spec analyst/critic 看不到代码清单。
- fresh code-only risk explorer 从真实边界、执行 plane、配置与平行路径反查设计问题，
  observation 不包含 verdict。
- claim review 在 investigation 前独立检查 quote entailment、normative strength、
  atomicity、applicability 及文档组遗漏，并绑定当前输入 digest。
- architecture、task portfolio 和 coverage 都有早期 gate；三种 exploration mode 必须由
  completed task/finding 证明，不能只写 round 标签。
- 每个 reviewable finding 必须进入 fresh critic，每个 finding 都必须有 final-judge
  verdict；候选不能因未发布而无声消失。
- finding merge 自动完成关联 task 并刷新 digest-bound provenance；typed handoff 使用
  独立文件、最多两个并发任务和原子 merge。
- 单点动态测试只用于增强或反驳已有语义证据；baseline、环境或可达性失败一律
  `inconclusive`，不能单独确认不一致。
- final gate 绑定 JSON/JSONL/Markdown、唯一 finding、完整 handoff、覆盖闭环、目标树
  未修改和六小时时限。
- `stage_replay.py` 仅用于本地冻结阶段调试，未被 `INSTRUCTION.md` 或正式
  `goal_runner.py` 调用；它会拒绝位于代码、设计、结果、日志或 review root 内的
  replay 目录，`--force` 不能删除输入。

项目主 Skill 的 frontmatter 已用等价只读校验通过；Skill Creator 自带
`quick_validate.py` 在当前 Mac Python 环境缺少 PyYAML，因此未安装额外依赖。
正式 F-Stack 全量 OpenCode 自验尚未在本次修改后重跑；启动时将使用后台进程、持久
日志、PID 与退出码记录，由人工消息触发进度读取，不进行高频轮询。
