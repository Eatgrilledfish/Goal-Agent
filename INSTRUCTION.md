# ShopHub Goal Runner 运行入口

本文件是作品运行入口。平台加载本 Markdown 后，请按本文直接运行作品，不需要安装插件，也不需要安装额外命令入口。

## 运行目标

在目标题目仓库中，使用 `work/` 内的 skill、subagent 定义和 helper scripts，持续比较设计文档、冻结 API 基线与 Java Spring Boot 实现，修复代码中的设计实现不一致，直到公开验证通过或触发明确安全停止条件。

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

必须使用的 subagent 定义：

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-module-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-cross-cut-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

## 题目依据

业务真相来源：

```text
${PROJECT_ROOT}/design-docs/
```

冻结 API 基线来源：

```text
${PROJECT_ROOT}/README.md
${PROJECT_ROOT}/design-docs/<API 参考文档>（由 api-guardian 语义识别，不固定文件名）
```

公开黑盒测试只作为诊断信号，不能作为唯一需求依据。

## 必须执行的工作流

1. 预检 `PROJECT_ROOT` 结构与 `git status --short`。
2. 读取 `design-docs/**`，提取可追踪业务规则。
3. 读取冻结 API 基线，记录并保护 `/api/v1/` REST 契约。
4. 建立代码地图：controller、service、repository、DTO、entity、event、test。
5. 运行基线验证，记录失败症状。
6. 将失败症状和高风险模块转换为带设计依据、代码位置和 API 影响的 issue。
7. 每轮只修一个 issue 或一个紧耦合 issue 组。
8. 每轮修复后运行聚焦测试、API 契约检查和 review。
9. 循环直到公开用例通过，或剩余问题被设计依据证明为风险接受/安全停止。
10. 生成 `${PROJECT_ROOT}/修复报告.md`。

## 验证命令

在 `PROJECT_ROOT` 中按顺序执行。本机必须使用 Maven；`PROJECT_ROOT/maven-settings.xml` 是内网镜像配置，所有 Maven 命令必须使用该文件。

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

允许修改：

- `code/**` 下 Java 源码和 JUnit 测试
- `code/**/application.yml` 或 `application.yaml`
- `code/**/pom.xml`

可添加响应兼容别名，但仅当它暴露已有领域状态、不删除或改名已文档化字段，且用于保持 README、API 参考文档或公开 fixture 观察到的 API 兼容性。

## 完成标准

完成时必须满足：

- 记录设计依据。
- 记录修复前代码行为或测试症状。
- 列出修改文件。
- API 契约保持兼容。
- 执行并记录验证命令结果。
- `${PROJECT_ROOT}/修复报告.md` 已生成。

最终回答必须包含：

- 状态：`DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`
- issue 发现/修复/剩余数量
- API 契约状态
- 验证命令和结果
- `修复报告.md` 路径
- 剩余风险
