# ShopHub Goal Runner 运行入口

本作品用于 `HW-ICT-CMP-04` ShopHub 设计实现一致性检查与修复比赛。平台加载本文件后，应把本仓库视为提交包，把 `work/` 视为 OpenCode 可运行交付件入口目录。

## 作品目标

在 OpenCode 中通过 skill + hidden agents 完成比赛目标。安装后可通过一个命令启动：

```text
/shophub
```

该命令会加载 `shophub-goal-runner` skill，启动隐藏的 `shophub-orchestrator` subagent，并由它调用以下专项 subagent 协同工作：

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

工作流会持续执行设计抽取、API 基线保护、代码地图、测试诊断、不一致审计、小步修复、复核和报告生成，直到 DONE 或触发安全停止条件。

## 真实比赛仓库结构

目标比赛仓库是：

```text
README.md
code/
design-docs/
test-cases/
```

不要要求旧版占位文件：

```text
API基线文档.md
比赛说明.md
黑盒用例说明.md
```

冻结 API 基线来自：

- `README.md` 第 6 节 `API 基线（冻结契约）`
- `design-docs/附录A-API接口参考.md`

## 安装作品

在提交包根目录执行，并传入比赛仓库路径：

```bash
bash work/install_opencode.sh /path/to/HW-ICT-CMP-04
```

如果当前目录就是比赛仓库，也可以执行：

```bash
bash /path/to/submission/work/install_opencode.sh
```

该脚本会把 skill、agents、command 和 helper scripts 安装到目标比赛仓库：

```text
.opencode/commands/shophub.md
.opencode/agents/shophub-*.md
.opencode/skills/shophub-goal-runner/SKILL.md
.opencode/shophub/tools/scripts/
```

不需要安装 Codex plugin，也不依赖 `~/plugins`。

## 运行作品

安装完成后，进入比赛仓库并启动 OpenCode：

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
/shophub report-only
```

真实比赛运行不要跳过测试。

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
- 不修改 `README.md` 中的比赛说明和 API 基线。
- 避免修改 `test-cases/**`。
- 不改变 `/api/v1/` REST URL、HTTP Method、请求头、请求体字段、响应体字段、成功状态码或公开错误码语义。
- 每个 issue 和 fix 必须能回溯到设计文档或 API 基线。
- 公开黑盒测试只能作为症状，不能作为唯一需求依据。
