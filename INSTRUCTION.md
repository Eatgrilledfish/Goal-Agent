# ShopHub Goal Runner 入口说明

本文件是比赛作品入口。评分平台加载本 Markdown 后，请按这里定位目标仓库、加载 `work/` 运行资产，并在符合题目结构的目标仓库中完成修复与验证。目标仓库名称不固定，不应依赖仓库目录名。

不需要安装插件，不需要复制文件到目标仓库，也不需要额外命令入口。

## 1. 定位目录

先确定两个目录：

- `SUBMISSION_ROOT`：本提交包根目录，包含 `INSTRUCTION.md` 和 `work/`。
- `PROJECT_ROOT`：待修复的 ShopHub 题目仓库根目录，必须包含：

```text
README.md
code/pom.xml
design-docs/
test-cases/pom.xml
```

如果当前工作目录就是上述结构，则它就是 `PROJECT_ROOT`。否则请在当前目录、父目录和兄弟目录中查找满足该结构的目录。

`WORK_ROOT` 固定为：

```text
${SUBMISSION_ROOT}/work
```

## 2. 加载作品与主执行路径

进入 `PROJECT_ROOT` 执行实际修复。运行前读取以下资产：

```text
${WORK_ROOT}/skills/goal-agent-spec-driven/SKILL.md
${WORK_ROOT}/skills/shophub-orchestrator.md
${WORK_ROOT}/skills/*.md
${WORK_ROOT}/tools/scripts/*.py
${WORK_ROOT}/tools/config/*.json
```

当前比赛平台和本地验证环境均支持 subagent/Task 调用，因此必须调用 `shophub-orchestrator` 作为总控 agent，并由其调度 `work/skills/*.md` 中定义的 subagent 完成设计解析、代码扫描、一致性审计、代码修复、验证和报告输出。

不得将 `shophub_goal_runner.py` 作为独立自主修复入口。它仅作为确定性 helper toolkit 使用，可由 orchestrator 或 subagent 按需调用以下子命令：

```text
init
read-specs
read-api
map-code
baseline-tests
summarize-tests
audit
prioritize
next-round
finish-round
add-issue
report
status
```

主修复 actor 是 `shophub-patch-agent`，其职责是一次修复一个 issue 或一个紧耦合 issue 组，并直接修改 `code/**` 下允许修改的文件。`shophub-review-agent` 负责补丁审查，`shophub-test-diagnoser` 和 helper scripts 负责验证。

详细工作流、模块映射、修复优先级和 helper scripts 用法以 `${WORK_ROOT}/skills/goal-agent-spec-driven/SKILL.md` 为准。

## 3. 目标

对照 `PROJECT_ROOT/design-docs/` 和冻结 API 基线，修复 `PROJECT_ROOT/code/**` 中的设计实现不一致，直到公开验证通过，或剩余问题被明确记录为设计依据不足、环境阻塞或安全停止。

冻结 API 基线来源：

```text
${PROJECT_ROOT}/README.md 中的 API 基线/冻结契约部分
${PROJECT_ROOT}/design-docs/ 中语义上承载 REST API 参考的文档
```

由 `shophub-api-guardian` 负责语义识别，不依赖固定文件名。公开黑盒测试是诊断信号，不是唯一需求依据。修复必须回到设计文档和 API 基线确认。

## 4. 验证命令

在 `PROJECT_ROOT` 中按顺序执行。所有 Maven 命令必须使用项目根目录的 `maven-settings.xml`：

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

## 5. 关键约束

禁止修改：

```text
design-docs/**
README.md 中的比赛说明和 API 基线
test-cases/**（除非仅为本地诊断，提交修复不得依赖测试改动）
```

不得改变冻结 `/api/v1/` REST 契约，包括 URL、HTTP Method、请求头语义、请求体字段名或类型、已文档化响应字段名或类型、成功状态码、公开错误码语义。

允许修改：

```text
code/** 下 Java 源码和 JUnit 测试
code/**/application.yml 或 application.yaml
code/**/pom.xml
```

可添加响应兼容别名，但仅当它暴露已有领域状态、不删除或改名已文档化字段，且用于保持 README、附录A或公开 fixture 观察到的 API 兼容性。

## 6. 完成输出

完成时必须在 `PROJECT_ROOT` 生成：

```text
修复报告.md
.agent-work/goal_status.json
.agent-work/final_goal_report.json
```

最终回答必须包含：

- 状态：`DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`
- issue 发现/修复/剩余数量
- API 契约状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险

最终 DONE 必须由机器 gate 判定：

```bash
python3 ${WORK_ROOT}/tools/scripts/final_goal_gate.py --root ${PROJECT_ROOT}
```
