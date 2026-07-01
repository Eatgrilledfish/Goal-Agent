# Goal-Agent `spec-driven-refactor` 最新代码增强后修复文档

## 1. 文档目的

本文档基于 `Eatgrilledfish/Goal-Agent` 仓库 `spec-driven-refactor` 分支的最新一轮更新审查结果编写。

本轮分支相对上一版已经有明显增强，已经从“规格驱动 pipeline 雏形”推进到“spec-driven repair engine 初版”。但当前版本仍存在若干运行级缺陷和工程闭环缺口，尤其集中在：

```text
1. candidate_sandbox.py 的运行 bug。
2. candidate_sandbox.py 还不是真正独立 sandbox。
3. generated tests 还没有真正进入 candidate scoring 和 stability gate。
4. spec_test_generator.py 存在可能生成不可编译断言的问题。
5. api_contract_builder.py 表格分隔行可能被误解析成字段。
6. patch_selector.py 的 patch_file 路径可能与真实 candidate patch 不一致。
```

本文档目标是把这些问题整理为可直接交给 Codex / Opencode / Claude Code 执行的修复任务。

---

## 2. 当前分支整体结论

当前分支：`spec-driven-refactor`

最新判断：

```text
上一版：spec-driven pipeline 雏形
当前版：spec-driven repair engine 初版
能力增强幅度：明显增强
对原设计文档遵循度：约 8 / 10
工程闭环完整度：约 7 / 10
是否建议直接作为最终比赛版：暂不建议
```

当前版本已经补齐了上一版最关键的三个脚本：

```text
work/tools/scripts/repair_task_builder.py
work/tools/scripts/candidate_sandbox.py
work/tools/scripts/patch_selector.py
```

同时以下脚本也有实质增强：

```text
work/tools/scripts/api_contract_builder.py
work/tools/scripts/dto_analyzer.py
work/tools/scripts/contract_checker.py
work/tools/scripts/spec_test_generator.py
work/tools/scripts/stability_runner.py
```

当前版本已经具备以下核心能力雏形：

```text
API Contract → Business Rules → Repo Map
→ Consistency Report → Trace Matrix
→ Repair Tasks → Candidate Patch Validation
→ Patch Scoring → Stability Gate
```

但仍需完成最后一轮工程加固，才能用于稳定比赛。

---

## 3. 已经增强的能力

### 3.1 已新增 `repair_task_builder.py`

当前版本已新增：

```text
work/tools/scripts/repair_task_builder.py
```

该脚本能够从以下来源构建修复任务：

```text
.agent-work/consistency_report.json
.agent-work/dto_validation_report.json
.agent-work/exception_coverage.json
.agent-work/test_symptoms.jsonl
```

输出：

```text
.agent-work/repair_tasks.json
.agent-work/repair_tasks.md
```

当前能力包括：

```text
1. 定义 validation、api_schema、response_schema、error_handling、business_rule、repository_query、pagination、sorting 等任务类型。
2. 定义 P0/P1/P2 issue 类型集合。
3. 能从 consistency_report 转换 repair task。
4. 能从 DTO validation gap 转换 repair task。
5. 能从 exception coverage gap 转换 repair task。
6. 能从 test symptoms 转换 repair task。
7. 能按 related_api + field + type 去重。
8. 能按 P0 > P1 > P2 排序。
```

这是非常关键的进步。系统现在不再完全依赖 Agent 自行总结“该修什么”，而是有了确定性的 repair task 队列。

---

### 3.2 已新增 `candidate_sandbox.py`

当前版本已新增：

```text
work/tools/scripts/candidate_sandbox.py
```

设计目标是验证每个候选补丁：

```text
compile → code tests → public tests → generated tests → contract checker → forbidden guard
```

当前已经具备：

```text
1. 读取 .agent-work/candidate_patches.jsonl。
2. 支持按 task-id 过滤。
3. 检查 forbidden path。
4. 检查疑似统一返回 200。
5. 检查疑似吞异常。
6. git apply --check。
7. git apply。
8. mvn -f code/pom.xml compile。
9. mvn -f code/pom.xml test。
10. mvn -f code/pom.xml install -DskipTests。
11. mvn -f test-cases/pom.xml test。
12. contract_checker.py。
13. forbidden_change_guard.py --strict。
14. 输出 .agent-work/candidate_validation.jsonl。
```

这是从“线性修复”升级到“候选补丁验证”的关键一步。

---

### 3.3 已新增 `patch_selector.py`

当前版本已新增：

```text
work/tools/scripts/patch_selector.py
```

它已经实现了评分公式：

```text
score =
  40% public test pass rate
+ 25% generated test pass rate
+ 15% contract checker pass
+ 10% diff minimization
+ 10% stability score
```

输出：

```text
.agent-work/selected_patch.json
.agent-work/selected_patch.md
```

这是从“候选验证”进入“候选竞争和补丁选择”的关键一步。

---

### 3.4 `contract_checker.py` 已明显增强

当前版本 `contract_checker.py` 已新增或增强：

```text
1. route existence check。
2. request field check。
3. response field check。
4. response type unwrap。
5. error code check。
6. endpoint error handler check。
7. exception anti-pattern check。
8. trace matrix status 判断。
```

其中 `unwrap_response_type()` 已支持：

```text
ResponseEntity<ApiResponse<T>>
ApiResponse<List<T>>
Result<PageResult<T>>
CommonResponse<T>
R<T>
BaseResponse<T>
Page<T>
PageResult<T>
IPage<T>
Slice<T>
List<T>
Set<T>
Collection<T>
```

`check_endpoint_errors()` 已能根据 API contract 中 documented errors 检查：

```text
400 → MethodArgumentNotValidException / ConstraintViolationException / BindException / HttpMessageNotReadableException
404 → EntityNotFoundException / ResourceNotFoundException / NotFoundException / NoSuchElementException
409 → ConflictException / DuplicateException / DataIntegrityViolationException
```

trace matrix 也已经不再是“endpoint 存在就 implemented”，而是根据 issue 判断：

```text
implemented
partial
missing
conflict
```

---

### 3.5 `dto_analyzer.py` 已修复全量 DTO 误匹配问题

上一版问题是：每个 endpoint 字段会和所有 DTO 对比，导致大量 false positive。

当前版本已经改成：

```text
endpoint → request_body_type → 对应 DTO → 对应字段
```

这是重要修复。它能显著降低误报，避免 Agent 修错 DTO。

---

### 3.6 `api_contract_builder.py` 已增强 required 解析

当前版本新增：

```text
extract_markdown_tables()
find_col()
parse_required()
extract_fields_from_nearby_table()
```

支持中文/英文表头：

```text
字段 / 字段名 / 名称 / field / name / 参数名
类型 / type
是否必填 / 必填 / required / 是否必须
说明 / 描述 / description
```

这能提升 required/null/blank 类隐藏 case 的识别能力。

---

### 3.7 `spec_test_generator.py` 已明显增强

当前版本已经支持：

```text
1. success status 从 contract 读取。
2. response jsonPath 从 contract response body 读取。
3. error format test 生成真实断言。
4. GET endpoint 生成 not-found、schema、pagination 测试。
5. DELETE endpoint 生成 not-found、success 测试。
6. 支持 --dry-run-compile。
7. 输出 generated_tests_manifest.json。
```

这比上一版的测试草稿强很多。

---

### 3.8 `stability_runner.py` 已升级为 full-gate

当前版本已新增：

```text
--mode public-only
--mode full-gate
```

默认模式：

```text
full-gate
```

full-gate 当前包含：

```text
1. mvn -f code/pom.xml test
2. mvn -f code/pom.xml install -DskipTests
3. mvn -f test-cases/pom.xml test
4. contract_checker.py
5. forbidden_change_guard.py --strict
```

这比上一版只跑 public tests 强很多。

---

## 4. 当前剩余 P0 问题

以下问题必须马上修复，否则最新增强能力无法稳定发挥。

---

## 5. P0-1：修复 `candidate_sandbox.py` 缺少 `import re`

### 5.1 问题描述

当前文件顶部 imports 中没有：

```python
import re
```

但脚本中使用了：

```python
re.search(...)
re.DOTALL
```

典型位置：

```python
def check_uniform_200(patch_content: str) -> bool:
    risky_patterns = [
        r"return\s+(?:new\s+)?\w+\s*\(\s*(?:200|HttpStatus\.OK|ok\(\))",
        r"status\(\s*(?:200|HttpStatus\.OK|ok\(\))\s*\)",
    ]
    return any(re.search(pattern, patch_content) for pattern in risky_patterns)
```

以及：

```python
def check_exception_swallow(patch_content: str) -> bool:
    swallow_pattern = r"catch\s*\(\s*(?:Exception|Throwable|RuntimeException)\s+\w+\s*\)\s*\{[^}]*\}"
    return bool(re.search(swallow_pattern, patch_content, re.DOTALL))
```

这会导致：

```text
NameError: name 're' is not defined
```

### 5.2 修复要求

在 `candidate_sandbox.py` 顶部加入：

```python
import re
```

建议放在：

```python
import json
import re
import subprocess
```

### 5.3 验收命令

```bash
python3 work/tools/scripts/candidate_sandbox.py --root . --help
python3 work/tools/scripts/candidate_sandbox.py --root .
```

如果没有 candidate 文件，可以返回 no candidate/skipped，但不能因为 `re` 报错。

---

## 6. P0-2：将 `candidate_sandbox.py` 改成真正独立 sandbox

### 6.1 问题描述

当前 `candidate_sandbox.py` 虽然文档写的是 isolated sandbox，但实际是在 `PROJECT_ROOT` 中直接：

```bash
git apply --check <patch>
git apply <patch>
```

然后用：

```bash
git checkout -- .
```

回滚。

这不是严格独立 sandbox。

风险：

```text
1. git checkout -- . 不会删除 untracked 文件。
2. 新增文件可能残留。
3. generated tests 复制到 code/src/test/java/generated 后可能残留。
4. 上一个 candidate 的残留可能影响下一个 candidate。
5. 如果中途异常退出，主工作区可能被污染。
6. 多候选验证不能并发。
```

### 6.2 修复目标

每个 candidate 必须在独立目录中验证。

最终结构建议：

```text
.tmp/candidates/
└── TASK-001/
    ├── candidate-1/
    ├── candidate-2/
    ├── candidate-3/
    ├── candidate-4/
    └── candidate-5/
```

### 6.3 推荐实现方案 A：使用 `git worktree`

优先推荐：

```bash
git worktree add .tmp/candidates/TASK-001/candidate-1 HEAD
```

验证完成后：

```bash
git worktree remove --force .tmp/candidates/TASK-001/candidate-1
```

优点：

```text
1. 快。
2. 干净。
3. 与 Git 状态隔离。
4. 不污染主工作区。
```

### 6.4 推荐实现方案 B：使用 `shutil.copytree`

如果环境不支持 git worktree，则使用：

```python
shutil.copytree(root, sandbox_dir, ignore=ignore_patterns)
```

忽略：

```text
.git
.tmp
.agent-work/candidates
target
node_modules
```

### 6.5 伪代码

```python
def create_candidate_workspace(root: Path, task_id: str, candidate_id: str) -> Path:
    sandbox_root = root / ".tmp" / "candidates" / task_id / candidate_id
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root)

    result = subprocess.run(
        ["git", "worktree", "add", str(sandbox_root), "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    if result.returncode == 0:
        return sandbox_root

    # fallback copytree
    shutil.copytree(
        root,
        sandbox_root,
        ignore=shutil.ignore_patterns(".git", ".tmp", "target", "node_modules")
    )
    return sandbox_root
```

### 6.6 验证逻辑改造

当前：

```python
validate_candidate(root, candidate, timeout)
```

应改为：

```python
sandbox = create_candidate_workspace(root, task_id, candidate_id)
validate_candidate_in_workspace(sandbox, candidate, timeout)
cleanup_candidate_workspace(sandbox)
```

### 6.7 关键要求

```text
1. 主 PROJECT_ROOT 不直接 git apply candidate patch。
2. 所有 Maven 命令在 sandbox cwd 中执行。
3. contract_checker.py、forbidden_change_guard.py 在 sandbox 中执行。
4. candidate_validation.jsonl 仍写回主 PROJECT_ROOT/.agent-work。
5. 无论成功失败都清理 sandbox。
6. 如果清理失败，记录 warning。
```

---

## 7. P0-3：让 `candidate_sandbox.py` 真正运行 generated tests

### 7.1 问题描述

当前逻辑：

```python
generated_dir = root / ".tmp" / "generated-tests"
if generated_dir.exists():
    result["generated_tests"] = "SKIPPED"
    result["score_inputs"]["generated_test_pass_rate"] = 0.5
else:
    result["generated_tests"] = "NONE"
```

这意味着：

```text
1. generated tests 没有真正运行。
2. patch_selector 25% generated tests 权重只是默认值 0.5。
3. 不能识别“public tests 过了但 generated tests 失败”的错误补丁。
4. 测试增强能力没有真正进入候选补丁选择。
```

### 7.2 修复目标

candidate sandbox 必须真正执行可编译的 generated tests。

### 7.3 输入来源

读取：

```text
.agent-work/generated_tests_manifest.json
.tmp/generated-tests/generated_tests.json
```

其中 test class 记录类似：

```json
{
  "test_class": "POSTApiProductsContractTest",
  "file": ".tmp/generated-tests/POSTApiProductsContractTest.java",
  "compilable": true
}
```

### 7.4 执行步骤

在 candidate sandbox 中：

```text
1. 读取 generated_tests_manifest.json。
2. 过滤 compilable=true 的 test classes。
3. 将测试文件复制到 sandbox/code/src/test/java/generated/。
4. 运行 mvn -f code/pom.xml -Dtest=<GeneratedTestClassList> test。
5. 解析 Tests run / Failures / Errors。
6. 计算 generated_test_pass_rate。
7. 写入 candidate_validation.jsonl。
```

### 7.5 推荐命令

如果 generated class 数量较少：

```bash
mvn -s maven-settings.xml -f code/pom.xml -Dtest=ClassA,ClassB,ClassC test
```

如果数量较多：

```bash
mvn -s maven-settings.xml -f code/pom.xml -Dtest=*ContractTest test
```

### 7.6 输出格式

```json
{
  "generated_tests": "PASS",
  "generated_test_summary": {
    "tests_run": 24,
    "failures": 0,
    "errors": 0,
    "skipped": 0,
    "pass_rate": 1.0
  },
  "score_inputs": {
    "generated_test_pass_rate": 1.0
  }
}
```

如果没有可编译测试：

```json
{
  "generated_tests": "NONE",
  "generated_test_summary": {
    "reason": "No compilable generated tests"
  },
  "score_inputs": {
    "generated_test_pass_rate": 0.5
  }
}
```

### 7.7 注意事项

```text
1. generated tests 失败不一定直接淘汰 candidate。
2. 但 generated_test_pass_rate 必须真实进入评分。
3. 如果 generated tests 编译失败，应标记 generated_tests=UNUSABLE，不参与强淘汰。
4. 如果 generated tests 全失败，应降低 candidate score。
```

---

## 8. P0-4：让 `stability_runner.py` full-gate 真正运行 generated tests

### 8.1 问题描述

当前 `stability_runner.py` full-gate 中：

```python
if gen_manifest.get("compilable_count", 0) > 0:
    gate["generated_tests"] = "SKIPPED"
else:
    gate["generated_tests"] = "NONE"
```

这意味着 full-gate 仍没有真正执行 generated tests。

### 8.2 修复要求

将 `generated_tests` 从 SKIPPED 改为真实执行。

### 8.3 推荐函数

新增：

```python
def run_generated_tests(root: Path, timeout: int) -> dict[str, Any]:
    ...
```

逻辑：

```text
1. 读取 .agent-work/generated_tests_manifest.json。
2. 找到 compilable=true 的 test classes。
3. 如果没有，返回 NONE。
4. 将 generated tests 复制到 code/src/test/java/generated。
5. mvn -f code/pom.xml -Dtest=<classes> test。
6. 返回 PASS/FAIL 和 pass_rate。
```

### 8.4 full-gate 输出

```json
{
  "run": 1,
  "code_tests": "PASS",
  "code_install": "PASS",
  "public_tests": "PASS",
  "generated_tests": "PASS",
  "contract_checker": "PASS",
  "forbidden_guard": "PASS"
}
```

### 8.5 稳定性判断

当前：

```python
all_passed = all("FAIL" not in [v for k, v in g.items() if k != "run"] for g in gate_results)
```

可保留，但需要确保 generated tests 如果失败，必须写入：

```text
generated_tests = "FAIL"
```

---

## 9. P0-5：修复 `spec_test_generator.py` 多成功状态码断言生成问题

### 9.1 问题描述

当前 `status_assertion()` 对多个 success status 会返回：

```java
hasStatusCode(status -> assertTrue(java.util.List.of(200, 201).contains(status), "Expected one of [200, 201]"))
```

但生成测试中使用方式是：

```java
.andExpect(status().{status_check})
```

最终可能生成：

```java
.andExpect(status().hasStatusCode(...))
```

`MockMvcResultMatchers.status()` 返回的 `StatusResultMatchers` 并没有这个通用方法，容易导致编译失败。

### 9.2 修复方案

将状态断言函数拆成两类：

```python
def status_assertion_chain(status_codes: list[int]) -> str:
    """For single status only, returns isOk(), isCreated(), etc."""

def status_assertion_line(status_codes: list[int]) -> str:
    """Returns complete .andExpect(...) line."""
```

推荐实现：

```python
def status_expect_line(status_codes: list[int]) -> str:
    if not status_codes:
        return ".andExpect(status().isOk())"

    if len(status_codes) == 1:
        code = status_codes[0]
        status_map = {
            200: "isOk()",
            201: "isCreated()",
            202: "isAccepted()",
            204: "isNoContent()",
            400: "isBadRequest()",
            404: "isNotFound()",
            409: "isConflict()",
        }
        if code in status_map:
            return f".andExpect(status().{status_map[code]})"
        return f".andExpect(status().is({code}))"

    codes_str = ", ".join(str(c) for c in status_codes)
    return (
        ".andExpect(result -> org.junit.jupiter.api.Assertions.assertTrue("
        f"java.util.List.of({codes_str}).contains(result.getResponse().getStatus()), "
        f"\"Expected one of [{codes_str}]\"))"
    )
```

然后生成时不要再写：

```java
.andExpect(status().{status_check})
```

改为直接插入：

```java
{status_expect_line(success_status)}
```

### 9.3 验收

如果 success_status 为：

```json
[200, 201]
```

生成代码应为：

```java
.andExpect(result -> org.junit.jupiter.api.Assertions.assertTrue(
    java.util.List.of(200, 201).contains(result.getResponse().getStatus()),
    "Expected one of [200, 201]"
))
```

而不是：

```java
.andExpect(status().hasStatusCode(...))
```

---

## 10. P0-6：修复 `api_contract_builder.py` Markdown 表格分隔行误解析

### 10.1 问题描述

当前 `extract_markdown_tables()` 会收集 Markdown 表格行，包括分隔行：

```markdown
|---|---|---|
```

后续 `extract_fields_from_nearby_table()` 遍历 `table[1:]` 时，如果不跳过 separator row，可能生成伪字段：

```text
field_name = "---"
field_type = "---"
```

这会污染 api_contract.json。

### 10.2 修复要求

在遍历 row 时跳过分隔行：

```python
def is_separator_row(row: list[str]) -> bool:
    return all(re.match(r"^[-: ]+$", c.strip()) for c in row if c.strip())
```

在 `for row in table[1:]:` 后加入：

```python
if is_separator_row(row):
    continue
```

### 10.3 验收样例

输入：

```markdown
| 字段名 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| name | string | 是 | 商品名称 |
| price | decimal | 是 | 商品价格 |
```

输出中不应出现：

```json
"---": {
  "type": "---"
}
```

---

## 11. P0-7：修复 `patch_selector.py` patch_file 路径推断问题

### 11.1 问题描述

当前 `patch_selector.py` 输出 patch_file 时使用推断路径：

```python
"patch_file": f".agent-work/patches/{best['task_id']}-{best['candidate_id']}.patch"
```

问题：

```text
1. candidate 原始 patch_file 不一定是这个路径。
2. patch-generator 可能输出不同目录或不同命名。
3. selected_patch.json 可能指向不存在的 patch。
```

### 11.2 修复要求

`candidate_sandbox.py` 在 validation result 中保留原始 candidate patch_file：

```python
result["patch_file"] = candidate.get("patch_file", "")
```

`patch_selector.py` 在 scored candidate 中保留 patch_file：

```python
"patch_file": candidate.get("patch_file", "")
```

最终 selected 输出：

```python
"patch_file": best.get("patch_file", "")
```

如果 patch_file 缺失，则 fallback 到推断路径，并记录 warning。

### 11.3 输出格式

```json
{
  "task_id": "TASK-001",
  "selected_candidate": "candidate-1",
  "score": 0.942,
  "patch_file": ".agent-work/patches/TASK-001-candidate-1.patch",
  "patch_file_source": "candidate_validation"
}
```

---

## 12. P1 问题：进一步增强但非阻塞

以下问题不是立即阻塞，但建议 P0 修完后继续做。

---

## 13. P1-1：`candidate_sandbox.py` 增加新文件清理

如果暂时不改成真 sandbox，至少需要在每次 candidate 验证后执行：

```bash
git reset --hard HEAD
git clean -fd
```

当前只有：

```bash
git checkout -- .
```

这不够。

推荐最终还是用真 sandbox；如果短期过渡，至少改成：

```python
subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=root, check=False)
subprocess.run(["git", "clean", "-fd"], cwd=root, check=False)
```

---

## 14. P1-2：`spec_test_generator.py` 测试数据 setup 仍不足

当前 generated tests 中很多 ID 固定为：

```text
1
999999
```

风险：

```text
1. id=1 不一定存在。
2. categoryId=1 不一定存在。
3. DELETE existing test 可能失败。
4. GET response schema test 可能失败。
```

后续增强建议：

```text
1. 如果是 create/list 类 API，优先先 create 再 query。
2. 如果需要 FK，如 categoryId，先通过 list categories 找一个存在 ID。
3. 如果无法 setup，则把该测试标记为 pseudo-test，不参与强评分。
4. generated tests 分为 executable 和 advisory 两类。
```

---

## 15. P1-3：`contract_checker.py` response schema 仍需改进

当前 response checker 已经能 unwrap response type，但仍有局限：

```text
1. wrapper 字段 code/message/data 未必在 dto_fields 中，因为 ApiResponse 可能不是 DTO。
2. data.id 判断依赖 inner_type，但 response type 提取可能不准确。
3. PageResult 的 metadata 可能在 wrapper 中，不在 inner DTO 中。
4. Result<PageResult<ProductVO>> 的两层 nested unwrap 还可能不完整。
```

后续建议：

```text
1. 建立 response wrapper registry。
2. 扫描 ApiResponse/Result/R/PageResult 类字段。
3. 对 data.items.*、data.records.*、data.content.* 建立统一映射。
4. 对分页字段支持 total、totalElements、totalPages、page、pageNum、pageSize、size、records、items、content。
```

---

## 16. P1-4：`repair_task_builder.py` suspected_files 仍有占位路径

DTO validation task 当前可能输出：

```text
code/.../{dto}.java
```

这是占位路径，不利于 patch-generator 精确修改。

应改为从 `dto_validation_report.json` 中带出真实文件路径。

要求：

```json
{
  "suspected_files": [
    "code/src/main/java/com/example/product/dto/ProductCreateRequest.java"
  ]
}
```

如果 DTO report 中没有 file 字段，应补充 DTO analyzer 输出。

---

## 17. P1-5：`contract_checker.py` 返回码策略需要区分 warning 与 blocker

当前 `contract_checker.py` 如果有 P0 issue，会返回非 0。

这适合 gate，但不适合 candidate_sandbox 中判断“新 P0”。因为 candidate_sandbox 现在只能看到当前 P0 数量，无法区分 baseline 已有 P0 和 candidate 新增 P0。

建议：

```text
1. baseline 阶段保存 .agent-work/baseline_consistency_report.json。
2. candidate sandbox 中比较 candidate P0 是否比 baseline 增加。
3. 如果没有新增 P0，可以不淘汰，但评分降低。
4. 如果新增 P0，直接淘汰。
```

---

## 18. 推荐下一步修复顺序

不要继续扩文档，按下面顺序修代码：

```text
1. candidate_sandbox.py 加 import re。
2. api_contract_builder.py 跳过 markdown separator row。
3. spec_test_generator.py 修复多 success status 断言生成。
4. candidate_sandbox.py 保留 patch_file 到 validation result。
5. patch_selector.py 使用真实 patch_file。
6. candidate_sandbox.py 使用 git worktree/copytree 真 sandbox。
7. candidate_sandbox.py 真运行 generated tests。
8. stability_runner.py full-gate 真运行 generated tests。
9. repair_task_builder.py suspected_files 输出真实路径。
10. contract_checker.py 区分 baseline P0 与 candidate 新增 P0。
```

---

## 19. Codex / Opencode 开发指令

可以直接把以下内容交给 Codex / Opencode：

```text
请继续修复 Goal-Agent 的 spec-driven-refactor 分支。当前分支已经从 spec-driven pipeline 雏形增强为 spec-driven repair engine 初版，已经新增 repair_task_builder.py、candidate_sandbox.py、patch_selector.py。不要回退这些架构，不要继续扩文档，优先修运行级缺陷和工程闭环。

请按以下顺序修改：

1. 修复 work/tools/scripts/candidate_sandbox.py：
   - 添加 import re。
   - 保证 check_uniform_200 和 check_exception_swallow 不再 NameError。

2. 修复 work/tools/scripts/api_contract_builder.py：
   - 在 extract_fields_from_nearby_table 遍历 markdown table rows 时跳过 separator row。
   - 不允许把 |---|---| 解析成字段。

3. 修复 work/tools/scripts/spec_test_generator.py：
   - 修改 success status 断言生成逻辑。
   - 多个成功状态码时，不要生成 status().hasStatusCode(...)。
   - 应生成完整 .andExpect(result -> Assertions.assertTrue(...))。
   - 如果使用 assertTrue，必须使用全限定名 org.junit.jupiter.api.Assertions.assertTrue，或者增加静态导入。

4. 修复 work/tools/scripts/candidate_sandbox.py：
   - validation result 中保留原始 candidate.patch_file。
   - 输出字段包括 patch_file、patch_file_exists、patch_file_source。

5. 修复 work/tools/scripts/patch_selector.py：
   - 不要硬编码推断 patch_file。
   - 优先使用 candidate_validation.jsonl 中的 patch_file。
   - 如果缺失，再 fallback 到推断路径，并写 warning。

6. 重构 work/tools/scripts/candidate_sandbox.py：
   - 每个 candidate 必须在独立 sandbox 中验证。
   - 优先使用 git worktree add .tmp/candidates/<task>/<candidate> HEAD。
   - 如果 worktree 失败，则 fallback 到 shutil.copytree。
   - 不允许在 PROJECT_ROOT 直接 git apply candidate patch。
   - 所有测试和 checker 都在 sandbox cwd 中执行。
   - 验证结果写回 PROJECT_ROOT/.agent-work/candidate_validation.jsonl。
   - 验证结束后清理 sandbox。

7. 增强 work/tools/scripts/candidate_sandbox.py：
   - 真正运行 generated tests。
   - 读取 .agent-work/generated_tests_manifest.json。
   - 找到 compilable=true 的 generated test classes。
   - 复制测试到 sandbox/code/src/test/java/generated。
   - 执行 mvn -f code/pom.xml -Dtest=<generated classes> test。
   - 解析 Tests run / Failures / Errors / Skipped。
   - 计算 generated_test_pass_rate。
   - 不要再固定设置 generated_test_pass_rate=0.5，除非没有可用 generated tests。

8. 增强 work/tools/scripts/stability_runner.py：
   - full-gate 模式真正运行 generated tests。
   - 逻辑与 candidate_sandbox 中 generated tests 执行保持一致。
   - generated tests 失败时，gate["generated_tests"]="FAIL"。
   - 3 轮 full-gate 全部 PASS 才 stable=true。

9. 增强 work/tools/scripts/repair_task_builder.py 和 dto_analyzer.py：
   - DTO validation task 的 suspected_files 必须是真实文件路径。
   - 不要输出 code/.../{dto}.java 这种占位路径。

10. 增强 work/tools/scripts/contract_checker.py：
    - 支持 baseline consistency 对比。
    - candidate_sandbox 中应区分已有 P0 和 candidate 新增 P0。
    - 新增 P0 直接淘汰，已有 P0 不一定淘汰但降低评分。

要求：
- 不修改 design-docs/**。
- 不修改 README.md 中 API 基线。
- 不修改 test-cases/**。
- 不硬编码公开测试。
- 不吞异常。
- 不统一返回 200。
- 不删除 @Valid、@Transactional、Repository 查询条件。
- 每个脚本必须支持 --root。
- 缺少输入时输出 warning，不要崩溃。
- 所有新增/修改脚本必须能通过 python -m py_compile。
```

---

## 20. 验收命令

修复完成后，至少执行以下命令：

```bash
python3 -m py_compile work/tools/scripts/candidate_sandbox.py
python3 -m py_compile work/tools/scripts/patch_selector.py
python3 -m py_compile work/tools/scripts/spec_test_generator.py
python3 -m py_compile work/tools/scripts/stability_runner.py
python3 -m py_compile work/tools/scripts/api_contract_builder.py
python3 -m py_compile work/tools/scripts/repair_task_builder.py
```

基础流程：

```bash
python3 work/tools/scripts/shophub_goal_runner.py --root . init
python3 work/tools/scripts/api_contract_builder.py --root .
python3 work/tools/scripts/business_rule_builder.py --root .
python3 work/tools/scripts/spring_scanner.py --root .
python3 work/tools/scripts/dto_analyzer.py --root .
python3 work/tools/scripts/exception_analyzer.py --root .
python3 work/tools/scripts/contract_checker.py --root .
python3 work/tools/scripts/repair_task_builder.py --root .
python3 work/tools/scripts/spec_test_generator.py --root . --dry-run-compile
```

候选补丁流程：

```bash
python3 work/tools/scripts/candidate_sandbox.py --root .
python3 work/tools/scripts/patch_selector.py --root .
```

稳定性门禁：

```bash
python3 work/tools/scripts/stability_runner.py --root . --mode full-gate --runs 3
python3 work/tools/scripts/forbidden_change_guard.py --root . --strict
```

完整 Maven 验证：

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

---

## 21. 验收输出文件

最终应生成：

```text
.agent-work/api_contract.json
.agent-work/business_rules.json
.agent-work/repo_map.json
.agent-work/dto_validation_report.json
.agent-work/exception_coverage.json
.agent-work/consistency_report.json
.agent-work/trace_matrix.json
.agent-work/repair_tasks.json
.agent-work/repair_tasks.md
.agent-work/generated_tests_manifest.json
.agent-work/candidate_validation.jsonl
.agent-work/candidate_validation.md
.agent-work/selected_patch.json
.agent-work/selected_patch.md
.agent-work/stability_report.json
.agent-work/08_stability_report.md
.agent-work/forbidden_change_report.json
result/output.md
```

---

## 22. 最终判断

最新代码能力确实明显增强。

当前分支已经从：

```text
文档驱动 + 初步脚本
```

升级为：

```text
repair task builder + candidate sandbox + patch selector + contract checker + generated tests + full-gate
```

这说明系统已经具备 spec-driven repair engine 的核心骨架。

但还不能直接作为最终比赛版，主要原因是：

```text
1. candidate_sandbox.py 有 import re 的硬 bug。
2. candidate_sandbox.py 还不是真独立 sandbox。
3. generated tests 还没有真正参与 candidate scoring。
4. stability full-gate 还没有真正执行 generated tests。
5. spec_test_generator.py 多成功状态码断言可能生成不可编译代码。
6. patch_selector.py 可能输出错误 patch_file 路径。
```

修完这些问题后，Goal-Agent 才会真正具备：

```text
规格驱动定位
候选补丁竞争
增强测试评分
独立沙箱验证
稳定性门禁
失败归因沉淀
```

这会比原始 Goal-Agent 更适合做设计实现一致性检查和一致性修复，也更有机会把黑箱通过率从当前 70%~75% 推到 80%~85% 以上。
