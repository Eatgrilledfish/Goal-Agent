# ShopHub 设计实现一致性检查与修复 Goal Runner 设计文档

## 1. 项目背景

本项目用于参加 ShopHub 电商系统“设计实现一致性检查与修复”比赛。

比赛要求参赛者使用 AI Agent 阅读：

* `design-docs/` 中的业务设计文档；
* `code/` 中的 Java Spring Boot 多模块工程；
* `API基线文档.md` 中冻结的 REST API 契约；
* `test-cases/` 中的公开黑盒 JUnit 测试；

并完成：

1. 对比设计文档与代码实现；
2. 发现普通不一致点；
3. 修复代码，使代码行为符合设计文档；
4. 保持项目可编译；
5. 保持 REST API 契约不变；
6. 输出完整修复报告。

本设计文档的目标是开发一个可在本地运行的 **Goal Runner 多 Agent 工作流**。该工作流能够自动按状态机持续执行任务，直到完成比赛要求或触发安全停止条件。

---

## 2. 设计目标

### 2.1 核心目标

开发一个面向 ShopHub 比赛的自动化 Agent 流程，具备以下能力：

1. 自动建立设计文档索引；
2. 自动建立 API 冻结契约索引；
3. 自动建立代码结构地图；
4. 自动运行基础测试和公开黑盒测试；
5. 自动按模块审计设计与实现不一致点；
6. 自动按优先级选择问题；
7. 自动小步修复代码；
8. 自动检查 API 契约是否被破坏；
9. 自动运行分步测试和全量测试；
10. 自动 review 每轮 diff；
11. 自动根据评分决定继续、重审、回滚或停止；
12. 自动生成最终 `修复报告.md`。

### 2.2 非目标

本项目不做以下事情：

1. 不修改 `design-docs/`；
2. 不修改 `API基线文档.md`；
3. 不修改比赛规则文档；
4. 不为了公开黑盒测试硬编码逻辑；
5. 不改变 REST API URL；
6. 不改变 HTTP Method；
7. 不改变 Request Header 定义；
8. 不改变 Request Body 字段名和类型；
9. 不改变 Response Body 字段名和类型；
10. 不做无依据的大规模重构。

---

## 3. 基本原则

### 3.1 设计文档优先

`design-docs/` 是最终业务验收基准。

当代码行为与设计文档冲突时，应修改代码，而不是修改设计文档。

### 3.2 API 基线冻结

`API基线文档.md` 是冻结 REST API 契约。

允许修复业务逻辑，但不得破坏 API 外部签名。

冻结内容包括：

* REST URL；
* HTTP Method；
* Request Header；
* Request Body 字段名；
* Request Body 字段类型；
* Response Body 字段名；
* Response Body 字段类型；
* 错误码；
* 黑盒支撑管理接口。

### 3.3 测试只是症状，不是需求

`test-cases/` 公开黑盒测试用于暴露问题，但不能作为唯一设计依据。

修复必须能回溯到 `design-docs/` 或 `API基线文档.md`。

### 3.4 小步修复

每轮修复只处理：

* 1 个明确 issue；
* 或 1 组强相关 issue。

禁止一次性大范围修改。

### 3.5 Agent 分工

采用主控 Agent + 专项 Subagent 模式。

主控 Agent 负责流程、状态、评分和决策。

专项 Agent 负责单一职责任务，例如设计抽取、API 守卫、代码地图、测试诊断、模块审计、代码修复、代码 review、报告生成。

### 3.6 自动循环但必须有停止条件

Goal Runner 可以持续自动执行，但必须在以下情况下停止：

* 比赛任务完成；
* 高优问题清空；
* 全量测试通过；
* 连续多轮没有有效进展；
* API 契约存在破坏风险；
* 修复引入连续回归；
* 达到最大轮数；
* 达到 token 或时间预算。

---

## 4. 总体架构

### 4.1 Agent 架构

```text
ShopHub Goal Runner Orchestrator
│
├── spec-librarian      设计文档索引 Agent，只读
├── api-guardian        API 契约守卫 Agent，只读
├── code-mapper         代码结构地图 Agent，只读
├── test-diagnoser      测试诊断 Agent，只读
├── module-auditor      模块一致性审计 Agent，只读
├── patch-agent         小步修复 Agent，可写
├── review-agent        代码审查 Agent，只读
└── report-writer       修复报告 Agent，可写报告
```

### 4.2 工作流总览

```text
INIT
  ↓
READ_SPECS
  ↓
READ_API_BASELINE
  ↓
MAP_CODE
  ↓
RUN_BASELINE_TESTS
  ↓
AUDIT_INCONSISTENCIES
  ↓
PRIORITIZE_ISSUES
  ↓
FIX_LOOP
  ↓
RUN_FULL_TESTS
  ↓
WRITE_REPORT
  ↓
DONE
```

---

## 5. 目录结构设计

项目根目录保持比赛原结构：

```text
.
├── code/
├── design-docs/
├── test-cases/
├── API基线文档.md
├── 黑盒用例说明.md
├── 比赛说明.md
├── AGENTS.md
├── .opencode/
│   └── agents/
│       ├── spec-librarian.md
│       ├── api-guardian.md
│       ├── code-mapper.md
│       ├── test-diagnoser.md
│       ├── module-auditor.md
│       ├── patch-agent.md
│       ├── review-agent.md
│       └── report-writer.md
└── .agent-work/
    ├── state.json
    ├── goal.md
    ├── spec_rules.jsonl
    ├── api_contract.json
    ├── api_snapshot_baseline.json
    ├── api_snapshot_current.json
    ├── code_map.md
    ├── baseline_tests.md
    ├── issues.jsonl
    ├── fix_plan.md
    ├── final_report_source.md
    ├── rounds/
    │   ├── round-001.md
    │   ├── round-002.md
    │   └── ...
    ├── test-results/
    │   ├── code-test-baseline.log
    │   ├── code-install-baseline.log
    │   ├── blackbox-baseline.log
    │   ├── round-001-focused.log
    │   └── ...
    └── reports/
        └── 修复报告.md
```

---

## 6. 状态文件设计

### 6.1 `.agent-work/state.json`

`state.json` 用于记录 Goal Runner 的当前执行状态。

示例：

```json
{
  "phase": "FIX_LOOP",
  "round": 4,
  "max_rounds": 20,
  "fixed_count": 5,
  "reverted_count": 1,
  "high_priority_open": 3,
  "medium_priority_open": 4,
  "low_priority_open": 6,
  "consecutive_no_progress": 0,
  "consecutive_regressions": 0,
  "api_contract_safe": true,
  "last_focused_test": "passed",
  "last_full_test": "failed",
  "last_score": 86,
  "should_continue": true,
  "stop_reason": null
}
```

### 6.2 字段说明

| 字段                      | 含义            |
| ----------------------- | ------------- |
| phase                   | 当前状态机阶段       |
| round                   | 当前修复轮次        |
| max_rounds              | 最大修复轮数        |
| fixed_count             | 已修复问题数        |
| reverted_count          | 已回滚轮次数        |
| high_priority_open      | 未处理高优问题数      |
| medium_priority_open    | 未处理中优问题数      |
| low_priority_open       | 未处理低优问题数      |
| consecutive_no_progress | 连续无有效进展次数     |
| consecutive_regressions | 连续引入回归次数      |
| api_contract_safe       | 当前 API 契约是否安全 |
| last_focused_test       | 最近一次聚焦测试结果    |
| last_full_test          | 最近一次全量测试结果    |
| last_score              | 最近一轮评分        |
| should_continue         | 是否继续运行        |
| stop_reason             | 停止原因          |

---

## 7. Agent 详细设计

## 7.1 Orchestrator 主控 Agent

### 职责

Orchestrator 是唯一主控 Agent，负责：

1. 维护状态机；
2. 创建和更新 `.agent-work/state.json`；
3. 调用 subagent；
4. 合并 subagent 输出；
5. 控制修复优先级；
6. 执行测试命令；
7. 触发 API 契约检查；
8. 触发代码 review；
9. 打分；
10. 决定继续、重审、回滚或停止；
11. 最终触发报告生成。

### Orchestrator 不应该做的事

1. 不直接大范围扫描所有文件；
2. 不绕过 subagent 自己修代码；
3. 不在没有设计依据时创建 issue；
4. 不在 API 契约不安全时继续修复；
5. 不忽略测试失败；
6. 不无限循环。

---

## 7.2 spec-librarian

### 类型

只读 Agent。

### 输入

* `design-docs/`

### 输出

* `.agent-work/spec_rules.jsonl`
* `.agent-work/01_spec_index.md`

### 任务

1. 阅读 `design-docs/` 中所有文档；
2. 按模块抽取业务规则；
3. 抽取实体规则、状态规则、金额规则、库存规则、订单规则、支付规则、异常规则、边界条件；
4. 为每条规则生成稳定 `spec_id`；
5. 输出结构化 JSONL。

### 输出格式

```json
{
  "spec_id": "ORDER-STATUS-001",
  "module": "order",
  "source_doc": "design-docs/order.md",
  "section": "订单状态流转",
  "design_rule": "订单取消后应释放库存",
  "expected_behavior": "取消订单时恢复库存并记录状态变更",
  "boundary_conditions": ["已支付订单不得直接取消", "已发货订单不得取消"],
  "related_api_if_any": ["/api/orders/{id}/cancel"],
  "severity_hint": "high"
}
```

### 约束

1. 不读取代码实现；
2. 不提出修复方案；
3. 不修改任何文件；
4. 不根据公开测试反推规则。

---

## 7.3 api-guardian

### 类型

只读 Agent。

### 输入

* `API基线文档.md`
* `code/`

### 输出

* `.agent-work/api_contract.json`
* `.agent-work/api_snapshot_baseline.json`
* `.agent-work/api_snapshot_current.json`
* `.agent-work/02_api_contract_index.md`

### 任务

1. 阅读 `API基线文档.md`；
2. 抽取冻结 API 契约；
3. 扫描代码中 Controller 和 DTO；
4. 建立当前 API 快照；
5. 每轮修复后比较基线契约和当前代码；
6. 检测 REST API 是否被破坏。

### API 契约字段

```json
{
  "endpoint_id": "ORDER-CREATE-API",
  "method": "POST",
  "url": "/api/orders",
  "headers": {
    "X-User-Id": "Long"
  },
  "request_body": {
    "userId": "Long",
    "items": "List<OrderItemRequest>"
  },
  "response_body": {
    "orderId": "Long",
    "status": "String"
  },
  "error_codes": ["ORDER_NOT_FOUND", "INVALID_ORDER_STATUS"],
  "frozen": true
}
```

### 阻断规则

出现以下情况必须阻断：

1. URL 改变；
2. Method 改变；
3. Header 改变；
4. Request 字段名改变；
5. Request 字段类型改变；
6. Response 字段名改变；
7. Response 字段类型改变；
8. 错误码丢失或语义被破坏。

---

## 7.4 code-mapper

### 类型

只读 Agent。

### 输入

* `code/`

### 输出

* `.agent-work/code_map.md`
* `.agent-work/code_call_chains.jsonl`

### 任务

1. 扫描 Maven 多模块结构；
2. 找出 Controller、Service、Repository、DTO、Exception、Config；
3. 建立 API 到服务实现的调用链；
4. 建立设计模块和代码模块之间的映射；
5. 找出相关单元测试位置。

### 输出示例

```md
## order 模块

- Controller:
  - code/order-service/src/main/java/.../OrderController.java

- Service:
  - code/order-service/src/main/java/.../OrderService.java

- Repository:
  - code/order-service/src/main/java/.../OrderRepository.java

- DTO:
  - code/order-service/src/main/java/.../OrderRequest.java
  - code/order-service/src/main/java/.../OrderResponse.java

- Tests:
  - code/order-service/src/test/java/.../OrderServiceTest.java

- Call Chain:
  - POST /api/orders → OrderController.create → OrderService.createOrder → OrderRepository.save
```

---

## 7.5 test-diagnoser

### 类型

只读 Agent。

### 输入

* Maven 测试日志
* `.agent-work/spec_rules.jsonl`
* `.agent-work/code_map.md`

### 输出

* `.agent-work/baseline_tests.md`
* `.agent-work/test_symptoms.jsonl`

### 任务

1. 运行或分析基础测试日志；
2. 运行或分析公开黑盒测试日志；
3. 归类失败原因；
4. 将失败症状映射到可能的设计规则和代码模块；
5. 明确说明测试失败只是症状，不是设计依据。

### 测试命令

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

### 输出示例

```json
{
  "test_name": "OrderCancelBlackBoxTest",
  "failure_type": "business_behavior_mismatch",
  "symptom": "取消订单后库存未恢复",
  "likely_modules": ["order", "inventory"],
  "related_spec_ids": ["ORDER-STATUS-001", "INV-STOCK-002"],
  "is_design_evidence": false
}
```

---

## 7.6 module-auditor

### 类型

只读 Agent。

### 输入

* 指定模块名；
* `.agent-work/spec_rules.jsonl`；
* `.agent-work/code_map.md`；
* `.agent-work/api_contract.json`；
* `.agent-work/test_symptoms.jsonl`。

### 输出

* `.agent-work/issues.jsonl`

### 任务

1. 对比指定模块的设计规则和代码实现；
2. 找出普通不一致点；
3. 每个不一致点必须包含设计依据；
4. 每个不一致点必须包含代码位置；
5. 每个不一致点必须评估 API 影响；
6. 输出结构化 issue。

### Issue 格式

```json
{
  "issue_id": "ORDER-INV-001",
  "severity": "high",
  "module": "order",
  "design_basis": "design-docs/order.md#订单取消",
  "code_location": "code/order-service/src/main/java/.../OrderService.java#cancelOrder",
  "design_behavior": "取消订单应释放库存",
  "actual_behavior": "当前代码仅修改订单状态，未调用库存释放逻辑",
  "type": "business_rule_mismatch",
  "api_impact": "none",
  "fix_suggestion": "在订单取消成功事务内调用库存释放逻辑",
  "test_suggestion": "新增订单取消释放库存单元测试，并运行订单黑盒测试",
  "confidence": 0.92,
  "estimated_fix_effort": "small",
  "status": "open"
}
```

### 严重程度定义

| 严重程度   | 说明                    |
| ------ | --------------------- |
| high   | 影响订单、库存、支付、金额、状态机、错误码 |
| medium | 影响校验、查询、边界条件、幂等性      |
| low    | 影响提示、日志、非核心配置、低风险行为   |

---

## 7.7 patch-agent

### 类型

可写 Agent。

### 输入

* 单个 issue；
* 相关设计依据；
* 相关代码位置；
* API 契约片段；
* 测试建议。

### 输出

* 修改后的代码；
* `.agent-work/rounds/round-XXX.md`；
* 测试日志。

### 任务

1. 只修当前 issue；
2. 修复前说明计划；
3. 修改最少必要文件；
4. 优先修改 Service / Domain 逻辑；
5. 能不改 Controller 就不改 Controller；
6. 能不改 DTO 就不改 DTO；
7. 增加或调整必要单元测试；
8. 运行聚焦测试；
9. 输出修复摘要。

### 每轮修改范围限制

默认每轮最多修改：

* 3 个 Java 业务文件；
* 2 个测试文件；
* 1 个配置文件；
* 0 个设计文档；
* 0 个 API 基线文档。

如果超过范围，必须进入 `REPLAN`，不得直接继续。

### 修复摘要格式

````md
# Round 001

## Issue

- issue_id: ORDER-INV-001
- severity: high
- module: order

## Design Basis

取消订单应释放库存。

## Before

当前代码仅更新订单状态，未释放库存。

## After

取消订单成功后，在同一事务内释放库存。

## Modified Files

- code/order-service/src/main/java/.../OrderService.java
- code/order-service/src/test/java/.../OrderServiceTest.java

## API Impact

None.

## Tests Run

```bash
mvn -f code/pom.xml -pl order-service test
````

## Result

PASS.

## Risk

需要全量测试确认未影响库存模块。

````

---

## 7.8 review-agent

### 类型

只读 Agent。

### 输入

- 当前 git diff；
- 当前 issue；
- 设计依据；
- API 契约；
- 测试结果。

### 输出

- Review 结论；
- 风险说明。

### 任务

1. 审查代码是否符合设计；
2. 审查是否破坏 API；
3. 审查是否过度修改；
4. 审查是否硬编码公开测试；
5. 审查是否引入隐藏测试风险；
6. 给出 PASS / REWORK_REQUIRED / REJECT_AND_REVERT。

### 输出格式

```json
{
  "round": 1,
  "issue_id": "ORDER-INV-001",
  "verdict": "PASS",
  "design_match": true,
  "api_safe": true,
  "minimal_change": true,
  "hardcoding_risk": false,
  "regression_risk": "low",
  "comments": ["修复集中在订单取消逻辑，未改变 API 契约。"]
}
````

---

## 7.9 report-writer

### 类型

可写报告 Agent。

### 输入

* `.agent-work/issues.jsonl`
* `.agent-work/rounds/`
* `.agent-work/test-results/`
* 当前 git diff；
* 未修复风险。

### 输出

* `修复报告.md`

### 报告结构

```md
# ShopHub 设计实现一致性检查与修复报告

## 发现的不一致点

| 编号 | 严重程度 | 位置 | 设计描述 | 代码行为 | 类型 | 是否修复 |
|------|----------|------|----------|----------|------|----------|

## 修复详情

逐项说明修改文件、修改前行为、修改后行为和验证方式。

## 未修复风险说明

说明仍未修复或未完全确认的问题。

## 总结

发现总数：
已修复数量：
未修复数量：
仍有风险：
API 契约是否保持不变：
最终验证命令：
最终验证结果：
```

---

## 8. 状态机详细设计

## 8.1 INIT

### 动作

1. 创建 `.agent-work/` 目录；
2. 创建 `state.json`；
3. 检查 `git status`；
4. 记录 baseline；
5. 确认比赛目录存在。

### 必须检查

```text
code/
design-docs/
test-cases/
API基线文档.md
黑盒用例说明.md
比赛说明.md
```

### 输出

* `.agent-work/state.json`
* `.agent-work/goal.md`

---

## 8.2 READ_SPECS

### 动作

1. 调用 `spec-librarian`；
2. 阅读 `design-docs/`；
3. 输出 `spec_rules.jsonl`；
4. 输出 `01_spec_index.md`。

### 成功条件

1. 每个设计文档都被读取；
2. 每个核心模块都有设计规则；
3. 每条规则有来源；
4. 每条规则可追踪。

---

## 8.3 READ_API_BASELINE

### 动作

1. 调用 `api-guardian`；
2. 阅读 `API基线文档.md`；
3. 输出 `api_contract.json`；
4. 生成初始 API 快照。

### 成功条件

1. 所有 API 均被抽取；
2. 所有冻结字段均被记录；
3. 当前代码 API 快照可比较；
4. API 破坏检测机制可执行。

---

## 8.4 MAP_CODE

### 动作

1. 调用 `code-mapper`；
2. 扫描 `code/`；
3. 建立模块、类、调用链地图。

### 成功条件

1. 找到 Maven 模块；
2. 找到 Controller；
3. 找到 Service；
4. 找到 Repository；
5. 找到 DTO；
6. 找到测试类；
7. 能从 API 映射到实现逻辑。

---

## 8.5 RUN_BASELINE_TESTS

### 动作

运行：

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

### 输出

* `.agent-work/test-results/code-test-baseline.log`
* `.agent-work/test-results/code-install-baseline.log`
* `.agent-work/test-results/blackbox-baseline.log`
* `.agent-work/baseline_tests.md`

### 成功条件

即使测试失败也可以继续，但必须完成失败摘要。

---

## 8.6 AUDIT_INCONSISTENCIES

### 动作

1. 按模块调用 `module-auditor`；
2. 对比设计规则和实现；
3. 输出 `issues.jsonl`；
4. 去重。

### 成功条件

1. 每个 issue 有设计依据；
2. 每个 issue 有代码位置；
3. 每个 issue 有置信度；
4. 每个 issue 有修复建议；
5. 每个 issue 标注 API 影响。

---

## 8.7 PRIORITIZE_ISSUES

### 优先级公式

```text
priority_score = severity_weight * confidence * hidden_test_likelihood / fix_complexity_weight
```

### severity_weight

| 严重程度   | 权重 |
| ------ | -: |
| high   |  3 |
| medium |  2 |
| low    |  1 |

### fix_complexity_weight

| 复杂度    | 权重 |
| ------ | -: |
| small  |  1 |
| medium |  2 |
| large  |  4 |

### 优先处理

1. 订单；
2. 库存；
3. 支付；
4. 金额；
5. 状态流转；
6. 错误码；
7. 边界校验；
8. 幂等性；
9. 查询一致性。

---

## 8.8 FIX_LOOP

### 循环步骤

每轮执行：

1. 选择最高优先级 issue；
2. 调用 `patch-agent`；
3. 修改代码；
4. 运行聚焦测试；
5. 调用 `api-guardian`；
6. 调用 `review-agent`；
7. 计算评分；
8. 更新 `state.json`；
9. 决定继续、重审、回滚或停止。

### 每轮测试策略

优先运行模块级测试：

```bash
mvn -f code/pom.xml -pl <module> test
```

如模块依赖复杂，运行：

```bash
mvn -f code/pom.xml test
```

每完成 3 轮，运行全量测试：

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

---

## 9. 评分机制

### 9.1 单轮评分公式

```text
score = evidence_quality * 0.30
      + task_completion * 0.25
      + api_safety * 0.15
      + test_effectiveness * 0.15
      + token_efficiency * 0.15
```

### 9.2 评分维度

| 维度                 | 含义                |
| ------------------ | ----------------- |
| evidence_quality   | 是否有明确设计依据         |
| task_completion    | 是否完整修复当前 issue    |
| api_safety         | 是否保持 API 契约不变     |
| test_effectiveness | 是否运行有效测试          |
| token_efficiency   | 是否控制上下文和 token 消耗 |

### 9.3 token 效率

```text
token_efficiency = min(100, target_token_budget / actual_token_usage * 100)
```

如果无法获取真实 token，可用以下近似指标：

```text
context_efficiency = min(100, expected_file_count / actual_file_count_read * 100)
```

或：

```text
context_efficiency = min(100, expected_changed_files / actual_changed_files * 100)
```

### 9.4 评分动作

|               分数 | 动作                |
| ---------------: | ----------------- |
|            >= 85 | 接受本轮修复，继续下一 issue |
|          70 - 84 | 接受，但记录风险          |
|          60 - 69 | 暂停，重新审视任务边界       |
|             < 60 | 回滚本轮，重新分解         |
| api_safety < 100 | 无条件回滚             |
|             编译失败 | 停止处理新 issue，先修编译  |

---

## 10. 回滚机制

### 10.1 必须回滚的情况

出现以下情况必须回滚本轮：

1. 修改了 `design-docs/`；
2. 修改了 `API基线文档.md`；
3. 修改了比赛说明文档；
4. 改变 REST URL；
5. 改变 HTTP Method；
6. 改变 Request Header；
7. 改变 Request Body 字段名或类型；
8. 改变 Response Body 字段名或类型；
9. 基础工程无法编译；
10. 明显为公开测试硬编码；
11. 单轮修改范围明显过大且没有事先 REPLAN。

### 10.2 推荐回滚方式

修复前记录：

```bash
git diff > .agent-work/rounds/round-XXX-before.diff
```

修复后如果失败，可使用：

```bash
git checkout -- <modified_files>
```

或者在有独立 commit 的情况下：

```bash
git reset --hard HEAD
```

建议每个通过 review 的 round 单独 commit：

```bash
git add .
git commit -m "fix: resolve <issue_id>"
```

---

## 11. 停止条件

### 11.1 DONE 条件

满足任意条件即可进入报告阶段：

1. 高优和中优 issue 均已处理，且全量测试通过；
2. 公开黑盒测试通过，剩余 issue 均为低置信度或高风险；
3. 连续 3 轮没有新增有效修复；
4. 连续 2 轮修复引入回归；
5. 达到最大修复轮数 `max_rounds = 20`；
6. 达到 token 或时间预算；
7. API 契约存在持续破坏风险；
8. Orchestrator 判断继续修复风险大于收益。

### 11.2 不允许停止的情况

以下情况不能直接 DONE：

1. 基础工程无法编译；
2. API 契约已被破坏；
3. 修复报告未生成；
4. 已修复 issue 没有验证记录；
5. 没有记录未修复风险。

---

## 12. 测试策略

### 12.1 基础测试

```bash
mvn -f code/pom.xml test
```

用于确认业务工程自身测试通过。

### 12.2 安装基础工程

```bash
mvn -f code/pom.xml install
```

用于供独立黑盒测试工程解析依赖。

### 12.3 公开黑盒测试

```bash
mvn -f test-cases/pom.xml test
```

用于验证公开 API 行为。

### 12.4 测试执行频率

| 场景        | 测试                             |
| --------- | ------------------------------ |
| 单模块小修     | 模块测试                           |
| 影响多个模块    | code 全量测试                      |
| 每 3 轮修复后  | code test + install + blackbox |
| 准备 DONE 前 | 全量三条命令                         |
| 编译失败后     | 先恢复 `mvn -f code/pom.xml test` |

---

## 13. API 契约检查策略

### 13.1 静态检查

扫描以下内容：

1. Controller 注解；
2. RequestMapping；
3. GetMapping；
4. PostMapping；
5. PutMapping；
6. DeleteMapping；
7. RequestBody DTO；
8. Response DTO；
9. Header 注解；
10. 错误码枚举或常量。

### 13.2 快照比较

生成：

```text
.agent-work/api_snapshot_baseline.json
.agent-work/api_snapshot_current.json
```

比较内容：

```text
endpoint_id
method
url
headers
request_body_fields
request_body_types
response_body_fields
response_body_types
error_codes
```

### 13.3 允许变化

以下变化允许：

1. Service 内部逻辑；
2. Repository 查询逻辑；
3. Domain 行为；
4. 配置；
5. 内部异常处理实现；
6. 测试代码；
7. 不改变外部字段的内部 DTO 方法。

### 13.4 禁止变化

以下变化禁止：

1. 改 URL；
2. 改 Method；
3. 改 Header；
4. 改字段名；
5. 改字段类型；
6. 删除响应字段；
7. 删除请求字段；
8. 改错误码对外语义。

---

## 14. 安全约束

### 14.1 文件修改白名单

允许修改：

```text
code/**/*.java
code/**/application.yml
code/**/application.yaml
code/**/pom.xml
code/**/src/test/**/*.java
```

允许新增：

```text
.agent-work/**
修复报告.md
```

### 14.2 文件修改黑名单

禁止修改：

```text
design-docs/**
API基线文档.md
比赛说明.md
黑盒用例说明.md
test-cases/**
```

说明：比赛规则允许修改 JUnit，但不建议修改 `test-cases/`，因为公开黑盒测试是独立验收参考。若确需修改，只允许本地调试，不得作为最终提交依据。

---

## 15. AGENTS.md 设计

项目根目录应生成 `AGENTS.md`。

内容如下：

````md
# ShopHub Competition Agent Rules

## Mission

Use AI agents to compare design documents, frozen REST API contract, and Java Spring Boot implementation. Find inconsistencies and fix code to match design documents while preserving the frozen API contract.

## Source of Truth

1. `design-docs/` is the business source of truth.
2. `API基线文档.md` is the frozen REST API contract.
3. `test-cases/` public black-box tests are diagnostic signals only.
4. Current code behavior is not authoritative when it conflicts with design documents.

## Forbidden Changes

Do not modify:

- `design-docs/**`
- `API基线文档.md`
- `比赛说明.md`
- `黑盒用例说明.md`

Do not change:

- REST API URL
- HTTP Method
- Request Header definition
- Request Body field names or types
- Response Body field names or types

## Allowed Changes

You may modify:

- Java source code
- `application.yml`
- `pom.xml`
- JUnit tests under `code/`

Avoid modifying `test-cases/`.

## Required Workflow

1. Read design documents.
2. Read API baseline.
3. Build code map.
4. Run baseline tests.
5. Find design-code inconsistencies.
6. Fix code in small steps.
7. Run focused tests after each fix.
8. Run full verification before final report.
9. Produce `修复报告.md`.

## Verification Commands

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
````

## Completion Definition

A task is not complete unless:

* Design basis is recorded.
* Code behavior before fix is recorded.
* Modified files are listed.
* API contract is preserved.
* Relevant tests were run.
* Risks are documented.

````

---

## 16. opencode Subagent 文件设计

### 16.1 `.opencode/agents/spec-librarian.md`

```md
---
name: spec-librarian
description: Read-only agent that extracts business rules from design-docs into structured requirements.
tools:
  write: false
  edit: false
---

You are the Spec Librarian for the ShopHub consistency competition.

Your only job is to read `design-docs/` and extract business rules.

Do not inspect implementation unless explicitly asked.
Do not modify any file.

For each rule, output:

- spec_id
- module
- source_doc
- section
- design_rule
- expected_behavior
- boundary_conditions
- related_api_if_any
- severity_hint

Design documents are the final business authority.
````

### 16.2 `.opencode/agents/api-guardian.md`

```md
---
name: api-guardian
description: Read-only API contract guardian that prevents REST API signature drift.
tools:
  write: false
  edit: false
---

You are the API Guardian.

Your job is to protect the frozen REST API contract in `API基线文档.md`.

You must check whether code changes alter:

- URL
- HTTP Method
- Headers
- Request Body field names
- Request Body field types
- Response Body field names
- Response Body field types
- documented error codes

Any such change must be reported as a blocking violation.

Do not modify code.
Do not propose changing the API baseline.
```

### 16.3 `.opencode/agents/code-mapper.md`

```md
---
name: code-mapper
description: Read-only agent that maps Java Spring Boot modules, APIs, services, repositories, DTOs, exceptions, and tests.
tools:
  write: false
  edit: false
---

You are the Code Mapper.

Your job is to inspect `code/` and produce a concise but useful implementation map.

Map:

- Maven modules
- Controllers
- Services
- Repositories
- DTOs
- Domain models
- Exceptions
- Config files
- Unit tests
- API to implementation call chains

Do not modify files.
Do not infer business correctness.
Only map implementation structure.
```

### 16.4 `.opencode/agents/test-diagnoser.md`

```md
---
name: test-diagnoser
description: Read-only agent that summarizes Maven test failures and maps symptoms to likely modules and design rules.
tools:
  write: false
  edit: false
---

You are the Test Diagnoser.

Your job is to analyze test logs from:

- `mvn -f code/pom.xml test`
- `mvn -f code/pom.xml install`
- `mvn -f test-cases/pom.xml test`

Summarize failures as symptoms.

Important:

- Public black-box tests are diagnostic signals only.
- Do not treat tests as design authority.
- Map failures to possible modules and spec_ids when possible.
- Do not modify files.
```

### 16.5 `.opencode/agents/module-auditor.md`

```md
---
name: module-auditor
description: Read-only agent that compares one business module's design rules against code implementation.
tools:
  write: false
  edit: false
---

You are a Module Consistency Auditor.

Input will include:

- module name
- relevant design rules
- related code map
- API constraints
- optional test failure summaries

Your job:

1. Compare design behavior with implementation behavior.
2. Find ordinary inconsistencies.
3. Provide exact evidence.
4. Do not modify code.

Each issue must include:

- issue_id
- severity
- module
- design_basis
- code_location
- design_behavior
- actual_behavior
- type
- api_impact
- fix_suggestion
- test_suggestion
- confidence
- estimated_fix_effort
- status

Reject any issue that lacks design evidence.
```

### 16.6 `.opencode/agents/patch-agent.md`

```md
---
name: patch-agent
description: Code modification agent that fixes one approved inconsistency at a time.
tools:
  write: true
  edit: true
---

You are the Patch Agent.

You may modify Java source code, application.yml, pom.xml, and JUnit tests under code/.

You must not modify:

- design-docs/**
- API基线文档.md
- 比赛说明.md
- 黑盒用例说明.md

Avoid modifying test-cases/.

You must not change frozen REST API signatures.

Fix exactly the issue assigned to you. Prefer minimal, localized changes.

Before editing, restate:

- issue_id
- design basis
- current code behavior
- planned files to modify
- API impact

After editing, report:

- modified files
- before behavior
- after behavior
- tests run
- remaining risks
```

### 16.7 `.opencode/agents/review-agent.md`

```md
---
name: review-agent
description: Read-only reviewer for design consistency, API safety, regressions, and hidden-test risk.
tools:
  write: false
  edit: false
---

You are the Review Agent.

Review the current diff against:

- design-docs/
- API基线文档.md
- assigned issue
- tests run

Return one of:

- PASS
- REWORK_REQUIRED
- REJECT_AND_REVERT

Check:

1. Does the fix match design?
2. Does it preserve API contract?
3. Is it minimal?
4. Does it avoid hardcoding public tests?
5. Are tests adequate?
6. Could it break hidden tests?
```

### 16.8 `.opencode/agents/report-writer.md`

```md
---
name: report-writer
description: Agent that writes final competition repair report from issues, rounds, diffs, and test results.
tools:
  write: true
  edit: true
---

You are the Report Writer.

Generate `修复报告.md`.

The report must include:

## 发现的不一致点

| 编号 | 严重程度 | 位置 | 设计描述 | 代码行为 | 类型 | 是否修复 |

## 修复详情

For each fixed issue, explain:

- modified files
- behavior before fix
- behavior after fix
- design basis
- validation method

## 未修复风险说明

List unresolved or risky items.

## 总结

Include:

- 发现总数
- 已修复数量
- 未修复数量
- 仍有风险
- API 契约是否保持不变
- 最终验证命令
- 最终验证结果
```

---

## 17. Goal Runner 主 Prompt

Codex / opencode 执行时使用以下主 Prompt：

```text
你是 ShopHub 比赛 Goal Runner Orchestrator。

你的目标是：自动完成“设计实现一致性检查与修复”比赛任务，直到达到 DONE 条件，然后输出最终修复报告。

项目结构：
- code/：Java Spring Boot 多模块项目
- design-docs/：业务设计文档，最终验收以此为准
- test-cases/：公开黑盒 JUnit 项目
- API基线文档.md：冻结 REST API 契约
- 黑盒用例说明.md：公开黑盒测试运行方式
- 比赛说明.md：比赛规则

最高优先级规则：
1. design-docs/ 是业务真相，不得修改。
2. API基线文档.md 是冻结 REST API 契约，不得修改。
3. 禁止修改 REST API URL、HTTP Method、Request Header、Request Body 字段名和类型、Response Body 字段名和类型。
4. 可以修改 Java 源代码、application.yml、pom.xml、code/ 下的 JUnit 测试。
5. 避免修改 test-cases/。
6. 公开黑盒测试只作为症状反馈，不能为了特定公开测试硬编码。
7. 每个修复都必须有设计依据。
8. 每轮修复必须小步进行，优先修高确定性、高收益、低复杂度问题。
9. 每轮修复后必须测试、API 契约检查、Review、评分。
10. 如果评分不佳，必须重新审视当前任务，而不是继续盲修。
11. 最终必须输出 修复报告.md。

你必须创建并持续维护：
- AGENTS.md
- .opencode/agents/*.md
- .agent-work/state.json
- .agent-work/spec_rules.jsonl
- .agent-work/api_contract.json
- .agent-work/code_map.md
- .agent-work/issues.jsonl
- .agent-work/fix_plan.md
- .agent-work/rounds/
- .agent-work/test-results/
- 修复报告.md

请按以下状态机自动执行：

INIT：
- 创建 .agent-work/ 目录。
- 初始化 state.json。
- 检查 git status。
- 记录 baseline。
- 创建或更新 AGENTS.md。
- 创建或更新 .opencode/agents/*.md。

READ_SPECS：
- 调用 spec-librarian。
- 阅读 design-docs/ 中所有设计文档。
- 输出 spec_rules.jsonl 和 01_spec_index.md。
- 每条规则包含 spec_id、module、source_doc、section、design_rule、expected_behavior、boundary_conditions、related_api_if_any、severity_hint。

READ_API_BASELINE：
- 调用 api-guardian。
- 阅读 API基线文档.md。
- 输出 api_contract.json 和 02_api_contract_index.md。
- 抽取 URL、Method、Header、Request Body、Response Body、错误码。
- 标记所有不可修改 API 契约。

MAP_CODE：
- 调用 code-mapper。
- 阅读 code/ 模块结构。
- 输出 code_map.md。
- 映射 Controller、Service、Repository、DTO、Exception、Config、测试类。

RUN_BASELINE_TESTS：
- 运行：
  mvn -f code/pom.xml test
  mvn -f code/pom.xml install
  mvn -f test-cases/pom.xml test
- 保存日志到 .agent-work/test-results/。
- 调用 test-diagnoser 总结失败症状。
- 注意：测试失败不是需求依据，只是症状。

AUDIT_INCONSISTENCIES：
- 按模块调用 module-auditor。
- 对比 design-docs 规则和代码实现。
- 输出 .agent-work/issues.jsonl。
- 每个 issue 必须包含：
  issue_id、severity、module、design_basis、code_location、design_behavior、actual_behavior、type、api_impact、fix_suggestion、test_suggestion、confidence、estimated_fix_effort、status。
- 没有设计依据的问题不得加入 issues.jsonl。

PRIORITIZE_ISSUES：
- 按以下公式排序：
  priority = severity_weight * confidence * hidden_test_likelihood / fix_complexity_weight
- 优先修：
  1. 订单、库存、支付、金额、状态流转、错误码
  2. 有明确设计依据
  3. 黑盒或隐藏测试可能覆盖
  4. 修复范围小
- 输出 fix_plan.md。

FIX_LOOP：
重复执行，直到 DONE 条件满足：

1. 从 fix_plan.md 选择当前最优 issue。
2. 调用 patch-agent 修复。
3. 一次只修一个 issue 或一组强相关 issue。
4. 禁止顺手重构。
5. 禁止修改冻结 API。
6. 修复后运行最小相关测试。
7. 调用 api-guardian 检查 API 契约。
8. 调用 review-agent 审查 diff。
9. 给本轮打分。
10. 更新 state.json。
11. 如果通过，进入下一 issue。
12. 如果失败，根据失败类型选择继续修当前 issue、回滚本轮、或重新审计。

每轮评分公式：
score = evidence_quality * 0.30
      + task_completion * 0.25
      + api_safety * 0.15
      + test_effectiveness * 0.15
      + token_efficiency * 0.15

评分标准：
- >= 85：接受本轮修复，继续。
- 70-84：接受，但记录风险。
- 60-69：暂停继续修复，重新审视任务边界。
- < 60：废弃本轮修复，回滚并重新分解。
- api_safety < 100：无条件回滚。
- 编译失败：必须先修复编译，不得继续新 issue。

RUN_FULL_TESTS：
在以下情况触发：
- 每完成 3 个 issue。
- 修复影响多个模块。
- 所有高优 issue 修完。
- 准备 DONE 前。

运行：
  mvn -f code/pom.xml test
  mvn -f code/pom.xml install
  mvn -f test-cases/pom.xml test

DONE 条件：
满足任意条件后停止修复并写报告：
1. 高优和中优 issue 均已处理，且全量测试通过。
2. 公开黑盒测试通过，剩余 issue 均为低置信度或高风险。
3. 连续 3 轮没有新增有效修复。
4. 连续 2 轮修复引入回归。
5. 达到 max_rounds = 20。
6. 达到 token 或时间预算。
7. API 契约存在持续破坏风险。

WRITE_REPORT：
- 调用 report-writer。
- 输出 修复报告.md。
- 报告必须包含：
  ## 发现的不一致点
  ## 修复详情
  ## 未修复风险说明
  ## 总结

现在开始执行。不要只给计划，要实际推进。除非遇到阻塞条件，否则持续执行到 DONE。
```

---

## 18. Codex 开发任务拆分

如果要让 Codex 先开发这个流程框架，而不是直接跑比赛，可按以下任务拆分。

### Task 1：初始化项目规则和 Agent 文件

目标：

1. 生成 `AGENTS.md`；
2. 生成 `.opencode/agents/*.md`；
3. 生成 `.agent-work/` 目录结构；
4. 生成初始 `state.json`。

验收：

```bash
ls AGENTS.md
ls .opencode/agents/
ls .agent-work/state.json
```

### Task 2：实现状态机脚本

建议新增：

```text
scripts/shophub_goal_runner.sh
```

或：

```text
scripts/shophub_goal_runner.py
```

脚本职责：

1. 初始化目录；
2. 检查必要文件；
3. 运行测试命令；
4. 保存日志；
5. 更新 `state.json`；
6. 检查停止条件。

### Task 3：实现 API 快照脚本

建议新增：

```text
scripts/api_snapshot.py
```

职责：

1. 扫描 Controller；
2. 扫描 DTO；
3. 输出 API 快照；
4. 比较 API baseline 和 current snapshot。

注意：如果自动解析复杂，第一版可以先做弱校验：

1. 检查 Controller 注解变动；
2. 检查 DTO 字段变动；
3. 检查错误码枚举变动。

### Task 4：实现测试日志汇总脚本

建议新增：

```text
scripts/summarize_test_logs.py
```

职责：

1. 读取 `.agent-work/test-results/*.log`；
2. 提取 failed tests；
3. 提取 compilation errors；
4. 提取 surefire summary；
5. 输出 markdown 摘要。

### Task 5：实现 issue 队列和 round 记录

建议新增：

```text
scripts/issue_queue.py
scripts/round_recorder.py
```

职责：

1. 读取 `issues.jsonl`；
2. 按优先级排序；
3. 选择 next issue；
4. 记录每轮修复结果；
5. 更新 issue status。

---

## 19. 推荐最终提交物

最终提交前应确保存在：

```text
AGENTS.md
.opencode/agents/spec-librarian.md
.opencode/agents/api-guardian.md
.opencode/agents/code-mapper.md
.opencode/agents/test-diagnoser.md
.opencode/agents/module-auditor.md
.opencode/agents/patch-agent.md
.opencode/agents/review-agent.md
.opencode/agents/report-writer.md
.agent-work/spec_rules.jsonl
.agent-work/api_contract.json
.agent-work/code_map.md
.agent-work/issues.jsonl
.agent-work/fix_plan.md
.agent-work/rounds/*.md
.agent-work/test-results/*.log
修复报告.md
```

但比赛最终如果不要求提交 `.agent-work/`，可只提交：

```text
code/
修复报告.md
```

同时保留 `.agent-work/` 作为本地审计证据。

---

## 20. 最终验收标准

### 20.1 功能验收

1. 能读取设计文档；
2. 能读取 API 基线；
3. 能生成代码地图；
4. 能运行测试；
5. 能发现不一致点；
6. 能小步修复；
7. 能检查 API 契约；
8. 能 review diff；
9. 能输出报告。

### 20.2 比赛验收

必须执行：

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

期望：

1. code 基础测试通过；
2. code install 通过；
3. 公开黑盒测试尽可能通过；
4. API 契约不变；
5. 修复报告完整。

---

## 21. 风险与应对

| 风险         | 表现              | 应对                     |
| ---------- | --------------- | ---------------------- |
| Agent 盲目修复 | 大范围改动           | 每轮限制文件数量               |
| API 被破坏    | 黑盒或隐藏测试失败       | api-guardian 每轮检查      |
| 只按公开测试修    | hidden tests 失败 | issue 必须有设计依据          |
| 上下文爆炸      | token 消耗过高      | subagent 分工 + JSONL 索引 |
| 测试循环       | 反复失败            | 连续失败停止并重审              |
| 误改文档       | 违反规则            | 文件黑名单 + git diff 检查    |
| 编译失败       | 无法继续验证          | 编译失败优先修复               |
| 报告缺证据      | 评分损失            | round 记录强制包含设计依据       |

---

## 22. 最小可用版本

如果时间有限，优先实现最小版本：

1. `AGENTS.md`；
2. `.opencode/agents/` 下 8 个 agent 文件；
3. `.agent-work/state.json`；
4. `spec_rules.jsonl`；
5. `api_contract.json`；
6. `code_map.md`；
7. `issues.jsonl`；
8. `fix_plan.md`；
9. 小步修复循环；
10. `修复报告.md`。

脚本可以后补，但规则文件和状态文件必须先有。

---

## 23. 一句话总结

本项目要实现的不是单次代码修复，而是一个面向比赛的自动化 Goal Runner：

```text
设计文档索引 → API 契约守卫 → 代码地图 → 测试诊断 → 模块审计 → 优先级队列 → 小步修复 → API 检查 → 测试 → Review → 评分 → 持续循环 → 最终报告
```

核心竞争力是：

1. 设计依据强；
2. API 不破坏；
3. 修复小步可控；
4. 测试持续验证；
5. 失败可回滚；
6. 报告证据完整。
