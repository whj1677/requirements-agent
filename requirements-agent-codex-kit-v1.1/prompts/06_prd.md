# 阶段：prd — 有界章节建议

只输出可信任务头给出的 plan_version=1 契约，不输出其它阶段 envelope 或最终文档结构。
模型负责标题、章节组织和简短讨论说明；程序负责章节 ID、层级、原文回填、来源、问答、覆盖关系和导出。

可信任务头 plan_contract 提供由当前快照计算的引用清单和经过 Schema 校验的最小结构示例。示例仅说明结构，不是项目答案。
normative_refs 只能使用 normative_item_ids；为空时所有 normative_refs 必须为 []。不重复输出条款文字。
discussion_refs 只能使用 discussion_item_ids。问题 ID、方案 ID、来源 ID 均不能放入这两个字段。
已有回答和未决问题由程序单独保留，不需要重复引用；不引用问题 ID 不等于删除问题。
B 是资料陈述和已有回答；selected 不等于业务负责人批准。答案可能与旧条目不一致，不能偷偷覆盖旧文。
C 是未采纳建议、推断、假设和候选方向。只可列 discussion_refs，保持其身份，不能引用为规范。
D 是未决问题、限制和读取状态。未知不能推定为不适用，不能新增业务决定。

章节参考用户 PRD/MRD Word 的内容维度，可合并、裁剪和改标题，不强制固定章节数。
模板的示例业务、角色、字段、驳回原因必填、旧作者与日期都不是本产品需求。
叙述只能解释组织建议，不承诺新的规则，不把候选改写为确定义务；新的业务决定请在 limitations 中列明。
已有回答的真实性由用户核对，不能自称批准。没有原型时如实说明，不声称已插图。

根节点只有且必须包含 plan_version、title、sections、limitations；plan_version 为 "1"。
每个 section 只有且必须包含 title、normative_refs、discussion_refs、narration。
narration 是章节级必填字符串，无内容时为 ""，不得省略、放到根节点或使用 null。
最多 12 节，建议优先少量合并章节。不要重复问答、条款、来源和参考映射，程序会保留它们。
仅在实际截断时压缩 narration 或合并章节，仍保留全部必填字段和业务含义；不能提高 max_tokens、增加请求次数、删除关键未知或自动采纳。
