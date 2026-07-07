# ShopHub Goal Runner 入口说明

本文件是比赛作品入口。评分平台加载本 Markdown 后，请按这里定位赛事提供的 ShopHub 项目、加载 `work/` 运行资产，并在目标项目的 `code/` 中完成修复与验证。

不需要安装插件，不需要复制文件到目标仓库，也不需要额外命令入口。

## 1. 定位目录

先确定两个目录：

- `SUBMISSION_ROOT`：本提交包根目录，包含 `INSTRUCTION.md` 和 `work/`。
- `PROJECT_ROOT`：待修复的 ShopHub 题目仓库根目录。

比赛评测环境中，`PROJECT_ROOT` 固定为：

```text
/app/code/judge-assets/02_04_design_implementation_consistency/
```

该目录由平台提供，结构为：

```text
README.md
code/pom.xml
design-docs/
test-cases/pom.xml
```

执行时应优先使用上述固定路径作为比赛入口。仅当本地调试环境不存在该固定路径时，才可在当前目录、父目录和兄弟目录中查找同时包含 `README.md`、`code/pom.xml`、`design-docs/`、`test-cases/pom.xml` 的目录作为 `PROJECT_ROOT`。

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

## 3. 目标与修复依据

对照 `PROJECT_ROOT/design-docs/` 和冻结 API 基线，修复 `PROJECT_ROOT/code/**` 中的设计实现不一致，直到公开验证通过，或剩余问题被明确记录为设计依据不足、环境阻塞或安全停止。

冻结 API 基线来源：

```text
${PROJECT_ROOT}/README.md 中的 API 基线/冻结契约部分
${PROJECT_ROOT}/design-docs/ 中语义上承载 REST API 参考的文档
```

由 `shophub-api-guardian` 负责语义识别，不依赖固定文件名。公开黑盒测试是诊断信号，不是唯一需求依据。修复必须回到设计文档和 API 基线确认。

标准修复流程为：

```text
设计文档/API 契约分析
↓
代码一致性检查
↓
修复 code/
↓
运行构建验证和公开黑盒测试
↓
根据测试结果继续修复
↓
稳定后输出结果
```

其中 `design-docs/` 和 `README.md` 是问题判定依据，`test-cases/` 用于验证修复效果。黑盒测试结果不能替代设计文档和 API 契约作为修复依据。

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
README.md
design-docs/**
test-cases/**
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

最终修复结果以 `PROJECT_ROOT/code/` 中的代码为准。评测系统获取修复结果时，不依赖内部状态文件或内部 gate 的完成判定。

稳定后应尽量在 `PROJECT_ROOT` 生成以下运行记录，便于人工复盘和问题追踪：

```text
修复报告.md
.agent-work/goal_status.json
.agent-work/final_goal_report.json
```

最终回答建议包含：

- issue 发现/修复/剩余数量
- API 契约状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险

`final_goal_gate.py` 保留为内部质量检查工具，可在稳定后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/final_goal_gate.py --root ${PROJECT_ROOT}
```

`.agent-work/goal_status.json`、`.agent-work/final_goal_report.json` 和 `final_goal_gate.py` 不作为评测系统获取 `PROJECT_ROOT/code/` 修复结果的前置条件。
