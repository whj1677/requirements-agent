# 2026-10-02 第一小轮真实 DeepSeek 验收

本轮已实际执行真实模型请求，但**完整真实交付验收未通过**。主场景完成真实整理、回答落实、MRD/PRD生成，最终审查返回无效JSON；反例完成真实冲突澄清和合成回答保存，回答修订因混合分类被阻塞。没有生成这两个项目的确认、基线或机器交接ZIP，不将旧离线成功导出或HTTP200替代本轮结果。

任务边界是两个全新合成项目的第一小轮；只有已复现的程序误报做最小修复，并重跑受影响整理步骤一次。遇到后续模型输出失败保留原结果，不追加付费批次、不手改最终正文、不补造未知答案、不降低校验。全部回答、候选采纳和阶段核对来自**合成测试操作者**，绝非真实业务PM批准。本轮单Agent执行；不推送、合并、部署，不改Skill/UIAgent、配置、密钥、网络或代理。

## 冻结输入与证据身份

证据目录为 `evidence/real-final-round-20261002-2114/`，忽略于Git。首次模型请求前保存原输入、固定预期、合成决定、未知事项答复、动作消息及边界为 `frozen-oracle.json`，SHA-256为 **e66c07631cbe40f95462421187eaf44efce0e9184ae6e775e248266b68d3e537**。每次控制操作复核此hash，未根据输出修改真值。

入口是现有正式 `http://127.0.0.1:8765/`，采用真实项目/直填资料/actions plan/授权提交/问题回答/候选核对/成文/审查API，并核对正式UI。没有seed实体、mock、deterministic compile_plan或手工替换成文。只发送这两个项目的纯合成资料到已配置DeepSeek。API请求先落盘再提交；持久幂等键及已有run/task核对防止重连盲重发。

起点提交 **db5b5e25f7ed49b1810abc87c5d6c62056cd7352**，正式/磁盘fingerprint **7001cabd1554f22b**，dirty仅 `?? work/`，全局活动任务0，正式可见输入为空。先作一致性SQLite备份 `before-live.sqlite3` 和原行hash保护清单。测试项目分别为：

- 主场景 **P-5ec3a502e2c94cc9**：纯合成真实验收-园区云资源申请-20261002-2114。
- 反例 **P-df7c5942e7904214**：纯合成真实验收-备注字素簇与未知结果-20261002-2114。

## 原输入到实际文档的证据链

主场景原文规定同租户每批最多20项、任一项配额不足整批拒绝不创建；既有审计运行正常但本期覆盖无证据，并错误写“失败时既有资源不变，超时视作失败并允许再次提交”。原始片段 **SRC-815f482331224e26/EX-ceabdbb4f1c4498c** 保留。

真实整理提出 **Q-c629b4080878483d**，同时绑定原REQ与RULE及真实来源，追问超时/再次提交口径；why/options指出未知结果和重复创建风险，没有自行选择方案。审计缺实现证据被保留为提示和候选约束，未升格为已覆盖。另提出非blocking反馈粒度问题 **Q-a9c484eba79746ae**；冻结资料无答案，保留open及未知说明，未为导出选一个模型建议。

合成回答原文保存为 **SRC-0175352bcf214e76/EX-e6137895895a4df4**：明确拒绝本批不创建、既有资源不变；超时/响应丢失为未知，沿原申请ID查询、确认前不重复提交；本期沿用审计入口而无旧覆盖证明；租户管理员不新增审批人。真实clarify生成修订REQ/RULE、审计规则和两个AC候选。人工逐项核对采纳；具体入口/资源字段仍由模型留空，操作者只在behavior空项标“未提供、待真实业务方决定”，没有编页面或字段，也没有改模型最终文稿。此人工处理不计为模型完整理解或真实业务细节验收。

最终已选稳定条目及版本为 **REQ-aa8902ae16654968 v2、RULE-ad63943c24664d73 v2、RULE-fd1c4b2df2cb4fa6 v1、AC-753b569f8eae4375 v1、AC-0be9c302b8a44a98 v1**。修订候选REQ-601eed6e9d634783/RULE-261cc48f41c343cc由程序复制到原身份后deferred；ROLE-c6852dc895c346d7及as_is约束ITEM-702429c9aa2348e1仍candidate，未进入已选交付集合。

真实生成文档：

| 文档 | ID / 文档版本 / 底稿 | content规范化JSON SHA-256 |
| --- | --- | --- |
| MRD | DOC-576d3a6badfc4562 / v1 / v16 | 5098de13700ff8b62499999fd0f90d1638bbea3f6655324b2434e369e75d4f92 |
| PRD | DOC-34d194e3c4654202 / v1 / v17 | 40790c0ab19dcb7799ab6c0c6dc03f607d97e8c3104dcebe451e996d4fd6489b |

这是 `artifact.content` 规范化JSON（UTF-8、排序键、紧凑分隔符）的hash，**不是未成功下载文件的hash**。两文档delivery_item_ids与API当前confirmation_scope_ids均为上述5项；document.requirement_versions还含deferred修订候选，不将该快照数组误称正式交付范围。全部选择、约束分类、来源及原句见 `main-independent-content-findings.json` 和终态项目快照。

规范REQ/RULE、结果未知AC和PRD业务规则保留了明确拒绝/未知分支、同租户/最多20/任一项量词、原申请ID查询与确认前不重复提交。审计正文明确本期要求不等于旧实现已覆盖；未新增执行审批人。但独立内容检查找到真实错误：**MRD-3e895b840001.blocks[1]** 写“租户管理员目前逐项申请云资源”“本期改为”“逐项提交的低效”，冻结来源没有提供该现状和动机，不能由新增批量需求反推旧流程。问题只定位这份MRD和真实块，不连带声称PRD有同句。另 **MRD-b359808c62ad.blocks[1]** 的“本期优先安排”与已核对优先级仍待定并存，标为待人工核对措辞，未替业务方作排序。

真实最终审查 **RUN-b9494359cfba4eda / CALL-794cd14276f741fa** 也在原始文本中指出上述MRD位置及仍open的反馈问题，文字意向为needs_changes；然而 `perspectives` 返回多余引号等JSON语法错误，首次解析为 `Expecting ':' delimiter`，offset3273/column3274。原文SHA-256 **6099b245365bda6977d2b5acd28e569515c67591987b87f63754733c5d240ed1**。HTTP200、finish_reason=stop；合法review没有登记（project.review=null），不能把人工读出的needs_changes算产品审查成功。CALL为SCHEMA_INVALID，程序保真修复检查拒绝不完整JSON，run终态SEMANTIC_BLOCKED。未手修JSON入库，也未追加付费修复。

反例原文 **SRC-0e3655ff87e54b82/EX-b89fba3ce0804b47** 同时写100Unicode码点、emoji一码点及超时明确失败。修复后真实整理 **RUN-c63db7e6f36a4920** 产生 **Q-285b7d4a7c7744a8、Q-a2378286a6454466**，正确指出已有单位定义自相矛盾，给出99个A+👍🏽=101码点/100扩展字素簇，提供一致选项并要求人工选择，同时区分明确失败与未知。没有把材料错报为完全未定义计数单位。

冻结合成选择是100扩展字素簇，99A+👍🏽允许、100A+👍🏽拒绝；明确失败不写入，超时未知先查原请求、不得断言记录未变或重试。两条答案已保存、有真实答案来源，但理解仍pending。随后真实clarify **RUN-71913dad4d93476e / CALL-1a91222cf6ad479b** 的TMP-1同时提交change和preserve的scope_evidence，却标整项new；现有明确契约禁止这种混合身份，程序正确SEMANTIC_BLOCKED。原JSON和来源保留，生成修订未激活/未采纳；该项目没有MRD/PRD、review、confirmation、baseline或export。本轮不反复抽样到模型偶然输出合格。

## 实际程序误报与最小修复

反例首次整理 **RUN-a75ad2972c824df5 / CALL-88bcace107cd45c7** 已生成正确反例，码点备选要求“删除或纠正”引文中的旧错误表述，却被视作继续断言旧规则。先保存失败与公开validate_response最小复放，再修 `app/semantic_quality.py`：仅问题备选启用紧邻真实引文的明确删除/纠正识别，否定该动作仍保留检查；其它活动断言逐个检查，来源、现行条款、人工回答仍走严格默认路径。未替操作者采纳建议，未改schema、业务规则或历史资料。

新增 `tests/test_counting_rejected_quotation.py` 的19项正反回归；修复前12失败/7通过，修复后定向集合237通过。原首次失败JSON字节SHA-256 **310ab475d7e4ae9ae8a084a4f3a5c3e9071ea6c76acdfccdb1ffdba4370e95f0** 不变；零调用复放由阻塞转为accepted，只证明校验修复，不冒充真实生成。随后只重跑受影响的真实整理一次，真实接收成功；冻结输入hash及实际模型input_hash与首次整理一致。

实现/测试/初始说明提交 **3f61bb1bc0dee79fccd16f8dab430c279b4c5171**，父 **db5b5e25f7ed49b1810abc87c5d6c62056cd7352**。本报告是其后的单独说明归档；最终说明SHA/父链见本轮 `commit-receipt.json`，不改已确认历史报告。

## 测试、运行与下载门

已执行命令及结果：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_counting_rejected_quotation.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_counting_rejected_quotation.py tests/test_semantic_quality_boundaries.py tests/test_semantic_quality_witnesses.py tests/test_semantic_quality_regressions.py tests/test_counting_adoption_history.py tests/test_counting_definition_classification.py -q --junitxml=evidence/real-final-round-20261002-2114/targeted.xml
$env:RA_TEST_LOCAL_OFFICE='0'
.venv/Scripts/python.exe -X utf8 -m pytest tests -q --junitxml=evidence/real-final-round-20261002-2114/full-after-fix.xml
```

修复后定向237通过；必要全量退出0，**1161通过、5skip、0失败、0错误，235.05秒，2个既有弃用警告**。JUnit独立重算1166 testcase，其中五个为Office opt-in skip：合成Word/Excel/PPT、相对data目录、Excel预览回退。没有再跑Office；旧3535156干净全量1147通过/0skip含五项实际Office的证据按 `docs/clean-release-boundary-20261002.md` 复用，不声称本次五项实际执行。全量通过不替代真实成文/模型审查/人工业务验收。

源码变化后，确认全局无在途模型与可见未保存输入为空，再以原scripts/stop.ps1、scripts/start.ps1 -NoAuth各执行一次，退出0，保持现有启动模式。正式与磁盘fingerprint均 **a92911e66804cf96**，对应3f61bb1源码；起点前两个整理CALL对应7001cabd1554f22b/db5，后六次对应a92911e66804cf96/3f61bb1。只有原8765监听（终检PID43624，已核项目Python/serve.py归属），没有另起长期服务或Office。组合长命令曾在执行前被自动审批以“blocked by policy”拒绝；未产生模型调用，改为独立执行原脚本和只读检查后完成，未绕过权限或更改配置。

指定两份真实文档ID的MRD/PRD MD/DOCX四次GET均实际 **409/CLARIFICATION_REQUIRED**，原请求/响应留存；正式UI同步显示1项待回答、两文档未核对、失败审查及交接按钮disabled。非blocking问题能通过成文阶段，但下载门检查全部未答问题；本轮如实记录这处阶段差异，未将未知答复冒充已选方案或修改门使其放行。

没有点击“我已核对”认可已知有错的文稿，没有指定版本确认或正式合成基线。**确认后四文件、delivery_scope、requirements机器实际数组、ZIP CRC/manifest/hash均NOT_RUN/无产物**；不存在可声称核过的baseline_id或新增记录字段。既有旧项目409及原成功导出证据不修改、不用作本轮通过依据。机器消费者、新机/安装器、真实PM及发布验收仍NOT_RUN。

## 八次真实调用与用量

以下以两项目run.attempts和原CALL回执去重；不把task、run、UI事件重复计费。全部为真实DeepSeek HTTP200，response_received=true、finish_reason=stop；HTTP成功与输出被接收分开统计。

| 场景/步骤 | CALL | prompt / completion / total | 输出处理 |
| --- | --- | --- | --- |
| 主/整理 | 959a6ab29ef04d1f | 10150 / 3871 / 14021 | accepted，run partial |
| 反例/首次整理 | 88bcace107cd45c7 | 10519 / 3423 / 13942 | SEMANTIC_BLOCKED，误报已修 |
| 主/回答落实 | d7296529fcb24641 | 14188 / 3908 / 18096 | accepted，run partial |
| 反例/修复后整理 | e0cbe804f824410e | 10519 / 3665 / 14184 | accepted，run partial |
| 主/MRD | db194ed059f843d1 | 13718 / 1810 / 15528 | accepted，独立内容审查发现错误 |
| 反例/回答落实 | 1a91222cf6ad479b | 17051 / 3346 / 20397 | SEMANTIC_BLOCKED，混合分类 |
| 主/PRD | 5bcfcac79d7542ce | 15035 / 1398 / 16433 | accepted |
| 主/最终审查 | 794cd14276f741fa | 26767 / 34252 / 61019 | SCHEMA_INVALID，run阻塞 |

总请求**8**，输出accepted **5**、拒绝**3**，网络失败**0**、无响应unknown **0**、无效JSON **1**、自动付费修复请求 **0**。总prompt **117947**、completion **55673**、total **173620** tokens。最终审查completion含reasoning **31968**，已包含在completion内，不再次相加；实际审查max_tokens65536，其它七次48000。金额无可证账单，记 **UNKNOWN**。没有额度错误、充值处理、网络重发或同输入循环整批。

## 保护终检与剩余限制

`final-preservation.json` 核原18项目、279 revisions、727 records、1 settings、1 migrations全部原行hash不变；新增2项目/26 revisions/51 records只归属这两个合成项目，settings/migrations没有新增或更改。SQLite integrity_check=ok、foreign_key_check为空。原325项tracked文件中仅授权app实现改变，另新增明确测试/说明；未知work的25项文件大小与mtime完整一致。全部原CALL副本与rootdata原回执字节一致；终态在途任务0、原始来源/已确认历史/旧证据保持身份。原配置和业务数据未用于补造输入或导出。

有限规则已修紧邻撤回引文误报，仍不能证明模型所有解释事实有依据；本轮MRD未经支持的旧流程通过成文程序，须人工审查。来源引用正确不等于该句推论正确。反馈粒度、入口、字段和配额实现细节仍未知，100扩展字素簇的合成选择尚未经成功修订落到反例文档。无效审查JSON和被拒的分类均不能当作完成审核。可交付边界是**有证据的程序修复、离线回归和第一轮真实失败/部分链路记录**，不是完整真实业务交接或发布批准。到本第一轮终态收尾，不增加功能、时序样例或新付费批次。
