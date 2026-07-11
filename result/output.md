# Goal-Agent 自验证记录

## 运行入口

评测平台由运行中的 OpenCode CLI 读取根目录 `INSTRUCTION.md`，再调用 `work/skill/SKILL.md`、角色定义和 deterministic helpers。系统自动发现赛事代码与设计材料，只读目标树，只在 `result`、`logs` 和 session state 中写产物；正式链路不需要 `opencode.json`、注册步骤或人工参数。

语义判断由模型角色完成。脚本只负责输入隔离、schema/reference/lifecycle 校验、证据逐行验真、原子 merge、报告和 final gate，不使用项目名、固定路径、协议表、关键词命中、regex 或公开答案决定 issue。

## 当前流程

1. `prepare` 建立只读快照和 session；主 agent 生成 architecture map，每个 integration boundary 明确关联 execution planes。
2. architecture-check 后生成绑定架构 digest 的 `risk_sweep_plan.json`。全部 mapped boundary、plane、parallel path 按共享 plane和重叠 architecture path 建成不可拆 component，再形成至少两个非空、互斥 focused slices。
3. 按 plan 顺序每批并发启动两个 code-only risk explorer，全局并发保持 2；单数尾批不复制范围。每个 agent 只拥有自己的 IDs、完整 architecture paths、JSON 数组 handoff 和 self-check report；全部 slices 完成后才原子重建共享 risk ledger。
4. risk merge 成功后再运行 design-only spec analyst。design claims 是检索索引，不是必须逐条证明的任务队列。
5. orchestrator 从真实 risk、外部设计义务和 capability surface 选择小范围 `claim_review_scope`；fresh spec critic 只深审新增范围，可在设计输入 digest 未变化时复用旧 accepted review。
6. 每轮最多 4 个 task、最多 2 个并发 investigator。首轮只要求一个 risk-backed 高风险锚点；当前轮未 drain 前，validator 拒绝创建后轮或跳到容易候选。
7. 每轮调查结束先运行 coverage critic。只有具体 scope、lens、mode、boundary、parallel plane 或 risk 缺口才能创建下一轮；coverage closed 前不运行 evidence critic 或 judge。
8. coverage closed 后才选择少量 design-grounded probe、为每个候选启动 fresh critic，并对当前闭环 frontier 做 final judge。`review`、report 和 final gate 都拒绝 stale revision，最终只发布 confirmed issue。

## 验证结果

执行命令：

```bash
python3 -m pytest -q work/tools/tests --tb=short
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

结果：

```text
180 passed in 55.92s
py_compile: passed
git diff --check: passed
```

测试新增覆盖多 slice 精确/互斥集合、共享 plane/重叠 path 连通分量不可拆、单一分量 fail-fast、安全 sweep ID、root scope、anchor 存在性、逐 ID 本地证据、完整 lens coverage、全 sweep 原子 merge、旧 risk ledger 清除、plan/architecture digest freshness、stale merge report 拒绝，以及 plan 驱动的 replay。原有 scoped claim review、round、coverage、critic、evidence、report/final gate 和完整 session 隔离回归继续通过。运行资产的项目特例扫描只命中测试中的禁止词回归清单，未命中正式 instruction、skill、role 或 helper。

## 当前状态与风险

本轮修改前的后台 OpenCode 已发送 SIGTERM 并卸载对应 LaunchAgent；日志和 session state 均保留，但 wrapper 未留下 exit marker。本文记录生成时，新版本尚未重新启动全量模型验证，因此这里只证明 deterministic 契约与测试通过，公共基准的最新端到端召回仍由下一次独立全量运行验证。

`review_context=fresh_subagent` 只是 handoff 策略声明，同一可写文件系统无法由作品自身提供平台级 Task 身份证明；当前防线是强制角色 Task、严格字段/引用/digest 契约、独立 handoff 和禁止 orchestrator 补语义字段。最终 confirmed 配额只存在于比赛 final gate，不传给 investigator、critic 或 judge，也不参与 issue 判定。
