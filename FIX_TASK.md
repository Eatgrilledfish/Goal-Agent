请仔细阅读 FIX.md（spec-driven-refactor 分支修复文档，1620行），然后严格按照文档对当前项目进行修复。

核心原则：
- 不要回退架构，不要继续堆文档，优先补工程闭环
- 按 P0 优先级顺序修复
- 每个新脚本必须支持 --root 参数
- 每个新脚本必须输出 JSON 和 Markdown 摘要
- 每个新脚本必须在缺少输入时给出清晰 warning，而不是崩溃
- 不修改 design-docs、README API 基线、test-cases
- 不硬编码公开测试、不吞异常、不统一返回200
- 优先最小 diff

P0 修复顺序：
1. 修复 exception_analyzer.py 的运行 bug（错误的文件路径读取）
2. 修复 api_contract_builder.py 的 required 字段解析
3. 修复 dto_analyzer.py 的全量 DTO 误匹配
4. 强化 contract_checker.py 的 error handling 检查和 response schema checker
5. 修复 trace matrix 判断过粗（implemented/partial/missing/conflict）
6. 新增 repair_task_builder.py
7. 新增 candidate_sandbox.py
8. 新增 patch_selector.py
9. 强化 spec_test_generator.py
10. 强化 stability_runner.py 为完整 gate
