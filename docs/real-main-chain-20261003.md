# 2026-10-03 主场景有界真实验收：首步条件升级后停止

本轮主链未闭合。一次真实 DeepSeek 请求返回合法 JSON，应用校验接受并保存了候选，但人工核对发现“配额满足”被提升为“整批创建”的充分条件。本轮按预先冻结的首缺陷停止规则结束，没有修改源码、正文、业务真值或校验，没有再发请求求成功。

## 范围、前置和冻结

- 唯一工作台：`http://127.0.0.1:8765/`，根目录 `data/`，监听进程 PID 49492。无重启、第二服务、观察器或 Office 操作；旧 PID 47496 已不存在。
- 起始实现提交：`ddd5b4cdf14616563db36b374a98d277bf1857d6`；父：`ec3aacf4de087bcceb5eeac2a2b7278c45254f12`；分支 `codex/conversational-intake-20261001`。实现未变，源码/正式服务指纹均为 `a985c172d208bcf5`。
- 前置：数据库无 queued/running；可见输入为空，无停止按钮；Git 只有未知未跟踪 `work/`。上一回合终态采用主管确认，本轮没有重新投递旧任务。
- 只处理已准备的纯合成主项目 `P-f73f8130d36040af`，旧来源项目 `P-5ec3a502e2c94cc9` 原样保留。反例 `P-f31d1a6267f041bc` 完全只读，完整反例 `NOT_RUN`。
- 原真值文件 `evidence/real-minimal-retest-20261003-0012/frozen-plan-and-truth.json` SHA256：`2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc`，原文件未改。
- 新目录：`evidence/real-main-chain-20261003-0132/`。新 `execution-freeze.json` SHA256：`4cfe5352e4571fda3c1f24e16453e146607f32b3fdd94bf507a361baa98da0a1`。它引用旧真值，独立冻结本轮四步计划：整理、MRD、PRD、最终审查，各 `max_calls=2`；最多允许第五个必要动作，但本轮没有重新分配。
- 硬限：全轮最多5业务动作/8实际出站请求，首次、自动修复、已知 HTTP 重试和截断升级均计入；未知/保留占用不释放。操作者是 `synthetic test operator`，不是用户真实 PM、业务批准或发布批准。

## 实际公开路径与预算

1. `GET /api/session`、主项目、actions/runs 和只读数据库活动任务检查，确认源码/服务绑定与空闲。
2. `POST /api/projects/P-f73f8130d36040af/actions/plan`：revision=2，action=organize，message 为空，max_calls=2。HTTP 200；effective max_calls=2，stages=[ingest]，missing=[]。授权对象仅两份现有纯合成来源，未发送整库或真实材料。
3. 冻结公开 plan hash `1990c4aa45a88409107da0e9dfa48c0a6a2eaf57a8b94797cede81051cfc2a11` 和唯一提交键后，单次 `POST /actions` 正常授权/开始，没有直调 runs、models/test 或 Provider。
4. `TASK-97c9f6a3da384d08` / `RUN-16fbea78b75445df` / `CALL-a88590ed94784688`；16秒左右返回。Provider HTTP 200，finish_reason=stop，validation_result=accepted；任务与 run 为 partial，result_applied=true，形成项目 revision=3。
5. terminal 后核 ledger：limit=2，used=1，calls=1，reservation=response_received；全轮保守占用1、实际发送1、剩余7、未知0。无修复、HTTP 重试、截断升级、网络未知或额度续跑。

执行脚本初次核计划时，误在 token policy 摘要中查找请求上限字段，产生本地 KeyError，发生在任何 submit 之前。公开顶层 max_calls 已正确返回2；脚本改查实际契约字段后使用同一已冻结计划，没有改产品源码或重复模型。此事实保存在 `freeze-hashes-and-driver-note.json`，不计为模型失败。

## 原输入 → 决定 → 真实结果 → 人工判定

两份来源都保持 v1：

- 原输入 `SRC-91cf252efd44431e / EX-b150205f8a2c4c1e`：同租户每批最多20项，任一项配额不满足则整批拒绝不创建；既有审计正常运行不能证明新场景覆盖；原文含“超时视作失败并允许再次提交”。
- 合成操作者决定 `SRC-d456da0255274954 / EX-28c1e86aa1bb423d`：服务明确拒绝时本批不创建且既有资源不变；超时/响应丢失为结果未知，沿原申请ID查询，状态确认前不重复提交；审计沿用既有入口是本期要求，没有既有实现覆盖证据；无新增执行审批人。
- 同来源 `EX-bef928a4d1714f21`：整批拒绝反馈本批**每个**配额不满足项的标识及对应原因，仅本租户，不新增审批、排序、分页。

| 维度 | 真实输出位置与结果 | 本轮人工核对 |
|---|---|---|
| 数量与整批拒绝 | proposals[0].statement / REQ-6809025ae40a4e2f v1：同租户、每批最多20项、任一配额不足整批拒绝且不创建 | 这些限定保留；没有正式 AC，因此20/21端点的成文验收未执行 |
| 成功条件 | proposals[0].behavior.result：**“全部项配额满足则整批创建；任一项配额不满足则整批拒绝，本批不创建任何资源”**；同句逻辑也出现在 result.understanding | **BLOCK_NOW：必要条件被写成充分条件，本轮停止点** |
| 失败反馈 | proposals[2].statement / REQ-d9e3c695e4c14ee7 v1：每个不足项标识及对应原因，不泄露其他租户 | 原决定的量词、对象与权限保留 |
| 未知结果 | proposals[4].statement / RULE-59348d25135b4a91 v1：超时未知、原ID查询、确认前不重复提交，不能断言既有资源未变 | 条件保留，没有擅自采纳重试 |
| 审计 | proposals[3].statement / ITEM-5924943e07404777：本期要求沿用入口，无本期覆盖实现证据 | 没有把既有审计正常运行升级为已实现覆盖 |
| 角色 | proposals[1].statement / ROLE-82840aab76d244e6：租户管理员，无新增审批人，需求确认不属于执行审批 | 未把 PM 写成实际执行审批人 |
| 建议价值 | product_context_proposal.value：**“支持一次提交多项云资源申请，减少逐项提交操作；具体效率收益未量化”** | 对旧逐项流程的比较前提没有来源且未标假设，保留独立风险；不以“未量化”本身否定定性收益，不靠这条争议单独决定停轮 |

成功条件的可复核反例：同租户20项、全部配额满足，但服务明确拒绝。冻结决定要求本批不创建，模型行为却按“全部项配额满足则整批创建”断言创建。冻结材料没有规定“服务明确拒绝只能由配额不足引起”，同时要求其它既有业务规则和权限保持不变；不能自行排除该情形。此为离线语义反例，没有执行云平台交易，也没有新增业务决定或额外模型请求。

模型另提出 `Q-1e44e50ac69c4bb0`：用两份正确来源询问历史超时失败口径与新增未知结果口径采用哪种。问句本身正确指出冲突，本轮不把合理问题统一判错。新决定已经是输入；发现成功条件升级后，没有保存新回答、调用答案落实、强行采纳、更改条款或清零问题。所有7条条目仍为 candidate，产品上下文建议未正式采纳。

## 计费证据及状态边界

| CALL | HTTP / 校验 / finish | prompt | completion | total | repair_of |
|---|---|---:|---:|---:|---|
| CALL-a88590ed94784688 | 200 / accepted / stop | 10,452 | 4,245 | 14,697 | null |

实际发送1；收到成功 HTTP1；传输/结构失败0；未知0；自动格式修复0；业务人工核对未接受1。金额无账单证据，`UNKNOWN`。本轮用量单独统计，未把此前13次/315166 tokens当作本轮或全历史用量。

- 整个 CALL JSON 文件 SHA256：`cfea68ca78a5759443a9d199dcc28525771d4e412ddc780da4c90366aedf5b91`。
- final_output 原文 UTF8 SHA256：`314f6c8e3870796c3258d20f049ff3d2af0ebb00a58e50760f141fe1de2264d3`。与文件 SHA 分开，不混称原文 hash。
- actual_input_hash：见原 CALL、run attempts 及 `stop-and-human-content-audit.json`；本轮 request/plan/authorization/run/source 与输出身份均保存。
- MRD、PRD、注册最终业务审查、文档人工核对、正式 confirmation/baseline、MD/DOCX、交接ZIP、CRC/manifest/hash/requirements/constraints一致链、完整反例全部 **NOT_RUN**。没有文档或基线 ID 可声称通过，没有把人工阅读代替注册 review，也没有人为请求下载绕过门禁。

## 保护核验、验证命令与交付边界

`before.json` 与 `after.json` 按行 SHA 比较：22项目不变，314→315 revisions、792→798 records，仅主项目新增 revision3及6条正常业务记录。其它21项目当前行和全部既有 revisions/records原行均未改。旧188 CALL文件原字节不变，新增1CALL；反例API快照相同。旧证据、原冻结文件、work/、模型配置行、凭据文件hash、`.env`hash、web/dist及所有原tracked文件hash不变。只有既有8765监听，源/服指纹一致，无在途任务。未读取或输出密钥内容。

实际执行（本轮不会重放这些业务命令）：

```powershell
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py init
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py plan main-organize
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py start main-organize
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py poll
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py capture main-organize
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/closeout.py
.venv\Scripts\python.exe -X utf8 evidence/real-main-chain-20261003-0132/execution.py protect-final
```

init/protection/capture/closeout退出0；plan首次仅本地驱动字段检查异常退出1，响应及修正说明保留；start退出0；公开查询与预算/保护核对退出0。原四步冻结计划未改为声称已经执行的五步计划。未运行 pytest全量、Office或额外压测；无实现变更，复用主管已接受的预算回归和历史 Office范围，不把它们计成本轮真实主链通过。

公开接口、原始 CALL、终态 task/run、真实 UI草稿、停止审查、保护快照和各文件hash在新 evidence目录，供主管独立复算；原模型字节不进Git。本轮Git只归档此中文报告，实现仍绑定 ddd5b4c；最终报告提交、父链和精确diff写入同目录 `final-binding.json` / `report-commit.diff`，保留 work/，不 add-A、不推送。

有限规则没有覆盖所有自然语言条件推论：JSON/schema/引用接受与候选成功保存，不能证明业务内容正确；候选行为也必须人工核对。此次在生成文档前发现条件升级并保留未采纳状态，保护门未被人为解除。主链未闭合，真实PM、机器消费者、新机/安装器、发布均未验。本轮到此结束，不修代码、不追加下一真实批次。
