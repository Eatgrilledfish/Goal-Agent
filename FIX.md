# Goal-Agent `spec-driven-refactor` 分支修复文档

## 1. 文档目的

本文档用于指导 Goal-Agent 项目继续修复 `spec-driven-refactor` 分支，使其真正形成“规格驱动的设计实现一致性检查与自动修复系统”。

当前分支已经完成了正确的架构转向，但仍存在若干关键工程缺口。本文档的目标是把这些问题转化为可执行的修复任务，方便直接交给 Codex / Opencode / Claude Code 等代码 Agent 继续开发。

---

## 2. 当前分支总体评价

当前分支：`spec-driven-refactor`

仓库：`Eatgrilledfish/Goal-Agent`

整体判断：

```text
架构方向正确度：8 / 10
对原始重构文档的遵循度：6.5 / 10
可实际提升黑箱通过率的落地度：5 / 10
当前最大风险：脚本可运行性、误报/漏报、候选补丁 sandbox 未真正实现
```

当前分支已经做对了以下方向：

```text
1. 将主流程改为 spec-driven pipeline。
2. 新增 API contract builder。
3. 新增 business rule builder。
4. 新增 Spring Boot repo scanner。
5. 新增 DTO analyzer。
6. 新增 exception analyzer。
7. 新增 contract checker。
8. 新增 spec test generator。
9. 新增 forbidden-change guard。
10. 新增 stability runner。
11. 在 INSTRUCTION.md 和 SKILL.md 中明确了多阶段执行流程。
```

但当前还不能算完整实现，原因是：

```text
1. 多候选补丁生成还主要停留在 subagent 文档层面。
2. candidate sandbox 没有确定性脚本落地。
3. patch selector 和 scoring 没有实际工程实现。
4. repair_tasks.json 的确定性生成不完整。
5. trace matrix 过粗，不能准确判断 partial/conflict。
6. error handling checker 基本为空实现。
7. generated tests 还不能作为可靠 oracle。
8. 部分脚本存在明确运行 bug 或高误报风险。
```

---

## 3. 修复目标

### 3.1 总目标

将当前分支从：

```text
Spec → Trace → Test draft → Agent prompt → Stability rerun
```

升级为：

```text
Spec → Trace → Static Check → Repair Task → Candidate Patch
→ Sandbox Validation → Scoring → Apply Best Patch → Stability Gate
```

### 3.2 比赛目标

当前目标仍然是提升黑箱测试通过率和稳定率。

```text
当前水平：约 70%~75%
第一阶段目标：80%+
第二阶段目标：85%+
理想目标：90%+
```

### 3.3 工程目标

修复完成后，系统必须具备以下能力：

```text
1. API contract 能准确解析 required、type、status、error code。
2. DTO analyzer 只检查 endpoint 对应 DTO，不做全量笛卡尔积误匹配。
3. exception analyzer 不崩溃，并能准确输出 exception coverage。
4. contract checker 能检查 route、request、response、error、validation。
5. trace matrix 能区分 implemented / partial / missing / conflict。
6. spec test generator 生成的测试能编译、能运行、能参与评分。
7. repair_task_builder 能确定性生成 repair_tasks.json。
8. candidate_sandbox 能独立验证每个候选补丁。
9. patch_selector 能根据测试、contract、guard、diff、stability 评分。
10. stability_runner 能运行完整门禁，而不是只跑 test-cases。
```

---

## 4. 当前分支已完成项

### 4.1 主流程文档已基本正确

`work/skill/SKILL.md` 已定义以下阶段：

```text
Phase 0: Preflight
Phase 1: Build Contracts
Phase 2: Scan Code
Phase 3: Build Trace Matrix + Static Consistency Check
Phase 4: Generate Spec-Driven Tests
Phase 5: Baseline Test Run
Phase 6: Localize & Prioritize Repair Tasks
Phase 7: Fix Loop
Phase 8: Stability Gate
Phase 9: Report & Deliver
```

这个流程方向正确，保留。

### 4.2 INSTRUCTION.md 已改成规格驱动入口

`INSTRUCTION.md` 已明确运行目标：

```text
API Contract → Business Rules → Trace Matrix → Static Check
→ Generated Tests → Patch Candidates → Sandbox → Scoring → Stability
```

这个入口方向正确，保留。

### 4.3 已新增核心脚本

当前新增脚本包括：

```text
work/tools/scripts/api_contract_builder.py
work/tools/scripts/business_rule_builder.py
work/tools/scripts/spring_scanner.py
work/tools/scripts/dto_analyzer.py
work/tools/scripts/exception_analyzer.py
work/tools/scripts/contract_checker.py
work/tools/scripts/spec_test_generator.py
work/tools/scripts/forbidden_change_guard.py
work/tools/scripts/stability_runner.py
```

这些脚本方向正确，但需要继续补齐实现质量。

---

## 5. P0 问题清单

P0 问题必须优先修复，否则后续 patch loop 会被错误输入误导。

---

## 6. P0-1：修复 `exception_analyzer.py` 运行 bug

### 6.1 问题描述

当前 `exception_analyzer.py` 存在如下逻辑：

```python
has_api_response = any(
    "@RestControllerAdvice" in runner.read_text(code_dir / "**" / "*.java")
    for _ in [1]
)
```

问题：

```text
1. code_dir / "**" / "*.java" 不是 glob，而是一个字面路径。
2. runner.read_text() 会尝试读取 code/**/*.java，极可能 FileNotFoundError。
3. has_api_response 变量后续没有被使用。
4. 该问题会导致 Phase 2 扫描异常处理时直接崩溃。
```

### 6.2 修复要求

删除这段无用逻辑，或者改成真实遍历。

推荐直接删除：

```python
# Remove unused has_api_response block
```

如需保留，可改成：

```python
has_api_response = False
for java_path in code_dir.rglob("*.java"):
    text = runner.read_text(java_path)
    if "ApiResponse" in text or "Result<" in text or "ResponseResult" in text:
        has_api_response = True
        break
```

### 6.3 验证方式

运行：

```bash
python3 work/tools/scripts/exception_analyzer.py --root .
```

预期：

```text
1. 不抛 FileNotFoundError。
2. 生成 .agent-work/exception_coverage.json。
3. 生成 .agent-work/05_exception_coverage.md。
```

---

## 7. P0-2：修复 `api_contract_builder.py` required 字段解析失效

### 7.1 问题描述

当前 `extract_fields_from_nearby_table()` 的 regex 实际只捕获 field name 和 type，但后续代码尝试读取 `match.group(3)` 判断 required。

结果：

```text
required 基本都会是 False。
```

影响：

```text
1. required 字段无法准确识别。
2. null/blank 负例测试无法准确生成。
3. DTO validation gap 判断会失真。
4. contract checker 会漏掉必填字段校验缺失。
5. 隐藏测试中 required/null/blank 类 case 仍然容易失败。
```

### 7.2 修复要求

重写字段表格解析逻辑，支持以下表头：

```text
字段
字段名
名称
name
field
参数名
类型
type
是否必填
必填
required
是否必须
说明
描述
description
```

推荐实现：

```python
def extract_markdown_tables(context: str) -> list[list[list[str]]]:
    tables = []
    lines = context.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        if len(block) >= 2:
            rows = []
            for row in block:
                cells = [c.strip().strip("`") for c in row.strip("|").split("|")]
                rows.append(cells)
            tables.append(rows)
    return tables
```

然后根据 header 定位列：

```python
field_col = find_col(headers, ["字段", "字段名", "名称", "field", "name", "参数名"])
type_col = find_col(headers, ["类型", "type"])
required_col = find_col(headers, ["是否必填", "必填", "required", "是否必须"])
desc_col = find_col(headers, ["说明", "描述", "description"])
```

required 判断：

```python
def parse_required(value: str) -> bool:
    v = value.strip().lower()
    return v in {"是", "必填", "必须", "y", "yes", "true", "required", "mandatory"}
```

### 7.3 输出字段

每个字段必须包含：

```json
{
  "type": "string",
  "required": true,
  "constraints": ["not_blank"],
  "description": "商品名称"
}
```

### 7.4 验证方式

构造 markdown 表：

```markdown
| 字段名 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| name | string | 是 | 商品名称 |
| price | decimal | 是 | 商品价格 |
| description | string | 否 | 商品描述 |
```

预期输出：

```json
{
  "name": {"required": true},
  "price": {"required": true},
  "description": {"required": false}
}
```

---

## 8. P0-3：修复 `dto_analyzer.py` 全量 DTO 误匹配

### 8.1 问题描述

当前 `dto_analyzer.py` 在检查 API contract 字段时，会把每个 endpoint 的字段拿去和所有 DTO 对比。

错误效果：

```text
POST /products 的 price 字段
会被拿去检查 UserRequest、OrderRequest、CategoryRequest 等所有 DTO。
```

这会产生大量 false positive，导致 Agent 修错地方。

### 8.2 修复要求

必须改成：

```text
endpoint -> controller method -> request_body_type -> 对应 DTO -> 对应字段
```

不能全量扫描所有 DTO。

### 8.3 实现方案

在 `dto_analyzer.py` 中读取 `.agent-work/repo_map.json` 或使用 `runner.snapshot_code_api(root)`。

伪代码：

```python
snapshot = runner.snapshot_code_api(root)
endpoint_to_dto = {}

for ep in snapshot.get("endpoints", []):
    key = (ep.get("method"), ep.get("url"))
    dto_name = ep.get("request_body_type")
    if dto_name:
        endpoint_to_dto[key] = dto_name

for endpoint in api_contract.get("endpoints", []):
    key = (endpoint["method"], endpoint["path"])
    dto_name = endpoint_to_dto.get(key)

    if not dto_name:
        report missing_request_dto
        continue

    dto_info = find_dto_report(dto_name)
    check only dto_info fields
```

### 8.4 输出要求

`gap_details` 中必须包含：

```json
{
  "endpoint": "POST /api/products",
  "dto": "ProductCreateRequest",
  "field": "price",
  "expected": "type=decimal, required=true",
  "gaps": ["MISSING_DECIMAL_MIN_OR_POSITIVE"]
}
```

不得出现无关 DTO 的同字段误报。

### 8.5 验证方式

如果有 10 个 DTO，一个 endpoint 只对应 1 个 request DTO，则该 endpoint 的字段 gap 只能出现在这个 DTO 上。

---

## 9. P0-4：强化 `contract_checker.py` 的 error handling 检查

### 9.1 问题描述

当前 `check_endpoint_errors()` 基本是空实现：

```python
if status and status == 400 and "validation" in condition.lower():
    pass
elif status and status == 404:
    pass
elif status and status == 409:
    pass
```

这无法发现：

```text
1. 校验失败返回 500。
2. 资源不存在返回 500。
3. 重复创建返回 200。
4. 错误码不符合 API 基线。
5. 错误响应体缺少 code/message。
6. 全局 Exception 被吞掉。
```

这些正是隐藏测试最容易覆盖的 case。

### 9.2 修复要求

`contract_checker.py` 必须读取：

```text
.agent-work/exception_coverage.json
.agent-work/repo_map.json
.agent-work/api_contract.json
```

对每个 endpoint 的 documented errors 做检查。

### 9.3 规则设计

400 类错误：

```text
需要存在以下至少一个 handler：
- MethodArgumentNotValidException
- ConstraintViolationException
- BindException
- HttpMessageNotReadableException
```

404 类错误：

```text
需要存在以下至少一个 handler：
- EntityNotFoundException
- ResourceNotFoundException
- NotFoundException
- NoSuchElementException
```

409 类错误：

```text
需要存在以下至少一个 handler：
- ConflictException
- DuplicateException
- DataIntegrityViolationException
```

错误体检查：

```text
必须能够返回 code/message 或项目统一错误结构。
如果 contract 指定 error code，则 handler 或 error enum 中必须出现该 code。
```

### 9.4 issue 输出格式

```json
{
  "type": "missing_error_handler",
  "severity": "P0",
  "endpoint_id": "API-001",
  "method": "POST",
  "path": "/api/products",
  "expected_status": 400,
  "expected_code": "VALIDATION_ERROR",
  "detail": "API contract documents 400 VALIDATION_ERROR but no validation exception handler was found",
  "suspected_files": ["GlobalExceptionHandler.java"]
}
```

### 9.5 验证方式

构造 contract：

```json
{
  "errors": [
    {
      "status": 400,
      "body": {"code": "VALIDATION_ERROR", "message": "string"}
    }
  ]
}
```

如果代码没有 MethodArgumentNotValidException handler，则必须输出 P0 issue。

---

## 10. P0-5：强化 response schema checker

### 10.1 问题描述

当前 response 检查只做浅层字段匹配，且跳过 `data.id` 这类嵌套字段。

问题：

```text
1. 无法识别 ApiResponse<T> 泛型。
2. 无法识别 ResponseEntity<ApiResponse<T>>。
3. 无法检查 data.id / data.items / data.total 等嵌套字段。
4. 无法检查空列表返回 [] 还是 null。
5. 无法检查分页 metadata。
```

### 10.2 修复要求

新增 response type parser：

```python
def unwrap_response_type(response_type: str) -> dict:
    """
    Input:
      ResponseEntity<ApiResponse<ProductResponse>>
      ApiResponse<List<OrderResponse>>
      Result<PageResult<ProductVO>>

    Output:
      {
        "wrapper": "ApiResponse",
        "inner": "ProductResponse",
        "collection": false,
        "page": false
      }
    """
```

需要支持：

```text
ApiResponse<T>
Result<T>
ResponseEntity<T>
List<T>
Page<T>
PageResult<T>
IPage<T>
```

### 10.3 嵌套字段检查

如果 contract 有：

```json
{
  "response": {
    "body": {
      "code": "integer",
      "message": "string",
      "data.id": "long",
      "data.name": "string"
    }
  }
}
```

检查逻辑：

```text
code/message 在 wrapper DTO 或统一响应类中检查。
data.id/data.name 在 inner response DTO 中检查。
data.items.* 在 page/list 内部 DTO 中检查。
```

### 10.4 issue 输出格式

```json
{
  "type": "missing_response_field",
  "severity": "P1",
  "endpoint_id": "API-001",
  "method": "GET",
  "path": "/api/products/{id}",
  "response_type": "ApiResponse<ProductResponse>",
  "field": "data.price",
  "detail": "Contract requires data.price but ProductResponse has no price field"
}
```

---

## 11. P0-6：修复 trace matrix 判断过粗

### 11.1 问题描述

当前 trace matrix 只要找到 endpoint，就标记为 `implemented`。

这是错误的。

真实情况可能是：

```text
endpoint 存在，但 DTO 字段缺失：partial
endpoint 存在，但 validation 缺失：partial
endpoint 存在，但 response schema 不一致：conflict
endpoint 存在，但 error handler 不一致：conflict
endpoint 存在，但 business rule 未实现：partial / missing
```

### 11.2 修复要求

trace matrix 必须结合 consistency issues 更新状态。

### 11.3 状态规则

```python
def determine_implementation_status(endpoint, issues):
    endpoint_issues = find_issues_for_endpoint(endpoint, issues)

    if any(i["type"] == "missing_endpoint" for i in endpoint_issues):
        return "missing"

    if any(i["severity"] == "P0" and i["type"] in {
        "type_mismatch",
        "wrong_http_method",
        "wrong_path",
        "wrong_status_code",
        "wrong_error_code"
    } for i in endpoint_issues):
        return "conflict"

    if endpoint_issues:
        return "partial"

    return "implemented"
```

### 11.4 trace item 输出

```json
{
  "requirement_id": "API-001",
  "api_id": "API-001",
  "description": "创建商品",
  "links": {
    "controller": {
      "file": "ProductController.java",
      "symbol": "createProduct",
      "confidence": 0.95
    },
    "request_dto": {
      "file": "ProductCreateRequest.java",
      "symbol": "price",
      "confidence": 0.90
    }
  },
  "implementation_status": "partial",
  "gap": "price field lacks @DecimalMin(\"0.01\"); validation error handler missing",
  "repair_priority": "P0"
}
```

---

## 12. P0-7：新增 `repair_task_builder.py`

### 12.1 问题描述

当前 `consistency-checker.md` 要求生成 `.agent-work/repair_tasks.json`，但没有确定性脚本落地。

这导致 Phase 6 仍然依赖 Agent 自由总结。

### 12.2 新增文件

```text
work/tools/scripts/repair_task_builder.py
```

### 12.3 输入

```text
.agent-work/api_contract.json
.agent-work/business_rules.json
.agent-work/repo_map.json
.agent-work/dto_validation_report.json
.agent-work/exception_coverage.json
.agent-work/consistency_report.json
.agent-work/test_symptoms.jsonl
```

### 12.4 输出

```text
.agent-work/repair_tasks.json
.agent-work/repair_tasks.md
```

### 12.5 任务分类

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

### 12.6 优先级规则

```text
P0:
- missing_endpoint
- type_mismatch
- missing_required_field
- missing_validation_for_required_field
- missing_error_handler
- wrong_status_code
- wrong_error_code

P1:
- missing_response_field
- pagination_metadata_missing
- missing_sorting
- null_vs_empty_list
- repository_query_filter_missing

P2:
- documentation mismatch
- log message
- naming/style
```

### 12.7 输出格式

```json
{
  "generated_at": "2026-07-01T00:00:00+09:00",
  "tasks": [
    {
      "id": "TASK-001",
      "type": "validation",
      "priority": "P0",
      "source": "contract_checker",
      "related_api": "POST /api/products",
      "requirement_id": "API-001",
      "symptom": "price field lacks positive validation",
      "suspected_files": [
        "code/src/main/java/.../ProductCreateRequest.java"
      ],
      "expected_fix": "Add @DecimalMin(\"0.01\") and ensure controller uses @Valid",
      "verification_tests": [
        "generated:create_product_price_zero_should_return_400",
        "mvn -s maven-settings.xml -f test-cases/pom.xml test"
      ],
      "risk": "low"
    }
  ]
}
```

### 12.8 去重规则

同一个 endpoint + same field + same issue type 只保留一个 task。

```python
dedup_key = (related_api, field, type)
```

---

## 13. P0-8：新增 candidate sandbox

### 13.1 问题描述

当前多候选补丁仅存在于 `patch-generator.md` 文档中，没有实际 sandbox 验证脚本。

这意味着系统仍可能线性修复：

```text
生成一个补丁 → 应用 → 失败后继续叠改
```

容易导致代码越来越乱。

### 13.2 新增文件

```text
work/tools/scripts/candidate_sandbox.py
```

### 13.3 功能要求

每个 candidate 必须在独立 workspace 中验证。

流程：

```text
1. 从当前 clean baseline 复制 workspace 或 git worktree。
2. 应用 candidate patch。
3. 运行 compile。
4. 运行 code module tests。
5. 运行 public black-box tests。
6. 运行 generated tests。
7. 运行 contract_checker。
8. 运行 forbidden_change_guard。
9. 记录 candidate 验证结果。
10. 删除失败 workspace。
```

### 13.4 输入

```text
--root PROJECT_ROOT
--task-id TASK-001
--candidate-file .agent-work/candidate_patches.jsonl
```

candidate_patches.jsonl 每行格式：

```json
{
  "task_id": "TASK-001",
  "candidate_id": "candidate-1",
  "patch_file": ".agent-work/patches/TASK-001-candidate-1.patch",
  "strategy": "minimal-dto",
  "modified_files": ["code/.../ProductCreateRequest.java"]
}
```

### 13.5 输出

```text
.agent-work/candidate_validation.jsonl
.agent-work/candidates/TASK-001/candidate-1/
```

输出记录：

```json
{
  "task_id": "TASK-001",
  "candidate_id": "candidate-1",
  "strategy": "minimal-dto",
  "compile": "PASS",
  "code_tests": "PASS",
  "public_tests": "PASS",
  "generated_tests": "PASS",
  "contract_check": "PASS",
  "forbidden_guard": "PASS",
  "diff_files": 1,
  "diff_lines": 3,
  "score_inputs": {
    "public_test_pass_rate": 1.0,
    "generated_test_pass_rate": 1.0,
    "contract_checker_pass": 1.0,
    "diff_files": 1,
    "diff_lines": 3,
    "stable": null
  },
  "eligible": true
}
```

### 13.6 直接淘汰条件

```text
1. 编译失败。
2. forbidden guard 出现 blocker。
3. contract checker 出现新的 P0 issue。
4. 修改 design-docs、README、test-cases。
5. 统一返回 200。
6. 吞异常返回成功。
7. 硬编码公开测试数据。
```

---

## 14. P0-9：新增 patch selector

### 14.1 新增文件

```text
work/tools/scripts/patch_selector.py
```

### 14.2 输入

```text
.agent-work/candidate_validation.jsonl
```

### 14.3 评分公式

```text
score =
  40% * public_test_pass_rate
+ 25% * generated_test_pass_rate
+ 15% * contract_checker_pass
+ 10% * diff_minimization
+ 10% * stability_score
```

### 14.4 diff_minimization

```python
diff_minimization = max(0, 1 - (diff_files * 0.1 + diff_lines * 0.005))
```

### 14.5 stability_score

如果未进行多轮稳定性验证：

```text
默认 0.5
```

如果已验证：

```text
3 次全过：1.0
2 次过 1 次失败：0.3
有 intermittent failure：0.0
```

### 14.6 输出

```text
.agent-work/selected_patch.json
.agent-work/selected_patch.md
```

格式：

```json
{
  "task_id": "TASK-001",
  "selected_candidate": "candidate-1",
  "score": 0.94,
  "reason": "Highest pass rate, minimal diff, no guard violation",
  "patch_file": ".agent-work/patches/TASK-001-candidate-1.patch",
  "fallback_candidates": ["candidate-2", "candidate-3"]
}
```

---

## 15. P0-10：强化 spec test generator

### 15.1 问题描述

当前 `spec_test_generator.py` 方向正确，但不能作为可靠 oracle。

主要问题：

```text
1. positive test 固定期望 status().isOk()，但 contract 可能是 201。
2. response 固定检查 $.data，项目可能是别的响应结构。
3. error format test 只是注释，没有实际断言。
4. GET / DELETE 测试被大量跳过。
5. 测试数据没有 setup，ID=1 不一定存在。
6. 生成测试可能无法编译。
```

### 15.2 修复要求

#### 15.2.1 success status 从 contract 读取

```python
success_status = endpoint.get("response", {}).get("success_status", [200])
```

生成断言：

```java
.andExpect(status().isCreated())
```

或：

```java
.andExpect(status().isOk())
```

如果允许多个成功码，生成：

```java
.andExpect(result -> assertTrue(
    List.of(200, 201).contains(result.getResponse().getStatus())
));
```

#### 15.2.2 response jsonPath 从 contract 读取

如果 contract response body 有：

```json
{
  "code": "integer",
  "message": "string",
  "data.id": "long"
}
```

生成：

```java
.andExpect(jsonPath("$.code").exists())
.andExpect(jsonPath("$.message").exists())
.andExpect(jsonPath("$.data.id").exists())
```

#### 15.2.3 error format test 必须有实际断言

不能只生成注释。

示例：

```java
mockMvc.perform(post("/api/products")
        .contentType(MediaType.APPLICATION_JSON)
        .content("{\"name\":\"\",\"price\":-1}"))
    .andExpect(status().isBadRequest())
    .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
    .andExpect(jsonPath("$.message").exists());
```

#### 15.2.4 不要跳过 GET/DELETE

GET 需要生成：

```text
1. list endpoint pagination test
2. not found test for /{id}
3. response schema test
```

DELETE 需要生成：

```text
1. delete non-existing id -> 404
2. delete existing id -> 200/204
3. delete again -> 404 或幂等成功，按 contract 判断
```

#### 15.2.5 生成测试先 dry-run compile

新增命令：

```bash
python3 work/tools/scripts/spec_test_generator.py --root . --dry-run-compile
```

逻辑：

```text
1. 生成测试到 .tmp/generated-tests。
2. 临时复制到 code/src/test/java/generated。
3. mvn -f code/pom.xml -Dtest=Generated* test-compile。
4. 编译失败则标记 generated_test_status=unusable。
5. 不把不可编译测试纳入 patch scoring。
```

---

## 16. P0-11：强化 stability runner 为完整 gate

### 16.1 问题描述

当前 `stability_runner.py` 只跑：

```text
mvn -f test-cases/pom.xml test
```

但完整稳定性门禁应该包含：

```text
1. code/pom.xml test
2. code/pom.xml install -DskipTests
3. test-cases/pom.xml test
4. contract_checker
5. forbidden_change_guard
6. generated tests
```

### 16.2 修复要求

`stability_runner.py` 增加参数：

```text
--mode public-only
--mode full-gate
```

默认：

```text
full-gate
```

### 16.3 full-gate 每轮执行

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
python3 work/tools/scripts/contract_checker.py --root .
python3 work/tools/scripts/forbidden_change_guard.py --root . --strict
```

如果 `.tmp/generated-tests/` 存在且可用，则运行 generated tests。

### 16.4 输出

```json
{
  "stable": true,
  "runs_requested": 3,
  "runs_completed": 3,
  "gate_results": [
    {
      "run": 1,
      "code_tests": "PASS",
      "code_install": "PASS",
      "public_tests": "PASS",
      "generated_tests": "PASS",
      "contract_checker": "PASS",
      "forbidden_guard": "PASS"
    }
  ]
}
```

---

## 17. P1 问题清单

P1 问题影响黑箱通过率，但可以在 P0 完成后处理。

---

## 18. P1-1：去掉 legacy agent 对主流程的干扰

### 18.1 问题描述

当前 `SKILL.md` 和 `INSTRUCTION.md` 仍然保留大量 legacy subagent：

```text
shophub-spec-librarian
shophub-api-guardian
shophub-code-mapper
shophub-module-mapper
shophub-test-diagnoser
shophub-module-auditor
shophub-cross-cut-auditor
shophub-patch-agent
shophub-review-agent
shophub-report-writer
```

问题：

```text
1. 与新 spec-driven subagents 职责重叠。
2. Agent 可能走回旧流程。
3. 调度复杂度增加。
4. 不符合“不考虑继承性和兼容性”的重构要求。
```

### 18.2 修复要求

主路径只保留：

```text
contract-builder
code-analyzer
consistency-checker
patch-generator
stability-verifier
report-writer
```

legacy agents 改为 fallback，不在主流程中主动调用。

文档中应改成：

```text
Legacy agents are fallback only. Do not invoke unless spec-driven pipeline cannot proceed.
```

---

## 19. P1-2：强化 repository query checker

### 19.1 目标

发现隐藏测试常见问题：

```text
1. 查询返回软删除数据。
2. 查询未过滤 status。
3. 查询未过滤 userId / ownerId。
4. 列表无稳定排序。
5. duplicate check 不完整。
```

### 19.2 新增检查

在 `spring_scanner.py` 或 `contract_checker.py` 中新增 repository issue：

```json
{
  "type": "repository_query_filter_missing",
  "severity": "P1",
  "repository": "ProductRepository",
  "method": "findByCategoryId",
  "detail": "List query may not filter deleted/status fields",
  "suggested_fix": "Add status/deleted filter if entity has deleted/status field"
}
```

### 19.3 判断逻辑

如果 entity 有字段：

```text
deleted
isDeleted
status
enabled
active
ownerId
userId
tenantId
```

则对应 repository 查询应尽量包含这些条件。

---

## 20. P1-3：增加 failure taxonomy

### 20.1 新增文件

```text
work/tools/scripts/failure_taxonomy_builder.py
```

### 20.2 输入

```text
.agent-work/consistency_report.json
.agent-work/test_symptoms.jsonl
.agent-work/stability_report.json
.agent-work/candidate_validation.jsonl
```

### 20.3 输出

```text
.agent-work/failure_taxonomy.jsonl
.agent-work/failure_taxonomy.md
```

### 20.4 分类

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

### 20.5 输出格式

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
  "status": "open"
}
```

---

## 21. 推荐最终目录结构

修复后建议形成：

```text
work/tools/scripts/
├── api_contract_builder.py
├── business_rule_builder.py
├── spring_scanner.py
├── dto_analyzer.py
├── exception_analyzer.py
├── contract_checker.py
├── repair_task_builder.py
├── spec_test_generator.py
├── candidate_sandbox.py
├── patch_selector.py
├── patch_score.py
├── forbidden_change_guard.py
├── stability_runner.py
├── failure_taxonomy_builder.py
└── shophub_goal_runner.py
```

---

## 22. 修复后完整 Pipeline

最终主流程应为：

```text
Phase 0: Preflight
  - 检查 PROJECT_ROOT
  - 初始化 .agent-work
  - 保存 git baseline

Phase 1: Build Contracts
  - api_contract_builder.py
  - business_rule_builder.py
  - contract-builder subagent 语义补强

Phase 2: Scan Code
  - spring_scanner.py
  - dto_analyzer.py
  - exception_analyzer.py

Phase 3: Static Consistency Check
  - contract_checker.py
  - 生成 consistency_report.json
  - 生成 trace_matrix.json

Phase 4: Repair Task Build
  - repair_task_builder.py
  - 生成 repair_tasks.json

Phase 5: Spec Test Generation
  - spec_test_generator.py
  - dry-run compile
  - 标记 generated tests 可用性

Phase 6: Baseline Test Run
  - code tests
  - code install
  - public black-box tests
  - 记录 test_symptoms.jsonl

Phase 7: Candidate Patch Loop
  - patch-generator 生成 3~5 candidates
  - candidate_sandbox.py 独立验证
  - patch_selector.py 选择最优
  - 应用最优补丁
  - 失败则回滚并尝试 next candidate

Phase 8: Stability Gate
  - stability_runner.py --mode full-gate --runs 3
  - forbidden_change_guard.py --strict
  - contract_checker.py
  - generated tests

Phase 9: Report
  - result/output.md
  - 修复报告.md
  - failure_taxonomy.md
```

---

## 23. Codex / Opencode 开发指令

可以直接把以下内容交给 Codex / Opencode：

```text
请继续修复 Goal-Agent 的 spec-driven-refactor 分支。当前分支方向正确，但还没有形成完整的 spec-driven repair engine。不要回退架构，不要继续堆文档，优先补工程闭环。

请按以下 P0 顺序修改：

1. 修复 work/tools/scripts/exception_analyzer.py：
   - 删除或修复 code_dir / "**" / "*.java" 的错误读取逻辑。
   - 保证脚本可运行并生成 exception_coverage.json。

2. 修复 work/tools/scripts/api_contract_builder.py：
   - 重写 markdown 表格字段解析。
   - 正确识别字段名、类型、是否必填、说明。
   - 支持中文和英文表头。
   - required 字段必须准确输出 true/false。

3. 修复 work/tools/scripts/dto_analyzer.py：
   - 不要把每个 endpoint 字段和所有 DTO 对比。
   - 必须通过 endpoint -> request_body_type -> DTO 关系只检查对应 DTO。
   - 避免 validation gap false positive。

4. 强化 work/tools/scripts/contract_checker.py：
   - check_endpoint_errors 不能是空实现。
   - 必须读取 exception_coverage.json 检查 400/404/409 handler。
   - 必须检查错误响应 code/message。
   - 强化 response schema checker，支持 ApiResponse<T>、ResponseEntity<T>、Result<T>、List<T>、PageResult<T>。
   - trace_matrix 必须能根据 issues 判断 implemented / partial / missing / conflict。

5. 新增 work/tools/scripts/repair_task_builder.py：
   - 输入 api_contract、business_rules、repo_map、dto_validation_report、exception_coverage、consistency_report、test_symptoms。
   - 输出 .agent-work/repair_tasks.json 和 repair_tasks.md。
   - 支持 validation、api_schema、response_schema、error_handling、business_rule、repository_query、pagination、sorting 等类型。
   - 必须去重并按 P0/P1/P2 排序。

6. 新增 work/tools/scripts/candidate_sandbox.py：
   - 每个 candidate patch 在独立 workspace 验证。
   - 运行 compile、code tests、public tests、generated tests、contract checker、forbidden guard。
   - 输出 .agent-work/candidate_validation.jsonl。
   - 编译失败、guard blocker、contract P0 均淘汰。

7. 新增 work/tools/scripts/patch_selector.py：
   - 根据 candidate_validation.jsonl 打分。
   - 评分公式：40% public tests + 25% generated tests + 15% contract checker + 10% diff 最小化 + 10% stability。
   - 输出 selected_patch.json。

8. 强化 work/tools/scripts/spec_test_generator.py：
   - success status 从 contract 读取，不固定 200。
   - response jsonPath 从 contract 读取，不固定 $.data。
   - error format tests 必须有真实断言。
   - GET/DELETE 不要无条件跳过。
   - 增加 dry-run compile，无法编译的 generated tests 不参与评分。

9. 强化 work/tools/scripts/stability_runner.py：
   - 支持 --mode full-gate。
   - 每轮执行 code tests、code install、public tests、generated tests、contract checker、forbidden guard。
   - 3 轮全过才 stable=true。

10. 调整 INSTRUCTION.md 和 work/skill/SKILL.md：
    - 主流程只保留 spec-driven agents。
    - legacy agents 降级为 fallback，不要作为主路径主动调用。
    - 明确 repair_task_builder、candidate_sandbox、patch_selector 是必跑步骤。

要求：
- 不修改 design-docs/**。
- 不修改 README.md 中 API 基线。
- 不修改 test-cases/**。
- 不硬编码公开测试。
- 不吞异常。
- 不统一返回 200。
- 不删除 @Valid、@Transactional、Repository 过滤条件。
- 优先最小 diff。
- 每个新脚本必须支持 --root 参数。
- 每个新脚本必须输出 JSON 和 Markdown 摘要。
- 每个新脚本必须在缺少输入时给出清晰 warning，而不是崩溃。
```

---

## 24. 验收标准

### 24.1 脚本验收

以下命令必须可运行：

```bash
python3 work/tools/scripts/api_contract_builder.py --root .
python3 work/tools/scripts/business_rule_builder.py --root .
python3 work/tools/scripts/spring_scanner.py --root .
python3 work/tools/scripts/dto_analyzer.py --root .
python3 work/tools/scripts/exception_analyzer.py --root .
python3 work/tools/scripts/contract_checker.py --root .
python3 work/tools/scripts/repair_task_builder.py --root .
python3 work/tools/scripts/spec_test_generator.py --root .
python3 work/tools/scripts/candidate_sandbox.py --root . --help
python3 work/tools/scripts/patch_selector.py --root . --help
python3 work/tools/scripts/forbidden_change_guard.py --root .
python3 work/tools/scripts/stability_runner.py --root . --mode full-gate --runs 3
```

### 24.2 输出文件验收

必须生成：

```text
.agent-work/api_contract.json
.agent-work/business_rules.json
.agent-work/repo_map.json
.agent-work/dto_validation_report.json
.agent-work/exception_coverage.json
.agent-work/consistency_report.json
.agent-work/trace_matrix.json
.agent-work/repair_tasks.json
.agent-work/generated_tests_manifest.json
.agent-work/candidate_validation.jsonl
.agent-work/selected_patch.json
.agent-work/forbidden_change_report.json
.agent-work/stability_report.json
```

### 24.3 质量验收

```text
1. api_contract.json 中 required 字段准确。
2. dto_validation_report.json 无大规模无关 DTO 误报。
3. exception_coverage.json 不崩溃。
4. consistency_report.json 中 error handling issue 可检测。
5. trace_matrix.json 能区分 partial/conflict。
6. generated tests 至少能 dry-run compile。
7. candidate_sandbox 能淘汰编译失败候选。
8. patch_selector 能输出最优 candidate。
9. stability_runner full-gate 能连续跑 3 次。
10. forbidden_change_guard 能拦截非法修改。
```

### 24.4 比赛效果验收

重新跑 72 个 case 后：

```text
第一阶段目标：失败 case 从 18 个降到 <= 14 个。
第二阶段目标：失败 case 从 18 个降到 <= 10 个。
理想目标：失败 case 从 18 个降到 <= 7 个。
```

同时要求：

```text
1. 每个失败 case 能归因。
2. 每个失败 case 能映射到 repair task。
3. 每个 repair task 有 candidate patch 验证记录。
4. 最终补丁可以稳定重复运行。
```

---

## 25. 最终结论

当前 `spec-driven-refactor` 分支方向是正确的，保留并继续推进。

但当前版本还不是完整的 spec-driven repair engine，而是一个具备雏形的 spec-driven pipeline。要真正提升黑箱通过率和稳定率，必须补齐以下核心闭环：

```text
repair_task_builder
candidate_sandbox
patch_selector
完整 error checker
可靠 generated tests
完整 stability gate
```

优先级最高的不是继续增加 subagent 文档，而是把多候选补丁验证、评分、选择、回滚变成确定性工程能力。

修复完成后，Goal-Agent 才能从“Agent 按文档修代码”升级为：

```text
规格驱动定位
测试增强验证
多候选补丁竞争
确定性安全门禁
稳定性重跑
失败归因沉淀
```

这是提升设计实现一致性检查和一致性修复通过率的关键。
