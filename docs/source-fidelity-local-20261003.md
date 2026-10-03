# 四项来源语义关口：本地修复与离线验收（2026-10-03）

本轮完成四项有限、可解释的来源检查和公开采纳保护。本结论限定为当前代码的离线合成验收；不能据此认定需求 Agent 全面可交付或真实模型已经改进。

## 修复与行为

- 将反馈收件人推成排他可见权限：同对象、同角色的明确权限来源或人工决定才允许断言。
- 将资源配额改成业务预算/资金：有配额上下文时，需要明确业务预算来源，模型 token 或请求预算不构成业务来源。
- 将配额满足改成字段存在/齐全：需要明确存在性校验来源，不能弱化原来的数量满足条件。
- 声称减少旧流程逐项/人工操作：需要同对象、同操作的既有流程来源；明确标为建议或假设的文字仍可保留。

检查覆盖候选标题、正文、全部行为说明、摘要、分析理解、产品价值及文档解释。生成阶段可保留与对象、来源和问题维度共同绑定的阻塞澄清；采纳阶段仍重新校验，问题存在或勾选“已核对”均不创造业务证据。

沿用现有来源引用及已保存、当前有效、直接关联条目的人工回答。不新增持久字段或公开 schema；内部诊断包含原句、字段、来源 ID、段落位置及已有 source_hash。错误为 SEMANTIC_BLOCKED，禁止自动付费格式修复，不自动删除或改写内容。

单条、整批、参考条款纳入本期、人工回答落实及最终确认关口均接入；同步编辑标题的采纳请求也先检查新标题。真实人工改写正文按现有接口保存为新的人工来源。源代码修复不回写已确认历史文档或旧 CALL。

## 验证

- 第一轮新增案例：51 通过、4 失败；修正两处语义误报和两处合成测试结构错误。
- 相关回归：445 通过；之后补齐审查就绪和解释引用范围，新增关口复测 65 通过。
- 首次全量：1600 通过、6 失败、5 跳过；六处均为明确跨分句假设/建议被误当成旧流程事实，复用既有限定词边界修复，没有更改原有测试预期。
- 最终全量：1614 通过、5 跳过、0 失败、0 错误；进程退出码 0。两项既有依赖弃用警告保留。
- 最终代码磁盘 fingerprint：`f2f1b7b4032646970f3f6a4243d8827f428d05f08ecfa15ce8fa70c0ee369612`，为按路径排序拼接 app/*.py 原始字节的 SHA256。
- 所有测试使用临时 Store / 测试应用；网络守卫记录无外部连接尝试，并禁止日常 8765 连接。MockTransport 合成响应不计为真实模型调用。
- 旧合成 CALL `CALL-94cf90efe9534bbe` 的原始响应只读复核：修复前曾离线通过，现在 SEMANTIC_BLOCKED；四个维度均检出，8 个正反来源对照符合预期，参数不变。保留历史 validation_result，不用新结果覆盖旧记录。
- 原 CALL 文件 SHA256：`59f8cc55df99405490b5fc7796e2b69eee1175bb994210a17f86525a6793fcef`。
- 原响应 SHA256：`e48b4b7a4280ed01b9491a41f6fb2b800b6783d35342d67537f47d5af75cf9b9`。

## 五项跳过

- `tests.test_excel_preview_repair.test_local_office_fallback_locator_reaches_preview_helper`
- `tests.test_office_sources.test_installed_office_reads_synthetic_document[.docx]`
- `tests.test_office_sources.test_installed_office_reads_synthetic_document[.xlsx]`
- `tests.test_office_sources.test_installed_office_reads_synthetic_document[.pptx]`
- `tests.test_office_sources.test_installed_office_relative_data_directory`

这些是 Office opt-in 验证，本轮没有运行。不能从普通全量通过推出 Office 实机链路通过。

## 数据保护、费用与运行限制

开始 HEAD 为 7cf647897afc7d8bf9127ca9be92ab71cf36aa74，分支 codex/conversational-intake-20261001；原有 dirty 仅 work/。本轮我为唯一写入者，未向原 GUI Codex 分派新回合。

保护检查比较 projects/revisions/records/migrations 的全部行，以及全部 192 个旧 CALL 文件、来源文件、原有 work/、web/dist 和无关 app/tests/docs 文件；提交前均不变。项目 22、修订 319、记录 807，未发现 queued/running 模型任务。设置表和凭据未读取；未做 .env 内容或配置 hash 验证，配置未写入。

新增真实模型请求 0、外发项目数据 0、新增模型费用 0；没有可据以计算旧费用的价格/账单证据。此前检查点记录该合成语义场景累计 107331 token，本轮未重新清点历史用量，仅作为继承的历史账，不计作本轮验证。

本轮没有重启服务、操纵用户浏览器或进行 UI/实际 DeepSeek 验收；运行中的旧服务不据此声称已加载本轮新代码。后续须在获得相应外发授权后，小范围验证真实生成→澄清→人工答案→采纳→一致文档→最终确认，不可重发旧失败 CALL。

本机制是有限中文谓词、角色/对象及引用匹配，不能替代一般语义蕴含或完整内容审查；复杂跨句、隐含指代、反讽、未覆盖同义表达和互相矛盾的多来源仍需人工审阅。一个明确来源也不证明既有系统实现或普遍业务质量。

## 可复核证据

本轮本地目录 source-fidelity-local-20261003 含每轮日志、JUnit、命令与源 fingerprint、网络守卫、旧 CALL 修复后诊断、保护检查、最终精确 git show/父链及源码副本。before.json 为本机保护快照，包含旧文件的 opaque hash 清单，不纳入对外证据包。最终 commit、工作区残留和 manifest 在归档 receipt 中记录。

本轮不 push、merge、deploy，不改 Skill/UIAgent、网络代理、凭据、真实业务资料或历史基线。
