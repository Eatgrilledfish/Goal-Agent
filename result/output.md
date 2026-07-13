# Goal-Agent 自验证记录

## 结论

本轮先停止了正在运行的 OpenCode 全量验证，再依据完整日志做根因重构。停止状态已确认：LaunchAgent未加载、wrapper/child/process group均不存在，退出码为SIGTERM对应的 `-15`；不是provider或额度中断。

被停止的运行只召回开发 oracle 的一小部分，最终 finding为零。原因不是单一模型失误，而是两类系统缺陷叠加：

- 探索范围过大且raw scout被要求提前完成接近最终证明的工作，产生“receipt全部完成但真实候选召回很低”；
- 并发risk merge/receipt无锁读改写，真实发生过候选被后写覆盖；最后一个risk merge还等待尚未写入的receipt，形成顺序死锁。后续finding目录、task lifecycle trace和多写者phase又会继续阻断已经发现的候选。

这些问题已按根因重构，而不是增加项目专用规则、兼容分支或放松最终证据gate。

## 当前运行架构

正式评测由运行中的OpenCode CLI读取仓库根目录 `INSTRUCTION.md`。比赛入口固定发现：

```text
/app/code/judge-assets/01_03_ai_implementation_design_difference_detection/
```

不需要注册、人工参数、provider配置或 `opencode.json`。目标代码与supplied design只读；运行产物仅写入 `/logs` 和 `/result`。

当前流程：

```text
prepare + 只读快照
→ 模型建立轻量architecture map（只导航）
→ 确定性heading-aware design inventory
→ 输入规模驱动的双向semantic scout plan
→ 全部current handoff/coverage receipts
→ 模型选择最多12个candidate IDs
→ 确定性candidate→claim→task provenance投影
→ fresh spec critic
→ investigator + fresh evidence critic
→ coverage + final judge
→ provisional report + final gate
```

关键约束：

1. Design plan按当前输入规模生成；每slice不超过3500行、最多2个文档，document-local chunk连续，全部in-scope section全局唯一owner。
2. Code plan只从当前architecture实际scope和boundary path生成，递归拆分为互斥anchors；risk-plan gate实际核对每slice不超过1200个文件。
3. Boundary path与linked plane代码完全分离时，boundary获得自己的合法slice，不会被挂到不包含该代码的plane slice中伪装覆盖。
4. `test_surfaces`、architecture IDs和catalog不能成为candidate/task的语义门槛；地图遗漏不会机械删除plane或候选。
5. Raw scout负责高召回线索：原子设计义务、真实代码lead或结构化absence lead和最低限度反证即可输出`uncertain`；完整入口、替代/补偿、配置/注册/构建和误报闭环由investigator完成、critic独立挑战。
6. 每个semantic scout最多12条raw observation；全局模型只排序最多12个ID进入深查。Helper不按关键词、regex、项目名、固定分数或已知答案判断。
7. Design-source bundle先在staging完整构建，成功才整体替换；失败不发布半成品，成功重跑会删除已不在current plan中的旧source。
8. Catalog身份来自source manifest显式provenance，不按目录名猜测；普通用户设计位于名为`catalog`的目录时仍可正常in-scope。
9. 累计JSONL使用跨进程sidecar lock，发布使用同目录唯一临时文件和原子replace；两个并发scout不会丢更新。
10. Risk merge只验证当前slice，不等待全局receipts；全局closure由controller/final gate判断，消除最后一个scout的顺序死锁。
11. `pipeline_controller.py`覆盖 `map_architecture → build_inventory → build_scout_plan` bootstrap，并作为phase、pending IDs和next action的唯一真相源。局部checkpoint和其他helper只写ledger。
12. 当前无人值守链路没有完整dynamic probe执行协议，因此明确拒绝`selected`，避免进入永远无法闭合的分支。

## 确定性验证

执行：

```bash
python3 -m pytest -q work/tools/tests --disable-warnings
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

结果：

```text
456 passed in 116.02s
py_compile: passed
git diff --check: passed
```

回归覆盖包括：

- Controller bootstrap、stable task-plan/lifecycle snapshot、hard deadline terminal状态；
- design/source staging整体替换和失败回滚；
- heading inventory、3500行/2文档分片和大文档连续chunk；
- 1501文件broad plane拆分、1200文件strict gate、anchor全局不重叠；
- boundary-only entry与linked core分离分片；
- architecture test metadata不再删除plane或candidate；
- current receipt的session、plan、handoff、coverage hash和精确scope；
- 并发risk merge、并发receipt、agent ledger append不丢更新；
- risk merge与receipt顺序解耦；
- candidate→claim→task一对一lineage与selected-frontier gate；
- canonical finding发布、task lifecycle trace登记、critic和verdict链；
- helper不覆盖Controller phase；
- provisional report、final gate、只读完整性和机器可读结果。

## 公开材料静态breadth smoke

用本地公开材料重新生成current inventory和plan，结果：

```text
design document groups: 24
bounded inventory sections: 2418
design-to-code scouts: 18
code-to-design scouts: 13
total semantic scouts: 31
measured maximum design slice: 3479 lines（hard cap 3500）
measured maximum code slice: 1194 files（hard cap 1200）
risk-plan validation: passed
```

开发侧oracle仅在运行资产之外用于对照。静态机会审计结果为：六项均各自拥有包含规范正文的design入口和包含相关实现路径的code入口，机械validator阻断为 `0/6`。这只证明它们不会在探索前被计划或gate排除，不等同于真实模型已经召回。

运行资产中没有公开工程名、协议专用检测器、固定RFC章节、固定代码路径/符号、公开答案、关键词评分表或固定issue数量逻辑。相同流程可用于不同文档格式、代码语言和仓库结构。

## 尚需真实全量run确认

本轮没有在修改后重新启动耗时的OpenCode全量验证，因此不宣称已经实际召回全部开发oracle。下一次后台全量run需要实测：

- raw与selected candidate的真实召回；
- investigator/critic后的confirmed数量与误报率；
- 31个scout在最多两个并发下的墙钟与provider稳定性；
- `/result/issues.json`、`issues.jsonl`、`00-summary.md`和单issue报告的最终gate。

当前剩余风险主要是模型是否真实履行每个receipt声明的审阅范围；机械系统可以验证scope、hash、工具链和证据lineage，但不能用规则替代语义理解。下一次run若仍漏召回，应依据每个小slice的候选和tool trace定位语义失败，不应再次放大scope、放松证据或添加项目专用答案。
