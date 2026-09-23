# 提示词加载规则

这些是产品运行时提示词，不是交给 Codex 的开发指令。Codex 启动使用根目录 `02_CODEX_START_PROMPT.md`。

通常一次调用使用 `00_system.md` + 一个 stage 提示词 + 可信任务头 + JSON 序列化上下文 + 当前 stage Schema。不得一轮加载全部阶段，也不得把每个阶段实现成独立自治 Agent。

阶段文件：ingest=01、brainstorm=02、clarify=03、review=04、ui=05、prd=06（文档组织，按 document_type 区分 PRD／MRD）、handoff=07、change=08、vision=09。结构错误时重用原阶段并追加 10_repair.md。

UI/PRD/handoff 的规范性内容必须受底稿版本和程序门禁约束。只修改提示词无法保证批准、来源、版本或安全，不能删除后端检查。

业务素材不进入 system 指令层。使用 JSON 序列化传入用户/资料上下文，图像通过真实多模态 content parts 传递。不要用字符串替换让材料获得模板执行能力。

修改提示词需更新版本与哈希，运行记录保存所用版本；保持旧记录不变。验收必须覆盖至少一个正常和一个违反边界的输入，不能仅检验提示词文件存在。


v1.1 的文档阶段只装配对应 content profile，不把原 Word 的旧业务样例当系统规则。参考配置从 references/content_profiles.json 读取，额外上下文包括文档类型、profile ID／版本和裁剪意图；规范性业务原文仍来自底稿。响应外壳 Schema 1.1 与 wireframe 内部 Schema 1.0 分别处理。新增文档字段不是模型获取审批权限的通道。
