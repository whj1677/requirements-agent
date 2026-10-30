# 1.2.11 原生 COM 本机读取

客户在 WPS 环境运行 1.2.10 后，读取仍停在 `OFFICE_WORKER_POLICY_BLOCKED`：PowerShell 读取脚本尚未执行，WPS 尚未打开材料。此结果不能证明文档损坏，也不能证明正文可由自动化接口提取。

## 修改与边界

- 修改前提交 `e7fd171b9f6b8b7ae96f2d8e935225021a8be73b` 已归档并推送 `codex/archive-pm-req-20261030-native-com`。
- Word、Excel、PPT 统一使用程序自己的原生 COM 子进程；删除原 `.ps1` 读取组件，超时清理也改为 Windows 进程句柄 API，不执行 PowerShell，不修改执行策略。
- 增加锁定的 Windows 运行依赖 `pywin32==312`，由安装包携带，不运行其全局 post-install 注册操作。普通格式仍优先内置解析，失败后才使用本机已注册且获准使用的 Office／WPS。
- 保留单实例串行、WPS 既有实例拒绝接管、宏禁用、外链更新关闭、只读打开、原件不保存和数据不迁移。权限拒绝仍返回拒绝状态，不换应用或读取方式强行提取。
- 只记录本次确认创建、映像路径及创建时间匹配的进程；清理通过持有的精确句柄执行，不按进程名批量结束程序。
- 保留正文、表格、单元格和公式原文、幻灯片文本及表格，以及 Word 可导出的内嵌图片。图片不自动进入 OCR／模型分析；浮动对象、图表、备注等未提取项继续列明。
- 增加安装后 `windows_com_runtime` 检查：实际运行冻结 EXE 的子进程，加载随包 COM DLL 并初始化／释放 COM，干净 CI 无须安装 Office 也能发现漏打包组件。该检查不等于真实 WPS 读取验收。

Microsoft 说明 [执行策略控制 PowerShell 脚本的加载和运行](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-7.5)。[pywin32 提供 Windows API 与 COM 客户端支持](https://github.com/mhammond/pywin32)。本轮使用正常程序接口，不更改 PowerShell、应用或文件的授权配置；企业若禁止 COM 自动化或材料提取，仍须取得相应授权。

## 验证进度

首轮相关测试 51 通过、1 项相对数据目录回归失败：源码子进程在临时 cwd 下找不到 `app` 模块。已固定子进程工作目录，复验连同新增原生安全和打包验证共 **111 项通过**。其中包含真实 Microsoft Word／Excel／PowerPoint 的合成文件读取，以及 Word 内嵌图片导出；没有 WPS 真机，WPS 保护及参数验证使用合成对象，不能视为客户环境通过。

全量回归、候选包及字节、安装、升级、卸载验证在完成后补记。实现冻结后不继续扩展对象类型或业务功能。日常工作台不重启，日常数据只读复核；客户真实文件不进入 Git、CI 或外部模型。

## 客户复验

升级 1.2.11 候选包后，先保存并自行关闭相应 WPS 应用，在原资料上选择“重新读取”，分别核对 Word、Excel、PPT 的来源片段是否与手动显示一致。新记录应为 `engine=native-com`，不再出现 PowerShell 读取阶段。若仍失败，只需反馈状态、错误码、失败阶段和实际应用，不必外发业务正文。

脚本策略障碍移除后，仍需验证 WPS 版本、COM 接口、企业材料权限及实际内容；乱码、空提取或与人工显示不符都不能算读取成功。
