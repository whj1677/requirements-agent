# 2026-10-02 计数备选中的撤回引文边界

本轮正式 8765 的真实 DeepSeek 整理返回提出了正确的复合 emoji 反例和两个一致的计数选项。其中码点选项要求删除或纠正引号内“emoji按一个码点计”的旧表述，却被 `counting_witness` 当成该选项仍在断言旧规则，导致 `SEMANTIC_BLOCKED`。原失败保留，没有改写模型返回、原输入或测试真值。

最小修复只在分析问题的计数备选检查中启用 `alternative=True`：明确删除/纠正动词只作用于其紧邻的实际引文；如果删除/纠正动作被否定，该引文仍参与矛盾检查。其它未被撤回的计数断言逐个检查。“删掉背景颜色”不豁免计数矛盾，前一句撤回引文也不能豁免后一句重新断言。

来源、当前条款和人工回答仍使用严格默认检查。模型提出删除旧句不代表本期已经采纳，原单位候选、对象、来源、位置和人工决定结构不变。没有修改响应 Schema、业务计数规则、审批流程或既有文档。

新增 19 项成对回归：修复前 12 失败 / 7 通过；修复后与已有定义、采纳历史、来源联合绑定、语义反例集合一起为 237 通过。固定原真实返回经公开 `validate_response` 零调用复放由 `SEMANTIC_BLOCKED` 转为 accepted，项目、输出及来源未被改写。这是离线校验结果，不冒充模型重新生成或真实全流程验收。

本机未提交证据位于 `evidence/real-final-round-20261002-2114/`，冻结真值 SHA-256 为 `e66c07631cbe40f95462421187eaf44efce0e9184ae6e775e248266b68d3e537`；失败调用 `CALL-88bcace107cd45c7` 的证据文件 SHA-256 为 `310ab475d7e4ae9ae8a084a4f3a5c3e9071ea6c76acdfccdb1ffdba4370e95f0`。模型原始材料与调用证据不提交 Git。

命令：

```powershell
.venv/Scripts/python.exe -X utf8 evidence/real-final-round-20261002-2114/replay-counter.py before
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_counting_rejected_quotation.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_counting_rejected_quotation.py tests/test_semantic_quality_boundaries.py tests/test_semantic_quality_witnesses.py tests/test_semantic_quality_regressions.py tests/test_counting_adoption_history.py tests/test_counting_definition_classification.py -q --junitxml=evidence/real-final-round-20261002-2114/targeted.xml
.venv/Scripts/python.exe -X utf8 evidence/real-final-round-20261002-2114/replay-counter.py after
```

截至此修复归档，必要全量与修复后真实步骤尚未执行，后续第一轮结果另行记录，不将本次离线修复说明作为最终验收。
