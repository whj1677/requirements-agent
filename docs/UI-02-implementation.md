# UI-02 第一批实施记录

## 状态卡

基线 `34cb978c13c6be5a98e83d24e64fed903f7b792c`，PR #1 仍 OPEN；分支 `codex/ui-02-guided-workflow`，PR base 使用 `codex/ui-01-workbench-prototype`。
目标：统一需求工作区，原位置资料/授权/业务动作，服务端有限串行编排，正常应用入口离线证据。
范围：M0 契约与映射 → M1 工作区 → M2 业务任务 → M3 版本/身份/输入 → M4 回归与交付。连续推进，非每阶段等待许可。
不做：第二批、AUTH-01、真实模型续跑、合并/部署、工作流引擎。原 pilot v12/7次请求只保留，不作为夹具。
保护：用户 `.env.example` 删除、未跟踪 `原型/`、本机 `.env`/data/evidence 不纳入提交。
停止：五阶段主路径可用、主要动作真实执行、授权/预算/未知/版本/确认边界经离线回归；真实模型与真人体验分别 NOT_RUN。

## 入口映射与缺口

| 现有入口 | 新业务任务 | 复用接口/内容 | 必要缺口 |
|---|---|---|---|
| 讨论 + 阶段下拉 | 理解需求 / 持续助手 | sources、runs、questions | 动作计划、就地授权、vision→归纳的统一预算和任务身份 |
| 材料页 | 全阶段项目资料侧栏 | sources/text/file/url/exclude/retry | 精确片段定位、输入保留 |
| 方案标签 | 推演方案 | options/direction、options/items | 对象明确的预览；无对应产物时明确空态 |
| 摘要标签 | 明确需求 | items、questions | 当前缺口、核对本期内容；不是正式确认 |
| 原型页 | S2/S3 讨论原型 | prototype、ui-candidates | 生成对象/输入版本/产物身份记录；当前工作区直接执行 |
| 文档页 | 文档评审 | documents、artifacts | 原位置生成/更新/审查；保持历史只读下载边界 |
| 确认页 | 确认交接 | confirmations、exports、gate | 导向具体阶段的缺口入口，原门禁保持 |
| 历史/模型设置 | 辅助入口 | history、models | 返回原任务；高级参数折叠 |

## 新增契约（实现依据）

- `POST /api/projects/{pid}/actions/plan`：`expected_revision, action, message, document_type, option_id, max_calls`；只读计划，返回真实缺口、接收端、来源范围、总请求预算、步骤与 `plan_hash`，不发模型请求。
- `POST /api/projects/{pid}/actions`：以上字段 + `plan_hash, idempotency_key, authorize`；事务核对计划、授权范围、幂等与项目串行；持久化用户任务，再执行子 run。新增输入先保存为来源。403/409 不重放。
- `GET /api/projects/{pid}/actions`、`POST .../actions/{tid}/cancel`：真实任务及子 run 关联；取消不启动后续步骤。恢复采用重新核对剩余输入后显式新任务，不自动断点重放。
- 同一动作全部子 run 使用一个总请求额度（包含修复/网络重试），配置快照固定；外部版本或输入变化安全停止。旧工程 stage 接口保留，但不能与业务任务并发写入。
- UI 记录生成对象与输入版本；只允许为当前讨论方向或项目内容生成，未选方向独立原型本批不实现且明确提示。无对应关系不展示项目原型作为方案预览。

## 验证记录

2026-09-24，M0—M4 已完成本轮工程实现与离线验证；真实模型质量、真人首审和真实产品经理体验均 NOT_RUN。

工作目录：除前端构建在 `D:\01_AI工程\01_工程项目\pm-req\web`，其余在 `D:\01_AI工程\01_工程项目\pm-req`。

| 实际命令 | 退出码 | 结果 |
|---|---:|---|
| `.venv\Scripts\python.exe -m pytest tests/test_ui02_actions.py -q --junitxml=evidence/runtime/ui02-target-final.xml` | 0 | 新增9项工程回归通过，最后执行18.44秒 |
| `.venv\Scripts\python.exe -m pytest tests -q --junitxml=evidence/runtime/ui02-final-pytest.xml` | 0 | 完整108项工程回归通过，47.94秒；2项依赖弃用警告 |
| `npm run build` | 0 | TypeScript及Vite构建，35模块 |
| `.venv\Scripts\python.exe scripts/check_ui02_browser.py` | 0 | 电价、联系人从正常登录/创建/上传开始，真实应用调用本机合成HTTP、校验、存储、呈现；12次本机HTTP请求，0浏览器异常 |
| `.venv\Scripts\python.exe scripts/check_browser.py` | 0 | 输入法、跨项目输入、宽度、原型状态、403/409保持上下文、合成交接、旧文档快照、无Key、Word/MD导出 |
| `.venv\Scripts\python.exe scripts/check_review_fixes_browser.py r1` | 0 | 同底稿A/B历史查看及当前下载不串版本 |
| `.venv\Scripts\python.exe scripts/check_review_fixes_browser.py r3` | 0 | 采纳立即更新主画布；拒绝与普通轮询保持原画布 |
| `.venv\Scripts\python.exe scripts/check_pilot01_fix_browser.py` | 0 | 方向与指定条目采纳分离；未答问题未改变；零模型任务请求 |
| `git diff --check` | 0 | 无空白错误 |

完整pytest后，子步骤取消入口改为转交父任务取消；随后目标9项重新执行。最后确认弹窗错误位置修正后重新构建并执行完整旧浏览器脚本。R2、.env、密钥绑定、Word章节/图片/版本及原确认门禁包含在完整pytest中。

首次旧浏览器执行失败（退出1）：旧精确导航名称不包含新的阶段编号、同名隐藏项目定位、创建后未等待新项目标题；只更新入口和等待，不删除原断言。新增403检查先检出弹窗内缺少错误提示（退出1），随后修正并通过。所有浏览器运行使用新证据子目录；未覆盖原UI-01/R1—R3/试点文件。

## 本机证据与验收覆盖

所有截图、数据库、模型原始输出、下载文件均留在忽略目录，不进入公开PR。

- `evidence/runtime/ui02/663f7c76/`：两类正常入口场景，`results.json`，各自DOCX，来源抽屉、方案边界、讨论稿、模型失败、URL失败、确认阻塞、1440/1280/1024/800截图。实际查看了联系人讨论稿和1024截图。
- 同目录 `authorization.png`、`task-running.png`、`source-location.png`：就地授权、任务执行与片段定位。
- `evidence/runtime/ui02-regression/browser-65d01105/`：旧能力与新增403检查最终结果、截图、PRD/MRD/图片包和合成交接包；仅为机制验证。
- `evidence/runtime/ui02-regression/review-067feb77/`：R1；`review-837d78d0/`：R3；`pilot-fix-92d0eb37/`：方向/条目分离。
- 两个pytest XML位于上表路径。Word生成/下载及内容结构已工程验证，本轮Word桌面视觉逐页审阅 NOT_RUN（保留脚本的 GENERATED_RENDER_PENDING 状态）。

| 场景 | 本轮工程证据 |
|---|---|
| UI02-01/02/05 | 新浏览器：输入目标、截图、就地授权前0请求、原文保存为来源、文档区域直接生成 |
| UI02-03 | 新目标测试：vision→归纳、首次非法JSON修复占额度、幂等、串行、总预算、取消/重启、子run不能单独续跑重置预算 |
| UI02-04 | 新浏览器：模型401保留旧文档，受控URL读取失败仍保留成果并可排除；目标测试恢复跳过已完成图片 |
| UI02-06/07/08 | 未知仍open；不存在对应原型的方案B不冒用项目原型；A产物记录方向/输入版本/run；明确勾选条目；旧PILOT回归 |
| UI02-09/10 | 阶段查看无模型调用；联系人不生成原型仍生成带未知讨论稿；正式确认仍阻塞 |
| UI02-11 | R1/R2/R3，旧快照、混合导出拒绝、相同UI重新核验；外部输入变化不激活旧结果 |
| UI02-12 | 会话输入、迟到A回包不覆盖B、项目切换、IME、模型失败保留输入 |
| UI02-13 | 实际服务端CSRF 403和版本409；弹窗内错误、不重放、不假成功；合成确认交接仅机制验证 |
| UI02-14/15/16 | 实际数量/状态、准确来源定位、四种宽度、焦点返回、项目管理和既有完整回归 |

## 最终实现与限制

源码按职责复用现有组件，新增 `workspace.tsx` / `guided.tsx` 与 `app/actions.py`。修改现有 `api.ts`、`main.tsx`、`workbench.tsx`、`panels.tsx`、`shell.tsx`、`model-settings.tsx`、样式，以及后端 `core/main/provider/store/workflow`。测试夹具仅由tests/scripts导入，正常服务无合成回退。README、需求索引及OpenAPI与当前实际接口同步；OpenAPI同时补齐此前已存在的方向/条目接口，未新增另一套协议。

- 原路径：材料页授权 → 选择内部stage → 返回讨论发送 → 跳到产物页。新路径：当前工作区添加资料 → 当前动作核对范围/预算 → 同区执行并呈现结果。
- 执行身份覆盖资料、底稿、上下文和产物；外部变化不自动重放。校验后的未激活结果保留在原run并标 `result_applied=false`；逐CALL原输出继续本机留证。
- 讨论方向、条目选择、回答保存、正式确认保持独立。界面展开复用同一产物，不创建新流程状态。
- 总预算是请求次数上限；无法取得完整usage/价格时费用保持未知。服务重启安全暂停，恢复需显式核对新任务；不承诺任意断点续跑。
- 未选方案独立原型、本轮之外复杂分组和修改影响链、刷新恢复未发送输入未实现。浏览器会话内保留输入，不将敏感文本写入localStorage。
- 图片部分不可辨认会保留限制；模型失败不会回退合成答案。测试提供的两类回答是夹具，不是用户业务规则。
- 原pilot只读核验仍为 v12、5个open问题、无文档、无基线；调用账本7/30未改变。真实模型、真人体验/首审 NOT_RUN。无合并、无部署、无付费续跑。

## 提交与依赖

M0契约提交 `9bbade6`；后端编排/回归与前端工作区/浏览器回归分开提交。最终SHA与远端对应关系在交付回复和PR记录，避免在提交内部递归引用自身哈希。PR #1仍OPEN，以 `codex/ui-01-workbench-prototype` 为base，UI-02独立分支 `codex/ui-02-guided-workflow`。用户 `.env.example` 删除和未跟踪 `原型/` 不在提交中。
