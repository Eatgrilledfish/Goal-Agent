# Goal-Agent 自验证记录

## 运行入口

评测平台由运行中的 OpenCode CLI 读取根目录 `INSTRUCTION.md`，再调用 `work/skill/SKILL.md`、角色定义和 deterministic helpers。系统自动发现赛事代码与设计材料，只读目标树，只在 `result`、`logs` 和 session state 中写产物；正式链路不需要 `opencode.json`、注册步骤或人工参数。

语义判断由模型角色完成。脚本只负责输入隔离、schema/reference/lifecycle 校验、证据逐行验真、原子 merge、报告和 final gate，不使用项目名、固定路径、协议表、关键词命中、regex 或公开答案决定 issue。

## 当前流程

1. `prepare` 建立只读快照和 session；主 agent 生成 architecture map。
2. design-only spec analyst 与 code-only risk explorer 在 architecture-check 后并行运行。design claims 是检索索引，不是必须逐条证明的任务队列。
3. orchestrator 从真实 risk、外部设计义务和 capability surface 选择小范围 `claim_review_scope`；fresh spec critic 只深审新增范围，可在设计输入 digest 未变化时复用旧 accepted review。
4. 每轮最多 4 个 task、最多 2 个并发 investigator。首轮只要求一个 risk-backed 高风险锚点；当前轮未 drain 前，validator 拒绝创建后轮或跳到容易候选。
5. 每轮调查结束先运行 coverage critic。只有具体 scope、lens、mode、boundary、parallel plane 或 risk 缺口才能创建下一轮；coverage closed 前不运行 evidence critic 或 judge。
6. coverage closed 后才选择少量 design-grounded probe、为每个候选启动 fresh critic，并对当前闭环 frontier 做 final judge。probe 的环境失败或不可达只能是 `inconclusive`。
7. `review` 将 claims/findings/critics/probes/verdicts 的 digest 绑定到 `validated_issues.json`；report 和 final gate 都拒绝 stale revision。最终只发布 confirmed issue。

## 验证结果

执行命令：

```bash
python3 -m pytest -q work/tools/tests --tb=short
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

结果：

```text
140 passed in 46.43s
py_compile: passed
git diff --check: passed
```

测试覆盖 scoped claim review、claim repair、四任务 round 上限、FIFO handoff、禁止提前创建后轮、risk/task 引用、coverage closed、critic 严格 schema、evidence freshness、报告/final gate、一阶段 plan/coverage replay，以及完整 session 的隔离 gate replay。运行资产的项目特例扫描只命中测试中的禁止词回归清单，未命中正式 instruction、skill、role 或 helper。

## 当前状态与风险

本轮修改前正在运行的后台 OpenCode 已主动发送 SIGTERM 停止，退出码 143；日志和 session state 保留，未在修改后重新启动全量模型验证。因此当前结论是 deterministic 契约与测试已通过，公共基准的最新端到端召回仍需下一次独立全量运行验证。

`review_context=fresh_subagent` 只是 handoff 策略声明，同一可写文件系统无法由作品自身提供平台级 Task 身份证明；当前防线是强制角色 Task、严格字段/引用/digest 契约、独立 handoff 和禁止 orchestrator 补语义字段。最终 confirmed 配额只存在于比赛 final gate，不传给 investigator、critic 或 judge，也不参与 issue 判定。
