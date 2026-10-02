# 最小真实复验在模型前阻塞（2026-10-03）

本轮状态是 **BLOCKED_BEFORE_MODEL**，真实复验未执行，不能称模型验收通过。父已独立验收上一轮离线修复并授权最多5个业务动作、包括自动修复在内最多8次真实发送。本轮先核现有入口，发现请求次数上限被忽略；按用户“遇首个产品缺陷停止该分支、父审核后再决定下一修复”收口，没有改源码来继续，没有放松真值或预算。

起点 `f55b085e5f9f58679b140a1d3708745e4d773a9a`，源码 `29341eaca5337acc63f20fc0c0d1363b15710c8d`，分支 `codex/conversational-intake-20261001`。8765及磁盘指纹均 `c6e25f01e6d832a8`，无在途任务，8765可见输入为空、无停止控件。原任务终态的发送前提由父提供；本轮仅核实际工作台与在途记录，不把当前Codex执行状态冒充原任务GUI状态。未重启、构建、改配置/凭据/代理/网络/许可、处理充值卡或进行其它项目工作。

## 冻结真值和有限计划

`evidence/real-minimal-retest-20261003-0012/frozen-plan-and-truth.json` SHA256 **2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc**。冻结发生在任何模型请求之前，没有根据生成结果修改真值。

计划分配：主整理2次、主MRD2次、主PRD2次、主审查1次；反例答案落实预留1次，共5动作/8请求。每动作含所有自动修复、重试、失败和未知发送。只有现有run机制能落实这些上限才能提交；不可使用全局 unlimited 或外部轮询取消冒充上限已生效。未知结果先核已有任务，不盲重发。任一不可修复错误停止该分支，另一分支仅能继续原冻结步骤且不超总上限。

主场景沿用旧纯合成事实：同租户每批最多20项，任一配额不足整批拒绝且不创建，明确拒绝时本批不创建/既有资源不变；超时或响应丢失为未知，沿原申请ID查询，状态确认前不重复提交。本期沿用审计入口不证明旧实现覆盖；租户管理员执行，无新增审批人，没有既有逐项流程事实。只增加用户本轮明确的测试决定：反馈本批**每个**配额不满足项的标识及对应原因，仅本租户，不泄露其它租户，不新增审批/排序/分页。

反例仍保留历史100Unicode码点与emoji恒一码点输入，以及冻结选择100扩展字素簇：99A+👍🏽允许、100A+👍🏽拒绝，复合emoji可能多码点；明确失败不写入、超时未知查原请求，不擅自重试。操作者是 synthetic test operator，材料准备和测试决定不是真实业务PM或上线批准。

三个旧冻结文件hash及目录均保留：oracle `e66c07631cbe40f95462421187eaf44efce0e9184ae6e775e248266b68d3e537`、round-freeze `4feebf6e69e6dac9a740ae4a461efd211bf7312a3db7d1394648ecd544fdada0`、task-freeze `344602d597e8bf20e6edf48e66680d6d2f4d06a3591b7d0ae6c6272f035f6b01`。父的 `offline-decision-flow-independent-audit-20261002.json`及独立84项结果仅作为上一轮核验，未冒充本轮测试。

## 合法准备与来源映射

仅调用已有 `POST /api/projects` 和 `POST /api/projects/{pid}/sources/text`，新增两个独立项目、各两份纯合成文本来源；没有直接写SQLite、复制整库、seed实体/问题/答案/阶段状态或改最终正文。

| 旧→新项目 | 新项目状态 | 历史原输入来源v1 | 合成决定来源v1 |
|---|---|---|---|
| P-5ec3a502e2c94cc9 → **P-f73f8130d36040af** | revision2，只有材料 | SRC-91cf252efd44431e / EX-b150205f8a2c4c1e | SRC-d456da0255274954 / EX-28c1e86aa1bb423d、EX-bef928a4d1714f21 |
| P-df7c5942e7904214 → **P-f31d1a6267f041bc** | revision2，只有材料 | SRC-9723a1398c984805 / EX-015b35f853b64a6b | SRC-d01093b3ab6449c6 / EX-f82f4e30dc6145d6 |

`main-mapping.json`、`counter-mapping.json`及`lineage-mapping.json`记录旧项目/来源/问题/文档版本到新来源的身份、位置和hash。新项目均 items=[]、questions=[]、documents={}；没有把复制的材料称为已恢复的问答状态或真实生成结果。

## 公开入口真实复现：请求上限丢弃

在新主项目revision2，调用：

```text
POST /api/projects/P-f73f8130d36040af/actions/plan
{"expected_revision":2,"action":"organize","message":"","document_type":"prd","max_calls":2}
```

实际HTTP200，返回 **max_calls=null**、`budget_policies.ingest.mode=unlimited`、`missing=[]`。输出tokens策略的上限不是请求次数上限，不能据此宣称本轮8次发送可控。请求/响应分别是 `api-0017-request.json`、`api-0017-response.json`，汇总是 `blocking-plan-probe.json`。这是正式8765真实非模型接口结果，不是Mock。没有 POST /api/projects/{pid}/actions 或 /api/projects/{pid}/runs。

源码与此一致：`app/main.py:85`接受ActionPlan.max_calls；`app/actions.py:91`的plan、133行任务记录、157行执行config均强制max_calls=None；`app/workflow.py:108`也将配置设为 unlimited/None。原`ActionStart`仍能携带max_calls且参与plan_hash，但它不成为实际run请求次数限额。自动修复/截断升级/明确服务错误重试仍可能追加发送；本轮不改这些源码或用取消竞态绕过预算。复现到此停止，首个真实模型请求**未启动**。

## 反例的独立恢复边界

当前OpenAPI没有按项目恢复/克隆已答状态的入口；`history`与`artifacts`是读取，`business-context`是材料导入，不恢复原问题/回答身份和应用状态。现有备份工具也不提供本任务所需的新项目身份映射恢复；不能把整库/旧项目还原覆盖到正式data。

旧Q-285b7d4a7c7744a8/Q-a2378286a6454466及其回答/出处仍在旧反例中原样保留。新反例只准备原材料与合成决定，未恢复原目标，故单个“答案落实”动作 **NOT_RUN**。不把追加文本来源冒充系统已保存的答案，不重新生成一整套来耗费预留预算。

## 实际结果、保护与停止

真实业务动作启动 **0/5**，DeepSeek实际请求 **0/8**：成功0、失败0、unknown0，CALL/RUN列表为空，自动修复0；token为NOT_APPLICABLE（没有模型请求），无账单金额UNKNOWN。主整理/答案落实、MRD、PRD、登记审查、正式确认、MD/DOCX、机器ZIP、CRC/manifest/requirements核验，以及反例答案落实均 **NOT_RUN**。没有合法当前审查或基线，自然不能称交接通过。

`closeout.json`逐原数据库行hash验证：原20项目/308 revisions/788 records全部不变；新增仅为2个准备项目及其来源相关记录，现为22项目/314 revisions/792 records，settings/migrations各1保持。188份原模型CALL文件逐文件不变，三个旧证据目录、work元数据及其它受控文件不变。未写或上传真实资料/整库，未改旧P-5ec3a502e2c94cc9、P-df7c5942e7904214的needs_changes、未知Q-a9c484eba79746ae、文档/确认/409条件。原409证据复用，本轮没有重复旧项目下载操作。

只归档本说明，不提交模型证据、配置或生成物。命令 `preflight-control.py freeze`、`prepare main`、`prepare counter`、`probe`、`close`均实际退出0；其退出0表示准备/阻塞事实核对完成，不是模型业务验收通过。无源码改动，不重复上一轮1252/5skip全量、实际Office或压测；源码/正式runtime继续是c6e25f01e6d832a8。

父的下一判断仅需覆盖：怎样恢复可验证的本轮请求硬上限，以及反例没有项目恢复入口时是否批准另一条合法、明确动作数的验证路径。本任务不自动修复这些新边界或开启下一批。单合成场景、机器消费者、新机/安装器、真实PM及发布验收均未新增验证。
