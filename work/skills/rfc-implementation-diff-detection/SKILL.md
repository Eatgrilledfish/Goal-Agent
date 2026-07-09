---
name: rfc-implementation-diff-detection
description: RFC代码实现不一致识别。读取RFC规范文档与F-Stack C/C++/DPDK/FreeBSD实现代码，提取规范性需求(MUST/SHOULD/MAY)，建立需求→代码追溯矩阵，检测不一致问题，生成证据链报告。只读代码，不修改目标工程。
---

# RFC 实现差异检视流水线

本 Skill 定义 RFC 规范与 C/C++ 网络协议栈实现之间的差异检视流程。全程只读目标代码，所有输出写入 `/result`、`/logs` 和 `.agent-work/`。

## 运行前提

- 目标仓库为 F-Stack（C/C++/DPDK/FreeBSD 网络协议栈），**不存在 `pom.xml`、`maven-settings.xml`**
- 不得运行 Maven / Java 构建
- 不得修改 `code/**`、`Difference/**`、`benchmark.md`
- 只允许写入 `/result/**`、`/logs/**`、`.agent-work/**`

## 目录结构（固定路径）

```text
CODE_ROOT   = /app/code/judge-assets/01_03_ai_implementation_design_difference_detection/code/f-stack
DESIGN_ROOT = /app/code/judge-assets/01_03_ai_implementation_design_difference_detection/Difference
BENCHMARK   = ${DESIGN_ROOT}/benchmark.md
RESULT_ROOT = /result
LOG_ROOT    = /logs
```

## 流水线阶段

### Phase 0: Preflight — 环境初始化

- 确认 `CODE_ROOT`、`DESIGN_ROOT`、`BENCHMARK` 路径存在
- 创建 `.agent-work/`、`/result`、`/logs/trace` 目录
- 写入 `pipeline_state.json` 初始化状态
- 缺失关键输入 → STOP，输出 `missing_competition_inputs`

```bash
python3 work/tools/scripts/rfc_goal_runner.py init
```

### Phase 1: Load RFC Sources — 加载 RFC 文档

- 解析 `benchmark.md`，提取 RFC 列表与 commit 信息
- 批量获取 RFC 原文（本地缓存优先，缺失时从 `rfc-editor.org` 拉取）
- 处理 RFC supersession 链（如 8200→2460, 4861→2461），记录替代关系

```bash
python3 work/tools/scripts/rfc_goal_runner.py load-docs
```

**调用脚本**: `benchmark_reader.py` → `rfc_fetch_convert.py`

### Phase 2: Build Normative Requirements — 提取规范性需求

- 遍历所有 RFC 文档，提取 MUST / MUST NOT / SHOULD / SHOULD NOT / MAY 条款
- 每条需求附 RFC 编号、章节号、原文引用、规范级别
- 输出结构化 IR：`rfc_requirements.json`

```bash
python3 work/tools/scripts/rfc_goal_runner.py extract-spec
```

**调用脚本**: `normative_requirement_extractor.py`

### Phase 3: Index Code — 扫描 C/H 代码

- 递归扫描 F-Stack 下所有 `.c`、`.h` 文件
- 提取函数签名、宏定义、控制流结构、协议常量
- 按协议域（IPv6/IPsec/TCP/UDP/SCTP/ICMP/ND）分类索引

```bash
python3 work/tools/scripts/rfc_goal_runner.py index-code
```

**调用脚本**: `c_code_indexer.py`
**输出**: `code_index.json`

### Phase 4: Requirement-Code Mapping — 需求→代码追溯

- 将每条 RFC 规范需求映射到候选代码位置
- 状态标记：`linked`（已匹配）/ `unlinked`（未匹配）/ `ambiguous`（多候选）
- 利用 `rfc_domain_map.json` 协议域→代码路径映射辅助匹配

```bash
python3 work/tools/scripts/rfc_goal_runner.py map
```

**调用脚本**: `requirement_code_mapper.py`
**输出**: `rfc_code_trace.json`

### Phase 5: Difference Detection — 不一致检测

- 基于 7 种检测类型提出不一致候选：
  1. `hardcoded_limit_mismatch` — 硬编码限制与 RFC 不符
  2. `missing_required_behavior` — 缺失 RFC 要求的 MUST/SHOULD 行为
  3. `wrong_control_flow` — TLV/扩展头遍历链路提前终止
  4. `missing_feature_protocol_gap` — 协议域整体缺失实现
  5. `silent_drop_error_handling_mismatch` — 静默丢弃却未发 ICMP/错误反馈
  6. `timer_delay_behavior_mismatch` — 定时器/延迟/随机化行为不符
  7. `packet_path_mismatch` — 报文路径错误（旁路/转发/丢弃）

- 每个候选必须同时提供 RFC 原文证据和代码证据

```bash
python3 work/tools/scripts/rfc_goal_runner.py detect
```

**调用脚本**: `protocol_inconsistency_detector.py`
**输出**: `candidate_issues.json`

### Phase 6: Evidence Review — 证据审查与误报过滤

- 评估每条候选的置信度（权重：设计证据 0.25 + 代码证据 0.25 + 代码位置 0.15 + 设计位置 0.10 + 误报控制 0.15 + 控制流证据 0.10）
- 规范级别调整：MUST +0.05, SHOULD 不变, MAY -0.10
- 追溯状态调整：linked 不变, ambiguous -0.10, unlinked -0.30
- 分类：`confirmed`(≥0.80) / `probable`(≥0.65) / `rejected`(<0.65)
- MAY 条款降权，无代码证据的候选不输出

```bash
python3 work/tools/scripts/rfc_goal_runner.py review
```

**调用脚本**: `evidence_validator.py` → `issue_ranker.py`

### Phase 7: Report — 生成报告

- 生成机器可读主结果：`/result/issues.json`（schema 见 `output_schema.json`）
- 生成人类可读总览：`/result/00-summary.md`
- 逐 issue 生成证据链报告：`/result/01-*.md`
- 每份报告含 RFC 原文引用、代码位置、检测类型、置信度、证据链

```bash
python3 work/tools/scripts/rfc_goal_runner.py report
```

**调用脚本**: `issue_report_writer.py`

### Phase 8: Final Detection Gate — 最终判定门

- 校验输出完整性：`issues.json` 存在且合法、至少一个单 issue markdown
- 检查问题数量与质量：confirmed + probable ≥ 4 个
- 不足 4 个时记录原因（RFC 获取失败 / 证据不足 / 代码路径无法确认），不得伪造

```bash
python3 work/tools/scripts/rfc_goal_runner.py gate
```

**调用脚本**: `final_detection_gate.py`
**输出**: `/logs/trace/final_detection_gate.json`

## 完成标准

1. `rfc_goal_runner.py run-all` 退出码 0
2. `/result/issues.json` 已生成
3. `/result/00-summary.md` 已生成
4. `/result/` 下至少一个 `01-*.md` 单 issue 报告
5. `/logs/trace/final_detection_gate.json` 已生成，gate 判定通过

## 关键约束

- **不修改目标代码**：所有阶段只读 `code/`、`Difference/`、`benchmark.md`
- **不依赖 Maven/Java**：本题为 C/C++/DPDK/FreeBSD 工程，无 Java 构建
- **不引入 Spring Boot / ShopHub 假设**
- **证据不足不伪造**：未达 4 个 issue 时在 `00-summary.md` 如实说明原因
