# requirements-agent 全面功能与体验验收 · 2026-09-30

**负责人结论：发布验收未通过。** 本轮已实际执行资料导入、分析、澄清、规则采纳、MRD/PRD 生成与核对、语义审查及文档 ZIP 导出。最终进入第 5 步，但模型审查为 `needs_changes`，程序正确阻止建立正式基线。首次分析的恢复体验、审查意见的处理入口仍有 P1 问题，不宜直接交给无人指导的新用户使用。

文档已具备可评审的需求骨架，核心条款没有丢失；这不等于文档质量、真实业务理解或产品发布已经验收通过。本轮仅测试并记录问题，**没有修功能、改模型预算、重新打包或发布**。

## 1. 对象、方法与证据边界

| 项目 | 本轮实际范围 |
| --- | --- |
| 源码 | `main`，`c9a5b603a110c8bcc02337762c4554fb4169eea1`；运行根目录 `app/` 与 `web/dist/` |
| 工作台 | 唯一地址 `http://127.0.0.1:8765/`；运行指纹 `6505d86a91340b55` |
| 测试数据 | 单独的 `evidence/comprehensive-acceptance-20260930/isolated-data/`，2 个合成项目；不把原项目发送给模型 |
| 业务对照 | [scenario.md](../evidence/comprehensive-acceptance-20260930/scenario.md)：云平台巡检任务批量导出，8 条规则、8 条验收、一次审计留存时长澄清 |
| 网页执行 | 内置浏览器实际导入、预览、填表、导航、确认、刷新和双标签页冲突；主要使用语义定位与 Enter，不等同于完整鼠标/触屏/辅助技术验收 |
| 模型执行 | 使用已配置的 `deepseek-flash`，实际图像分析、需求整理、澄清、文档生成、语义审查；只发送本轮合成材料 |
| 独立复核 | 主管亲自核对网页、源代码相关分支、运行记录、实际导出内容；较轻子 Agent 分别执行离线回归和文档语义审查，自报结论不直接当作最终结论 |
| 导出验证 | 实际 HTTP 文档 ZIP 响应、文件哈希、DOCX 正文和 Markdown 对照；这是独立接口检查，不能冒充浏览器下载保存成功 |

主要合成项目为 `P-737b9ec1914d487d`，最终底稿 v40；MRD/PRD 各文档 v1，生成依据底稿 v37。另一个项目 `P-a62c5290eabc4d20` 用于输入保留与跨标签页过期计划检查，未发起模型调用。

测试作者提前明确了大量业务要求，因此本轮可以核对规则保真、追溯和澄清闭环，**不能证明 Agent 面对模糊业务时会自主发现所有缺失信息**。合成工程快照不是用户真实云平台的事实。

## 2. 五个方面的总体判断

| 方面 | 观察与判断 |
| --- | --- |
| 界面 | Word 连续阅读比编号段落更自然，资料类别和读取状态可见；Excel 被拆成单元格文字段落，表格关系难辨；必需的文档核对入口默认折叠；部分阶段/审查状态容易误导。 |
| 操作方式 | 保存、离开保护、刷新后恢复输入、成功确认后的阶段跳转均实际工作；首轮分析失败后缺少针对性恢复，过期计划只能靠手动刷新，审查失败后的引导跳错页。 |
| 对话质量 | 能询问真实未知，并把回答转换为待采纳的修订，未直接把调查备注当答案；选项会提出无依据的既有策略或固定时长，容易形成暗示；首次整理仍被输出分类元数据卡住。 |
| 文档质量 | 8 条规则和 A01–A08 保留，权限、幂等、超时、失败恢复、有效期及留存口径准确；叙述重复、章节归属不佳，两处工程推断未清楚标明身份，正式实现交接前需处理。 |
| 信息是否全面 | 已明确的角色、现状、目标、范围、保持项、权限、数据、异常及验收维度覆盖较完整；没有捏造 ROI 或把原型示例当业务上限。仍需用真实、模糊、冲突和规模更大的材料继续验证，不能由一个充分输入的案例推出全面理解能力。 |

## 3. 实际执行的覆盖记录

状态仅针对下列操作及合成案例；“已观察符合”不代表产品验收通过。

| 编号 | 操作 | 实际结果与证据 |
| --- | --- | --- |
| T01 | Word、Excel 导入及读取反馈 | Word 约 11 秒，经本机 Office 后备读取取得 30 个文字摘录；Excel 约 7 秒，取得 2 个工作表的 36 个单元格摘录。网页有读取进度/阶段反馈，完成后诚实标为“部分读取”。[Word 预览](../evidence/comprehensive-acceptance-20260930/browser/03-word-preview.png)、[Excel 预览](../evidence/comprehensive-acceptance-20260930/browser/04-excel-preview.png)。不据此认定其他企业电脑策略也兼容。 |
| T02 | 附件阅读方式 | Word 正文为连续阅读，未显示“段落 1/2”；Excel 仍按文字段落排列，行列关系丢失。原排版/未提取对象明确提示需查看原附件，不冒充原生 Office 渲染。 |
| T03 | 原型截图 | 通过“导入墨刀原型截图”加入合成 PNG，归入设计参考，实际进行了视觉模型读取；样例布局没有变成已确认业务上限。图像读取仍有部分限制。 |
| T04 | 工程上下文 JSON | 合法包展示架构、数据流、约束、来源，4 个模块默认全部选择。快照事实作为工程上下文，不自动采纳未来业务规则。非法包明确“未导入”，未把伪造的 `approved` 属性当授权。[界面](../evidence/comprehensive-acceptance-20260930/browser/05-engineering-context.png)、[非法包](../evidence/comprehensive-acceptance-20260930/browser/12-invalid-engineering-package.png)。 |
| T05 | 资料类别、版本及模型发送范围 | 展示目标与诉求、设计参考、工程上下文。诉求 v2 替代旧 v1 的 20 条上限，发送前预检排除旧版本；最终条款使用 30 条。库内仍保留已排除版本，属于历史可追溯而非当前输入。[预检](../evidence/comprehensive-acceptance-20260930/browser/06-analysis-preflight.txt)。 |
| T06 | 分析响应、失败反馈及恢复 | 有进度、忙碌状态和停止入口，不是无反馈等待；首轮 4 次模型请求后任务失败，原因是分类依据与 `modified` 不一致，0 个需求候选写入。补充自然语言范围说明后再分析，形成 23 个候选。[失败画面](../evidence/comprehensive-acceptance-20260930/browser/10-analysis-semantic-blocked.png)、[运行快照](../evidence/comprehensive-acceptance-20260930/snapshots/final-isolated.json)。后者不倒算为首轮成功。 |
| T07 | 未保存输入保护 | 实际检查“留在当前页”与“保存后离开”，重新打开及刷新均保留输入；本轮未复现此前“点击分析文字消失”。[恢复画面](../evidence/comprehensive-acceptance-20260930/browser/11-saved-draft-reopened.png)。 |
| T08 | 第 2 步缺项引导与阶段跳转 | 缺失优先级时“去填写优先级与依据”定位到实际字段；保存范围、确认规则、确认文档后分别进入后续阶段。条目修改使前序核对失效，需要重新核对，已填字段保留。 |
| T09 | 未知、回答、修订和采纳 | “暂时不知道”的调查备注保存后仍保持阻塞，刷新后保留。正式答复“审计事件起算 30 天，仅审计，ZIP 仍 24 小时”形成 2 个修订候选；人工采纳后保留原 REQ/AC 编号并升级 v2，问题为 `answered / applied`。[最终快照](../evidence/comprehensive-acceptance-20260930/snapshots/final-isolated.json)。 |
| T10 | 业务行为维度 | 选定的 8 条需求均展示角色、入口、流程、数据、权限、结果、异常维度；已知规则保留。个别字段仍填“未提及异常分支”，信息增量不足。[规则记录](../evidence/comprehensive-acceptance-20260930/browser/16-adopted-rules.txt)。 |
| T11 | PRD、MRD 生成与正文核对 | 两份实际文档均有 8 条规则及 8 条验收；未混入旧 20 条、未采纳原型布局、模型讨论过程或虚构 ROI。查看与核对各自版本后进入第 5 步。生成状态 `partial` 保留 Office/图片读取限制，不描述为全部资料已理解。 |
| T12 | 导出文档与服务响应 | 实际 PRD ZIP 响应约 0.172 秒、MRD 约 0.156 秒；同期轻量 API 探针均响应。ZIP 内 DOCX 可解析且段落文字与 Markdown 相符，封面换行不同。未复现此前文档 ZIP 拖死 API；本例体量很小，不是压力测试。[PRD 证据](../evidence/comprehensive-acceptance-20260930/artifacts/prd/export-evidence.json)、[MRD 证据](../evidence/comprehensive-acceptance-20260930/artifacts/mrd/export-evidence.json)。 |
| T13 | 语义审查与确认门禁 | 真实审查约 79 秒，输出 `needs_changes`、2 条 warning、2 条 info；确认门禁显示“语义审查仍有阻塞项”，正式确认按钮禁用。未绕过建立基线或交接包。[第 5 步](../evidence/comprehensive-acceptance-20260930/browser/24-review-blocked.txt)。 |
| T14 | 审查意见处理入口 | 第 5 步“前往核对”跳到第 3 步，那里是 0 个待答问题，没有审查意见。主管手动切回文档页才找到意见；其关联条款定位按钮实际工作。[错误跳转](../evidence/comprehensive-acceptance-20260930/browser/25-review-link-wrong-stage.txt)、[找到意见及条款定位](../evidence/comprehensive-acceptance-20260930/browser/26-review-finding-location.png)。 |
| T15 | 双标签页过期计划 | A 打开分析预检，B 保存新版本，A 确认后服务端拒绝旧版本；没有新增运行或付费请求，输入保留。返回再分析仍使用旧版本并重复报错；手动刷新后才加载新内容并重新预检。[重复报错](../evidence/comprehensive-acceptance-20260930/browser/22-stale-plan-retry.txt)、[刷新恢复](../evidence/comprehensive-acceptance-20260930/browser/23-stale-plan-reloaded.txt)。 |
| T16 | 窄屏基础检查 | 390×844 未观察到横向溢出，但页面纵向很长，主要操作需要较多滚动，模型设置随侧栏隐藏。仅桌面浏览器改变视口，不是手机实机或触控验收。[画面](../evidence/comprehensive-acceptance-20260930/browser/08-mobile-progress.png)。 |

## 4. 已复现问题及整改顺序

P1 表示关键工作流可靠性或阻塞后恢复问题；P2 表示明显操作障碍或正式交接前必须明确的内容；P3 表示阅读与文案优化。问题优先级由主管依据实际后果确定，不照搬模型审查的标签。

| ID / 优先级 | 问题、触发与影响 | 证据与验收要求 |
| --- | --- | --- |
| F01 / **P1** | **首次分析因模型分类元数据错误整步失败，缺少针对性恢复。** 在现有单条导出上增加批量能力，模型修复结构后仍把含现状依据的条目分类成新增，程序应要求 `modified`。门禁拦截合理，但普通用户看到临时 ID 和分类错误，无法编辑尚未写入的候选，只能再分析。测试通过额外补充范围解释恢复，原输入本已包含现有平台与改动关系。 | `RUN-c745763f08e64db9`；[失败记录](../evidence/comprehensive-acceptance-20260930/browser/10-analysis-semantic-blocked.txt)；分类守卫 `app/understanding.py:119`。统一提示词与校验语义，错误时针对对应条目修复并保留可核对内容，不能靠削弱来源门禁解决。用本例首次输入复验，且用有冲突依据的反例确认仍会明确拦截；不以相同请求反复重跑强求一次绿灯。 |
| F02 / **P1** | **审查阻塞后的入口跳错页，且摘要显示“0 项需处理”。** 第 5 步提示语义审查有阻塞，“前往核对”进入第 3 步的 0 个待答问题；实际审查意见在第 4 步。文档折叠摘要的 0 只统计另一类问题，不能反映这次审查结果。用户既被阻止继续，又没有正确下一步。 | [错误跳转](../evidence/comprehensive-acceptance-20260930/browser/25-review-link-wrong-stage.txt)、[实际意见](../evidence/comprehensive-acceptance-20260930/browser/26-review-finding-location.png)；`web/src/panels.tsx:29,112-113`。从门禁一次点击进入当前版本的审查意见，展开并定位需处理条款；待处理数量与实际阻塞类型明确一致。 |
| F03 / P2 | **必需的文档核对操作默认藏在折叠区。** 用户看到“请评审核对文档”，主操作区缺少直接入口；需要自行展开“评审与交付状态”才能找到各文档核对按钮。 | [核对区域](../evidence/comprehensive-acceptance-20260930/browser/20-document-review-controls.png)。未核对时直接展示待做动作和当前文档版本；缺项提示可直接定位。 |
| F04 / P2 | **Excel 预览失去表格关系。** “字段 / 含义 / 规范 / taskId / 原巡检任务身份 …”逐段显示，用户难以确认哪些值属于同一行；把正文美化成纸张不能解决结构阅读。 | [实际画面](../evidence/comprehensive-acceptance-20260930/browser/04-excel-preview.png)。至少按工作表和可得行列位置展示轻量表格；取不到位置就明确标明文字摘录，不冒充原生工作表。 |
| F05 / P2 | **过期计划拦截后无法按页面提示恢复。** 双标签页保存导致版本过期，A 的输入保留且旧请求被拒绝；“返回保留输入”再分析仍报相同错误，必须手动刷新。 | [重复报错](../evidence/comprehensive-acceptance-20260930/browser/22-stale-plan-retry.txt)。提供加载最新版本与比较我的输入的路径，重新计算预检；不能自动重发旧请求，也不能丢弃本地输入。 |
| F06 / P2 | **项目列表与主页面的阶段不同步。** 只保存、尚未分析的项目列表已显示第 2 步；主页面进入第 5 步时列表仍显示第 3 步，刷新后才一致。 | [保存后重开](../evidence/comprehensive-acceptance-20260930/browser/11-saved-draft-reopened.png)、[第 5 步记录](../evidence/comprehensive-acceptance-20260930/browser/24-review-blocked.txt)。列表以同一权威业务阶段计算，并在保存、任务完成和核对后更新；当前查看阶段与业务完成阶段如有区别须明确标注。 |
| F07 / P2 | **把当前单条报告的存储方式写成新 ZIP 的确定方案。** “产物落地在对象存储这一既有能力上”是可信复用候选，快照并未决定本期 ZIP 选型。 | [PRD](../evidence/comprehensive-acceptance-20260930/artifacts/prd/PRD.md) 第 322 行；对照 `scenario.md:7,14,23`。明确为工程建议或经有权责任人采纳后形成方案，不能静默变成业务规范。 |
| F08 / P2 | **审计写入粒度从推断变成确定叙述。** “在导出任务创建、完成或失败时写入审计记录”超出已给定的字段、最终结果与保留规则。合理实现推断仍需标明身份；主管在候选采纳时也未发现这个差异，说明核对界面和提示尚不能充分辅助审阅。 | [PRD](../evidence/comprehensive-acceptance-20260930/artifacts/prd/PRD.md) 第 296、326 行，原条目的 `behavior.flow`。区分一条记录更新、多个事件记录等方案；没有决策证据时不规定事件粒度。 |
| F09 / P3 | **文档用重复内容代替信息深度。** 已逐条展示规则与行为字段，又在 PRD 的审计小节下复述整个功能；MRD 多处重复目标与范围，再附全部详细条款。阅读者很难辨认 MRD 的决策信息和 PRD 的实现/验收信息。 | PRD 第 314–326 行、MRD 第 9–44、46–142 行。[独立文档审查](../evidence/comprehensive-acceptance-20260930/semantic-review/final-document-review.md)。MRD 展开有证据的用户、价值、优先级和验证计划；PRD 把流程、数据、权限与异常归入对应需求，保留必要追溯但删除无增量复述。缺数字时保留未知，不以虚构数据增加“深度”。 |
| F10 / P3 | **澄清选项有无依据的暗示。** “按现有平台审计策略默认执行”并没有该策略存在或时长证据；“固定 180 天”也没有业务来源。它们尚未采纳，不属于已生成错误规范，但会引导用户草率选择。 | [候选审查](../evidence/comprehensive-acceptance-20260930/semantic-review/applied-candidates-review.md)。先问明确时长、起算点与规则适用对象；方案例子标为建议并允许直接填写，不暗示已经有平台事实。 |
| F11 / P3 | **文案及空占位影响准确性。** A08 的“已有批量导出审计写入能力”可以合理解释为实现后的验收环境前提，但容易与当前已有能力混读；“未提及异常分支”没有额外信息；刚生成的无关联编号意见被称为“旧意见”；Word 读取提示出现“0 图片待视觉分析”。 | PRD 第 108、244、376 行，MRD 第 142 行，文档审查和预览记录。使用明确的测试前提、真实关联状态与读取数量；不把无编号自动叫旧意见，不填无信息占位。 |
| F12 / P3 | **窄屏关键入口与操作距离。** 侧栏隐藏后没有可见模型设置入口，长页面把主要动作推至很下方。 | 窄屏画面及 `web/src/workflow.css:301`、`web/src/main.tsx:49`。如果支持窄屏，提供可达的设置入口与主要操作；手机实机验证仍需补做。 |

本轮未观察到服务无声崩溃、分析时输入清空或文档 ZIP 导致 API 死锁。这里的“未观察到”只适用于本次有限场景，不注销历史问题，也不等于稳定性已经全面证明。

## 5. 文档、业务完整性与模型审查的区别

### 5.1 已核对的实际文档

- [PRD Markdown](../evidence/comprehensive-acceptance-20260930/artifacts/prd/PRD.md)、[PRD Word](../evidence/comprehensive-acceptance-20260930/artifacts/prd/PRD.docx)。
- [MRD Markdown](../evidence/comprehensive-acceptance-20260930/artifacts/mrd/MRD.md)、[MRD Word](../evidence/comprehensive-acceptance-20260930/artifacts/mrd/MRD.docx)。
- [最终独立语义审查](../evidence/comprehensive-acceptance-20260930/semantic-review/final-document-review.md)，以实际 HTTP ZIP 的 DOCX 字节及 Markdown 为对象。

规则覆盖包括角色及租户/园区权限、0/30/31 边界、翻页与筛选、服务端整批校验、幂等和超时结果待确认、导出状态及整体失败、文件与 CSV 字段、24 小时有效期、下载重新鉴权、审计字段和 30 天留存。留存答复已进入原条目 v2，没有把 ZIP 24 小时改成审计时长。

DOCX 与 Markdown 段落文字相符，仅封面换行不同。两份 DOCX 无内嵌图片或表格，本轮没有验收分页、打印效果或原生 Word 页面渲染；不能以能解析正文替代排版验收。

MRD 没有虚构市场、用户规模、耗时或收入数据，这是正确的证据边界。若需要更成熟的 MRD，应增加有材料支持的决策依据和试点验证方法，而不是凭空补数字。PRD 需要解决 F07/F08 的事实与方案界线，再改善 F09 的章节组织；条款完整不等于开发者已无需澄清。

### 5.2 对真实模型审查的独立判断

模型审查为 `needs_changes`，有 2 条 warning 和 2 条 info，程序因此阻止确认。主管没有把这些标签直接当产品缺陷等级：

- 对新 ZIP 确定采用对象存储的质疑有依据，归入 F07；“既有权限/审计组件可复用”可以是合理建议，不能一概判错。
- A08 写在验收前提中，“已有”可能指实施后的测试环境。不能仅据此断言文档声称当前生产已有批量导出；归入 F11 的措辞问题。
- 模型还指出部分 profile 映射仍 `pending`、来源读取限制表达笼统。这是审查输出，尚未独立确认全部字段冲突，不增加为已复现产品缺陷。
- 主管与独立审查另发现审计事件粒度推断 F08；模型意见没有覆盖主管结论的全部内容。

合成对照本身也有未明确之处：无权 ID 的失败原因不能泄露其他租户元数据；24 小时后不可下载不自动等于物理删除文件。这两点应在真实项目中进一步确定，但不把测试作者未设定的答案作为模型必须命中的规则。

## 6. 离线回归与真实调用账本

### 离线回归

实际命令：`.venv\Scripts\python.exe -X utf8 -m pytest tests -q`。

结果为 **602 项通过、1 项失败、4 项跳过、2 条警告**，pytest 耗时 203.74 秒。失败项：`tests/test_windows_install_verifier.py::test_cleanup_refuses_identity_mismatch_before_terminating_process`，在 finally 清理后代进程时触发 `CLEANUP_DESCENDANT_IDENTITY_UNVERIFIED`。

主管核对了测试、验证器和 traceback：当前错误码汇总多种清理失败分支，没有具体后代 PID/Win32 细节。一次定向复验为 `1 passed in 0.82s`；**首轮失败保留，未改代码，不能据此宣称整套回归通过或问题已修复**。目前归为偶发时序/环境候选，未定位；不是本轮浏览器服务崩溃证据。复验后按相关命令行未发现测试进程遗留。

[离线回归说明](../evidence/comprehensive-acceptance-20260930/regression/summary.md)、[首轮原始日志](../evidence/comprehensive-acceptance-20260930/regression/pytest.stdout.log)、[定向复验日志](../evidence/comprehensive-acceptance-20260930/regression/targeted-rerun.stdout.log)。代表性测试的隔离配置已复核，未进行全套运行时文件系统追踪。

### 真实外部模型调用

| 项目 | 实际值 |
| --- | --- |
| 模型 / 来源 | `deepseek-flash` / `https://api.deepseek.com` |
| 用户动作 / 后端运行 / HTTP 请求 | 6 / 7 / 10；包含自动结构修复请求 |
| 收到响应 / 有 usage 的请求 | 10 / 10；传输层均 HTTP 200，部分输出校验失败 |
| 输入 / 输出 / 总 token | 285,996 / 61,867 / **347,863** |
| 阶段请求 | 图像 2、整理 3、澄清 1、文档 3、审查 1；MRD 与 PRD 在账本中均使用 `prd` 阶段标识 |
| 预算设置 | 延用输出上限 48,000、上下文预算 520,000、单次超时 120 秒、动作超时 300 秒；调用预算为 unlimited。本轮未改这些设置，未因预算暂停或输出截断阻断。 |
| 实际金额 | 未知，未配置供应商计价；主管与子 Agent 的 Codex 消耗不计入上述 token |

审查的 12,466 reasoning token 已包括在输出 token 中，没有重复相加。请求数来自唯一 call ID 去重，运行数来自唯一 run ID，不能把动作、运行和请求相加作为调用数。

[脱敏模型账本](../evidence/comprehensive-acceptance-20260930/model-ledger.json)仅展示阶段、模型、用量、耗时和校验状态。原始模型输入输出、凭据、日常备份、测试数据库均留在忽略的本机证据目录，不作为公开发布材料。

## 7. 下一轮整改与复验计划

以下是建议顺序，不表示本轮已经修复或承诺发布日期。

1. **先恢复关键流程的可用性：F01/F02。** 修正分类契约与针对性恢复；把语义审查门禁指向当前文档意见并正确汇总。验收既要覆盖首次分析成功，也要覆盖真实输出不合法时有清晰下一步、输入不丢、来源门禁仍有效。
2. **再处理可操作性：F03–F06。** 必需动作直接可见、Excel 保留结构、跨标签页冲突可安全加载最新版本、项目阶段统一。使用本轮保存离开、跨标签页和确认后跳转步骤复验，确认没有重复提交或额外模型请求。
3. **提高文档与澄清质量：F07–F11。** 保持快照事实、业务决定、工程建议的身份清晰；调整 MRD/PRD 分工和章节归属；用本轮 8 条规则逐项核对，再加入一例信息不足、材料冲突的真实代表案例。不得靠篇幅、章节数或更大的输出上限代替语义质量。
4. **发布前补齐证据。** 定位 Windows 清理测试偶发失败；在修改后复验相关项与要求的回归。完成正式确认和交接包整程、较大材料/耗时任务、网络失败恢复、安装包及另一台企业电脑验证，再判断能否发布。窄屏按实际支持范围复验。

本轮停止点已达到：代表性整程取得实际结果，阻断没有被绕过，问题有证据及验收要求，原工作台恢复。整改和再打包需要进入后续实施任务，不能把本轮报告当成修复交付。

## 8. 未执行与运行恢复

**未执行：** 真人业务负责人确认、正式基线和基线交接 ZIP（被真实审查门禁阻止）、新安装包/干净电脑/第二设备测试、Word 分页与打印渲染、手机实机/完整无障碍、超大材料及并发压力、畸形 Office 文件、强制网络断开/模型限流、处理中取消、保存成功后 GET 失败、同一问题并发回答冲突。本轮不为这些项目填写通过状态。

恢复情况：测试前一致性备份已建立；测试结束时没有活动模型任务，隔离服务和自建浏览器标签页已关闭，视口恢复。根目录 `scripts/start.ps1 -NoAuth` 重新启动原工作台，仅监听 `127.0.0.1:8765`。恢复后的 HTTP 页面为 200，运行指纹仍为 `6505d86a91340b55`，API 显示 14 个原项目且没有 2 个合成项目。

恢复前后均以只读方式核对：`projects / revisions / records / settings / migrations` 五张表的有序内容与备份一致；备份覆盖的 `sources / exports / evidence/model-calls` 共 239 个文件的身份、大小和 SHA-256 一致，数据库完整性为 `ok`，活动任务数为 0。该核对不等于根 data 目录全部运行日志/PID 文件字节不变。

[恢复前核对](../evidence/comprehensive-acceptance-20260930/closure-before-restart.json)、[恢复后核对](../evidence/comprehensive-acceptance-20260930/closure-after-restart.json)、[隔离测试最终快照](../evidence/comprehensive-acceptance-20260930/snapshots/final-isolated.json)。已有未跟踪 `work/` 保留，本轮未修改应用源码或日常模型设置。

证据根目录：`D:\01_AI工程\01_工程项目\pm-req\evidence\comprehensive-acceptance-20260930\`。截图和原始运行证据为本机附件，未随此报告发布到云端。
