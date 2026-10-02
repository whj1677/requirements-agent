# 一次公开回答落实工作流真实验证（2026-10-03）

本轮按原问题保存合成回答、补齐资料已有背景、核对第一步，再执行一次真实 `clarify`。公开状态迁移与答案引用合法；模型返回 HTTP 200 / stop，但应用实际以 `SEMANTIC_BLOCKED` 拒绝，TASK/RUN 均 failed。四个原始提案没有登记为候选，原七候选没有改写或采纳。本轮没有闭合成文、审查、确认或导出链路。

首个拒绝把“不断言创建、未创建或既有资源未变”这一禁止断言的否定句识别成未知结果断言，是有限规则的误报边界。原响应另有权限范围和验收措辞需要人工核对，不能因为这一误报就宣布全部业务内容合格。本轮停止于首次真实终态，没有修改响应、校验或产品实现。

## 范围、身份与冻结

- 唯一项目：`P-f73f8130d36040af`，开始 v3、七 candidate、原问题 `Q-1e44e50ac69c4bb0` open / answer=null；原问题直接关联 `REQ-6809025ae40a4e2f` 和 `RULE-59348d25135b4a91`。
- 操作者是 **synthetic test operator**，回答与第一步核对都不是用户真实 PM 批准，更不是上线批准。
- 独立证据目录：`evidence/real-answer-workflow-20261003-0503/`；新文件 create-only，原失败、来源与协调更正链只读。原请求、响应和数据库行 hash 只保存在本机，未提交 Git。
- 冻结材料：`evidence/real-minimal-retest-20261003-0012/frozen-plan-and-truth.json`，SHA256 `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc`。本轮冻结为 `execution-freeze.json`，包含原完整项目、来源、回答、动作消息、预期和停止条件。
- 硬上限为一次公开 clarify、一次实际 DeepSeek 请求，显式 `max_calls=1`；无重试、付费修复、诊断模型或 models/test。
- 不改产品源码、测试实现、prompt/schema、配置、凭据、网络；没有重启服务、另开工作台、派 Agent、运行 Office、组件或全量测试。

开始 Git 为 `bf2e11451437c9aad3a3cdb4f457992cf6e8bac6`，父 `f6a057ccae56c8a34e1f7b0e5886ed467f232769`，分支 `codex/conversational-intake-20261001`，dirty 仅原 `work/`。产品实现仍为 `4ce112ba5030f4466f7b6abd4fb26b01ba4393a6`。app 按现有指纹算法的全 SHA256 为 `28a0a64060190f806406709b855354a601a00db69226ea9532050e617746ad74`，唯一正式 8765 的 runtime 为 `28a0a64060190f80`，预检无 queued/running TASK/RUN。

## 原输入、问题与公开回答

历史 reference `SRC-91cf252efd44431e / EX-b150205f8a2c4c1e` 包含“超时视作失败并允许再次提交”；当前 goal `SRC-d456da0255274954 / EX-28c1e86aa1bb423d` 给出明确服务拒绝和未知结果的不同处理，`EX-bef928a4d1714f21` 给出本批每个配额不足项标识、原因、不泄露其他租户以及范围排除。三个原 excerpt 与原身份、时序均保留，完整内容见 `main-before.json` 和 `execution-freeze.json`。

原问题全文：

> 历史材料写“超时视作失败并允许再次提交”，新增决定写“响应丢失或超时是结果未知，需沿既有申请ID查询同一申请结果、状态确认前不重复提交”，本期应采用哪种超时处理口径？

经公开 `/answers` 保存的回答全文：

> 合成测试操作者回答，绝非真实业务PM批准。
>
> 对原问题 Q-1e44e50ac69c4bb0，本期采用当前明确合成决定 SRC-d456da0255274954 / EX-28c1e86aa1bb423d 的结果未知口径；不采用历史参考 SRC-91cf252efd44431e / EX-b150205f8a2c4c1e 中“超时视作失败并允许再次提交”的口径。
>
> 服务明确拒绝时，本批不创建且既有资源不变。响应丢失或超时是结果未知，不能据此断言申请失败、本批未创建或既有资源未变；沿既有申请ID查询同一申请结果，状态确认前不重复提交。此回答仅落实该原超时问题的既有冻结测试选择，不新增重试规则或真实业务批准。

新增回答 source 为 `SRC-cd226953df7b4d3f`，UTC `2026-10-02T21:05:46.741220+00:00`，source SHA256 `4f420eb33841f73995b5af464a60ef761a36148ded698956dc76dccbd18b0e0a`。真实 `answer_source_refs` 是 `EX-46e09867416f41e7`、`EX-6cb75ea41f254c1e`、`EX-32297aad3bb64546`；分别保留测试身份、选择依据与处理条件。回答 digest 为 `35255574e788050acbe4ad647e34b64e0a4bdcce97faef3653ae588e77714ecc`。

Q 保持原 ID，变为 answered / understanding_status=pending，`answer_applications={}`、applied IDs 为空；初次回答没有旧答案历史，公开对象没有数值 `answer_revision` 字段，不虚构该字段。回答不会自动改原条款或采纳状态。

## 实际公开路径与版本

所有写入都走既有 8765 API，使用当时真实 expected_revision；每步 intent、请求正文、响应及读回对象分别落盘，没有 SQLite 写入或 seed。

| 步骤 | 入口与输入 | 实际状态 |
| --- | --- | --- |
| 保存原问题回答 | `POST /api/projects/{pid}/answers`，expected_revision=3，仅原 Q | v3→v4；新增回答 source，七原 item 不变 |
| 补背景 | `POST /api/projects/{pid}/product-context`，expected_revision=4 | v4→v5；仅 product=园区云平台、module=云资源申请、intent=本期新增同租户每批最多20项批量申请，任一配额不足整批拒绝不创建 |
| 核对第一步 | `POST /api/projects/{pid}/stage-checks/1`，expected_revision=5，实际 product_flow[0].content_hash | v5→v6；第一步 complete，第二步未 complete |
| 计划 | `POST /api/projects/{pid}/actions/plan`，expected_revision=6，action=clarify，target 原 Q，document_type=prd，max_calls=1 | missing=[]，stages=[clarify]，有效上限严格为1 |
| 单次提交 | `POST /api/projects/{pid}/actions`，同 plan_hash、authorize=true、新幂等键，提交前保存 intent | 新 TASK/RUN；正常动作将非空 message 保存为新 source，v6→v7；随后唯一模型调用 |

第一步核对使用真实 hash `fa4156fcd89c280cb56613df72c4f1f9953e600553bb3a4e062afa3206ff6888`；没有补造旧逐项流程、入口页名、成功充分条件或审计实现证据。

计划 hash `e535dce3283055ad3f8a77fac34fa5c542f74b82ad64134443ee7667de43ebb3`，幂等键及提交正文保存在 `clarify-submit-intent.json`，唯一提交回执在 `clarify-submit-response.json`。上下文中已回答问题只供两个原目标同 kind revise 的答案落实；不能再通过空 decision_points 把已答问题当待拆分问题。

正常动作新增的 message source：`SRC-6c4522d41fbe4cdc / EX-2e0d4dc67ae24b6c`，UTC `2026-10-02T21:06:18.451695+00:00`，SHA256 `49021ec00a13c8c8b6a1173d0ef09ee69cae652360f891f5f4609d67974b239a`。内容只限定回答落实、原两条条件纠错、冻结条件保留和待人工核对；完整 message 见冻结文件。新增 source、回答状态、背景、checkpoint、revision、action 和 message 都是本轮工作流变量，因此这不是独立单变量 prompt 实验。

## 唯一真实请求与实际拒绝

| 项目 | 实际值 |
| --- | --- |
| TASK | `TASK-48c02d9f30b94ca4`，failed |
| RUN | `RUN-4cc53977f5d04b9c`，clarify / failed / revision=7 |
| CALL | `CALL-94cf90efe9534bbe` |
| Provider / HTTP / finish | 现有 DeepSeek，deepseek-flash / 200 / stop |
| 实际注册校验 | SEMANTIC_BLOCKED |
| request_budget | limit=1 / used=1 / calls=1；reservation=response_received |
| 修复、未知、在途 | repair_count=0，未知0，无 queued/running |
| prompt / completion / total | 29068 / 4415 / 33483 tokens |
| 本主批累计 | 4/8 请求；已知 107331 tokens（此前73848＋本轮33483） |
| 金额 | UNKNOWN，无账单，不估算费用 |

请求占用 UTC `2026-10-02T21:06:18.653472+00:00`，响应收到 UTC `2026-10-02T21:06:35.784850+00:00`；对应本机 10月3日05:06。没有自动修复或重发。历史其他用量不并入这个主批；Mock 数值不计用量。

实际 error 原样保存在 `terminal.json`，两个定位均指向同一原句，不能算两个独立问题：

> 结果条件不一致：proposals[0].behavior.result 原句『响应丢失或超时为结果未知，不断言创建、未创建或既有资源未变』：unknown_result_asserted；proposals[0].behavior.result 原句『响应丢失或超时为结果未知，不断言创建、未创建或既有资源未变』：unknown_result_asserted。

原句禁止对未知结果作断言。该提案 statement、exceptions 同时明确“不能据此断言”、沿原申请 ID 查询和确认前不重复，不能把它读成已创建、未创建或既有不变的保证。只读离线完整验证复现同一 SEMANTIC_BLOCKED；这是规则误报的证据，**实际 failed 保持不变**。

原始正文没有清洗。公开 strict JSON/schema 与答案/动作/来源绑定单独离线检查 accepted；完整 validate 和条件核对均拒绝。单项 schema accepted 不等于业务合格、应用 accepted 或已登记 review。

## 原回答到提案的实际内容链

完整原 JSON、四提案全文、behavior、所有 source/answer refs、旧→实际对象逐字段对照和人工发现保存在 `final-output-original.txt`、`content-audit.json`。下表中的 TMP 都只是失败 CALL 内临时标识，**不是已登记候选 ID**。

| 原始提案 | 目标、引用与内容 | 本轮判断 |
| --- | --- | --- |
| TMP-1 / revise / requirement | 原 `REQ-6809025ae40a4e2f`，原 content_version=1；answer_refs=[原 Q]，引用原 reference、goal、真实回答片段与动作 source。正文明确“配额满足是必要条件而非成功保证”、明确拒绝不创建、未知不推断、沿原 ID 查询和不重复；保留同租户20项、任一不足全拒绝、逐项失败反馈、不泄露其他租户 | 原充分保证在本次 raw 中移除；同 kind / ID / 回答引用合法，但整体校验阻塞，未登记/未应用 |
| TMP-2 / revise / rule | 原 `RULE-59348d25135b4a91`，原 content_version=1；answer_refs=[原 Q]，引用当前 goal 与真实回答片段。明确采用本期未知口径、不采用历史失败重试；明确拒绝与未知分支分开 | 没有重问已答问题；引用合法，未登记/未应用 |
| TMP-3 / add / acceptance | target=null，answer_refs=[]，来源指向当前冻结决定和真实回答，related_refs 关联原条款；描述配额不足整批拒绝、逐项反馈及未知查询 | permissions 写“仅本租户管理员可见反馈”，比来源“向本租户管理员反馈／不泄露其他租户信息”更强，来源没有排除同租户其他角色查看，需人工核权限范围 |
| TMP-4 / add / acceptance | target=null，answer_refs=[]；当前 goal、真实回答和动作 source；拒绝/未知结论约束保留 | 标题“预算不足/拒绝之外的提交结果验收”中的预算没有业务来源；前提“各项配额均不缺失”不等于“各项配额均满足”，不能称已精确复验原充分条件反例 |

两 revise 的真实 answer_source_refs 与原 Q 绑定已核对，没有通过同标题暗映射。raw 没有系统 `answer_versions` 字段；只有完整校验后的 annotate_candidates 才会登记相应元数据。本次校验拒绝，不能填造已注册版本或已应用回答。原 norm 的 content_version=1 仍为原值，非 norm 对象缺失版本字段时不虚构。

原 `questions=[]`，没有再次问超时选择。summary 明确仅落实原 Q 到两原目标、仍待人工核对；result.scope_summary 保留同租户/20项、全拒绝、逐项标识原因/不泄露、未知查询不重复、审计本期要求非实现证据、租户管理员无新增审批。其它 entry/前置条件未知继续保留，未补造答案；未出现新增自动重试、未知新建、部分创建、培训、时限、排序或分页义务。

未采纳 `product_context_proposal.value` 仍写：

> 支持一次提交多项云资源申请，减少逐项提交操作；具体效率收益未量化。

旧逐项操作流程没有来源，比较前提未标假设，仍需核对；“收益未量化”本身不构成拒绝理由，普通定性目标不应因此被判错。该模型背景提案未保存为正式背景。

以上内容人工核对只说明已观察到的改进和剩余问题，不是独立模型复审、正式业务审查或真实 PM 验收。没有手工改最终正文来制造通过。

## 证据哈希与执行限制

| 证据 | SHA256 / 含义 |
| --- | --- |
| 整个 CALL JSON 文件 | `59f8cc55df99405490b5fc7796e2b69eee1175bb994210a17f86525a6793fcef` |
| final_output UTF8 正文 | `e48b4b7a4280ed01b9491a41f6fb2b800b6783d35342d67537f47d5af75cf9b9` |
| request_input UTF8 正文 / actual_input_hash | `142c9047d90f391ca781a9d002ed10bc4327f436f31cc6f04d8e7b35e05311e2` |

`pre-submit-assembly.json` 是 v6 的提交前纯组装，明确不是实际出站 hash。实际动作新增消息 source 后以 v7 出站；`actual-outbound-receipt.json` 使用 CALL 内不可变 context.user_message 重建，实际消息与 hash 完全一致，全部原 excerpt 和七原候选保留。不能把预组装或离线重建代替实际 CALL 证据。

本机执行脚本为证据目录内 `execute_public_once.py` / `execute_public_once-v2.py`；只读内容复核脚本为 `audit_original_result.py`。实际请求与每个公开写入的 intent/response 已保存。此报告不把这些阶段调用包装成 pytest 验收。

两个本地探针失误原样留证：首次 checkpoint 探针误读 `flow` 而非公开 `product_flow`，未发送 checkpoint POST 或模型请求；只修证据脚本的字段读取后执行合法 checkpoint。首次返回后重建误用 RUN 已变为错误说明的 message，离线断言失败；改用 CALL 原 context.user_message 后一致。它们没有触发额外付费、产品修改或响应修复。

开始浏览器页输入为空且无 Stop。结束的既有页未刷新，`ui-terminal-visible-main.txt` 仍含旧状态；它不是本轮新终态 UI 验收，当前状态以新 GET、TASK/RUN/CALL 为证。本轮未核刷新后的界面显示，也没有完整 UI E2E。

## 状态与保护结果

`before.json`、`after.json` 和 `protection-result.json` 保存本地逐行/逐文件 hash。结束 main v7，原 Q answered / pending；七原 candidate 的全文、行为、ID、selection、原 source refs 与实际版本字段完全不变。原 REQ 中错误“全部项配额满足则整批创建”仍在**未采纳旧候选**里，本次 failed 没有改写它。

- 数据计数：22 projects 不变；315→319 revisions（仅主 v4、v5、v6、v7）；802→807 records（3 audit＋唯一 TASK＋唯一 RUN）；settings1、migrations1 不变。
- 其余21项目（含对照 v2）、全部旧315 revision 行与旧802 record 行均保持 hash。仅允许本轮主项目正常修改原 project 行和上述新记录。
- CALL：191旧文件全部逐 hash 保留，新增1个，合计192；原 source 文件不变，只新增回答和动作 message 两 source。
- 原 evidence、work、dist、凭据/配置 hash、协调回执、已跟踪旧文件、app 指纹和正式 runtime 全部保持；没有在途 TASK/RUN，无额外常驻服务或 Office。保护核对通过只证明保护范围，不代表业务通过。
- 旧 accepted/partial、SCHEMA_INVALID、REFERENCE_INVALID 和其各自纯条件诊断原状态保留，没有回填或改判。

协调回执旧事故继续注明：`request-cap-independent-public-contract-20261002.json` 曾由协调方只读预检更新 observed_utc，旧索引 hash 为 `df31c785fcf178eefdc2fb81ed2c937cdad89e615f6322ba166e709e9b141cb7`，当前为 `c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9`。恢复旧 hash 曾被自动审批拒绝，本轮仅读，不再恢复或换路由；追加更正文件保持。协调早先 v1 的实际 exit0 / false flags 与 v2 更正原样保留，不将工具投影误报说成产品数据损坏，也不沿用无证据的 exit1 说法。

本轮只有明确 docs 报告本地归档，产品源码和测试实现不变；精确提交 SHA、父链、git show/stat/diff、报告 blob 和运行绑定见本轮 `final-git-runtime-binding.json`。原 `work/` 继续未跟踪，不 add-A，不提交 raw CALL、数据库、秘密或巨大生成物，不 push/merge/deploy。

## 停止点和未验范围

本阶段真实请求已终态，1/1 授权已用；剩余主批额度不是继续授权。正确公开回答路径解决了“材料决定不是系统正式 answer”的状态差异，本次 raw 也没有重问原决定并纠正了旧无条件保证；实际流程仍阻塞，内容仍需人工核对。这不能证明 prompt 单变量改善，也不能称全面 ready。

下一步只建议父独立审阅原响应、否定句误报定位及额外范围措辞，另行决定是否授权最小修复。本任务到报告归档即停，不自行实现或再发请求。

人工采纳、回答应用、正式 review 登记、MRD/PRD、确认、baseline、MD/DOCX/ZIP 导出、完整反例模型均 **NOT_RUN**。本轮没有新 pytest/全量/Office 批次；旧1411通过/5项Office skip与设计阶段37/0仅属各自历史证据，不能冒充本轮新验收。五项 Office 本轮未运行。机器消费者、新机、安装器、真实 PM 和发布仍未验。
