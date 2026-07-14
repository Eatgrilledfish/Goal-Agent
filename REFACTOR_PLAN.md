# Goal-Agent 语义召回重构方案

## 结论

多轮修改后仍不能稳定召回，不是某一个prompt措辞错误，而是发现阶段的抽象层选错了。系统曾在两个极端之间反复摆动：

- 把大段设计和大代码域直接交给自由scout，模型只关注显眼主题，形式上读完、实际上漏掉低显著性的数值、时序、可选副作用、链遍历和跨边界路由；
- 为防漏项建立“行为×风险镜头”矩阵，再让coverage/critic提前证明每个格子，模型把预算消耗在填表和排除少数候选，真正的代码对照反而变浅。

确定性gate只能证明文件、ID、hash和声明的scope完整，不能证明模型真的理解了规范。因此继续增加receipt、矩阵、评分或全量重跑不会解决召回问题。

## 根因

### 1. 测试harness没有测到目标设计条款

旧的便宜盲测按文档名选择第一个slice。以本地RFC 4861为例，被选范围止于约2373行，而需要检验的章节在2661、3355和3439行附近。所谓“六个代表切片”并未包含其中三个目标规范，失败结果混合了设计检索失败与代码判断失败，无法指导修复。

### 2. 发现单元过大

3500行/2文档的design slice仍包含大量独立义务。模型在一次上下文中同时做规范抽取、代码检索、适用性、反证和结果格式，容易按熟悉度自行挑少数检查点。Receipt只证明它输出了coverage字段。

### 3. 义务、实现清单和审查维度混在一起

规范中的一个原子要求才是应被逐项消费的工作单元。预先枚举代码行为会受architecture map遗漏影响；把每个义务复制到多个lens会制造大量重复工作。两者都会稀释真正的设计↔代码比较。

### 4. 模型承担了过多机械字段

让scout复制session、plan digest、direction、architecture IDs和完整requirement既浪费token，也会因复制错误触发repair。语义模型应只写它判断出的义务、实际行为和证据；current运行envelope应由helper注入。

### 5. 开发评测只给出最终召回数

一次全量run为零时，无法区分：设计条款没取到、条款抽取漏了、代码没检索到、对照判断错了、候选被选择阶段丢弃，还是后续证据gate拒绝。没有分层harness，修改只能靠猜。

## 新的最小主链路

```text
输入发现 + 只读快照
        ↓
轻量 architecture map（只给code-origin导航）
        ↓
确定性 design inventory + bounded plan
        ↓
每个design slice（≤1200行、1个文档）
        ↓
fresh design-only obligation extractor
        ↓
helper绑定source/section/hash，形成原子义务队列
        ↓
fresh scout逐义务搜索全仓并直接对照
        ↓
helper注入session/sweep/digest并校验逐义务coverage
        ↓
candidate selection（≤12）
        ↓
spec critic → investigator → evidence critic → final judge
```

Code-to-design保留为补充入口：代码被切成互斥anchors，scout逐anchor检查硬编码、循环推进、分类、dispatch和边界出口，再从完整设计inventory反查义务。它不替代design-to-code主入口。

## 原子义务，而不是矩阵

Extractor只读设计，不看代码，逐行提取可由实现满足或违反的单一要求：subject、trigger、obligation、observable result、normative strength、适用条件和精确source range。MUST、SHOULD、已声明能力以及明确MAY行为都保留。

对于“扫描/遍历/处理一个复数集合并对各元素执行动作”的算法性正文，即使没有在总述句重复MUST，也要提取“处理全部有效/适用元素，除非设计明确给出上限”的aggregate obligation。这来自设计的集合语义，不是项目专用规则。

每个义务只选择一个最合适的比较模式：

- `contract_mechanics`：常量、上下限、长度、状态、错误、集合/链/嵌套元素推进；
- `temporal_conditional`：延迟、随机化、顺序、重试、周期、unsolicited和条件副作用；
- `routing_capability`：入口、注册、构建、配置、按顺序的分类/dispatch、adapter、所有权及fast/slow路径；特别检查宽泛catch-all或early return是否遮蔽专用分支。

模式是检索提示，不是义务×维度笛卡尔积。Scout必须按queue顺序逐条给出`candidate`或`no_mismatch`，但不需要在raw阶段完成最终证明。真实代码lead或结构化absence lead加最低限度反查即可输出`uncertain`，深度闭环留给investigator和critic。

Normative strength影响最终分类而不是raw eligibility：父能力已经实现、但设计明确给出的MAY/optional分支完全没有机制时，保留为“非MUST违反”的optional design gap；不能仅以“MAY允许省略”静默丢弃。

这三个mode同时是claim、task、coverage和final gate的唯一review vocabulary；不再保留另一套8项portfolio lens，否则早期合法候选会在后半程被判为unknown lens。

## Helper边界

模型不再写session、sweep、plan digest、direction、section IDs、architecture IDs或design-to-code候选的完整requirement。

- `obligation_queue.py`校验source range确实位于assigned section，读取原文摘录，生成稳定obligation ID并绑定current digest；
- `scout_materializer.py`从queue投影requirement和review mode，从plan投影scope/envelope，并校验候选与coverage一一对应；
- `scout_receipt.py`重新核验queue/plan digest、义务或anchor顺序、candidate ownership和handoff hash。

这些helper只保证provenance和完整消费，不用regex、关键词、项目名、固定答案或分数判断语义。

## Fresh negative coverage review

Scout的`no_mismatch`不再直接关闭义务或anchor。`negative_review.py prepare`从初始coverage中机械提取待关闭单元，在blind packet中移除原disposition、candidate绑定、搜索摘要和countercheck，只保留source-bound义务/anchor，并切成每批最多4项。每个batch使用不同fresh provider session，遵循`negative-coverage-reviewer.md`逐项先搜索、再窄窗口读取并比较当前design/code。Reviewer选择verdict前必须结构化列出入口、推进/转换、guard/bound、终止/出口、剩余适用工作和替代/补偿反查，helper仅校验这些通用证据字段非空。Reviewer以设计明确写出的适用域和例外为证据边界，不得用实现动机或常见实践补写例外；直接反例与一次定向替代/补偿反查充分后立即结束当前item：`upheld`才允许关闭，`challenged`或证据不足必须输出普通candidate进入既有investigator/critic链路。

Reconcile helper只校验packet与current plan/inventory/raw semantic digests一致、blind batches逐项完整、candidate绑定回原item、scout和所有review sessions互异，并把分歧机械升级；它不包含任何“何为语义矛盾”的规则。Receipt再次核验review artifacts、digest、freshness及每个最终negative的upheld状态。由此避免为某个集合、状态机、时序或路由案例持续增加不可穷举的硬编码gate。

每slice最多12条只限制Scout初始输出，避免单个探索角色无界扩张；blind reviewer推翻的结论不受该配额压制，全部进入canonical observation ledger，再由全局candidate selector按证据最多选择12项进入昂贵调查链路。

## 分层开发harness

公开答案只保存在被git忽略的本地开发评测目录，不进入`/work`、`INSTRUCTION.md`、prompt或正式检测逻辑。框架修改后按以下顺序验证：

1. **Pair judgement**：给出正确设计条款和相关代码闭包，验证模型是否能作出设计↔实现判断；
2. **Code retrieval**：给出正确设计条款和完整代码仓，不给实现路径，验证义务驱动检索；
3. **Design retrieval**：使用正式inventory/plan，验证目标条款是否进入义务队列；
4. **Raw recall**：对全部materialized candidates运行本地oracle，至少4/6才允许全量；
5. **Pipeline survival**：检查已召回候选是否在selection、claim、investigation和final gate中保留。

这使失败首次可定位到一个具体阶段。局部盲测仍消耗模型额度，但远少于31个scout及后续调查的完整run。

## 保留的工程约束

- 最多两个并发语义任务，且primary design sections或code anchors互斥；
- target code和supplied design只读；
- semantic task写隔离文件，累计JSONL由有锁helper原子发布；
- controller是phase与next action唯一真相源；
- catalog只证明来源，architecture只导航，测试缺失不等于实现缺失；
- 不添加fallback、人工参数、项目专用规则或放宽最终证据gate；
- 正式入口不包含`opencode.json`。

## 验收标准

- 完整pytest、`py_compile`、`git diff --check`通过；
- 每个最终`no_mismatch`都有fresh provider session给出的独立upheld结论，分歧均进入candidate链路；
- 本地pair/retrieval harness能分别报告抽取、检索、判断和pipeline survival，而不是只有一个总分；
- 本地公开oracle在raw candidates至少召回4/6后才启动全量，目标为6/6；
- `/result/issues.json`、`issues.jsonl`、`00-summary.md`及单issue报告完整生成；
- 每个issue包含设计/代码证据、路径行号、差异原因、误报排除和置信度；
- 误报率不高于50%，总时长不超过6小时；
- 不同语言、仓库和设计文档形式走同一义务驱动流程。
