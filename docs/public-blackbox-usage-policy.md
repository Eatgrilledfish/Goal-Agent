# 公开黑盒测试使用策略：参考但不依赖

> 结论：公开黑盒测试仍然要跑，但在 `competition-final` 模式下只能作为 smoke/canary 和诊断参考，不能作为唯一 oracle，不能直接驱动针对性修复，更不能替代 design-docs 与 README 冻结 REST API 契约。

---

## 1. 正确理解

公开黑盒不是完全不用；相反，公开黑盒仍然有价值：

```text
会跑公开黑盒：
- 用来确认最终工程没有明显集成失败。
- 用来暴露公开 API 行为兼容问题。
- 用来作为 smoke/canary 检查修复是否破坏已有公开场景。
- 用来辅助定位可能的设计实现不一致。

但不依赖公开黑盒：
- 不把公开测试失败直接当成业务需求。
- 不把公开测试里的 fixture literal 当成修复目标。
- 不因为某个公开断言失败就写针对性 if/else 或硬编码。
- 不把 public matrix all-green 当成 hidden full-score 的充分条件。
```

一句话：

```text
公开黑盒可以提示“哪里可能有问题”，但真正决定“该怎么修”的必须是 design-docs 和 README 冻结 REST API 契约。
```

---

## 2. 新旧策略对比

### 2.1 不推荐的旧策略

```text
运行公开 test-cases
  → 看到失败类/失败断言/失败 fixture
  → 直接针对这个失败修改代码
  → 再跑公开 test-cases
  → 循环直到公开用例通过
```

问题：

```text
- 最终隐藏黑盒没有二次修复机会。
- 公开用例只覆盖部分场景。
- 针对公开 fixture 修复容易过拟合。
- 公开测试并不是设计文档的完整表达。
```

### 2.2 推荐的新策略

```text
读取 README.md + design-docs/**
  → 构建 Spec IR / API contract / business rules
  → 扫描 code/** 并建立 trace matrix
  → 静态/语义 checker + generated spec tests 找出 spec-backed gaps
  → 公开黑盒失败只作为辅助 symptom
  → 每个修复必须回溯到 spec_id 或 frozen API contract
  → 修复 code/**
  → generated spec tests + checker + public smoke + final gate
```

核心区别：

```text
旧策略：test failure decides the fix.
新策略：spec decides the fix; test failure only hints where to look.
```

---

## 3. 公开黑盒在 pipeline 中的位置

### 3.1 可以运行的位置

公开黑盒可以在两个阶段运行：

```text
1. Baseline diagnostic / optional
   - 只记录公开症状。
   - 不直接生成 P0/P1 requirement。
   - 不直接生成 P0/P1 repair task。

2. Final public smoke / required
   - 在 spec gates、build gates、guards 基本通过后运行。
   - 用来确认公开可观测行为没有被破坏。
   - 失败时必须先映射回 design/API，再决定是否修复。
```

### 3.2 不能做的事情

在 `competition-final` 模式下，公开黑盒结果不能：

```text
- 直接写入 business_rules.json。
- 直接写入 feature_list.json 的 P0/P1 必过项。
- 直接写入 repair_tasks.jsonl。
- 单独构成 issue 的 design_basis。
- 覆盖 design-docs 或 README 的语义。
- 导致 hardcoded public fixture patch。
```

---

## 4. 公开黑盒失败后的处理流程

当公开黑盒失败时，不是“不修”，而是按下面流程处理：

```text
public failure
  → extract symptom: endpoint / test name / error message / observed response
  → map to README frozen API endpoint or design-doc requirement
  → if mapping found:
       create or update spec-backed issue/task
       include spec_id, design_source, expected_behavior, actual_behavior, code path, verifier
       repair according to design/API
    else:
       record public diagnostic risk
       do not hardcode fixture
       do not fabricate design requirement
```

判断标准：

```text
可以修：
- 公开失败能映射到 README API 契约，例如字段名、状态码、错误码、响应 schema。
- 公开失败能映射到 design-docs 业务规则，例如订单状态机、金额公式、库存规则、用户状态限制。
- 公开失败暴露了真实 build/config/transaction/validation 问题。

不能直接修：
- 失败只依赖某个公开测试专用 userId/orderId/productName。
- 失败要求的行为在 README/design-docs 中找不到依据。
- 修复方式需要修改 test-cases/** 或改变冻结 API。
- 修复方式只是为了让某个断言过，而不是实现设计语义。
```

---

## 5. Repair task 接受规则

公开黑盒失败只有在完成“设计映射”后，才能进入修复队列。

P0/P1 task 必须包含：

```json
{
  "spec_id": "REQ-... or API-...",
  "design_source": "README.md or design-docs/<file>.md:<line>",
  "expected_behavior": "由设计/API 得出的期望行为",
  "public_symptom": "公开黑盒观察到的失败，仅作为 symptom",
  "actual_behavior": "代码中确认的真实不一致行为",
  "code_location": "code/.../Class.java#method",
  "verification": [
    "checker:<checker_id>",
    "generated:<spec_test>",
    "public-smoke:<test-class>#<method>"
  ]
}
```

不合格 task：

```json
{
  "design_basis": "public black-box test failed",
  "actual_behavior": "test failed",
  "code_location": "code/#deterministic-check"
}
```

---

## 6. final gate 中的公开黑盒地位

公开黑盒在 final gate 中应叫：

```text
public_smoke
```

而不是：

```text
hidden_test_proxy
primary_oracle
business_truth
```

DONE 判断不能只看 public smoke。正确 DONE 必须同时满足：

```text
- spec_ir valid
- trace_coverage pass
- static_semantic_consistency pass
- generated_spec_tests pass
- code_tests pass
- code_install pass
- public_smoke pass
- forbidden_change_guard pass
- hardcoding_guard pass
- stability pass
- repair_report present
```

如果 public smoke 失败，但 spec gates 全绿，需要输出：

```text
- 失败 test 名称
- observed symptom
- 是否能映射到 spec_id/API contract
- 若能映射，生成 spec-backed repair task
- 若不能映射，记录为 public diagnostic risk，不硬编码
```

---

## 7. 建议写进 Goal 的一句话

```text
公开 test-cases/** 会运行，但只作为 smoke/canary 与诊断参考；任何公开失败都必须先回溯到 design-docs/** 或 README 冻结 REST API 契约，才能生成 P0/P1 修复任务。修复目标是实现设计/API 语义，而不是针对公开黑盒结果做定制化修改。
```

---

## 8. 实现含义

这意味着项目实现上应调整为：

```text
public_case_rule_builder.py
  competition-final: diagnostic-only

blackbox_explorer.py
  competition-final: smoke/report-only

feature_registry.py
  P0/P1 feature pass/fail 不由 public matrix 单独决定

rule_issue_builder.py
  public failure 不能单独生成 P0/P1 issue

repair_task_builder.py
  public symptom 只能附着到 spec-backed task

final_goal_gate.py
  public_smoke 是必要 smoke gate，但不是唯一 DONE gate
```

---

## 9. 最终结论

公开黑盒仍然要跑，而且应该跑；但它的角色从“老师”降级为“报警器”。

```text
老师：design-docs + README frozen API contract
验证器：generated spec tests + deterministic checkers
报警器：public black-box smoke
裁判：hidden black-box final score
```

因此，比赛满分导向的正确策略是：

```text
参考公开黑盒，但不依赖公开黑盒；
利用公开黑盒定位症状，但只根据设计/API 语义修复；
让隐藏黑盒面对的是一个已被规格充分约束和验证的实现。
```