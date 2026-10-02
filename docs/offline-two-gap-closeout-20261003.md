# 两项离线缺口收尾（2026-10-03）

范围仅为分号后的未知结果条件，以及有效回答落实后的重复结果任务卡。起点 `bd067bd10f44b2e3cae2a36f366ce939ac450017`，分支 `codex/conversational-intake-20261001`。本轮0真实模型请求、0日常项目业务写入、0服务API读取/重启；主批仍4/8、107331已知tokens，金额UNKNOWN。没有派发Agent或启动观察器。

新证据目录：`evidence/offline-two-gap-closeout-20261003-060911/`。原协调固定用例、公开采纳trace、CALL/响应、历史失败和更正回执只读；本轮输出用独立新名/create-only。本报告不修改旧确认文档或业务规则，不是业务批准。

## 最小修复与真实复现

| 缺口 | 修复前证据 | 修复后证据 |
| --- | --- | --- |
| “结果未知，不断言创建；本批已创建资源” | `before-replay.json`：完整原CALL结构的clarify校验accepted_offline_only；固定15例14/15符合预期 | `after-replay-final.json`：SEMANTIC_BLOCKED，定位proposals[0].behavior.result后半句“本批已创建资源”，原因unknown_result_asserted；固定15例15/15 |
| 有效answered/applied回答仍生成2卡 | 同一原公开采纳trace的两条应用answer_hash/clause_hash有效，focus无待落实目标，但local_result_tasks有2张 | 同一trace重放0张；原trace及输入对象不变，hash保留 |

`app/result_conditions.py`原来按分号拆句，否定本来已局部结束，但同对象未知条件也一并丢失。现在在同一字段、句号/换行前保留同对象的未知原句上下文；分号仍终止断言谓词的否定作用域。结果断言与未知依据各保留实际原句、位置、对象及source_refs。不继承其它对象或显式成功/拒绝分支的条件；同对象查询明确结果可结束未知上下文，其它对象查询不能代替。复用既有确认结果识别，不新增业务成功规则。

`app/generation_context.py`只为当前有效回答的“原直接目标集合－现有applied_targets有效集合”生成卡。复用answer_targets及answer/clause hash合法性，不按applied标签一刀切。有效落实、部分落实、改答、条款变化、新直接目标、失效问题均保持现有工作流；旧无hash记录继续按现有helper兼容读取，不迁移/回填旧行。公共响应schema、ID/版本/引用、严格人工采纳、请求cap与语义错误不付费修复门均未改。

新增 `tests/test_semicolon_and_applied_cards.py` 共39项。固定15例逐字逐期望与协调文件相等；测试经过完整validate_response及行为核对门。另覆盖分号后双否定、原结果与未来新申请、未知后的明确拒绝/成功限定、否定/其它对象成功、查询确认作用域；公开隔离保存回答→Mock候选→逐项核对采纳验证卡2→1→0，改答后恢复2卡且旧候选STALE_ANSWER/409，拒绝后状态不变。有效hash但旧pending标签不产生重复卡；applied标签但hash失效仍产生对应卡。

`fixed15-original-complete-validation.json`将相同固定15例仅作明确标记的本地字段反例，置于原CALL完整clarify响应中，15/15符合预期。没有私改原模型字节或向项目保存这些反例。

原CALL-94cf90efe9534bbe全文件SHA256仍 `59f8cc55df99405490b5fc7796e2b69eee1175bb994210a17f86525a6793fcef`；final_output UTF8正文仍 `e48b4b7a4280ed01b9491a41f6fb2b800b6783d35342d67537f47d5af75cf9b9`。原响应本轮离线有限校验仍accepted_offline_only；历史registered SEMANTIC_BLOCKED未回写。这不代表内容可采纳：反馈对象≠排他可见权限、quota≠budget、存在≠满足、无来源旧逐项流程比较四项仍为人工来源诊断/生成约束，未实施通用自动蕴含校验。

## 测试与源码绑定

最终磁盘app源码SHA256：`50386087dd486be8d2d05ead7c2f66965cf8e744af94e4a09aedaf583593e114`。磁盘短指纹为 `50386087dd486be8`，不是已验证的运行指纹。最终定向、根全量与固定证据复放均绑定此源码。

| 实际执行 | 结果/退出码 |
| --- | --- |
| before-targeted | 12通过/26失败，exit1；包括新增测试错误把人工核对元数据加入模型响应，导致schema错误，原日志/JUnit保留 |
| before-targeted-v2 | 修正为独立核对副本、不改固定期望；302通过/12失败，exit1，暴露两缺口及直接边界 |
| targeted-v1 | 314通过，exit0；中间源码证据保留 |
| targeted-final | 315通过/0skip，exit0，40.16秒；包含其它对象查询不能清除本对象未知 |
| full | 1554通过/5skip/0失败，exit0，283.61秒 |

准确argv、退出码、源码SHA及每次log/JUnit在各 `*-command.json`、`*.log`、`*.xml`。退出码来自subprocess返回值，日志中的“退出清理”标记不是退出码证据。最终命令：

```powershell
.venv\Scripts\python.exe -X utf8 evidence/offline-two-gap-closeout-20261003-060911/run_tests.py targeted-final
.venv\Scripts\python.exe -X utf8 evidence/offline-two-gap-closeout-20261003-060911/run_tests.py full
```

根pytest实际参数为 `.venv/Scripts/python.exe -X utf8 -m pytest tests -q -p offline_provider_guard --junitxml=<本轮绝对目录>/full.xml`。包装只在测试进程PYTHONPATH加入本轮guard；其socket连接guard禁止外网和日常8765，最终两份guard回执连接尝试为空。隔离tmp Store/create_app/absent.env/伪key/MockTransport不使用日常凭据或数据；Mock用量不是实际DeepSeek用量。

Python3.11.9、pytest9.1.1、FastAPI0.141.1、httpx0.28.1、jsonschema4.26.0、uvicorn0.53.0、python-docx1.2.0；完整依赖记录在before/after快照。两项已有弃用警告保留。5skip仍是Word/Excel/PPT合成Office、相对目录COM与Excel预览后备opt-in，本轮没有开启Office；旧实际证据不能算本轮执行。

## 保护、运行未验与停止

只读SQLite前后逐行hash核对22项目、319revisions、807records、settings1、migrations1，全部不变且无queued/running记录。主P-f73f8130d36040af保持v7、七原candidate、Q-1e44e50ac69c4bb0 answered/pending；对照P-f31d1a6267f041bc v2及其它项目保持。192旧CALL逐一hash、旧sources/evidence/work/dist/配置及更正回执保持；配置仅核hash，不打印内容。完整本地保护证据不上传全库或秘密。

本轮禁止服务重启/API状态读取。此前自动审批拒绝的准确动作为“另进程读取 http://127.0.0.1:8765/api/session 并保存恢复回执”，返回仅“Rejected: blocked by policy”，未给具体原因。本轮未重试、未换工具/进程绕过。**当前服务状态、未保存输入和新版运行fingerprint均未验**；没有把旧21:46观察或磁盘指纹当成新版服务证据。

协调request-cap回执既有df31→c8失配及append correction只读保持；恢复旧hash曾被拒，本轮无恢复尝试。早期路径/投影误报及实际exit0/false flags与v2更正链保持，不将工具误报称为产品数据损坏。

仅四个明确文件本地Git：上述两模块、本轮测试、本报告。原work/未跟踪保留；不add-A、不提交DB/原始模型/凭据/巨量生成物，不push/merge/deploy。最终SHA/父链、完整git show/stat、新测试源码、逐成员SHA256 manifest、Git对象与磁盘LF规范化绑定及保护结果在本轮 `final-binding.json`、`git-show-full.diff`、`git-show-stat.txt`、`git-parent-chain.txt`、`manifest-sha256.json`。manifest不含自身、pycache或秘密备份。

有限规则依赖显式谓词、已支持对象名及局部作用域；不能解析所有省略指代、多对象同名、跨句历史时序或证明通用来源蕴含。离线回归绿不代替父独立复核、真实模型内容审核或真实PM批准。真实模型复验、日常回答/采纳、MRD/PRD、正式review、确认/基线、导出、机器消费者、新机与发布均NOT_RUN。完成离线收口即停，不自行花剩余4请求。
