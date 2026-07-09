---
name: goal-agent-spec-driven
description: 已迁移为 RFC 实现差异检视系统。本 Skill 现仅作重定向入口，指向 work/skills/rfc-implementation-diff-detection/SKILL.md（F-Stack C/C++/DPDK/FreeBSD 工程的 RFC/Spec-first 不一致识别，只读代码、不修改目标代码、不依赖 Maven/Spring Boot）。
---

# Goal Agent — 已迁移

本项目已从 ShopHub Spring Boot 设计-实现一致性修复系统迁移为 **RFC 实现差异检视系统**（F-Stack C/C++/DPDK/FreeBSD 网络协议栈）。

请加载新的 Skill 定义：

```text
work/skills/rfc-implementation-diff-detection/SKILL.md
```

新的入口为 `INSTRUCTION.md` → `rfc-implementation-diff-detection/SKILL.md` → `rfc-diff-orchestrator` → subagents + deterministic scripts。

旧的 ShopHub / Spring Boot / Maven 相关逻辑不再使用。目标仓库为 F-Stack C/C++ 工程，流程只读代码、只输出差异报告、不修改目标代码。
