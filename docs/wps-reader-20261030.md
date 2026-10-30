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

全量回归及候选安装包验证结果在完成后补记。日常工作台本轮不重启；开发源码和候选包不是对客户文件已经读成功的证明。

## 客户复验

1. 保存当前输入、结束任务，并正常退出需求 Agent；安装后从原入口打开。
2. 保存并自行关闭对应的 WPS 应用，在原资料上使用“重新读取”。此操作保留旧来源和历史，不自动将未读取材料当成需求结论。
3. 先检查读取状态和实际应用是否为 WPS 文字，再检查来源片段中的正文、表格是否与 WPS 手动显示一致；只返回空白、乱码或不符文字时，继续判定为未取得正确内容。
4. 若仍失败，反馈状态、错误码、失败阶段、实际应用和退出码即可，不需要外发业务正文。`OFFICE_WORKER_POLICY_BLOCKED` 须由客户管理员确认允许的脚本执行或签名方式；程序不自行更改策略。`OFFICE_PERMISSION_DENIED` 也不会改用其他方式绕过企业限制。

本轮不加 OCR、不自动导出 PDF、不解密或改名，不把“WPS 手动能打开”推导为“允许自动提取或允许外发”。
