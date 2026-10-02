# 上下文分层后的单次真实 organize 复验 — 2026-10-03

本轮 **1 个公开 organize 动作 / 1 次实际 DeepSeek 请求**，cap=1 已占用1/1，HTTP200/stop。原始 JSON 完整合法，三份 schema 均匹配；完整业务校验实际以 **REFERENCE_INVALID** 拒绝，TASK/RUN failed，未登记任何新候选。独立内容审核也不合格；请求返回后未继续任何付费、修复或业务动作。本次不构成完整主链、真实 PM 批准或发布验收。

## 冻结范围和实际执行

- 实现 HEAD `4ce112ba5030f4466f7b6abd4fb26b01ba4393a6`，父 `27cc28802a0153d3024e1cd94d23e33de4790313`，分支 codex/conversational-intake-20261001；源码全 SHA `28a0a64060190f806406709b855354a601a00db69226ea9532050e617746ad74`，8765 runtime `28a0a64060190f80`。
- 主项目 P-f73f8130d36040af v3，七 candidate、开放 Q-1e44e50ac69c4bb0、无文档。反例 P-f31d1a6267f041bc v2 及其它项目只读。原来源、时序、七候选实际 ID/content_version（有则保存，缺则明示缺）、正文/行为/引用、实际 product_context/提案/历史 RUN.summary/understanding 全量冻结。未改源、旧候选、回答或真值。
- `execution-freeze.json` 与完整 main-before.json 为本轮独立冻结；旧 frozen-plan-and-truth.json SHA `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 原样引用，不冒充旧五动作计划已经执行。所有决定是 synthetic test operator，不是真实业务 PM 批准。
- 原主批2/8、已知45651 tokens，本阶段硬授权仅1请求；剩余次数不作授权。0额外模型诊断/models-test、0重试/自动格式修复/截断升级，0观察器/其它Agent，未改实现、校验、配置、网络或凭据，未重启。

公开 `POST /api/projects/P-f73f8130d36040af/actions/plan` 请求 expected_revision=3、action=organize、max_calls=1、message为空；返回 max_calls=1、missing=[]、stages=[ingest]。计划 hash `f89d3a8fee7f8a5027d85694eb2bc137d5ff8a2b85450d5ea7c640ccd3c19fc4`。提交前 create-only 保存 intent、新幂等 key 并 flush/fsync，随后仅一次 `POST /actions` 携同一 plan_hash/authorize=true。授权快照 ingest.max_calls=1，CALL.authorization.approved_max_calls=1；初始/最大输出 token 政策未为测试缩减。

| 实际身份 | 值 |
| --- | --- |
| TASK | TASK-5ba84a606e39456e |
| RUN | RUN-e323352145894050 |
| CALL | CALL-7fd0f893ebf045df |
| 请求及响应 model | deepseek-flash / deepseek-flash |
| HTTP / finish_reason | 200 / stop |
| 请求状态 | response_received；终态未知0、在途0 |
| 实际完整业务校验 | REFERENCE_INVALID；model_repair_allowed=false |
| TASK / RUN | failed / failed |
| cap / used / calls | 1 / 1 / 1 |
| repair_count / repair_of | 0 / null |

命令使用本轮 execute_once.py 的 freeze、plan、assembly、start（单次）、poll、capture、protect；api只向正式8765，唯一实际Provider出站有上述CALL。`terminal.json`、ledger-final.json 及原CALL字节保留；任何未知情况下只读恢复、不重新POST。

## 实际出站输入核验

纯组装离线 hash `2751968fc0133fa9ab0c55abe85d71c8e775b35a3372632e16375d11dd9bdadd` 首先与上轮保存的新输入相等；此值不是凭离线结果宣称发送。之后从实际 CALL.request_input 重新解码/计算并验证，实际输入 hash **2751968fc0133fa9ab0c55abe85d71c8e775b35a3372632e16375d11dd9bdadd**；两者本次恰好相等。原请求字符串 UTF8 SHA `2751968fc0133fa9ab0c55abe85d71c8e775b35a3372632e16375d11dd9bdadd`。

`actual-input-verification.json` 是实际CALL的独立核对：generation-evidence-1；43 source_statement 谓词与10 unadopted_clause 断言完整；七真实revision_targets及同kind revise提示；QTMP pattern由实际schema派生且相同；items/questions/excerpts逐项与冻结一致，omitted=[]；旧assistant正文/整响应没有重复发送。没有把原错误候选清洗成正确候选。

实际输入中历史 SRC-91cf252efd44431e/reference，created `2026-10-02T16:16:25.505891+00:00`，SHA `1ef8d3e61e501fd4f0d08e6a6e3dec30ad918c4da90dabe30b6bbc243cf80d90`；当前 SRC-d456da0255274954/goal，created `2026-10-02T16:16:25.543543+00:00`，SHA `40fb3b96c94d083c8c9b4f21d2931438b5603f33d249ff49fdb156c5fa7d18cb`。全部原excerpts和引用保留。CALL没有独立source_hash顶层字段；这些来源hash来自实际request的来源索引并与冻结原文件身份核对，不能声称新增CALL字段。

## 第一实际拒绝与独立内容结论

第一实际拒绝位置：`$.questions[0].target_question_id=Q-1e44e50ac69c4bb0` 与 `decision_points=[]`。目标真实存在，仍open、没有保存回答；失败原因是 **target_question_id只用于明确拆分未答问题，decision_points为空不构成拆分**（app/understanding.py:196-202）。通用报错包含「已有回答必须保留」，本例并非已答目标。旧Q仍原样，未伪造回答或改成已答。

严格JSON解码、stage schema、公共schema及实际生成schema都匹配；stage-local ingest.validate成功，完整validate_response复放同REFERENCE_INVALID且禁止付费修复。原实际流程没有进入后面的条件语义校验。对原对象另行直接条件诊断得到SEMANTIC_BLOCKED（proposals[0].behavior.result，unqualified_success_conflicts_with_exception），与实际首拒分开记录；未改raw再做可接受化。原响应未登记为review。

| 原输入/当前合成决定 | 本次原始输出具体证据 | 有界内容结论 |
| --- | --- | --- |
| 任一配额不足→整批拒绝不创建；服务明确拒绝→本批不创建 | proposals[0].statement「全部项配额满足时整批创建」；behavior.result「全部项配额满足则整批创建」；result.understanding及product_context_proposal.change_scope同样保证创建 | **仍不合格**：必要→充分误推未消失。全部配额满足且服务明确拒绝时，结果字段与例外字段相冲突。例外确实保留，不能称全部丢失；仅文本逻辑检查，未测云后端 |
| 当前决定：响应丢失/超时未知，沿原申请ID查询，确认前不重复 | proposals[4]正确表达查询/不重复；questions[0]、findings[0]、summary、limitations及understanding又说待PM裁定超时口径 | **仍不合格**：当前明确合成选择又被当无答案。不得伪造系统保存回答/真实批准，也不能靠再选历史口径处理这份已冻结决定 |
| 七候选真实ID与kind，未采纳 | 七项全action=revise，分别target原七ID且同kind；Q临时ID为QTMP-timeout-conflict | 身份表达比上次七add/null改善，但**没有注册新ID/版本**，原v3七candidate不变，不能称修订已落实 |
| 同租户/每批20/任一不足整批拒绝，反馈每失败项标识原因、无其它租户泄漏 | proposals[0]/[2]及summary/understanding保留原数量和量词 | 原raw保留；不代替整条结果合格 |
| 操作者租户管理员，需求PM确认不是实际执行审批；不新增审批/排序/分页等义务 | proposals[1]/[5]/[6]及summary/范围说明 | 原raw保留，未冒充PM执行审批人，无新增培训/时限义务 |
| 本期审计要求沿用入口，无本期覆盖实现证据 | proposals[3]、summary、understanding明确当前无覆盖证据 | 原raw没有把既有审计正常提升为本期已实现 |
| 未提供旧逐项流程；允许定性目标，未量化不是错误 | product_context_proposal.value「支持一次提交多项云资源申请，替代逐项提交；具体效率收益未量化」 | 未采纳比较前提没有来源或假设标注，需核对。缺量化本身不硬拒，也不是唯一阻塞理由 |
| 未提供入口/字段/失败反馈存储查询细节 | findings[1:3]给补充页面/字段/留痕建议 | 仍是建议/待定，未授权扩义务或补造既有页面事实，本轮不据此新增范围 |

完整原句/source_refs、七对象旧→raw revise→实际保存对象、实际存在的版本字段/缺字段、各statement/behavior及refs差异在 candidate-comparison.json；所有 saved_after_full 与 old_full 相等。summary/understanding以旧注册RUN-16fbea78b75445df.response对本次raw全文比较，保存项目实际存在字段而不虚构project.summary；见 narrative-comparison.json。本次只更新正常授权grants和新增TASK/RUN，raw未部分接受，无新候选、回答、人工采纳或正式业务确认。

UI终态保留 ui-after-refresh.txt，显示「分析未成功」及具体校验理由，输入为空、发送禁用。ui-terminal.txt为刷新前旧paused状态的中间页面，原文件保留，不能当本次任务状态。界面通用文案说「格式问题」，本次实际是引用工作流违规，不能据该文案许可付费格式修复；本阶段不改UI。

## 用量、保护和未验范围

| 本阶段真实CALL | prompt | completion | total | 应用校验 |
| --- | ---: | ---: | ---: | --- |
| CALL-7fd0f893ebf045df | 23296 | 4901 | 28197 | REFERENCE_INVALID |

旧主批2请求/45651已知tokens + 本次1/28197 = **3/8、73848已知tokens**。网络/未知/截断0、自动修复/重试0；这是新主批统计，不冒充全历史或全账户用量。金额没有账单，UNKNOWN。CALL文件SHA `34532b4de64651b64036b13793935112ed1f3c0f194c32c13e2aaf1da3a7a381`；final_output原UTF8正文SHA `82bdb95d2ded020503746fe2ccece45097f42dcabe122a147225c9e1fba12622`，两口径明确区分。

保护实际结果：22项目不增；315 revisions不增；records800→802，仅新增本主TASK/RUN；所有旧records/revisions/其它项目原行hash不变，主project仅grants正常授权变化；主v3七candidate、Q、来源、原文档状态不变。190旧CALL逐文件hash保持，新增唯一CALL后共191。旧evidence/work/dist/配置/凭据hash与协调回执不变；只本地保留全DB逐行hash，不上传完整DB/真实资料/密钥。保护通过不代表语义合格。

本轮实现/测试未变，无测试、Office、构建或重启。1411通过/5项Office skip仅属上轮4ce112b离线源码证据，不能计作本轮新增测试。0回答/采纳/MRD/PRD/registered review/confirmation/baseline/下载导出/反例调用，全部NOT_RUN。机器消费者、新机/安装器、真实PM和发布仍未验。

本轮证据独立 create-only目录 evidence/real-organize-context-retest-20261003-0412，不覆盖旧原始输入/响应/失败记录。两次只读诊断命令自身失败已保存原因：PowerShell展开字面$defs导致KeyError、临时字典表达式语法错误；改用保存脚本完成同一只读核对，没发模型/改响应/写业务，不能误报产品损坏。

协调链仅只读：当前request-cap回执SHA c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9及append correction不变，恢复旧hash曾自动审批拒绝（可能破坏证据链），本轮不恢复/换路由。generation-context-coordinator-closeout-20261002.json SHA23699bf4f9ddeed14d82521396ffaa5db1f576cdd39c3af253474406a2b951f2核对不变。原协调v1路径/内部投影false flags与v2原回执均保留；更正口径：旧报告对v1「exit1」的说法不能当退出码证据，协调实际exit0有false flags，不能称产品数据损坏。

本轮只将本报告精确路径本地Git归档。最终报告提交SHA/父链/stat/精确diff、报告hash、源码/服务对应和保护复核入final-binding.json；实现保持4ce112b和28a0a64060190f80，dirty仅原work，不提交raw/DB/配置。未push/merge/deploy。

最小后续建议：父先独立核本次原响应及输入，决定是否另授权**离线**检查现有开放问题的生成动作路由与必要/充分断言的候选复用。七revise和QTMP只改善身份表达，核心成功断言和已给选择仍重复；本轮不继续堆验证器、不清洗响应、不再付费刷结果，也不代填PM决定或继续主链。
