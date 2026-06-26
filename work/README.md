# Work Deliverable

本目录是比赛提交结构中的可运行交付件目录，也是 OpenCode agent 应当视为根目录的目录。

当前作品是 OpenCode `skill + hidden agents + slash command` 的 Goal Runner，不再是插件。

```bash
bash install_opencode.sh /path/to/HW-ICT-CMP-04
```

安装脚本会把实际运行资产复制到目标比赛仓库：

- `.opencode/commands/shophub.md`
- `.opencode/agents/shophub-*.md`
- `.opencode/skills/shophub-goal-runner/SKILL.md`
- `.opencode/shophub/tools/scripts/*.py`

用户可见入口只有：

```text
/shophub
```

OpenCode 内部会加载 `shophub-goal-runner` skill，并通过 `shophub-orchestrator` 调用多个 hidden subagent 完成比赛任务。
