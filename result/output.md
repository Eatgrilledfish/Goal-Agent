# Goal-Agent 自验证记录

## 当前结论

本轮没有继续在旧框架上增加规则或放宽证据gate，而是重做了发现阶段的最小工作单元。此前多版不稳定的核心原因是：自由scout会按显著性自行挑题；行为×lens矩阵又把预算消耗在填表和提前证明；机械receipt只能验证声明，不能证明模型真实理解。开发harness还曾选到不包含目标条款的文档首slice，导致检索失败与判断失败混在同一个召回分数里。

当前正式链路改为：

```text
prepare + 只读快照
→ 轻量architecture map（只导航）
→ 确定性design inventory与bounded plan
→ fresh design-only obligation extractor
→ helper绑定source/section/hash，生成原子义务队列
→ fresh scout逐义务搜索全仓并直接对照
→ source-only blind negative review（每批最多4项、独立fresh session）
→ helper合并质疑项并物化canonical candidate与逐义务coverage
→ candidate selection（最多12）
→ spec critic → investigator → evidence critic
→ coverage → final judge → provisional report → final gate
```

Code-to-design仍作为补充入口：互斥code anchors逐项反查完整design inventory。正式逻辑不包含公开工程名、协议专用规则、固定路径、关键词评分、公开答案或固定issue数。

## 本轮关键修复

1. Design slice从3500行/最多2文档缩为1200行/1文档连续range；全部in-scope sections仍全局唯一owner。
2. 新增fresh `obligation-extractor`。它只读设计，把精确值/边界、集合和链推进、时序/条件副作用、路由/dispatch及能力要求拆成原子义务，不读取代码。
3. 每个assigned section必须至少产出一个义务，或显式记录无可实现义务及原因，不能静默跳过。
4. 每个义务只选一个主要比较模式：`contract_mechanics`、`temporal_conditional`或`routing_capability`；不再生成行为×维度矩阵。
5. 这三个mode也是claim、task、coverage和final gate的唯一review vocabulary，消除了旧8-lens体系在后半程把新候选判为unknown lens的问题。
6. `obligation_queue.py`校验设计source range属于current section、读取原文摘录、生成稳定义务ID并绑定current plan/inventory hash。
7. Risk scout按queue顺序逐义务给出`candidate/no_mismatch`、实际检索与countercheck；raw阶段保留高召回职责，完整证明仍由investigator/critic完成。
8. 模型不再复制session、sweep、digest、direction、architecture IDs或design-origin完整requirement。`scout_materializer.py`从current plan/queue注入这些字段。
9. 模型只写slice内`candidate_key`；helper按`sweep + key`生成全局稳定`observation_id`，避免并发scout都写`CANDIDATE-1`造成候选冲突。
10. Canonical coverage逐义务或逐anchor绑定所有候选一次且仅一次；receipt重验queue/plan digest、顺序、ownership和handoff hash。
11. 修复正式risk validator把合法空`exceptions/ambiguities`数组误判为缺失的问题；新增materialized candidate直接通过正式risk schema的集成测试。
12. 新增blind negative coverage review：scout准备关闭的每个义务/anchor进入不含原disposition、candidate或scout reasoning的source-only packet，并切成每批最多4项；每批使用不同fresh provider session，逐项先搜索再读取窄源码窗口。每个verdict前必须结构化记录入口、推进/转换、guard/bound、终止/出口、剩余适用工作和一次替代/补偿反查；直接证据和反查充分后立即结束当前item。质疑项机械升级为candidate，只有独立upheld才保留`no_mismatch`。Helper只校验current digest、逐项覆盖、证据字段存在、candidate绑定和session隔离，不使用领域语义规则。
13. Scout的每slice 12条上限不再错误压制blind challenges：初始Scout仍受限，Reviewer推翻项全部保留到canonical ledger，再由全局selector最多选择12项进入调查。

## 确定性验证

执行：

```bash
python3 -m pytest -q work/tools/tests --disable-warnings
python3 -m py_compile work/tools/scripts/*.py
git diff --check
```

当前结果：

```text
471 passed in 115.36s
py_compile: passed
git diff --check: passed
```

新增回归覆盖包括：

- 设计source必须落在assigned section且单义务引用不超过80行；
- 每个section必须有义务或显式empty reason；
- 模型不能写tool-owned envelope或canonical observation ID；
- local candidate key被投影为全局稳定ID；
- design requirement和review mode从current queue逐值投影；
- code-origin candidate必须在primary anchor内有代码证据；
- obligation/anchor coverage顺序、candidate ownership和queue digest重验；
- 1200行/1文档design plan、大文档拆分、section唯一ownership；
- materialized candidate可通过正式risk handoff schema；
- 既有controller、并发发布、claim、investigation、critic、report和final gate回归保持通过。
- blind review packet不泄露原disposition或scout reasoning，review顺序完整且scout与所有batch sessions彼此不同；challenged项进入canonical candidate，upheld项才可关闭。
- `negative_review.py batch/assemble`按正式命令行无`--state-root`运行成功，避免函数级测试通过但orchestrator入口失败。

## 本地分层模型诊断

公开答案仅存在于被git忽略的`.agent-work/dev-eval`本地评测器，不进入`/work`、`INSTRUCTION.md`、Skill、正式prompt或正式检测逻辑。Pair诊断会从本地fixture提供设计/代码证据坐标，以单独测量判断能力。

新的开发harness分层报告：

1. pair judgement：正确设计范围和相关代码闭包均已提供，检查模型语义判断；
2. code retrieval：只提供设计范围和完整代码仓，检查义务驱动实现检索；
3. design retrieval：使用正式inventory/plan检查目标条款是否进入义务队列；
4. raw recall：对全部canonical candidates做本地结果对照；
5. pipeline survival：检查候选是否在selection、claim、investigation和final gate中保留。

三阶段pair回归曾达到5/6，暴露出旧review packet携带Scout肯定性notes造成上下文锚定：Reviewer虽使用新session且读到限制代码，仍重复了Scout的错误结论。source-only blind batches随后重新抽取16个义务；Scout仍漏掉此前唯一漏项，blind reviewer则独立将其升级为candidate，证明去除Scout结论能恢复该漏项。该次最多4项的历史回归耗时1348.5秒，但单batch仍使用约6.7万至10.2万token并整读大型源码。

中间版本曾把batch固定收敛为最多2项、逐项search-first和不超过240行的源码窗口，并增加通用证据边界：实现动机、常见实践或推测意图不能替设计补写例外。相同source-only双项packet的fresh回归中，第一版虽把总token降至26,097，却因模型自行补写设计例外而仍漏报；加入证据边界后，新的独立provider session正确将该项判为`challenged`。该实验说明固定2项既非必要也非充分，却把reviewer调用数近乎翻倍，因此恢复每批最多4项。

恢复4项后的回归进一步暴露出自然语言要求可能被模型跳过：一次有界读取运行虽然读到相关控制流，却没有在verdict中核算循环终止条件而漏报。当前因此保留source-only、窄窗口、设计证据边界和fresh session，并要求每项结构化填写六个`execution_accounting`字段；helper只校验字段非空，不以字段内容作领域判断。最终同一4项packet的fresh回归中，此前漏项被正确判为`challenged`，本地ignored oracle确认命中；另有一个独立guard-path候选。总token为57,302，12次源码读取全部带offset和不超过240行的limit，没有整文件读取。与双项成功回归的54,416 token相比只增加约5%，但reviewer调用数减半。正式prompt没有得到旧结论、公开答案或项目专用判断规则。

## 尚未宣称的结果

单个fresh-review漏项回归通过不等于完整六对局部gate已经通过，也不等于完整流水线已经生成最终报告。本轮尚未重新运行六对局部gate或完整OpenCode run，因此不宣称最终issues数量或误报率已经达标；下一步应先运行完整便宜局部盲测，达到门槛后才进入完整链路，并以最终`/result/issues.json`、`issues.jsonl`、`00-summary.md`、单issue报告和final gate为准。
