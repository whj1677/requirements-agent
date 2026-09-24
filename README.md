# 需求 Agent

Python 核心、中文本地 Web 工作台。原始开发规格保留在 `requirements-agent-codex-kit-v1.1/`；本目录为独立应用实现。实际完成范围与未验证项见 `docs/acceptance-report.md`，不要把合成测试视为真实模型或真人首审。

## Windows 使用

在本目录 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
# 在本机编辑器中填写 RA_DEEPSEEK_API_KEY 并保存；不要提交或发送真实 Key
.\scripts\install.ps1
.\scripts\start.ps1
```

首次使用请先运行安装脚本；已有 `.env` 时保留原文件，不重新复制。打开 `http://127.0.0.1:8765`，使用启动脚本显示的本机访问令牌登录。此令牌仅用于本机登录，不是模型 API Key。

`.env` 是本机明文配置文件，不提供加密存储；它已被 Git 忽略。普通启动与诊断不会自动调用付费模型，实际发送材料仍需按项目确认接收端和来源范围。

1. 创建项目，在统一工作区的助手描述目标，随时用“添加资料”粘贴文字、上传文档/截图或读取公开 URL。输入仅在本次浏览器会话保留；提交任务后新增输入保存为来源。
2. 在“模型设置”核对接收端和密钥状态。默认读取项目 `.env` 的 `RA_DEEPSEEK_API_KEY`，修改后重启应用。临时 Key、进程环境变量、`.env` 依次优先，密钥绑定接收端。
3. 点击“整理当前信息”，就地核对资料范围、实际接收端和整个任务请求上限。待分析图片先经过视觉分析，再进入归纳；修复和重试共用该上限。新增材料/回答需要核对新增发送范围。
4. 按需进入“推演方案”。“按此方向继续讨论”只记录方向；“采纳指定条目”只处理明确勾选的内容。没有对应原型的方案明确显示空态；未选方案独立生成本批不支持。可以跳过多方案和原型。
5. 在“明确需求”核对事实、建议、范围和未知。保存回答不调用模型；“根据回答更新理解”另行核对并运行。阶段导航不代表完成或批准。
6. 在“文档评审”直接生成/更新 PRD，MRD 按需。允许有未知的讨论稿；规范条款仍引用已选底稿原文。章节参考原 Word 的内容维度。历史文档只读；当前导出继续遵守旧快照及图片版本规则。
7. 在“确认交接”审查当前内容，查看真实阻塞，核对指定版本并提交服务端确认。正式确认绑定版本、范围与哈希；成功后生成指定基线交接包。合成测试点击确认不代表真人业务批准。

取消会停止后续步骤，已发请求仍可能计费。服务重启不自动重放；重新核对后显式发起新任务，已完成的图片分析无需重做。工程诊断入口同样受授权和总预算限制。UI-02 范围与验证见 `docs/UI-02-implementation.md`。

## 停止与诊断

```powershell
.\scripts\stop.ps1
.\scripts\diagnose.ps1
.\.venv\Scripts\python.exe .\scripts\backup.py
```

数据在 `data/`；不要提交该目录。SQLite 备份同时复制来源原件与导出包。恢复时先停止服务，将备份里的数据库、sources、exports 恢复到数据目录；保留恢复前目录供回滚。仅监听 loopback，不包含外网部署配置。

## 版本与重启

- 后端代码变更后必须重启服务才生效（`scripts/serve.py` 不使用自动重载）；`GET /api/session` 响应中的 `backend.runtime_id` 是启动时对 `app/` 源码计算的一次性指纹，进程内固定，可用它确认正在运行的后端版本。
- 前端重新构建后，已打开的浏览器标签页需要重新加载才会使用新资源；刷新前请先保留未发送的输入（输入仅保留在当前浏览器会话）。
- 前后端版本不匹配时，工作区会提示缺少的接口能力或"兼容性未核实"，并暂停发起动作；查看、返回与重试不受影响。
- `scripts/start.ps1` 报"端口 8765 可能已被 PID xxx 占用"时，表示 PID 文件指向正在运行的进程，通常是本项目已在运行的服务；先用 `scripts/diagnose.ps1` 检查，确认后手动停止，脚本不会自动结束任何进程。

## 工程验证

`evidence/` 是本机生成的测试与评估数据，不纳入公开仓库；克隆后可用下列命令重新生成。

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=evidence/pytest.xml
.\.venv\Scripts\python.exe scripts/check_browser.py
.\.venv\Scripts\python.exe scripts/evaluate_live.py
```

最后一条默认只生成未执行清单。真实调用必须显式 `--execute --max-calls N`，并使用本项目明确授权的凭据；先小批试跑，不会自动消费 27 次全量评测。评测器不把 expected/forbidden 发给模型，也不自动判定语义通过。

## 主要边界

- 主机/Origin/会话/CSRF、JSON Schema、引用与确认事务由服务端执行；模型无法批准或发布。
- 文件原件、基线和历史记录保留；材料不是指令，不执行模型生成代码。
- 公开网页独立于模型权限域，默认拒绝私网地址；动态资源通过应用验证并固定连接 IP，不使用用户浏览器登录态。
- Word / Markdown 使用同一内容对象；规范条款由底稿原文插入。自动语义审查仍需真实模型与真人复核。
- 普通启动没有 Fixture Provider，不会在真实模型失败时偷偷回退到合成答案。
- 上游中性交接与现有 ai-engineer-context 的私有格式适配未验证，未修改已有 Skill。
