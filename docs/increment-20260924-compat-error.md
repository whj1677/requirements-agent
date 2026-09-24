# 版本兼容标识与接口错误呈现增量报告

日期：2026-09-24。起点：`codex/ui-02-guided-workflow` / `53cc9f2`，开始时工作区仅有既有的 `.env.example` 删除与未跟踪 `原型/`（均保持原状，未提交）。本轮只处理 `docs/ui02-readonly-walkthrough-20260924.md` 第二章实测发现的两项问题：R1 服务曾为"新前端 + 旧后端进程"混合版本且无任何提示；R2 接口 404（FastAPI 默认 `{"detail":"Not Found"}`）被前端原样渲染为裸文本，无恢复入口。

本轮提交文件：后端 `app/contracts.py`、`app/main.py`；前端 `web/src/api.ts`、`web/src/main.tsx`、`web/src/workspace.tsx`；脚本 `scripts/serve.py`、`scripts/start.ps1`、`scripts/check_compat_browser.py`；测试 `tests/test_runtime_info.py`；说明 `README.md`、`docs/ui02-readonly-walkthrough-20260924.md`（仅令牌脱敏）、本报告。方向选择、条目采纳、确认门禁与业务预算逻辑未改动；空项目按钮置灰问题本轮不碰。

## 变更与依据

- R1 后端能力标识：`app/contracts.py` 新增排序元组 `API_CAPABILITIES`（以实际路由为准，`actions` 为关键项）。`app/main.py` 在 `create_app` 启动时对 `app/*.py` 内容计算一次 sha256 短指纹，连同 `started_at`、`capabilities` 存入 `app.state.runtime`；`GET /api/session` 响应纯附加 `backend` 字段（旧前端忽略即兼容）。指纹在进程启动时固定，严禁每次请求重读磁盘或读取 git HEAD；不凭 dist mtime 判断兼容。
- R1 工程提示：`scripts/serve.py` 启动时打印 runtime_id、磁盘上的前端构建资源文件名（明确标注是磁盘文件而非浏览器已加载版本）及两行说明（后端代码变更需重启服务；前端更新后已打开标签页需重新加载，刷新前保留未发送输入）。`scripts/start.ps1` 的 PID 冲突报错改为明确说明端口占用含义并指向 `scripts/diagnose.ps1`，保持不自动结束进程。
- R1 前端兼容状态：`api.ts` 的 `SessionInfo` 增加可选 `backend`；`main.tsx` 在会话恢复与登录后计算兼容状态（含全部必需能力=['actions']→`ok`；缺必需能力→`incompatible`；无 `backend` 字段→`unverified`），传入 Workspace。`unverified` 显示一次性可关闭提示"兼容性未核实"，不编造已确认诊断；`incompatible` 显示持续横幅说明缺少的能力与恢复方法，并暂停发起动作。
- R2 API 客户端：`api.ts` 的 `ApiError` 扩展为 `{status, code, kind, path}`；fetch TypeError→`network`；响应先读 text 再尝试 JSON.parse，解析失败绝不掩盖原 HTTP 状态；FastAPI 风格 `{"detail}` 不再原样渲染（`code='ROUTE_OR_OBJECT'`）；非 JSON/空体给含状态码的安全通用消息。导出 `categorizeError`：`404+后端code→not-found`、`404+无code→route-missing`、`network`、`text/empty→non-json`、`401→session`、`403→forbidden`、`409→conflict`、`5xx→server`。
- R2 工作区：`refresh()` 拆分核心（`GET /projects/{id}`）与辅助（runs/actions/artifacts 各自独立 catch，失败保留上一次数据并置 stale 标记，绝不用空数组顶替）。核心读取失败且无项目内容→可恢复中文错误页（按类别区分标题）+「重新加载」「返回项目列表」（401 另有「重新登录」）+ 折叠诊断（HTTP 状态/接口路径/错误码/kind，不含密钥、Cookie、请求体或响应原文）。已有内容时刷新或轮询失败→保留显示与未发送输入，横幅"最新状态尚未取得：{安全原因}"+「重试」。incompatible 或任务列表从未加载成功且加载失败时，主动作按钮禁用并附原因（`planAction` 内同样有守卫）；查看、返回、重试保持可用。403/409 不自动改参重放，401 不统一写成"后端过旧"。
- 文档：`README.md` 新增"版本与重启"小节；走查报告第二章的本机临时令牌字符串已替换为「（本机临时令牌，按任务要求不写入仓库）」，仅改令牌本身，原始观察与证据等级保留。

## 本轮验证

以下命令均在仓库根目录运行（前端构建在 `web/`），使用合成凭据与隔离数据目录，未触碰 8765 真实服务，真实模型请求为 0。

| 检查 | 退出码 | 实际结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m pytest tests/test_runtime_info.py -q` | 0 | 4 项通过（session 含 backend.capabilities 含 'actions'；runtime_id 启动时固定、响应直接来自 app.state；响应不含令牌/Cookie；能力元组有序），2 条依赖弃用警告 |
| `cd web && npm run build`（含 `tsc`） | 0 | TypeScript 与 Vite 构建成功，产物 `dist/assets/index-D3c1DBvZ.js` |
| `.venv\Scripts\python.exe scripts/check_compat_browser.py` | 0 | 7 个合成故障注入场景全部 VERIFIED，model_calls=0，无页面 JS 错误 |
| `.venv\Scripts\python.exe -m pytest tests -q --junitxml=evidence/runtime/compat-final-pytest.xml` | 0 | 112 项离线工程测试通过，0 失败 |
| `.venv\Scripts\python.exe scripts/check_browser.py` | 0 | 原有合成浏览器场景（含 403/409 确认流）全部 ENGINEERING_PASS |
| `.venv\Scripts\python.exe scripts/check_ui02_browser.py` | 0 | UI-02 两项目合成场景通过，证据 `evidence/runtime/ui02/efca52d0` |
| `RA_PORT=18765 .venv\Scripts\python.exe scripts/serve.py`（临时端口冒烟后手动结束） | 0 | 启动输出打印 runtime_id（与浏览器实测 `ee9eb6d634f1c04a` 一致）、dist 资源文件名与两行重启/重载说明；未触碰 8765 |
| `git diff --check` | 0 | 无空白错误 |

`check_compat_browser.py` 场景（证据 `evidence/runtime/compat/a194486d/`，含故障页与恢复后截图）：

- a. `/actions` 与 `/artifacts` 返回 FastAPI 风格 404（模拟旧后端）：页面无裸 "Not Found"，显示中文"部分状态未能更新"，主动作按钮禁用并附原因，模型任务 0；关闭故障后点「重试」恢复正常，项目数不变（不重建项目）。截图 `a-legacy-fault.png`、`a-recovered.png`。
- b. `GET /projects/{pid}` 返回后端风格 404（`{'code':'NOT_FOUND'}`）：显示"项目不存在或已被删除"，页面不含"前后端版本可能不兼容"字样。截图 `b-project-not-found.png`。
- c. HTML 500、空 500 错误体：显示"服务返回了无法识别的内容"，不渲染原始 HTML 或解析异常；`page.route` abort 模拟网络失败：已有内容与未发送输入保留，出现"最新状态尚未取得：无法连接服务"。截图 `c-html-500.png`、`c-empty-500.png`、`c-network-preserved.png`。
- d. 409（STALE_REVISION）与 403（CSRF_DENIED）响应形状不变、单次请求不重放；清 Cookie 后进入项目显示"登录已失效，请重新登录"，「重新登录」按钮回到登录页。截图 `d-session-expired.png`、`d-back-to-login.png`。
- e. 正常兼容：无 incompatible/unverified 横幅，runtime_id 两次请求一致。截图 `e-normal.png`。
- f. 会话响应无 `backend` 字段→一次性可关闭"兼容性未核实"提示且动作不阻断；`capabilities` 缺 `actions`→持续横幅并禁用主动作按钮。截图 `f-unverified.png`、`f-incompatible.png`。

前端对"旧式无 backend 字段会话响应"的处理无法由 pytest 覆盖（纯前端逻辑），依据为场景 f 的浏览器实测与 `main.tsx` 的 `compatOf` 实现。

## 限制

- 8765 真实服务未重启、未改动；其维护（含以新构建重启）需用户另行许可。走查报告第一章"标签页实际加载版本无法从服务端证实"的核对点保持原状。
- unverified 状态只表示"兼容性未核实"，不代表已确认不兼容；runtime_id 是源码指纹而非语义版本，文档提交/重新构建等不影响它，但任何 `app/*.py` 内容变化都会改变它。
- 真实模型质量、真人体验与首审保持 NOT_RUN；本轮全部为合成凭据、合成端点与故障注入的工程验证，不改变任何验收状态。

# 追加：任务列表"先成功后失败"时新动作未暂停（2026-09-24 第二轮）

起点：同分支 `1bdb339`。缺陷：阻塞条件 `stale.includes('任务列表') && !tasksLoaded` 只覆盖"从未加载成功"；`/actions` 首次成功后转为失败时，旧任务记录仍在界面，但当前任务状态已无法核实，发起动作路径却被判定为可执行。

## 复现与修复

- 回归先行：在 `scripts/check_compat_browser.py` 追加场景组 g（注入合成任务记录，故障开关控制 `/actions`）。修复前运行退出码 1，失败于 `get_by_text("任务状态读取失败")` 等待超时（`scripts/check_compat_browser.py` 场景 A，日志见本机 `/tmp/compat-prefix.log`）：先成功后失败时既无提示也不暂停。
- 最小修复（仅 `web/src/workspace.tsx`，`api.ts` 未动）：阻塞条件改为 `stale.includes('任务列表')`（不再要求 `!tasksLoaded`），提示区分两个事实——曾经取得过任务数据（旧记录保留可供查看，提示"任务状态读取失败，当前状态待刷新"）与从未取得（提示"任务状态尚未取得"）；读取失败从不清空 tasks 数组。守卫同时落在实际发起路径：`planAction`（原有）与 `startAction`（新增提交前最终核对，弹窗打开后发生的读取失败同样生效，拒绝时原因显示在弹窗内，不创建业务任务）。重试成功取得任务状态后按实际状态恢复；有活动任务时的既有串行规则（plan.missing / busy）未改动。

## 本轮验证

命令均在仓库根目录运行（前端构建在 `web/`），合成凭据与隔离数据目录，未触碰 8765 与其提供的 `web/dist`（前端构建输出到 `evidence/runtime/compat-web/`，经 `RA_COMPAT_DIST` 提供给被测服务）。

| 检查 | 退出码 | 实际结果 |
|---|---|---|
| 修复前 `python scripts/check_compat_browser.py`（场景 g） | 1 | 按预期检出缺陷：场景 A 等待"任务状态读取失败"超时 |
| `cd web && npx tsc && npx vite build --outDir dist-verify`（验证后删除） | 0 | 类型检查与构建通过，无 dist 变更、无残留产物 |
| `cd web && npx vite build --outDir ../evidence/runtime/compat-web --emptyOutDir` | 0 | 临时构建 `index-CzcPKpUe.js`；`git status` 无 dist 变更 |
| `RA_COMPAT_DIST=<临时构建> python scripts/check_compat_browser.py` | 0 | 10 个场景全部 VERIFIED（原 7 个无回归 + 新增 g 组 3 个），model_calls=0，无页面 JS 错误 |
| `python -m pytest tests -q --junitxml=evidence/runtime/compat-stale-tasks-pytest.xml` | 0 | 112 项通过，0 失败（含 tests/test_runtime_info.py） |
| `python scripts/check_browser.py` | 0 | 全部 ENGINEERING_PASS，证据 `evidence/runtime/ui02-regression/browser-2f4f186c` |
| `python scripts/check_ui02_browser.py` | 0 | 通过，证据 `evidence/runtime/ui02/fdd548da` |
| `git diff --check` | 0 | 无空白错误 |

场景 g（证据 `evidence/runtime/compat/b97da84f/`）：

- A 先成功后失败：旧任务记录"合成历史任务"仍可见，出现"部分状态未能更新（任务列表…）"与"任务状态读取失败，当前状态待刷新…发起动作已暂停"，主动作按钮禁用；经未置灰入口（根据回答更新理解）发起时被守卫拦截，连 plan 预检请求都未发出（截图 `g-stale-blocked.png`）。
- B 重试成功后按实际状态恢复：横幅消失、按钮恢复、可再次打开预检弹窗；项目数不变（不重建项目）、revision 保持 0（无业务写入）、未发送输入保留（截图 `g-recovered.png`）。
- C 弹窗已打开时状态失效：弹窗为模态，侧栏真实点击被遮罩拦截，改用 DOM 派发走真实 React 处理器触发刷新；失效后点击"授权本次范围并运行"被提交前最终核对阻止，原因显示在弹窗内、弹窗保持打开、业务任务数不变、模型请求 0（截图 `g-dialog-blocked.png`）。

## 限制

- `scripts/check_compat_browser.py` 的 g 场景针对含修复的前端；不设 `RA_COMPAT_DIST` 运行时仍服务 `web/dist` 旧构建，g 场景会按设计失败——8765 换用新构建需用户授权重启后另行验证。
- 后端授权、预算、串行执行、确认门禁未改动；`api.ts` 未改动。真实模型请求全程为 0，真人体验与首审保持 NOT_RUN。
