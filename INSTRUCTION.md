# Goal-Agent 运行入口（规格驱动）

本文件是作品运行入口。平台加载本 Markdown 后，请按本文直接运行作品。

## 运行目标

在目标题目仓库中，使用 `work/` 内的 skill、subagent 定义和 helper scripts，执行规格驱动的设计实现一致性检查与自动修复：

```text
API Contract → Business Rules → Trace Matrix → Static Check
→ Generated Tests → Patch Candidates → Sandbox → Scoring → Stability
```

## 目录定位

先确定两个根目录：

- `SUBMISSION_ROOT`：本提交包根目录，包含本文件和 `work/`。
- `PROJECT_ROOT`：待修复的目标题目仓库根目录，必须包含：

```text
README.md
code/pom.xml
design-docs/
test-cases/pom.xml
```

如果当前工作目录已经包含这些题目仓库文件，则它就是 `PROJECT_ROOT`。否则在当前目录、父目录、兄弟目录中查找满足上述结构的目录。

`WORK_ROOT` 固定为：

```text
${SUBMISSION_ROOT}/work
```

## 加载作品

按以下顺序读取并遵守运行资产：

1. `${WORK_ROOT}/AGENTS.md`
2. `${WORK_ROOT}/skill/SKILL.md`
3. `${WORK_ROOT}/skills/*.md`
4. `${WORK_ROOT}/tools/scripts/*.py`

`work/skills/` 中的每个 Markdown 是一个 subagent 定义。若当前 agent 框架支持 subagent/Task 调用，请按文件名中的 agent 名称调用；若不支持，则由主 agent 读取这些文件并按其职责顺序执行。

### 规格驱动 subagent（新增）

以下 subagent 定义实现 DESIGN.md 的规格驱动流程：

- `contract-builder` — API Contract + Business Rule 抽取
- `code-analyzer` — Spring Boot 代码扫描（Controller/DTO/Exception/Repository）
- `consistency-checker` — 静态一致性检查 + Trace Matrix
- `patch-generator` — 多候选补丁生成
- `stability-verifier` — 验证 + forbidden-change guard + 稳定性重跑

### 原有 subagent（保留）

- `shophub-spec-librarian` — 设计规则填充
- `shophub-api-guardian` — API 契约守卫
- `shophub-code-mapper` — 代码地图
- `shophub-module-mapper` — 模块映射
- `shophub-test-diagnoser` — 测试诊断
- `shophub-module-auditor` — 模块审计
- `shophub-cross-cut-auditor` — 跨模块审计
- `shophub-patch-agent` — 补丁执行
- `shophub-review-agent` — 补丁审查
- `shophub-report-writer` — 报告写入

## 题目依据

业务真相来源：

```text
${PROJECT_ROOT}/design-docs/
```

冻结 API 基线来源（语义识别，不固定文件名）：

```text
${PROJECT_ROOT}/README.md
${PROJECT_ROOT}/design-docs/<API 参考文档>
```

公开黑盒测试只作为诊断信号，不能作为唯一需求依据。

## 规格驱动工作流（必须执行）

### Phase 0: 预检
- 验证 `PROJECT_ROOT` 结构与 `git status --short`。
- 初始化 `.agent-work/` 工作区。
- 执行：`python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT init`

### Phase 1: 构建契约
- 调用 `contract-builder` subagent，抽取 API Contract + Business Rules。
- 确定性辅助：
  - `python3 work/tools/scripts/api_contract_builder.py --root $PROJECT_ROOT`
  - `python3 work/tools/scripts/business_rule_builder.py --root $PROJECT_ROOT`
- 产出：`.agent-work/api_contract.json`、`.agent-work/business_rules.json`

### Phase 2: 扫描代码
- 调用 `code-analyzer` subagent，扫描 Spring Boot 代码结构。
- 确定性辅助：
  - `python3 work/tools/scripts/spring_scanner.py --root $PROJECT_ROOT`
  - `python3 work/tools/scripts/dto_analyzer.py --root $PROJECT_ROOT`
  - `python3 work/tools/scripts/exception_analyzer.py --root $PROJECT_ROOT`
- 产出：`.agent-work/repo_map.json`、`.agent-work/dto_validation_report.json`、`.agent-work/exception_coverage.json`

### Phase 3: 一致性检查
- 调用 `consistency-checker` subagent，构建 Trace Matrix + 静态检查。
- 确定性辅助：
  - `python3 work/tools/scripts/contract_checker.py --root $PROJECT_ROOT`
- 产出：`.agent-work/trace_matrix.json`、`.agent-work/consistency_report.json`

### Phase 4: 生成规格测试
- 根据 API Contract 和 Business Rules 自动生成测试。
- 确定性辅助：
  - `python3 work/tools/scripts/spec_test_generator.py --root $PROJECT_ROOT`
- 产出：`.tmp/generated-tests/`（不提交，验证后清理）

### Phase 5: 基线测试
- 执行公开测试获取基线。
- 确定性辅助：
  - `python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT baseline-tests`

### Phase 6: 定位修复任务
- 将一致性问题 + 测试失败 → repair_tasks.json。
- 按 P0 > P1 > P2 优先级排序。

### Phase 7: 修复循环
每轮只修一个 issue 或一个紧耦合 issue 组：

1. 调用 `patch-generator` subagent，生成 3~5 个候选补丁。
2. 每个候选补丁在独立 workspace 中验证：
   - 编译检查
   - 公开测试
   - 生成测试
   - contract checker
   - forbidden-change guard
3. 评分选择最优补丁（通过率 + diff 最小 + 风险最低）。
4. 应用最优补丁到主 workspace。
5. 调用 `stability-verifier` subagent 验证。
6. 循环直到公开用例通过，或触发安全停止条件。

### Phase 8: 稳定性门禁
- 最终补丁连续重跑 3 次全部通过。
- 确定性辅助：
  - `python3 work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --runs 3`
  - `python3 work/tools/scripts/forbidden_change_guard.py --root $PROJECT_ROOT`

### Phase 9: 输出报告
- 生成 `result/output.md` 和 `${PROJECT_ROOT}/修复报告.md`。
- 确定性辅助：
  - `python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT report`

## 验证命令

在 `PROJECT_ROOT` 中按顺序执行。使用项目根 `maven-settings.xml` 配置内网镜像：

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

## 修复约束

禁止修改：

```text
design-docs/**
README.md 中的比赛说明和 API 基线
test-cases/**（除非仅为本地诊断，提交修复不得依赖测试改动）
```

不得改变：

- `/api/v1/` REST URL
- HTTP Method
- 请求头语义
- 请求体字段名或类型
- 已文档化响应字段名或类型
- 成功状态码
- 公开错误码语义

禁止的代码模式（由 forbidden-change guard 检查）：

```text
- 硬编码测试固定值（if name.equals("公开测试固定值")）
- 吞异常（catch Exception e { return success; }）
- 统一返回 200（所有错误 return ResponseEntity.ok）
- 注释掉核心校验
- 删除 @Valid
- 删除 @Transactional
- 删除 Repository 查询条件
```

允许修改：

- `code/**` 下 Java 源码和 JUnit 测试
- `code/**/application.yml` 或 `application.yaml`
- `code/**/pom.xml`（需白名单确认）

可添加响应兼容别名，但仅当它暴露已有领域状态、不删除或改名已文档化字段，且用于保持 API 兼容性。

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

## 评分公式

```text
总分 = 40% 公开测试通过率 + 25% 生成测试通过率
     + 15% API contract checker 通过率 + 10% diff 最小化 + 10% 稳定性
```

## 完成标准

完成时必须满足：

- 记录设计依据。
- 记录修复前代码行为或测试症状。
- 列出修改文件。
- API 契约保持兼容。
- forbidden-change guard 通过。
- stability rerun（3 次）通过。
- 执行并记录验证命令结果。
- `${PROJECT_ROOT}/修复报告.md` 已生成。
- `result/output.md` 已生成。

最终回答必须包含：

- 状态：`DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`
- issue 发现/修复/剩余数量
- API 契约状态
- forbidden-change guard 状态
- stability rerun 状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险
