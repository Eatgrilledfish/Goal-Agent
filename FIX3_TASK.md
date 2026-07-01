请仔细阅读 FIX3.md（最后一轮比赛级加固文档，694行），严格按照文档执行。

这次不再扩架构，重点是比赛入口和工程细节：

1. **重写 INSTRUCTION.md**：面向比赛平台和 opencode，明确 SUBMISSION_ROOT/PROJECT_ROOT/WORK_ROOT，所有脚本路径使用 $WORK_ROOT/tools/scripts，明确 repair_task_builder + candidate_sandbox + patch_selector 必跑，明确 selected patch 应用和回滚，明确 baseline 保存和 output.md 格式。

2. **修改 .gitignore**：增加 .tmp/

3. **确保 baseline_consistency_report 保存**：contract_checker 后 cp consistency_report.json → baseline_consistency_report.json

4. **执行完整 dry-run**：所有脚本按顺序跑一遍，确认不崩溃。

禁止：修改 design-docs、README API 基线、test-cases，硬编码、吞异常、统一 200。
