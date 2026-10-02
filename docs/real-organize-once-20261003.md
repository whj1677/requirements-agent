# 一次真实整理复验：原结果拒绝，未形成新候选

本轮仅对主合成项目 `P-f73f8130d36040af` 通过公开 plan/actions 执行一次 organize。应用实现、测试、配置和前端均未改；不派Agent、不启动观察器、不重启8765。DeepSeek实际请求1次、修复/重试0次，遇首失败停止。本轮不是完整主链通过，也不是实际PM批准。人工读出的内容发现未注册为产品正式review。

起点 `31bfcfb8b000b981cfb21018fc7c9d8bdd7dcbf6`，父 `cf4c19e7cf329c690bec0b69ae66df3981e751c7`，分支 `codex/conversational-intake-20261001`；磁盘/运行 `60455f5fbffededd`，app SHA256 `60455f5fbffededdafbd1dd83663b1d983c76c6b96c05271daa0924bcb6557d3`。前轮已终态见会话上一条final和父独立验收；按用户给的回合ID调用read_thread未匹配线程，不把该工具返回冒充终态证据。唯一8765监听PID50068、旧观察器46232不存在；本轮未保存聊天输入为空，无Stop。刷新后UI明确显示额度1/1、已暂停及需新计划/授权，未点续跑。第一次刷新抓到加载中状态另存，终态另名，不覆盖。

本轮全部执行证据在 `evidence/real-organize-once-20261003-0323/`，create-only新目录、新名。`execution-freeze.json` 在真实发送前保存完整v3项目、七候选、开放Q-1e44e50ac69c4bb0、原输入/来源时序、固定预期及禁止后续动作。旧冻结真值SHA256 `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 保持不变；原事实和合成操作者决定未改、未代填答案。

## 公开计划、授权与真实用量

公开 `/api/projects/P-f73f8130d36040af/actions/plan`：expected_revision=3、action=organize、message为空、max_calls=1，stages=[ingest]、missing=[]。plan_hash `878597d1685738d01f8eb38e6e4a7e1ebda17fa2ed6d32ffb1c0970e89f69293`；一次新幂等键/authorize=true提交前保存 `organize-submit-intent.json`，随后只读轮询，未重发。TASK的authorized_configs.ingest.max_calls及RUN授权approved_max_calls均为1。

| TASK | RUN | CALL | HTTP / finish | 校验 / 终态 | prompt / completion / total tokens |
|---|---|---|---|---|---|
| TASK-522ba423abbb41f2 | RUN-7638591b186f4251 | CALL-e53bae5e8fb04d66 | 200 / stop | SCHEMA_INVALID；TASK与RUN paused_budget / BUDGET_EXHAUSTED | 26382 / 4572 / 30954 |

预算实际used=1/calls=1，request_state=response_received，repair_count=0、repair_of=null，无在途、无最终网络未知、金额UNKNOWN（无账单）。运行时预留dispatch_unknown只作为在途状态保存，未释放/重发。前链CALL-a88590ed94784688已用1次/14697tokens保持原accepted/partial，不事后改判；本轮使该主链累计 **2/8请求、45651可证tokens**。该累计仅两次主项目整理，不是全历史总用量；其余6次不是本轮授权。

actual_input_hash `a5a3dfd12d047d8330b8a314f2801745d64111bd5e14ff1d485ce806e6ba6c26`；已从原request_input解码并用程序digest复算相等。输入仅本主项目七候选和两合成来源，没有其它项目资料：SRC-91cf252efd44431e（reference）SHA `1ef8d3e61e501fd4f0d08e6a6e3dec30ad918c4da90dabe30b6bbc243cf80d90`；SRC-d456da0255274954（goal）SHA `40fb3b96c94d083c8c9b4f21d2931438b5603f33d249ff49fdb156c5fa7d18cb`。原source/excerpt配对和定位保存在request-scope-verification.json，旧→本次输入时序不重置。

## 精确失败与人工内容核对

原JSON完整合法、无重复键。实际第一个拒绝位置 `$.questions[0].temp_id`：`TMP-Q-timeout` 不满足 `^QTMP-[A-Za-z0-9_-]+$`。原字节对stage-local ingest.validate和完整validate_response离线复放均为SCHEMA_INVALID，未删/改非法ID。本次实际路径尚未进入后续语义校验；不能说该CALL已得到SEMANTIC_BLOCKED或业务accepted。预算保护阻止准备中的下一格式修复请求；未发修复，repair_count仍0。

人工读原输出另发现 **必要条件被加强为充分成功**：`$.summary`“全部满足则整批创建”；`$.proposals[0].statement`“全部项配额满足时整批创建”；同项behavior.result“全部项配额满足则整批创建”；`$.result.understanding`同样写该保证。源句仅有“任一项配额不满足则整批拒绝不创建”，并明确服务拒绝不创建。可信反例：配额全部满足但服务明确拒绝，未限定的保证与exceptions冲突。原exceptions确实保留服务拒绝/结果未知，不能声称全部丢失。classification_reason又声称“成功与配额拒绝分支均来自材料”，其成功充分条件并无引用原文支持。该问题仍不可采纳，不修改输出来通过。

历史超时句及合成新增决定均保留，但summary、understanding和一个新问题仍要求PM再次选择已有明确本期决定。原Q开放/answer=null保持不变；不把来源材料直接写成系统正式回答，也不伪造答案。该时序及当前决定识别留父独立审核，不能据本轮清零阻塞。

7项raw均为add/target_item_id=null，即使输入已带原七ID。下面只按相同kind/title作审阅对照，不称正式身份映射：没有新正式ID、新版本或新selection，原七条仍candidate。`candidate-comparison-v2-exact-fields.json` 保存每项原/保存后/原模型全文statement、behavior、source_refs、scope_evidence、分类及字段差异；三类版本字段如实保留，通用version不存在，不能由null猜版本。

| 保持原ID | 实际content_version | 原模型未注册temp_id | 原模型statement |
|---|---|---|---|
| REQ-6809025ae40a4e2f | 1 | TMP-REQ-batch-apply | 租户管理员可在同一租户内提交批量云资源申请，每批最多20项；全部项配额满足时整批创建，任一项配额不满足时整批拒绝，本批不创建任何资源。 |
| ROLE-82840aab76d244e6 | 字段不存在 | TMP-ROLE-tenant-admin | 本功能的实际操作角色始终为租户管理员，不新增审批人；需求的人工核对/确认不属于云资源实际执行审批流程。 |
| REQ-d9e3c695e4c14ee7 | 1 | TMP-REQ-reject-feedback | 整批拒绝时，向本租户管理员反馈本批每个配额不满足项的标识及对应原因，不泄露其他租户信息。 |
| ITEM-5924943e07404777 | 字段不存在 | TMP-ITEM-audit | 本期资源申请要求沿用既有审计入口；当前没有该场景的实现证据，不得声称旧实现已覆盖这项新场景。 |
| RULE-59348d25135b4a91 | 1 | TMP-RULE-unknown | 响应丢失或超时为结果未知，不能据此断言申请失败或既有资源未变；应沿既有申请ID查询同一申请结果，状态确认前不重复提交。 |
| ITEM-f709c5136a5f4c40 | 字段不存在 | TMP-ITEM-nogoal | 本期不新增审批、排序或分页规则；也未采纳自动重试、结果未知时新建申请、配额不足时部分创建及培训/时限等义务。 |
| ITEM-6d67b812b92f4228 | 字段不存在 | TMP-ITEM-preserve | 服务明确拒绝时本批不创建且既有资源不变；其它既有业务规则和权限体系保持不变。 |

正确保留的部分：租户管理员及PM确认不属于执行审批、同租户每批最多20、任一项不足整批拒绝不创建、每个不足项标识/原因及租户隔离、明确服务拒绝与未知响应分离、未知沿原ID查询且确认前不重复、本期审计要求且无旧实现覆盖证据、未采纳额外规则/义务。审计留痕写作本期目标，不作为实现证明。普通定性收益或未量化不作为唯一硬拒理由；本次value已把“逐项重复提交”的前提标为待核对，仍是未采纳提案。

原完整响应位于 `final-output-original.json` 和本轮CALL副本，未手工改写；所有summary/understanding/context/问题全文与前后对照另保存在candidate-comparison两份文件。CALL全文件SHA256 `d8bb419f976645358b5693749beb345fa59082f37f2ff58265edcaf166cb22d3`；final_output UTF8原正文SHA256 `2f1d3810cc0be6c3e6f553a463554ea6297452e97a5651a6b94951ae9da8113a`，口径分开。

## 保护与停止边界

before/after逐行本地哈希：22项目、315revisions保持；records798→800，仅本主项目新增TASK/RUN。全部旧records/revisions行不变，原189CALL逐一hash不变；主项目payload仅grants按本次明确授权更新，其revision/items/questions/sources/documents未变。反例P-f31d1a6267f041bc v2及其他21项目未改。原source目录、旧evidence（包括cf4c19e和两误报首失败日志）、冻结真值、work、dist、配置、所有原跟踪文件及明确旧协调回执均与本轮起点一致。新CALL使总量190，不提交DB/原raw/秘密，不上传全DB或资料。

协调回执事故单独保留：request-cap-independent-public-contract旧索引 `df31c785fcf178eefdc2fb81ed2c937cdad89e615f6322ba166e709e9b141cb7`、当前 `c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9`；追加更正保留。恢复旧hash曾被自动审批以可能破坏证据链拒绝，未恢复、未换路由。不能把本轮保护一致称作历史从未出过事故。

0答案应用/0人工采纳/0MRD/0PRD/0正式审查登记/0确认/0基线/0下载导出/0反例模型调用，均NOT_RUN。没有实现变化，不重复全量/Office，旧1394通过/5skip仅属前轮离线证据。本轮的原字节确定性复放仅诊断真实SCHEMA失败，不冒充产品验收。

只归档本说明一条明确docs路径本地Git，后续commit SHA/父链/stat/diff与源码/服务关系见本轮final-binding.json。源码仍对应31bfcfb，报告提交不改变应用指纹；剩余dirty仅原work/。本轮已结束，不自动运行UI的续跑按钮。最小后续建议仅供父判断：先离线核问题编号契约、原候选revise身份与当前决定时序，再决定是否另授权一次受影响动作；不扩条件引擎或把本结果称全面ready。真实PM、机器消费者、新机/安装器与发布仍未验。
