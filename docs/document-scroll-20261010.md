# 正式文档滚动修复（2026-10-10）

原因：固定高度工作台内，展开的正式文档 details 按正文自然高度撑开，父级禁止溢出；正文未形成有界滚动区。阻塞列表的 overscroll containment 还会吞掉页面滚轮。

展开正式文档时，工作台成为主滚动区域，项目栏保持可见；正文、文档工具栏、页底操作连续滚动。目录定位、阅读位置和阶段位置改用实际滚动元素。隐藏正文不记录其他阶段的滚动。返回完善需求后恢复表内滚动和固定确认栏。

验证：

- 原构建浏览器复现，连续滚轮仍无法到 PRD 末尾。
- 修复构建在 1440×900、1280×720、900×720 上验证长 PRD/MRD 末节与页底操作可达、目录跳转、文档切换、正式文档/正式确认往返、手动收起展开、返回完善需求表内滚动与固定确认栏。
- 浏览器使用隔离 ASGI 和合成储能云长文档，只允许 GET；未启动第二服务，0 模型请求，夹具数据前后相同，没有录屏。
- TypeScript/Vite 构建通过，文档任务、阅读投影和导航相关回归 11 passed。
- 证据：`evidence/document-scroll-20261010/fixed-browser-final/result.json`。这些检查验证页面交互，不代表模型内容或正式业务验收。

复跑：

```powershell
.venv/Scripts/python.exe -X utf8 tests/check_document_scroll_browser.py --static-build evidence/document-scroll-20261010/build-check --output evidence/document-scroll-20261010/recheck-new
```

更新后保存当前输入再刷新原工作台即可加载新版本；不自动刷新用户页面。
