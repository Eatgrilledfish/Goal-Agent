# Goal-Agent 运行入口（Spec-Driven Repair Engine）

本文件是作品运行入口。平台加载本 Markdown 后，请按本文直接运行作品，不得跳过任何必跑步骤。

---

## 目录定位

平台必须首先确定以下三个根目录：

```bash
# SUBMISSION_ROOT：本提交包根目录，包含本文件 INSTRUCTION.md 和 work/ 目录
export SUBMISSION_ROOT="$(pwd)"

# PROJECT_ROOT：待修复的目标题目仓库根目录，必须包含以下文件：
#   README.md
#   code/pom.xml
#   design-docs/
#   test-cases/pom.xml
#
# 如果当前工作目录已经包含这些文件，则 PROJECT_ROOT=$SUBMISSION_ROOT。
# 否则在当前目录、父目录、兄弟目录中查找满足上述结构的目录。
export PROJECT_ROOT="$SUBMISSION_ROOT"   # 或由平台注入

# WORK_ROOT：作品包中 work/ 目录，所有脚本和 skill/subagent 定义在此
export WORK_ROOT="$SUBMISSION_ROOT/work"
```

验证 PROJECT_ROOT 结构：

```bash
test -f "$PROJECT_ROOT/README.md" || echo "MISSING: README.md"
test -f "$PROJECT_ROOT/code/pom.xml" || echo "MISSING: code/pom.xml"
test -d "$PROJECT_ROOT/design-docs" || echo "MISSING: design-docs/"
test -f "$PROJECT_ROOT/test-cases/pom.xml" || echo "MISSING: test-cases/pom.xml"
```

如果任一 MISSING，报告 `BLOCKED` 并停止。

---

## 加载作品

按以下顺序读取并遵守运行资产：

1. `${WORK_ROOT}/AGENTS.md`
2. `${WORK_ROOT}/skill/SKILL.md`
3. `${WORK_ROOT}/skills/*.md`
4. `${WORK_ROOT}/tools/scripts/*.py`

`work/skills/` 中的每个 Markdown 是一个 subagent 定义。若当前 agent 框架支持 subagent/Task 调用，请按文件名中的 agent 名称调用；若不支持，则由主 agent 读取这些文件并按其职责顺序执行。

---

## 规格驱动工作流（Phase 0 ~ 12，必须按序执行）

### Phase 0：预检与初始化

验证 PROJECT_ROOT 结构并初始化工作区：

```bash
# 检查 git 状态
cd "$PROJECT_ROOT" && git status --short

# 初始化 .agent-work/ 工作区
python3 "$WORK_ROOT/tools/scripts/shophub_goal_runner.py" --root "$PROJECT_ROOT" init
```

产出：`.agent-work/` 目录及其状态文件。

---

### Phase 1：构建契约

抽取 API Contract 和 Business Rules：

```bash
python3 "$WORK_ROOT/tools/scripts/api_contract_builder.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/business_rule_builder.py" --root "$PROJECT_ROOT"
```

产出：
- `.agent-work/api_contract.json`
- `.agent-work/business_rules.json`

---

### Phase 2：扫描代码

扫描 Spring Boot 代码结构：

```bash
python3 "$WORK_ROOT/tools/scripts/spring_scanner.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/dto_analyzer.py" --root "$PROJECT_ROOT"
python3 "$WORK_ROOT/tools/scripts/exception_analyzer.py" --root "$PROJECT_ROOT"
```

产出：
- `.agent-work/repo_map.json`
- `.agent-work/dto_validation_report.json`
- `.agent-work/exception_coverage.json`

---

### Phase 3：一致性检查（含 Baseline 保存）

运行静态一致性检查，并保存 baseline 用于后续 P0 比较：

```bash
# 运行 contract checker，并保存 baseline（--save-baseline 自动生成 baseline_consistency_report.json）
python3 "$WORK_ROOT/tools/scripts/contract_checker.py" --root "$PROJECT_ROOT" --save-baseline
```

产出：
- `.agent-work/trace_matrix.json`
- `.agent-work/consistency_report.json`
- `.agent-work/baseline_consistency_report.json`  ← 由 --save-baseline 自动写入

> **重要**：如果 `contract_checker.py` 返回非零（存在 P0 问题），不要停止。这证明修复前基线确实有问题，正是本次修复的目标。

---

### Phase 4：生成规格测试

根据 API Contract 和 Business Rules 自动生成测试，并编译验证：

```bash
python3 "$WORK_ROOT/tools/scripts/spec_test_generator.py" \
  --root "$PROJECT_ROOT" \
  --dry-run-compile
```

产出：
- `.tmp/generated-tests/`（临时，不提交）
- `.agent-work/generated_tests_manifest.json`

---

### Phase 5：基线测试

执行公开测试获取修复前基线：

```bash
python3 "$WORK_ROOT/tools/scripts/shophub_goal_runner.py" --root "$PROJECT_ROOT" baseline-tests
```

产出：`.agent-work/test-results/` 中的测试日志。

---

### Phase 6：构建修复任务 [必跑]

**本步骤不可跳过。** 将一致性问题 + 测试失败转换为结构化修复任务队列：

```bash
python3 "$WORK_ROOT/tools/scripts/repair_task_builder.py" --root "$PROJECT_ROOT"
```

产出：
- `.agent-work/repair_tasks.json`
- `.agent-work/repair_tasks.md`

输入依赖（由前序 Phase 自动生成，无需手动准备）：
- `.agent-work/api_contract.json`
- `.agent-work/business_rules.json`
- `.agent-work/dto_validation_report.json`
- `.agent-work/exception_coverage.json`
- `.agent-work/consistency_report.json`
- `.agent-work/test_symptoms.jsonl`

---

### Phase 7：候选补丁沙箱验证 [必跑]

**本步骤不可跳过。** 每个修复任务的每个候选补丁在独立 sandbox 中验证：

```bash
python3 "$WORK_ROOT/tools/scripts/candidate_sandbox.py" --root "$PROJECT_ROOT"
```

可选项（按需）：
```bash
# 仅验证特定任务
python3 "$WORK_ROOT/tools/scripts/candidate_sandbox.py" --root "$PROJECT_ROOT" --task-id <TASK_ID>

# 指定候选补丁文件路径（默认读取 .agent-work/candidate_patches.jsonl）
python3 "$WORK_ROOT/tools/scripts/candidate_sandbox.py" --root "$PROJECT_ROOT" --candidate-file .agent-work/candidate_patches.jsonl
```

验证内容（每个候选补丁在独立 git worktree sandbox 中）：
1. patch 应用
2. 编译检查（mvn compile）
3. 公开测试（mvn test）
4. 生成测试（generated tests）
5. contract checker（P0 增量检查 vs baseline）
6. forbidden-change guard

产出：
- `.agent-work/candidate_validation.jsonl`
- `.agent-work/candidate_validation.md`

注意：sandbox 在验证完成后自动清理（`git worktree remove --force` 或 `shutil.rmtree`），不会污染主 workspace。

---

### Phase 8：补丁评分选择 [必跑]

**本步骤不可跳过。** 从已验证候选补丁中选择最优补丁：

```bash
python3 "$WORK_ROOT/tools/scripts/patch_selector.py" --root "$PROJECT_ROOT"
```

评分公式：
```text
总分 = 40% 公开测试通过率 + 25% 生成测试通过率
     + 15% API contract checker 通过率 + 10% diff 最小化 + 10% 稳定性
```

产出：
- `.agent-work/selected_patch.json`
- `.agent-work/selected_patch.md`

---

### Phase 9：应用 Selected Patch

根据 `selected_patch.json` 中的 `patch_file` 字段应用最优补丁：

```bash
# 读取 selected patch 路径
SELECTED_PATCH=$(python3 -c "
import json
with open('$PROJECT_ROOT/.agent-work/selected_patch.json') as f:
    data = json.load(f)
print(data.get('patch_file', ''))
")

if [ -z "$SELECTED_PATCH" ] || [ "$SELECTED_PATCH" = "null" ]; then
    echo "STATUS: BLOCKED — no valid patch selected"
    exit 1
fi

echo "Applying: $SELECTED_PATCH"

# 在 PROJECT_ROOT 中应用补丁
cd "$PROJECT_ROOT" && git apply --verbose "$SELECTED_PATCH"
APPLY_EXIT=$?

if [ $APPLY_EXIT -ne 0 ]; then
    echo "WARNING: git apply failed (exit=$APPLY_EXIT), attempting git apply --reject..."
    cd "$PROJECT_ROOT" && git apply --reject --verbose "$SELECTED_PATCH" || {
        echo "STATUS: BLOCKED — patch apply failed and --reject also failed"
        echo "Rolling back..."
        cd "$PROJECT_ROOT" && git checkout -- .
        exit 1
    }
fi
```

**回滚规则**：
- 如果 `git apply` 失败且 `git apply --reject` 也失败 → **完整回滚**：`git checkout -- .`，报告 `BLOCKED`。
- 如果 patch 应用后 Phase 10 稳定性门禁未通过 → **完整回滚**：`git checkout -- .`，标记当前任务为 `REVERTED`，尝试 fallback 候选补丁。

---

### Phase 10：稳定性门禁

应用补丁后执行完整门禁：

```bash
# full-gate 模式：连续运行 N 次全部通过才算稳定
python3 "$WORK_ROOT/tools/scripts/stability_runner.py" \
  --root "$PROJECT_ROOT" \
  --mode full-gate \
  --runs 3
```

full-gate 每轮包括：
1. `mvn -f code/pom.xml test`（代码单元测试）
2. `mvn -f code/pom.xml install -DskipTests`（编译安装）
3. `mvn -f test-cases/pom.xml test`（公开黑盒测试）
4. generated tests（规格生成测试）
5. contract checker（一致性重检）
6. forbidden-change guard（禁止变更检查）

如果 3 次运行中任一失败，稳定性门禁不通过。此时必须回滚补丁，尝试 fallback 候选。

产出：`.agent-work/stability_report.json`

---

### Phase 11：Forbidden-Change Guard

最终变更检查，确保没有违反禁止规则：

```bash
python3 "$WORK_ROOT/tools/scripts/forbidden_change_guard.py" \
  --root "$PROJECT_ROOT" \
  --strict
```

产出：`.agent-work/forbidden_change_report.json`

如果 `--strict` 模式下返回非零，报告 `STOPPED_BY_SAFETY` 并停止。

---

### Phase 12：输出报告

生成最终交付报告：

```bash
# 生成修复报告
python3 "$WORK_ROOT/tools/scripts/shophub_goal_runner.py" --root "$PROJECT_ROOT" report

# 生成 Maven 验证结果
cd "$PROJECT_ROOT"
mvn -s maven-settings.xml -f code/pom.xml test > /tmp/maven-code-test.log 2>&1
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests > /tmp/maven-install.log 2>&1
mvn -s maven-settings.xml -f test-cases/pom.xml test > /tmp/maven-public-test.log 2>&1
```

产出：
- `result/output.md`
- `${PROJECT_ROOT}/修复报告.md`

---

## 验证命令

在 `PROJECT_ROOT` 中按顺序执行。使用项目根 `maven-settings.xml` 配置内网镜像：

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

---

## 修复约束

### 禁止修改

```text
design-docs/**
README.md 中的比赛说明和 API 基线
test-cases/**（除非仅为本地诊断，提交修复不得依赖测试改动）
```

### 不得改变

- `/api/v1/` REST URL
- HTTP Method
- 请求头语义
- 请求体字段名或类型
- 已文档化响应字段名或类型
- 成功状态码
- 公开错误码语义

### 禁止的代码模式（由 forbidden-change guard 检查）

```text
- 硬编码测试固定值（if name.equals("公开测试固定值")）
- 吞异常（catch Exception e { return success; }）
- 统一返回 200（所有错误 return ResponseEntity.ok）
- 注释掉核心校验
- 删除 @Valid
- 删除 @Transactional
- 删除 Repository 查询条件
```

### 允许修改

- `code/**` 下 Java 源码和 JUnit 测试
- `code/**/application.yml` 或 `application.yaml`
- `code/**/pom.xml`（需白名单确认）

可添加响应兼容别名，但仅当它暴露已有领域状态、不删除或改名已文档化字段，且用于保持 API 兼容性。

---

## 补丁生成原则

每个修复任务生成 3~5 个候选补丁：

```text
candidate-1：最小 DTO/annotation 修复
candidate-2：Service 层显式校验修复
candidate-3：Controller + ExceptionHandler 修复
candidate-4：Repository/query 逻辑修复
candidate-5：综合修复（diff 预算封顶）
```

优先最小 diff，优先符合 API 基线，优先符合设计文档。

---

## 停止条件

以下任一条件触发时立即停止，不得继续修改代码：

### BLOCKED
- PROJECT_ROOT 结构验证失败（缺少必需文件）
- 所有候选补丁均未通过 sandbox 验证
- `patch_selector` 无有效补丁可选
- `git apply` 和 `git apply --reject` 均失败
- 连续 3 轮修复无进展
- Maven 编译失败（非补丁引入的已有问题除外）

### STOPPED_BY_SAFETY
- forbidden-change guard 检测到 BLOCKER 级别违规
- 补丁删除了 @Valid、@Transactional 或 Repository 查询条件
- 补丁引入了硬编码测试固定值
- 补丁将错误响应统一改为 200
- 补丁吞掉了核心异常

---

## 最终 output.md 格式

`result/output.md` 必须包含以下章节：

```markdown
# Goal-Agent 修复报告

## 状态
[DONE | BLOCKED | STOPPED_BY_SAFETY]

## 概览
- 发现问题数：N
- 已修复数：M
- 剩余问题数：K

## API 契约状态
- API Contract 端点合规数：X / Y
- Business Rules 合规数：A / B
- Consistency P0 问题：基线 C → 修复后 D

## 修改清单
| 文件 | 修改类型 | 原因 |
|---|---|---|
| code/.../Xxx.java | ... | ... |

## 补丁选择
- 候选补丁数：N
- 选中补丁：<patch_file>
- 评分：<score>
- Fallback 候选：<list>

## 验证结果
- Maven code test: PASS / FAIL
- Maven install: PASS / FAIL
- Maven public test: PASS / FAIL
- Stability gate (3 runs): PASS / FAIL
- Forbidden-change guard (strict): PASS / FAIL
- Contract checker P0 delta: +N / 0 /

## 剩余风险
- ...
```

---

## 最终回答必须包含

- 状态：`DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`
- issue 发现/修复/剩余数量
- API 契约状态
- forbidden-change guard 状态
- stability rerun 状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险

---

## 运行后必须生成的文件清单

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

## 不得提交的目录

```text
.agent-work/
.tmp/
code/src/test/java/generated/  （generated tests 临时目录，需在最终提交前清理）
```
