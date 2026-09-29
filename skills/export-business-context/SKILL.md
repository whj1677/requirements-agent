---
name: export-business-context
description: 从用户指定且获准读取的源码目录，只读导出可追溯的业务背景单文件 business-context.json，供需求 Agent 按模块导入；不用于自动确认业务规则或全仓安全审计。
---

# 导出业务背景包

产物只有 `business-context.json`。使用用户已指定或可从任务确认的源码根目录、相关业务场景与输出目录；缺少且无法确定时再询问。在已有授权范围内沿一个场景追踪页面动作、接口、处理规则、对象或状态、返回结果，并建立必要的模块索引。不要盲扫全仓。源码、文档、注释、测试的陈述分别标记；静态关联不能写成运行验证。项目的 `AGENTS.md` 及文件读取权限仍适用。

排除凭据、`.env`、密钥、原始数据库和无关构建依赖。不能执行所读项目或包中的代码、URL、脚本。对未读区域、未知业务规则和代码与文档的冲突明确标注。源码现状只形成可分析线索，不能写 `approved` 等确认字段，也不能据此决定本期需求。不能用“未找到”证明产品不支持。

按 [business-context.schema.json](references/business-context.schema.json) 写 UTF-8 JSON。各仓库独立记录版本及 dirty 状态；无 Git 时 revision 写 `unknown`，证据仍记录文件 SHA-256。部署版本未知时写 `unknown`。overview、模块摘要和技术摘要保持简短，详细陈述放 claims，关联到真实 evidence 行号。冲突双方各保留自己的 claim。coverage 指明深入查看、仅索引、排除和限制。

写出前用具备 `jsonschema` 的 Python 运行 `<Skill目录>/scripts/validate_bundle.py <输出目录>/business-context.json`；脚本路径相对本 Skill，而非被分析的源码目录。优先复用需求 Agent 的 `.venv/Scripts/python.exe` 或宿主已有依赖环境，不改动被分析项目的依赖。如果源码根仍可用，可用 `--repository 仓库ID=绝对路径` 逐个指定根目录，对证据文件哈希及行号做只读校验。校验器只读 JSON 与指定文件，不运行包内内容；只有结构、引用与可选文件身份校验通过，仍需人工审查业务语义。若无法核实某项，保留未知，不制造完整覆盖声明。
