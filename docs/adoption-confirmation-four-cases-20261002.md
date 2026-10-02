# 2026-10-02 同文计数建议确认的四场景收口

本轮只完成既定四场景验收及必要保护回归、全量、正式8765检查和本地归档，不代表业务批准或发布验收。起点 `5002d76efecba309156685d705cad67dddc4a85b`，起点仅有未跟踪 `work/`，活动任务为0。前序 `dd4b5d8 → b2f9f2d → 5002d76` 的成果和原始证据保留；本轮新增模型请求、模型用量、自动修复均为0，未派发其他Agent，不推送、合并或部署。

## 固定输入、问题与修复

协调器 `adoption-v7-independent-probe-20261002.json` 及 `probe-adoption-v7-independent.py` 与起点源码绑定。原件只读复制，原路径未执行或覆写；在本轮独立目录复放改写输出路径的副本。以下场景的当前规则均为“备注最多100字符。”，同一审查均为“本期计数口径未确定，需PM确认当前来源中的方案是否已经采纳。”。

| 固定来源 | 起点实际结果 | 修复后实际结果 | 采纳状态 |
| --- | --- | --- | --- |
| 方案建议：备注可以按Unicode码点计数。 | accepted | accepted | pending |
| 方案建议：备注可以按Unicode码点计数；该方案需产品经理确认后才采纳。 | accepted | accepted | pending |
| 产品经理已确认：备注按Unicode码点计数，最多100码点，复合emoji各组成码点分别计数。 | SEMANTIC_BLOCKED | SEMANTIC_BLOCKED | confirmed |
| 方案建议：备注可以按Unicode码点计数；产品经理已明确确认采纳这个计数建议，最多100码点，复合emoji各组成码点分别计数。 | accepted（错误） | SEMANTIC_BLOCKED | confirmed |

根因证据 `cause-before.json`：确认子句没有再次写单位，原局部指代规则也不识别“这个计数建议”，因此在检查已经能够匹配的“产品经理已明确确认”谓词之前被跳过。候选状态没有被该确认更新，后续逗号分开的单位句不能补回丢失的确认。

最小实现仅三处：局部指代允许已有指示词指向“方案/建议”，补“这个”；否定扫描包含确认谓词自身，避免把“尚未明确确认”误当肯定；策略版本升为 `semantic-quality-8`。已有肯定确认分支优先于候选措辞，无需增加采纳动作或新分类。不是硬编码整句、删掉未决匹配或跳过校验。

保留现有内部事实、对象、来源、位置、原句和决定证据；第四行同时保留原候选句的pending证据及明确确认句的confirmed证据。没有响应Schema、持久化结构、业务规则、人工审批、前端、Office、Skill、UIAgent、网络或凭据变化。来源声称“PM确认”只是材料证据，不替PM采纳，也不代替系统正式确认。

## 确定性回归与历史证据

- 新增8项：上述四个原样公开review边界；尚未确认/明确不采纳的两项反例；其它登录建议不能确认计数；后续明确待确认仍保留文本时序。每个来源事实核对实际位置 `SRC-0001/EX-0001`、原文和联合来源关系，输入只读相等。
- 原61项直接复用，继续覆盖码点/字素簇、review/ingest、缺失/未决/矛盾、带出处的人工作答、跨对象、已有规则和否定确认。真实矛盾仍有 **99个A加👍🏽 = 101码点、100扩展字素簇** 的可复算反例并要求PM选择；有出处或已确认不能解除矛盾。
- 首次新增回归5失败/3通过/61未选，其中3项为本轮新夹具错误地只预期REQ来源归属，另2项复现确认跳过及证据时序；初次修复后相关集合4失败/363通过，均为同一新夹具归属断言。保留失败日志；按实际夹具核对REQ/RULE/AC三个归属后，本轮新断言改为实际三者，未改旧断言、四场景接受/阻塞预期或业务内容。
- 最终相关回归 **367通过，2个依赖弃用警告，37.37秒**；本分类文件在全量JUnit中69项，原61+新8全部执行。
- 最终全量 **1134通过、5跳过、2个依赖弃用警告，227.59秒**；JUnit1139项、失败0、错误0、跳过5。警告来自既有Starlette/httpx及anyio弃用。
- 5项Office skip精确对应已核实际Word/Excel/PowerPoint合成读取、相对数据目录及Excel回退预览定位。Office相关源码未变，复用 `full-retest-20261002-0821/full-fixed-audit.json` 的五项实际执行证据，不声称本轮重跑。
- 冻结195个源码/测试/脚本/前端/依赖文件，与5002相比只修改semantic_quality及该测试；最终哈希与全量期间及固定探针一致。前端字节与既有构建一致，协调器原件未变。

| 原始请求固定离线复放 | 本轮实际结果 |
| --- | --- |
| 旧B2 `CALL-215298bd13a7468a`，证据SHA-256 `86570ee9ecc0748a92a823ba4241c37851b62955ce7b9dd82ce01e8fcaca0820` | 仍SEMANTIC_BLOCKED，定位实际 `DOC-512fc7cec6ec4e99.PRD-664e8e9dc8af.blocks[1]`，已有单位不能虚报缺失。 |
| 实际联系人复核 `CALL-0eb1dfbba5fb4923`，证据SHA-256 `231a04236ea91e753beb4894ff905227391a77bf9ad7841131c382fe9f6a8c28` | 原结果accepted，assessment仍needs_changes；两项实际计数冲突保留blocker、101/100反例及PM决定。 |

两份原始返回和项目哈希均未变，新调用0。上一轮“原输入→联合绑定问题→标为合成的PM回答→一致MRD/PRD”的证据链继续原样保留，不重新生成历史文档，也不把合成回答当真实PM批准。

## 正式8765及用户工作保护

已有8765起初是5002源码，运行fingerprint `2cfc3cfa7aff98b9`。更新后磁盘变化，必要安全重启一次：先核无在途任务、页面无未保存输入，根SQLite一致性备份，再使用现有stop/start脚本保持原启动模式；没有新建第二个工作台。重启后的 **`681e7f0487f404d6`** 与冻结源码一致，18个日常项目可读，SQLite integrity_check为ok，foreign_key_check空。

联系人PRD和电价PRD的Markdown/Word四次真实HTTP导出请求均409，仍由实际计数/失败条件/审计实现/角色问题阻塞，未创建导出。联系人正式页面未显示DOCX下载，历史审查提示“2条意见，需更新”；截图已目视核对。恢复原聊天项目后仍提示“已生成，请核对建议与未决问题”。未编辑、发模型请求、批准、确认或点击正向下载。电价MRD当前只读ready检查为true，正向下载沿用已核历史证据，不能说本轮重新执行200下载。“澄清0项”不能代替语义风险结论。

保护终检核对原有 **34,663个文件**，覆盖work、旧docs/原型/证据、来源、导出、备份及.env；原件哈希不变。日常projects/revisions/records/settings/migrations行数18/279/727/1/1及完整内容哈希不变。本报告是新增说明，已确认历史、文档和基线未改写。详细结果见 `preservation-result.json`，提交后复核见 `commit-receipt.json`。

## 执行命令、归档与停止点

本轮隔离证据目录 `evidence/adoption-confirmation-four-cases-20261002-1927/`；原模型证据、数据备份、配置和密钥不入Git。核心已执行命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/replay-four-cases.py final
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_counting_definition_classification.py tests/test_semantic_quality_regressions.py tests/test_semantic_quality_boundaries.py tests/test_continuous_quality_regressions.py -q
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/replay-fixed-evidence.py
$env:RA_TEST_LOCAL_OFFICE='0'
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/adoption-confirmation-four-cases-20261002-1927/full.xml
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/verify-evidence.py
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/verify-final-runtime.py
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/check-formal-export-gates.py
.\.venv\Scripts\python.exe -X utf8 evidence/adoption-confirmation-four-cases-20261002-1927/preserve.py after
```

仅实现、测试和本报告三文件按明确列表本地提交。修改前磁盘字节已事先保存；逐文件归档before/after、规范化Git对象、SHA-256及完整暂存补丁到 `file-archive/`。最终SHA、父5002、文件列表、测试冻结清单和运行fingerprint对应见 `commit-receipt.json`；不在提交后为自引用SHA改写本报告。

有限局部指代和文字顺序规则不能解决所有跨对象、多候选、省略主体或复杂历史时序；仍需人工核对。真实PM仍须选择存在矛盾的计数定义，系统严格人工审批不变。发布前干净检出、发布环境验证和人工业务验收未执行。本组固定验收满足后收尾，不扩新功能、无穷同义词或新增模型压测。
