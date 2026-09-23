# 需求 Agent

Python 核心、中文本地 Web 工作台。原始开发规格保留在 `requirements-agent-codex-kit-v1.1/`；本目录为独立应用实现。实际完成范围与未验证项见 `docs/acceptance-report.md`，不要把合成测试视为真实模型或真人首审。

## Windows 使用

在本目录 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
.\scripts\start.ps1
```

首次使用请先运行安装脚本。打开 `http://127.0.0.1:8765`，使用启动脚本显示的本机访问令牌登录。此令牌不是模型 API Key。

1. 创建项目，进入“材料”粘贴文字或上传 TXT、MD、DOCX、PDF、PNG、JPEG、WebP；公开网页可选择受控动态渲染。
2. 在“模型设置”配置主分析和视觉模型。默认 DeepSeek Flash；Key 仅服务进程内存，重启后重填，或使用项目专用环境变量。更换服务方请使用独立环境变量名，防止密钥跨接收端。
3. 检查材料预览、读取状态与排除项，授权当前接收端和材料范围。新增材料后需重新确认范围。
4. “讨论与澄清”先理解材料，再探索方案、回答问题。模型输出留在候选区；在底稿中明确选择本期范围。选择方向不等于批准 PRD。
5. 生成页面方案及 PRD。按需生成 MRD 或两者，不要求先 MRD。文档参考原 Word 章节维度，未知保持待确认。单独 Markdown 引用图片时请下载“MD 与图片包”。
6. 对当前内容运行多视角审查，再到“确认与交接”核对范围、规则、验收与重要未知。确认绑定底稿、PRD 和 UI 哈希，旧页面无法批准新内容。
7. 从指定已确认基线生成中性前后端、验收与追踪包。历史基线和旧包保持可下载；本包不代表开发、测试或上线验收。

## 停止与诊断

```powershell
.\scripts\stop.ps1
.\scripts\diagnose.ps1
.\.venv\Scripts\python.exe .\scripts\backup.py
```

数据在 `data/`；不要提交该目录。SQLite 备份同时复制来源原件与导出包。恢复时先停止服务，将备份里的数据库、sources、exports 恢复到数据目录；保留恢复前目录供回滚。仅监听 loopback，不包含外网部署配置。

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
