# 三次真实整理根因核对与最小动作设计（离线，有界）

本阶段完成的是证据比较和现有流程的隔离验证，不是产品修复或业务验收。0 真实模型请求、0 日常项目写入、0 产品实现变更。起点 `f6a057ccae56c8a34e1f7b0e5886ed467f232769`，父 `4ce112ba5030f4466f7b6abd4fb26b01ba4393a6`；应用源码仍为该父提交，SHA256 `28a0a64060190f806406709b855354a601a00db69226ea9532050e617746ad74`，正式 8765 runtime `28a0a64060190f80`。仅新增本说明和隔离测试，最终归档 SHA/父链/精确 diff 见本轮 `final-binding.json`。

任务状态卡：唯一目标是区分调用流程、生成违约和真实未决，再给最小可审阅方案。输入为三个原 CALL、对应冻结项目/来源及当前公开实现；有限交付为比较、状态迁移证据、必要回归和设计。禁止实现、日常业务写入、付费请求、Office、组件/Skill/UIAgent、配置/网络变化和发布。设计归档后停止，后续由父审核授权。

## 一、三次真实实验实际改变了什么

本轮独立证据目录 `evidence/offline-organize-root-design-20261003-0437/`，所有旧目录只读。`three-call-comparison.json` 保存完整原片段、原候选、原问题、请求头契约/schema、原响应逐路径对照和 CALL/正文/input 哈希；没有清洗实际响应。三个归档 CALL 与 `data/evidence/model-calls/` 同名文件字节一致，解码请求的 `digest(messages)` 均等于实际 `actual_input_hash`。第三次以当前未改源码重新 assemble 与其实际 messages 完全一致；这只是确定性重建，不是另一次发送。

|实际 CALL|输入版本/候选/问题|当时实现/运行指纹|cap|实际登记结果|prompt + completion = total tokens|
|---|---|---|---|---|---|
|CALL-a88590ed94784688|v2 / 0 / 0|ddd5b4c / a985c172d208bcf5|2|HTTP200 stop；accepted，TASK partial；未人工采纳|10452 + 4245 = 14697|
|CALL-e53bae5e8fb04d66|v3 / 7 / 1|31bfcfb8 / 60455f5fbffededd|1|HTTP200 stop；SCHEMA_INVALID，paused_budget|26382 + 4572 = 30954|
|CALL-7fd0f893ebf045df|v3 / 7 / 1|4ce112ba / 28a0a64060190f80|1|HTTP200 stop；REFERENCE_INVALID，failed|23296 + 4901 = 28197|

三次业务动作都是 organize→ingest，实际模型都是 deepseek-flash；初始输出上限 48000、批准最大输出上限 393216。第一次没有候选或历史 assistant 消息；第二次将首轮完整 assistant.response/text（约 10977 字符）再次输入，结果条件投影共 53 条（43 来源谓词＋10 模型条目断言）。第三次保留相同七个候选和开放问题、只保留历史模型事件身份，投影分成 43 来源谓词及 10 `unadopted_clause`，另加 generation-evidence-1、真实 revise 目标及来源时间。第一次与后两次的项目版本、候选/问题、运行代码、schema/config hash、cap 不同；后两次输入装配代码也不同。不能声称是只改变上下文的受控实验。

实际 input digest 依次为 `b18f88818cbbf87de527f40ea177b97906495311330280750e236ac7e4bb4fd9`、`a5a3dfd12d047d8330b8a314f2801745d64111bd5e14ff1d485ce806e6ba6c26`、`2751968fc0133fa9ab0c55abe85d71c8e775b35a3372632e16375d11dd9bdadd`。原 CALL 和正文 SHA、授权 hash、模型与各源原文见比较文件，金额无账单，UNKNOWN。本主批累计仍是 3/8、73848 已知 tokens；剩余五次不是本阶段授权。

三个请求完整 excerpts 逐字相同，无遗漏：

- 历史 `SRC-91cf252efd44431e`，purpose=reference，created=`2026-10-02T16:16:25.505891+00:00`，SHA `1ef8d3e61e501fd4f0d08e6a6e3dec30ad918c4da90dabe30b6bbc243cf80d90`，原段 `EX-b150205f8a2c4c1e`：本期同租户每批最多20项、任一项配额不满足整批拒绝不创建；既有审计正常而本场景覆盖无证据；原句“失败时既有资源不变，超时视作失败并允许再次提交”。
- 当前明确合成决定 `SRC-d456da0255274954`，purpose=goal，created=`2026-10-02T16:16:25.543543+00:00`，SHA `40fb3b96c94d083c8c9b4f21d2931438b5603f33d249ff49fdb156c5fa7d18cb`。`EX-28c1e86aa1bb423d` 明示“以下全部为合成测试操作者决定，绝非真实业务PM批准”，明确拒绝不创建/既有不变；超时或响应丢失未知、沿既有申请ID查询、确认前不重复；审计为本期要求而非旧实现证明；租户管理员、无新增审批/自动重试/未知新建/部分创建/培训时限义务。`EX-bef928a4d1714f21` 明确逐个配额不满足项的标识与原因、不泄露其它租户，不新增审批/排序/分页。

时间仅相差约38毫秒；purpose 和时间本身不能证明正式批准或自动覆盖。旧 oracle SHA `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 和全部旧 source 原字节保持。本轮没有把 goal 偷换成系统 answer。

## 二、归因：事实、推断与未证明部分

**生成缺陷已证明，不能全部归因旧候选复用。** 第一次 actual request `items=[]`、`questions=[]`、`recent_messages=[]`，但原输出 `$.proposals[0].behavior.result` 已写“全部项配额满足则整批创建”，`$.result.understanding` 也重复。因此该充分保证最初由生成产生，非旧候选污染的必要前提。第二/三次再次出现相同保证，旧候选和首轮历史可能放大锚定；这里只能证明被再次输入，不能证明模型内部因果或每个 token 的权重。

D1 是具体跨字段冲突：来源只给配额不足→拒绝的必要条件与明确服务拒绝/未知分支，不能推出配额满足→无条件创建。反例“所有配额满足，但服务明确拒绝”，`behavior.result` 会保证创建而 `behavior.exceptions` 要求不创建。三次 exceptions 确实保留拒绝/未知，不应写成全部丢失。首次 statement 主要保持必要条件；后两次 statement 也加入保证。三次 understanding 有 D1，第三次 `product_context_proposal.change_scope` 也有；三次 summary 保留拒绝和未知且没有同一配额充分保证，但将明确合成当前决定又称待裁定。正常受理且处理成功的限定分支、来源真实明确的充分条件仍可通过现有边界，不把全部正常结果或定性收益判错。

**问题动作违约已证明。** 第二次 `$.questions[0].temp_id=TMP-Q-timeout` 违反已有 `^QTMP-[A-Za-z0-9_-]+$`，实际为 schema 拒绝；原七条都是 add/null目标。第三次 QTMP、source_refs 和七个原 ID 同 kind revise 已正确；其 `$.questions[0].target_question_id=Q-1e44e50ac69c4bb0` 且 `decision_points=[]`，实际引用拒绝。原问题是 open、answer=null，拒绝原因是未提供拆分子决定，不是已答。不能补 child、删 target 或把此 raw 当答案落实成功。

实际请求 system 的 `understanding_contract.questions` 已规定非空拆分，`question_action_contract.instruction` 同样规定；schema 的 target enum 为 `[null,Q-1e44e50ac69c4bb0]`。但 `decision_points` 通用结构允许空数组、排除单子项，没有关联“非空 target 必须至少两子项”的条件，所以第三次合法 JSON/schema 仍不合法业务动作。`app/provider.py:350` 派生 enum，`:367` 注入 understanding 契约；`app/understanding.py:196` 继续严格拒绝该工作流错误，repair_allowed=false。可证明规则暴露和输出违约；不能猜测为何模型没有遵循。

**调用流程状态未落实也已证明。** 当前公开项目 v3 中 Q 原 open、answer=null、没有 answer_source_refs，七项均 candidate，product_context/intake/stage_checks 缺失。`current-public-route-design.json` 的 clarify `answer_binding_tasks=[]`。上传“操作者决定”材料是内容来源，不等于执行 `/answers` 保存回答；三轮只调用 organize，未执行保存回答与 clarify。这不解除 D1，也不能把重复询问全归因流程：第三次 source_policy 明示按原文时序、真实本期决定识别，模型仍把明确测试选择当作须重新二选一。产品应提示“尚未记录答案/待人工落实”，不能把已清楚的内容说成没有选择。

**真正需用户业务选择的范围。** 本合成操作者的超时选择已有冻结真值，不需要再发明选择。若在真实材料中仅有建议、作者无决定权、明确撤回/互相冲突的当前决定、对象不一致或效力未明，才继续联合追问并保留出处。本场景没有提供旧系统逐项操作或量化收益事实。首次 value“减少逐项提交操作”和第三次“替代逐项提交”比较前提未标假设；第二次明确“既有操作前提待核对”，是合理例外。缺少量化收益本身不构成错误，也不能据此把未采纳价值建议升级正式义务。租户管理员为运行角色，测试操作者/PM需求确认不是新增执行审批人；审计入口是本期要求，不证明新场景已实现。

`original-byte-diagnostics.json` 用未改原输出及当时冻结项目离线重放：当前完整 validate_response 得到 SEMANTIC_BLOCKED / SCHEMA_INVALID / REFERENCE_INVALID，单独条件诊断三条均 SEMANTIC_BLOCKED。这些是当前代码离线诊断，原登记 accepted/partial、SCHEMA_INVALID、REFERENCE_INVALID 原样保留，不追改历史状态。

## 三、现有合法动作序列与当前项目能否复用

可以复用 P-f73f8130d36040af v3，前提是父另行授权正常状态演进并冻结明确合成反馈；不需要重建或导入/seed旧状态。本轮没有执行以下日常写入。旧七候选、Q、原 sources 和三 CALL 应全留历史。

|顺序与公开入口|输入/身份/状态期望|实际模型动作|
|---|---|---|
|只读 GET project、actions、runs|核真实revision=3、无inflight、原Q相关 REQ-6809025ae40a4e2f/RULE-59348d25135b4a91，原规范v1；actor/constraint无content_version不虚构|0|
|POST `/answers`（或 `/questions/{qid}`）|expected_revision=3，answers={原Q:冻结合成操作者反馈}。正文明确本期采用当前未知口径、不采用历史超时失败重试；保留现有 source 引用及测试身份。新增回答 source，Q status=answered、understanding_status=pending、revision通常3→4；不改旧items/selection|0|
|POST `/product-context` + `/stage-checks/1`|先保存真实已有 product/module/intent，expected_revision逐步读回；checkpoint 用当前flow hash。不要复制无来源旧流程。也可使用已有 `/intake` 保存既有输入并记录第一步核对；二选一，不同时无谓复制。此状态变化必须显式冻结|0|
|POST `/actions/plan` → `/actions`|action=clarify，target={kind:question,id:原Q}，message明确仅两原条款的已有答案落实与成功条件纠错；显式cap=1、读回missing=[]、plan_hash/idempotency/authorize。普通无target clarify另需第二步范围核对，不能绕门禁；局部target依现有规则只需第一步|1（含任何实际修复都计cap，首缺陷即停）|
|模型候选返回、人工核全文|同kind revise 两原ID，answer_refs=[原Q]，source_refs含新增回答真实片段；Q→candidate_ready，old条款不替换。其他五项/独立未知不机械复制。D1、摘要/解释、角色、20/同租户、反馈、审计、未知分支逐项核|0新增动作|
|POST `/items/{候选id}/behavior`、`/items/{候选id}`|需求先核行为/来源，再明确 selected；两目标分别采纳后，稳定原ID内容版本递增、修订候选deferred保留，Q部分落实后最后applied。不能因为同标题或同kind偷映射。任何语义问题都不能采纳|0|
|可选 GET/POST `/questions/{qid}/application-review`|仅原条款已经人工修订、采纳且真实承载回答时，无模型核对路径：当前answer_hash、各原目标clause_hashes、review_confirmed及expected_revision；不替代修订、不自动证明内容一致|0|

`app/main.py:454`/`:849` 保存答案，`web/src/product-flow.tsx:145` 对应“保存本次回答”；`:141` 的问题卡提供“帮我分析这个问题”（局部 target），`app/actions.py:173` 将目标及用户意图加入 clarify。`app/product_flow.py:223` 保留步骤门。人工落实核对位于 `app/main.py:865`/`:873` 与 `web/src/product-flow.tsx:99`；原候选不包含答案时绝不能点确认应付。

没有独立 `answer_revision` 数值字段，不虚构该字段：现有记录用 answer/answered_at/answer_source_refs/answer_history，候选用 answer_versions[Q]=digest(answer)，人工应用记录用 answer_hash+clause_hash。`answer_targets` 仅该Q直接关联的原requirement/rule/acceptance/constraint；`affected_requirements` 可展示更广需求关联，不能等同正式绑定目标。回答变化会保留旧回答/来源、置pending并清应用；旧候选采纳返回 STALE_ANSWER。原条款变化也会使应用hash失效。actor内部记录为 authenticated_local_user；测试身份必须明确写在冻结反馈和报告中，不能称真实PM批准。selected/applied都不是confirmation/baseline。

若对象、版本或答案适用性失效，应先重新核当前记录，不能覆盖原问题历史；只有需从零评估冷启动、隔离不同真值/业务对象，或合法当前目标不再存在且不能按现有流程恢复时才另开合成场景。不能为避免本项目 D1 改源、换项目或重新seed。

## 四、最小设计（未实施）及下一阶段比较边界

优先采用现有回答→局部clarify流程。这个流程已经存在，不需要先加自动路由、恢复接口或重新扩展条件引擎。生成质量仍未知，不能从Mock链通过推导真实clarify能纠正D1。

若父认为需要代码，建议仅两项可分别审阅的生成输入补丁，逐项授权，不同时混进下一真实比较：

1. **问题动作结构一致性**：在生成专用 Question schema 增加条件：target_question_id非null时必有decision_points且至少两项；独立单问题target=null、空数组仍允许；已答目标不进enum。保留公共响应schema和validate严格动作语义。用已有“普通问题/合法拆分/错误空拆分/已答落实”短结构例子，替换易误读的泛化说明，不堆提示。修改范围预计 app/provider.py 的生成schema装配及定向测试；无raw修补、无临时ID重分配。Provider当前 response_format=json_object，仅承诺JSON格式，头中schema是指令，新增条件不代表供应商硬结构解码保证。
2. **结果条件的生成选择**：复用现有 result_condition_facts 的 role/object/operation/source_refs/location，不新增庞大事实引擎。为局部目标给短任务卡，明确可用必要条件和拒绝/未知分支，旧candidate“全部满足则创建”属于待纠错断言而非可复制来源。输出可以只保留被支持的结果；写正常成功分支必须限定实际同对象受理/处理成功，来源明确充分条件按真实作用域保留。源本身冲突时才追问，不替操作者添加成功充分规则。建议范围为生成上下文/提示的局部装配，保持现有校验。现有代码已经表达这些原则，是否短任务卡能改善真实输出仍未证，不能仅再加大段同义词校验。

固定回归要求：真实三响应仍按原错误拒绝；空候选时D1不靠候选污染解释；完整来源、当前明确决定和未采纳身份保留；合法split≥2、普通null目标、已答samekind revise/source_refs/answer_refs、失效答案拒绝；必要→充分和跨字段摘要误推仍阻塞，同对象限定正常分支/明确充分来源通过；定性价值与准确标假设不误报。已有相同覆盖直接复用，不无限扩同义词。

**下一阶段单变量安排（仅建议，未授权执行）**：先冻结当前代码4ce、model/config、材料原字节、原七候选、两个合法原目标、明确判断标准及cap；保持同一clarify动作、同一局部target/message，不同时改prompt/schema、模型或材料。离线双臂比较唯一干预“该Q是否通过公开保存回答”：A为原open无answer；B为公开保存该冻结合成反馈后answered/pending。B必然新增answer-source、answered_at、answer_history/answer_refs可用性和project revision，这些是该状态干预的真实组成，不称原输入完全不变。必要第一步核对在两臂先同样完成，仅来自原已明确product/module/intent。

当前三次都是organize，改为clarify本身是另一变量，不能拿过去第三次直接当上述A臂。可先只读离线比较当前同快照organize vs clarify暴露了哪些任务字段，单独标为动作变量；没有付费对照，不声称生成效果因果已验证。父如批准一请求正确流程试验，只发B的合法clarify、cap1，实际失败即停，记录其同时相对历史改变了动作和保存答案状态，是工作流验证而非单变量实证。若要真正付费单变量两臂实验，需父另批预算，不能默认再花两次或穷尽剩五次；建议先用离线双臂，避免无意义费用。

未来真实验收仍须原始失败原封保留；事前冻结“同kind两原ID＋答案真实引用＋无无条件配额充分保证＋未知不推断结果＋原量词/角色/来源齐全”等判据。Mock成功不能写成真实模型内容通过。若任何必要决定仍未知则记录NOT_RUN，不补造答案清零。

## 五、本轮验证、保护及收口

命令：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_offline_answer_route_design.py tests/test_decision_workflow_boundaries.py tests/test_clarify_answer_prompt.py -q --junitxml=evidence/offline-organize-root-design-20261003-0437/route-final-v2/junit.xml`。

实际 exit0，37 passed / 0 skipped，2项既有依赖弃用警告。新4项公开流程探针＋已有33项正反例复用；无全量或Office。本轮五项Office未执行/未选入；之前1411通过/5 Office opt-in skip属旧回归身份，不能冒充本轮新结果。新测试源码 SHA `ddd8f49d4a3096f06478351fe5f5ca695ed595fe5c68efbc256f8f5e55c160d1`，app SHA 与8765运行未变。

隔离 create_app 指向 tmp_path、absent.env、仅伪造测试Key；Provider请求全部MockTransport，IP guard阻断外部地址，允许Windows asyncio本机self-pipe；没有真实HTTP模型发送。成功例的模拟动作succeeded/calls1，原REQ/RULE在生成候选前后保持，原fixture v2→人工采纳后v3、两个修订候选deferred且answerhash/clausehash可核。旧答案变化的STALE_ANSWER、open问题空拆分REFERENCE_INVALID均无业务部分写入；合法拆分/已答失效正负例由已有定向用例复用。Mock的11/7/18 usage是夹具，不计任何真实tokens。

保存了全部初始探针失败：只读预检v1误认SQLite末列为payload，v2改用列名；测试初版外网guard挡Windows self-pipe、夹具错误设置product_context=None、猜测fixture版本、填错clarify/ingest schema、混比POST内部对象与GET公开投影、误读task error字符串/可选result_applied字段。错误、exit1、初始JUnit和各轮测试源码均新文件保留；修正的是本轮探针，不是产品或原模型输出，不据此报产品数据损坏或删除失败。最后定向exit0/37项绑定最终测试源码。

前置实际核实22项目/315revisions/802records/191CALL、无queued/running；主v3七candidate+原openQ、对照v2。前后保护逐行hash、191CALL逐一hash、source文件、旧evidence、work、dist、配置/凭据哈希及更正链，结果见protection-design.json和最终protection-final.json；不上传DB或密钥。Git只提交本说明与新隔离测试，原work仍未知未跟踪，旧tracked文件未改，未重启服务或追加观察器。

协调回执历史例外仍保留：request-cap独立回执旧索引df31c785fcf178eefdc2fb81ed2c937cdad89e615f6322ba166e709e9b141cb7，当前c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9；追加correction原样保留，恢复旧hash曾被自动审批拒绝，未重试/换路由。这里的保护是本轮开始至结束不变，不能否认曾发生该协调回执事故。v1内部/公开投影误报记录实际exit0且false flags，与v2更正链都保留。最新layered closeout SHA b1e2e792fdbee1c135f796cc821d88fb4bff73abd5c40e9b71c5a1f6238bc04a，30保护/38manifest核验不能代替模型内容通过。

本轮到设计收口停止。主项目没有回答/采纳/MRD/PRD/正式review/confirmation/baseline/下载导出；反例仍NOT_RUN。真实模型的D1和重复问题尚未证明解决，机器消费者、新机/安装器、真实PM、发布仍未验，不称全面ready。
