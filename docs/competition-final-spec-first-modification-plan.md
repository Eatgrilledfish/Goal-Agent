# Goal-Agent 比赛最终评分优化修改文档：Spec-First / Competition-Final 方案

> 目标：把当前 ShopHub 设计实现一致性修复系统从“公开黑盒反馈驱动修复”改造为“设计文档 / REST 契约驱动修复”。公开 `test-cases/` 仅作为提交前 smoke/canary，不再作为需求来源、修复来源或隐藏评分反馈替代品。

更新时间：2026-07-06  
适用仓库：`Eatgrilledfish/Goal-Agent`  
适用赛题：`02_04_design_implementation_consistency` / ShopHub 设计实现一致性检查与修复

---

## 1. 背景与核心问题

比赛规则要求参赛作品读取平台提供的：

```text
/app/code/judge-assets/02_04_design_implementation_consistency/
├── code/          # ShopHub 原始业务代码，参赛作品需要修复的对象
├── design-docs/   # 业务设计文档，最终验收基准
├── test-cases/    # 公开黑盒 JUnit 测试项目
└── README.md      # 比赛说明、REST API 契约、黑盒用例说明
```

最终评分会在参赛作品完成后运行隐藏黑盒测试；隐藏黑盒测试完成后不允许再修改代码。因此，系统不能依赖“黑盒失败结果 → 再修复”的反馈回路。正确策略必须是：

```text
README + design-docs
  → 可执行规格 / 需求 IR
  → 需求到代码的 trace matrix
  → 静态一致性 checker + 规格生成测试
  → 修复 code/**
  → final gate
  → 隐藏黑盒只做最终评分
```

当前项目中已经有 spec-driven pipeline 的框架，但公开测试结果仍进入了主修复链路，主要风险如下：

1. `public_case_rule_builder.py` 会读取 `test-cases/**/*.java`，把公开测试方法名、断言、关键字转为 public-case rules。
2. `blackbox_explorer.py` 会以 suite/class/method 粒度探索公开黑盒，并输出失败矩阵和 unmasked failures。
3. `feature_registry.py` 会把 public matrix 与 API/business rules 混合为 feature pass/fail 依据。
4. `rule_issue_builder.py` 会把 public matrix failure 转成 issue。
5. `repair_task_builder.py` 会把 `test_symptoms.jsonl` 和 public-case rules 转成 repair task。
6. `final_goal_gate.py` 当前把 public matrix all-green 作为核心 gate，但缺少等价强度的 `spec_ir`、`spec_coverage`、`generated_spec_tests` gate。

这会使系统在公开测试上看起来越来越强，但在隐藏测试上泛化不足。

---

## 2. 参考实现原则

### 2.1 OpenAI Codex / Agents 相关原则

OpenAI Codex 的关键工程原则是：任务在隔离 sandbox 中运行；agent 可以读写代码、运行 test harness、linters、type checkers，并通过 terminal logs 与 test outputs 提供可验证证据；`AGENTS.md` 用于提供项目导航、测试命令和工程规范。

映射到本项目：

- `INSTRUCTION.md` 与 `work/skills/*.md` 应承担类似 `AGENTS.md` 的角色。
- 测试信号必须来自可复现、可解释的规格 oracle，而不是隐藏黑盒反馈。
- 每次修复必须产出可验证证据：checker 报告、生成测试结果、Maven 构建日志、guard 报告。

OpenAI agents guide 还强调：先建立 eval baseline，再优化模型/成本；工具应有标准定义、文档化、可测试、可复用；guardrails 应采用多层防御，而不依赖单个检查。

映射到本项目：

- `spec_ir_gate`、`trace_coverage_gate`、`static_consistency_gate`、`generated_spec_tests_gate`、`forbidden_change_guard`、`hardcoding_guard` 都要进入 final gate。
- Helper scripts 应有固定 schema、稳定输出路径、可作为 deterministic tools 复用。

参考链接：

- OpenAI Codex: https://openai.com/index/introducing-codex/
- OpenAI, A practical guide to building agents: https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf

### 2.2 Anthropic Claude Code / Multi-Agent 相关原则

Anthropic Claude Code best practices 强调：必须给 agent 一个能运行的验证检查，例如 tests、build、linter、diff script 或 screenshot；没有可运行检查时，agent 只能依赖“看起来完成”。同时，Stop hook 可作为确定性 gate，verification subagent 可作为 fresh-context 二次审查。

映射到本项目：

- 不能让 patch-agent 自己宣称修复成功，必须由 checker/generator/final gate 判定。
- `shophub-review-agent` 应只拿 diff、spec task、relevant code slice、verifier report，不继承 patch-agent 的完整上下文。
- `final_goal_gate.py` 应成为“不能绕过”的完成判定脚本。

Anthropic 多 agent 系统经验强调：多 agent 适合宽度优先、可并行、信息超过单上下文的问题；但编码任务通常没有研究任务那么天然并行，且多 agent token 成本高。它还建议让 subagent 输出落到文件系统，避免主 agent 转述造成信息损失。

映射到本项目：

- 保留 module fan-out audit，但每个 subagent 必须输出结构化 artifact。
- 主 orchestrator 只合并 artifact，不让自然语言 summary 直接驱动修复。
- 多 agent 不应替代 deterministic checker；它只补足语义判断和代码定位。

参考链接：

- Claude Code best practices: https://code.claude.com/docs/en/best-practices
- Anthropic multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system

---

## 3. 总体改造目标

### 3.1 从 feedback-driven 改为 spec-first

现状：

```text
公开黑盒失败
  → test symptom / public matrix
  → issue / feature / repair task
  → patch-agent 修复
  → 重新跑公开黑盒
```

目标：

```text
README + design-docs
  → Canonical Spec IR
  → API / Rule / Invariant / StateMachine / ErrorContract / Formula
  → Trace Matrix
  → Static Checkers + Generated Spec Tests
  → Spec-backed Repair Tasks
  → Minimal Patch
  → Spec Gates + Build Gates + Public Smoke
```

### 3.2 公开测试降级为 smoke/canary

`test-cases/` 仍可运行，但角色改为：

```text
允许：
- 本地 smoke
- 公开可观测 API 兼容性 canary
- 修复后 regression signal

禁止：
- 默认转成 business rule
- 默认转成 P0/P1 feature
- 默认转成 repair task
- 作为 hidden-test feedback 替代
```

### 3.3 可执行规格成为主 oracle

新增主 oracle：

```text
.agent-work/spec_ir.json
.agent-work/spec_ir.schema.json
.agent-work/spec_coverage.json
.agent-work/generated_spec_test_report.json
.agent-work/static_semantic_report.json
```

DONE 必须由这些 artifact 证明，而不是只由 public matrix 证明。

---

## 4. 运行模式设计

新增 pipeline mode：

```text
GOAL_AGENT_MODE=competition-final   # 默认模式，面向最终隐藏评分
GOAL_AGENT_MODE=local-public-debug  # 本地调试模式，允许公开测试进入诊断
```

建议新增配置文件：

```text
work/tools/config/pipeline_modes.json
```

内容：

```json
{
  "default_mode": "competition-final",
  "modes": {
    "competition-final": {
      "public_tests_as_requirement_source": false,
      "public_tests_as_repair_task_source": false,
      "public_tests_as_feature_source": false,
      "run_public_smoke": true,
      "require_spec_ir_gate": true,
      "require_spec_coverage_gate": true,
      "require_generated_spec_tests_gate": true
    },
    "local-public-debug": {
      "public_tests_as_requirement_source": true,
      "public_tests_as_repair_task_source": true,
      "public_tests_as_feature_source": true,
      "run_public_smoke": true,
      "require_spec_ir_gate": true,
      "require_spec_coverage_gate": true,
      "require_generated_spec_tests_gate": true
    }
  }
}
```

Helper 函数建议放在：

```text
work/tools/scripts/pipeline_mode.py
```

提供：

```python
def current_mode(root: Path) -> str: ...
def public_as_requirements_enabled(root: Path) -> bool: ...
def public_as_tasks_enabled(root: Path) -> bool: ...
def public_as_features_enabled(root: Path) -> bool: ...
def spec_gates_required(root: Path) -> bool: ...
```

---

## 5. 新增核心 artifact

### 5.1 Canonical Spec IR

新增：

```text
work/tools/scripts/spec_ir_builder.py
work/tools/scripts/spec_ir_validator.py
work/tools/scripts/spec_ir_schema.py
```

输出：

```text
.agent-work/spec_ir.json
.agent-work/spec_ir.schema.json
.agent-work/spec_ir_index.md
.agent-work/spec_ir_validation_report.json
```

建议 schema：

```json
{
  "generated_at": "ISO-8601",
  "sources": ["README.md", "design-docs/order.md"],
  "requirements": [
    {
      "id": "REQ-ORDER-STATE-001",
      "source_file": "design-docs/order.md",
      "source_line_start": 42,
      "source_line_end": 58,
      "category": "state_machine",
      "priority": "P0",
      "domain": "order",
      "related_api": ["POST /api/v1/orders/{id}/pay"],
      "description": "取消订单不可支付",
      "preconditions": ["order.status == CANCELLED"],
      "postconditions": [],
      "invariants": ["payment must not be created", "order.status remains CANCELLED"],
      "expected_error": {
        "http_status": 409,
        "error_code": "ORDER_STATUS_CONFLICT"
      },
      "acceptance_assertions": [
        "pay cancelled order returns 409",
        "order status is unchanged after failed payment"
      ],
      "evidence_text": "...",
      "confidence": 0.92
    }
  ],
  "summary": {
    "total": 0,
    "p0": 0,
    "p1": 0,
    "by_category": {}
  }
}
```

### 5.2 Requirement-to-Code Trace Matrix v2

新增/升级：

```text
work/tools/scripts/trace_matrix_builder.py
```

输出：

```text
.agent-work/trace_matrix.json
.agent-work/trace_coverage.json
.agent-work/trace_matrix.md
```

Trace item 建议：

```json
{
  "requirement_id": "REQ-ORDER-STATE-001",
  "category": "state_machine",
  "priority": "P0",
  "source": "design-docs/order.md:42-58",
  "links": {
    "controller": {"file": "code/.../OrderController.java", "symbol": "pay", "confidence": 0.93},
    "service": {"file": "code/.../OrderService.java", "symbol": "pay", "confidence": 0.91},
    "repository": {"file": "code/.../OrderRepository.java", "symbol": "save", "confidence": 0.71},
    "dto": [],
    "exception_handler": {"file": "code/.../GlobalExceptionHandler.java", "symbol": "handleConflict", "confidence": 0.8}
  },
  "implementation_status": "implemented|partial|missing|conflict|unknown",
  "gaps": [],
  "checker_ids": ["state_machine_checker"],
  "generated_tests": ["OrderStateContractTest#cancelledOrderCannotPay"]
}
```

Coverage gate：

```json
{
  "p0_total": 20,
  "p0_linked": 20,
  "p0_executable": 19,
  "p1_total": 35,
  "p1_linked": 34,
  "unlinked": [],
  "blocking": []
}
```

---

## 6. 新增 / 升级 checker

### 6.1 API shape checker

新增：

```text
work/tools/scripts/checkers/api_shape_checker.py
```

检查：

- HTTP method / path / path variable / query param
- request body required fields / field type
- response wrapper / documented response fields
- success status code
- documented error status / error code
- `/api/v1/` prefix 不变

输出：

```text
.agent-work/checker_reports/api_shape_checker.json
```

### 6.2 State machine checker

当前已有 `state_machine_checker.py`，需要升级为读取 `spec_ir.json` 中的 `category=state_machine` 规则。

检查：

- 设计状态集合 vs 代码 enum
- 允许转换 / 禁止转换
- 非法转换是否返回设计错误码和状态码
- 失败转换是否不落库
- 状态流转是否存在跳步

### 6.3 Money formula checker

当前已有 `money_formula_checker.py`，需要升级为读取 `category=money_formula` 规则。

检查：

- `payableAmount = itemTotal + shippingFee - discountAmount - pointsDeductionAmount`
- `refundAmount <= paidAmount`
- `invoiceAmount == payableAmount` 或设计规定公式
- BigDecimal 不能使用 double 计算
- 金额不得为负
- 折扣/积分抵扣不能超过 eligible amount

### 6.4 Error contract checker

新增或并入 `contract_checker.py`：

```text
work/tools/scripts/checkers/error_contract_checker.py
```

检查：

- `MethodArgumentNotValidException` / `ConstraintViolationException` → 400
- NotFound → 404
- Conflict → 409
- 全局异常不得统一 200
- 错误响应字段名和 error code 与文档一致
- 不得吞异常后返回 success

### 6.5 Pagination / sorting checker

当前已有 `sorting_pagination_checker.py`，需要升级：

- `page=0` / `page=1` 语义按设计统一
- `size=0` / negative size 按设计返回 400 或 fallback
- list 返回空数组，不返回 null
- 默认排序稳定，例如 `createdAt desc, id desc`
- repository query 必须带 soft-delete / status / userId 过滤

### 6.6 Clock usage checker

当前已有 `clock_usage_checker.py`，需要升级：

- 核心业务逻辑禁止直接 `LocalDateTime.now()` / `Instant.now()` / `System.currentTimeMillis()`
- 必须使用可注入 `Clock` / `TimeProvider`
- 生成 repair task 时给出具体 service 方法位置

### 6.7 Transaction / failure isolation checker

新增：

```text
work/tools/scripts/checkers/transaction_boundary_checker.py
```

检查：

- 支付、扣库存、退款、发货等跨表更新必须有事务边界
- 后置通知失败不得错误回滚主交易，除非设计明确要求
- catch exception 后不得隐藏主流程失败

---

## 7. 规格生成测试升级

当前 `spec_test_generator.py` 已经可以从 API contract 和 business rules 生成 MockMvc/JUnit 测试，但它还不是主 gate。需要升级为：

```text
work/tools/scripts/spec_test_generator.py
work/tools/scripts/generated_spec_test_runner.py
```

输出：

```text
.agent-work/generated-tests/
.agent-work/spec_test_manifest.json
.agent-work/generated_spec_test_report.json
.agent-work/generated_spec_test_logs/
```

### 7.1 测试类型

1. Contract tests
   - 每个 endpoint 的 method/path/status/schema/error code
   - required fields null/blank/invalid type
   - response wrapper / data / pagination metadata

2. Behavior tests
   - 状态机非法转换
   - 金额公式边界
   - 库存不足
   - 用户冻结/未激活
   - 软删除不可见
   - 权限 / user ownership

3. Metamorphic tests
   - 同一订单重复支付只允许一次
   - 取消后支付必须失败
   - 退款总额不得超过实付
   - 同一 list 多次查询排序稳定
   - 边界分页不会丢数据/重复数据

4. Error format tests
   - 400/404/409/500 的结构一致
   - code/message 字段存在且类型稳定
   - 不返回 200 包错误

### 7.2 生成测试必须可编译

当前生成测试存在潜在问题：

- 可能缺少 fixture setup
- path variable 值可能未绑定
- 业务数据可能不存在
- 只用 MockMvc 正向请求不够

改造要求：

```text
- 每个 generated test 必须在 manifest 标注 fixture strategy
- 无法构造 fixture 的测试标记为 static_assertion_only，而不是进入 runnable tests
- runner 先 compile generated tests；不可编译即阻塞 gate
- generated tests 不写入 test-cases/**，只放 .agent-work 或临时复制到 code/**/src/test/generated/**
```

### 7.3 Runner 行为

新增命令：

```bash
python3 work/tools/scripts/spec_test_generator.py --root $PROJECT_ROOT --mode competition-final
python3 work/tools/scripts/generated_spec_test_runner.py --root $PROJECT_ROOT
```

`generated_spec_test_runner.py` 输出：

```json
{
  "generated": 120,
  "compiled": 118,
  "runnable": 80,
  "passed": 80,
  "failed": 0,
  "static_only": 38,
  "blocking": []
}
```

---

## 8. Repair task schema 改造

当前 task 可能从 public symptom 或泛化 feature 生成，缺少强设计锚点。新 task 必须 spec-backed。

新 schema：

```json
{
  "task_id": "TASK-ORDER-STATE-001",
  "priority": "P0",
  "category": "state_machine",
  "spec_id": "REQ-ORDER-STATE-001",
  "design_source": "design-docs/order.md:42-58",
  "expected_behavior": "CANCELLED order cannot transition to PAID; return 409 ORDER_STATUS_CONFLICT and do not mutate order",
  "actual_behavior": {
    "code_symbol": "code/.../OrderService.java#pay",
    "evidence": "method lacks guard for CANCELLED before payment update"
  },
  "code_path": [
    "OrderController#pay",
    "OrderService#pay",
    "OrderRepository#save"
  ],
  "suspected_files": [
    "code/.../OrderService.java",
    "code/.../GlobalExceptionHandler.java"
  ],
  "verification": [
    "checker:state_machine_checker#REQ-ORDER-STATE-001",
    "generated:OrderStateContractTest#cancelledOrderCannotPay"
  ],
  "negative_constraints": [
    "do not modify README.md",
    "do not modify design-docs/**",
    "do not modify test-cases/**",
    "do not change REST path/method/request/response/error semantics",
    "do not hardcode public fixtures"
  ],
  "status": "open"
}
```

强制规则：

```text
- P0/P1 task 必须有 spec_id
- P0/P1 task 必须有 design_source
- P0/P1 task 必须有非占位 code_location / suspected_files
- public matrix failure 不得单独创建 task；除非映射到 spec_id
- code/#deterministic-check 不得作为 P0/P1 code_location
```

---

## 9. 文件级修改清单

### 9.1 `INSTRUCTION.md`

修改目标：让裁判执行说明书明确默认使用 competition-final spec-first 模式。

建议修改：

```text
- 增加 GOAL_AGENT_MODE=competition-final 默认说明
- 明确 public test-cases 是 smoke/canary，不是需求来源
- 完成标准加入 spec_ir / spec_coverage / generated_spec_tests
- 保留 Maven 验证命令
- 明确 final DONE 由 final_goal_gate.py 判定
```

新增完成输出：

```text
.agent-work/spec_ir.json
.agent-work/spec_ir_validation_report.json
.agent-work/spec_coverage.json
.agent-work/generated_spec_test_report.json
.agent-work/static_semantic_report.json
```

### 9.2 `work/skills/goal-agent-spec-driven/SKILL.md`

修改 pipeline：

旧：

```text
Phase 4: Generate Spec-Driven Tests
Phase 5: Baseline Test Run
Phase 6: Localize & Prioritize Repair Tasks
Phase 7: Spec-Verified Repair Loop
```

新：

```text
Phase 0: Preflight
Phase 1: Build Canonical Spec IR
Phase 2: Build API Contract + Business Rule Catalog from Spec IR
Phase 3: Scan Code + Build Symbol Index
Phase 4: Build Trace Matrix + Spec Coverage
Phase 5: Static/Semantic Consistency Checkers
Phase 6: Generate and Run Spec-Derived Tests
Phase 7: Build Spec-Backed Repair Queue
Phase 8: Repair Loop
Phase 9: Build + Public Smoke + Stability
Phase 10: Final Goal Gate + Report
```

核心文字修改：

```text
公开 black-box tests 只能作为 smoke/canary 和 regression signal。
默认 competition-final 模式下，公开 tests 不得创建 requirements、features 或 repair tasks。
```

### 9.3 `work/skills/shophub-orchestrator.md`

修改目标：orchestrator 按 artifact-first 执行。

新增状态机：

```text
BUILD_SPEC_IR
VALIDATE_SPEC_IR
BUILD_TRACE_MATRIX
RUN_SPEC_CHECKERS
RUN_GENERATED_SPEC_TESTS
BUILD_SPEC_BACKED_REPAIR_QUEUE
REPAIR_LOOP
PUBLIC_SMOKE
FINAL_GATE
```

修改 fan-out audit：

```text
每个 auditor 输出 .agent-work/audits/<module>.json
主 orchestrator 合并 audits，不再依赖自然语言 issue append 作为唯一入口。
```

### 9.4 `work/tools/scripts/shophub_goal_runner.py`

修改目标：新增 competition-final 主链路 helper。

新增 subcommands：

```text
build-spec-ir
validate-spec-ir
trace
spec-check
generate-spec-tests
run-spec-tests
public-smoke
final-gates
```

修改现有函数：

- `build_rules_and_contracts()`：默认不调用 `public_case_rule_builder.py`。
- `run_baseline_matrix()`：改名或新增 `run_public_smoke()`，只用于 final smoke。
- `build_repair_queue()`：只消费 spec-backed issues/tasks；local-public-debug 才消费 public diagnostics。
- `run_final_goal_gate()`：新增 spec gates。

### 9.5 `work/tools/scripts/public_case_rule_builder.py`

修改目标：降级为 diagnostic-only。

默认输出：

```text
.agent-work/public_diagnostics.json
```

只有 `GOAL_AGENT_MODE=local-public-debug` 时才输出：

```text
.agent-work/public_case_rules.json
```

新增硬限制：

```text
- competition-final 模式下不得写入 business_rules.json
- competition-final 模式下不得影响 feature_list.json
- competition-final 模式下不得影响 repair_tasks.jsonl
```

### 9.6 `work/tools/scripts/feature_registry.py`

修改目标：feature pass/fail 以 spec/checker/generated tests 为准。

删除或条件化：

```text
matrix_all_green 作为 business_rules passes 的主条件
public_case_rules 默认进入 features
public matrix failure 默认生成 P0/P1 feature
```

新增输入：

```text
spec_ir.json
spec_coverage.json
generated_spec_test_report.json
checker_reports/*.json
```

新判定：

```text
P0/P1 feature passes =
  trace linked
  AND checker no blocking issue
  AND generated assertion pass or static assertion pass
```

### 9.7 `work/tools/scripts/rule_issue_builder.py`

修改目标：拒绝 public symptom-only issue。

新增校验：

```text
if priority in P0/P1:
  require spec_id
  require design_source
  require non-placeholder code_location
```

删除或条件化：

```text
matrix failure → issue
```

允许：

```text
matrix failure + spec_id mapping + code location → issue
```

### 9.8 `work/tools/scripts/repair_task_builder.py`

修改目标：只生成 spec-backed P0/P1 task。

删除或条件化：

```text
tasks_from_test_symptoms()
tasks_from_public_case_rules()
```

新增：

```text
tasks_from_spec_ir_gaps()
tasks_from_trace_gaps()
tasks_from_checker_reports()
tasks_from_generated_spec_test_failures()
```

Dedup key 从：

```text
(related_api, field, type)
```

改为：

```text
(spec_id, category, primary_code_symbol)
```

### 9.9 `work/tools/scripts/contract_checker.py`

修改目标：从 API 形状检查升级为 API + 业务规格检查入口之一。

必须补齐：

```text
check_null_vs_empty_list()
check_success_status()
check_query_params()
check_path_variables()
check_error_response_schema()
check_required_validation_annotations()
```

建议拆分：

```text
contract_checker.py                  # orchestration wrapper
checkers/api_shape_checker.py
checkers/response_schema_checker.py
checkers/error_contract_checker.py
```

### 9.10 `work/tools/scripts/spec_test_generator.py`

修改目标：从“生成辅助测试”升级为 final gate oracle。

新增能力：

```text
--from-spec-ir
--emit-manifest
--fixture-strategy auto|static-only|mockmvc
--copy-into-code-tests
--cleanup-after-run
```

输出 manifest：

```json
{
  "tests": [
    {
      "spec_id": "REQ-ORDER-STATE-001",
      "class": "OrderStateContractTest",
      "method": "cancelledOrderCannotPay",
      "runnable": true,
      "fixture_strategy": "repository-seed",
      "assertions": ["status=409", "errorCode=ORDER_STATUS_CONFLICT", "stateUnchanged"]
    }
  ]
}
```

### 9.11 `work/tools/scripts/final_goal_gate.py`

修改目标：新增 spec-first gates。

新 gates：

```text
input_integrity
spec_ir
trace_coverage
static_consistency
generated_spec_tests
code_tests
code_install
public_smoke
stability
forbidden_guard
hardcoding_guard
repair_report
```

DONE 条件：

```text
- spec_ir valid
- P0 trace coverage 100%
- P1 trace coverage >= 95% 或剩余项写入风险报告
- static checkers P0=0, P1=0
- generated spec tests pass
- code tests pass
- code install pass
- public smoke pass 或失败有明确 design-backed exception
- forbidden/hardcoding guard pass
- 修复报告.md 包含 evidence
```

### 9.12 `work/tools/scripts/blackbox_explorer.py`

修改目标：保留，但从 repair loop 主链路移出。

新行为：

```text
competition-final:
  - 只允许 --mode smoke
  - 输出 .agent-work/public_smoke_report.json
  - 不写 repair_tasks.jsonl
  - 不写 feature_list.json

local-public-debug:
  - 保留 baseline/sweep/class/method 探索
  - 输出 public_diagnostics
```

### 9.13 `work/skills/shophub-patch-agent.md`

修改目标：patch-agent 只处理 spec-backed task。

新增硬规则：

```text
拒绝没有 spec_id/design_source 的 P0/P1 task。
拒绝仅由 public test failure 形成的 task。
修复前必须列出 spec_id、design_source、expected_behavior、code_path、verification。
修复后必须运行 spec checker 或 generated spec test。
```

### 9.14 `work/skills/shophub-review-agent.md`

修改目标：fresh-context adversarial review。

输入仅包含：

```text
- task JSON
- patch diff
- relevant code slice
- checker report
- generated test report
```

检查：

```text
- spec_id 是否完整实现
- 是否改变冻结 API
- 是否硬编码 public fixture
- 是否只修 symptom 没修 root cause
- 是否引入事务/状态/金额/分页副作用
```

### 9.15 `work/skills/shophub-report-writer.md`

修改目标：报告从“测试结果说明”升级为“规格覆盖证明”。

报告必须包含：

```text
- Spec IR summary
- P0/P1 trace coverage
- Static checker summary
- Generated spec test summary
- Public smoke summary
- Modified code files
- Requirement → patch → verifier mapping
- Remaining design ambiguity / risk
```

---

## 10. 新 final pipeline

```text
Phase 0: Preflight
  - verify README.md, design-docs/, code/pom.xml, test-cases/pom.xml, maven-settings.xml
  - snapshot forbidden inputs

Phase 1: Build Canonical Spec IR
  - spec_ir_builder.py
  - spec_ir_validator.py

Phase 2: Build API / Business Rule Catalog
  - api_contract_builder.py from README/design-docs only
  - business_rule_builder.py from design-docs only
  - no public_case_rule_builder in competition-final

Phase 3: Scan Code
  - spring_scanner.py
  - dto_analyzer.py
  - exception_analyzer.py
  - code symbol index

Phase 4: Trace Matrix + Coverage
  - trace_matrix_builder.py
  - spec_coverage_gate.py

Phase 5: Static / Semantic Checkers
  - contract_checker.py
  - api_shape_checker.py
  - state_machine_checker.py
  - money_formula_checker.py
  - error_contract_checker.py
  - sorting_pagination_checker.py
  - clock_usage_checker.py
  - transaction_boundary_checker.py

Phase 6: Generated Spec Tests
  - spec_test_generator.py --from-spec-ir
  - generated_spec_test_runner.py

Phase 7: Build Repair Queue
  - rule_issue_builder.py from spec/checker/generated-test gaps
  - repair_task_builder.py spec-backed only

Phase 8: Repair Loop
  - patch-agent minimal fix
  - focused spec checker / generated test
  - fresh review
  - repeat until spec gates pass or safe stop

Phase 9: Build + Public Smoke + Stability
  - mvn -s maven-settings.xml -f code/pom.xml test
  - mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
  - mvn -s maven-settings.xml -f test-cases/pom.xml test as smoke
  - stability_runner.py
  - forbidden_change_guard.py
  - hardcoding_guard.py

Phase 10: Final Goal Gate + Report
  - final_goal_gate.py
  - report writer
```

---

## 11. 验收标准

### 11.1 机器 gate

`python3 work/tools/scripts/final_goal_gate.py --root $PROJECT_ROOT` 必须验证：

```json
{
  "done": true,
  "gates": {
    "input_integrity": {"passed": true},
    "spec_ir": {"passed": true},
    "trace_coverage": {"passed": true},
    "static_consistency": {"passed": true},
    "generated_spec_tests": {"passed": true},
    "code_tests": {"passed": true},
    "code_install": {"passed": true},
    "public_smoke": {"passed": true},
    "stability": {"passed": true},
    "forbidden_guard": {"passed": true},
    "hardcoding_guard": {"passed": true},
    "repair_report": {"passed": true}
  }
}
```

### 11.2 P0/P1 要求

```text
P0:
- 100% 有 spec_id
- 100% 有 source line
- 100% trace 到代码或明确标记为 design ambiguity
- 100% 有 checker 或 generated assertion
- blocker count = 0

P1:
- >=95% trace 到代码或有明确风险说明
- blocker count = 0，或剩余项有 design-backed risk waiver
```

### 11.3 禁止项

```text
- 不得修改 README.md
- 不得修改 design-docs/**
- 不得修改 test-cases/**
- 不得改变 REST API 契约
- 不得硬编码 public fixture
- 不得通过返回 200 包错误规避异常
- 不得吞异常伪装成功
- 不得删除核心过滤条件，如 deleted/status/userId/tenantId
```

---

## 12. 实施优先级

### P0：立即修复主链路过拟合风险

预计改动文件：

```text
work/tools/scripts/pipeline_mode.py
work/tools/config/pipeline_modes.json
work/tools/scripts/public_case_rule_builder.py
work/tools/scripts/feature_registry.py
work/tools/scripts/rule_issue_builder.py
work/tools/scripts/repair_task_builder.py
work/tools/scripts/final_goal_gate.py
work/skills/goal-agent-spec-driven/SKILL.md
work/skills/shophub-orchestrator.md
```

完成标准：

```text
competition-final 模式下：
- public_case_rules 不进入主 feature/task
- matrix failure 不自动创建 P0/P1 task
- final gate 要求 spec-related artifact
```

### P1：引入 Spec IR 和 Coverage Gate

预计新增：

```text
work/tools/scripts/spec_ir_builder.py
work/tools/scripts/spec_ir_validator.py
work/tools/scripts/trace_matrix_builder.py
work/tools/scripts/spec_coverage_gate.py
```

完成标准：

```text
.agent-work/spec_ir.json valid
.agent-work/spec_coverage.json generated
P0 trace coverage 100%
```

### P2：增强 checker 和 generated spec tests

预计新增/修改：

```text
work/tools/scripts/spec_test_generator.py
work/tools/scripts/generated_spec_test_runner.py
work/tools/scripts/checkers/api_shape_checker.py
work/tools/scripts/checkers/error_contract_checker.py
work/tools/scripts/checkers/transaction_boundary_checker.py
work/tools/scripts/checkers/state_machine_checker.py
work/tools/scripts/checkers/money_formula_checker.py
work/tools/scripts/checkers/sorting_pagination_checker.py
```

完成标准：

```text
generated spec tests compile/pass
static checkers P0/P1 = 0
```

### P3：artifact-first 多 agent 改造

预计修改：

```text
work/skills/shophub-module-auditor.md
work/skills/shophub-cross-cut-auditor.md
work/skills/shophub-patch-agent.md
work/skills/shophub-review-agent.md
work/skills/shophub-report-writer.md
```

完成标准：

```text
.agent-work/audits/*.json generated
repair task 全部 spec-backed
review-agent fresh-context gap report generated
```

---

## 13. 建议 PR 拆分

### PR-1：Competition-final mode and public-test demotion

内容：

```text
- pipeline_modes.json
- pipeline_mode.py
- public_case_rule_builder diagnostic-only
- feature_registry 不默认消费 public matrix
- rule_issue_builder / repair_task_builder 不默认消费 public symptoms
```

### PR-2：Spec IR and trace coverage

内容：

```text
- spec_ir_builder.py
- spec_ir_validator.py
- trace_matrix_builder.py
- spec_coverage_gate.py
- final_goal_gate 接入 spec gates
```

### PR-3：Checker upgrade

内容：

```text
- api_shape_checker
- error_contract_checker
- transaction_boundary_checker
- 升级 state/money/pagination/clock checker
- contract_checker placeholder 补齐
```

### PR-4：Generated spec tests as oracle

内容：

```text
- spec_test_generator --from-spec-ir
- generated_spec_test_runner
- generated_spec_test_report gate
```

### PR-5：Agent prompt and report rewrite

内容：

```text
- orchestrator skill
- patch-agent skill
- review-agent skill
- report-writer skill
- INSTRUCTION.md completion criteria
```

---

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 设计文档本身不完整 | Spec IR 无法覆盖隐藏测试 | 记录 design ambiguity；用 API contract 和 public README 补充，但不使用 public fixture |
| 生成测试 fixture 难构造 | generated tests 不可运行 | 标记 static_assertion_only；用 checker 覆盖；不让不可编译测试进入 gate |
| checker 误报 | 阻塞修复 | 每个 checker 输出 confidence、evidence、waiver reason；P0 不允许无证据误报 |
| 多 agent 产物不一致 | repair queue 混乱 | artifact-first；所有 subagent 输出 JSON schema；主 orchestrator 只合并合法 artifact |
| public smoke 失败但 spec gate 通过 | 可能存在公开兼容问题 | smoke 失败必须映射到 spec_id；无法映射则记录为 API compatibility risk，不允许硬编码修复 |
| 改造范围大 | 影响提交稳定性 | 按 PR 分阶段实现；先 P0 降低过拟合，再 P1/P2 增强 oracle |

---

## 15. 最终 Definition of Done

改造完成后，项目必须满足：

```text
1. 默认 GOAL_AGENT_MODE=competition-final。
2. 公开 test-cases 不再默认作为需求/feature/task 来源。
3. README.md + design-docs 能生成 spec_ir.json。
4. P0/P1 requirement 能生成 trace coverage。
5. 静态 / 语义 checker 能发现 API、状态机、金额、异常、分页、时间、事务问题。
6. generated spec tests 成为 final gate 的一等验证证据。
7. repair task 必须 spec-backed；P0/P1 不允许 public-symptom-only task。
8. patch-agent 修复后必须运行 spec checker 或 generated spec test。
9. review-agent fresh-context 审查 diff 与 spec task 的一致性。
10. final_goal_gate.py 以 spec-first gates 判定 DONE。
11. 修复报告.md 展示 requirement → code → patch → verifier 的完整证据链。
```

---

## 16. 核心结论

当前系统需要避免的不是“运行公开测试”，而是“把公开测试当作需求来源和修复老师”。比赛最终隐藏黑盒不会提供二次反馈，因此系统必须把 README 和 design-docs 提升为可执行 oracle。

最终架构应从：

```text
公开黑盒失败 → 猜设计 → 修代码 → 再跑公开黑盒
```

改为：

```text
设计文档/API 契约 → 可执行规格 → 静态/生成测试验证 → 修代码 → 隐藏黑盒最终评分
```

这份修改方案的第一优先级是切断 public-test-derived rules/tasks/features 的默认路径；第二优先级是引入 Spec IR、Trace Coverage 和 Generated Spec Tests；第三优先级是增强状态机、金额、异常、分页、时间、事务等高价值语义 checker。