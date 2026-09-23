# 数据、状态与模型输出契约

本文件与任务书共同约束实现。`examples/runtime_response.schema.json` 是应用内部校验 Schema，不应直接当成任何 Provider 的 strict-tool Schema：各服务支持的 JSON Schema 子集不同。

## 1. 权威数据与派生数据

SQLite 中的项目底稿、不可变基线、人工决策记录为业务权威数据；原始材料为不可变来源。Markdown、Word、页面原型、聊天摘要与导出包是派生数据。模型响应是待验证候选，不是数据库命令。

最低实体如下。可合并为较少物理表，但须保留逻辑关系与事务约束。

| 实体 | 最低字段/约束 |
|---|---|
| Project | id、名称、当前 draft_revision、active_baseline_id、时间 |
| Source | id、project_id、kind、purpose、title、URI/内部存储键、sha256、采集时间、版本、parse_status、failure_reason、授权范围 |
| Excerpt | id、source_id、source_hash、定位器、内容/内部图片引用、提取方法、提取完整度；生成后不可静默覆盖 |
| Item | id、kind、title、statement、applies_to、epistemic_status、source_refs、related_refs、selection_status、revision |
| Question | id、topic_key、问题、选项、影响、blocking、关联项、状态、回答依据、首次/最近轮次 |
| Option | id、方案内容、来源、相关候选项、selected/rejected/deferred、人工选择记录 |
| DraftRevision | project_id、revision、内容快照或可恢复差异、内容哈希、父版本、变更原因 |
| DocumentArtifact（替代逻辑名 PrdArtifact） | id、project_id、draft_revision／baseline_id、document_type、content_profile_id/version、reference_hashes、结构化章节、reference_mapping、内容哈希、style_version、生成器版本、MD/DOCX 文件哈希；PRD／MRD 分别记录 |
| Confirmation | id、project_id、revision、brief_hash、prd_content_hash、ui_spec_hash、scope_ids、actor、时间、确认动作 idempotency_key |
| Baseline | id、不可变内容、源 revision、哈希、confirmation_id、父基线；不随聊天修改 |
| Export | id、baseline_id、confirmation_id、生成器/提示词版本、manifest、artifact_hashes、相对当前基线状态 |
| Run | id、project_id、操作、起始 revision、模型配置、提示词版本、阶段、预算、状态、错误分类、可续跑点 |
| AuditEvent | 操作者类型、动作、对象、前后版本/哈希、时间、结果、关联 run；不得存密钥/隐藏推理全文 |

逻辑 ID 由服务端分配。模型新增条目使用本响应内唯一 `TMP-*` ID，问题用 `QTMP-*`；入库后分配稳定 ID 并替换所有关联。模型引用既有条目时必须使用上下文给出的 ID，不得重编号已有 REQ/AC。

推荐稳定编号：SRC-0001、EX-0001、REQ-0001、RULE-0001、AC-0001、UI-0001、Q-0001。编号策略可按项目唯一实现，但同一业务身份不可因导出而改变。

## 2. 来源可信度与需求采纳是两个维度

`applies_to`: `as_is`（现状）、`to_be`（目标）、`reference`（竞品/参考）。

`epistemic_status`: `reported`（材料或人员陈述）、`observed`（直接可见观察）、`inferred`（推断）、`proposed`（建议）。模型可以建议分类，系统保留来源。`observed` 不是“业务真相已经得到独立证实”。

`selection_status`: `candidate`、`selected`、`rejected`、`deferred`。人工选择方向只改变选择状态，不自动形成正式批准。

是否属于已确认需求，通过有效 Baseline 和 Confirmation 关系判断，不靠模型返回 `confirmed=true`。这类字段不得出现在模型输出 Schema 中。

例如：截图显示“导出”按钮 → `reference + observed`；“建议本期做导出” → `to_be + proposed`；产品经理选择它 → selected；明确范围与验收并在版本确认页确认 → 被纳入新 Baseline。四步不可合并。

## 3. 状态与门禁

业务工作模式 `explore/converge` 与材料/任务状态分离，不用一个巨大状态字段表示全部含义。

草稿可经历 collecting、exploring、clarifying、review_ready；基线 confirmation 独立记录，确认后仍可启动新草稿。导出状态 derived_current/derived_historical/invalid 与基线分离。

单次任务：queued → running → succeeded/partial/awaiting_user/failed/cancelled/paused_budget。每个来源与每个生成阶段独立记录状态。一个来源失败不能把整项目标成无结果。

关键门禁：

- 模型/材料无权限创建 Confirmation、设置 active_baseline 或发布 Export。
- 模型仅生成候选；服务端验证格式、ID、引用、权限、版本后写草稿。
- 明确“采纳方案”不等于“确认 PRD”；聊天表达可生成待确认动作卡，不能直接执行批准。
- 正式导出只使用不可变基线的同一内容，不混入较新草稿。
- 无效结构输出、来源 ID 越界、跨项目引用、未知 AC、过期 revision、未确认导出均拒绝。
- 阻塞项需要相关业务决定、缩减范围或明确豁免范围；豁免也不能把未知规则写成已知。

## 4. 确认事务

确认 API 接收 project_id、expected_revision、expected_hashes、scope_ids、idempotency_key；actor 从已验证本地会话取得，不相信请求体或模型提供的 actor。

事务内读取最新草稿、PRD 结构、UI 结构与审查结果；重新计算规范化内容哈希；核对 expected_revision/hash；检查范围内阻塞项和引用；创建 Confirmation 与 Baseline；提交。

哈希基于规范化语义 JSON（UTF-8、固定键顺序、固定分隔符、规范化换行、拒绝 NaN），不以易受 ZIP 时间戳影响的 DOCX 二进制哈希代替语义一致性。DOCX/MD 文件另存文件哈希。UI 在确认范围外时使用显式 null，不偷偷省略已确认的 UI 规则。

同一幂等键/同一请求返回同一结果；同一键不同内容拒绝。并发旧页返回版本冲突。历史记录不可修改；人工撤回通过新记录建立关系。

## 5. 提示词装配与数据隔离

每次请求组合：

1. `prompts/00_system.md`。
2. 当前阶段的提示词（不得把所有阶段一起塞入 system）。
3. 服务端生成的可信任务头：stage、project_id、revision、mode、能力、可用来源/条目 ID、剩余预算、允许输出 Schema；文档阶段另带 document_type、profile ID／版本及允许的参考维度。
4. 通过 JSON 序列化的上下文数据：用户消息、必要底稿、未决问题、选定材料片段、阶段目标。全部材料正文视为不可信引用数据。
5. 视觉调用在 user content 中附上真正的图片块，并给出 Source/Excerpt 映射；图片只出现在适配器允许的位置。

不要对原始材料进行模板二次执行。材料中的 JSON、XML 闭合标签或 Markdown 标题不提升权限。分隔符有助阅读但不是安全边界，权限由代码掌握。

发送最小必要上下文，不把全部源文件、历史对话或其它项目混在一起。上下文清单应可审计；摘要不能替代关键规则原文。

## 6. 统一响应外壳

正式 Schema 见 `examples/runtime_response.schema.json`。所有阶段用如下外壳：

```json
{
  "schema_version": "1.1",
  "stage": "clarify",
  "summary": "本轮给产品经理看的简要结论。",
  "proposals": [],
  "questions": [],
  "findings": [],
  "used_source_refs": [],
  "limitations": [],
  "result": {
    "scope_summary": "本期范围仍待明确。",
    "outstanding_decisions": ["确定导出对象范围"],
    "next_focus": "先确定导出当前页还是全部筛选结果"
  }
}
```

输出必须为一个完整 JSON object，不加代码围栏、不混合说明文字。各阶段 `result` 使用 Schema 的 stage-discriminated `oneOf`。JSON 解析通过不等于条目已有效，更不等于业务已批准。

`proposals` 只允许 add/revise 候选；删除通过 change 阶段提议，由人决定。proposals 无确认/发布字段，不能承载数据库路径或任意执行指令。

`used_source_refs` 只能来自本轮实际送入的 Excerpt。每项由 source_id 与 excerpt_id 构成；服务端检查匹配项目、来源哈希和可访问性。来源有效只说明可追溯，不自动证明语义支持，模型评审与人工审查仍要检查“来源是否真支持断言”。

`summary` 是面向用户的理由摘要，不索要、显示或保存隐藏思维链。需要解释时给出结论、关键依据、取舍和不确定项即可。

## 7. 各阶段的 result

| stage | result 内容 | 约束 |
|---|---|---|
| ingest | understanding、material_limits | 读取结果由工具提供，模型只归纳不能自行制造读取成功 |
| vision | observations、unobservable、unreadable | 仅依据实际图片；不推断隐藏后端行为 |
| brainstorm | options、recommended_option_id、recommendation_reason | 方案都是候选；选择权在人 |
| clarify | scope_summary、outstanding_decisions、next_focus | questions 存外壳；默认≤3，不重复已回答项 |
| review | assessment、reviewed_refs、perspectives、required_decisions | assessment 只建议是否准备好真人评审，不代表确认 |
| ui | spec | 使用独立线框 Schema；无任意 HTML/JS |
| prd（文档组织阶段，沿用名称） | document_type、content_profile_id/version、title、sections、coverage、reference_mapping | document_type 为 prd/mrd；按相应内容 profile 组织，不是另建 Agent；最终确认标签由服务端加 |
| handoff | documents、mappings、technical_proposals | 仅从基线生成；技术建议必须显式待开发确认 |
| change | changes、impacted_refs、unimpacted_refs、needs_new_confirmation、unresolved_effects | 影响判断为建议；最终基线关系由程序决定 |

`10_repair.md` 不是新业务阶段。它要求修复原阶段 JSON，stage 不变；最多 2 次，修复也计入调用预算。

## 8. PRD 与交接的一致性实现

不要只靠 LLM“再检查一次”。为 requirement/rule/acceptance 等规范性段落保存 canonical_refs，由服务端把基线规范原文插入文档。模型输出中的规范性 block.text 必须为 null；不能用改写文本覆盖原文。

narrative/UI 建议可由模型组织，须关联需求并标明建议。生成后的语义评审负责发现叙述区偷偷引入额外业务规则；发现冲突就阻止正式交接。引用图校验负责发现漏项，不能宣称自动语义评审是数学证明。

前后端交接中的规范内容通过 canonical_refs 插入；技术提议单独渲染“建议/待开发确认”。验收条件只来自基线 AC，不在交接阶段临时创造已确认 AC。

traceability 至少包含 requirement_id → rule_ids、ac_ids、ui_page_ids、frontend_sections、backend_sections、open_question_ids。映射允许一对多、多对多；没有实际关联的一端写不适用并说明，不硬凑映射。

## 9. UI 结构

`examples/wireframe.schema.json` 限制页面/区域/组件类型与字符串字段。状态使用 normal/loading/empty/error/forbidden；按场景提供适用状态，不适用项明确说明。

组件支持纯文本描述、字段、表格列与模拟行、白名单模拟 interaction。不得返回 HTML、JS、CSS 任意代码或远程依赖。所有文字在 DOM 中按文本渲染，不能使用未经处理的 innerHTML。

组件 ref_ids 可关联当前候选/已知 REQ，不存在的关联拒绝；只有纯装饰内容可为空。后端检查布局层数、组件数量、内容长度、列行匹配和目标组件 ID。示例数据始终标“模拟”。

## 10. 建议 API 表面

由 Codex 输出实际 OpenAPI，与下述逻辑能力保持一致；路径可调整，但不增加模型权限。

- 项目：create/list/get/update；读取当前底稿和历史版本。
- 材料：上传/录入 URL、读取/重试解析、取引用片段、排除材料。
- 会话/动作：提交用户消息与显式 mode/action，查询 Run、取消、续跑。
- 方案与草稿：人工选择方案、编辑条目、回答问题；所有写操作带 expected_revision。
- 预览：UI spec、PRD 结构、MD/DOCX 草稿。
- 确认：独立 confirmation endpoint；会话权限、CSRF、版本与幂等核验。
- 导出：对指定已确认 baseline 创建 Export、读取 manifest、下载受控包。
- 模型：安全保存非敏感配置、输入/清除密钥、连接与能力测试；不返回密钥。

错误码最低包括 CONFIG_MISSING、AUTH_FAILED、MODEL_UNSUPPORTED、VISION_UNAVAILABLE、SOURCE_FAILED、OUTPUT_EMPTY、OUTPUT_TRUNCATED、SCHEMA_INVALID、REFERENCE_INVALID、STALE_REVISION、CONFIRMATION_REQUIRED、SEMANTIC_BLOCKED、BUDGET_EXHAUSTED、CANCELLED。

## 11. 测试隔离

合成测试中的自动点击确认，用于验证机制，不是产品经理对实际业务 PRD 的批准。测试数据库/凭据/端口与正常运行隔离。

Fixture/Mock 与 Live Provider 必须由明确配置切换并在 UI/报告显著标识。生产运行不得在模型失败时自动回退为 fixture 答案。


## 12. v1.1 章节参考、文档结构和版本兼容

两份原件作为 content_reference 保存，具体映射来自 `references/content_profiles.json`。参考 profile 不是业务基线：其章节建议可被合并／裁剪，不应被写成全部必做需求；文件里的例子不可自动进入 REQ／RULE。

文档生成沿用 stage=prd，result.document_type=prd|mrd；每次只组织一种文档。同一轮用户选择两者时应用分别调度，锁定相同底稿版本并记录部分完成情况，不在一次无界输出中混合两份文档。

result.content_profile_id/version 必须与可信任务头一致；例：user-prd-reference／1.1、user-mrd-reference／1.1。模型返回不存在 profile 或错误文档类型应拒绝。允许系统明确启用并登记 builtin 回退 profile，但必须标记未按本次参考生成，不能仅让模型随意命名新 profile 绕过检查。

sections 增加 level（1～5）和 parent_section_id（顶层为 null）。section_id 对应 PRD-* 或 MRD-*；由应用检查类型前缀、唯一性、父节点存在、无循环、层级递增、显示顺序，映射到真实 Word 标题层级。编号与目录由渲染器根据当前章节产生，不引用原模板静态页码。

reference_mapping 每条包括 profile_section_id、scope_ref（文档级为 null，逐功能维度为合法功能条目 ID）、disposition、output_section_ids、reason。只允许 included／merged／not_applicable／pending。included 要有实际章节；merged 和 pending 还需理由且关联实际章节；not_applicable 要有理由且不应假造输出章节。

应用另外检查：选定 profile 中 mapping_required=true 的维度都有合理处置；repeat_per_function=true 的维度按本期功能实例化；没有函数／非适用时记录实际理由。模型的完整 PRD／MRD 成文结果不能因为 Schema 允许空映射就宣称已核验全覆盖。阶段性／合成协议样例的空映射只能标记局部，不能直接正式交接。

覆盖检查是内容和引用检查，不是“每个标题必须出现”的格式硬套。未决而相关的维度应 pending；业务阻塞与非阻塞仍按原门禁处理。所有相关 REQ／RULE／AC 在成文前后保持对应，不让一次合并丢掉后半条规则。

DocumentArtifact 的同一内容对象生成 MD／DOCX。两种 document_type 使用相同业务源版本；MRD 可以摘要侧重，但出现的规范条款不得与 PRD 不同。任何正式批准状态由真实记录决定，不能把 MRD 模板里的审核栏填成 PRD 批准人的“正式 MRD 审核”。

图示绑定由应用单独维护 document_asset_bindings：section_id、来源 UI／材料 ID、原始内容版本、asset_hash、caption、模拟／待确认状态。模型不能通过叙述字段提供任意本地路径或远程 URL 让导出器读取。只有实际可用的原型／图示才能嵌入并声明已展示；缺图时标记未提供或选合适的文字表达，不能虚构图片已生成。

本次运行时响应外壳升级为 schema_version=1.1。wireframe.schema.json 和其 spec.schema_version 仍为 1.0；不能全局替换所有版本值。新程序对已存在的 1.0 记录应显式读取兼容或生成可追踪的新版本，不改写旧 payload／确认／基线／文件哈希；不通过重算历史值伪造迁移前后没有变化。

仅字体／分页等样式变化使用新的 style_version 和导出记录，业务原文保持；若章节重组或文字变化影响 prd_content_hash，按原有差异预览与确认流程处理。可以复用已有业务答案，但不能跳过实际变更内容的确认。增加 MRD 导出不赋予模型审批权限。

确认事务必须显式读取 document_type=prd 的内容对象及其哈希，不得简单取“最新一份文档”而误用 MRD 代替 PRD。仅有 MRD 草稿时，正式前后端交接仍返回 CONFIRMATION_REQUIRED；可下载带草稿状态的 MRD，不伪造有效基线。
