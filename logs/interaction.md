# 人工交互记录

用户主要要求如下：

1. 根据 `design-document.md` 完成 ShopHub Goal Runner 项目。
2. 将项目上传到 GitHub。
3. 支持在 OpenCode 比赛环境中运行。
4. 通过 `/INSTRUCTION.md` 作为平台加载入口持续运行，直到完成比赛 goal 或触发安全停止。
5. 保留一个用户可见入口，同时内部加载并调用多个 subagent 协同工作。
6. 按比赛提交规范保存结构：
   - `/INSTRUCTION.md`
   - `/work`
   - `/result/output.md`
   - `/logs/interaction.md`
   - `/logs/trace`
7. 用户提供真实比赛题库后，要求按题库真实结构读取 `README.md`、`design-docs/`、`code/` 和 `test-cases/`，且不能依赖固定仓库名。
8. 用户明确要求不用做成插件，改为黑箱 OpenCode 环境可运行的 skill + agent 交付。
9. 用户要求严格按照作品格式，根目录不保留冗余文件，所有运行交付件都放入 `/work`，并让 agent 视角下的根目录为 `/work`。
10. 用户确认比赛环境有本机 Maven，且 `maven-settings.xml` 提供内网镜像配置；作品应使用本机 Maven 与该 settings 文件。

当前仓库是由 `/INSTRUCTION.md` 直接驱动的 OpenCode skill + subagent Goal Runner 提交包，subagent 定义位于 `/work/skills/*.md`。
