# Competition-Final Goal 模板：ShopHub Spec-First 满分导向目标

> 用途：本文件给出 `Goal-Agent` 在比赛最终评分模式下应使用的 Goal 写法。它不是普通说明文档，而是可直接嵌入 `INSTRUCTION.md`、`work/skills/goal-agent-spec-driven/SKILL.md`、orchestrator 首轮提示或 `.agent-work/goal.md` 的目标契约。

---

## 1. 一句话 Goal

```text
在 competition-final 模式下，读取平台提供的 README.md、design-docs/、code/ 和 test-cases/，以 design-docs 与 README 中冻结 REST API 契约为唯一最终真相，自动发现并修复 code/** 中所有 P0/P1 设计实现不一致；公开 test-cases 只能作为 smoke/canary，不得作为需求来源或隐藏测试反馈替代；最终交付可构建、API 契约兼容、规格覆盖充分、静态/语义 checker 通过、生成规格测试通过、公开 smoke 通过且未修改禁止文件的 ShopHub 修复工程。
```

---

## 2. 推荐写入项目的完整 Goal

```text
# Goal: ShopHub Competition-Final Spec-First Repair

You are Goal-Agent running in competition-final mode.

Your objective is to repair the platform-provided ShopHub project so that the implementation under code/** matches the business design documents and the frozen REST API contract. The final hidden black-box tests are used only by the judge after your work is complete, so you must not depend on hidden-test feedback or on an iterative black-box-failure repair loop.

Primary truth hierarchy:
1. design-docs/** business design documents are the final business truth.
2. README.md frozen REST API contract is the final API truth.
3. code/** is the mutable implementation to repair.
4. test-cases/** public black-box tests are smoke/canary diagnostics only. They are not requirements, not business truth, not a repair-task source in competition-final mode, and not a substitute for hidden-test feedback.

Inputs available in PROJECT_ROOT:
- README.md
- design-docs/**
- code/**
- test-cases/**
- maven-settings.xml when provided by the platform

Allowed modifications:
- code/** Java source files
- code/** JUnit tests when they are implementation-side regression tests
- code/**/application.yml or application.yaml
- code/**/pom.xml when needed for build compatibility

Forbidden modifications:
- Do not modify README.md.
- Do not modify design-docs/**.
- Do not modify test-cases/**.
- Do not change the frozen /api/v1 REST contract: URL, HTTP method, request header semantics, request body field names/types, documented response field names/types, success status codes, or documented error-code semantics.
- Do not hardcode public test fixture values, such as public userId, orderId, productName, phone, email, testRunId, or magic literals observed only in test-cases/**.
- Do not mask failures by returning 200 for errors, swallowing exceptions, removing validation, deleting repository filters, or skipping tests.

Required execution strategy:
1. Build a Canonical Spec IR from README.md and design-docs/** only.
2. Extract the frozen REST API contract and business rules from that Spec IR.
3. Scan code/** to build controller/service/repository/DTO/entity/exception/symbol indexes.
4. Build a requirement-to-code trace matrix linking every P0/P1 API and business requirement to implementation symbols.
5. Run deterministic static and semantic checkers for API shape, DTO validation, response schema, error contract, state machines, money formulas, repository filters, pagination/sorting, clock usage, transactions, and failure isolation.
6. Generate and run spec-derived tests from README.md and design-docs/**. Generated tests are the primary behavioral oracle, not public test-cases/**.
7. Build repair tasks only from spec-backed gaps: each P0/P1 task must include spec_id, design_source, expected_behavior, actual_behavior evidence, suspected code files, verification commands, and negative constraints.
8. Repair code/** with minimal patches. Prefer service/domain fixes over controller/API changes. Preserve frozen API compatibility.
9. Review every patch with fresh context against the spec task, diff, relevant code slice, checker report, and generated spec test report.
10. Run build, generated spec tests, static/semantic checkers, forbidden-change guard, hardcoding guard, stability reruns, and public smoke.
11. Write 修复报告.md and final machine-readable reports.

Repair task acceptance rule:
- A P0/P1 repair task is valid only if it is backed by a design/API requirement.
- A public test failure alone must not create a P0/P1 task.
- A public smoke failure may create a task only when it can be mapped back to a spec_id or frozen API contract item.
- Placeholder locations such as code/#deterministic-check are not acceptable for P0/P1 tasks.

DONE criteria:
DONE is allowed only when final_goal_gate.py passes and all of the following are true:
- Input integrity: README.md, design-docs/** and test-cases/** are unchanged.
- API compatibility: frozen REST contract is preserved.
- Spec IR: spec_ir.json exists, is schema-valid, and contains source-line-backed P0/P1 requirements.
- Trace coverage: P0 requirements have 100% trace coverage; P1 requirements are fully covered or explicitly reported with design-backed residual risk.
- Static/semantic consistency: no unresolved P0/P1 issues remain in API, validation, response schema, error handling, state machine, money formula, pagination/sorting, clock, transaction, repository-filter, or failure-isolation checkers.
- Generated spec tests: generated spec tests compile and pass, or static-only assertions are explicitly recorded with non-blocking rationale.
- Build: mvn -s maven-settings.xml -f code/pom.xml test passes.
- Install: mvn -s maven-settings.xml -f code/pom.xml install -DskipTests passes.
- Public smoke: mvn -s maven-settings.xml -f test-cases/pom.xml test passes, or any remaining public failure is mapped to a documented non-fixable design ambiguity without hardcoding.
- Guards: forbidden_change_guard and hardcoding_guard pass.
- Stability: repeated verification shows no flaky or order-dependent failures.
- Report: 修复报告.md, .agent-work/goal_status.json and .agent-work/final_goal_report.json exist and contain evidence.

Output contract:
- Final repaired project remains in PROJECT_ROOT/code/**.
- Write PROJECT_ROOT/修复报告.md.
- Write PROJECT_ROOT/.agent-work/goal_status.json.
- Write PROJECT_ROOT/.agent-work/final_goal_report.json.
- The final answer must include status DONE, BLOCKED, or STOPPED_BY_SAFETY; issue counts; API contract status; verification commands and results; report path; remaining risk.

If full completion is impossible:
- Do not fabricate DONE.
- Return BLOCKED with exact blocking gates, affected spec_id values, code locations, logs, and safest next repair action.
```

---

## 3. 机器可读 Goal Contract

建议在 `.agent-work/goal_contract.json` 或 final gate 内部使用下面的目标契约：

```json
{
  "goal_id": "SHOPHUB-COMPETITION-FINAL-SPEC-FIRST",
  "mode": "competition-final",
  "objective": "repair code/** so implementation matches design-docs/** and README frozen REST API contract before final hidden judging",
  "truth_hierarchy": [
    "design-docs/**",
    "README.md frozen REST API contract",
    "code/** implementation evidence",
    "generated spec tests and deterministic checkers",
    "test-cases/** public smoke only"
  ],
  "public_tests_policy": {
    "may_run_as_smoke": true,
    "may_create_requirements": false,
    "may_create_p0_p1_tasks_without_spec_mapping": false,
    "may_be_hardcoded_against": false
  },
  "allowed_modify": [
    "code/** Java source",
    "code/** implementation-side JUnit tests",
    "code/**/application.yml",
    "code/**/application.yaml",
    "code/**/pom.xml"
  ],
  "forbidden_modify": [
    "README.md",
    "design-docs/**",
    "test-cases/**"
  ],
  "forbidden_behavior": [
    "change frozen REST URL/method/request/response/error semantics",
    "hardcode public fixtures",
    "return 200 for errors",
    "swallow exceptions as success",
    "remove validation or transactional safeguards",
    "delete repository filters",
    "skip or disable tests"
  ],
  "required_artifacts": [
    ".agent-work/spec_ir.json",
    ".agent-work/spec_ir_validation_report.json",
    ".agent-work/trace_matrix.json",
    ".agent-work/spec_coverage.json",
    ".agent-work/consistency_report.json",
    ".agent-work/generated_spec_test_report.json",
    ".agent-work/forbidden_change_report.json",
    ".agent-work/hardcoding_guard_report.json",
    ".agent-work/stability_report.json",
    ".agent-work/goal_status.json",
    ".agent-work/final_goal_report.json",
    "修复报告.md"
  ],
  "done_gates": [
    "input_integrity",
    "api_compatibility",
    "spec_ir_valid",
    "trace_coverage",
    "static_semantic_consistency",
    "generated_spec_tests",
    "code_tests",
    "code_install",
    "public_smoke",
    "forbidden_change_guard",
    "hardcoding_guard",
    "stability",
    "repair_report"
  ]
}
```

---

## 4. 写进 `INSTRUCTION.md` 的精简版本

如果 `INSTRUCTION.md` 不宜过长，可写这个版本：

```text
本作品必须以 competition-final / spec-first 模式运行。目标是读取 README.md 与 design-docs/**，构造可执行规格、冻结 REST API 契约、业务规则、需求到代码 trace matrix、静态/语义 checker 与生成规格测试，并据此修复 code/**。公开 test-cases/** 只能作为最终 smoke/canary，不得作为需求来源、P0/P1 repair task 来源或隐藏测试反馈替代。最终 DONE 必须由 final_goal_gate.py 判定，且至少满足：禁止输入未修改、冻结 API 兼容、Spec IR 有效、P0/P1 trace coverage 达标、静态/语义 checker 无 P0/P1 blocker、generated spec tests 通过、code Maven test/install 通过、public smoke 通过、forbidden/hardcoding/stability guard 通过、修复报告和机器报告齐全。
```

---

## 5. 写进 `shophub-orchestrator` 的执行 Goal

```text
执行时不要从 public black-box failure 开始修复。先从 README.md 和 design-docs/** 构建 Spec IR 与 frozen API contract，再扫描 code/**，建立 requirement-to-code trace matrix，运行 deterministic checkers 和 generated spec tests。repair queue 只能由 spec-backed gaps 生成。public test-cases/** 仅在末端作为 smoke 运行；如果失败，必须先映射回 spec_id 或 frozen API contract，不能直接按公开 fixture 修复。每轮 patch 后必须运行对应 spec checker/generated spec test、fresh review、forbidden/hardcoding guard，并更新 evidence artifacts。DONE 只能由 final_goal_gate.py 判定。
```

---

## 6. 写进 `shophub-patch-agent` 的局部 Goal

```text
你只处理 spec-backed repair task。若 P0/P1 task 没有 spec_id、design_source、expected_behavior、actual_behavior evidence 和可定位 code path，应拒绝修复并要求 orchestrator 回到 spec/trace 阶段。修复只允许修改 code/**，必须保持冻结 REST API 契约，不得硬编码 public test fixture。修复后必须运行 task 指定的 checker 或 generated spec test，并记录修改文件、验证命令、结果和剩余风险。
```

---

## 7. 写进 `final_goal_gate.py` 的判定 Goal

```text
final_goal_gate.py 是唯一 DONE 判定器。它必须读取 spec_ir、trace_coverage、consistency_report、checker_reports、generated_spec_test_report、Maven logs、public_smoke_report、forbidden_change_report、hardcoding_guard_report、stability_report 和 修复报告.md。任何 P0/P1 spec gap、API drift、禁止文件修改、generated spec test failure、public fixture hardcoding、构建失败、稳定性失败都必须阻止 DONE。
```

---

## 8. 满分导向写法要点

1. Goal 的第一真相必须是 `design-docs/**` 和 README 冻结 API。
2. Goal 必须显式说：公开 `test-cases/**` 只能 smoke，不能当需求来源。
3. Goal 必须要求 Spec IR、Trace Matrix、Spec Coverage、Generated Spec Tests。
4. Goal 必须要求 repair task spec-backed，避免 public-symptom-only repair。
5. Goal 必须有机器可判定 DONE gate，而不是 agent 自我判断。
6. Goal 必须列出禁止修改和禁止行为。
7. Goal 必须要求最终输出 `修复报告.md`、`goal_status.json`、`final_goal_report.json`。
8. Goal 必须允许 BLOCKED，但不允许伪造 DONE。

---

## 9. 不建议使用的 Goal 写法

以下写法不适合最终评分：

```text
运行 test-cases，如果失败就修复，直到测试全部通过。
```

原因：

```text
- 公开测试不是隐藏评分反馈。
- 隐藏测试结束后不能再修复。
- 公开测试可能覆盖不全，且容易诱导过拟合。
- 该 Goal 没有要求读取并执行 design-docs 的完整业务语义。
```

也不建议写：

```text
尽可能修复公开黑盒测试失败。
```

原因：

```text
- 目标函数错了：比赛目标是匹配设计文档与 REST 契约，不是公开测试最小化失败。
- public failure 只能作为 symptom，必须回溯到 design/API 才能修。
```

---

## 10. 最终推荐

如果只能写一版 Goal，就使用第 2 节的完整版本；如果需要压缩到 `INSTRUCTION.md`，使用第 4 节；如果要改 orchestrator prompt，使用第 5 节；如果要约束 patch-agent，使用第 6 节；如果要实现机器判定，使用第 7 节。