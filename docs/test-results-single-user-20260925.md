# 单机版修正与验证记录（2026-09-25）

从 `f64e70329e0ecb9e7b5d4aabb538b9f01bcde4d2` 在原分支开发，更新原 PR #2，不合并。工作目录 `D:/01_AI工程/01_工程项目/pm-req`。原 `.env.example` 删除、原型目录及其他未跟踪文件未纳入本轮。8765 服务和其 dist 未改动。

## 修正与产品使用评价

成文输入补充当前活动页面，并将旧提示与当前事实分开；同一条款只完整呈现一次。UI 的外层响应与实际 wireframe 1.1 协议统一，给模型明确的操作连接规则；修复了仅显示“必填”却允许空值、筛选误匹配其它列、取消不确认、失败演示缺失、保存按钮在容器外的问题。

工作台保留既有风格和架构。生成后有直达成果的按钮，技术明细折叠；文档顶部提供下载；方案细节与旧对话折叠；原型操作区不再被来源 ID 和长说明挤占。用正常界面进行了实际新建、方向选择、原型操作、修改与讨论稿查看。评价范围为单机需求讨论与审稿，不是生产业务系统实现或真人审批。

## 实际命令

以下命令均在上述仓库目录运行，npm 命令在其 `web` 子目录运行。全程使用隔离 data，未向真实项目写底稿。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_prd_readability.py tests/test_prd01.py tests/test_prd_contract_consistency.py tests/test_ui01_review_fixes.py -q` | 0 | 26 项通过 |
| `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_preview_usability.py tests/test_ui01_preview.py -q` | 0 | 3 项通过，两类独立原型场景 |
| `.venv/Scripts/python.exe -X utf8 -m pytest -q` | 0 | 152 项通过；2 项依赖弃用警告 |
| `npm run build -- --outDir ../evidence/runtime/single-user-20260925/web/dist` | 0 | TypeScript/Vite 隔离构建，最终 JS `index-D8fHoMY0.js` |
| `.venv/Scripts/python.exe -X utf8 scripts/check_ui02_browser.py --web-dist evidence/runtime/single-user-20260925/web/dist` | 0 | 两类正常浏览器合成 HTTP 场景完成，控制台错误 0；1440/1280/1024/800 宽度 |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/render_word.ps1 -DocumentPath evidence/runtime/single-user-20260925/exports/PRD.docx -OutputDirectory evidence/runtime/single-user-20260925/word-render` | 0 | 餐饮 Word 渲染 12 页 |

中间失败未抹除：成文新回归最初 3 项失败；协议回归发现外层仍限 1.0；完整回归曾因旧版原型描述折叠导致可见性断言失败，修正为保持 1.0 既有显示行为后重跑。没有删除旧断言或重写历史预期。直接 PDF 导出未得到有效 PDF，保留该失败；随后使用已存在的 Word 页面渲染脚本检查。

## 新真实模型验证（与历史账本分开）

全部为本项目 DeepSeek 官方接收端、deepseek-flash；每次 max_tokens=16000，没有自动扩容。共 14 次实际请求，5 次修复。供应商响应 usage 合计：prompt 265961、completion 54286、total 320247；逐次 elapsed_seconds 合计 207.985 秒，只代表请求记录耗时，非端到端人工操作耗时。费用未知。

- 餐饮 v17 一致性副本：一次请求保存讨论稿并导出 Word。原 15 次调用和原项目快照/记录哈希核对未变；pilot-01 7/30、LIVE-01、LIVE-02 均不重写。
- 联系人全新合成项目：理解 1 次、方案 1 次；两个原型任务各失败 3 次，失败响应留存；修正后原型 1 次保存。布局候选 1 次保存并采纳，主 iframe 内容地址当场更新，条目和问题快照不变。
- 联系人第一份 PRD：2 次请求（章节数量错误后修复），保存。补充当前活动原型上下文后的独立任务：1 次保存，8 个正文章节。两份文档版本都保留。
- Codex 通过真实浏览器输入和点击应用。需求理解、方案、原型和文档来自应用的真实模型响应；没有手工写底稿或数据库来制造成功。联系人用预先准备的合成材料；未替真实业务负责人作答、确认或创建基线。

## 产物与证据（仅本机，不随公开仓库提交）

`evidence/runtime/single-user-20260925/` 保存：逐请求安全过滤输出与输入、调用账本、原版本预检、真实浏览器输入、餐饮/联系人 Word 与 Markdown、Word 页面图及版本快照。最终联系人文档为 `DOC-55a6fb742bd14c94`，底稿 v6；另一个历史文档保持原样。`all-new-calls-ledger.json` 为本轮独立逐笔账本。

`evidence/runtime/ui02/1e5aeb3f/` 为最终隔离前端合成浏览器报告和截图，`results.json` 明确标识 OFFLINE_HTTP_UI_VERIFIED，不与外部模型调用混算。所有截图和数据库均未公开上传。

## 产品经理视角的剩余限制

能完成材料→方向→可操作原型→布局修改→讨论稿→Word 查看与导出。读者可以辨别草稿、候选和正式确认；操作失败后保留已有成果。

模型语义仍须评审：可能把选填字段重复标为待确认，重复提出演示触发方式等非必要问题；即使结构有效，也不代表叙述没有歧义。联系人最后成文正确描述当前抽屉并保留历史弹窗说法，仍有部分过度保守的待定表达。未承诺自动生成可直接研发的完整规格。

NOT_RUN：真人业务内容首审、跨机器首次安装、长期压力与多人/公网运行。正式确认、基线及研发交接本轮未替真人执行；已有机制由工程回归覆盖。没有调用模型给自身产物评分。


补充 Word 视觉检查：本机 Office 16 COM 可用，餐饮与联系人各渲染 12 页并查看页面总览。联系人长原型图最初将图注挤到下一页，限高后从同一个已保存文档对象重新导出（未调用模型），第 4 页完整显示原型表格和图注；中文、标题、页脚及分页无主要裁切。最终文件在 `contact-final-layout/PRD.docx`，页面证据在同目录 `word-pages/`。此检查不代表业务内容已由真人批准。
