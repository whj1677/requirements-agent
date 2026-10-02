# 生成上下文分层离线修复 — 2026-10-03

本轮只修改生成输入组装和可信提示契约；真实模型请求 **0**、日常公开业务写入 **0**。离线定向 207 项通过，全量 1411 项通过 / 5 项 Office opt-in skip，均 exit 0。原响应仍拒绝，未以改 ID、改正文、删除候选或放松校验取得通过。真实生成质量、人工业务验收及交付链尚未通过本轮验证。

## 范围、起点及停止条件

- 分支 `codex/conversational-intake-20261001`；起点 `27cc28802a0153d3024e1cd94d23e33de4790313`，父 `31bfcfb8b000b981cfb21018fc7c9d8bdd7dcbf6`；起点应用/服务 `60455f5fbffededd`。
- 唯一输入为已冻结纯合成项目和 CALL-e53bae5e8fb04d66。主 P-f73f8130d36040af v3 七 candidate、开放 Q-1e44e50ac69c4bb0；反例 P-f31d1a6267f041bc v2 保持原样。
- 本轮交付：原请求归因、最小输入分层、正反及 Mock 回归、必要全量、精确本地 Git、正式 8765 绑定。结束即停，由父独立复核后另行决定真实请求。
- 不回答/采纳、不生成 MRD/PRD、不登记 review、不确认/建基线/导出。反例真实流程、所有上述后续步骤均 `NOT_RUN`。不改 Skill/UIAgent、业务规则、配置、密钥、网络、Office 真值；不 push/merge/deploy。

## 原请求的确定性归因

原实现 `app/workflow.py:160-168` 经 `assemble` 构建消息并计算输入 digest。以原完整项目快照和原 RUN.provider 配置重建，完整 messages 与 CALL.request_input 解码结果逐项相等；digest 同为 `a5a3dfd12d047d8330b8a314f2801745d64111bd5e14ff1d485ce806e6ba6c26`。`original-request-rebuilt.json` 保留完整重建，`original-code-attribution.json` 保留起点 Git 对象中的构造代码及行号。

原 `app/provider.py` 的 system 为 KIT 的 `00_system.md`、ingest 阶段提示及可信任务头；user 的原字段逐一收录在 `original-attribution.json`，包括 items、questions、options、documents、product_context、recent_messages、excerpts、source_status、counting/semantic 检查及 result_condition_facts。对这份请求核对得到：

| 原请求路径（user text JSON 内） | 实际暴露 | 身份问题 |
| --- | --- | --- |
| `$.items[0].behavior.result` | 全部项配额满足则整批创建；任一不足则整批拒绝 | 原条目仍 candidate / reported，不是独立事实 |
| `$.recent_messages[0].response.proposals[0].behavior.result` | 重复上述候选断言 | 原默认 `p.messages[-8:]` 带回旧 assistant 全响应 |
| `$.recent_messages[0].response.result.understanding` | 再重复充分成功表达，并把已提供的合成决定称待决冲突 | 旧 accepted/partial 解释不能代替当前材料决定 |
| `$.result_condition_facts.facts[13].quote` | 全部项配额满足则整批创建 | `projection` 将 10 条 model 谓词与 43 条 source_statement 谓词放在同一 facts 数组，缺采纳身份分层 |

新被拒 CALL 的 final_output 没有被存入 p.messages，也没有被带进这次原请求；重复来自先前 accepted/partial 输出。原七候选和异常字段确实保留服务拒绝及未知结果，不能声称它们全部丢失。观察是充分成功断言与拒绝/未知例外不统一。

来源原文和 source_refs 已有，但原 source_status 没有 created；原上下文没有单列的真实 revise 目标/证据角色索引。历史 reference 与当前明确合成决定 goal 需要依原文和身份核对效力，开放问题的存储状态不能自动证明材料无决定。上述是可复算的输入缺口与重复暴露，**不能证明模型内部为何选择某句**，不能保证提示改动必然改善真实输出。

QTMP 检查没有发现 schema 与旧静态提示互相矛盾：两者均规定 `^QTMP-[A-Za-z0-9_-]+$`，结构示例也是 QTMP。原生成的 `TMP-Q-timeout` 是违约；原七 add/null-target 也没有按既有条目提出 revise。本轮不做同名暗映射或响应清洗。

## 最小实现与兼容

1. 新增内部 `app/generation_context.py`，生成输入版本 `generation-evidence-1`，用于 ingest/clarify/change。可信任务头列 schema 派生的 Proposal/Question ID pattern、来源效力、必要/充分及修订契约。QTMP 包括 decision_points 子项；已有稳定 ID 原样引用，同 kind revise，不能以同标题推断身份。
2. `generation_evidence` 提供来源原 ID、hash、purpose、created、存储顺序、排除/替代/父来源及实际送入 excerpt IDs，列七条实际 revision_targets 和已存在的有效 saved_decision_ids。未采纳 product_context_proposal 原文以 unadopted_proposal 保留。
3. 生成输入的 `result_condition_facts.facts` 只保留原 source_statement 谓词；候选谓词完整放到 `clause_assertions`，保留位置、对象、原句及原来源，补 selection_status/epistemic_status/evidence_role。selected_clause 也不是旧实现证明；此分层不改变共享 projection 或语义验证器。
4. ingest 历史用户消息原样保留；历史 assistant 保留事件 ID/角色/阶段/时间/run 身份，不重复发送整份摘要及结构化结果。完整旧记录、当前候选与问题均保留，不删除模型证据或事后改业务状态。clarify 原有历史裁剪逻辑不变。

只增加内部输入字段，`response_boundary` 将 generation_evidence 标为 input-only。公共响应 schema、持久化 schema、旧记录读取、引用/版本/人工核对与采纳、原子请求 cap、默认无限行为、格式修复和未知网络保护不变。旧消费者继续读取原响应；无数据迁移，也不会将原材料声称的选择写成正式确认。

时间或 purpose 本身不证明采纳。只有原文明确本期选择、撤回或替代才核对演变，来源自身仍有内部矛盾/效力未知则保留同一绑定问题；不会假造保存回答或 answer_refs。配额满足不能由失败的逆命题升级成充分成功；来源确有充分条件时保留实际作用域。明确拒绝、超时/丢失未知、原申请 ID 查询及确认前不重复，必须在摘要/理解与条目中一致。本期审计要求不能升级为既有实现覆盖，需求确认 PM 不是云资源执行审批人。

## 固定证据与回归

`new-input-offline-replay.json` 保存新旧完整输入与严格拒绝结果。新离线输入 digest `2751968fc0133fa9ab0c55abe85d71c8e775b35a3372632e16375d11dd9bdadd`。所有 items/questions/excerpts 原样相等，所有来源和七个 revise 目标完整，43 来源谓词逐项原样相等，10 模型谓词移除新增角色字段后与原谓词逐项相等；没有省略材料。

- 历史 SRC-91cf252efd44431e/reference，created `2026-10-02T16:16:25.505891+00:00`，SHA `1ef8d3e61e501fd4f0d08e6a6e3dec30ad918c4da90dabe30b6bbc243cf80d90`：历史超时失败/可再次提交原文保留。
- 当前 SRC-d456da0255274954/goal，created `2026-10-02T16:16:25.543543+00:00`，SHA `40fb3b96c94d083c8c9b4f21d2931438b5603f33d249ff49fdb156c5fa7d18cb`：合成操作者明确决定原文保留。来源角色不冒充系统已保存回答或真实 PM 批准。
- 冻结真值 SHA `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 不变。全部同租户/20项/任一不足整批拒绝、逐失败项反馈和租户保密、拒绝与未知查询/不重复、审计边界及无新增审批/排序/分页决定保留在原 excerpts，旧候选不改。
- 原 CALL 文件 SHA `d8bb419f976645358b5693749beb345fa59082f37f2ff58265edcaf166cb22d3`；final_output UTF8 SHA `2f1d3810cc0be6c3e6f553a463554ea6297452e97a5651a6b94951ae9da8113a`。stage schema 和完整 validate_response 均仍 `SCHEMA_INVALID`；直接条件诊断仍 `SEMANTIC_BLOCKED`，位置 proposals[0].behavior.result，model_repair_allowed=false。这是独立离线诊断，原实际执行并未越过 schema 拒绝。

新增 `tests/test_generation_evidence_context.py` 为纯合成 17 项参数化测试，包含来源/时间/当前决定完整、候选与已选/暂缓/拒绝身份、原历史去重复但原项目不变、真实同 kind revise ID/版本、QTMP 合法/错前缀/空格、原格式及语义失败仍拒绝、明确来源充分条件与作用域保留、三生成阶段的输入边界，以及真实 Workflow + MockTransport 的 cap=1 合法修订/无效输出原状态不变。Mock 请求不是 DeepSeek 请求。

复用既有条件、两误报、决策工作流、ingest 可靠性、review/clarify 输入和请求 cap 回归，覆盖正常成功分支可放行、必要→充分误推仍拦、拒绝/未知不能推断结果、普通定性目标不误报、已答/失效答案及严格人工采纳等边界。

实际执行：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_generation_evidence_context.py tests/test_result_conditions.py tests/test_result_condition_false_positives.py tests/test_decision_workflow_boundaries.py tests/test_ingest_reliability.py tests/test_request_cap.py tests/test_review_context.py tests/test_clarify_answer_prompt.py -q --junitxml=evidence/offline-generation-context-20261003-0345/targeted-final-v2.xml
.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/offline-generation-context-20261003-0345/full-final.xml
```

上述由 create-only run_tests.py 用相同解释器执行并保存完整 argv/log/exit/JUnit/源码及测试逐文件 hash。定向 207 通过 / exit 0；全量 1411 通过 / 5 skip / exit 0。Office 五项本轮未执行，不把历史实测充作本轮测试。依赖未变，Python 与已安装版本列 dependencies.json；现有弃用 warning 未改环境消除。

初次旧代码复现 8 failed / 5 passed 保留。实现后第一轮 5 failed / 136 passed 为新测试错误地将整段分号正文与既有分句谓词比较；改为同时核完整 items 字段及逐谓词原句，业务预期未改。随后新增 Mock 两项首先失败（205 passed / 2 failed），因为与 Store 保存前的 fixture 比较遗漏正常写入的 source_namespace；改为与实际已保存快照比较。初始 JUnit、所有 exit1 和诊断都保留在 initial-failure-receipts.json 等原文件；未覆写失败日志。最终通过不抹去初始失败。

## 保护、运行绑定及剩余边界

本轮证据独立目录 `evidence/offline-generation-context-20261003-0345/`，create-only/open-x。本地逐行 hash 检查：22 项目 / 315 revisions / 800 records / settings1 / migrations1 全部行不变；190 CALL（189 旧 CALL + 最新失败）逐文件不变。两项目、原问题、七候选、来源、旧 evidence、work、dist、配置/凭据 hash 和协调回执均与本轮前置快照相等；hash 检查不输出密钥，也不上传 DB。保护通过不代表业务通过。

必要重启前 GUI 输入为空且发送禁用、无 Stop、全部 TASK/RUN 无 queued/running，已有日志归档及一致 SQLite 本地备份保存，原 web/dist 已完整备份到 before-restart-dist.zip 并记录 hash。本轮仅原 stop.ps1（无 Force）/start.ps1 -NoAuth 原模式，唯一 8765 根 app/data/dist；未替换 dist、未开第二服务。重载后首张 DOM 是列表加载中的中间快照（显示0而非数据库计数），随后 ui-after-restart-settled.txt 保留已加载页面；原中间快照不覆盖。

最终 app 磁盘 SHA `28a0a64060190f806406709b855354a601a00db69226ea9532050e617746ad74`；正式 runtime `28a0a64060190f80` 与此前缀一致，JUnit 同源码 SHA。最终提交 SHA/父链、精确 git show/stat、四文件 Git 对象与工作区规范化一致、完整新增测试源码及剩余 dirty 在 final-binding.json 和对应 diff/stat；报告在提交前记录测试/源指纹，避免自引用提交 hash。只归档 app/generation_context.py、app/provider.py、新测试及本报告；原 unknown work 保留，不提交原始模型/DB/配置/巨大产物。

跨阶段台账仍 **2/8、已知45651 tokens**，本轮增加0，金额无账单 UNKNOWN。没有付费修复、重试、整理、诊断模型或真实业务写入。父须独立复核新输入；后续如获准仅再验受影响生成，不据此称全面 ready。机器消费者、新机、安装器、真实 PM 与发布仍未验。

历史协调回执事故单列：request-cap-independent-public-contract-20261002.json 旧索引 SHA `df31c785fcf178eefdc2fb81ed2c937cdad89e615f6322ba166e709e9b141cb7`，当前 SHA `c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9`；曾被更新 observed_utc，恢复旧 hash 曾被自动审批拒绝（可能破坏证据链）。本轮仅只读核现状及追加更正链，不恢复/换路由。原独立审计 v1 的 SQLite 内部载荷与公开投影混比误报及 exit1 保留，v2 校正也保留；不得冒称数据损坏或所有历史回执从未变化。
