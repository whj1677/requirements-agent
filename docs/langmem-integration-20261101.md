# 项目记忆小范围接入

本功能默认关闭，只对用户显式开启的项目运行。`PATCH /api/projects/{pid}` 接受 `expected_revision` 和 `project_memory_mode: off | langmem`，沿用现有会话、CSRF 与版本检查；项目有排队或运行中的任务时不能切换。保存开关不调用模型，关闭不删除派生快照。旧项目缺字段即关闭，不批改历史项目或模型设置。

## 实际接入及边界

- 固定 LangMem 0.0.30，调用官方 `create_memory_manager` 提取、`create_manage_memory_tool` 管理、`create_search_memory_tool` 回取。原生 `InMemoryStore(index=None)` 只负责本项目与输入快照命名空间，SQLite `records` 的 `project_memory` 类型保存不可变快照，重启后重新装载。没有新增数据库表、迁移原资料或联网 embedding；这是项目快照过滤回取，不是向量语义搜索。
- `WorkflowMemoryModel` 是 `BaseChatModel` 适配器，把 LangMem 实际绑定的工具 schema 转交现有 Provider 的结构化 JSON 接口，再将真实输出还原成 LangChain 工具结果。每个缓存未命中的阶段最多发一次提取请求，不隐式重试；仍在当前任务预算、取消、超时和 ModelCallEvidence 内。没有新的接收端、模型 Key 或独立 HTTP 客户端。LangSmith tracing 在该处理上下文中关闭。
- 记忆只从本轮实际可发送的原片段提取。必须有匹配的项目来源、片段，且 quote 与完整原始 excerpt.text 逐值相等，包括空格和换行；禁止截断子句或概括。LangMem 负责选择哪些完整片段值得回取。SQLite 保留全文见证、范围提示、来源身份/版本与未确认身份；实际输入不重复发送与 statement 相同的 source_context。旧 AI 摘要不作为事实输入。记忆不新增规范条款、不改人工确认、不按上传顺序消解矛盾；当前来源和旧来源仍按原文核对。
- 输入身份覆盖实际来源原文/元数据、当前已保存回答及内部 whole-excerpt-1 策略。旧策略缓存不复用，缓存回取也重新核对全文相等。来源变化、排除或回答变化不能复用旧快照，旧快照仍保留；项目 ID 隔离缓存与检索。没有模型判定的自动删除或覆盖。
- `interview/ingest/clarify/change/review/prd` 在启用项目内消费同一机制。记忆写入现有 `messages[1]` JSON 的 `project_memory`，因此 DSH 的 `current-workspace.json` 与完整 PRD 输入都能实际读取。已有上下文投影先验证还原，再附加并重投影，PRD 规范白名单不变。初稿 coverage/challenge-2 核对仍以完整原始来源检查，不将记忆变成新的事实依据。

## 界面协议

计划可含 `project_memory`：mode、protocol、extract_extra_requests_max（每个缓存未命中阶段最多 1 次）、request_count_scope、stages、store、retrieval。

启用阶段的 `Run.memory_trace` 包含 preparing/ready/failed、consumed、reused、snapshot_id、input_hash、library_version、memory_ids、source_refs、extraction_call_ids、consumed_stages、limitations、entries。没有字段时保持旧界面行为。`ready` 表示准备完成；`consumed=true` 只在本轮生成接口实际返回后记为进入请求输入，不证明业务已确认、已答全部落实或矛盾已解决。失败或取消不自动再次发起模型调用。

## 验证记录

隔离 staged 源码使用真实安装的 LangMem 与实际 DSH SDK，但模型 HTTP 使用合成回包，不能当真实模型语义验收。

受影响检查组初次结果为 260 通过、1 失败，45.63 秒。唯一失败是隔离源码快照遗漏已有 `skills/requirements-material-review/SKILL.md`，尚未进入 DSH 请求；补齐只读原有技能文件后，单独复验真实 SDK 的记忆快照读取测试 1 通过，4.03 秒。没有修改该技能或 DSH 实现。检查包括来源 guards、工作流 Provider 预算/证据、取消、缓存重载/隔离/失效、开关权限/版本/活动任务、上下文投影、聊天摘要及 PRD 相关回归。

最后一次记忆专用复验 13 项通过，5.83 秒，包含实际上下文差异投影和修复请求身份防误标；随后新增有限额度只有 1 次时的停止条件，额度 1/4 两种工作流复验 2 项通过，2.20 秒。检查组有重叠，不相加为独立用例总数。

依赖只安装到试验目录 target；原项目虚拟环境保持。受原依赖锁约束的 dry-run 为 39 个新增包、0 个原包版本变化。LangChain/Trustcall 有旧 API 弃用提示，当前隔离测试通过；锁文件固定本次兼容版本。

本阶段尚未进行真实模型前后对照、完整 PRD 业务验收、日常工作台部署或发布；这些由集成阶段另行记录。模型未访问外部新接收端，未调用真实模型，未迁移日常数据。

官方来源：[LangMem 代码与 API](https://github.com/langchain-ai/langmem)、[PyPI 0.0.30](https://pypi.org/project/langmem/0.0.30/)。

### 完整片段接收边界收紧

集成前审查指出子串匹配可能从“不允许跨项目查询”选出“允许跨项目查询”。已将接收条件收紧为原始片段全文逐值相等，未增加业务词表或额外模型审查层。否定、仅限、条件及首尾空格/换行截断均拒收，每项对应全文正例保留。对外协议和前端字段不变；旧隔离试验快照不作为新策略缓存。

最终收紧版本记忆专项 18 项通过，9.48 秒，使用最终源码；含真实 LangMem、缓存、预算、取消、原来源 guards 及 DSH 实际 SDK 的模拟 HTTP 快照消费。仍非真实模型语义验收。
