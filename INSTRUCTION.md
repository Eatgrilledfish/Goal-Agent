# ShopHub Goal Runner 运行入口

本作品用于 ShopHub 设计实现一致性检查与修复比赛。平台加载本文件后，应把本仓库视为提交包，把 `work/` 视为可运行交付件入口目录。

## 作品目标

在 OpenCode 中通过一个命令完成比赛目标：

```text
/shophub
```

该命令会启动隐藏的 `shophub-orchestrator` subagent，并由它调用以下专项 subagent 协同工作：

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

工作流会持续执行设计抽取、API 基线保护、代码地图、测试诊断、不一致审计、小步修复、复核和报告生成，直到 DONE 或触发安全停止条件。

## 安装作品

在提交包根目录执行：

```bash
bash work/install_opencode.sh
```

该脚本会注册：

```text
~/.config/opencode/commands/shophub.md
~/.config/opencode/agents/shophub-*.md
```

如果 Codex CLI 可用，也会同步安装 Codex personal plugin，便于本地调试。

## 运行作品

安装完成后，进入比赛仓库，而不是本提交包仓库。比赛仓库应包含：

```text
code/
design-docs/
test-cases/
API基线文档.md
黑盒用例说明.md
比赛说明.md
```

然后启动 OpenCode：

```bash
opencode
```

在 OpenCode CLI 中输入：

```text
/shophub
```

可选参数：

```text
/shophub max-rounds=20
/shophub dry-run
/shophub no-tests
/shophub report-only
```

真实比赛运行不要使用 `no-tests`，除非只是验证命令加载。

## 输出要求

作品运行后会在比赛仓库中生成或更新：

```text
.agent-work/
修复报告.md
```

最终回答必须包含：

- 状态：DONE、BLOCKED 或 STOPPED_BY_SAFETY；
- 发现、修复、未修复 issue 数量；
- API 契约状态；
- 实际执行的验证命令与结果；
- `修复报告.md` 路径；
- 剩余风险。

## 安全约束

- 不修改 `design-docs/**`。
- 不修改 `API基线文档.md`。
- 不修改 `比赛说明.md`。
- 不修改 `黑盒用例说明.md`。
- 避免修改 `test-cases/**`。
- 不改变 REST URL、HTTP Method、请求头、请求体字段、响应体字段或公开错误码语义。
- 每个 issue 和 fix 必须能回溯到设计文档或 API 基线。
- 公开黑盒测试只能作为症状，不能作为唯一需求依据。
