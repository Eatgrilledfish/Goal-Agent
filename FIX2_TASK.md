请仔细阅读 FIX2.md（spec-driven-refactor 最新修复文档，1281行），严格按照文档对项目进行修复。

核心原则：
- 不要回退架构，不要继续扩文档，优先修运行级缺陷和工程闭环
- 按文档第18节的推荐顺序修复：P0-1 到 P0-7 依次修复
- 每个脚本必须支持 --root，必须能通过 python -m py_compile
- 缺少输入时输出 warning，不崩溃
- 不修改 design-docs、README API 基线、test-cases

修复顺序：
1. candidate_sandbox.py 加 import re
2. api_contract_builder.py 跳过分隔行
3. spec_test_generator.py 修复多 success status 断言
4. candidate_sandbox.py 保留 patch_file
5. patch_selector.py 使用真实 patch_file
6. candidate_sandbox.py git worktree 真 sandbox
7. candidate_sandbox.py 真运行 generated tests
8. stability_runner.py full-gate 真运行 generated tests
9. repair_task_builder.py suspected_files 真实路径
10. contract_checker.py baseline P0 vs candidate 新增 P0
