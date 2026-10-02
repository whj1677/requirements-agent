# 已答决定与收益前提的有界离线修复（2026-10-02）

本轮起点 `1d1961b0af3cbc33977e358dacf865e0e1b07e73`，分支 `codex/conversational-intake-20261001`；原源码提交 `9e2c1cfce07fb1ffc031d993893cc4f5db195666`，8765 原指纹 `2ac887ab1413b844`。只修已答决定动作边界、修复请求资格/重复结果及收益说明中的既有流程前提。模型请求预算 **0**；真实复验由父独立复核后另行决定，不自动追加。

## 冻结、原失败与离线结果

状态卡 `evidence/offline-decision-flow-20261002-2325/task-freeze.json` SHA256：`344602d597e8bf20e6edf48e66680d6d2f4d06a3591b7d0ae6c6272f035f6b01`。旧 round-freeze `4feebf6e69e6dac9a740ae4a461efd211bf7312a3db7d1394648ecd544fdada0`、旧 oracle `e66c07631cbe40f95462421187eaf44efce0e9184ae6e775e248266b68d3e537` 未改。协调器 `req-quality-fix-closeout-independent-audit-20261002.json`只读复用，原记录及全部旧证据保留。

`freeze-and-replay.py before/after`只严格解码保存的响应、走公开校验/成文路径，不调用模型、不写项目；前后各写独立 JSON，原文件没有覆盖。

| 冻结真实响应 | 原结果 | 修复后离线复放 |
|---|---|---|
| CALL-01e7437a28524036 | 合法 JSON；REFERENCE_INVALID；仍进修复通道 | 同样拒绝；内部 repair_allowed=false |
| CALL-8a5b5216c7bb41a9 | 与第一份解码对象相同；同错误 | 同样拒绝；不允许模型修复 |
| CALL-055ac96eba9a4dba | 与前两份解码对象相同；同错误 | 同样拒绝；不允许模型修复 |
| CALL-a902a10585024fb9 / MRD plan | 成文校验接受无来源的逐项操作收益前提 | SEMANTIC_BLOCKED，定位 sections[2].explanations[0].text |

前三份对象规范化 hash 都为 `f00da5d286579ef217ae0b67e8beddada904e61fd201d0c3c862dcd48aac8fe1`；前两份 final_output UTF-8 SHA256 `f9e85bf582dda29a249e5c8d03315f3c41dc969826b22a25fd19bc0ccc5692d7`，第三份 `481c2d9b63bb94c680eebb5d8a24f574789cd2d369f3b6e4ee10abc8f0699017`。MRD plan 正文 SHA256 `1130f31c740f751e3f86bd526d47e91789e7be554b7575c26c448188cea1235b`。这些是原正文 hash，不是整个 CALL JSON 文件 hash。

反例原输入包含 100 Unicode 码点/emoji 恒为一码点、超时当明确失败；真实问题 `Q-285b7d4a7c7744a8/Q-a2378286a6454466` 已有冻结合成测试回答及出处：100 扩展字素簇，99A+👍🏽允许、100A+👍🏽拒绝；明确失败不写入、超时未知先查原请求。三个真实响应却用 target_question_id 再问这两个决定，decision_points=[]。本轮保留问题、答案和 pending 身份；既不抽取局部候选，也不把失败响应 seed 进项目。未把测试操作者当真实业务 PM。

主输入只给本期同租户批量最多20项，任一项配额不足整批拒绝；没有既有逐项申请流程。MRD 实际 `DOC-de11a734ca904255.MRD-adbe1a74626a.blocks[2]`说“从而减少逐项重复提交的动作量”。新存量检查绑定此 MRD v2、REQ-aa8902ae16654968、该块原句，未指控无同句的 PRD。没有量化证据不否定定性预期收益；真正缺的是此收益前提里的既有逐项动作来源。该历史正文仍原样保留。

## 最小实现与契约

- `app/understanding.py`：从原问题状态派生已答决定、可拆未答及失效问题 ID 清单，完整历史仍发送；澄清 target 只允许明确拆分有效未答问题。已答落实走同 kind revise/answer_refs，仍须人工采纳。失效/超范围回答不能应用；直接采纳入口也先核对有效性再修改状态。
- `app/provider.py`：生成任务头明确上述动作清单，生成用 Question.target_question_id enum 限为 null 或有效未答 ID。公共响应 schema 不变，不删除上下文问题/回答；answer_binding_tasks 复用同一有效回答判定。
- `app/core.py`：Problem 增加仅内部的 model_repair_allowed，默认兼容原调用；HTTP code/message/status 不变。已答重问/失效回答错误标为不可模型修复，仍是 REFERENCE_INVALID。它不是把所有 REFERENCE_INVALID 改成同一类。
- `app/workflow.py`：不可模型修复的动作直接终止，整个响应未应用；可修复结构若重复同一完整对象（或相同完整原始正文）及同一错误，停止下一次修复。原始正文、拒绝、已有有限合法修复均保留；缺响应、截断、脱敏不能用来证明相同输出。没有宽松解析，也不允许格式修复改业务含义。
- `app/semantic_quality.py`：有限提取“减少逐项/手工……动作量/次数”等说明的既有操作前提，绑定该句对象、方式、动作及角色，与肯定既有来源比较。准确假设/待验证方案和定性预期收益仍可表达；本期条款、引用位置、无量化说明不能证明旧流程。语义契约版本11；未改业务规则或正式批准。

## 回归及全量

新增匿名确定性测试不复制模型证据进 Git：`tests/test_decision_workflow_boundaries.py` 17 项；`tests/test_current_workflow_evidence.py`新增26项，共 **43项**。覆盖已答/未答/失效、合理独立新问题、原目标正确落实、错误动作整项拒绝且状态不变；生成 schema/context 分工；相同对象与相同原始错误请求次数；无来源旧流程、有真实旧流程、跨角色/对象、假设、待验证建议和定性收益，摘要与 MRD/PRD说明。已有失败条件/量词/义务、计数及历史时序回归复用。

沿用 `test_answer_binding_repair.py` 的合法窄引用修复，实际 Mock 请求2次成功提出候选；`test_reference_repair.py` 的可证明来源配对例外保留。重复键、截断、缺字段、语义改变负例继续拒绝，不被包装成修复成功。Mock/隔离应用的请求不算真实模型请求。

原三处调用数断言（runtime、document_type_contract、ingest_reliability）从3改为2，原因是本轮明确要求同一输出/同错误不得继续付费重试。缺字段仍 SCHEMA_INVALID、更新失败仍保留原文档/条目/答案/未批准状态，未改变业务期望。没有最终正文或问题真值改写。

测试命令均用 `.venv/Scripts/python.exe -X utf8 -m pytest`：

1. 九文件定向集合（decision_workflow_boundaries、current_workflow_evidence、answer_binding_repair、reference_repair、review_json_integrity、atomic_question_repair、runtime、understanding、semantic_quality_boundaries）`-q --tb=short --junitxml=evidence/offline-decision-flow-20261002-2325/target-tests-2.xml`：实际退出0，242通过；补充2个收益例外及1个原始正文重复后，两重点文件 `target-tests-final.xml`：退出0，76通过。
2. 首次 `tests -q --tb=short --junitxml=evidence/offline-decision-flow-20261002-2325/full-tests.xml`：退出1，1250通过/5skip/2失败，236.00秒。两个失败仅为旧第三次请求计数；原失败JUnit及日志保留。
3. 最终 `tests -q --tb=short --junitxml=evidence/offline-decision-flow-20261002-2325/full-tests-final.xml`：实际退出0，**1252通过/5skip/0失败**，1257个case，236.36秒。两个现有依赖弃用警告，日志为 `full-tests-final.log`；没有重复 Office 或付费批次。

初版新增夹具日志 `before-tests.xml`保留28失败/44通过，其中4项用了子问题不允许的额外字段；修正了夹具结构。`target-tests-1.xml`保留3失败/238通过，三项为测试读取多模态 content 列表时误当字符串；修正取 text。它们不算产品真实失败或模型调用，不据此篡改业务预期。

Office相关实现未变。本轮五项 opt-in 明确 skip；只复用[既有干净检出实际Office证据](clean-release-boundary-20261002.md)，未打开用户 Office 或重复 Office/付费批次。Python 3.11.9；pytest9.1.1/fastapi0.141.1/starlette1.6.0/httpx0.28.1/jsonschema4.26.0/python-docx1.2.0/Pillow12.3.0/openpyxl3.1.5/python-pptx1.0.2，版本及每份测试源码hash在 `tested-source-final.json`。

## 保护、版本与停止条件

`tested-source-final.json`逐文件记录源码、测试及契约材料 hash；原20项目、308 revisions、788 records、settings/migrations各1全部原行hash一致，188个原模型CALL文件及两个历史证据目录逐文件一致，未知work元数据一致，其他受控文件未变。新增真实模型请求 **0**，真实复验 **NOT_RUN**；不推断费用。

新磁盘 app/*.py 指纹 `c6e25f01e6d832a8`。服务与源码对应、提交父链及正式导出门待本轮全量结束后核对并记录；只复用8765及原启动方式，不留下第二工作台。

限制：词法见证只覆盖有限动作/方式/对象形式，不是通用语义证明，复杂指代/别名及跨句前提仍须人工审查。来源匹配证明的是材料中有陈述，不证明实际实现或真实PM批准。模型生成是否遵守新任务约束尚未真实复验，不能把本轮离线通过称全面ready。主项目 needs_changes、未知反馈Q-a9c484eba79746ae及409必须保留；不代填未知，不新建确认/基线。收口后给父离线证据，等待父独立验收再决定后续最小真实步骤；本任务到此停止，不扩业务、新机、安装器、机器消费者或发布验证。
