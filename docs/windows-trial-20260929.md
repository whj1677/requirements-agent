# Windows 1.2.2 无设备授权试用包验收（2026-09-29）

## 交付状态与范围

用户要求“先把授权验证功能取消，然后重新打包一次”。设备许可证与激活校验已从客户运行入口移除，Windows 1.2.2 试用包已生成。模型 API Key、本机请求防护、材料外发确认继续保留。未新增真实模型请求，未创建正式发布标签或 GitHub Release。

本轮任务状态卡见 [Windows 构建说明](windows-build.md)。停止条件是本次安装包和有限安装验收完成，不扩展为新一轮业务设计或完整语义验收。

- 最终安装器源码：`959d0e940394ee979e5187550a43f9d50fe0e630`，干净 Windows CI 构建。
- 安装器：`RequirementsAgent-1.2.2-windows-x64-setup.exe`。
- 安装器 SHA-256：`1ceebc6d25a38bc24083a0811c8cd2834b7b6870f5511675833bd44bc9b4e7fc`。
- 可转发 ZIP：`evidence/windows-direct-20260929/RequirementsAgent-1.2.2-windows-x64-trial.zip`，139,807,230 字节。
- ZIP SHA-256：`7aeb209409029df51c35e4c128d4f71f230da3ebe067040807badc61c3efee22`。
- ZIP 只有安装器、`开始试用.html`、`版本与校验.txt`；已检查 CRC 和包内安装器哈希。
- [最终云端构建与安装验证](https://github.com/whj1677/requirements-agent/actions/runs/36541722667)。

## 实现核对

- 客户 EXE 直接加载工作台，不申请、导入或校验设备许可证，不读主板身份。
- 主应用初始化、HTTP 请求、模型请求、文件解析子进程和离线 smoke 均取消设备校验。
- 新的本机生命周期入口保留 Host、Origin、CSRF、实例身份及活动任务/请求保护。左下角“退出工作台”先处理未保存输入，再确认退出。
- 激活旧书签跳转到工作台；旧许可证接口返回 404，旧授权 CLI 不再提供。
- 新包不含授权 DLL、Bridge、激活网页或授权 Python 模块。构建实际检查 PyInstaller 内嵌模块目录，保留第三方组件的开源许可证。
- 升级只清理安装程序目录内的旧授权资源；用户目录、旧许可证、项目和模型配置不删除。
- 旧授权源码与组件单测留在仓库供历史维护，不作为当前运行功能，不进入新安装包。

## 实际验证与归属

| 检查 | 对象与实际结果 |
| --- | --- |
| 本机完整离线回归 | 603 passed、4 skipped，166.42 秒；4 项 opt-in 本机 Office 集成测试未启用。 |
| 受影响回归 | 最后调整后的桌面、打包和安装验证文件共 52 项成功；不是追加到完整回归后计算覆盖总数。 |
| 前端 | TypeScript/Vite 构建成功；安装版网页实际操作与截图已核对。 |
| 最终干净 CI 实装 | 安装后 EXE 在仅含系统目录的 PATH、新用户目录下直接运行；内置 DOCX/XLSX/PPTX/图片子进程、Chromium、前端/会话/项目 API 已执行。 |
| 最终 CI 保全 | 创建合成项目后正常退出、原位重装并读取项目；卸载后数据和旧目录哨兵不变，进程及注册项已清理。 |
| 本机旧版升级 | 先真实安装 1.2.1，再升级本轮功能构建；旧程序授权资源清除，用户哨兵和损坏的旧许可证保留，新版直接运行。 |
| 本机运行依赖 | 实际安装 EXE 的 8 项离线 smoke 成功，1658 个包内文件的 Python 与 Windows 原生读取哈希一致。 |
| 本机网页与重启 | 无许可证首页、模型设置、合成 Key 受保护保存、创建合成项目、未保存输入拦截、保存后退出、重启后同一草稿及 Key 可用。没有点击模型连接测试或发起分析。 |
| 本机卸载与恢复 | 临时安装程序与注册项已删除；独立测试用户数据保留。日常 14 个项目、五张数据库表及 239 个关联文件与一致性备份相同，SQLite 完整性 ok，无活动任务，源码指纹 `6505d86a91340b55` 与运行实例一致。 |

本机网页与升级检查针对第一次构建 `0a7a54d98e87b52fb0dc9a05d3fd07a51da5449e`。随后只更正两份随包说明的模型设置按钮名称，形成最终提交 `959d0e9`。两次构建的功能输入逐文件哈希一致；最终安装器另行完成干净 CI 实装。两个 EXE 不是同一二进制，未把第一次本机结果冒充为最终安装器再次本机执行；差异见 `final-build-comparison.json`。

## 证据位置

本机忽略目录 `evidence/windows-direct-20260929/`：

- `cloud-final/installation-verification/installation-verification.json`：最终 CI 实际执行结果。
- `cloud-final/output/build-result.json`、`native-byte-verification.json`、`application/RequirementsAgent/release-manifest.json`：最终来源与逐文件哈希。
- `local-installation.json`、`installed-native-bytes.json`：本机旧版升级、运行与保全结果。
- `installed-direct-home.png`、`installed-model-settings.png`、`installed-exit-confirmation.png`、`installed-reopened-draft.png`：网页核对。
- `restoration-check.json`：日常数据恢复核对。
- `delivery-verification.json`、`final-build-comparison.json`：交付包校验及两次构建差异。

私有用户目录、测试密钥、模型原始证据和备份未提交 Git、未装入交付包。

## 实际限制及后续试用

- 本轮验证的是授权移除、程序安装与相关运行路径；完整 MRD/PRD 内容质量和客户完整五步业务流程未重测，真实模型请求为 0。
- 未验证另一台物理电脑或客户企业保护 Office 文档；这些环境条件仍需客户试用核对。
- `BACKLOG`：保存草稿后的项目列表阶段提示会显示第二步，当前工作区仍显示第一步“待分析”。这不影响本轮安装/保存/退出，但阶段提示的一致性留待后续体验修正，未静默记为已修复。
- 应用和安装器未做商业代码签名；文件身份以本报告和随包哈希核对。

协作：2 个内部子 Agent（Luna/medium 负责入口审查和桌面回归，Sol/medium 负责安装验证器），3 次任务派发；主管独立检查关键 diff、实际运行、截图、文件哈希和数据保全。实际模型费用不可见，不宣称节省金额。
