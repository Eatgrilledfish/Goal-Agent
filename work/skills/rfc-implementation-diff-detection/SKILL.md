---
name: rfc-implementation-diff-detection
description: 设计文档/RFC 与目标代码实现不一致识别。helper 脚本负责加载设计、提取需求、索引代码、生成候选和证据包；运行中的 opencode agent 负责按需探索代码与设计文档并写出最终语义 verdict。只读目标工程。
---

# 设计/代码实现差异检视流程

本 Skill 定义“设计依据 ↔ 代码实现”差异检视流程。它兼容 RFC 形式的协议设计文档，也允许 opencode 对非 RFC 的设计文档进行人工智能语义调查。全程只读目标代码，所有输出写入 `/result`、`/logs` 和 `.agent-work/`。

## 核心原则

- 候选生成是 recall，不是裁决。
- regex、权重、domain map、`semantic_detection` 只能帮助排序和找上下文，不能决定 final confirmed。
- final confirmed/probable/rejected 必须来自 opencode 写入的 `${AGENT_WORK}/agent_review_verdicts.jsonl`。
- opencode 可以确认候选，也可以新增 `AGENT-DISCOVERED-*` issue，只要提供完整设计证据和代码证据。
- 不得硬编码公开项目、文件名、RFC 编号或已知 gold issue。

## 目录约定

```text
CODE_ROOT   = <ASSET_ROOT>/code/<target-project>
DESIGN_ROOT = <ASSET_ROOT>/Difference or design/design-docs/docs
BENCHMARK   = <DESIGN_ROOT>/benchmark.md or the design entry document
RESULT_ROOT = /result
LOG_ROOT    = /logs
```

`rfc_goal_runner.py` 在未显式传参时会自动发现 `ASSET_ROOT/code/` 下的目标仓库。公共样例是 F-Stack；隐藏评测不应假设项目名固定。

## 流水线阶段

### Phase 0: Preflight

创建 `.agent-work/`、`/result`、`/logs/trace`，记录输入路径。

```bash
python3 work/tools/scripts/rfc_goal_runner.py init
```

### Phase 1: Load Design / RFC Sources

解析设计入口，公共 RFC 任务会提取 RFC 列表并缓存 RFC 文档。若设计不是 RFC，opencode 仍应在审阅阶段直接读取 `DESIGN_ROOT` 中的设计文档。

```bash
python3 work/tools/scripts/rfc_goal_runner.py load-docs
```

调用脚本：`benchmark_reader.py`、`rfc_fetch_convert.py`

### Phase 2: Scope Plan

生成轻量代码清单，选择第一轮重点设计/RFC 范围，避免把引用性 RFC 或无代码锚点的文档放大为主任务。

```bash
python3 work/tools/scripts/rfc_goal_runner.py scope-plan
```

调用脚本：`code_inventory_lite.py`、`rfc_scope_planner.py`、`rfc_scope_plan_validator.py`

### Phase 3: Extract Requirements

从 RFC/设计文档中抽取规范性或约束性语句。对 RFC 使用 MUST/SHOULD/MAY；若没有 RFC manifest 或没有抽到 RFC requirement，则从普通设计文档中抽取 modal design requirement，保留 requirement text、source doc 与章节上下文，并用 generic mapper 做跨项目召回。

```bash
python3 work/tools/scripts/rfc_goal_runner.py extract-spec
```

调用脚本：`normative_requirement_extractor.py`

### Phase 4: Index Code

扫描目标仓库 C/C++/header/build 文件，提取函数、宏、符号和片段。索引器必须支持多行函数签名与 FreeBSD/KNF 风格，但不得把已知 issue 写成特例。

```bash
python3 work/tools/scripts/rfc_goal_runner.py index-code
```

输出：`.agent-work/code_index.json`、`/logs/trace/code_index_stats.json`

### Phase 5: Requirement-Code Mapping

将需求映射到可能相关的代码位置，输出追溯矩阵。映射结果是调查入口，不是证据结论。

```bash
python3 work/tools/scripts/rfc_goal_runner.py map
```

输出：`.agent-work/rfc_code_trace.json`

### Phase 6: Candidate Recall

基于需求、代码索引、追溯矩阵提出候选不一致。候选可以使用规则或模式做召回，但不得作为最终 issue 输出。

```bash
python3 work/tools/scripts/rfc_goal_runner.py detect
```

输出：`.agent-work/candidate_issues.json`

### Phase 7: Prepare Agent Review

这是 opencode 的稳定入口。它会先刷新 Phase 1-6 的确定性召回 artifacts，再把候选、设计片段、代码上下文、相关需求、建议检索命令打包给 opencode。

```bash
python3 work/tools/scripts/rfc_goal_runner.py prepare-review
```

输出：

```text
${AGENT_WORK}/agent_review_queue.json
${AGENT_WORK}/agent-review/*.json
/logs/trace/agent_review_queue_summary.json
```

`agent_review_queue.json` 包含 `agent_work`、`verdict_output` 和每个 item 的 `bundle_abs_path`，opencode 应使用这些绝对路径接续审阅。
queue 的 `items` 先列 `protocol_domain_review`，再列 `candidate_review`。opencode 应先做协议/设计域级调查，再用候选 bundle 补充确认或拒绝。

### Phase 8: Opencode Semantic Review

这是唯一的语义裁决阶段，由 opencode 执行，不由 Python helper 自动替代。

opencode 必须：

1. 读取 `${AGENT_WORK}/agent_review_queue.json`。
2. 逐个读取 queue item 的 `bundle_abs_path`。
3. 对候选证据做 just-in-time 调查：`rg`、`sed`、`nl`、读调用者、读相邻宏、读设计上下文。
4. 对候选未覆盖的设计要求继续做全局调查。
5. 写 queue 中 `verdict_output` 指向的 JSONL 文件。

JSONL verdict 的 `status` 只能是：

- `confirmed`：进入正式结果候选，仍需 schema/evidence 校验。
- `probable`：进入 review queue，不写入 `/result/issues.json`。
- `rejected`：丢弃并记录原因。

### Phase 9: Validate, Rank, Report, Gate

`review` 阶段只消费 opencode verdict；缺少 verdict 时失败，不把 helper 分数升级为 issue。

```bash
python3 work/tools/scripts/rfc_goal_runner.py review
python3 work/tools/scripts/rfc_goal_runner.py report
python3 work/tools/scripts/rfc_goal_runner.py gate
```

调用脚本：

- `evidence_validator.py`：校验 opencode verdict 的字段和证据完整性，写 `validated_issues.json`。
- `issue_ranker.py`：只把 `confirmed` 写入 `ranked_issues.json`；`probable` 写入 `probable_review_queue.json`。
- `issue_report_writer.py`：生成 `/result`。
- `final_detection_gate.py`：确保主结果只含 confirmed，且 schema 合法。

## Confirmed Issue 要求

每个 confirmed issue 必须包含：

- 设计证据：设计文档/RFC 路径、章节或标题、短引用。
- 代码证据：repo-relative 文件、行号范围、符号、代码片段。
- 不一致解释：设计要求与实现行为之间的真实语义矛盾或遗漏。
- 影响说明：协议、功能、运行时或用户可见影响。
- 误报控制：至少一个反向检查。
- 泛化说明：为什么这不是针对公开项目或已知答案的硬编码。

Feature gap 可以 confirmed，但需要代码侧证据支撑缺失判断，例如显式 unsupported/not implemented 注释、相邻入口函数缺少分支、构建/注册表缺失、全局搜索摘要或替代实现排除记录。

MAY/SHOULD 行为可以 confirmed，但必须解释省略该行为造成的互操作、兼容、功能或设计一致性影响，并证明没有其他代码路径实现该行为。

## 完成标准

1. `${AGENT_WORK}/agent_review_verdicts.jsonl` 存在。
2. `/result/issues.json`、`/result/issues.jsonl`、`/result/00-summary.md` 已生成。
3. `/result/issues.json` 中只包含 `confirmed` issue。
4. `/logs/trace/final_detection_gate.json` 已生成。
5. confirmed 少于 4 个时，报告说明证据不足原因，不伪造。

## Public Fixture Regression

公共 F-Stack 样例有一个只用于本地回归的 gold evaluator：

```bash
python3 work/tools/scripts/public_fstack_gold_evaluator.py \
  --result /result/issues.json \
  --output /logs/trace/public_fstack_gold_eval.json
```

它检查最终 confirmed 输出是否覆盖公开样例的已知问题，并估算额外 confirmed 比例。该 evaluator 不得被 detector、mapper、validator、ranker、report 或正式 gate 调用；隐藏评测仍以 opencode 的通用设计/代码语义审阅为准。

## 禁止事项

- 不修改目标代码或设计文档。
- 不运行与目标技术栈无关的构建系统。
- 不引入 Spring Boot、ShopHub、F-Stack 公开答案等项目特定假设。
- 不用规则分数、regex 命中或候选标题替代 opencode 语义判断。
