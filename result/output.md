# 自验证输出记录

## 作品形态

- 入口文件：`/INSTRUCTION.md`
- 可运行交付件目录：`/work`
- Skill：`/work/skill/SKILL.md`
- OpenCode 项目本地 Skill：`/work/.opencode/skills/shophub-goal-runner/SKILL.md`
- OpenCode 用户入口：`/shophub`
- OpenCode hidden subagents：`/work/.opencode/agents/shophub-*.md`
- Helper scripts：`/work/tools/scripts/*.py`
- Agent 根目录：`/work`

## 已完成的本地验证记录

当前 skill + agent 形态已执行以下验证，均通过：

```text
python3 -m py_compile scripts/*.py
python3 -m py_compile work/tools/scripts/*.py
bash -n work/install_opencode.sh
YAML frontmatter ok for work/.opencode agents, command, and skill files
Skill is valid!
strict submission structure ok
```

已在真实题库克隆 `/tmp/HW-ICT-CMP-04` 上执行 `/work` 安装入口：

```text
Installed ShopHub Goal Runner into:
  /tmp/HW-ICT-CMP-04/.opencode

OpenCode assets:
  .opencode/commands/shophub.md
  .opencode/agents/shophub-*.md
  .opencode/skills/shophub-goal-runner/SKILL.md
  .opencode/shophub/tools/scripts/
```

真实题库结构验证：

```text
missing_required_paths: []
api baseline sources: README.md, design-docs/附录A-API接口参考.md
api endpoints extracted: 81
generic module-missing issues: 0
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

本次实测暴露出的设计不一致已同步进 `/work` 的 skill 与 hidden agents 的公开基线诊断清单，后续 `/shophub` 运行会优先审计这些高风险点。

说明：当前交付不再依赖 Codex plugin 或 `~/plugins`；黑箱环境只需将 `/work` 安装到目标比赛仓库 `.opencode/`。

## 提交结构自检

必选路径：

```text
/INSTRUCTION.md
/work
/result/output.md
/logs/interaction.md
/logs/trace
```

根目录已移除旧插件、旧脚本、旧报告和旧运行产物；运行交付件只保留在 `/work` 内。

已使用可选 Skill 路径：

```text
/work/skill/SKILL.md
```

运行约束文件：

```text
/work/AGENTS.md
```

未使用可选目录：

```text
/result/screenshot
/problem_statement
```

## 运行成功后的预期输出

在真实 ShopHub 比赛仓库中运行 `/shophub` 后，应生成：

```text
.agent-work/
修复报告.md
```

最终输出应报告 DONE、BLOCKED 或 STOPPED_BY_SAFETY，并列出 issue 统计、API 契约状态、验证命令及结果。
