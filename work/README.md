# Work Deliverable

本目录是比赛提交结构中的可运行交付件目录。

当前作品不是单独 Skill，也不是 MCP server；它是 OpenCode slash command + hidden subagents 的 Goal Runner。因此本目录保留安装入口：

```bash
bash work/install_opencode.sh
```

安装脚本会从提交包根目录注册实际运行资产：

- `commands/shophub.md`
- `agents/shophub-*.md`
- `scripts/*.py`
- `.codex-plugin/plugin.json`

用户可见入口只有：

```text
/shophub
```

OpenCode 内部会通过 `shophub-orchestrator` 调用多个 hidden subagent 完成比赛任务。
