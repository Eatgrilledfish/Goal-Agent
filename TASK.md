## 项目格式（必须严格遵守）

这是算法比赛项目，目录结构如下：

```
INSTRUCTION.md          ← 统一入口，平台加载此文件启动作品
work/
  skill/SKILL.md        ← 主 Skill，定义主流程、能力、运行规则
  skills/*.md           ← subagent 定义（planner/solver/reviewer等）
  tools/scripts/*.py    ← Python脚本，被skill调用
  config/               ← 配置文件
result/
  output.md             ← 自验证输出
  screenshot/
logs/
  interaction.md        ← 人机交互记录
  trace/                ← 执行轨迹日志
problem_statement/
  PROBLEM.md
```

不得创建 work/goal_agent/ 这样的 Python package。所有能力用 skill/subagent markdown + tools/scripts Python 脚本实现。

## 你的任务

请仔细阅读 DESIGN.md（1872行的完整重构设计文档），将其设计思想适配到上述比赛格式下：

### 核心改造方向（来自 DESIGN.md）

1. 规格驱动流程：API Contract -> Business Rules -> Trace Matrix -> Static Check -> Generated Tests -> Patch Candidates -> Sandbox -> Scoring -> Stability
2. MVP优先（DESIGN.md 第24节）：API基线解析 -> Controller路由/DTO/ExceptionHandler扫描 -> contract checker -> generated tests -> patch generator -> forbidden-change guard -> mvn test -> stability rerun
3. 第一优先级模块（DESIGN.md 第23节）：api_baseline_parser, design_doc_parser, api_contract_builder, business_rule_builder, spring_route_analyzer, dto_analyzer, exception_analyzer, contract_checker, forbidden_change_guard, test_runner

### 实现方式

- work/skill/SKILL.md -> 主流程编排，定义 pipeline 步骤，调用 subagent
- work/skills/contract-builder.md -> 负责 API contract + business rule 抽取
- work/skills/code-analyzer.md -> 负责 Spring Boot 代码扫描（Controller/DTO/Exception/Repository）
- work/skills/consistency-checker.md -> 负责静态一致性检查 + trace matrix
- work/skills/patch-generator.md -> 负责候选补丁生成
- work/skills/stability-verifier.md -> 负责验证 + forbidden-change guard + 稳定性重跑
- work/tools/scripts/ -> 增强现有脚本，新增：
  - api_contract_builder.py, business_rule_builder.py
  - spring_scanner.py, dto_analyzer.py, exception_analyzer.py
  - contract_checker.py, spec_test_generator.py
  - forbidden_change_guard.py, stability_runner.py

### 关键约束

- 不修改 API基线文档、design-docs、test代码
- 不硬编码公开测试
- 不吞异常、不统一返回200
- 优先最小diff
- INSTRUCTION.md 是统一入口
- 保留 work/tools/scripts/ 下现有的 shophub_goal_runner.py 等脚本，在其基础上增强

先做 MVP 闭环，再扩展。
