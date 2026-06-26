# 自验证输出记录

## 作品形态

- 入口文件：`/INSTRUCTION.md`
- 可运行交付件目录：`/work`
- OpenCode 用户入口：`/shophub`
- OpenCode hidden subagents：`shophub-*.md`

## 已完成的本地验证记录

本次结构化保存后已执行以下验证，均通过：

```text
Plugin validation passed: /Users/fangjianqiao/Documents/Goal-Agent
python3 -m py_compile scripts/*.py
bash -n work/install_opencode.sh
bash -n scripts/install_plugin.sh
required submission structure ok
```

已执行 `/work` 安装入口：

```text
Installed shophub-goal-runner.
OpenCode global command:
  /Users/fangjianqiao/.config/opencode/commands/shophub.md
OpenCode hidden subagents:
  /Users/fangjianqiao/.config/opencode/agents/shophub-*.md
```

OpenCode 链接验证：

```text
/Users/fangjianqiao/.config/opencode/commands/shophub.md
  -> /Users/fangjianqiao/Documents/Goal-Agent/commands/shophub.md

/Users/fangjianqiao/.config/opencode/agents/shophub-orchestrator.md
  -> /Users/fangjianqiao/Documents/Goal-Agent/agents/shophub-orchestrator.md

/Users/fangjianqiao/.config/opencode/agents/shophub-patch-agent.md
  -> /Users/fangjianqiao/Documents/Goal-Agent/agents/shophub-patch-agent.md
```

说明：当前本机 Codex CLI 的 `codex plugin add` 子命令不可用，因此 Codex personal plugin 安装状态显示为非阻断失败；OpenCode command/subagent 注册已成功。

## 提交结构自检

本次结构化保存新增了比赛要求的必选路径：

```text
/INSTRUCTION.md
/work
/result/output.md
/logs/interaction.md
/logs/trace
```

可选目录未使用时未创建：

```text
/work/skill/SKILL.md
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
