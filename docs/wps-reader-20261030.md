# WPS 本机读取适配与客户复验

客户反馈：同一 DOCX 在 WPS 中手动显示正常，需求 Agent 未返回内容；另一个代码 Agent 调用 `Word.Application` 后只取得「显示出v不那么，」等不符文字。后者只能证明自动化接口返回了字符串，不能证明实际正文已正确读取。

## 修改范围

修改前源码 `c1895c14fb9a0858bfb0eedd41084ad89ab9f5f6` 已归档并推送 `codex/archive-pm-req-20261030-wps`。不修改或重读日常项目资料，不修改企业策略，不自动迁移数据。

- 普通 DOCX/XLSX/PPTX 继续优先走内置解析器。需要本机读取时，查询已注册的 WPS／Microsoft Office 自动化接口；有 WPS 原生接口时优先使用。
- 从注册服务器的实际 EXE 名称确定进程和应用标签，避免仅凭 `Word.Application` 就假定启动了 Microsoft Word。支持对应的 `wps/et/wpp` 进程归属检查及本次实例清理。
- WPS 可能共用进程；发现对应应用已运行时，在启动 COM 和改变安全设置之前拒绝接管，提示用户先保存并自行关闭。继续要求新实例、无已打开文档、禁用宏、关闭外链更新及只读打开；不能提供安全设置时停止。
- 读取记录包含实际应用、ProgID、进程名；失败区分接口注册、读取脚本执行和应用打开／提取阶段。脚本未返回 JSON 时保存退出码；明确的 PowerShell 安全拒绝单独报告，不根据通用错误推断未安装或未激活。不输出原始 stderr、账号或原文到诊断记录。
- 读取仍标记为 `partial`，并记录 `manual_open/content_match=not_verified`。人工看到正文、接口返回文字、页面渲染是不同证据。没有新增基于字数或关键词的“乱码检测”，不误拒绝正常的短需求。

WPS 官方资料给出原生应用标识符 [CreateObject](https://open.wps.cn/documents/app-integration-dev/wps365/client/wpsoffice/jsapi/macro-editor-api/function/createobject) 和 [Documents.Open 的只读参数](https://open.wps.cn/documents/app-integration-dev/wps365/client/wpsoffice/jsapi/wps/Documents/member/Open)。文档不代表所有客户 WPS 版本和企业插件都提供相同能力，实际可用性仍以客户接口及结果为准。

## 验证与限制

当前开发电脑只注册了 Microsoft Word／Excel／PowerPoint，没有 WPS。首轮相关测试 43 通过、1 项新增测试因误选禁止脚本执行的旧 PowerShell 失败；测试改为使用应用实际选用的宿主，保留失败记录。复验 **45 项通过**，其中包含真实 Microsoft Office 合成文件读取、合成 COM 注册、WPS 既有进程保护、错误状态和来源重读边界。合成 WPS 验证不等于真实 WPS 验收。

应用实现冻结于 `9fb5c760325db444adb9688cf558cd72ce432c29`，之后仅补测试、升级基准及本记录；`app/` 和 `web/src/` 保持一致。最终候选安装器的构建提交为 `2c93111da4787a0b093cc55268e9ad9a531bfdda`，版本为 **1.2.10**。

| 检查 | 实际结果 |
| --- | --- |
| 全量离线回归 | **3084 passed，0 failed，0 skipped**，约 10 分 53 秒，包含真实 Microsoft Word／Excel／PowerPoint 合成文件读取；3 项依赖或报告格式提示。应用实现保持冻结，此后新增的七个参数场景单独复验。 |
| 三类 WPS 专项 | **16 passed**；覆盖六种扩展名的应用选择和调用参数、文字／表格／演示的既有实例保护、接口不存在及脚本执行失败。通过合成注册和模拟结果验证，不冒充真实 WPS 读取。 |
| 策略拒绝诊断 | 在本机实际禁止脚本执行的 Windows PowerShell 上，仅用合成材料复现，返回 `OFFICE_WORKER_POLICY_BLOCKED`、退出码 1、无正文；未更改执行策略，也未启动 Word。 |
| 前端 | 类型检查、隔离构建及 5 项 Node 前端测试通过；助手入口、附件、本机文件和缺失内容 4 组浏览器流程最终通过。 |
| 候选包构建 | [最终 CI 40993794015](https://github.com/whj1677/requirements-agent/actions/runs/40993794015)：**107 项边界检查通过**，1664/1664 包文件的 Python／Windows 原生 SHA-256 一致。 |
| 实际安装链 | 全新安装、同版本重装、中文桌面／开始菜单启动和复用、**1.2.9→1.2.10** 升级、正常退出及卸载均通过。项目 JSON、历史、附件、配置、受保护密钥保留，两份报告均为 `DIRECT_INSTALLATION_VERIFIED`，清理均为 `OWNED_PROCESSES_STOPPED`。 |
| 日常数据 | 只读复核 36 项目、770 修订、2031 记录、1 设置、1 迁移；5 张表的逐行摘要与开始前一致，数据库完整性 `ok`。本轮未重启日常工作台。 |

失败和复验记录均保留在本机 `evidence/wps-reader-20261030/`：

- 附件浏览器首轮在手机尺寸恢复项目后，项目抽屉仍展开而遮挡输入区；旧构建同样复现。只修正该测试的导航操作，补齐关闭抽屉，保留原输入框命中和发送断言；复验通过，未改产品布局。
- 首轮探索 CI `40993200422` 使用旧 1.2.8 基准，安装、升级后的数据和快捷方式检查通过，但最后卸载等待超时；清理成功，超时原因未证实，该轮不作为通过或交付依据。最终按用户确认的现用 1.2.9 基准独立执行上述验收；不据此宣布 1.2.8 直升验收通过。
- 连接器文件下载途中断开，残缺副本校验不符，未用于交付；从 GitHub 同一精确工件重新下载并复核后才可交付。

日常工作台没有重启或部署本候选代码。本机没有 WPS，客户的 Word、Excel、PPT 真实文件均为 **待复验**；开发源码、合成测试和候选安装链通过，不能证明企业保护文件已正确读取。未调用真实模型或 OCR，未向外部服务发送客户原件。

## 本地候选交付

- 目录：`output/需求Agent-1.2.10-WPS兼容候选版-20261030/`，包含安装器、`SHA256.json`、`客户复验说明.md`。
- 安装器：`RequirementsAgent-1.2.10-windows-x64-setup.exe`，141,074,750 字节。
- SHA-256：`70b90bf88fe5e1796838f19f4cd3cccada9a3f0711594dbe91487f6df1d598fa`。
- 客户已确认使用上次交付的 1.2.9，最终安装链与该版本对应。
- 从 GitHub 精确工件下载后，安装器哈希与构建清单一致；下载副本及本地交付的三个文件均通过原生字节复核。记录位于 `evidence/wps-reader-20261030/candidate-verified.json`、`delivery-verified.json` 和 `delivery-native-verification.json`。残缺下载保留为 `candidate-incomplete.zip`，没有用于安装或交付。
- 未创建公开 Release、发布标签或合并 main。此后本记录的提交不改变上述候选包源提交身份。

## 客户复验

1. 保存当前输入、结束任务，并正常退出需求 Agent；安装后从原入口打开。
2. 保存并自行关闭对应的 WPS 应用，在原资料上使用“重新读取”。此操作保留旧来源和历史，不自动将未读取材料当成需求结论。
3. 先检查读取状态和实际应用是否为 WPS 文字，再检查来源片段中的正文、表格是否与 WPS 手动显示一致；只返回空白、乱码或不符文字时，继续判定为未取得正确内容。
4. 若仍失败，反馈状态、错误码、失败阶段、实际应用和退出码即可，不需要外发业务正文。`OFFICE_WORKER_POLICY_BLOCKED` 须由客户管理员确认允许的脚本执行或签名方式；程序不自行更改策略。`OFFICE_PERMISSION_DENIED` 也不会改用其他方式绕过企业限制。

本轮不加 OCR、不自动导出 PDF、不解密或改名，不把“WPS 手动能打开”推导为“允许自动提取或允许外发”。
