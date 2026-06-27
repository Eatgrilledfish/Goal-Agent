# 自验证输出记录

## 作品形态

- 入口文件：`/INSTRUCTION.md`
- 可运行交付件目录：`/work`
- 主 Skill：`/work/skill/SKILL.md`
- Subagents：`/work/skills/shophub-*.md`
- Helper scripts：`/work/tools/scripts/*.py`
- Agent 根目录：`/work`

当前交付不需要安装插件，不需要安装额外命令入口，也不需要把运行资产复制到题目仓库。平台加载 `/INSTRUCTION.md` 后，直接读取 `/work` 内资产并作用于目标 `HW-ICT-CMP-04` 题目仓库。

## 本地结构自检

必选路径：

```text
/INSTRUCTION.md
/work
/result/output.md
/logs/interaction.md
/logs/trace
```

可选路径：

```text
/work/skill/SKILL.md
/work/skills/shophub-api-guardian.md
/work/skills/shophub-code-mapper.md
/work/skills/shophub-module-auditor.md
/work/skills/shophub-orchestrator.md
/work/skills/shophub-patch-agent.md
/work/skills/shophub-report-writer.md
/work/skills/shophub-review-agent.md
/work/skills/shophub-spec-librarian.md
/work/skills/shophub-test-diagnoser.md
```

已执行轻量验证：

```text
python3 -m py_compile work/tools/scripts/*.py
YAML frontmatter ok for work/skills/*.md and work/skill/SKILL.md
strict submission structure ok
```

## 真实公开用例验证记录

已在真实题库克隆 `/tmp/HW-ICT-CMP-04` 中按设计文档修复公开用例暴露的问题，并通过以下验证：

```text
docker run --rm -v /tmp/HW-ICT-CMP-04:/workspace -v /tmp/m2-shophub:/root/.m2 -w /workspace maven:3.9-eclipse-temurin-17 mvn -f code/pom.xml test
docker run --rm -v /tmp/HW-ICT-CMP-04:/workspace -v /tmp/m2-shophub:/root/.m2 -w /workspace maven:3.9-eclipse-temurin-17 mvn -f code/pom.xml install -DskipTests
docker run --rm -v /tmp/HW-ICT-CMP-04:/workspace -v /tmp/m2-shophub:/root/.m2 -w /workspace maven:3.9-eclipse-temurin-17 mvn -f test-cases/pom.xml test
```

结果：

```text
code/pom.xml test: BUILD SUCCESS
code/pom.xml install -DskipTests: BUILD SUCCESS
test-cases/pom.xml test: 24 tests, 0 failures, 0 errors, BUILD SUCCESS
```

本次实测暴露出的设计不一致已同步进 `/work/skill/SKILL.md` 与 `/work/skills/*.md` 的公开基线诊断清单。

## 运行成功后的预期输出

在真实 ShopHub 比赛仓库中按 `/INSTRUCTION.md` 执行后，应生成：

```text
.agent-work/
修复报告.md
```

最终输出应报告 DONE、BLOCKED 或 STOPPED_BY_SAFETY，并列出 issue 统计、API 契约状态、验证命令及结果。
