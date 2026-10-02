# 有限否定作用域与来源忠实修复（2026-10-03）

本轮仅离线实现、回归和独立来源诊断，真实模型请求0、日常项目业务写入0。原CALL正文未改：否定误报消失，schema、答案绑定、完整条件校验在离线复放中 accepted；历史注册结果仍是 SEMANTIC_BLOCKED。原四项内容问题仍需人工来源核对，不能称原响应可采纳、真实生成质量已通过或全面 ready。

## 基准与权限

开始HEAD `e17ec3345203b65a241d9ae4275da851319253e7`，父 `bf2e11451437c9aad3a3cdb4f457992cf6e8bac6`，分支 `codex/conversational-intake-20261001`，dirty仅原work/。旧产品实现为4ce112ba，app全SHA256 `28a0a64060190f806406709b855354a601a00db69226ea9532050e617746ad74`，原8765 runtime `28a0a64060190f80`，预检无在途TASK/RUN。

新证据目录 `evidence/offline-assertion-scope-20261003-0524/`，create-only；完整旧报告、content-audit、negation-minimal-facts、main-after、冻结源和CALL均已读取并记录hash。未派Agent、开启观察器、修改其他项目/Skill/UIAgent/组件、网络或模型配置。模型批次维持4/8、107331已知tokens，剩余4次不是本轮授权，金额未知。

不可变基准为上一轮 `CALL-94cf90efe9534bbe.json`：全文件SHA256 `59f8cc55df99405490b5fc7796e2b69eee1175bb994210a17f86525a6793fcef`；final_output UTF8正文SHA256 `e48b4b7a4280ed01b9491a41f6fb2b800b6783d35342d67537f47d5af75cf9b9`；actual_input digest `142c9047d90f391ca781a9d002ed10bc4327f436f31cc6f04d8e7b35e05311e2`。没有清洗、补字段或回填旧RUN。

## 最小实现

`app/result_conditions.py`按局部断言谓词建立有限作用域：断言、推断、认定、确认、反推、展示；否定仅支配该谓词后同分句的并列结果，逗号及已支持转折结束作用域。否定数量为偶数时不能当禁止断言。前提中的“不满足”不叠加到“不能从前提反推结论”的外层否定。相邻的“展示或断言”共享外层否定，不把任意其它分句并入。

投影新增内部角色 `inference_constraint`，保留对象、位置、来源、原句与谓词范围偏移；不据此改写业务规范。明确拒绝分句不继承前一分句的未知条件；未知分句自身仍不保证任何结果。未来新申请禁止操作继续与原申请历史结果断言区分。

完整原响应还暴露两项相关角色误读：已保存回答被当业务写入；纠正引用中的旧充分保证被当当前断言。现在有限地区分“保存回答/答案”的工作流对象，并把明确纠正的引用限定到原闭引号；引号后的肯定结果仍受校验。没有把整个句子或任意否定词列入放行名单。

`app/generation_context.py`仅增加来源忠实约束与当前有效回答的局部结果任务卡：反馈对象≠排他可见权限，配额≠预算，字段存在≠配额满足，无旧流程来源的比较必须标假设/建议。缺量化本身不否定定性目标；有明确来源的合法对应内容按原文保留。

任务卡引用真实target_item_id、同kind、原question_ids及既有谓词位置，不根据标题映射，不造回答/采纳身份。来源时序、原excerpts、当前回答和未采纳候选均完整保留。新增字段只在既有内部输入投影/任务头中；response schema、数据库行、导出契约、临时ID规则、引用/版本/人工审批与request cap未改变。没有实施此前泛化question schema设计。

## 原字节离线复放

`replay_original.py`使用原CALL request中的实际excerpts与原main-after快照，并禁止Provider网络；原对象与正文hash前后不变。

| 阶段 | 实际检查结果 |
| --- | --- |
| before | schema、答案绑定accepted_offline_only；完整/条件校验SEMANTIC_BLOCKED，原“不断言创建、未创建或既有资源未变”误报 |
| after / after-final / after-coordination / after-scoped-refusal | 中间完整失败分别暴露禁止反推、并列禁止展示/断言、摘要中的局部拒绝与保存回答角色；各文件原样保留 |
| after-projection | schema、答案绑定、完整validate_response与条件校验均accepted_offline_only；不改历史SEMANTIC_BLOCKED |

原响应的条件误报消失不代表四项来源问题消失，也没有向日常项目登记任何新候选。

固定三句及相关边界经过完整validate_response、行为核对/人工采纳门测试，而非只看regex返回：

- 合法：“结果未知，不断言创建、未创建或既有资源未变。”保留为禁止推断；未来“禁止再次创建新的申请”亦合法。
- 阻塞：“结果未知，原批已创建。”和“结果未知，原批未创建且既有资源不变。”
- 阻塞双否定：“结果未知，不能不断言原批未创建。”；禁止断言后另分句断言已创建/未创建/既有不变仍阻塞，禁止新申请不能证明原申请结果。
- 明确本对象正常成功分支、真实来源充分条件及其作用域仍可通过；无来源“全部配额满足就保证创建”、其它对象/否定成功分支、明确拒绝却创建仍失败。
- 未核行为不能自动采纳；合法Mock回答落实只产生candidate与candidate_ready，不产生正式回答应用或业务批准；错误输出一次Mock请求即停止，无付费格式修复。

## 四项实际内容诊断

`four-fidelity-diagnostics.json`逐项保存原位置、完整原句、引用的实际source/excerpt及source hash、维度和合法对应例。程序没有新增通用语义蕴含引擎；下表四项是**独立来源审阅待核**，不是已实施自动硬拦截，也不能凭存在引用判定有依据。

| 原位置/原句 | 真实来源与维度 | 合法对应边界 |
| --- | --- | --- |
| $.proposals[2].behavior.permissions：“仅本租户管理员可见反馈，不泄露其他租户信息。” | 当前goal EX-bef928a4d1714f21仅规定向管理员反馈、不泄露其它租户；既有权限保持，没有排除同租户其它角色可见的依据。维度：排他权限扩大 | 来源真正规定只有该角色可见时可用，不能从反馈对象推排他权限 |
| $.proposals[3].title：“预算不足/拒绝之外的提交结果验收” | 当前goal、保存回答及动作来源都只有资源配额；Provider请求预算不是业务预算。维度：对象/概念替换 | 真实预算场景有来源时允许，不把所有预算词判错 |
| $.proposals[3].statement：“各项配额均不缺失” | 原规则与纠错要求涉及配额满足，存在性不是满足；不能称精确设置了“配额全满足且服务拒绝”的原D1反例。维度：验收前提 | 真正字段存在性校验允许，不能替代容量条件 |
| $.product_context_proposal.value：“支持一次提交多项云资源申请，减少逐项提交操作；具体效率收益未量化。” | 原材料只说明本期批量目标，没有旧逐项流程。维度：比较前提来源；未量化本身不是错误 | 明确旧逐项现状有来源或显式假设/建议合法；普通定性价值合法 |

上述四项分别有固定正反来源例，测试刻意保留“现有schema/条件校验未覆盖这类通用来源蕴含”的边界，不谎称已自动阻止。生成忠实约束通过Mock实际输入与原身份保留验证，但**没有新的真实模型输出证明生成质量改善**。

## 本轮测试

最终源SHA256 `3eba9d79c6f5bd6bac47f79e468ddbf07a36bb1b5e4fd787af2ed223f5c27cf7`。最终定向及根全量均绑定此源码，新增两份测试包含100个实际JUnit用例；不按历史数字推算新增数量。

| 本轮运行 | 结果与退出 |
| --- | --- |
| targeted-initial（早期集合） | 171通过，exit0，不代替最终集合 |
| targeted-final / targeted-v2 / targeted-v3 | 分别211通过/4失败、213/2、214/1，均exit1；原log/JUnit保留 |
| targeted-v4 | **215通过、0skip、0失败，exit0** |
| full（根tests） | **1515通过、5skip、0失败，exit0**，282.88秒 |

中间失败涉及遗漏的冒号后保存回答作用域、隔离Mock未按真实第一步核对与局部question入口启动，以及测试错误要求登记有效candidate后问题仍pending。后者按既有契约校正为candidate_ready，同时严格核原ID、原回答/refs、旧条款、未应用/未采纳；没有修改业务真值、源码门禁或删除失败用例。

准确命令由 `*-command.json` 保存。根命令为：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests -q -p offline_provider_guard --junitxml=evidence/offline-assertion-scope-20261003-0524/full.xml
```

本轮进程PYTHONPATH增加证据目录以加载只作用于测试进程的网络guard；guard禁止外部Provider连接及日常8765，最终记录连接尝试为空。对应包装命令：

```powershell
.venv\Scripts\python.exe -X utf8 evidence/offline-assertion-scope-20261003-0524/run_tests.py targeted-v4
.venv\Scripts\python.exe -X utf8 evidence/offline-assertion-scope-20261003-0524/run_tests.py full
```

隔离Store/absent.env/伪测试key/MockTransport未使用日常数据或真实用量。实际依赖版本见dependency-versions.json。两项已有弃用警告保留。5skip是既有Word/Excel/PPT/相对目录Office与Excel预览后备opt-in，本轮未开Office，不能冒用历史Office实测替代本轮结果。

## 正式运行与保护

无在途、当前主界面输入为空且无Stop后，先作本地一致SQLite备份、源材料、配置及dist备份，再用原stop.ps1和start.ps1 -NoAuth保持既有入口模式；不改dist或配置。初次备份误假设可选data/.credentials目录存在，失败与部分备份保留；独立v2备份成功，没有停服前丢失原件。

stop脚本exit0。服务启动日志确认唯一8765、根data及新版runtime **3eba9d79c6f5bd6b**，对应上述已测源码。另起只读API核对进程被自动审批拒绝，仅返回blocked by policy；没有换路由重试该API动作。运行关系证据来自已有服务启动日志及本地保护核对，**不是成功GET API回执**。

启动脚本进程已自然退出，但外层subprocess包装未收集完成（原因未证，不猜）。只在核实本轮专属命令、父子身份和不是正式服务PID后，清理该包装进程；原包装exit1属于这次清理，不能当启动服务失败或成功退出码。未重复启动，正式服务保留。日志、清理回执和退出未收集限制均保留。

前后逐行hash要求22项目/319revisions/807records/settings1/migrations1全不变；主P-f73f8130d36040af仍v7、七candidate、原Q answered/pending；对照v2和所有其他项目不变。192旧CALL、全部旧source、旧evidence/work/dist/配置与协调更正链均逐hash核对。允许的变动只限本轮两模块、两测试、此说明及重启PID/服务日志。最终核对、精确Git SHA/父链/stat/diff、完整新增测试源码与运行关系见本轮closeout绑定，不将数据保护当业务通过。

协调request-cap回执既有df31→c8失配及append correction、拒绝恢复历史保持只读，本轮不恢复；早期投影误报的实际exit0/false flags与v2更正链保持。没有将工具错误说成业务数据损坏。

仅五个明确源码/测试/docs文件本地Git，原work/保持；不add-A、不提交数据库、原CALL、秘密、备份或巨大证据，不push/merge/deploy。

## 停止与局限

有限语法只支持上述谓词、局部否定/已支持连接、并列结果与显式分支，不能涵盖全部中文、省略指代、嵌套时序或来源蕴含。新的范围措辞仍要人工核对，提示约束也不能保证模型遵循。双否定未被放宽为禁止推断；真未知结果肯定断言仍阻塞。

本轮0真实请求，原主批4/8及107331tokens不变；原CALL registered SEMANTIC_BLOCKED不变。未自动采纳、回答应用、MRD/PRD、正式审查、确认、基线或导出，反例项目未动。真实模型复验、真实PM、新机/安装器、机器消费者及发布均NOT_RUN。完成离线收口即停，由父决定下一次唯一真实clarify，不自行花剩余额度。
