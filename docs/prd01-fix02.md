# PRD-01-FIX-02：章节建议契约一致性

基于 `7d9b912c7abc01429b87617a02802dadba6275d0`，在现有 `codex/ui-02-guided-workflow` / PR #2 追加。仅修改 PRD 提示、引用校验、修复指令及离线回归；没有 UI、模型、预算或成文架构变更。

原 LIVE-01 的 3 请求、2 修复、无截断、未保存 PRD 保持为失败。原餐饮 15 次请求和 pilot-01 的 7/30 仍分别保留。本轮真实模型请求为 0；没有改写私有输入、输出或账本。

## 三类失败与修正

| 失败形状 | 修正与拒绝边界 |
| --- | --- |
| 根节点多出 narration | 首次提示、任务头与修复均明确根节点四个字段；未知字段仍由原 Schema 拒绝，不清洗后报成功。 |
| 多个 section 缺少 narration | 返回全部实际错误路径；所有章节必须保留字符串，无说明为 `""`，null 仍拒绝。Schema 错误不再触发删字段式压缩。 |
| discussion_refs 放入真实问题 ID | 从快照生成两份条目清单，模型与校验器共用；查实际 questions/options/sources 集合，区分存在但类型错误与未知 ID，不按前缀推断。 |

`PLAN_SCHEMA` 没有放宽。候选条目进入 normative_refs 仍触发 `SEMANTIC_BLOCKED`，不会进入格式修复或自动采纳。只有实际 `OUTPUT_TRUNCATED` 才要求压缩叙述或合并章节，保留必填字段、未知和原预算。

## 实际请求契约的脱敏摘录

以下为程序实际组装字段与指令的摘录；ID 使用隔离合成夹具，不是餐饮输入。

首次系统提示：

> 根节点只有且必须包含 plan_version、title、sections、limitations；plan_version 为 "1"。
> narration 是章节级必填字符串，无内容时为 ""，不得省略、放到根节点或使用 null。

可信任务头 `plan_contract` 含版本 `prd-plan-contract-2`、Schema 验证过的结构示例及实际清单：

```json
{
  "plan_version": "1",
  "title": "需求讨论稿",
  "sections": [{"title": "背景与待定范围", "normative_refs": [], "discussion_refs": [], "narration": ""}],
  "limitations": []
}
```

示例只说明结构，没有成为运行失败的回退产物。`normative_item_ids` 由本期已选 requirement/rule/acceptance 计算；`discussion_item_ids` 保持原规则，允许快照中的全部条目以讨论身份引用。问题、方案、来源均不属于这两类引用。每次修复重用相同快照和契约，原始任务头保持不变。

Schema 修复实际指令摘录：

> 按实际错误位置修复结构或 JSON。根节点不得添加字段；所有 sections 元素必须保留 narration，无需说明时填空字符串，不使用 null。不得静默删除内容后宣称成功。

引用修复实际指令摘录：

> 按错误位置、具体 ID 和实际对象类型修复引用。问题由程序保留，不能放入条目引用；不能修改底稿或问题记录。

错误会携带如 `sections[0].discussion_refs[0] REQ-EXISTS 该ID存在，但属于问题，不是条目`；即使问题使用 REQ 前缀，也按对象集合识别。修复消息同时携带完整允许清单、示例、契约版本和实际错误。每次实际请求仍保存输入哈希、响应、校验结果和 repair_of；失败响应不覆盖。

## 离线证据与边界

新增脱敏失败形状回归，覆盖根字段、多章节缺字段、null/数字、真实问题/方案/来源 ID、未知 ID、空规范白名单、三类 HTTP 失败后修复、契约一致性、证据关联及仅截断压缩。已有候选越界、固定预算、旧文档保护、原文回填、问答与 Markdown/Word 一致性测试继续执行。

空规范白名单场景保留已采纳目标等非规范条目和问题，coverage 为空，可保存讨论稿；没有创建正式基线，确认问题仍存在。受控 HTTP 模型仅证明请求、校验、修复、组装机制，不证明真实模型会遵循指令。

命令与结果（Windows PowerShell）：

- 工作目录 `D:\01_AI工程\01_工程项目\pm-req`：修改前 `.venv/Scripts/python.exe -m pytest tests/test_prd_contract_consistency.py -q`，退出 1，8 项失败。新增接口缺失、错误类型不明确、任务头缺契约均被检出；并非历史真实结果重跑。
- 同目录：`.venv/Scripts/python.exe -m pytest tests/test_prd_contract_consistency.py tests/test_prd01.py -q`，退出 0，19 passed（18.00 秒）。
- 同目录：`.venv/Scripts/python.exe -m pytest -q`，退出 0，144 passed（81.59 秒）。两项均只有既有依赖弃用警告。
- 工作目录 `D:\01_AI工程\01_工程项目\pm-req\web`：`npm run build -- --outDir ../evidence/runtime/prd01-fix02-20260924/web/dist`，退出 0。未覆盖 8765 使用的 dist，未重启服务。

真实模型修复效果为 NOT_RUN；本轮不申请或执行续跑。浏览器检查未执行（没有 UI 修改）；交付后等待新请求契约复核，不自动开展下一轮。
