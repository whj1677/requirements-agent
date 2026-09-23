# 需求 Agent 实施与验证报告

日期：2026-09-23。以下记录来自原开发工作区；文中的 `evidence/` 为本机生成的验证产物，公开仓库不包含，克隆后需重新运行相应检查。

同日 Word 图片绑定与 `.env` 密钥读取的增量验证见 `docs/increment-20260923-word-env.md`；下方保留原轮次记录，不追溯改写历史测试结果。

当前交付为可运行的本地应用与离线工程验证成果；**不是 DONE，不代表完整 V1 产品验收通过**。真实模型评测及真人首审未执行。AT 的语义预期仍按原包保持，不因工程测试通过而改写为已验收。

## 已实现范围

FastAPI/Python 核心、React/TypeScript 中文工作台、SQLite 版本快照、项目与来源管理、公开网页受控读取、文档与像素图片输入、兼容 Chat Completions 调用、预算/修复/取消、探索与澄清、人工候选采纳、受约束页面预览、同源 PRD/MRD MD/DOCX、参考章节映射、指定内容哈希确认、基线与中性研发交接包。

常规启动不含测试模型。工程测试中的合成 HTTP 服务只存在于 `tests/`；运行时真实调用失败时不会回退到示例答案。

## 阶段证据

| 阶段 | 本轮工程结果 | 证据 |
|---|---|---|
| M0 | 检查空工作区；保留原包；记录架构、范围与停止条件；原始 Word SHA-256 一致 | `docs/implementation.md`、`docs/requirement-index.json`、原始包 |
| M1 | 项目、SQLite、会话与模型配置、文本分析调用；无 Key 不生成答案；重开存储恢复 | `tests/test_runtime.py`、`tests/test_http_journey.py` |
| M2 | 文档/图片解析；来源逐项失败；图片块真实编码；受控动态 JS 文本读取 | `app/sources.py`、`tests/test_extended.py` |
| M3 | 候选/选择分离；回答持久化与 topic 去重；变更候选不覆盖原条目 | `app/workflow.py`、`app/main.py`、版本快照 |
| M4 | 白名单线框及模拟状态；真实浏览器检查；双文档导出与原生 Word 页面检查 | `evidence/runtime/browser/`、`evidence/runtime/word-v2/` |
| M5 | 哈希、范围、审查与确认事务；幂等与旧页冲突；历史基线和 ZIP manifest | `tests/test_contracts.py`、`tests/test_exports.py` |
| M6 | 故障注入、HTTP 合成全链路、浏览器、启动/停止/诊断；真实评测入口和未执行清单 | `evidence/pytest.xml`、`scripts/evaluate_live.py`、`evidence/live/` |

阶段表说明工程实现与证据位置，不把 M2 的合成图片请求说成真实视觉模型语义验收。

## 实际执行记录

所有下列项目命令的 cwd 均为本工作区，前端命令 cwd 为 `web/`。

| 命令/操作 | 退出码与结果 |
|---|---|
| `.venv\Scripts\python.exe -m pytest tests -q --junitxml=evidence/pytest.xml` | 68 项离线工程测试通过；不是 43 个 AT 全项或 27 次 EV 验收通过 |
| `npm.cmd run build` | 0；TypeScript 与 Vite 构建成功 |
| `npm.cmd audit --json` | 最终 0；已知漏洞 0。期间一次 TLS 网络失败，重试后得到完整审计结果 |
| `python scripts/check_browser.py` | 0；实际 Chromium 页面、线框正常/错误切换、合成确认与下载、无 Key 错误；无页面 JS 异常 |
| `scripts/diagnose.ps1` | 最终 0；Python 3.11.9、SQLite 3.45.1、两参考 profile、数据库 integrity ok、前端存在 |
| 同宿主调用 `scripts/start.ps1 -Port 8878` → 登录/列项目 → `scripts/stop.ps1` | 0；登录 200、项目 200；已停止测试服务 |
| `scripts/evaluate_live.py`（无 execute 参数） | 0；planned=27、executed=0、passed=0、failed=0、not_run=27 |
| 最终回归 `python -m pytest tests -q --junitxml=evidence/pytest-final.xml` | 0；69/69 离线工程测试通过，0 失败，0 跳过；包含新增的逐需求验收关联门禁 |

测试依赖输出两条弃用警告（Starlette TestClient/httpx 和 anyio BlockingPortal），未影响执行结果；未为消除警告升级无关依赖。

Windows 脚本首次在 PowerShell 5 中因无 BOM 的中文编码解析失败，已改为 UTF-8 BOM 并复验。嵌套启动 PowerShell 后捕获全部输出会等候后台解释器，故操作指南改为在当前 PowerShell 中直接调用脚本。停止脚本会核对本项目脚本路径并停止 Windows venv 的实际子解释器，忽略其控制台宿主；不会按进程名批量停止其它 Python。

## Word 验证

两份原件已提取章节正文并核对哈希；未复制原业务、旧作者、公司标识、签名或静态页码。

标准 `render_docx.py` 实际尝试失败：本机没有 PATH 可用的 LibreOffice。随后 Word PDF 导出文件不能被 PDF 解析器读取，根因未确定，未修改任何安全软件或系统配置。采用 Microsoft 文档支持的 [Page.EnhMetaFileBits](https://learn.microsoft.com/en-us/office/vba/api/word.page.enhmetafilebits) 原生页面表示，运行 `scripts/render_word.ps1` 获得逐页 PNG。

首轮检查发现默认 Title 段落蓝色边框和参考处置重复文字；已去除边框并合并相同理由，保留全部维度和机器可读映射。最终 PRD 两页、MRD 两页，四页均已实际查看：中文可读，标题、正文、线框图、页脚和分页无重叠裁切。图像来自对应 UI JSON 的实际浏览器渲染，并记录 UI 哈希与资产哈希。

最终工程样例：`evidence/runtime/word-v2/prd/PRD.docx` 与 `mrd/MRD.docx`；这些是**合成协议/渲染样例**，内容缺口故意可见，不是实际业务 PRD，也不能证明模型已经正确覆盖全部章节。

## 真实模型与真人评审边界

- L1/L2：确定性单元、集成、协议与故障注入已执行；具体测试见 XML，不等于任意输入都安全或语义正确。
- L3：已执行浏览器工程场景及 API+真实 HTTP 合成模型全链路；不是用真实 DeepSeek 完成的用户体验验收。
- L4：DeepSeek、第二家 Provider、EV-01～EV-09 各三次均 **NOT_RUN**。没有使用任何真实 Key，也没有扫描其它工程/浏览器凭据。
- L5：真实产品经理首审 **NOT_RUN**。测试点击确认只验证机制。

`evaluate_live.py --execute --max-calls N --case EV-01 --repetitions 1` 是有预算的真实首阶段评测入口；它记录完整模型请求结果与来源范围，expected/forbidden 不送模型。**它不自动完成全部后续业务决定**：在应用中打开该评测数据目录后继续澄清、选择、生成文档、评审和确认。EV-06 需要先经测试 UI 建立对应冻结合成基线，未建立时明确 BLOCKED_PRECONDITION。不得把入口的一次 intake 成功填成整个 EV 通过。

默认模型为 deepseek-flash。其视觉能力目前按 [DeepSeek 官方模型表](https://api-docs.deepseek.com/quick_start/pricing/) 标为 documented；没有标成真实已验证。视觉设置页可主动发送合成颜色探针，成功也仅证明该探针，不证明需求分析质量。

## 仍待验证及能力限制

- 真实模型的提问价值、冲突识别、方案污染、文档叙述语义与不适用判断，必须使用冻结 EV 加独立人工语义审查验证；程序引用检查不能替代。
- 前后端包目前以共同基线原文及 UI/关联映射作中性交接；没有声称兼容本地 ai-engineer-context 私有 Schema，也未修改已有 Skill。
- 1.0 读取测试覆盖旧快照不重写；没有真实旧版用户数据库可做迁移演练。
- 公开网页动态读取已做合成受控传输验证；任意互联网网站兼容性、恶意文件资源耗尽及完整安全渗透并未宣称通过。
- 真实全流程 AT-36（关闭 Codex 后由产品经理使用）仍待真人执行。

当前可开始本机体验和有预算的真实模型验收；完整 V1 最终验收保持未完成。后续只围绕冻结 AT/EV 修复，不扩大为新平台。
