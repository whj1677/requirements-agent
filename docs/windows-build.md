# Windows 客户安装包构建

从 1.2.2 起，客户安装包取消设备许可证和激活校验，直接打开固定 8765 工作台。包含 Python、解析依赖、前端、Playwright 驱动与 Chromium；模型 API Key 由客户自行配置。安装步骤见 [安装与模型配置](windows-installation.md)。

## 本轮任务状态卡（2026-09-29）

- 阶段：设备授权移除、构建与安装验收。
- 目标：交付无需设备许可证、包含最新已修复工作台的自包含 Windows 试用安装器。
- 输入：当前 main 源码、锁定依赖、已有 Windows 构建与安装验证流程。
- 交付物：1.2.2 安装器、SHA-256、清单、实际验证记录和随包指南。
- 验收：干净构建；新用户不带许可证直接进入工作台；打包后的解析与内置浏览器可运行；升级和卸载保留用户数据；不携带用户材料、凭据或授权运行组件。
- 边界：保留模型 API Key、本机访问保护、材料外发确认；不增加业务功能，不重新执行付费模型评测，不创建公开 Release。
- 停止条件：上述构建和有限安装验收完成并交付，未覆盖的第二台物理电脑与企业环境另列实际限制。

## 构建入口：干净 Windows CI

`.github/workflows/windows-distribution.yml` 使用 GitHub 托管 Windows 环境，手动触发，不因提交自动运行、不自动创建 Release。源码仓库继续公开；包不包含 .env、客户资料、模型凭据、许可证和签发工具。

1. 将相互依赖的实现、测试、构建脚本及文档提交。
2. 从 Actions 的 **Windows customer distribution** 选择已审核分支手动运行，实际检出提交作为版本事实源。
3. 构建前执行桌面运行和打包边界测试；构建后进行实际安装、EXE 离线 smoke、直接启动、原位重装及卸载验证。
4. 下载 `requirements-agent-windows-<提交SHA>` 工件，核对安装器 SHA-256、`source_commit`、`working_tree_dirty=false`、`native_file_bytes_verified=true`、`device_license_required=false` 和发行标识 `windows-x64-direct`。
5. 构建清单的 `BUILT_NOT_ACCEPTED` 仅说明构建完成；实际安装结论以 `installation-verification.json` 为准。

验证命令：`python scripts/verify_windows_install.py --installer <安装器> --output <新目录>`。它在新用户目录、仅含 Windows 系统目录的 PATH 下运行安装后的 EXE，不依赖主机 Python/Node/浏览器缓存。先执行内置 DOCX/XLSX/PPTX/图片读取与 Chromium smoke，再启动业务 API 创建合成项目，退出、原位重装并读取同一项目，最后卸载并核对数据文件不变。旧许可证哨兵仅验证保留，不具备授权作用。全程模型请求为零。

同 AppId 已安装、输出目录已存在或 8765 已占用时验证器拒绝开始。清理只处理本次创建并核对映像及创建时间的进程；CI 只上传脱敏结果与允许的构建日志，`private/` 不上传。`DIRECT_INSTALLATION_VERIFIED` 是这些实际安装检查的结论，不代表客户完整业务与真实模型语义验收。

旧 `app/activation.py`、`app/licensing.py`、`app/license_trust.py`、原生授权组件与激活页面仅留作源码历史维护，均不被新入口调用或装入新包。构建时显式排除旧模块；升级时只清除程序目录中的旧组件，不动用户目录。

## 固定输入与构建工具

| 项目 | 构建约束 |
| --- | --- |
| 源码 | 干净检出；提交 SHA、输入逐文件哈希及汇总 SHA 写入清单 |
| Python | CI 固定为 3.11.9 x64 |
| Node.js | CI 使用 22 系列，仅用于构建前端 |
| Python 运行依赖 | `requirements.lock.txt` |
| Python 构建工具 | `packaging/requirements-build.txt`，含 PyInstaller 6.21.0 |
| 前端依赖 | `web/package-lock.json`，使用 `npm ci` |
| 内置浏览器 | 与锁定 Playwright 匹配的 headless shell 和 FFmpeg，使用 `playwright install --only-shell chromium` |
| Inno Setup | 6.7.3；官方发布 URL、SHA-256 和 `Pyrsys B.V.` 有效 Authenticode 签名均须匹配 |
| Actions | 固定到官方仓库已核实的完整提交 SHA |

Inno 下载地址为其 [官方 GitHub 发布](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)，校验依据见 [官方签名核对说明](https://jrsoftware.org/isdl-verify.php)。当前钉定的安装器 SHA-256 为：

```text
9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
```

构建产物保留上游许可证和组件元数据，客户包包括离线 HTML 使用说明。当前应用与安装器未做商业代码签名。第三方组件的开源许可证继续随包保留，与已取消的设备授权无关。

## 本机构建命令

只在经过允许、能够向各程序提供一致文件字节的 Windows x64 构建环境执行。不要在运行中的日常工作台目录替换 `web/dist`；本脚本将前端和打包中间结果放入新的指定输出目录。

```powershell
python -m pip install -r requirements.lock.txt -r packaging/requirements-build.txt
npm.cmd --prefix web ci
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\approved-build\browsers'
python -m playwright install --only-shell chromium
python scripts/build_windows.py --output C:\approved-build\release-1.2.2 `
  --iscc 'C:\approved-build\InnoSetup-6.7.3\ISCC.exe' `
  --browser-cache C:\approved-build\browsers
```

输出目录必须尚不存在。正式构建拒绝有未提交或未跟踪修改的源码；仅 QA 临时包可显式添加 `--allow-dirty`，清单会如实标记，不能作为干净提交的发布证据。更新 Inno 或 Actions 时须重新核实来源、版本和校验值，不能静默回退到另一个下载文件。

## 字节一致性门与本机观察

打包器在生成 Inno 安装器之前，对客户包逐文件比较 Python 与 Windows 原生读取计算的 SHA-256。读取不一致或检查未完整执行时停止构建，留下 `native-byte-verification.json`；不替换文件内容、不尝试解密或改名、不修改企业策略。

2026-09-26 本机 QA 观察到同一 `Python-LICENSE.txt`、`python-docx` 默认模板和构建日志，通过 Python 与 Windows .NET 读取时长度、头部和 SHA-256 不同。主管实际运行该次安装版时，默认 DOCX 模板解析失败。这里只记录已观察到的差异，不据此判断保护产品或具体根因；该次本机 QA 包不能交付。干净 CI 必须独立构建并通过字节检查，不能复用该次本机安装包。

## 安装版验收边界

- 新用户直接启动；缺失或损坏旧许可证不影响使用。
- 在相同 Windows 用户下升级、卸载和重装保留项目、用户配置和旧许可证文件；未保存输入和活动任务受到退出保护。
- 从实际 EXE 核对独立解析进程、内置 Chromium、固定 8765 与前端资源；不借用开发环境。
- 清单逐文件哈希、Windows 原生字节检查与干净源码提交一致，不将未跟踪源码混入发布证据。
- 客户自己的 API Key、接收端绑定与材料发送确认继续有效。

Office 只读后备仍依赖客户获准使用的 Microsoft Office 与企业策略。第二台物理电脑、客户受保护材料及真实模型 MRD/PRD 质量需分别记录，不能从安装成功推断。
