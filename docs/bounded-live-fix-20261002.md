# 最终真实轮的有界修复与复验（2026-10-02）

本轮起点 `0e50e17fd32dd43d8a41102d3f2941a8aa242c6e`，分支 `codex/conversational-intake-20261001`；正式工作台原指纹 `a92911e66804cf96`。仅处理上一真实轮的三项失败，并复验受影响步骤。原8次付费请求、18个历史项目、已确认文档与未知 `work/` 保留；不推送、合并或部署。

## 冻结边界

旧真值 `evidence/real-final-round-20261002-2114/frozen-oracle.json` SHA256仍为 `e66c07631cbe40f95462421187eaf44efce0e9184ae6e775e248266b68d3e537`。本轮状态卡及步骤冻结在 `evidence/bounded-live-fix-20261002-2231/round-freeze.json`，SHA256为 `4feebf6e69e6dac9a740ae4a461efd211bf7312a3db7d1394648ecd544fdada0`。冻结后不改输入、问题期望或答案。

最多六个动作：反例澄清、主场景MRD、主场景审查、反例MRD、反例PRD、反例审查。后续步骤依赖合法候选落实及阶段条件，不重复成功主整理、回答和PRD。客户端重复提交为0；产品已有有限格式修复/截断升级若发生，逐CALL记录。网络结果未知不自动重发。合成测试操作者的核对、选择与确认均不是实际业务PM批准。旧主场景反馈粒度未知仍保留，不补造答案清零。

## 离线复现与实现

新增回归修复前25失败/19符合预期；修复后含补充角色及请求未知边界的定向集67项通过（JUnit `targeted.xml`，0失败/0skip）。这些离线夹具与MockTransport不算真实模型调用。

- `app/semantic_quality.py`：有限的现状操作见证，绑定操作对象、方式、动作、明确角色与肯定既有证据；已有本期决定、来源引用位置、假设/建议不证明旧流程。解释、可见叙述及摘要均核对；存量复核使用文档冻结快照，指向实际文档/块/原句，不连带指控另一份文档。语义版本10；响应schema不变。
- `app/contracts.py`：公开成文验证也检查实际解释及叙述，不仅检查引用元数据；整理/澄清/变更/成文摘要不能反推旧流程。历史讨论附录保持原身份。
- `app/understanding.py`：给生成器提供变化需求与保持约束各有来源的分离示例；分类拒绝规则不放松，程序不将new改成existing。
- `app/core.py`、`app/provider.py`、`app/ingest.py`：模型正文严格JSON解析，重复键不能静默覆盖或作为修复锚点；原始正文先保存。审查示例包含两个对象，增加格式自检；旧多余引号仍拒绝，没有手工修成合法review。既有窄范围括号修复继续完整schema及语义验证，不扩展为宽松解析。
- `app/workflow.py`：未知网络/超时结果保留失败事实，不自动重发收费请求；重复键不追加保真无法证明的格式请求。明确429等既有有限处理保留。
- 仅更正旧报告的审查hash口径，未改原CALL文件：`6099b245…`是完整CALL JSON文件，`681f86a0…`是其`final_output` UTF-8正文。

`offline-fixed-evidence-replay.json`离线复放三份原CALL：主MRD无来源现状断言→SEMANTIC_BLOCKED；反例TMP-1混合change/preserve→SEMANTIC_BLOCKED；原审查多余引号→SCHEMA_INVALID，offset3273/column3274，不能窄格式归一化。原文件及正文hash均记录。复放不调用模型、不改项目。

## 必要全量与正式运行

首次全量退出1，1208通过/5项Office skip/1失败，原JUnit及日志保留。唯一失败是旧`test_no_response_records_absence_without_invented_output`固定要求同一无响应收费请求发送3次，与本轮用户明确禁止未知结果盲重发冲突。按新请求边界同步为1次，进一步核对恰好1份证据；失败状态及无正文断言保留，业务语义、计数真值和导出预期没有修改。

最终命令 `.venv/Scripts/python.exe -X utf8 -m pytest tests -q --junitxml=evidence/bounded-live-fix-20261002-2231/full-tests-final.xml`（输出保存为`full-tests-final.log`），实际退出0：**1209通过/5skip**，1214个case，231.98秒；2个现有依赖弃用警告。定向命令为同一pytest入口加`tests/test_current_workflow_evidence.py tests/test_review_json_integrity.py tests/test_change_preservation_contract.py tests/test_runtime.py -q --junitxml=evidence/bounded-live-fix-20261002-2231/targeted.xml`，67通过。新文件46项及既有runtime新增2项合计48项新增回归。首次失败JUnit为`full-tests-first.xml`。

Office读取/导出实现未改；复用[干净检出实际Office记录](clean-release-boundary-20261002.md)，本轮5项opt-in明确skip，不称本轮又执行Office。没有打开或遗留Office进程。

源码提交 **9e2c1cfce07fb1ffc031d993893cc4f5db195666**，直接父提交 **0e50e17fd32dd43d8a41102d3f2941a8aa242c6e**；此前父链为`0e50e17 → 3f61bb1bc0dee79fccd16f8dab430c279b4c5171 → db5b5e25f7ed49b1810abc87c5d6c62056cd7352`。14个明确文件（7实现、5测试、2说明），逐文件diff可用`git show --stat 9e2c1cf`及`git show 9e2c1cf -- <文件>`复核；未使用`git add -A`。

`restart-guard-and-source-binding.json`逐受控文件保存磁盘/Git对象及CRLF规范化hash；绑定全量测试源码与该提交。重启前所有原数据库表一致、在途任务0、可见搜索/聊天输入长度0、无Stop，旧模型证据与work元数据一致。执行原`scripts/stop.ps1`及`scripts/start.ps1 -NoAuth`均退出0，没有Force、第二工作台或配置改写。正式8765当前runtime_id **2ac887ab1413b844** 等于磁盘app/*.py按既有规则计算的指纹；报告提交不再改源码，指纹不变。

## 唯一受影响真实轮：未完整通过

共执行3个动作，另3个反例后续动作因前置失败为NOT_RUN。没有重做原8次、主整理/回答/PRD，也没有客户端重提。本轮5个实际DeepSeek请求均HTTP200，2个响应通过程序验证、3个响应被拒绝，unknown=0，网络重发0。其中2次是产品已有有限格式修复；第二与第三请求实际输入hash相同，均原样保留，不能算成功修复。金额没有账单证据，记UNKNOWN。

| 实际CALL | RUN/阶段 | 校验结果 | 输入token | 输出token | 总token |
|---|---|---|---:|---:|---:|
| CALL-01e7437a28524036 | RUN-6001a1a8810a4a79 / clarify | REFERENCE_INVALID | 17482 | 4720 | 22202 |
| CALL-8a5b5216c7bb41a9 | 同RUN；修复前CALL | REFERENCE_INVALID | 22543 | 4720 | 27263 |
| CALL-055ac96eba9a4dba | 同RUN；修复前CALL | REFERENCE_INVALID | 22543 | 4033 | 26576 |
| CALL-a902a10585024fb9 | RUN-e026469e774042ea / MRD | accepted；真实生成v2 | 15579 | 2082 | 17661 |
| CALL-cc8903b1120e40c8 | RUN-b9302b94c24a45c5 / review | accepted；合法needs_changes | 28523 | 19321 | 47844 |

合计prompt **106670**、completion **34876**、total **141546**；审查reasoning **17178**已包含在completion中，不重复相加。每CALL原文件和`final_output`正文各有SHA256，见`final-audit.json`；原响应和请求输入都在忽略的本轮证据目录，未提交Git。自动修复不等于生成动作成功，HTTP200不等于业务验收通过。

### 原输入、问题、冻结回答与实际文档

主项目 **P-5ec3a502e2c94cc9** 的原文`SRC-815f482331224e26/EX-ceabdbb4f1c4498c`同时给出同租户/每批最多20/任一项配额不满足整批拒绝不创建，以及“超时视作失败并允许再次提交”。原真实问题 **Q-c629b4080878483d** 已识别明确失败与结果未知冲突。冻结合成回答`SRC-0175352bcf214e76/EX-e6137895895a4df4`选择：明确拒绝时本批不创建、既有资源不变；超时/响应丢失为未知，沿既有申请ID查询，状态确认前不重复提交。本期沿用审计入口不证明旧实现覆盖；租户管理员始终是执行角色，无新增审批人。本輪复用已落实的回答，不重答。

主真实MRD生成成功，新版本 **DOC-de11a734ca904255 v2 / draft19**，content_hash **95bc571359f31636f1f9835ef1024f0cccf12f8a4522ab7cafddc665b2b1b2a5**；原 **DOC-576d3a6badfc4562 v1** 留在原记录/修订历史，没有覆写。原PRD **DOC-34d194e3c4654202 v1 / draft17** 内容及完整artifact不变，content_hash **40790c0ab19dcb7799ab6c0c6dc03f607d97e8c3104dcebe451e996d4fd6489b**。两者brief_hash一致，为`68232817eb843bc0874e83ebb56a620ea4db85429dd4a7d2c9bb79d32cb35863`。

本期已选草稿条目仍是 **REQ-aa8902ae16654968 v2、RULE-ad63943c24664d73 v2、RULE-fd1c4b2df2cb4fa6 v1、AC-753b569f8eae4375 v1、AC-0be9c302b8a44a98 v1**；本轮没有更改任何条目或选择状态，它们尚未正式确认或形成基线。实际MRD/PRD保留整批量词、明确拒绝/未知的条件与时序，审计是本期要求而非既有实现覆盖证明。旧明确现状断言消失，但MRD **MRD-adbe1a74626a.blocks[2]** 又出现“从而减少逐项重复提交的动作量”，仍未标成假设，来源没有该现状/收益证据。

真实审查严格JSON一次接受，无语法归一化、无付费格式修复，review已实际登记（target_hash `257bbf22239a1f89da2cf900f3341b2e03cd384c203a44a41253b5380b296d50`），结论 **needs_changes**。finding只指向 **DOC-de11a734ca904255.MRD-adbe1a74626a.blocks[2]** 的真实原句及上述原来源；明确指出PRD未有同句。独立阅读也定位到该句。其关于“与无量化证据自相矛盾”的理由需人工判断：无量化本身不否定潜在定性收益，确定问题是没有现状/收益来源且未标为推论；不把模型全部理由当事实。优先级没有选定业务排序，依赖顺序解释仍需人工核对其语气和边界。

**Q-a9c484eba79746ae** 反馈粒度仍open，未编造回答、未出范围或采纳模型建议清零。审查required_decisions明确保留该决定，正式确认未执行。

反例项目 **P-df7c5942e7904214** 原文明确100Unicode码点、emoji一码点及超时明确失败；原问题 **Q-285b7d4a7c7744a8/Q-a2378286a6454466** 与反例99A+👍🏽=101码点/100字素簇保留。已冻结答案选择100扩展字素簇，99A+👍🏽允许、100A+👍🏽拒绝；明确失败不写入、未知先查原请求，不断言未变或自动重试。它们仍有原出处且pending，未自动落实成规范。

本轮真实clarify的TMP-1已用只有change的scope_evidence而不是旧change/preserve混合，但响应又要求用target_question_id重提/拆分这两个**已回答**的问题；三次都被`只能明确拆分尚未回答的现有问题；已有回答必须保留`拒绝。不能删除问题、手工抽取局部候选或将失败JSONseed进项目来冒充真实生成成功。整个动作失败；候选、条目、回答和理解状态与原终态完全一致，反例MRD/PRD/review为NOT_RUN。因此没有证明该场景真实“回答→一致文档”链路通过。

## 下载、保护与停止

正式8765在当前MRD v2与原PRD v1各执行MD/DOCX GET，**4次HTTP409 / CLARIFICATION_REQUIRED**，指定document_id均写入请求证据。UI刷新后实际显示“审查要求修改（1条问题）”“已有当前版本审查”、真实MRD位置和待回答反馈问题；页面/截图见`main-ui-final-registered-review.*`。未手工修改文档正文、审查结果或阶段状态。

本轮模型动作由公开API驱动；UI核对实际版本、失败提示、审查和门禁，不是完整纯浏览器点击验收。一次把MRD当作button的定位失败未改变产品状态，随后按实际DOM的“MRD评审稿”tab读取成功；API后台审查完成后刷新页面才核对已登记状态。该观察纠正保存在`ui-observation-corrections.json`，不算产品流程成功或新增模型请求。

baseline_id=null；confirmation、ZIP、manifest/CRC/逐文件hash、正式constraints/requirements机器交接验收均 **NOT_RUN**，不能凭草稿内容hash称完成正式文件交接。没有导出文件或baseline可核验，本轮没有调用无基线的机器交接生成。真实PM、机器消费者与新机/安装器仍未验。

`final-audit.json`独立核对：18个原历史项目current rows不变；本轮开始前全部 **305 revisions、778 records、1 settings、1 migration** 的行hash不变（覆盖原279/727及上一真实轮的历史）。仅两合成项目当前视图按已授权动作追加变化；反例完整items/questions与主items、PRD完整artifact未变。旧证据所有文件hash、work的25文件元数据及所有未授权受控文件hash保持一致。根目录没有提交DB、证据、模型输入、配置或凭据。最终在途任务0，只留下唯一正式8765工作台。

本轮到此停止。源码的有限语义保护和结构检查经离线全量验证；主真实成文与合法审查链路已执行，但审查要求修改，反例真实澄清失败，**不称全面ready或业务交付验收通过**。下一步若另行授权，应针对上述真实未通过点，不自动开始新的同输入批次。

## 可交付边界与限制

现状操作见证是有限规则，只覆盖明确现状词及常见操作方式/动作与局部对象；不保证理解全部同义表达、复杂指代、隐含动机或所有未授权推论。来源本身真实性、优先级、反馈粒度与实现覆盖仍需人工核对。审查提示和JSON模式降低结构错误概率，不能保证模型不再出错；失败输出继续拒绝。

只有合法审查和必要合成决定成立才进入指定版本确认与导出；409不能强放行。机器消费者、新机/安装器、实际业务PM及发布验收仍未验证。旧Office实际证据复用，本轮不重复Office，明确记录5项opt-in skip。
