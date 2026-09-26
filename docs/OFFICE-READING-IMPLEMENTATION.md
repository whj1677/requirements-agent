# Office 材料读取接入记录

日期：2026-09-25。工作线：`codex/ui-02-guided-workflow`，实施前 HEAD：`8fb93e08ade1a7d2fc7bc16be0fd86c194848a4f`。

## 目标和结果

用户在正常工作台导入公司 Word 时，普通 DOCX 解析失败、0 个来源片段。本轮接入 Word、Excel、PowerPoint 的本机读取能力，五步工作流、需求成文、外发授权和业务确认不变。

- DOCX/XLSX/PPTX 先走普通解析；DOC/XLS/PPT 或普通解析不可用时尝试本机 Office 只读提取。
- 来源保留哈希、位置、提取方法和限制；失败不判断“文件损坏”，也不生成假摘录。Office 不可用、权限受限、部分读取分别显示。
- 读取和重新读取均接入真实上传 API；保留旧材料版本。新版本不会自动获得外发授权，也不会自动修改需求或创建确认。
- 本机已授权 Word：真实字节导入、实际浏览器上传和重试均得到 99 个来源片段；正文/表格3193字符，12个内嵌对象未视觉识别。不是完整图文理解。
- 已更新唯一日常入口 8765，并通过原工作台的“重新读取（保留原件）”成功读取此前失败的来源。旧失败记录保留，新记录为部分读取。没有改写旧结果。

## 实现

`app/office_formats.py` 添加普通 Excel/PPT 提取；`app/office.py`、`app/office_worker.ps1` 实现有时限的独立 Office 提取；`app/sources.py` 与 `parse_worker.py` 接入错误分类和来源元数据；`main.py` 保留版本门禁并在线程中执行重读；`actions.py`、`workflow.py` 识别新限制状态。前端只增加格式能力、读取中反馈、读取方式和状态文字。

Office 宏关闭，按只读参数打开，不保存原件，不调用外部链接刷新。Word 的临时链接更新选项在退出时恢复。只关闭本次核实的新实例；超时清理核对 PID、进程名和启动时间。PowerPoint 已在使用时不接管用户实例。脚本遵守所选 PowerShell 的现有执行策略，没有修改执行策略、加密设置或企业权限。

普通解析优先的依赖：`openpyxl==3.1.5`、`python-pptx==1.0.2` 及锁定的传递依赖；Office COM 不增加账号、模型或付费服务。

## 实际验证

下列 Python 命令工作目录均为仓库根目录，npm 命令目录为 `web/`。

| 命令/检查 | 退出码 | 实际结果 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/test_office_sources.py -q`（最初离线轮） | 0 | 19项通过，3项真实Office测试默认跳过 |
| `RA_TEST_LOCAL_OFFICE=1` 后运行同一目标文件（最终轮） | 0 | 23项通过，包含真实Word/Excel/PPT及相对数据目录读取 |
| `.venv/Scripts/python.exe -m pytest tests -q` | 0 | 219项通过、3项Office测试跳过；之后补充的相对路径回归已包含在最终23项中另行通过 |
| `npm run build -- --outDir ../evidence/office-reading/ui-dist` | 0 | TypeScript/Vite隔离构建成功，JS资源 `index-B1ZNDc-E.js` |
| 私有 `verify_protected_upload.py`，原始文件字节经上传API提交 | 0 | 普通解析失败→本机Word成功；99片段、3193字符、输入/来源哈希相同、模型run为0 |
| 实际应用浏览器，隔离8771 | 已执行 | 用户授权Word、合成Excel、合成PPT逐项上传；读取中/部分读取/方式/来源数可见；Word重读产生v2、保留v1及相同来源哈希 |
| 日常8765 | 已执行 | 备份后替换，核对新能力和构建；原失败材料重新读取成功，原失败记录未改 |

过程失败明确保留：无目录限制的 `pytest -q` 收集了本机历史归档，136处模块重名收集错误，退出1/工具返回1；改用现有仓库测试目录 `pytest tests -q`。首轮完整回归有1处视觉阶段状态回归（218通过、1失败、3跳过），修复新状态枚举后完整回归通过。初期Office测试还检出旧PowerShell执行策略限制、错误的窗口句柄假设、相对输入路径；分别使用已允许的PowerShell7、核实新实例PID及空文档集合、绝对路径修正。没有放宽原断言或修改真实文件来通过。

## 运行与证据边界

安装前查询活动模型任务为0，查看当前页面没有未保存输入，在线一致性备份数据库及旧前端/源码后停止8765。复制经过检查的隔离前端，按原本机无令牌模式启动同一入口；没有扩大监听范围，没有更换模型配置。当前前端 `index-B1ZNDc-E.js`，后端运行标识 `c8e2f8465c5314a5`。

本机证据：`evidence/office-reading/protected-agent-result.json`、`browser-imports.png`、`daily-word-reread.png`、隔离浏览器数据、`before-install/`。这些内容被忽略，不公开提交。原失败来源仍在，用户看到旧卡片并不代表新版本仍失败；新卡片有99个片段。后续模型使用范围仍由用户核对，未自动排除或修改旧记录。

未执行：其它公司保护格式、真实受保护Excel/PPT业务样例、旧式Office真实样例、图片OCR/视觉理解、模型语义验收。本轮真实模型请求0；不宣称完整图文读取或业务验收通过。
