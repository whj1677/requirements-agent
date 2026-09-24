# UI-02 回归测试报告

- 执行日期：2026-09-24（Asia/Shanghai）
- 被测提交：`14425cae5294f079ef523c46f317d5c8cd236d75`
- 分支：`codex/ui-02-guided-workflow`
- 数据范围：隔离的本机合成项目与 loopback 模型夹具；未调用外部模型。

| 检查 | 命令 | 结果 | 退出码 |
|---|---|---:|---:|
| 常规工作台浏览器回归 | `.venv\Scripts\python.exe scripts\check_browser.py` | 工程断言通过；确认 403/409 保留输入，旧文档读取自身快照，重命名空值校验，无 Key 时不发起模型请求；导出生成完成，Word 渲染标记为待检查 | 0 |
| UI-02 合成场景浏览器回归 | `.venv\Scripts\python.exe scripts\check_ui02_browser.py` | 两类合成场景运行完成；资料用途、错误恢复、方案方向选择及原型操作路径有断言 | 0 |
| 前端生产构建 | `npm run build`（目录：`web/`） | TypeScript 检查及 Vite 构建成功 | 0 |
| 完整 Python 测试集 | `.venv\Scripts\python.exe -m pytest tests -q` | `125 passed, 2 warnings` | 0 |
| Git 差异空白检查 | `git diff --cached --check` | 无空白错误 | 0 |

## 结果边界

这些结果证明合成环境下的工程回归与前端构建已执行，不代表真实模型验收、真人业务首审或生产业务验收。两条浏览器脚本运行时生成的本机截图和数据库留在忽略的 `evidence/` 目录，没有纳入公开仓库。PRD 导出在浏览器检查中生成成功；该轮脚本没有完成 Word 页面渲染的视觉检查，因此该项不作通过声明。完整真人试跑记录与调用证据保留在本机，没有上传。
