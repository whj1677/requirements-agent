# 2026-10-02 单位候选与本期采纳决定边界

本轮完成固定边界的源码修复及下列验证，不代表业务批准或发布验收。起点为 `b2f9f2dd863a1a12b1d7cda95209fd06f70a95a8`；起点已跟踪文件无未提交改动，已有 `work/` 保留。上一轮归档、1098项全量、Office证据、B2和真实复核均未改写；不重跑十场景或旧付费批次。本轮新增模型请求、自动修复及模型用量均为0，未派发其他Agent、未推送/合并/部署。

## 已确定问题与最小设计

协调器的 `definition-decision-independent-probe-20261002.json` 和 `probe-definition-decision-boundary.py` 与起点冻结源码哈希相符。原件只读复制到本轮证据目录；在仓库内隔离临时数据复放副本，不执行会改写协调器原报告的脚本原件。

| 来源与当前规则 | 同一审查声明 | 修复前 → 修复后 |
| --- | --- | --- |
| 来源：“方案建议：备注可以按Unicode码点计数；该方案需产品经理确认后才采纳。”；当前规则仅“备注最多100字符” | “本期计数口径未确定，需PM确认当前来源中的方案是否已经采纳。” | `SEMANTIC_BLOCKED` → 合法接收；候选单位存在，采纳仍待决定。 |
| 来源：“产品经理已确认：备注按Unicode码点计数，最多100码点，复合emoji各组成码点分别计数。”；当前规则相同 | 同上 | 两次均 `SEMANTIC_BLOCKED`；不能把明确决定虚报为未决。 |

最小修复只改 `app/semantic_quality.py`，追加 `tests/test_counting_definition_classification.py`，并新增本报告。内部只读事实保留 `definition_present/declared_units`、来源、对象、位置、原文和人工回答引用，增加 `decision_status`（pending/confirmed/not_stated）及携带原句和单位的 `decision_evidence`。

单位候选出现不等于本期已采纳。显式建议、待确认/待采纳的单位仍是证据，但分类为 undecided；同文明确PM确认本计数单位，或明确指回前述计数方案，才可结束该候选的待采纳状态。“未确认”“没有证据表明已确认”“已确认不采纳”和另一登录方案的确认均不当作肯定采纳。原无建议标记的确定规则继续保留已有定义检查；not_stated不宣称已有人工批准。

缺失声明核对单位是否实际存在，未决声明核对相应决定依据；不是删除“未确定”匹配、屏蔽固定句子或跳过校验。矛盾分类优先于定义/采纳：有出处的人工回答即使写“已确认”，重复“Unicode码点且每个emoji恒为一码点”仍被现有成文门拦截，仍保留 **99个A加👍🏽 = 101码点、100扩展字素簇** 反例及PM选择。

策略版本升为 `semantic-quality-7`，使历史审查需重新核对，不改写历史内容。未改变响应Schema、provider输出边界、持久化业务结构、需求规则、人工核对/正式确认/基线流程；分类事实不是实际采纳动作，也不会替PM选择单位。没有前端、Office、Skill、UIAgent、网络或凭据改动。

## 确定性回归与固定证据

- 首批选择 adoption 相关新测试：19失败、4通过、36未选；失败包含待采纳误拦和新事实字段尚未实现，保留 `reproduce.log`。追加否定采纳成对复现：1失败、1通过、59未选，保留 `reproduce-negated-adoption.log`。没有修改旧测试断言来过关。
- 中间相关集合357项通过、2个依赖弃用警告（36.51秒）。补否定采纳后，该文件最终61项通过（原33项 + 新28项），2个依赖弃用警告，6.41秒。见 `targeted.log`、`new-final.log`。
- 新28项覆盖码点/字素簇、公开review/ingest边界、显式待采纳、明确确认、同文建议后确认、否定或未证明确认、前述方案指代、已存在当前规则、带出处的人工回答、仍有矛盾的已确认回答及无关能力确认。源引用和对象/位置保留，输入只读相等。
- 最终源码全量：**1126通过、5跳过、2个依赖弃用警告，227.99秒**；JUnit1131项、失败0、错误0、跳过5。见 `full.log/full.xml`。
- 5项跳过精确对应已核实际Office证据：合成Word/Excel/PowerPoint、Office相对数据路径、Excel回退预览定位；Office/解析相关文件未变，按授权复用 `full-retest-20261002-0821/full-fixed-audit.json`，不声称本次重新执行。
- 冻结195个源码/测试/脚本/前端/依赖文件；相比起点仅semantic_quality及本测试文件变化。正式前端字节与既有构建证据一致，响应契约、Office和原始数据配置不变。

最终探针复放 `probe-final.json` 两行均匹配预期、项目不变、模型调用0。

固定请求原样离线复放：

| 已保存请求 | 本轮结果 |
| --- | --- |
| 旧B2 `CALL-215298bd13a7468a` | 仍 `SEMANTIC_BLOCKED`，定位 `DOC-512fc7cec6ec4e99.PRD-664e8e9dc8af.blocks[1]`；该块已有单位，错误缺失措辞继续拦截。 |
| 上轮真实联系人复核 `CALL-0eb1dfbba5fb4923` | 原结果仍合法接收，assessment仍为 `needs_changes`；两项实际计数冲突各自保留101/100反例、blocker及PM决定，未代选或解除风险。 |

见 `fixed-replays.json`，记录原请求哈希；两份原始返回和项目未变，新增付费调用0。上一轮原输入→联合绑定问题→标注为合成的PM回答→一致MRD/PRD文档证据继续保留，不重复生成历史成果或把合成回答当成真实PM批准。

## 正式8765与保护检查

重启前日常活动任务检查返回0，现有正式页面无未保存输入；先对根目录SQLite作一致性备份，再用原stop/start脚本安全重启，保留原启动模式。只有根目录正式8765工作台，无第二个临时服务。旧运行fingerprint `ee52293d62581f3e`，新运行 **`2cfc3cfa7aff98b9`** 与冻结后磁盘源码一致。

- 18个日常项目可读，SQLite integrity_check为ok，foreign_key_check空。
- 正式HTTP：联系人PRD、电价PRD的Markdown和Word四次导出请求均409，理由继续是实际计数/失败条件/审计实现/角色风险；未创建导出记录。
- 正式浏览器只读核对：首屏提示核对建议与未决问题；联系人和电价PRD没有下载入口，历史审查提示需更新；电价MRD ready且显示DOCX/MD/ZIP下载入口。截图已目视核对，随后恢复原聊天项目，未编辑、发模型请求、批准、确认或点击正向下载。
- 正向正式下载文件沿用上轮已核证据，本轮新做的是准备状态/页面入口和真实409门检查，不能说重新执行了正向200下载。旧“澄清0项”仅反映保存问题状态，不表示语义风险已消失。
- 保护终检：**34,608个原有受保护文件哈希无变化**；projects/revisions/records/settings/migrations的行数18/279/727/1/1及完整内容哈希与起点一致。`work/`、数据配置、旧证据、历史来源、文档和基线均保留。见 `preservation-result.json`。

## 命令、归档与限制

本轮隔离证据脚本及结果均在 `evidence/definition-adoption-boundary-20261002-1854/`，原模型证据、数据备份、配置和密钥不入Git。核心已执行命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/replay-coordinator-probe.py final
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_counting_definition_classification.py -q
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/replay-fixed-evidence.py
$env:RA_TEST_LOCAL_OFFICE='0'
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/definition-adoption-boundary-20261002-1854/full.xml
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/verify-evidence.py
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/verify-final-runtime.py
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/check-formal-export-gates.py
.\.venv\Scripts\python.exe -X utf8 evidence/definition-adoption-boundary-20261002-1854/preserve.py after
```

仅上述实现、测试、本报告三文件按明确列表提交；修改前磁盘原版、修改后版、Git原版身份、SHA-256及完整补丁逐文件存入 `file-archive/`。当前提交及其父b2f9f2d、195文件测试清单和正式运行fingerprint的对应关系见 `commit-receipt.json`，不在提交后改写本报告为自引用SHA。

有限规则识别明确单位、采纳/否定谓词及同文计数指代，不是完整自然语言理解；复杂跨对象、多候选指代、历史时序或省略主体仍需人工核对。来源写“已确认”只是可追溯的材料陈述，不能替代系统中的人工审批或正式确认动作。有出处或已采纳也不证明含义一致，真实PM仍须解决计数矛盾。发布前干净检出、发布环境及人工业务验收未执行；本轮不推送、合并或部署。固定验收集合满足后停止，不扩新功能或循环调用模型。
