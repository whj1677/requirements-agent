# 契约与合成材料说明

- `runtime_response.schema.json`：应用内部完整输出校验 Schema，stage 用 oneOf 约束。
- `wireframe.schema.json`：低保真页面结构 Schema。
- `model_profiles.example.json`：DeepSeek 默认配置及能力状态示例，无真实 Key。
- `response_examples.json`：九个阶段的十份合成协议样例（含按文档类型生成 MRD 的样例），不是模型实测，不是完整业务交付物。
- `wireframe.example.json`：供渲染器开发的合成页面结构。
- `evaluation_cases.json`：九组冻结评测设计；v1.1 仅新增 EV-09，原八组预期保持。Codex 需生成真实合成网页/截图/文档并记录哈希。

Schema 只能约束字段和形状；来源存在、跨项目权限、版本、业务正确性、确认记录与完整交接包由额外程序检查。示例通过 Schema 不代表通过产品验收。

把 evaluation_cases 的 materials/fixture_render 生成物传给被测 Agent；frozen_known、must_remain_unknown、must_ask_or_record、forbidden 只能给评估器，不能泄露给被测模型。

UI component.provisional 不能仅由模型自由决定：程序应根据关联条目的真实确认状态校验；任何候选功能都不得被渲染为“已批准”。draft_revision 必须匹配可信任务头。

同一 action=revise 必须有合法 target_item_id；add 必须为 null。删除变更需 existing target_ref；新增/修改与 proposals 关系需检查。JSON Schema 没有完全表达的关联不应省略。

模型支持列表应在首次真实连接时重新验证；模型别名更新会影响可复现性，应记录请求/响应模型及时间。


v1.1 运行时外壳为 1.1；wireframe 内部结构仍为 1.0。prd 阶段的 result.document_type 区分 PRD／MRD，新增章节层级、内容 profile 和参考映射，详见 04_CONTRACTS.md 第 12 节。两个文档响应样例均是局部协议样例，不是完整需求，也不能用其少量映射证明参考维度全覆盖。

references/content_profiles.json 来源是两份真实原件；profile 中 mapping_required 只要求每个适用维度有处置，不要求全部原章节必须保留或每个维度新增功能。应用仍须校验类型/profile一致、层级图、引用、逐功能实例、未决项和实际内容，不仅检查Schema。
