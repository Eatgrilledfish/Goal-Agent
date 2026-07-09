# RFC 实现差异检视入口说明

本文件是比赛作品入口。评分平台加载本 Markdown 后，请按这里定位赛事提供的待检视工程（F-Stack 及其设计依据 RFC），加载 `work/` 运行资产，识别代码实现与设计文档/RFC 之间的不一致问题并输出证据链。

本系统**不修改目标代码**，只识别不一致问题、输出 issues 及证据链报告。不需要安装插件，不需要复制文件到目标仓库。

## 1. 定位目录

先确定以下目录：

- `SUBMISSION_ROOT`：本提交包根目录，包含 `INSTRUCTION.md` 和 `work/`。
- `WORK_ROOT`：固定为 `${SUBMISSION_ROOT}/work`。

比赛评测环境中，赛事资产根目录固定为：

```text
ASSET_ROOT=/app/code/judge-assets/01_03_ai_implementation_design_difference_detection
```

该目录由平台提供，结构为：

```text
01_03_ai_implementation_design_difference_detection/
├── code/
│   └── f-stack/        ← 待检视的 C/C++/DPDK/FreeBSD 网络协议栈代码
└── Difference/
    └── benchmark.md    ← 设计依据入口，列出相关 RFC 与 F-Stack commit 信息
```

由此派生固定路径：

```text
CODE_ROOT=${ASSET_ROOT}/code/f-stack
DESIGN_ROOT=${ASSET_ROOT}/Difference
BENCHMARK=${ASSET_ROOT}/Difference/benchmark.md
RESULT_ROOT=/result
LOG_ROOT=/logs
```

执行时应优先使用上述固定路径作为比赛入口。仅当本地调试环境不存在该固定路径时，才允许通过命令行参数传入：

```text
--code-root <path>
--design-root <path>
--benchmark <path>
--result-root <path>
--log-root <path>
```

## 2. 加载作品与主执行路径

运行前读取以下资产：

```text
${WORK_ROOT}/skills/rfc-implementation-diff-detection/SKILL.md
${WORK_ROOT}/agents/*.md
${WORK_ROOT}/skills/*.md
${WORK_ROOT}/tools/scripts/*.py
${WORK_ROOT}/tools/config/*.json
```

当前比赛平台和本地验证环境均支持 subagent/Task 调用，因此必须：

1. 先加载 `${WORK_ROOT}/skills/rfc-implementation-diff-detection/SKILL.md`；
2. 再调用 `rfc-diff-orchestrator` 作为总控 agent；
3. 由 orchestrator 调度 `work/agents/*.md` 中定义的 subagent 与 `work/tools/scripts/*.py` 确定性 helper 脚本完成 RFC 读取、规范提取、代码扫描、映射、检测、审查、报告与最终 gate。

## 3. 启动命令

确定性主入口为 `rfc_goal_runner.py`，它管理 `.agent-work/` 状态目录、按阶段调度 helper 脚本、生成最终结果。一键执行全部阶段：

```bash
python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py \
  --code-root ${CODE_ROOT} \
  --design-root ${DESIGN_ROOT} \
  --benchmark ${BENCHMARK} \
  --result-root ${RESULT_ROOT} \
  --log-root ${LOG_ROOT} \
  run-all
```

也可分阶段执行（阶段顺序见 SKILL.md）：

```text
init | load-docs | extract-spec | index-code | map | detect | review | report | gate
```

如果平台支持 agent/skill 调用，则按本文件第 2 节：先加载 SKILL.md，再调用 `rfc-diff-orchestrator`，由 orchestrator 调用 helper scripts 和 subagents 完成检视。

`rfc_goal_runner.py` 是确定性 helper toolkit 与流水线骨架，不是自主修改代码的 actor。本题**不修复代码**，所有阶段只读代码、只写 `/result`、`/logs` 与 `.agent-work/`。

## 4. 执行完成判定

满足以下全部条件即视为完成：

1. `rfc_goal_runner.py` 正常退出（`run-all` 退出码 0）；
2. `/result/issues.json` 生成；
3. `/result/00-summary.md` 生成；
4. `/result` 下至少生成一个单 issue markdown 文件（`01-*.md` 等）；
5. `/logs/trace/final_detection_gate.json` 生成，且 gate 判定通过或已记录未达 4 个 issue 的原因。

## 5. 结果获取方式

裁判读取以下文件：

```text
/result/issues.json      ← 机器可读主结果
/result/issues.jsonl     ← 每行一个 issue
/result/00-summary.md    ← 人类可读总览
/result/*.md             ← 单 issue 证据链报告
```

其中 `issues.json` 是机器可读主结果，schema 见 `work/skills/rfc-implementation-diff-detection/SKILL.md` 及 `work/tools/config/output_schema.json`。

## 6. 关键约束

禁止修改：

```text
code/**            ← 待检视目标代码，只读
Difference/**      ← 设计依据，只读
benchmark.md       ← 设计入口，只读
```

只允许写：

```text
/result/**
/logs/**
.agent-work/**
```

不得运行 Maven / Java 构建（本题为 C/C++/DPDK/FreeBSD 工程，无 `pom.xml`、`maven-settings.xml`）。不得引入 Spring Boot / ShopHub 相关假设与脚本。

## 7. 评价目标

```text
识别 issues 数量 >= 4（confirmed/probable）
误报率 <= 50%
总检视时长 <= 6 小时
```

若证据不足未达 4 个 issue，不得伪造。应在 `00-summary.md` 中如实说明原因（RFC 获取失败 / 证据不足 / 代码路径无法确认）。
