# Interaction Log

正式自验证流程不要求人工确认设计结论；opencode 按 `INSTRUCTION.md` 自主完成
只读探索、角色交接、按需隔离单点 probe、反证和结果生成。probe 能力由仓库和
当前环境自动发现，不要求人工填写构建或测试参数。若运行环境触发外部审批，事件会写入
session 的 `approval_events.jsonl`，而不是在这里补写结论。

`prepare` 自动把原始输入复制到 session-local review roots，后续 Task 不需要人工批准外部目录访问。
