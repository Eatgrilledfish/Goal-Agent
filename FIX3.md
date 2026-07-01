# Goal-Agent `spec-driven-refactor` 最新统一审查与修复文档

## 1. 文档目的

本文档用于指导 Goal-Agent 项目在 `spec-driven-refactor` 分支上完成最后一轮比赛级加固。

当前分支已经完成了从普通 LLM 修复 Agent 到 **spec-driven repair engine 初版** 的升级，核心能力包括：

```text
API Contract
→ Business Rules
→ Repo Map
→ Consistency Check
→ Trace Matrix
→ Repair Tasks
→ Generated Tests
→ Candidate Patches
→ Candidate Sandbox
→ Patch Selection
→ Stability Gate
→ Report
```

本次文档重点不是重新设计架构，而是明确：

1. 当前最新代码已经完成了哪些关键能力。
2. 还剩哪些比赛入口和工程细节需要修。
3. `INSTRUCTION.md` 应如何作为比赛平台入口正确指导 opencode 执行作品。
4. 最终验收命令和交付产物是什么。

---

## 2. 当前最新代码总体判断

当前分支：`spec-driven-refactor`

审查结论：

```text
上一版状态：spec-driven pipeline 雏形
当前状态：spec-driven repair engine 初版

架构先进性：8.5 / 10
工程闭环完整度：8 / 10
比赛可运行性：7.5 / 10
继续增强潜力：高
```

当前代码已经明显强于普通“Agent 读文档、读代码、直接改代码”的方案。

当前版本已经具备：

```text
1. 规格抽取：api_contract_builder.py、business_rule_builder.py
2. 代码扫描：spring_scanner.py、dto_analyzer.py、exception_analyzer.py
3. 一致性检查：contract_checker.py
4. Trace Matrix：trace_matrix.json
5. 修复任务生成：repair_task_builder.py
6. 规格测试生成：spec_test_generator.py
7. 候选补丁验证：candidate_sandbox.py
8. 补丁评分选择：patch_selector.py
9. 禁止修改守卫：forbidden_change_guard.py
10. 稳定性门禁：stability_runner.py
```

---

## 3. 与先进实现和最新论文方向的对齐情况

当前实现已经对齐以下先进方向。

### 3.1 Generate-and-Validate 自动修复范式

当前系统不再是单一补丁直接应用，而是：

```text
repair task
→ 3~5 个 candidate patch
→ sandbox 验证
→ scoring
→ selected patch
→ stability gate
```

这与自动程序修复领域主流的 generate-and-validate 范式一致。

### 3.2 CI / Gate 驱动的仓库级验证

`stability_runner.py` 已经覆盖：

```text
code tests
code install
public tests
generated tests
contract checker
forbidden guard
```

这比只跑单一测试更接近真实 CI workflow，也更适合比赛黑箱验证。

### 3.3 多候选补丁竞争和评分

`candidate_sandbox.py` + `patch_selector.py` 已经形成：

```text
候选补丁验证
→ public test pass rate
→ generated test pass rate
→ contract checker pass
→ diff minimization
→ stability score
→ 综合评分
```

这与最新 LLM 修复系统中“避免单补丁过拟合、通过多候选竞争提升正确率”的方向一致。

### 3.4 Requirement-to-Code Traceability

当前 `api_contract.json`、`business_rules.json`、`repo_map.json`、`trace_matrix.json` 已经形成规格到代码的映射基础。

这比单纯让 LLM 搜索代码更稳定，能够把设计要求、API 契约、Controller、DTO、ExceptionHandler、Service、Repository 之间建立可追踪关系。

### 3.5 测试增强

`spec_test_generator.py` 已经能够根据 contract 生成：

```text
success status 测试
response schema 测试
null / blank / zero / negative 测试
error format 测试
GET not found 测试
DELETE not found 测试
pagination 测试
```

并通过 `--dry-run-compile` 标记生成测试是否可用。

### 3.6 仍未达到的最高形态

当前系统还没有完全达到最新论文中更前沿的形态，例如：

```text
1. 基于执行 trace 的失败诊断。
2. 函数/文件级 hierarchical code documentation。
3. 自动 question-driven debugging。
4. 对候选补丁做语义级 refinement。
5. 对失败 case 做长期知识沉淀和复用。
```

但作为比赛工程实现，当前骨架已经较先进，下一步不应继续大幅扩架构，而应确保入口文档、执行路径、输出目录和验证命令完全稳定。

---

## 4. 最新代码已修复的重要问题

### 4.1 `candidate_sandbox.py` 已修复 `import re`

之前 `candidate_sandbox.py` 使用 `re.search` 和 `re.DOTALL`，但没有 `import re`。

当前已经补齐：

```python
import re
```

该问题已修复。

---

### 4.2 `candidate_sandbox.py` 已改成独立 sandbox

当前版本已经不再直接污染主工作区，而是：

```text
优先：git worktree add .tmp/candidates/<task>/<candidate> HEAD
fallback：shutil.copytree(...)
```

验证完成后清理 sandbox：

```text
git worktree remove --force
或 shutil.rmtree
```

这是重要修复。

当前能力：

```text
1. 每个 candidate 使用独立 workspace。
2. patch 在 sandbox 中 apply。
3. 编译和测试在 sandbox 中执行。
4. contract checker 和 forbidden guard 在 sandbox 中执行。
5. 结果写回主 PROJECT_ROOT/.agent-work。
6. 验证完成后清理 sandbox。
```

---

### 4.3 `candidate_sandbox.py` 已真实运行 generated tests

当前版本已经不再固定：

```text
generated_test_pass_rate = 0.5
```

而是读取：

```text
.agent-work/generated_tests_manifest.json
```

复制可编译 generated tests 到 sandbox：

```text
sandbox/code/src/test/java/generated/
```

然后运行：

```bash
mvn -f code/pom.xml -Dtest=<generated test classes> test
```

并解析：

```text
Tests run
Failures
Errors
Skipped
pass_rate
```

这是从“测试草稿”到“测试评分 oracle”的关键升级。

---

### 4.4 `patch_selector.py` 已使用真实 patch_file

之前 `patch_selector.py` 硬编码推断：

```text
.agent-work/patches/<task>-<candidate>.patch
```

现在已经优先使用 `candidate_validation.jsonl` 中的真实 `patch_file`，只有缺失时才 fallback，并记录 warning。

该问题已修复。

---

### 4.5 `stability_runner.py` 已将 generated tests 纳入 full-gate

当前 `stability_runner.py` 已支持：

```text
--mode public-only
--mode full-gate
```

默认 full-gate，并执行：

```text
1. mvn -f code/pom.xml test
2. mvn -f code/pom.xml install -DskipTests
3. mvn -f test-cases/pom.xml test
4. generated tests
5. contract_checker.py
6. forbidden_change_guard.py --strict
```

这是非常重要的增强。

---

### 4.6 `spec_test_generator.py` 已修复多成功状态码断言

之前多个 success status 可能生成错误的：

```java
status().hasStatusCode(...)
```

当前已改成：

```java
.andExpect(result -> org.junit.jupiter.api.Assertions.assertTrue(
    java.util.List.of(200, 201).contains(result.getResponse().getStatus()),
    "Expected one of [200, 201]"
))
```

该问题已修复。

---

### 4.7 `api_contract_builder.py` 已跳过 Markdown 表格分隔行

当前已经新增：

```python
is_separator_row(row)
```

并在字段解析时跳过：

```markdown
|---|---|---|
```

避免把 `---` 解析成字段。

该问题已修复。

---

### 4.8 `repair_task_builder.py` 已尝试输出真实 DTO 文件路径

当前 `repair_task_builder.py` 已经支持通过 DTO 名称查找真实文件路径：

```python
find_dto_file(root, dto_name)
```

比之前的占位路径更好。

---

## 5. 当前仍建议修复的问题

当前代码主闭环已经基本补齐，但仍建议最后修以下几个点。

---

## 6. P0：重写 `INSTRUCTION.md`

### 6.1 问题描述

当前 `INSTRUCTION.md` 仍偏“说明性文档”，作为比赛入口还不够“机器可执行”。

主要问题：

```text
1. 脚本路径仍写成 python3 work/tools/scripts/...，没有统一使用 $WORK_ROOT。
2. Phase 6 没有明确运行 repair_task_builder.py。
3. Phase 7 没有明确运行 candidate_sandbox.py 和 patch_selector.py。
4. legacy subagent 仍在主文档中占比较大，可能误导 opencode 走旧流程。
5. 没有明确 selected_patch 如何应用。
6. 没有明确 baseline_consistency_report.json 如何生成。
7. 没有明确失败时 BLOCKED / STOPPED_BY_SAFETY 的停止条件。
8. 没有明确最终 output.md 必须写什么。
```

### 6.2 修复要求

用随本文提供的新版 `INSTRUCTION.md` 直接替换仓库根目录 `INSTRUCTION.md`。

新版入口文档必须具备：

```text
1. 明确 SUBMISSION_ROOT、PROJECT_ROOT、WORK_ROOT。
2. 所有脚本都使用 $WORK_ROOT/tools/scripts。
3. 明确 Phase 0 至 Phase 12。
4. 明确 repair_task_builder.py 是必跑步骤。
5. 明确 candidate_sandbox.py 是必跑步骤。
6. 明确 patch_selector.py 是必跑步骤。
7. 明确 selected patch 如何 git apply。
8. 明确 selected patch 失败如何回滚。
9. 明确 stability full-gate。
10. 明确最终输出格式。
```

---

## 7. P1：`.gitignore` 建议增加 `.tmp/`

当前 `.gitignore` 只忽略：

```text
__pycache__/
.agent-work/
```

建议增加：

```text
.tmp/
```

因为当前系统会在 `.tmp/` 下生成：

```text
.tmp/generated-tests/
.tmp/candidates/
```

这些不应提交。

修复：

```text
__pycache__/
.agent-work/
.tmp/
```

---

## 8. P1：确保 baseline consistency report 生成

`candidate_sandbox.py` 已经支持读取：

```text
.agent-work/baseline_consistency_report.json
```

用于判断 candidate 是否新增 P0 问题。

但需要确保在首次 `contract_checker.py` 后保存 baseline：

```bash
cp "$PROJECT_ROOT/.agent-work/consistency_report.json" \
   "$PROJECT_ROOT/.agent-work/baseline_consistency_report.json"
```

该命令已经写入新版 `INSTRUCTION.md`。

---

## 9. P1：generated tests 残留清理

`spec_test_generator.py --dry-run-compile` 会将 generated tests 复制到：

```text
code/src/test/java/generated/
```

建议后续增强：

```text
1. dry-run compile 后清理 generated 目录，或
2. 在 forbidden guard 中允许 generated 目录临时存在但不提交，或
3. 在 report 前执行清理。
```

当前不是阻塞项，因为 generated tests 是验证工具，但比赛提交时需要避免把 generated tests 作为最终修复依赖。

建议在 report 前检查：

```bash
git status --short
```

如果出现非预期 generated test 文件，需要清理。

---

## 10. P1：candidate_sandbox 中 baseline P0 比较仍需注意

当前逻辑是：

```text
candidate_p0 - baseline_p0
```

如果 baseline report 不存在，则 baseline_p0 = 0。

因此必须执行 Phase 3 中的 baseline 保存命令，否则原本已有 P0 可能导致 candidate 被误判为新增 P0。

新版 `INSTRUCTION.md` 已明确该命令。

---

## 11. 推荐最终修改清单

### 11.1 必改

```text
1. 用新版 INSTRUCTION.md 替换当前仓库根目录 INSTRUCTION.md。
2. .gitignore 增加 .tmp/。
3. 确认 Phase 3 后保存 baseline_consistency_report.json。
```

### 11.2 可选增强

```text
1. dry-run compile 后清理 generated tests。
2. result/output.md 中增加 candidate validation 和 patch selector 摘要。
3. repair_task_builder.py 输出更精确的 suspected_files。
4. contract_checker.py 增加 response wrapper registry。
5. repo_map 增加 hierarchical code documentation。
```

---

## 12. 推荐给 Codex / Opencode 的修复指令

可以直接给 Codex / Opencode：

```text
请继续修复 Goal-Agent 的 spec-driven-refactor 分支。当前代码主闭环已经基本完成，不要重构大架构，不要继续堆设计文档。重点完成比赛入口和最后的工程加固。

请执行以下修改：

1. 用新的 INSTRUCTION.md 完整替换仓库根目录 INSTRUCTION.md。
   - 新版 INSTRUCTION.md 必须面向比赛平台和 opencode。
   - 必须明确 SUBMISSION_ROOT、PROJECT_ROOT、WORK_ROOT。
   - 所有脚本路径必须使用 $WORK_ROOT/tools/scripts。
   - 必须明确运行 repair_task_builder.py、candidate_sandbox.py、patch_selector.py。
   - 必须明确 selected patch 如何应用和失败如何回滚。
   - 必须明确 baseline_consistency_report.json 如何保存。
   - 必须明确 output.md 和 修复报告.md 的最终内容。

2. 修改 .gitignore：
   - 增加 .tmp/。

3. 检查脚本可编译：
   - python3 -m py_compile work/tools/scripts/*.py

4. 执行完整 dry-run：
   - shophub_goal_runner.py init
   - api_contract_builder.py
   - business_rule_builder.py
   - spring_scanner.py
   - dto_analyzer.py
   - exception_analyzer.py
   - contract_checker.py
   - 保存 baseline_consistency_report.json
   - spec_test_generator.py --dry-run-compile
   - repair_task_builder.py
   - candidate_sandbox.py
   - patch_selector.py
   - stability_runner.py --mode full-gate --runs 3

5. 确保最终不会提交：
   - .agent-work/
   - .tmp/
   - 临时 generated tests

禁止：
- 修改 design-docs/**
- 修改 README.md 中 API 基线
- 修改 test-cases/**
- 硬编码公开测试
- 吞异常
- 统一返回 200
- 删除 @Valid、@Transactional 或 Repository 查询条件
```

---

## 13. 验收命令

### 13.1 Python 脚本编译

```bash
python3 -m py_compile work/tools/scripts/api_contract_builder.py
python3 -m py_compile work/tools/scripts/business_rule_builder.py
python3 -m py_compile work/tools/scripts/spring_scanner.py
python3 -m py_compile work/tools/scripts/dto_analyzer.py
python3 -m py_compile work/tools/scripts/exception_analyzer.py
python3 -m py_compile work/tools/scripts/contract_checker.py
python3 -m py_compile work/tools/scripts/repair_task_builder.py
python3 -m py_compile work/tools/scripts/spec_test_generator.py
python3 -m py_compile work/tools/scripts/candidate_sandbox.py
python3 -m py_compile work/tools/scripts/patch_selector.py
python3 -m py_compile work/tools/scripts/forbidden_change_guard.py
python3 -m py_compile work/tools/scripts/stability_runner.py
```

### 13.2 Spec-driven 主流程

```bash
export SUBMISSION_ROOT="$(pwd)"
export WORK_ROOT="$SUBMISSION_ROOT/work"
export PROJECT_ROOT="$SUBMISSION_ROOT"

python3 "$WORK_ROOT/tools/scripts/shophub_goal_runner.py" --root "$PROJECT_ROOT" init

python3 "$WORK_ROOT/tools/scripts/api_contract_builder.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/business_rule_builder.py" --root "$PROJECT_ROOT"

python3 "$WORK_ROOT/tools/scripts/spring_scanner.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/dto_analyzer.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/exception_analyzer.py" --root "$PROJECT_ROOT"

python3 "$WORK_ROOT/tools/scripts/contract_checker.py" --root "$PROJECT_ROOT" || true
cp "$PROJECT_ROOT/.agent-work/consistency_report.json" \
   "$PROJECT_ROOT/.agent-work/baseline_consistency_report.json"

python3 "$WORK_ROOT/tools/scripts/spec_test_generator.py" \
  --root "$PROJECT_ROOT" \
  --dry-run-compile

python3 "$WORK_ROOT/tools/scripts/repair_task_builder.py" --root "$PROJECT_ROOT"
```

### 13.3 候选补丁流程

```bash
python3 "$WORK_ROOT/tools/scripts/candidate_sandbox.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/patch_selector.py" --root "$PROJECT_ROOT"
```

### 13.4 稳定性门禁

```bash
python3 "$WORK_ROOT/tools/scripts/stability_runner.py" \
  --root "$PROJECT_ROOT" \
  --mode full-gate \
  --runs 3

python3 "$WORK_ROOT/tools/scripts/forbidden_change_guard.py" \
  --root "$PROJECT_ROOT" \
  --strict
```

### 13.5 Maven 验证

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

---

## 14. 最终交付文件

必须存在：

```text
INSTRUCTION.md
work/
result/output.md
logs/
```

运行后必须生成：

```text
.agent-work/api_contract.json
.agent-work/business_rules.json
.agent-work/repo_map.json
.agent-work/dto_validation_report.json
.agent-work/exception_coverage.json
.agent-work/consistency_report.json
.agent-work/baseline_consistency_report.json
.agent-work/trace_matrix.json
.agent-work/generated_tests_manifest.json
.agent-work/repair_tasks.json
.agent-work/candidate_validation.jsonl
.agent-work/selected_patch.json
.agent-work/stability_report.json
.agent-work/forbidden_change_report.json
result/output.md
修复报告.md
```

---

## 15. 最终判断

当前最新代码已经基本达到先进工程实现的骨架要求：

```text
规格抽取
Traceability
静态一致性检查
规格测试生成
修复任务队列
多候选补丁
独立 sandbox 验证
补丁评分选择
稳定性门禁
forbidden-change guard
```

现在最重要的不是继续增加新模块，而是让比赛平台通过 `INSTRUCTION.md` 能够稳定、无歧义地运行整套作品。

最终建议：

```text
1. 替换 INSTRUCTION.md。
2. .gitignore 增加 .tmp/。
3. 确认 baseline_consistency_report.json 保存逻辑。
4. 执行完整 dry-run。
5. 再提交最终版。
```
