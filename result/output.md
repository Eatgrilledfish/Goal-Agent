# Goal-Agent 自验证记录

## 结论

本次确认此前“一个正确候选都没有”是流程设计缺陷，不是模型偶然失手，也不是provider额度问题。旧流程在深度调查前已经丢失了召回：

- 先按代码地图裁剪设计section，未分配的规范永远不会进入候选；
- 按代码slice限制design-to-code证据，导入实现、adapter和未映射路径被挡在搜索外；
- 强迫每个sweep产出样本，使合规样本占用候选预算；
- 部分sweep完成后就开始全局选题，形成first-arrival bias；
- claim/task由模型重复改写，候选的设计分支和代码起点会在handoff中漂移；
- 局部checkpoint的`complete`曾被错误复制成全局complete；
- 模型大量时间用于生成inventory和修复大schema，而不是阅读设计与代码。

这些问题属于严重架构缺陷，已按根因重构。

## 当前运行架构

正式评测只需让运行中的OpenCode CLI读取仓库根目录`INSTRUCTION.md`。入口固定使用：

```text
/app/code/judge-assets/01_03_ai_implementation_design_difference_detection/
```

不需要注册、人工参数、provider配置或`opencode.json`。目标代码与supplied design只读；运行结果写入`/result`，状态和证据写入`/logs`。

新流程为：

```text
prepare + 只读快照
→ 轻量architecture map
→ 确定性design inventory
→ 双向semantic scouts
→ 全部scout receipts完成
→ 模型只排序candidate IDs
→ 确定性candidate→claim
→ fresh spec critic
→ 确定性claim→task
→ investigator + fresh evidence critic
→ 确定性coverage记账
→ final judge + report + final gate
```

关键变化：

1. Design-to-code scouts按互斥document groups覆盖全部in-scope设计，并可搜索整个代码仓，不受预先architecture map裁剪。
2. Code-to-design scouts按不重叠top-level anchors探索，并可从完整design inventory动态检索规范。
3. Scout只输出疑似差异；合规实现不进入候选，零候选合法。
4. 每个scout独立写receipt；全部receipt完成前，controller拒绝candidate selection。
5. Receipt机械核对handoff候选是否全部merge，避免候选静默丢失。
6. 模型只选择最多12个candidate IDs；requirement、source range、code evidence、direction和mismatch signal由helper逐值投影成claim/task。
7. Spec critic和investigator只写最小语义输出；identity、digest、设计证据、代码snippet和recommendation由materializer生成。
8. Coverage只按已有证据记录investigated/gap，不再启动额外coverage LLM或fallback supplement。
9. 局部checkpoint只能表示局部进展；只有final gate能把全局session置为complete。
10. `pipeline_controller.py`机械给出唯一下一步，防止跳过breadth、claim review、investigation或critic。

运行链路没有项目名、协议名、固定路径/符号、公开答案、regex检测器、关键词评分表或固定issue数量逻辑。模型仍负责规范理解、代码探索、候选语义、调查、反证和最终判断；helper只负责检索范围分配、schema、provenance、状态机与输出验真。

## 自验证

执行：

```bash
python3 -m pytest -q work/tools/tests --disable-warnings
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

结果：

```text
416 passed in 115.04s
py_compile: passed
git diff --check: passed
```

测试覆盖：

- 完整design source物化与确定性auto-inventory；
- requirement-centric双向scout plan和互斥code anchors；
- 空/非空scout receipt、stale digest、foreign sweep和候选漏merge拒绝；
- candidate→lookup→claim→task一对一lineage；
- 合规observation不能进入frontier；
- task代码起点、方向、architecture IDs和candidate ID不可漂移；
- 最小spec review/finding materializer的字段覆盖攻击拒绝；
- investigator、probe、critic、judge的identity和evidence绑定；
- 局部complete不能终止全局session；
- controller从scouts到final的前置条件短路；
- coverage、报告、只读完整性、clock、trace和final gate；
- 普通设计与catalog/外部HTTPS设计来源；
- 完整小型夹具从prepare到confirmed-only报告的端到端gate。

使用本地F-Stack材料的确定性breadth smoke结果：

```text
design document groups: 24
bounded inventory sections: 75
inventory size: 65,789 bytes
design-to-code scouts: 4
code-to-design scouts: 2
in-scope document groups owned: 23（剩余catalog为informational）
risk-plan validation: passed
```

相比旧run中约2.1MB的模型手写inventory，新inventory约65KB；所有实际设计文档均有owner，代码scouts的primary anchors不重叠。

## 仍需由真实完整模型run确认

单元/端到端夹具证明新的状态机和证据链不会再机械丢失候选，但不能代替OpenCode在真实F-Stack上的完整语义运行。下一次后台全量验证仍需实测：

- 隐藏正例最终召回数量；
- confirmed中的误报率；
- scout、investigation、critic和总墙钟；
- 模型在真实长session中是否持续遵循handoff和fresh-session约束。

在未完成新的全量模型run前，不宣称已经实际召回全部隐藏答案；本次交付解决的是导致“零召回”的结构性原因，并已通过确定性breadth与完整回归验证。
