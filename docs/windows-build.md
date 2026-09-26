# Windows 客户安装包构建

客户版固定为 Windows 10 / 11 x64，包含 Python、解析依赖、前端、Playwright Node 驱动和 Chromium headless shell。客户不安装开发工具，首次使用须导入软件拥有者签发的设备许可证，并自行配置模型 API Key。安装步骤见 [Windows 安装与设备授权](windows-installation.md)。

仓库保持公开。设备授权限制本项目交付的客户安装版；公开源码本身不受这份运行时授权门约束。客户安装包不携带签发私钥、已签发许可证、模型凭据或已有项目数据。

## 推荐构建入口：干净 Windows CI

仓库提供 `.github/workflows/windows-distribution.yml`，在 GitHub 托管的 `windows-latest` 环境构建。流程仅支持手动 `workflow_dispatch`，不会因提交自动运行，也不会自动创建 Release。

1. 将实现、测试、构建脚本、固定公钥模块与文档一并提交。不要提交 `.env`、用户数据、授权申请、客户许可证或签发私钥。
2. 在 GitHub 仓库的 **Actions → Windows customer distribution → Run workflow** 选择已审核分支并启动。任务实际检出的提交作为该包版本事实源。
3. 等待构建结果；成功后下载 `requirements-agent-windows-<提交SHA>` 工件。该工件只包含安装器、文件哈希清单、构建结果与原生字节检查报告。
4. 另一个 `requirements-agent-windows-build-logs-<运行编号>-<尝试编号>` 工件保存白名单内的依赖安装、浏览器下载、Inno 校验、边界测试和构建日志。失败时也尽量保留这些日志。
5. 核对安装器 SHA-256、`source_commit`、`working_tree_dirty=false` 和 `native_file_bytes_verified=true`。构建成功的状态仍为 `BUILT_NOT_ACCEPTED`，须完成下文安装版验收，才能决定对客户交付。

流程只需要仓库读取权限与 GitHub Actions 自带的工件上传能力，无需配置模型 Secret、授权私钥或签发凭据。不要将拥有者的 DPAPI 私钥搬到 CI。正式签发仍在拥有者自己的环境完成。

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

构建产物保留上游许可证和组件元数据，客户包包括离线 HTML 使用说明。当前应用与安装器未做商业代码签名；设备许可证的 RSA 签名用于授权校验，不等同于 Windows 可执行文件的发布者签名。

## 本机构建命令

只在经过允许、能够向各程序提供一致文件字节的 Windows x64 构建环境执行。不要在运行中的日常工作台目录替换 `web/dist`；本脚本将前端和打包中间结果放入新的指定输出目录。

```powershell
python -m pip install -r requirements.lock.txt -r packaging/requirements-build.txt
npm.cmd --prefix web ci
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\approved-build\browsers'
python -m playwright install --only-shell chromium
python scripts/build_windows.py --output C:\approved-build\release-1.2.0 `
  --iscc 'C:\approved-build\InnoSetup-6.7.3\ISCC.exe' `
  --browser-cache C:\approved-build\browsers
```

输出目录必须尚不存在。正式构建拒绝有未提交或未跟踪修改的源码；仅 QA 临时包可显式添加 `--allow-dirty`，清单会如实标记，不能作为干净提交的发布证据。更新 Inno 或 Actions 时须重新核实来源、版本和校验值，不能静默回退到另一个下载文件。

## 字节一致性门与本机观察

打包器在生成 Inno 安装器之前，对客户包逐文件比较 Python 与 Windows 原生读取计算的 SHA-256。读取不一致或检查未完整执行时停止构建，留下 `native-byte-verification.json`；不替换文件内容、不尝试解密或改名、不修改企业策略。

2026-09-26 本机 QA 观察到同一 `Python-LICENSE.txt`、`python-docx` 默认模板和构建日志，通过 Python 与 Windows .NET 读取时长度、头部和 SHA-256 不同。主管实际运行该次安装版时，默认 DOCX 模板解析失败。这里只记录已观察到的差异，不据此判断保护产品或具体根因；该次本机 QA 包不能交付。干净 CI 必须独立构建并通过字节检查，不能复用该次本机安装包。

## 安装版必须另行验收

CI 构建与离线边界测试不替代客户安装版验收。主管至少核对：

- 在隔离 Windows 用户目录安装、升级、卸载和重装；数据、用户配置和授权保持在程序目录之外。
- 无许可证时仅开放网页激活入口，业务启动与诊断入口拒绝运行，未初始化业务数据库。
- 拥有者在 CI 之外审核并签发测试设备许可证；正确导入可使用，伪造、错产品和不匹配设备的授权被拒绝。测试授权不能附到客户安装包。
- 从实际安装 EXE 核对内置浏览器、独立材料解析进程、五步工作台及固定 8765 入口；不依赖构建机 Python、Node 或浏览器缓存。普通格式解析不依赖 Office；企业保护材料须另外验证本机获准的 Office 只读后备路径。
- 对安装后文件复核发布清单；确认没有安装器、驱动或其它原生文件读取差异。
- 界面正常退出、活动任务保护、重复打开，以及安装期间禁止覆盖运行中的程序。

Office 只读后备功能仍依赖客户自己安装并获准使用的 Microsoft Office。模型业务测试需要客户自己的授权与凭据；无模型测试不能写成真实模型语义验收通过。
