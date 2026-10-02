# 2026-10-02 计数定义与审查措辞的有界收口

本轮已完成下列固定验证集合及本地源码交付，真实模型结果仍为 `needs_changes`，不是业务批准、真实 PM 决策或发布验收。只修改 pm-req，不改 Skill、UIAgent、网络、凭据和历史文档；未派发其他 Agent，未推送。

## 起点、范围与保护

- 起始 HEAD：`dd4b5d8a511ba430313c34e452717d4653a2ffe3`，分支 `codex/conversational-intake-20261001`。起点没有已跟踪未提交改动，已有未跟踪 `work/` 保留。
- 开始核对磁盘、日常任务及已有请求后推进；本轮专项请求结束、日常活动任务检查返回 0、页面输入为空后，先对 SQLite 做一致性备份，再用原 stop/start 脚本重启唯一正式 `http://127.0.0.1:8765/`。没有第二个 TCP 工作台。
- 终检：原有 34,535 个受保护文件哈希无变化；`projects/revisions/records/settings/migrations` 的行数及完整内容哈希均与起点相同（18/279/727/1/1）。历史来源、确认基线、文档、旧请求与失败证据保留。证据：本轮 `preservation-result.json`、`before.json`、`after.json`。
- 本轮证据统一放在忽略目录 `evidence/bounded-closeout-20261002-1752/`；原始模型返回、数据备份和密钥不入 Git。

## 原输入、错误评语与最小修复

原输入为“备注最多100个Unicode码点（含空格与换行，emoji按一个码点计）”。来源已经定义 Unicode 码点，不能说它没有定义；但“每个 emoji 恒为一个码点”与复合 emoji 有矛盾。

旧 B2 原始复核 `CALL-215298bd13a7468a` 对实际文档边界块 `DOC-512fc7cec6ec4e99.PRD-664e8e9dc8af.blocks[1]` 说计数单位未明确。本轮先离线原样复放：旧程序接受，新程序以 `SEMANTIC_BLOCKED` 拒绝这一错误缺失判断，准确指出该块已存在定义。原返回及项目输入保持相等，复放 0 次付费调用。见 `b2-replay-before.json` 与 `b2-replay-after.json`；旧报告及旧 B2 证据没有事后改写。

改动只涉及两个实现文件、一个新回归文件及本说明：

| 文件 | 改动与目的 |
| --- | --- |
| `app/semantic_quality.py` | 增加定义事实及缺失判断分类；保留来源、对象、人工回答出处和真实段落位置，区分定义存在、内部矛盾和 PM 是否已作一致选择。公开分析/复核校验拒绝把已定义单位报告为缺失。策略版本升为 `semantic-quality-6`，历史审查成为待更新状态，原件不变。 |
| `app/provider.py` | 给分析、成文和复核上下文提供 `counting_definition_facts`，明确它是只读输入证据，不允许复制成输出诊断字段。 |
| `tests/test_counting_definition_classification.py` | 新增 33 项确定性正反用例，经公开 `validate_response`、`assemble`、`compile_plan` 校验。 |
| 本说明 | 记录复现、验证范围、真实请求、来源到文档的链路与限制。 |

分类基于单位、计数语境、否定及缺失谓词，未屏蔽旧模型的固定词句。按实际文档块判断局部定义；父条款已定义，不会掩盖另一边界句确实遗漏单位的局部清晰度问题。不同对象的定义不会填补另一个对象的缺口。已有计数反例、联合绑定、成文和导出门保持生效，未放宽 Schema、删除业务内容或修改旧测试预期。

| 证据情况 | 应有判断 |
| --- | --- |
| 只写“最多100字符”，未给计数单位 | 可追问缺失定义。 |
| 明确 Unicode 码点且规则一致 | 不得报告计数定义缺失。 |
| 明确扩展字素簇且规则一致 | 不得报告计数定义缺失。 |
| 明确码点，又说每个 emoji 恒为一码点 | 定义存在但有矛盾；提供反例，请 PM 选择。 |
| 人工回答有出处，但继续重复矛盾 | 来源可追溯仍不表示一致，成文继续拦截。 |

“计数选择未决”与“计数定义缺失”不同：旧定义虽存在，修订时选哪种一致规则仍可由 PM 决定。

## 原输入 → 问题 → 合成 PM 回答 → 一致文档

在隔离合成副本中保存原来源，以同一问题联合绑定 `REQ-0001`、`SRC-0001/EX-0001` 及计数冲突。问题明确说明已定义码点，要求选择码点分别计数或扩展字素簇；反例为 **99 个 A 加 👍🏽：101 个 Unicode 码点、100 个扩展字素簇**。上限为 100 时两种口径分别拒绝、允许，程序不代选。

| 标注为合成的 PM 回答 | 校验及文档结果 |
| --- | --- |
| 保留 Unicode 码点，各组成码点分别计数；反例为101，拒绝保存 | 同步合成需求、行为、验收及范围，MRD/PRD 阅读稿、Markdown、Word 均保留一致规则和反例，不再包含旧矛盾句。 |
| 改为扩展字素簇；反例为100，允许，101字素簇拒绝 | 同样得到一致 MRD/PRD 阅读稿、Markdown、Word。 |
| 有回答出处，仍原样重复“码点且emoji一个码点” | `SEMANTIC_BLOCKED`，不输出可交付文档。 |

证据为 `definition-to-document.json`、`codepoints-MRD/PRD.md/docx`、`graphemes-MRD/PRD.md/docx`。原来源未改，只在合成副本添加标注的回答出处；编译与导出不修改输入。此链路调用已有公开校验、编译及导出函数，0 次付费；不是实际 PM 选择，也不是业务系统长度计算验收或重新执行一整批模型成文。

## 确定性验证及证据复用

- 初次成对复现：28 项中 18 失败、10 通过；其中 3 项暴露新增来源事实上下文缺失，其余暴露公开边界对错误缺失评语的接受。原失败日志保留在 `reproduce.log`。
- 最小修复后的相关集合：344 通过、2 个依赖弃用警告，34.87 秒（`targeted.log`）。
- 补 5 项问题句/否定句回归后，一次测试 fixture 因旧 ingest 示例没有 question 而出现 3 个 IndexError；改用已有合成 question helper，未修改断言预期。失败保留在 `targeted-final.log`。
- 最终新增用例：33 通过、2 个依赖弃用警告，3.94 秒（`new-final.log`）。
- **代码最终状态的全量**：1098 通过、5 跳过、2 个依赖弃用警告，223.47 秒（`full.log`、`full.xml`）。JUnit 总数1103，失败0、错误0、跳过5。
- 5 项跳过均为明确开关控制的本机 Office 用例，本次按授权复用 `full-retest-20261002-0821` 已实际执行的合成 Word/Excel/PowerPoint、相对路径及 Excel 回退预览定位证据。不能说这5项在本次重新执行。
- 旧1070项全量和 B2 十场景原始请求、失败及自动修复链保留复用，不重复整批付费测试。195个最终源码/回归/脚本/前端/依赖文件哈希冻结；与旧194文件清单相比仅两个实现文件变化，另加新回归文件。Office/解析/前端源码未变；正式 `web/dist` 与已有构建证据字节相同，无需重建前端。
- 解释文字不得泛化明确失败条件、把超时/未知结果归为原记录不变，或把刷新建议升级为培训义务，直接复用 `test_semantic_quality_regressions.py`、`test_semantic_quality_boundaries.py`、`test_continuous_quality_regressions.py` 中现有成对覆盖，并纳入本次全量。

核心执行命令（仓库根目录，证据脚本仅保存本轮隔离验证）：

```powershell
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/replay-b2.py before
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/replay-b2.py after
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_counting_definition_classification.py -q
$env:RA_TEST_LOCAL_OFFICE='0'
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/bounded-closeout-20261002-1752/full.xml
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/definition-to-document.py
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/audit-real.py
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/verify-evidence.py
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/verify-final-runtime.py
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/check-formal-export-gates.py
.\.venv\Scripts\python.exe -X utf8 evidence/bounded-closeout-20261002-1752/preserve.py after
```

`before` 是修复前实际执行结果，列出用于追溯，不表示现在重跑可以得到旧行为。带真实模型的脚本不放入自动重跑命令，避免误付费。

## 一组针对性真实模型核验

离线拦截旧评语后，实际执行一次隔离副本的公开 review 动作，使用现有模型配置与授权范围；不在日常数据中覆盖审查或文档。

- DeepSeek `deepseek-flash`；`RUN-d33bb63146c440e6` / `CALL-0eb1dfbba5fb4923`；1 组、1 次请求，0 次自动修复、0 次失败请求、0 次手工重试。第二组 `NOT_RUN_not_needed`，不再补调用。
- 请求 2026-10-02 18:32:36 至18:33:32（北京时间），实际55.422秒；HTTP200、响应校验接受。输入33803、输出13721、合计47524 tokens，币值费用未知。
- `needs_changes`、任务 partial：模型保留真实计数冲突、99A加👍🏽的101/100反例及 PM 选择；没有再把旧边界块已写的单位说成缺失。MRD培训义务、PRD失败范围及审计实现断言等实际风险仍保留，未自动选择口径。
- 隔离副本的来源、条款及基线未变。见 `real-audit.json`、`real-group-1.log` 和 `real-group-1/` 原始请求/响应、usage与动作证据。HTTP200不代表内容可批准。

## 正式运行、页面与导出门

安全重启前 fingerprint 为 `3d146f34b1ad441a`；重启后正式8765的 runtime_id 为 **`ee52293d62581f3e`**，等于最终磁盘 app 指纹。18个日常项目可读，SQLite integrity_check 为 ok、foreign_key_check 空；未创建新日常导出或基线。

- 联系人 PRD 的 Markdown、Word 实际HTTP请求均409，理由包含计数矛盾、失败条件泛化及缺少对应审计实现证据。
- 电价 PRD 的 Markdown、Word 实际HTTP请求均409，仍定位实际角色问题；电价 MRD 的 GET准备状态为 ready。
- 正式浏览器：首屏聊天正常加载；联系人和电价 PRD 无下载入口；电价 MRD 显示 DOCX/MD/ZIP 下载入口。历史审查明确显示需更新。核对后恢复原聊天项目，未发送、编辑、确认、生成或点击正向下载。见 `ui-*-dom.txt` 和已目视核对的截图。
- 本次不重新执行正式工作台的正向200下载，避免新增日常导出记录；正向下载复用旧 B2 证据，新增合成规则的实际 Markdown/Word 编译导出在隔离目录执行。页面下载入口、服务端ready与实际文件导出是分别记录的验证范围。
- UI“澄清0项”反映已保存的问题状态，不代表所有语义风险已消除；服务端导出门继续拦截真实风险。本轮不扩展为新的UI改版。

证据：`runtime-before/after.json`、`runtime-readiness-final.json`、`formal-export-gates.json`、`verification.json`、SQLite一致性备份。提交与上述195文件清单、运行fingerprint的对应关系见本轮 `commit-receipt.json`；文档本身纳入同一提交，不在提交后改写为自引用SHA。

## 归档与实际限制

本轮四个交付文件逐文件存放原版/新版及哈希，补完整暂存补丁，归档在 `file-archive/`；新测试和本报告标为新增，历史源码原版对应起点提交。Git存在换行规范化，不能复原原磁盘换行字节的文件使用起点Git blob，并分别记录Git原版哈希和旧磁盘哈希，不声称字节相同。Git只提交上述四文件，已有 `work/` 不纳入。

有限规则识别已列举的单位、缺失/否定谓词及已绑定的来源/位置，并复用现有矛盾反例；它不是完整自然语言蕴含证明。未绑定的总结评语、新单位、跨多个对象的复杂句、视觉内容或未读取来源仍需人工审查；模型自由评语中的同义转述也不能当成精确原文引用。真实模型一次正确返回不保证未来每句措辞正确。

历史确认身份不变，也不因本轮修复成为当前内容质量批准。真实 PM 仍须选择一致口径并同步需求、行为、验收和文档；合成答案不能替代该决定。发布前仍需从干净检出复核版本/依赖、发布环境、企业真实资料及人工业务验收；本轮未推送、打发布标签或执行这些发布验证。达到固定收口集合后停止，未新增无尽优化或整批付费重测。
