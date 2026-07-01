# Goal-Agent 证据驱动 FSM 一致性修复设计文档

**面向设计实现一致性检查与修复的测试矩阵收敛方案**

版本：v1.0  
日期：2026-07-01  
适用范围：Goal-Agent / ShopHub 设计实现一致性检查与修复

## 1. 背景与问题定义

当前 Goal-Agent 已经具备较完整的“设计文档 → API 合同 → 代码扫描 → 静态一致性检查 → 候选补丁 → 沙箱验证 → 稳定性验证 → 报告”的框架。但在本地修复与黑箱测试中出现了一个典型问题：第一次测试只暴露出明显失败，修复该失败后，原先被遮蔽的隐藏失败才被执行或才表现出来，最终导致最终黑箱测试不通过。

这个问题不是单纯的补丁质量问题，而是验证模型的问题。当前流程主要关注“已经暴露出来的失败”，而没有把“未执行、被跳过、以 ERROR 形式出现、被前置失败遮蔽”的测试状态作为一等证据纳入修复闭环。

典型表现包括：
- 前置测试失败导致后续测试类或测试方法没有执行。
- 测试结果表现为 ERROR，而不是 assertion FAILURE，日志摘要没有稳定提取到具体用例。
- Maven stdout 中只有汇总行，无法可靠知道每个测试方法的真实状态。
- 修复当前失败后，测试路径继续向后推进，新的失败才暴露。
- 系统将一次 full test 的返回码或摘要结果当成收敛依据，但没有建立完整测试矩阵。

因此，本设计文档的核心目标是：在不继续横向扩张 agent 数量的前提下，将 Goal-Agent 收敛为一个“证据驱动的状态机式一致性修复流水线”，并通过测试结果矩阵和 Unmasking Gate 解决修复后隐藏失败暴露的问题。

## 2. 设计目标

本方案的目标不是重新设计一个更大的多 agent 系统，而是在现有 Goal-Agent 基础上补强关键收敛机制。

核心目标如下：
1. 建立完整测试观测矩阵，覆盖 PASS、FAILURE、ERROR、SKIPPED、TIMEOUT、NOT_RUN、FLAKY 等状态。
2. 每轮补丁接受后，强制执行 Unmasking Sweep，主动发现修复后才暴露的隐藏失败。
3. 将 ERROR、SKIPPED、NOT_RUN 从日志噪声升级为可入队的 repair task。
4. 将候选补丁验证从“打分选择”升级为“硬门槛 + 评分排序”。
5. 将 DONE 条件从 last_full_test == passed 改为 matrix 全绿 + API 合同安全 + 无新增 P0/P1 静态问题。
6. 保持单主线架构，避免 Agentless、RepairAgent、AutoCodeRover、APR 等理念被拆成多个松散模块。

设计后的 Goal-Agent 定位为：

“面向设计实现一致性修复的证据驱动 FSM。多 agent 只是执行角色，测试矩阵才是最终收敛裁判。”

## 3. 总体范式：一条主流程 + 三个 Gate

为了避免“集百家之长”导致系统过散，本方案只保留一条主流程：

发现证据 → 定位根因 → 生成候选补丁 → 沙箱验证 → 接受或回滚 → 重新展开测试矩阵 → 收敛判断

所有业内方法只作为这条主流程中的局部实现手段：
- Agentless 的价值：提供强约束的 localization → repair → validation 三段式主线。
- RepairAgent 的价值：提供有限状态机式反馈闭环。
- AutoCodeRover 的价值：提供测试方法 → endpoint → controller → service → repository → design rule 的结构化定位思路。
- APR / SWE-bench 的价值：提醒系统不能只依赖 public test pass，而要防止测试过拟合和错误补丁。
- Maven Surefire 工程实践的价值：从 XML 层面解析每个测试方法的真实状态，而不是只扫 stdout。

三类 Gate：

Gate 1：Evidence Gate
- 没有设计依据、API 依据、代码位置、测试症状，不允许进入修复队列。
- public black-box tests 只作为症状，不作为设计真相。
- 每个 repair task 必须能追溯到 design rule、API contract、code symbol 或 test symptom。

Gate 2：Patch Gate
- 补丁必须编译通过。
- API 合同不能被破坏。
- 不能修改 design-docs、README API baseline、test-cases。
- 不能硬编码 public test fixture。
- 不能吞异常、统一返回 200、删除关键过滤条件。
- 不能新增 P0/P1 静态一致性问题。

Gate 3：Unmasking Gate
- 每个补丁被接受后，必须重新展开测试矩阵。
- 不仅要看整套黑箱测试是否通过，还要检查是否有新增 ERROR、SKIPPED、NOT_RUN。
- 如果修复当前失败后暴露新失败，新失败必须重新入队，而不是宣告完成。

## 4. 目标架构

目标架构由九个阶段组成，但只有一条主链路：

Phase 0：Preflight
- 校验项目结构、Maven 可用性、git 状态。
- 输出 .agent-work/state.json 和 goal.md。

Phase 1：Evidence Build
- 从 design-docs 和 README/API baseline 中抽取 API contract、business rules、spec rules。
- 建立“设计真相”的结构化表示。

Phase 2：Code Scan & Trace
- 扫描 Spring Boot controller、DTO、service、repository、entity、exception handler。
- 建立 endpoint → controller → service → repository 的追踪链。

Phase 3：Static Consistency Check
- 检查 endpoint 缺失、DTO 字段缺失、类型不匹配、响应字段缺失、错误码缺失、异常处理缺失等。
- 输出 consistency_report.json、trace_matrix.json、repair_tasks.json。

Phase 4：Baseline Observation
- 运行 code tests、code install、public black-box tests。
- 解析 Surefire XML，生成 baseline_test_matrix.json。
- 建立每个测试类/方法的真实 outcome。

Phase 5：Localization & Queue
- 合并静态问题和测试矩阵问题。
- 将 FAILURE、ERROR、SKIPPED、NOT_RUN 转成 repair task。
- 按 P0/P1/P2、影响范围、测试遮蔽风险排序。

Phase 6：Candidate Repair
- 针对单个 repair task 生成多个候选补丁。
- 每个候选补丁在独立 sandbox 中验证。

Phase 7：Apply & Review
- 只应用通过硬门槛的候选补丁。
- review-agent 检查设计匹配、API 安全、最小变更、硬编码风险、隐藏测试风险。

Phase 8：Unmasking Sweep
- 对接受后的主工作区重新运行测试矩阵探索。
- 若发现新增 FAILURE / ERROR / SKIPPED / NOT_RUN，重新定位并入队。

Phase 9：Stability & Report
- 连续运行三轮以上，确认无 flaky、新增失败、合同破坏。
- 生成修复报告和 result/output.md。

## 5. FSM 状态机设计

Goal-Agent 的主控不应是自由发挥式 agent，而应是显式状态机。

状态定义：
- INIT：初始化工作目录与状态。
- BUILD_EVIDENCE：构建设计、API、业务规则、代码映射证据。
- BASELINE_OBSERVE：运行基线测试并生成测试矩阵。
- LOCALIZE：把静态问题和测试症状定位到代码与设计规则。
- QUEUE_READY：修复队列已生成并排序。
- GENERATE_CANDIDATES：为当前任务生成候选补丁。
- SANDBOX_VALIDATE：在候选工作区验证补丁。
- APPLY_OR_REJECT：接受最佳补丁或回滚。
- UNMASK_SWEEP：重新展开测试矩阵，发现修复后新暴露问题。
- REQUEUE：将新问题入队并重新排序。
- STABILITY_GATE：连续验证，无新增失败或不稳定问题。
- REPORT：生成交付报告。
- DONE：满足全部收敛条件。
- BLOCKED：环境、依赖、构建等不可恢复阻塞。
- STOPPED_BY_SAFETY：触发安全停止条件，例如 API 合同破坏、连续无进展、连续回归。

关键状态转移：
1. BASELINE_OBSERVE → LOCALIZE：只要测试矩阵存在 FAILURE、ERROR、SKIPPED、NOT_RUN，即进入定位。
2. SANDBOX_VALIDATE → APPLY_OR_REJECT：只有候选补丁通过硬门槛，才能进入应用。
3. APPLY_OR_REJECT → UNMASK_SWEEP：每个被接受的补丁都必须触发。
4. UNMASK_SWEEP → REQUEUE：如果出现新增失败、ERROR、未运行或异常跳过。
5. UNMASK_SWEEP → STABILITY_GATE：只有测试矩阵全绿且无异常跳过。
6. STABILITY_GATE → DONE：连续验证通过、合同安全、无开放 P0/P1。

## 6. 测试结果矩阵 Test Outcome Matrix

测试结果矩阵是本次设计的核心新增能力。它的目标是替代“只看 Maven stdout 和返回码”的粗粒度验证方式。

矩阵记录粒度：
- suite：code-unit / blackbox-public / generated-spec / stability
- class_name：测试类名
- method_name：测试方法名
- outcome：PASS / FAILURE / ERROR / SKIPPED / TIMEOUT / NOT_RUN / FLAKY_FAILURE / FLAKY_ERROR
- failure_kind：AssertionError、NullPointerException、ApplicationContext failure、SQL constraint violation 等
- message：失败摘要
- stack_top：栈顶关键帧
- related_endpoint：关联 API endpoint
- related_module：关联代码模块
- related_design_rule：关联设计规则
- run_id：本次运行编号
- source_xml：Surefire XML 路径
- source_log：Maven log 路径
- masked_by：如果该测试没有运行，记录可能的前置失败
- first_seen_round：首次出现轮次
- status_change：NEW / RESOLVED / REGRESSED / UNCHANGED

状态语义：
- PASS：测试方法明确执行且通过。
- FAILURE：断言失败，通常说明业务行为与预期不一致。
- ERROR：测试执行发生异常，通常说明启动、序列化、异常处理、数据库状态或空指针问题。
- SKIPPED：测试被跳过，必须区分预期跳过与异常跳过。
- TIMEOUT：命令或测试方法超时。
- NOT_RUN：根据测试发现清单应该存在，但本轮结果中没有出现。
- FLAKY_FAILURE / FLAKY_ERROR：多轮运行中有时通过有时失败。

矩阵输出文件：
- .agent-work/test_matrix/baseline_test_matrix.json
- .agent-work/test_matrix/current_test_matrix.json
- .agent-work/test_matrix/matrix_diff.json
- .agent-work/test_matrix/unmasking_report.md

## 7. Surefire XML 解析设计

Maven Surefire 会在 target/surefire-reports 下生成 TEST-*.xml。相比 stdout，XML 可以稳定表达每个 testcase 的 classname、name、time、failure、error、skipped 等结构化状态。

新增脚本：work/tools/scripts/test_outcome_collector.py

职责：
1. 发现 surefire reports：
   - code/**/target/surefire-reports/TEST-*.xml
   - test-cases/**/target/surefire-reports/TEST-*.xml
2. 解析 testsuite/testcase 节点。
3. 将 failure、error、skipped 映射为标准 outcome。
4. 与测试源码发现结果对齐，补充 NOT_RUN。
5. 输出 test_outcome_matrix.json。

测试源码发现规则：
- 扫描 test-cases/**/*Test.java、test-cases/**/*Tests.java。
- 提取 class name。
- 提取 @Test 标注的方法名。
- 如果 XML 中没有对应 testcase，标记 NOT_RUN。

简化伪代码：

for xml in surefire_reports:
    suite = parse_testsuite(xml)
    for testcase in suite.testcases:
        if testcase.has_failure:
            outcome = 'FAILURE'
        elif testcase.has_error:
            outcome = 'ERROR'
        elif testcase.has_skipped:
            outcome = 'SKIPPED'
        else:
            outcome = 'PASS'
        emit_record(testcase, outcome)

for discovered_test in discovered_tests:
    if discovered_test not in emitted_records:
        emit_record(discovered_test, 'NOT_RUN')

## 8. Blackbox Explorer 设计

仅运行 mvn -f test-cases/pom.xml test 不足以发现被前置失败遮蔽的测试。因此新增 blackbox_explorer.py，专门负责主动展开黑箱测试空间。

新增脚本：work/tools/scripts/blackbox_explorer.py

运行策略：
1. Suite 级运行：
   mvn -s maven-settings.xml -f test-cases/pom.xml test

2. Class 级运行：
   对发现的每个测试类单独运行：
   mvn -s maven-settings.xml -f test-cases/pom.xml -Dtest=ClassName test

3. Method 级运行：
   对失败、ERROR、SKIPPED、NOT_RUN 的测试方法单独运行：
   mvn -s maven-settings.xml -f test-cases/pom.xml -Dtest=ClassName#methodName test

4. 重点重放：
   - 新出现的 ERROR。
   - 本轮 NOT_RUN 但源码中存在的测试。
   - 上一轮被前置失败遮蔽的测试。
   - 与当前修复模块关联的测试。

5. 结果合并：
   - 优先记录 method 级结果。
   - class 级结果用于补充 suite 级未覆盖项。
   - suite 级结果用于整体状态和状态污染判断。

输出：
- .agent-work/test_matrix/blackbox_explorer_runs.jsonl
- .agent-work/test_matrix/current_test_matrix.json
- .agent-work/test_matrix/unmasked_failures.jsonl

设计原则：
- Explorer 不修改 test-cases。
- Explorer 不把 public test 当设计真相，只把它转成症状。
- Explorer 的目标是增加可观测性，不是硬编码 public test。

## 9. Unmasking Gate 设计

Unmasking Gate 是解决本次问题的关键。它在每个补丁被接受后强制执行。

触发时机：
- 任何候选补丁被应用到主工作区后。
- 任何 issue 被标记 fixed 前。
- 最终 DONE 判断前。

输入：
- baseline_test_matrix.json
- previous_test_matrix.json
- current_test_matrix.json
- consistency_report.json
- api_compare.json
- 当前 git diff

处理步骤：
1. 运行 blackbox_explorer.py，生成 current_test_matrix.json。
2. 运行 matrix_diff.py，对比 previous 与 current。
3. 识别新增问题：
   - PASS → FAILURE / ERROR / TIMEOUT
   - NOT_RUN → FAILURE / ERROR
   - SKIPPED → FAILURE / ERROR
   - 未出现过的新 FAILURE / ERROR
4. 识别已解决问题：
   - FAILURE / ERROR → PASS
5. 识别遮蔽问题：
   - suite 级没有出现，但 class/method 级单独运行失败。
   - 某测试在前一轮 NOT_RUN，本轮因前置修复后变成 FAILURE/ERROR。
6. 将新增问题转成 repair task。
7. 如果新增问题存在，不允许进入 DONE。

通过条件：
- 无 FAILURE。
- 无 ERROR。
- 无 TIMEOUT。
- 无非预期 SKIPPED。
- 无 NOT_RUN。
- 无新增 P0/P1 静态一致性问题。
- API 合同安全。

失败处理：
- 生成 unmasked repair task。
- 状态转入 REQUEUE。
- 当前补丁不一定回滚；如果它净改善并且没有新增回归，可保留并继续修复新暴露问题。
- 如果新增问题属于补丁引入的回归，则标记 REJECT_AND_REVERT。

## 10. Repair Task 模型

修复任务必须是证据驱动的，而不是日志驱动的自由文本。

建议统一 repair task schema：

{
  "task_id": "TASK-BB-ERROR-001",
  "source": "blackbox_matrix | static_consistency | generated_spec | api_contract",
  "priority": "P0 | P1 | P2",
  "issue_type": "test_failure | test_error | test_skipped | test_not_run | api_drift | business_rule_mismatch | validation_missing | error_handler_missing",
  "status": "open | in_progress | fixed | rejected | blocked",
  "evidence": {
    "design_rule_ids": ["RULE-001"],
    "api_ids": ["POST-/api/v1/users/register"],
    "test_cases": ["PubBasicFlowTest#pub001_registerActivateLogin"],
    "logs": [".agent-work/test-results/round-003-blackbox.log"],
    "xml": ["test-cases/target/surefire-reports/TEST-PubBasicFlowTest.xml"]
  },
  "localization": {
    "endpoint": "POST /api/v1/users/register",
    "controller": "code/.../UserController.java#register",
    "service": "code/.../UserService.java#register",
    "repository": "code/.../UserRepository.java",
    "confidence": 0.82
  },
  "observed_behavior": "激活登录链路在注册后返回 ERROR，栈顶为 NullPointerException",
  "expected_behavior": "注册、激活、登录流程应按设计状态机完成",
  "repair_strategy": "修复服务层状态转移与异常处理，不修改 API 与测试",
  "regression_risk": "medium"
}

入队规则：
- 静态 P0/P1 直接入队。
- FAILURE 入队，但必须关联设计/API/代码证据后才能修复。
- ERROR 直接入队，优先级通常高于普通 FAILURE。
- SKIPPED / NOT_RUN 不直接修复业务代码，先进入 diagnosis task；如果单独运行后失败，再转 repair task。

## 11. Candidate Sandbox 验证规则

当前候选补丁验证需要从“评分排序”升级为“硬淘汰 + 评分排序”。

硬淘汰条件：
- patch 无法 apply。
- compile 失败。
- code install 失败。
- public black-box matrix 中存在 FAILURE / ERROR / TIMEOUT。
- 出现非预期 SKIPPED / NOT_RUN。
- generated spec tests 中存在 spec-backed failure。
- contract checker 新增 P0。
- forbidden guard 失败。
- 修改 design-docs、README API baseline、test-cases。
- 硬编码 public fixture。
- catch Exception 后返回成功。
- 统一返回 200。
- 删除 repository 关键过滤条件，例如 deleted flag、status、userId。

评分只在通过硬门槛的候选之间使用：
score = 35% * public_matrix_pass
      + 20% * generated_spec_pass
      + 20% * design_match
      + 10% * contract_safety
      + 10% * diff_minimality
      + 5%  * stability

其中 public_matrix_pass 必须为 1，否则直接淘汰，不进入评分。

候选输出：
- candidate_validation.jsonl
- candidate_validation.md
- selected_patch.json

selected_patch 必须记录：
- 选择原因。
- 被淘汰候选原因。
- 设计依据。
- 测试矩阵变化。
- 风险。

## 12. DONE 条件重定义

原先 DONE 容易依赖 last_full_test == passed。新设计中，DONE 必须满足全部条件：

1. code tests 通过：
   mvn -s maven-settings.xml -f code/pom.xml test

2. code install 通过：
   mvn -s maven-settings.xml -f code/pom.xml install -DskipTests

3. public black-box suite 通过：
   mvn -s maven-settings.xml -f test-cases/pom.xml test

4. test outcome matrix 全绿：
   - 无 FAILURE。
   - 无 ERROR。
   - 无 TIMEOUT。
   - 无非预期 SKIPPED。
   - 无 NOT_RUN。

5. Unmasking Gate 通过：
   - 修复后没有新暴露失败。
   - 新问题已入队或已修复。

6. Stability Gate 通过：
   - 连续多轮运行无 intermittent failure。
   - 无新增 flaky failure / flaky error。

7. Contract Gate 通过：
   - API baseline 不变。
   - contract checker 无 P0。

8. Forbidden Guard 通过：
   - 不修改禁止路径。
   - 不硬编码测试。
   - 不吞异常。
   - 不破坏事务、校验、过滤条件。

9. Issue Queue 收敛：
   - 无开放 P0/P1。
   - P2 可留存，但必须在报告中说明风险。

10. 报告完成：
   - 修复报告.md 存在。
   - result/output.md 存在。
   - 每轮修复记录、测试矩阵、候选验证结果可追溯。

## 13. 需要新增或修改的脚本

新增脚本：

1. test_outcome_collector.py
职责：解析 Surefire XML，合并源码发现清单，输出 test_outcome_matrix。

2. blackbox_explorer.py
职责：suite/class/method 级运行黑箱测试，主动发现被遮蔽的失败。

3. matrix_diff.py
职责：对比 baseline、previous、current 测试矩阵，识别 NEW、RESOLVED、REGRESSED、MASKED、UNMASKED。

4. matrix_to_repair_tasks.py
职责：将 FAILURE、ERROR、异常 SKIPPED、NOT_RUN 转成 repair/diagnosis task。

5. unmasking_gate.py
职责：封装补丁接受后的完整 unmasking sweep，返回 PASS / REQUEUE / REJECT_AND_REVERT。

修改脚本：

1. shophub_goal_runner.py
需要修改：
- run_baseline_tests：运行后调用 test_outcome_collector。
- run_verification_tests：不仅保存 log，还保存 current_test_matrix。
- summarize_test_logs：保留 stdout 摘要，但不再作为唯一测试证据。
- done_decision：以 matrix 全绿作为 DONE 必要条件。
- run_until_done：每轮 patch 后调用 unmasking_gate。

2. candidate_sandbox.py
需要修改：
- public_tests != PASS 不再只是降低 pass rate，而是硬淘汰。
- sandbox 中调用 test_outcome_collector 和 blackbox_explorer。
- contract checker 新增 P0 直接淘汰。
- generated spec failure 如果有设计依据，直接淘汰。

3. stability_runner.py
需要修改：
- 每轮输出 test matrix。
- 对比多轮 matrix，识别 intermittent failures。
- 不只看 gate_results 中 PASS/FAIL 字符串。

4. shophub-test-diagnoser.md
需要补充：
- 必须解析 Surefire XML。
- ERROR/SKIPPED/NOT_RUN 必须输出为症状。
- 如果方法级运行不可用，至少 class 级独立运行。

5. shophub-review-agent.md
需要补充：
- review 时必须读取 matrix_diff。
- 如果补丁导致新增 ERROR/NOT_RUN，不得 PASS。

## 14. 主循环伪代码

建议主循环如下：

initialize_workspace()
build_contracts()
scan_code()
run_static_consistency_check()

baseline_matrix = run_baseline_observation()
issue_queue = build_issue_queue(consistency_report, baseline_matrix)

while not done:
    task = issue_queue.next()
    if task is None:
        gate = run_unmasking_gate()
        if gate.passed and stability_gate_passed() and contract_safe():
            done = True
            break
        issue_queue.add_all(gate.new_tasks)
        continue

    candidates = generate_candidate_patches(task)
    validation_results = []

    for candidate in candidates:
        result = validate_in_sandbox(candidate)
        validation_results.append(result)

    best = select_best_eligible_candidate(validation_results)
    if best is None:
        mark_task_blocked(task)
        continue

    apply_patch(best)
    review = review_patch(task, best)
    if review.verdict != 'PASS':
        revert_patch(best)
        mark_candidate_rejected(best)
        continue

    unmask = run_unmasking_gate()
    if unmask.verdict == 'REJECT_AND_REVERT':
        revert_patch(best)
        mark_task_regressed(task)
        continue

    mark_task_fixed(task)
    issue_queue.add_all(unmask.new_tasks)
    issue_queue.reprioritize()

write_report()

该循环的关键点：
- 每轮修复后必须跑 unmasking gate。
- 新暴露失败不是异常情况，而是正常反馈。
- DONE 只由矩阵、合同、稳定性和队列共同决定。

## 15. CLI 使用方式设计

建议提供以下命令：

1. 初始化：
python3 work/tools/scripts/shophub_goal_runner.py --root . init

2. 构建证据：
python3 work/tools/scripts/api_contract_builder.py --root .
python3 work/tools/scripts/business_rule_builder.py --root .
python3 work/tools/scripts/spring_scanner.py --root .
python3 work/tools/scripts/contract_checker.py --root . --save-baseline

3. 基线测试矩阵：
python3 work/tools/scripts/blackbox_explorer.py --root . --mode baseline
python3 work/tools/scripts/test_outcome_collector.py --root . --suite blackbox --output .agent-work/test_matrix/baseline_test_matrix.json

4. 修复后展开隐藏失败：
python3 work/tools/scripts/unmasking_gate.py --root . --previous .agent-work/test_matrix/previous_test_matrix.json --current-output .agent-work/test_matrix/current_test_matrix.json

5. 候选补丁验证：
python3 work/tools/scripts/candidate_sandbox.py --root . --task-id TASK-001

6. 稳定性验证：
python3 work/tools/scripts/stability_runner.py --root . --runs 3 --mode full-gate

7. 报告生成：
python3 work/tools/scripts/shophub_goal_runner.py --root . report

## 16. 对当前问题的处理闭环示例

以 pub001_registerActivateLogin 为例：

Round 1：
- suite 级黑箱测试失败。
- XML 矩阵显示 pub001_registerActivateLogin = FAILURE。
- 后续 pub002、pub003 在源码发现中存在，但 XML 中没有结果，标记 NOT_RUN。
- pub001 入 repair queue，pub002/pub003 入 diagnosis queue。

Round 2：
- 修复 pub001。
- 补丁通过 sandbox。
- 应用补丁后触发 Unmasking Gate。
- Explorer 单独运行 PubBasicFlowTest 和相关方法。
- pub001 = PASS。
- pub002 由 NOT_RUN 变成 ERROR。
- matrix_diff 标记 pub002 为 UNMASKED_NEW_ERROR。
- pub002 转成新 repair task，不允许 DONE。

Round 3：
- 定位 pub002 ERROR 的根因，例如激活状态、登录 token、异常处理或数据库状态污染。
- 修复后再次触发 Unmasking Gate。
- 所有 public 方法 PASS，无 NOT_RUN。
- 进入 Stability Gate。

这个流程把“隐藏失败暴露”从意外变成正常收敛过程。

## 17. 实施优先级

优先级 P0：必须先做
- test_outcome_collector.py：解析 Surefire XML，生成矩阵。
- done_decision 改造：DONE 必须依赖矩阵全绿。
- candidate_sandbox.py 改造：public_tests != PASS 硬淘汰。
- Unmasking Gate：每轮补丁接受后执行。

优先级 P1：建议同步做
- blackbox_explorer.py：class/method 级独立运行。
- matrix_diff.py：识别 NEW / RESOLVED / REGRESSED / UNMASKED。
- matrix_to_repair_tasks.py：将 ERROR/SKIPPED/NOT_RUN 入队。

优先级 P2：增强能力
- generated spec tests 与矩阵合并。
- 多轮 flaky 识别。
- 更强的 endpoint/controller/service/repository 定位。
- review-agent 引入 matrix_diff 审核。

推荐先完成 P0 + P1，避免继续增加 agent 数量。

## 18. 验收标准

功能验收：
- 能解析 Surefire XML 并输出 method 级测试矩阵。
- 能识别 FAILURE、ERROR、SKIPPED、NOT_RUN。
- 能在修复一个失败后发现后续新暴露失败。
- 能把新暴露失败自动转入 repair queue。
- public black-box 失败的候选补丁不能被选中。
- DONE 条件不再只依赖 Maven 返回码。

质量验收：
- 不修改 design-docs、README API baseline、test-cases。
- 不硬编码 public test fixture。
- API 合同保持兼容。
- 每轮修复有证据链和测试矩阵记录。
- 修复报告能解释：发现了什么、修了什么、为什么没破坏合同、是否存在剩余风险。

回归验收：
- 构造一个“前置失败遮蔽后续失败”的测试场景。
- 第一轮只暴露前置失败。
- 修复前置失败后，Unmasking Gate 必须发现后续失败。
- 系统不能提前 DONE。

## 19. 风险与边界

风险 1：方法级 Maven 运行不稳定
处理：如果 -Dtest=Class#method 不可用，则降级为 class 级运行；矩阵仍记录 method 级 NOT_RUN。

风险 2：public tests 有顺序依赖或共享状态
处理：Explorer 同时保留 suite 级结果和 class/method 级结果。如果单独运行通过但 suite 失败，标记 state_pollution 或 ordering_dependency。

风险 3：生成测试误报
处理：generated spec tests 必须有 design/API 依据。无明确依据的 generated failure 只能作为低置信诊断信号。

风险 4：候选补丁全被淘汰
处理：标记 task blocked，回到 localization 阶段补充证据，而不是放宽硬门槛。

风险 5：持续暴露新失败导致轮次过多
处理：设置 max_rounds，但报告中必须说明剩余 matrix 状态和开放问题，不能伪装 DONE。

## 20. 给 Codex 的开发摘要

开发目标：
在 Goal-Agent 中新增“测试矩阵 + Unmasking Gate”，解决修复一个黑箱失败后隐藏失败才暴露的问题。

必须完成：
1. 新增 test_outcome_collector.py，解析 Surefire XML，输出 method 级 test_outcome_matrix.json。
2. 新增 blackbox_explorer.py，支持 suite/class/method 级运行黑箱测试。
3. 新增 matrix_diff.py，识别 NEW、RESOLVED、REGRESSED、UNMASKED、MASKED。
4. 新增 unmasking_gate.py，修复后强制运行并决定 PASS / REQUEUE / REJECT_AND_REVERT。
5. 修改 shophub_goal_runner.py 的 done_decision，DONE 必须要求矩阵全绿。
6. 修改 candidate_sandbox.py，public black-box matrix 非全绿时候选补丁直接淘汰。
7. 修改 stability_runner.py，使其基于多轮矩阵判断稳定性，而不是只看 PASS/FAIL 字符串。
8. 更新 shophub-test-diagnoser.md 和 shophub-review-agent.md，让 ERROR、SKIPPED、NOT_RUN 成为一等证据。

禁止：
- 不要新增更多无必要 agent。
- 不要修改 test-cases。
- 不要把 public test 当设计真相。
- 不要只靠 Maven stdout 正则判断测试结果。
- 不要在 public tests 失败时仍允许候选补丁 eligible。

最终标准：
系统只有在 code tests、install、public tests、test matrix、unmasking gate、stability gate、contract checker、forbidden guard 全部通过时，才能进入 DONE。
