# 人工交互记录

用户主要要求如下：

1. 根据 `design-document.md` 完成 ShopHub Goal Runner 项目。
2. 将项目上传到 GitHub。
3. 支持在 OpenCode 比赛环境中运行。
4. 通过一个 CLI slash command `/shophub` 持续运行，直到完成比赛 goal 或触发安全停止。
5. 保留一个用户可见入口，同时内部注册并调用多个 subagent 协同工作。
6. 按比赛提交规范保存结构：
   - `/INSTRUCTION.md`
   - `/work`
   - `/result/output.md`
   - `/logs/interaction.md`
   - `/logs/trace`
7. 用户提供真实比赛题库 `https://gitcode.com/oyealex/HW-ICT-CMP-04` 后，发现旧实现错误依赖 `API基线文档.md`、`比赛说明.md`、`黑盒用例说明.md`。
8. 用户明确要求不用做成插件，改为黑箱 OpenCode 环境可运行的 skill + agent 交付。

当前仓库是可安装运行的 OpenCode skill + hidden-agent Goal Runner 提交包。
