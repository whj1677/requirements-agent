# 需求 Agent

Python 核心、中文本地 Web 工作台，面向已有平台的增量需求。当前五步操作见本 README；需求编号方案和原始实施记录见 [PRODUCT-01](docs/PRODUCT-01.md)，该记录中的旧六步/草图内容为历史方案，草图已取消。原始开发规格保留在 `requirements-agent-codex-kit-v1.1/`，旧验收报告保留各自历史边界。

完整操作步骤、合成练习和常见问题见 [使用说明](docs/user-guide.md)。该手册位于源码仓库，适用于当前五步工作台。

本 README 同时说明源码仓库和精简单机包。单机包内可用的命令是 `start-local.cmd`、`scripts/install.ps1`、`scripts/start.ps1`、`scripts/stop.ps1`、`scripts/diagnose.ps1` 和 `scripts/backup.py`；包内附有 `web/dist` 和重建所需的 `web/` 源码。下文指向 `docs/`、`tests/`、浏览器回归及 `scripts/evaluate_live.py` 的链接或命令只适用于源码仓库，精简单机包不包含这些工程资料。实际打包文件与哈希以包内 `release-manifest.json` 为准。

## Windows 使用

在本目录 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
if (-not (Test-Path -LiteralPath .env)) { 'RA_DEEPSEEK_API_KEY=' | Set-Content -LiteralPath .env -Encoding utf8 }
notepad .env
# 在本机编辑器中填写 RA_DEEPSEEK_API_KEY 并保存；不要提交或发送真实 Key
.\scripts\install.ps1
.\scripts\start.ps1
```

首次使用需要本机 Python 3.11+、Node.js/npm，并运行安装脚本；单机包不包含运行时或模型密钥。已有 `.env` 时保留原文件，不重新创建。安装脚本先隔离构建前端；8765 正在运行或存在活动任务时会拒绝更新，需先完成备份并正常停服。**以后只使用本工程根目录这一套工作台，固定入口为 `http://127.0.0.1:8765/`，数据统一保存在根目录 `data/`。** 不再另开端口或从历史交付包运行日常工作台。

日常双击根目录 `start-local.cmd` 即可启动并打开浏览器（本机无令牌模式，仅监听 loopback）。重复启动复用现有服务，不新增实例，也不改变现有登录方式。需要令牌登录时先停止，再运行 `.\scripts\start.ps1 -OpenBrowser`，使用启动脚本显示的本机访问令牌；它不是模型 API Key。两种方式共用同一入口和数据。

`.env` 是本机明文配置文件，不提供加密存储；它已被 Git 忽略。普通启动与诊断不会自动调用付费模型，实际发送材料仍需按项目确认接收端和来源范围。

1. 创建项目，在统一工作区提供本次诉求，并用“添加资料”粘贴文字、上传支持的文档/截图或读取公开 URL。新增材料保存后才是项目来源。
2. “核对现状、价值与改动范围”：确认目标用户、价值、优先级、新增/修改、保持和不涉及范围，逐条处理需求候选。按方向继续讨论不连带采纳条目。
3. “澄清流程与规则”：逐需求核对角色、入口、流程、数据、权限、结果、异常和验收。未决不能假装已解决；明确移出本期须填写理由。
4. “评审 MRD 与 PRD”：分别阅读同源评审稿，规范条款引用底稿原文和版本。未澄清或产物过期时不能下载；旧文档只读，切换文档不调用模型。
5. “确认与交接”：审查当前范围与阻塞，核对指定版本并提交服务端确认。正式确认绑定版本、范围与哈希；成功后生成指定基线交接包。工程/合成测试点击确认不代表真人业务批准。

准备阶段：在“模型设置”核对接收端与密钥状态。默认读取项目 `.env` 的 `RA_DEEPSEEK_API_KEY`；改动后重启应用。普通模型任务发出前在工作台核对材料范围、接收端及总请求上限；新增材料/回答需要核对新增发送范围。图片视觉处理只在相应授权下执行。

DeepSeek 的语义审查默认使用思考模式；“模型设置”可单独关闭，它与整理、澄清和成文阶段的开关分别生效。新配置默认输出上限为 16000 token，已有保存的预算保持不变。审查达到输出上限时会暂停，不采纳不完整结论，也不会自动加预算或重试；可在核对设置后显式发起新任务。思考模式通常耗时和 token 更多，本地证据只保存最终答复和用量，不保存模型思考原文。

取消会停止后续步骤，已发请求仍可能计费。服务重启不自动重放；重新核对后显式发起新任务，已完成的图片分析无需重做。工程诊断入口同样受授权和总预算限制。UI-02 范围与验证见 `docs/UI-02-implementation.md`。

## 停止与诊断

```powershell
.\scripts\stop.ps1
.\scripts\diagnose.ps1
.\.venv\Scripts\python.exe .\scripts\backup.py
```

数据在 `data/`；不要提交该目录。备份复制 SQLite、来源原件、导出包及 `evidence/model-calls`，生成哈希清单并校验关联；存在活动任务或复制期间数据变化时拒绝生成备份，不包含 `.env`。可用 `.\.venv\Scripts\python.exe scripts/backup.py --verify <备份目录>` 再次校验。恢复时先停止服务并校验备份，在隔离目录核对数据库和文件后，保留原 `data/` 供回滚，再恢复数据库及上述三个目录；凭据单独保管，不从备份恢复。仅监听 loopback，不包含外网部署配置。

正常 `stop.ps1` 在检测到活动模型任务或无法确认任务状态时拒绝停止。只有明确接受中断时才使用 `stop.ps1 -Force`；已发送请求仍可能计费，强停也不会结束身份未核实的进程。

## 版本与重启

正式源码分支为 `main`；本次单机本地发布以标签 `v1.1.0-local.20260926` 固定。`codex/ui-01-workbench-prototype`、`codex/ui-02-guided-workflow` 和 `codex/test-records-release-20260926` 是历史开发/记录分支，不作为另一个正式版本。版本来源、受控范围和验证结果见 [Git 归档与正式版本](docs/version-control-20260926.md)。

- 后端代码变更后必须重启服务才生效（`scripts/serve.py` 不使用自动重载）；`GET /api/session` 响应中的 `backend.runtime_id` 是启动时对 `app/` 源码计算的一次性指纹，进程内固定，可用它确认正在运行的后端版本。
- 前端重新构建后，已打开的浏览器标签页需要重新加载才会使用新资源；刷新前请先保留未发送的输入（输入仅保留在当前浏览器会话）。
- 前后端版本不匹配时，工作区会提示缺少的接口能力或"兼容性未核实"，并暂停发起动作；查看、返回与重试不受影响。
- 启动脚本发现本工程已监听 8765 时直接复用；其他进程或另一份工程占用端口时明确报错，不自动换端口、不结束未知进程。底层入口也先占用唯一端口，再加载应用，避免重复启动影响进行中的任务。
- 更新时先备份并运行 `scripts/stop.ps1`，再在本目录更新代码或安装依赖；`scripts/install.ps1` 在隔离目录构建，停服状态下才替换 `web/dist`，并保留上一版前端供回滚。应用代码、`web/dist` 与 `data/` 应按同一版本成套核对和回退，再启动同一个工作台。历史 `evidence/`、交付包和试跑副本仅作证据归档，不再作为另一个日常工作台。验证优先使用隔离数据与 TestClient；浏览器验证使用当前统一入口，完成后保留一个实例。

## 源码仓库工程验证

本节仅适用于包含 `tests/` 和完整 `scripts/` 的源码仓库；精简单机包不提供这些测试或评测入口。`evidence/` 是本机生成的测试与评估数据，不纳入公开仓库；克隆后可用下列命令重新生成。

```powershell
$env:PYTHONUTF8='1'
$env:RA_DATA_DIR=Join-Path (Get-Location) 'evidence/test-import-sandbox'
.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=evidence/pytest.xml
npm --prefix web run build -- --outDir ../evidence/verify-dist
.\.venv\Scripts\python.exe scripts/check_five_step_browser.py --web-dist evidence/verify-dist
.\.venv\Scripts\python.exe scripts/evaluate_live.py
```

最后一条默认只生成未执行清单。真实调用必须显式 `--execute --max-calls N`，并使用本项目明确授权的凭据；先小批试跑，不会自动消费 27 次全量评测。评测器不把 expected/forbidden 发给模型，也不自动判定语义通过。

当前浏览器回归使用临时数据与限时测试服务，结束后关闭，不改日常 `data/` 或交付第二工作台。旧 `check_browser.py` / `check_compat_browser.py` 保留为历史脚本，当前 UI 请使用上述五步脚本。2026-09-25 的诊断修复、真实调用账本与实际验收边界见 [修复验收记录](docs/agent-repair-20260925.md)。

后续单机发布修复、真实场景验收及当前限制见 [单机发布验收](docs/release-readiness-20260925.md)。

## 主要边界

- 主机/Origin/会话/CSRF、JSON Schema、引用与确认事务由服务端执行；模型无法批准或发布。
- 文件原件、基线和历史记录保留；材料不是指令，不执行模型生成代码。
- 公开网页独立于模型权限域，默认拒绝私网地址；动态资源通过应用验证并固定连接 IP，不使用用户浏览器登录态。
- Word / Markdown 使用同一内容对象；规范条款由底稿原文插入。自动语义审查仍需真实模型与真人复核。
- 普通启动没有 Fixture Provider，不会在真实模型失败时偷偷回退到合成答案。
- 上游中性交接与现有 ai-engineer-context 的私有格式适配未验证，未修改已有 Skill。

## 企业保护的 Office 材料

支持 Word（DOCX/DOC）、Excel（XLSX/XLS）、PowerPoint（PPTX/PPT）。优先普通解析；旧格式或普通解析失败时，自动尝试本机已安装的 Microsoft Office 只读提取。其它格式继续沿用现有解析器。更新后的已有失败材料可点击“重新读取（保留原件）”。本机 Office 和允许执行本地脚本的 PowerShell 必须可用；程序不修改执行策略或企业保护设置。

上传成功不等于已读取；图片、图表和其它未取得对象会明确提示“部分读取”。Office 不可用、权限受限和读取失败分别显示；读取不会自动发送给模型。详见 [Office 文件读取限制与验证记录](docs/OFFICE-READING.md)。本轮验证了用户授权的 Word 样例以及三类合成文件，不能外推为支持所有公司的保护格式。
