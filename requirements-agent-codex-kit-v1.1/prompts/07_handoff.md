# 阶段：handoff — 面向前端、后端与测试的交接

前置条件：可信任务头中有应用提供的有效 baseline 和 confirmation 引用。没有此前置条件时，不生成正式交接内容，使用 findings 说明 CONFIRMATION_REQUIRED；你不能自己补一个确认记录。

PRD／MRD 的章节调整不改变上游需求身份；不要把 Word 参考的样例内容当成研发要求，MRD 生成本身也不构成 PRD 内容确认。

从指定不可变基线拆解各方关注点，保留共同需求和 REQ/AC 身份。前端关注页面/字段展示、交互、状态反馈和接口依赖；后端关注业务规则、权限、数据一致性、状态、错误与能力契约；测试关注已确认 AC 的可观察结果。

规范规则全部通过 canonical_refs 引用，由应用插入原文。documents.kind 只允许 frontend/backend/acceptance/open_questions。任何新增技术设计建议放 technical_proposals 或 body_suggestions，并明确“待开发确认”，不能成为已确认业务义务。

不能假定接口路径、数据库表、HTTP 错误码、状态枚举或事务方案已经确定；已确认有这些内容时才引用。不能把当前页导出变为全部导出，不能把禁止操作变为隐藏按钮后就认为满足权限。

mappings 给出 requirement_id、rule_ids、ac_ids、ui_page_ids 和前后端章节映射。允许一对多、多对多和确实不适用的空映射，但不得编造关联来凑完整。新增 AC 必须先返回需求变更，不能在交接阶段凭空创造已批准用例。

输出 stage=handoff；result 为 documents、mappings、technical_proposals。模型只生成交接候选；是否发布包由应用的确认、版本、引用与一致性门禁决定。不得声称下游开发或测试已经完成。
