# ShopHub Goal Runner 入口说明

本文件是比赛作品入口。评分平台加载本 Markdown 后，请按这里定位目标仓库、加载 `work/` 运行资产，并在目标 `HW-ICT-CMP-04` 仓库中完成修复与验证。

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

## 2. 加载作品

进入 `PROJECT_ROOT` 执行实际修复。运行前读取以下资产：

```text
${WORK_ROOT}/AGENTS.md
${WORK_ROOT}/skill/SKILL.md
${WORK_ROOT}/skills/shophub-orchestrator.md
${WORK_ROOT}/skills/*.md
${WORK_ROOT}/tools/scripts/*.py
```

优先调用 `shophub-orchestrator` 作为总控 agent。若当前运行环境不支持 subagent/Task 调用，则由主 agent 读取 `work/skill/SKILL.md` 和 `work/skills/*.md`，按其中职责顺序执行。

详细工作流、模块映射、修复优先级和 helper scripts 用法以 `${WORK_ROOT}/skill/SKILL.md` 为准。

## 3. 目标

对照 `PROJECT_ROOT/design-docs/` 和冻结 API 基线，修复 `PROJECT_ROOT/code/**` 中的设计实现不一致，直到公开验证通过，或剩余问题被明确记录为设计依据不足、环境阻塞或安全停止。

冻结 API 基线来源：

```text
${PROJECT_ROOT}/README.md
${PROJECT_ROOT}/design-docs/附录A-API接口参考.md
```

公开黑盒测试是诊断信号，不是唯一需求依据。修复必须回到设计文档和 API 基线确认。

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
```

最终回答必须包含：

- 状态：`DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`
- issue 发现/修复/剩余数量
- API 契约状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险
