# 自验证输出记录

## 作品形态

- 入口文件：`/INSTRUCTION.md`
- 可运行交付件目录：`/work`
- 主 Skill：`/work/skills/goal-agent-spec-driven/SKILL.md`
- 规格驱动 Subagents：`/work/skills/contract-builder.md`, `code-analyzer.md`, `consistency-checker.md`, `patch-generator.md`, `stability-verifier.md`
- ShopHub Subagents：`/work/skills/shophub-*.md`
- Helper scripts：`/work/tools/scripts/*.py`
- Runtime config：`/work/tools/config/*.json`
- Agent 根目录：`/work`

当前交付不需要安装插件，不需要安装额外命令入口，也不需要把运行资产复制到题目仓库。平台加载 `/INSTRUCTION.md` 后，直接读取 `/work` 内资产并作用于目标题目仓库。

## 规格驱动 Pipeline 架构

```text
Phase 0: Preflight
Phase 1: Build Contracts (API + Business Rules)
Phase 2: Scan Code (Controller/DTO/Service/Repository/Entity/Exception)
Phase 3: Build Trace Matrix + Static Consistency Check
Phase 4: Generate Spec-Driven Tests
Phase 5: Baseline Test Run
Phase 6: Localize & Prioritize Repair Tasks
Phase 7: Fix Loop (per task: 3-5 candidates → sandbox → score → apply best)
Phase 8: Stability Gate (3x rerun + contract re-check + forbidden-change guard)
Phase 9: Report & Deliver
```

## 本地结构自检

必选路径：

```text
/INSTRUCTION.md
/work
/result/output.md
/logs/interaction.md
/logs/trace
```

规格驱动新增文件：

```text
/work/skills/goal-agent-spec-driven/SKILL.md ← 主 Skill
/work/skills/contract-builder.md       ← API Contract + Business Rule 抽取
/work/skills/code-analyzer.md           ← Spring Boot 代码扫描
/work/skills/consistency-checker.md     ← 静态一致性检查 + Trace Matrix
/work/skills/patch-generator.md         ← 多候选补丁生成
/work/skills/stability-verifier.md      ← 验证 + guard + 稳定性

/work/tools/scripts/api_contract_builder.py
/work/tools/scripts/business_rule_builder.py
/work/tools/scripts/spring_scanner.py
/work/tools/scripts/dto_analyzer.py
/work/tools/scripts/exception_analyzer.py
/work/tools/scripts/contract_checker.py
/work/tools/scripts/spec_test_generator.py
/work/tools/scripts/forbidden_change_guard.py
/work/tools/scripts/stability_runner.py
/work/tools/config/audit_priorities.json
```

## 运行成功后的预期输出

在目标题目仓库中按 `/INSTRUCTION.md` 执行后，应生成：

```text
.agent-work/
├── api_contract.json              ← Phase 1: 冻结 API 契约
├── business_rules.json            ← Phase 1: 业务规则
├── repo_map.json                  ← Phase 2: 代码结构地图
├── dto_validation_report.json     ← Phase 2: DTO 校验覆盖
├── exception_coverage.json        ← Phase 2: 异常处理覆盖
├── trace_matrix.json              ← Phase 3: 需求→代码追踪
├── consistency_report.json        ← Phase 3: 一致性检查报告
├── repair_tasks.json              ← Phase 6: 修复任务
├── candidate_patches.jsonl        ← Phase 7: 候选补丁记录
├── forbidden_change_report.json   ← Phase 8: 禁止修改检查
├── stability_report.json          ← Phase 8: 稳定性报告
└── validation_results.jsonl       ← Phase 7-8: 验证结果

.tmp/generated-tests/              ← Phase 4: 生成测试（不提交）
修复报告.md                         ← Phase 9: 最终修复报告
```

最终输出应报告 `DONE`、`BLOCKED` 或 `STOPPED_BY_SAFETY`，并列出：
- Issue 发现/修复/剩余数量
- API 契约状态
- Forbidden-change guard 状态
- Stability rerun 状态
- 验证命令及结果
- 剩余风险
