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

实现冻结为 `753b97ab8103f8ab39621bc069da955531589aa3`，以下验证均使用该应用代码。后续仅补充本记录，不改变候选包的源码身份。

| 检查 | 实际结果 |
| --- | --- |
| 全量离线回归 | **3098 passed，0 failed，0 skipped**，约 12 分 52 秒，3 项既有依赖／报告格式提示。 |
| 实际 Office | 本机 Microsoft Word、Excel、PPT 合成文档读取、Word 内嵌图片、相对目录和精确实例清理通过；本机 Windows PowerShell 有效策略仍为 `Restricted`，未修改。没有真实 WPS 真机验证。 |
| 源码安全边界 | 既有 WPS 实例不接管；只读失败和内层 COM 权限拒绝不产生来源片段；创建时间／映像不符不终止进程；超时仅清理本次核实的实例。 |
| 干净 CI | [40998897742](https://github.com/whj1677/requirements-agent/actions/runs/40998897742) 成功，113 项边界检查通过；1 项需真实 Word 的图片检查未在 CI 执行，已包含在本机通过项。 |
| 字节一致性 | **1689/1689** 包文件 Python／Windows 原生 SHA-256 相同，0 差异；生产校验未移除或放宽。 |
| 实际安装 | 全新安装、同版本重装、中文桌面／开始菜单启动及复用、1.2.10→1.2.11 升级、正常退出和卸载均通过；新版本冻结 EXE 的 `windows_com_runtime` 实际子进程检查通过，旧基准版本不伪称具备该检查。 |
| 数据保留 | 两份安装报告均为 `DIRECT_INSTALLATION_VERIFIED`、清理为 `OWNED_PROCESSES_STOPPED`；合成项目、历史、附件、配置和受保护密钥升级后保持一致，卸载后保留。 |
| 日常数据 | 36 项目、770 修订、2031 记录、1 设置、1 迁移，5 张表全部摘要与开始前一致，数据库完整性 `ok`。本轮未启动、停止或重启日常工作台；本机没有遗留的本次 Office 进程。 |

日志、XML、合成材料及安装报告保留在本机忽略目录 `evidence/native-office-20261030/`，不发布真实业务材料。安装包仍是 WPS 客户复验候选版，不把正常脚本策略下的源码运行、库加载检查或 Microsoft Office 验证替代客户 WPS 的真实内容验收。没有调用付费或真实模型。

## 候选安装器

- 目录：`output/需求Agent-1.2.11-原生COM候选版-20261030/`，包含安装器、SHA256 清单及客户复验说明。
- 文件：`RequirementsAgent-1.2.11-windows-x64-setup.exe`，143,868,287 字节。
- SHA-256：`b12995a3f10b5df922707b3e87d568befb55e9499e85bc6426392d4cdd542a3f`。
- 下载自上述成功 CI 的精确提交工件；下载副本、构建清单和安装报告身份匹配。本地交付的三个文件均经 Python 与 Windows 原生读取核对，0 差异。
- 复核记录：`evidence/native-office-20261030/candidate-verified.json`、`delivery-verified.json` 和 `delivery-native-verification.json`。
- 源码、测试及说明已推送 `codex/native-office-reader-20261030`，未合并 main、创建发布标签或公开 Release；后续说明提交不改变安装器身份。

## 客户复验

升级 1.2.11 候选包后，先保存并自行关闭相应 WPS 应用，在原资料上选择“重新读取”，分别核对 Word、Excel、PPT 的来源片段是否与手动显示一致。新记录应为 `engine=native-com`，不再出现 PowerShell 读取阶段。若仍失败，只需反馈状态、错误码、失败阶段和实际应用，不必外发业务正文。

脚本策略障碍移除后，仍需验证 WPS 版本、COM 接口、企业材料权限及实际内容；乱码、空提取或与人工显示不符都不能算读取成功。
