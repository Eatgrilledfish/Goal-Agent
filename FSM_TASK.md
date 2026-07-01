请仔细阅读 FSM_DESIGN.md（证据驱动 FSM 一致性修复设计文档，690行），严格按照文档实施。

核心目标：在现有 Goal-Agent 基础上新增「测试矩阵 + Unmasking Gate」，解决修复一个黑箱失败后隐藏失败才暴露的问题。

**P0 必做（第17节）：**
1. 新增 test_outcome_collector.py — 解析 Surefire XML，输出 method 级 test_outcome_matrix.json
2. 修改 shophub_goal_runner.py 的 done_decision — DONE 必须要求矩阵全绿
3. 修改 candidate_sandbox.py — public black-box matrix 非全绿候选直接硬淘汰
4. 新增 unmasking_gate.py — 每轮补丁接受后强制执行，返回 PASS/REQUEUE/REJECT_AND_REVERT

**P1 同步做：**
5. 新增 blackbox_explorer.py — suite/class/method 级运行黑箱测试
6. 新增 matrix_diff.py — 识别 NEW/RESOLVED/REGRESSED/UNMASKED/MASKED
7. 新增 matrix_to_repair_tasks.py — ERROR/SKIPPED/NOT_RUN 转 repair task

**修改现有：**
8. stability_runner.py — 基于多轮矩阵判断稳定性，不只看 PASS/FAIL 字符串
9. shophub-test-diagnoser.md — ERROR/SKIPPED/NOT_RUN 作为一等证据
10. shophub-review-agent.md — review 时读取 matrix_diff

禁止：修改 design-docs、README API baseline、test-cases；硬编码 public fixture；吞异常统一 200；增加无必要 agent。
