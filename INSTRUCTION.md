# 设计/代码实现差异检视入口说明

本文件是比赛作品入口。评分平台加载本 Markdown 后，应由运行中的 opencode CLI 作为主 agent 执行检视：读取设计文档与目标代码仓，使用 `work/` 下的 helper 脚本生成索引、候选、证据包，然后由 opencode 自己做语义调查和最终裁决。

本系统不修改目标代码，只识别设计文档与实现之间的不一致问题，输出 issues 与证据链报告。

## 1. 定位目录

先确定以下目录：

- `SUBMISSION_ROOT`：本提交包根目录，包含 `INSTRUCTION.md` 和 `work/`。
- `WORK_ROOT`：固定为 `${SUBMISSION_ROOT}/work`。

比赛评测环境中，赛事资产根目录通常为：

```text
ASSET_ROOT=/app/code/judge-assets/01_03_ai_implementation_design_difference_detection
```

公共样例结构为：

```text
01_03_ai_implementation_design_difference_detection/
├── code/
│   └── f-stack/
└── Difference/
    └── benchmark.md
```

隐藏评测可能把不同内部工程放在 `code/` 下。不要写死 `f-stack`：若 `code/f-stack` 存在则使用它；否则使用 `code/` 下唯一的项目目录；若存在多个目录，由 opencode 检查设计入口并选择真正的目标代码仓。

推荐派生路径：

```text
CODE_ROOT=<ASSET_ROOT>/code/<target-project>
DESIGN_ROOT=<ASSET_ROOT>/Difference        # 若不存在，检查 design/、design-docs/、docs/
BENCHMARK=<DESIGN_ROOT>/benchmark.md       # 若不存在，选择设计入口 markdown/txt
RESULT_ROOT=/result
LOG_ROOT=/logs
```

本地调试时可以显式传入：

```text
--code-root <path>
--design-root <path>
--benchmark <path>
--result-root <path>
--log-root <path>
```

## 2. 必须加载的作品资产

opencode 启动后必须读取：

```text
${WORK_ROOT}/skills/rfc-implementation-diff-detection/SKILL.md
${WORK_ROOT}/agents/rfc-diff-orchestrator.md
${WORK_ROOT}/agents/rfc-evidence-reviewer.md
${WORK_ROOT}/agents/*.md
${WORK_ROOT}/tools/scripts/*.py
${WORK_ROOT}/tools/config/*.json
```

角色边界：

- Python helper scripts：索引、召回、证据包生成、schema 校验、报告生成。
- opencode agent：读取设计与代码、按需 `rg`/读文件/追调用链、判断是否真有设计实现不一致。
- `evidence_validator.py`：只消费 opencode verdict 并校验证据形状，不用规则或权重决定 confirmed。

## 3. 主执行路径

不要把 `run-all` 当作最终 agent loop。正式执行时先初始化，再运行 `prepare-review`。`prepare-review` 会刷新确定性召回链路：

```text
load-docs -> scope-plan -> extract-spec -> index-code -> map -> detect -> bundle
```

之后必须暂停 helper-only pipeline，由 opencode 完成语义审阅；没有 opencode verdict 时不得进入正式结果。

```bash
python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py \
  --code-root ${CODE_ROOT} \
  --design-root ${DESIGN_ROOT} \
  --benchmark ${BENCHMARK} \
  --result-root ${RESULT_ROOT} \
  --log-root ${LOG_ROOT} \
  init

python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} --benchmark ${BENCHMARK} --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} prepare-review
```

`prepare-review` 会生成：

```text
${AGENT_WORK}/agent_review_queue.json
${AGENT_WORK}/agent_loop_contract.json
${AGENT_WORK}/agent_loop_state.json
${AGENT_WORK}/agent_run_ledger.jsonl
${AGENT_WORK}/agent-review/*.json
${LOG_ROOT}/trace/agent_review_queue_summary.json
```

其中 `AGENT_WORK` 由 runner 写入 queue 的 `agent_work` 字段，通常是 `CODE_ROOT` 同级目录下的 `.agent-work`。随后 opencode 必须读取 `agent_review_queue.json`、`agent_loop_state.json` 和每个 item 的 `bundle_abs_path`，执行语义调查。候选只是召回提示，不是完整搜索空间；若设计文档描述了候选未覆盖的问题，opencode 可以新增 `AGENT-DISCOVERED-*` verdict。

`agent_review_queue.json` 内嵌以下 machine-readable contract：

- `session`：当前 opencode review session、`verdict_output`、`agent_loop_state.json`、`agent_run_ledger.jsonl` 和恢复策略。
- `agent_loop_contract`：模型驱动 loop 的阶段、停止条件和 review contract。
- `handoffs`：helper recall → opencode semantic review → validator/ranker/report/gate 的交接物与验收条件。
- `guardrails`：只读目标仓、禁止硬编码、禁止把 regex/权重当 final evidence。
- `approval_flows`：默认自动批准只读搜索和 helper 脚本；目标代码/设计文档写入、破坏性命令和无关长任务必须跳过或需要外部批准。
- `tracing`：每个 confirmed verdict 必须写非空 `tool_trace`，全局状态写入 loop state、ledger 和 `/logs/trace`。

opencode 审阅时应把增量进展追加到 `${AGENT_WORK}/agent_run_ledger.jsonl`，至少记录本轮假设、已查设计区域/代码区域、确认/拒绝原因、失败样本、下一步待办和停止原因。长任务恢复时先读 ledger 和既有 `agent_review_verdicts.jsonl`，跳过已经 review 的 `candidate_id`。

opencode 审阅完成后写入：

```text
${AGENT_WORK}/agent_review_verdicts.jsonl
```

每行一个 JSON object，字段至少包含：

```json
{
  "candidate_id": "candidate id or AGENT-DISCOVERED-001",
  "status": "confirmed",
  "confidence": 0.9,
  "title": "short issue title",
  "normative_level": "MUST/SHOULD/MAY/design-requirement/unknown",
  "design_evidence": {
    "rfc": "RFC id or design document id",
    "section": "section or heading",
    "doc_path": "design document path",
    "quote": "short design quote"
  },
  "code_evidence": [
    {
      "file": "repo-relative source file",
      "line_start": 1,
      "line_end": 2,
      "symbol": "function/class/module",
      "snippet": "source excerpt"
    }
  ],
  "inconsistency": "why design and implementation differ",
  "impact": "runtime/protocol/user-visible impact",
  "false_positive_controls": ["reverse checks performed"],
  "related_files": ["repo-relative files"],
  "agent_notes": "concise reasoning and tool trail",
  "tool_trace": [
    {
      "tool": "rg/read_file/shell/analysis",
      "target": "file, symbol, command, or design section inspected",
      "purpose": "why this step was needed",
      "result": "short observation used in the verdict"
    }
  ],
  "generalization_rationale": "why this is not project-name hardcoding"
}
```

只允许 `status` 为 `confirmed`、`probable`、`rejected`。正式结果只输出 `confirmed`；`probable` 进入 review queue。

最后运行：

```bash
python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} --benchmark ${BENCHMARK} --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} review
python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} --benchmark ${BENCHMARK} --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} report
python3 ${WORK_ROOT}/tools/scripts/rfc_goal_runner.py --code-root ${CODE_ROOT} --design-root ${DESIGN_ROOT} --benchmark ${BENCHMARK} --result-root ${RESULT_ROOT} --log-root ${LOG_ROOT} gate
```

## 4. opencode 审阅准则

确认一个 issue 必须同时满足：

1. 设计文档/RFC 明确描述行为、约束、能力或禁止事项。
2. 代码证据位于相关实现路径，包含具体文件、行号、符号和片段。
3. 代码证据与设计要求存在真实语义矛盾、遗漏或不完整实现。
4. 至少做一次反向误报检查，例如检查调用路径、宏使用、邻近分支、配置开关、替代实现或测试代码混淆。
5. `tool_trace` 至少记录一次真实文件阅读、搜索、命令或分析步骤，说明该步骤如何支撑结论。
6. 结论不能依赖项目名或已知答案；应能迁移到其他设计文档和代码仓。

Feature gap 和 MAY/SHOULD 行为也可以作为 confirmed issue，但必须更严格：

- Feature gap 必须给代码证据，例如相邻实现注释、入口函数缺失分支、构建配置缺失、全局搜索摘要或显式 unsupported/not implemented 证据。
- MAY/SHOULD 必须说明遗漏为什么产生互操作、功能或设计一致性差异，并检查没有其他路径提供该行为。

禁止行为：

- 不得把 regex 命中、权重分数、`semantic_detection` 标记当最终结论。
- 不得针对 F-Stack 或公开 gold issue 硬编码标题、文件或 RFC。
- 不得伪造缺失证据；证据不足写 `probable` 或 `rejected`。
- 不得修改 `code/**`、`Difference/**`、`benchmark.md` 或目标设计文档。

## 5. 结果获取方式

裁判读取：

```text
/result/issues.json
/result/issues.jsonl
/result/00-summary.md
/result/*.md
```

日志与可接续状态：

```text
/logs/trace/**
${AGENT_WORK}/pipeline_state.json
${AGENT_WORK}/agent_review_queue.json
${AGENT_WORK}/agent_loop_contract.json
${AGENT_WORK}/agent_loop_state.json
${AGENT_WORK}/agent_run_ledger.jsonl
${AGENT_WORK}/agent_review_verdicts.jsonl
${AGENT_WORK}/validated_issues.json
${AGENT_WORK}/ranked_issues.json
${AGENT_WORK}/probable_review_queue.json
```

## 6. 完成判定

满足以下全部条件即视为完成：

1. opencode 已写入 `${AGENT_WORK}/agent_review_verdicts.jsonl`。
2. `review`、`report`、`gate` 阶段已执行。
3. `/result/issues.json`、`/result/00-summary.md`、`/logs/trace/final_detection_gate.json` 已生成。
4. `/result/issues.json` 中只包含 `confirmed` issue。
5. 若 confirmed 少于 4 个，报告必须说明证据不足原因，不得补造。

评价目标：

```text
识别 confirmed issues 数量 >= 4
误报率 <= 50%
总检视时长 <= 6 小时
```

## 7. 公开样例回归检查（可选）

公共 F-Stack 样例可以用以下命令检查最终 confirmed 输出是否覆盖公开已知问题：

```bash
python3 ${WORK_ROOT}/tools/scripts/public_fstack_gold_evaluator.py \
  --result ${RESULT_ROOT}/issues.json \
  --output ${LOG_ROOT}/trace/public_fstack_gold_eval.json
```

这是本地回归 oracle，不是正式检测逻辑。它不得被 `prepare-review`、detector、validator、ranker、report 或 gate 自动调用；隐藏评测项目仍必须依赖 opencode 对设计文档和代码的通用语义审阅。
