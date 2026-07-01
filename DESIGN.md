# Goal-agent 设计实现一致性检查与修复能力重构文档

## 1. 背景

当前 Goal-agent 项目用于参加“设计实现一致性检查与修复”类比赛，目标是让 AI Agent 自动阅读设计文档、API 基线文档和 Java Spring Boot 代码，识别设计与实现之间的不一致点，并自动修复代码，使项目在黑箱测试中获得尽可能高的通过率和稳定率。

当前比赛结果为：

```text
总 case 数：72
通过 case：54
失败 case：18
按 case 计算通过率：75%
用户反馈综合通过率/稳定率约：70%
```

这说明当前 Goal-agent 已具备基础可用能力，但仍存在系统性短板：

1. 对设计文档和 API 基线的结构化理解不足。
2. 缺少设计要求到代码实现的 traceability 矩阵。
3. 缺少 contract-first 的 API 一致性校验。
4. 缺少隐藏测试模拟器和规格驱动测试生成。
5. 缺少多候选补丁生成与补丁评分机制。
6. 缺少稳定性重跑和 flaky 问题识别。
7. 缺少失败归因闭环，导致失败 case 无法沉淀成下一轮修复策略。
8. Agent 线性修复，容易在失败补丁上继续叠加修改，导致代码越来越乱。

本次重构不考虑兼容旧版本，不保留旧流程，不要求继承已有模块接口，可以直接重建 Goal-agent 的执行架构。

---

## 2. 重构目标

### 2.1 总目标

将 Goal-agent 从“LLM 驱动的代码修复 Agent”升级为“规格驱动的一致性检查与自动修复系统”。

新的核心流程为：

```text
文档结构化
  -> API/业务规则抽取
  -> 代码结构扫描
  -> 设计-代码 traceability 建模
  -> 自动生成一致性测试
  -> 定位不一致点
  -> 生成多个候选补丁
  -> 独立沙箱验证
  -> 补丁评分与选择
  -> 稳定性重跑
  -> 输出修复报告和交付记录
```

### 2.2 比赛目标

在黑箱测试中，将当前通过率从约 70%~75% 提升到：

```text
目标通过率：85%+
理想通过率：90%+
稳定通过率：85%+
```

### 2.3 工程目标

新的 Goal-agent 必须具备以下能力：

1. 自动读取 `design-docs/` 中所有设计文档。
2. 自动读取 `API基线文档.md`。
3. 自动扫描 `code/` 中 Java Spring Boot 项目。
4. 自动构建 API contract。
5. 自动构建业务规则 contract。
6. 自动构建 Controller / DTO / Service / Repository / Entity / ExceptionHandler 映射。
7. 自动识别实现缺失、实现冲突、实现不完整。
8. 自动生成 MockMvc / JUnit 测试。
9. 自动生成多个候选修复补丁。
10. 自动验证补丁。
11. 自动选择最小且最稳补丁。
12. 自动输出比赛所需日志、报告和修复证据。

---

## 3. 非目标

本次重构不做以下事情：

1. 不考虑旧版 Goal-agent 的接口兼容。
2. 不保留旧版 Agent 调度逻辑。
3. 不保留旧版 prompt 模板。
4. 不为了兼容旧数据结构增加适配层。
5. 不做通用软件工程平台，只服务本次“设计实现一致性检查与修复”比赛。
6. 不优先追求复杂多 Agent 编排，优先追求黑箱通过率和稳定率。
7. 不允许为了通过测试修改设计文档、API 基线文档或测试代码。
8. 不允许硬编码公开测试 case。
9. 不允许删除测试、跳过测试或修改测试框架。
10. 不允许通过吞异常、统一返回 200、放宽所有校验等方式伪修复。

---

## 4. 项目目录设计

重构后目录建议如下：

```text
Goal-agent/
├── INSTRUCTION.md
├── work/
│   ├── goal_agent/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── orchestrator/
│   │   │   ├── pipeline.py
│   │   │   ├── run_context.py
│   │   │   └── patch_selector.py
│   │   ├── parsers/
│   │   │   ├── api_baseline_parser.py
│   │   │   ├── design_doc_parser.py
│   │   │   └── markdown_utils.py
│   │   ├── analyzers/
│   │   │   ├── spring_route_analyzer.py
│   │   │   ├── dto_analyzer.py
│   │   │   ├── service_analyzer.py
│   │   │   ├── repository_analyzer.py
│   │   │   ├── entity_analyzer.py
│   │   │   ├── exception_analyzer.py
│   │   │   └── static_consistency_analyzer.py
│   │   ├── contracts/
│   │   │   ├── api_contract_builder.py
│   │   │   ├── business_rule_builder.py
│   │   │   ├── contract_checker.py
│   │   │   └── schema_validator.py
│   │   ├── traceability/
│   │   │   ├── trace_matrix_builder.py
│   │   │   ├── requirement_linker.py
│   │   │   └── evidence_collector.py
│   │   ├── testsynth/
│   │   │   ├── spec_test_generator.py
│   │   │   ├── mockmvc_generator.py
│   │   │   ├── boundary_case_generator.py
│   │   │   └── hidden_case_simulator.py
│   │   ├── repair/
│   │   │   ├── issue_localizer.py
│   │   │   ├── patch_planner.py
│   │   │   ├── patch_generator.py
│   │   │   ├── patch_applier.py
│   │   │   └── repair_strategies/
│   │   │       ├── validation_strategy.py
│   │   │       ├── api_schema_strategy.py
│   │   │       ├── exception_strategy.py
│   │   │       ├── pagination_strategy.py
│   │   │       ├── repository_query_strategy.py
│   │   │       └── business_rule_strategy.py
│   │   ├── validation/
│   │   │   ├── test_runner.py
│   │   │   ├── stability_runner.py
│   │   │   ├── forbidden_change_guard.py
│   │   │   ├── diff_guard.py
│   │   │   └── scoring.py
│   │   ├── llm/
│   │   │   ├── client.py
│   │   │   ├── prompts.py
│   │   │   ├── structured_output.py
│   │   │   └── retry_policy.py
│   │   └── utils/
│   │       ├── shell.py
│   │       ├── git.py
│   │       ├── jsonl.py
│   │       ├── fs.py
│   │       └── logger.py
│   └── skill/
│       └── SKILL.md
├── result/
│   ├── output.md
│   ├── contract_report.md
│   ├── repair_report.md
│   ├── stability_report.md
│   └── screenshot/
├── logs/
│   ├── interaction.md
│   ├── trace/
│   │   ├── pipeline_trace.jsonl
│   │   ├── extracted_api_contract.json
│   │   ├── extracted_business_rules.json
│   │   ├── repo_map.json
│   │   ├── trace_matrix.json
│   │   ├── generated_tests.json
│   │   ├── candidate_patches.jsonl
│   │   ├── validation_results.jsonl
│   │   └── failure_taxonomy.jsonl
│   └── prompts/
└── README.md
```

---

## 5. 新核心流程

### 5.1 总体 Pipeline

```text
Step 1: 初始化运行环境
Step 2: 读取 API 基线文档
Step 3: 读取设计文档
Step 4: 构建 API Contract
Step 5: 构建 Business Rule Contract
Step 6: 扫描 Spring Boot 代码结构
Step 7: 构建 Repo Map
Step 8: 构建 Requirement-Code Trace Matrix
Step 9: 执行静态一致性检查
Step 10: 生成规格驱动测试
Step 11: 执行 baseline 测试
Step 12: 定位失败和不一致点
Step 13: 生成候选补丁
Step 14: 独立沙箱验证候选补丁
Step 15: 选择最优补丁
Step 16: 稳定性重跑
Step 17: 输出报告和交付产物
```

### 5.2 伪代码

```python
def run_goal_agent(project_root: str):
    ctx = RunContext(project_root)

    init_workspace(ctx)

    api_contract = build_api_contract(ctx)
    business_rules = build_business_rules(ctx)

    repo_map = build_repo_map(ctx)

    trace_matrix = build_trace_matrix(
        api_contract=api_contract,
        business_rules=business_rules,
        repo_map=repo_map,
    )

    consistency_issues = run_static_consistency_checks(
        api_contract=api_contract,
        business_rules=business_rules,
        repo_map=repo_map,
        trace_matrix=trace_matrix,
    )

    generated_tests = generate_spec_tests(
        api_contract=api_contract,
        business_rules=business_rules,
        trace_matrix=trace_matrix,
    )

    baseline_result = run_baseline_tests(ctx)

    repair_tasks = localize_repair_tasks(
        consistency_issues=consistency_issues,
        baseline_result=baseline_result,
        generated_tests=generated_tests,
        trace_matrix=trace_matrix,
    )

    all_candidates = []

    for task in repair_tasks:
        candidates = generate_patch_candidates(task, ctx, count=5)
        validated_candidates = validate_candidates(candidates, ctx)
        best_candidate = select_best_candidate(validated_candidates)
        apply_best_candidate(best_candidate, ctx)
        all_candidates.append(best_candidate)

    final_result = run_stability_gate(ctx)

    write_reports(
        ctx=ctx,
        api_contract=api_contract,
        business_rules=business_rules,
        repo_map=repo_map,
        trace_matrix=trace_matrix,
        candidates=all_candidates,
        final_result=final_result,
    )

    return final_result
```

---

## 6. API Contract Compiler

### 6.1 目标

从 `API基线文档.md` 中抽取冻结 REST API 契约，生成机器可读的 `api_contract.json`。

### 6.2 需要抽取的信息

每个接口需要抽取：

```text
接口名称
HTTP Method
Path
Path Variable
Query Param
Request Body
Request Field
字段类型
字段必填性
字段边界
Response Status
Response Body
Response Field
Error Status
Error Body
业务错误码
分页结构
排序结构
```

### 6.3 输出结构

```json
{
  "endpoints": [
    {
      "id": "API-001",
      "method": "POST",
      "path": "/api/products",
      "summary": "创建商品",
      "request": {
        "content_type": "application/json",
        "body": {
          "name": {
            "type": "string",
            "required": true,
            "constraints": ["not_blank"]
          },
          "price": {
            "type": "decimal",
            "required": true,
            "constraints": ["min:0.01"]
          },
          "categoryId": {
            "type": "long",
            "required": true,
            "constraints": ["exists:category"]
          }
        }
      },
      "response": {
        "success_status": [200, 201],
        "body": {
          "code": "integer",
          "message": "string",
          "data.id": "long",
          "data.name": "string",
          "data.price": "decimal"
        }
      },
      "errors": [
        {
          "condition": "invalid request",
          "status": 400,
          "body": {
            "code": "VALIDATION_ERROR",
            "message": "string"
          }
        },
        {
          "condition": "category not found",
          "status": 404,
          "body": {
            "code": "NOT_FOUND",
            "message": "string"
          }
        }
      ]
    }
  ]
}
```

### 6.4 实现方式

优先采用规则解析 + LLM 结构化抽取结合：

1. 使用正则和 Markdown 解析器识别标题、接口段落、表格。
2. 先用 deterministic parser 提取明显结构。
3. 对非结构化描述使用 LLM 转换成 JSON。
4. 对 LLM 输出做 schema 校验。
5. 对缺失项标记为 `unknown`，不得随意猜测。
6. 如果 API 基线和设计文档冲突，以 API 基线为准。

### 6.5 关键规则

```text
API 基线文档优先级最高。
不得修改 API 基线文档。
不得修改接口路径。
不得修改 HTTP Method。
不得修改字段名。
不得修改成功响应结构。
不得修改错误响应结构。
不得为了适配代码而反向修改 contract。
```

---

## 7. Business Rule Compiler

### 7.1 目标

从 `design-docs/` 中抽取业务规则，生成 `business_rules.json`。

### 7.2 业务规则类型

需要重点抽取：

```text
字段校验规则
状态流转规则
创建规则
更新规则
删除规则
查询规则
权限规则
唯一性规则
库存/金额/数量规则
分页规则
排序规则
异常规则
默认值规则
空值处理规则
```

### 7.3 输出结构

```json
{
  "rules": [
    {
      "id": "REQ-001",
      "source_file": "design-docs/product.md",
      "source_section": "商品创建",
      "type": "validation",
      "target_domain": "Product",
      "description": "创建商品时 price 必须大于 0",
      "condition": "create product",
      "expected_behavior": "price <= 0 should be rejected",
      "expected_status": 400,
      "related_api": ["POST /api/products"],
      "priority": "P0",
      "test_cases": [
        {
          "name": "create_product_price_zero_should_fail",
          "input": {"price": 0},
          "expected": {"status": 400}
        },
        {
          "name": "create_product_price_negative_should_fail",
          "input": {"price": -1},
          "expected": {"status": 400}
        }
      ]
    }
  ]
}
```

### 7.4 优先级规则

```text
P0：会直接影响隐藏测试通过的 API 契约、字段校验、错误码、状态码、核心业务规则。
P1：分页、排序、默认值、边界条件、空值行为。
P2：文案、日志、内部实现风格、非核心字段。
```

修复时优先修 P0，再修 P1，最后修 P2。

---

## 8. Spring Boot Repo Map

### 8.1 目标

扫描 `code/` 目录，生成 Spring Boot 项目的结构化代码地图 `repo_map.json`。

### 8.2 需要扫描的对象

```text
Controller
DTO
Request 类
Response 类
VO 类
Service
ServiceImpl
Repository
Mapper
Entity
Enum
Exception
GlobalExceptionHandler
Configuration
Test
```

### 8.3 Controller Map

抽取：

```text
类名
文件路径
@RequestMapping
@GetMapping
@PostMapping
@PutMapping
@DeleteMapping
参数
@RequestBody
@PathVariable
@RequestParam
@Valid
返回类型
调用的 Service 方法
```

示例：

```json
{
  "controllers": [
    {
      "class_name": "ProductController",
      "file": "src/main/java/.../ProductController.java",
      "base_path": "/api/products",
      "methods": [
        {
          "method_name": "createProduct",
          "http_method": "POST",
          "path": "",
          "full_path": "/api/products",
          "request_body": "ProductCreateRequest",
          "validated": true,
          "response_type": "ApiResponse<ProductResponse>",
          "service_calls": ["productService.createProduct"]
        }
      ]
    }
  ]
}
```

### 8.4 DTO Map

抽取：

```text
字段名
字段类型
Bean Validation 注解
JSON 注解
默认值
是否必填
是否允许 blank
是否允许 null
```

示例：

```json
{
  "dtos": [
    {
      "class_name": "ProductCreateRequest",
      "file": "src/main/java/.../ProductCreateRequest.java",
      "fields": [
        {
          "name": "name",
          "type": "String",
          "annotations": ["@NotBlank"],
          "json_name": "name"
        },
        {
          "name": "price",
          "type": "BigDecimal",
          "annotations": ["@NotNull", "@DecimalMin(\"0.01\")"],
          "json_name": "price"
        }
      ]
    }
  ]
}
```

### 8.5 Exception Map

抽取：

```text
@ControllerAdvice
@ExceptionHandler
异常类型
HTTP Status
错误响应 body
错误码
错误消息
```

### 8.6 Repository Map

抽取：

```text
Repository 接口
继承类型
自定义查询方法
@Query
方法名推断条件
是否包含 status/deleted/userId 等过滤条件
```

---

## 9. Trace Matrix

### 9.1 目标

构建 `trace_matrix.json`，把设计规则和 API 契约映射到具体代码位置。

### 9.2 映射关系

每条规则要映射到：

```text
API endpoint
Controller method
Request DTO
Response DTO
Service method
Repository method
Entity field
Exception handler
Test case
```

### 9.3 输出结构

```json
{
  "trace_items": [
    {
      "requirement_id": "REQ-001",
      "api_id": "API-001",
      "description": "创建商品时 price 必须大于 0",
      "links": {
        "controller": {
          "file": "ProductController.java",
          "symbol": "createProduct",
          "confidence": 0.95
        },
        "request_dto": {
          "file": "ProductCreateRequest.java",
          "symbol": "price",
          "confidence": 0.95
        },
        "service": {
          "file": "ProductServiceImpl.java",
          "symbol": "createProduct",
          "confidence": 0.80
        },
        "exception_handler": {
          "file": "GlobalExceptionHandler.java",
          "symbol": "handleMethodArgumentNotValidException",
          "confidence": 0.75
        }
      },
      "implementation_status": "partial",
      "gap": "price 字段缺少 @DecimalMin(\"0.01\")",
      "repair_priority": "P0"
    }
  ]
}
```

### 9.4 状态枚举

```text
implemented：实现完整
partial：部分实现
missing：完全缺失
conflict：实现与设计冲突
unknown：无法判断
```

---

## 10. Static Consistency Checker

### 10.1 目标

在不运行测试的情况下，先发现明显设计实现不一致问题。

### 10.2 检查项

#### 10.2.1 API 路由检查

```text
API 基线中存在的接口，Controller 中是否存在。
HTTP Method 是否一致。
Path 是否一致。
PathVariable 名称是否一致。
RequestParam 名称是否一致。
```

#### 10.2.2 Request Body 检查

```text
字段是否存在。
字段类型是否一致。
字段是否必填。
字段是否有对应 Bean Validation。
字段 JSON 名称是否一致。
```

#### 10.2.3 Response Body 检查

```text
是否使用统一响应包装。
data 字段结构是否一致。
列表返回是否为数组。
分页返回字段是否一致。
空列表是否返回 [] 而不是 null。
```

#### 10.2.4 Error Handling 检查

```text
校验失败是否返回 400。
资源不存在是否返回 404。
冲突是否返回 409。
权限问题是否返回 403。
未知异常是否避免直接暴露 500 堆栈。
错误响应格式是否符合 API 基线。
```

#### 10.2.5 Business Rule 检查

```text
状态流转是否完整。
删除后是否不可查询或状态正确。
重复创建是否处理。
金额/数量/库存是否做边界检查。
Repository 查询是否过滤 deleted/status/userId。
```

---

## 11. Spec-to-Test Generator

### 11.1 目标

根据 API contract 和 business rules 自动生成测试，用来模拟隐藏测试。

### 11.2 测试类型

```text
Positive Test：正常输入，应成功。
Negative Test：非法输入，应失败。
Boundary Test：边界值测试。
Schema Test：响应结构测试。
Error Test：异常格式测试。
State Transition Test：状态流转测试。
Pagination Test：分页测试。
Sorting Test：排序稳定性测试。
Null/Blank Test：空值测试。
```

### 11.3 生成位置

生成测试文件到：

```text
code/src/test/java/generated/
```

或者：

```text
.tmp/generated-tests/
```

推荐默认放在 `.tmp/generated-tests/`，验证时临时复制到 `src/test/java/generated/`，避免污染最终提交。

### 11.4 MockMvc 测试模板

```java
@Test
void createProduct_priceZero_shouldReturn400() throws Exception {
    String body = """
        {
          "name": "test product",
          "price": 0,
          "categoryId": 1
        }
        """;

    mockMvc.perform(post("/api/products")
            .contentType(MediaType.APPLICATION_JSON)
            .content(body))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").exists())
        .andExpect(jsonPath("$.message").exists());
}
```

### 11.5 高优先级隐藏测试模拟项

必须生成以下通用测试：

```text
字段缺失
字段为 null
字段为空字符串
字段为纯空格字符串
数字为 0
数字为 -1
金额为 0.00
金额为 -0.01
ID 不存在
ID 类型非法
重复创建
删除后查询
删除后再次删除
更新不存在资源
非法 enum
非法状态流转
分页 page = 0
分页 size = 0
分页 size 超上限
排序字段非法
列表为空
响应字段缺失
错误响应格式
```

---

## 12. Repair Task Localizer

### 12.1 目标

将一致性问题转换成可执行修复任务。

### 12.2 输入

```text
api_contract.json
business_rules.json
repo_map.json
trace_matrix.json
static_consistency_report.json
baseline_test_result
generated_test_result
```

### 12.3 输出

```json
{
  "repair_tasks": [
    {
      "id": "TASK-001",
      "type": "validation",
      "priority": "P0",
      "requirement_id": "REQ-001",
      "api_id": "API-001",
      "symptom": "price <= 0 没有返回 400",
      "suspected_files": [
        "ProductCreateRequest.java",
        "ProductController.java",
        "GlobalExceptionHandler.java"
      ],
      "expected_fix": "在 price 字段增加 @DecimalMin(\"0.01\")，并确保 MethodArgumentNotValidException 返回 400",
      "verification_tests": [
        "createProduct_priceZero_shouldReturn400",
        "createProduct_priceNegative_shouldReturn400"
      ]
    }
  ]
}
```

### 12.4 任务分类

```text
validation
api_schema
response_schema
error_handling
business_rule
repository_query
pagination
sorting
state_transition
null_handling
flaky
regression
```

---

## 13. Patch Generator

### 13.1 目标

每个 repair task 不只生成一个补丁，而是生成 3~5 个候选补丁。

### 13.2 候选补丁原则

```text
candidate-1：最小 DTO/annotation 修复
candidate-2：Service 层显式校验修复
candidate-3：Controller + ExceptionHandler 修复
candidate-4：Repository/query 逻辑修复
candidate-5：综合修复，但 diff 不得过大
```

### 13.3 生成要求

每个候选补丁必须输出：

```text
修改文件
修改原因
diff
影响范围
验证方式
可能风险
```

### 13.4 Prompt 要求

LLM 生成补丁时必须遵守：

```text
只修改 src/main/java。
除非必要，不修改 pom.xml。
不得修改 src/test/java。
不得修改 design-docs。
不得修改 API基线文档.md。
不得硬编码测试数据。
不得删除已有逻辑。
优先最小改动。
优先符合 API 基线。
优先符合设计文档。
```

---

## 14. Candidate Patch Sandbox

### 14.1 目标

每个候选补丁都在独立 workspace 中验证，避免失败补丁污染主工作区。

### 14.2 工作流

```text
1. 从 baseline 创建 workspace。
2. 应用 candidate patch。
3. 运行编译。
4. 运行公开测试。
5. 运行 generated tests。
6. 运行 contract checker。
7. 运行 forbidden-change guard。
8. 记录结果。
9. 删除失败 workspace。
10. 最优 patch 合并回主 workspace。
```

### 14.3 目录示例

```text
.tmp/candidates/
├── TASK-001/
│   ├── candidate-1/
│   ├── candidate-2/
│   ├── candidate-3/
│   ├── candidate-4/
│   └── candidate-5/
```

---

## 15. Patch Scoring

### 15.1 评分公式

```text
总分 = 
  40% 公开测试通过率
+ 25% 生成测试通过率
+ 15% API contract checker 通过率
+ 10% diff 最小化
+ 10% 稳定性
```

### 15.2 直接淘汰条件

候选补丁如果出现以下情况，直接淘汰：

```text
修改 API基线文档.md
修改 design-docs/**
修改测试代码
删除核心业务逻辑
跳过校验
吞异常
统一返回 200
硬编码公开测试数据
导致项目无法编译
引入明显安全风险
```

### 15.3 diff 最小化规则

```text
修改文件越少越好。
修改行数越少越好。
优先修改 DTO validation。
其次修改 Service 业务规则。
再次修改 ExceptionHandler。
最后才修改 Controller 路由。
不允许无意义重构。
不允许格式化整个文件。
```

---

## 16. Forbidden Change Guard

### 16.1 目标

用 deterministic 代码规则拦截非法修改，不依赖 LLM 判断。

### 16.2 检查项

```bash
git diff --name-only
git diff -- API基线文档.md
git diff -- design-docs/
git diff -- src/test/
grep -R "TODO\|HACK\|hardcode\|test" src/main/java
grep -R "return ResponseEntity.ok" src/main/java
```

### 16.3 禁止修改文件

```text
API基线文档.md
design-docs/**
src/test/**
pom.xml，除非明确需要并经过白名单
README.md，除非报告需要
```

### 16.4 禁止代码模式

```text
if (name.equals("公开测试中的固定值"))
if (id == 1)
catch (Exception e) { return success; }
return 200 for all errors
注释掉核心校验
删除 @Valid
删除 @Transactional
删除 Repository 查询条件
```

---

## 17. Stability Gate

### 17.1 目标

解决当前“本地过、黑箱不稳”的问题。

### 17.2 最终补丁验收规则

最终合并后的代码必须通过：

```text
mvn clean test：连续 3 次通过
generated tests：连续 3 次通过
contract checker：1 次完整通过
forbidden-change guard：1 次完整通过
随机测试顺序：至少 1 次通过
```

### 17.3 flaky 问题识别

如果测试结果不稳定，自动归类：

```text
数据库状态污染
测试顺序依赖
静态变量残留
时间 now() 不稳定
排序不稳定
分页无默认排序
HashMap/HashSet 顺序不稳定
事务未隔离
并发/异步未等待
```

### 17.4 修复策略

```text
查询列表统一增加稳定 order by。
分页接口必须显式排序。
测试数据使用唯一值。
Service 不依赖静态全局状态。
涉及时间逻辑时集中封装 Clock。
避免异步未完成就返回。
Repository 查询必须带状态过滤。
```

---

## 18. Failure Taxonomy

### 18.1 目标

将 18 个失败 case 从“未知失败”变成可归因、可复用的修复知识。

### 18.2 分类

```text
A. API 契约不一致
B. DTO validation 缺失
C. 错误状态码不一致
D. 错误响应体不一致
E. 业务状态流转缺失
F. Repository 查询条件错误
G. 删除/状态过滤错误
H. 分页边界错误
I. 排序不稳定
J. null/blank/empty 边界错误
K. 金额/数量边界错误
L. enum 转换错误
M. 事务或持久化错误
N. 跨文件联动修复不完整
O. 回归问题
P. flaky 问题
```

### 18.3 输出格式

```json
{
  "case_id": "FAILED-INFERRED-001",
  "category": "DTO validation 缺失",
  "symptom": "price 为 0 时没有返回 400",
  "related_requirement": "REQ-001",
  "related_api": "POST /api/products",
  "suspected_files": [
    "ProductCreateRequest.java",
    "GlobalExceptionHandler.java"
  ],
  "root_cause": "DTO 缺少 @DecimalMin(\"0.01\")",
  "repair_strategy": "validation_strategy",
  "status": "fixed"
}
```

---

## 19. Repair Strategies

### 19.1 Validation Strategy

适用问题：

```text
字段缺失校验
null 未拦截
blank 未拦截
数字边界错误
金额边界错误
enum 非法值错误
```

优先修复位置：

```text
Request DTO
Controller @Valid
GlobalExceptionHandler
Service 层兜底校验
```

常用修复：

```java
@NotNull
@NotBlank
@Size(min = 1, max = 100)
@Min(1)
@DecimalMin(value = "0.01")
@Pattern(regexp = "...")
@Valid
```

### 19.2 API Schema Strategy

适用问题：

```text
字段名不一致
响应包装不一致
data 缺失
code/message 缺失
列表返回 null
分页字段不一致
```

优先修复位置：

```text
Response DTO
ApiResponse
Controller return
Mapper/Converter
```

### 19.3 Exception Strategy

适用问题：

```text
校验失败返回 500
资源不存在返回 500
重复创建返回 500
错误码不符合 API 基线
错误响应体格式不一致
```

优先修复位置：

```text
GlobalExceptionHandler
Custom Exception
Service throw exception
```

推荐结构：

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(MethodArgumentNotValidException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public ApiResponse<Void> handleValidation(MethodArgumentNotValidException e) {
        return ApiResponse.error("VALIDATION_ERROR", extractMessage(e));
    }

    @ExceptionHandler(ResourceNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public ApiResponse<Void> handleNotFound(ResourceNotFoundException e) {
        return ApiResponse.error("NOT_FOUND", e.getMessage());
    }

    @ExceptionHandler(ConflictException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public ApiResponse<Void> handleConflict(ConflictException e) {
        return ApiResponse.error("CONFLICT", e.getMessage());
    }
}
```

### 19.4 Pagination Strategy

适用问题：

```text
page 从 0 还是 1 开始不一致
size 为 0 未拦截
size 超限未拦截
分页返回结构不一致
排序不稳定
```

修复原则：

```text
以 API 基线为准。
统一 page 起始值。
size 设置最大值。
默认排序必须显式。
空列表返回 []。
分页 metadata 字段必须完整。
```

### 19.5 Repository Query Strategy

适用问题：

```text
查询返回已删除数据
查询未过滤状态
查询未过滤 userId
重复判断错误
大小写匹配错误
```

修复原则：

```text
所有查询必须符合业务可见性。
软删除字段必须过滤。
状态字段必须过滤。
用户维度数据必须过滤 userId/ownerId。
列表查询必须稳定排序。
```

### 19.6 Business Rule Strategy

适用问题：

```text
状态流转非法
重复提交
库存不足
金额非法
删除后可更新
不存在资源仍更新成功
```

修复原则：

```text
Service 层实现业务规则。
DTO 层只做字段基础校验。
Controller 不写复杂业务逻辑。
Repository 只做查询。
异常由 GlobalExceptionHandler 统一转换。
```

---

## 20. LLM 使用策略

### 20.1 LLM 不负责的事情

以下事情必须由程序 deterministic 完成：

```text
文件扫描
路由提取
DTO 字段提取
测试执行
diff 检查
forbidden-change guard
评分排序
稳定性重跑
报告落盘
```

### 20.2 LLM 负责的事情

```text
非结构化设计文档规则抽取
需求到代码的语义映射
复杂业务规则理解
补丁方案生成
失败原因解释
报告文字生成
```

### 20.3 LLM 输出必须结构化

所有 LLM 输出必须是 JSON，不允许自由文本直接进入流程。

示例：

```json
{
  "issue_type": "validation",
  "root_cause": "price field lacks positive validation",
  "target_files": [
    "ProductCreateRequest.java"
  ],
  "patch_plan": [
    {
      "file": "ProductCreateRequest.java",
      "change": "add @DecimalMin(\"0.01\") to price field"
    }
  ],
  "tests_to_run": [
    "createProduct_priceZero_shouldReturn400"
  ]
}
```

---

## 21. 日志与比赛交付

### 21.1 必须输出的日志

```text
logs/interaction.md
logs/trace/pipeline_trace.jsonl
logs/trace/extracted_api_contract.json
logs/trace/extracted_business_rules.json
logs/trace/repo_map.json
logs/trace/trace_matrix.json
logs/trace/generated_tests.json
logs/trace/candidate_patches.jsonl
logs/trace/validation_results.jsonl
logs/trace/failure_taxonomy.jsonl
```

### 21.2 result/output.md 内容

必须包含：

```text
项目运行入口
读取了哪些设计文档
读取了哪些 API 基线
识别出的不一致点数量
修复的不一致点数量
修改文件列表
测试执行结果
稳定性重跑结果
最终结论
```

### 21.3 修复报告模板

```markdown
# Goal-agent 修复报告

## 1. 运行概况

- 设计文档数量：
- API 契约数量：
- 扫描 Controller 数量：
- 扫描 DTO 数量：
- 扫描 Service 数量：
- 识别不一致点数量：
- 修复不一致点数量：

## 2. 主要修复项

| ID | 类型 | 设计要求 | 修复文件 | 验证方式 | 状态 |
|---|---|---|---|---|---|

## 3. 测试结果

| 测试类型 | 结果 |
|---|---|
| mvn clean test | PASS |
| generated tests | PASS |
| contract checker | PASS |
| forbidden-change guard | PASS |
| stability rerun | PASS |

## 4. 风险说明

## 5. 结论
```

---

## 22. INSTRUCTION.md 设计

比赛入口文件建议写成：

````markdown
# Goal-agent 运行说明

## 运行入口

请在项目根目录执行：

```bash
python3 work/goal_agent/main.py --project-root . --mode full
```

## 运行模式

```bash
# 完整模式：抽取、检查、修复、验证、报告
python3 work/goal_agent/main.py --project-root . --mode full

# 只检查不修复
python3 work/goal_agent/main.py --project-root . --mode check

# 只生成测试
python3 work/goal_agent/main.py --project-root . --mode generate-tests

# 只验证当前代码
python3 work/goal_agent/main.py --project-root . --mode validate
```

## 输出

运行完成后查看：

```text
result/output.md
result/repair_report.md
result/stability_report.md
logs/trace/
```
````

---

## 23. 开发优先级

### 第一阶段：P0 基础闭环

必须先完成：

```text
1. api_baseline_parser.py
2. design_doc_parser.py
3. api_contract_builder.py
4. business_rule_builder.py
5. spring_route_analyzer.py
6. dto_analyzer.py
7. exception_analyzer.py
8. contract_checker.py
9. forbidden_change_guard.py
10. test_runner.py
```

目标：

```text
能读取文档。
能扫描代码。
能发现明显 API/DTO/Exception 不一致。
能输出报告。
能禁止非法修改。
```

### 第二阶段：P0 修复能力

继续完成：

```text
1. trace_matrix_builder.py
2. issue_localizer.py
3. patch_planner.py
4. patch_generator.py
5. patch_applier.py
6. patch_selector.py
7. validation_strategy.py
8. api_schema_strategy.py
9. exception_strategy.py
```

目标：

```text
能定位问题。
能生成多个候选补丁。
能独立验证候选补丁。
能选择最优补丁。
```

### 第三阶段：P1 黑箱增强

继续完成：

```text
1. spec_test_generator.py
2. mockmvc_generator.py
3. boundary_case_generator.py
4. hidden_case_simulator.py
5. stability_runner.py
6. failure_taxonomy.jsonl
7. pagination_strategy.py
8. repository_query_strategy.py
9. business_rule_strategy.py
```

目标：

```text
提升隐藏测试通过率。
提升稳定率。
减少边界 case 失败。
沉淀失败归因。
```

---

## 24. 最小可用版本 MVP

如果时间有限，先做 MVP，不要一开始做完整大系统。

MVP 必须包括：

```text
1. API 基线解析
2. Controller 路由扫描
3. DTO 字段与 validation 扫描
4. ExceptionHandler 扫描
5. API contract checker
6. generated negative tests
7. LLM patch generator
8. forbidden-change guard
9. mvn clean test
10. stability rerun
```

MVP 运行流程：

```text
读取 API基线文档.md
  -> 扫描 Controller/DTO/ExceptionHandler
  -> 对比接口、字段、校验、错误响应
  -> 生成不一致点
  -> 对每个不一致点生成候选补丁
  -> 应用最小补丁
  -> 跑 mvn clean test
  -> 跑 generated tests
  -> 跑 forbidden guard
  -> 输出 result/output.md
```

---

## 25. 当前 18 个失败 case 的专项修复策略

虽然不知道隐藏测试具体内容，但从比赛类型判断，优先补以下能力。

### 25.1 Validation 补强

重点检查：

```text
@NotNull 是否缺失
@NotBlank 是否缺失
@Min/@Max 是否缺失
@DecimalMin 是否缺失
@Size 是否缺失
@Valid 是否缺失
Controller 参数是否缺少 @Valid
```

预期提升：

```text
可修复 3~6 个隐藏失败 case
```

### 25.2 ExceptionHandler 补强

重点检查：

```text
MethodArgumentNotValidException
ConstraintViolationException
IllegalArgumentException
ResourceNotFoundException
Duplicate/Conflict Exception
```

预期提升：

```text
可修复 3~5 个隐藏失败 case
```

### 25.3 Response Schema 补强

重点检查：

```text
是否统一 ApiResponse
code/message/data 是否存在
列表为空是否返回 []
分页字段是否完整
错误响应是否和 API 基线一致
```

预期提升：

```text
可修复 2~4 个隐藏失败 case
```

### 25.4 Pagination / Sorting 补强

重点检查：

```text
page 起始值
size 下限
size 上限
默认排序
排序字段白名单
空页行为
```

预期提升：

```text
可修复 1~3 个隐藏失败 case
```

### 25.5 Repository Query 补强

重点检查：

```text
软删除过滤
状态过滤
用户归属过滤
重复判断
exists 查询
大小写敏感
```

预期提升：

```text
可修复 2~4 个隐藏失败 case
```

---

## 26. 验收标准

### 26.1 功能验收

```text
能自动读取 design-docs。
能自动读取 API基线文档.md。
能自动扫描 Java Spring Boot 项目。
能生成 api_contract.json。
能生成 business_rules.json。
能生成 repo_map.json。
能生成 trace_matrix.json。
能识别不一致点。
能自动生成候选补丁。
能自动验证补丁。
能输出 result/output.md。
```

### 26.2 质量验收

```text
不得修改设计文档。
不得修改 API 基线。
不得修改测试代码。
不得硬编码公开 case。
不得导致项目无法编译。
最终 mvn clean test 至少连续 3 次通过。
生成测试至少连续 3 次通过。
contract checker 必须通过。
forbidden-change guard 必须通过。
```

### 26.3 比赛效果验收

目标：

```text
黑箱通过率 >= 85%
稳定通过率 >= 85%
失败 case 可归因率 >= 90%
```

最低可接受：

```text
黑箱通过率 >= 80%
稳定通过率 >= 80%
失败 case 可归因率 >= 70%
```

---

## 27. 给 Codex 的开发指令

可以直接使用以下指令让 Codex 开发：

```text
请重构当前 Goal-agent 项目，不需要兼容旧版本，也不需要保留旧接口。

目标是将 Goal-agent 改造成一个规格驱动的设计实现一致性检查与自动修复系统，用于 Java Spring Boot 项目的比赛场景。

请按照以下优先级实现：

第一优先级：
1. 读取 API基线文档.md，生成 api_contract.json。
2. 读取 design-docs/，生成 business_rules.json。
3. 扫描 code/ 中的 Spring Boot 项目，生成 repo_map.json。
4. 对比 api_contract、business_rules、repo_map，生成 trace_matrix.json。
5. 实现 contract_checker，检查接口路径、method、DTO 字段、validation、response schema、error schema。
6. 实现 forbidden_change_guard，禁止修改 API基线文档.md、design-docs/**、src/test/**。
7. 实现 test_runner，执行 mvn clean test。
8. 输出 result/output.md 和 logs/trace/*。

第二优先级：
1. 实现 issue_localizer，将一致性问题转换为 repair_tasks。
2. 实现 patch_generator，每个 repair task 生成 3~5 个候选补丁。
3. 实现 candidate sandbox，每个补丁独立验证。
4. 实现 patch_selector，选择测试通过率最高、diff 最小、风险最低的补丁。
5. 实现 stability_runner，最终补丁连续重跑 3 次。

第三优先级：
1. 实现 spec_test_generator，根据 API contract 和 business rules 自动生成 MockMvc/JUnit 测试。
2. 实现 hidden_case_simulator，覆盖 null、blank、zero、negative、not found、duplicate、pagination、sorting、error response 等隐藏测试常见 case。
3. 实现 failure_taxonomy.jsonl，将失败按 validation、api_schema、error_handling、business_rule、pagination、repository_query、flaky 等类型归因。

要求：
- 不修改 API基线文档.md。
- 不修改 design-docs/**。
- 不修改 src/test/**。
- 不硬编码公开测试。
- 不删除测试。
- 不吞异常。
- 不统一返回 200。
- 优先最小 diff。
- 每次修复必须有日志。
- 最终必须生成 result/output.md。
```

---

## 28. 最终结论

Goal-agent 当前通过率停在 70%~75%，核心原因不是 Agent 数量不足，而是缺少规格驱动的工程闭环。

本次重构应从“Agent 自由读代码和修代码”转向：

```text
API Contract
Business Rules
Repo Map
Trace Matrix
Generated Tests
Patch Candidates
Sandbox Validation
Patch Scoring
Stability Gate
Failure Taxonomy
```

这套结构能够把黑箱失败从不可解释问题，转成可定位、可验证、可复用的修复任务。

优先级最高的不是继续加复杂 Agent，而是先完成：

```text
1. API contract checker
2. DTO/Exception/Response schema 检查
3. generated hidden-case tests
4. 多候选补丁 sandbox
5. forbidden-change guard
6. stability rerun
```

只要这六个模块完成，Goal-agent 的黑箱通过率和稳定率应该会明显高于当前版本。
