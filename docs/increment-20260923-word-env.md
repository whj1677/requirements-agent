# Word 图片绑定与项目环境密钥增量报告

日期：2026-09-23。起点：`main` / `294e6c6ca7a261eed20d3c73ae9cb402ddad996c`，开始时无未提交改动。本轮只修改 Word 插图绑定和项目根目录 `.env` 的模型密钥读取。原始两份 Word 与 `content_profiles.json` 未改动，原件 SHA-256 与 profile 记录均一致。

本轮提交文件共 21 个：配置与说明 `.env.example`、`.gitignore`、`README.md`、`requirements.lock.txt`、`docs/acceptance-report.md`、本报告；后端 `app/config.py`、`app/provider.py`、`app/main.py`、`app/exports.py`；界面 `web/src/main.tsx`；脚本 `scripts/start.ps1`、`scripts/diagnose.ps1`、`scripts/check_browser.py`、`scripts/generate_export_sample.py`；测试 `tests/helpers.py`、`tests/test_env_config.py`、`tests/test_exports.py`、`tests/test_extended.py`、`tests/test_http_journey.py`、`tests/test_runtime.py`。

## 变更与复现

- 修复前，用合法 MRD 测试样例把 `MRD-5.1.F.3 页面布局` 映射到 `MRD-02 交互与功能说明`，把 `MRD-5.1.F.4 功能点描述` 映射到 `MRD-01`；新测试实际失败，绑定记录把图片放在 `MRD-01`。
- 现在 MRD 插图只跟随 `MRD-5.1.F.3`，PRD 仍跟随 `PRD-4.F.1`。只有 `included` 或 `merged` 且有该功能 `scope_ref` 的实际章节可插图；页面的 `requirement_refs` 必须匹配。合并到自定义章节不依赖标题。无 UI 或 UI 对应旧底稿时，双格式正文提示未插图。图片与说明在 Word 中保持同页。
- 使用原有同一内容对象生成 MD 和 DOCX；`reference_mapping.json` 保留维度处置，`document_asset_bindings.json` 记录实际 `section_id`、`ui_page_id`、UI/图片哈希。测试覆盖两功能不同页面、不串图、PRD 回归、无 UI、旧 UI、裁剪及重要未知。
- 后端 `app/config.py` 用 `python-dotenv==1.2.3` 从代码确定的项目根目录显式读取 `.env`，不搜索工作目录或父目录，不执行文件内容。Provider 在创建时加载一次；新进程重启读取修改。优先级：显式临时 Key → 有效进程变量 → 有效 `.env` 值 → 未配置。空值和明显占位符不触发模型请求。
- 页面只显示是否配置、来源、变量名和绑定接收端，不返回 Key。已保存的非敏感模型绑定在启动时恢复；不同服务使用各自变量名，同一接收端可显式复用。清除临时 Key 后回退到环境来源。密钥本机明文保存于 `.env`，不是加密存储；模型材料外发仍需原有范围与接收端授权。

## 本轮验证

以下命令均在仓库根目录运行，除前端构建在 `web/`；退出码均为 0，修复前复现命令除外。

| 检查 | 实际结果 |
|---|---|
| 修复前 `python -m pytest tests/test_exports.py::test_mrd_picture_follows_layout_mapping_not_description -q` | 退出码 1；实际图片绑定 `MRD-01`，预期 `MRD-02` |
| `python -m pytest tests/test_env_config.py tests/test_exports.py -q` | 退出码 0；16 项通过，2 条依赖弃用警告 |
| `python -m pytest tests -q --junitxml=evidence/pytest-increment-final-20260923.xml` | 退出码 0；83 项离线工程测试通过，0 失败，2 条依赖弃用警告 |
| `npm.cmd run build` | 退出码 0；TypeScript 和 Vite 构建成功 |
| `python scripts/check_browser.py` | 退出码 0；原有合成浏览器场景与下载检查成功，无页面 JS 错误 |
| 隔离目录中的合成 `.env` + Chromium 页面检查 | 退出码 0；页面显示来源、变量名和接收端，页面 HTML 不含合成密钥值；未发模型请求 |
| `scripts/diagnose.ps1` | 退出码 0；显示项目 `.env` 路径、存在状态及脱敏模型状态；本机根目录 `.env` 当前不存在 |
| `git check-ignore -v .env .env.local` 与 `git ls-files --error-unmatch .env` | `.env` / `.env.local` 被忽略；`.env` 未被 Git 跟踪；`.env.example` 保留可提交 |

测试使用合成凭据和合成 HTTP 模型端点。新进程从非项目工作目录读取指定根目录 `.env` 两次，修改文件后取得新值；父目录和当前目录诱饵未被读取。进程变量、临时 Key、模型名称切换、独立接收端绑定、缺失与占位符、导出/备份不含合成 Key 均有回归。

## 应用导出与逐页检查

运行 `python scripts/generate_export_sample.py`，通过应用的 `document_files` 导出路径生成合成 PRD/MRD，存放于 `evidence/runtime/word-increment-57f6b580b36c42c5/`。该目录是本机测试产物，Git 忽略；没有覆盖之前的 Word 样例。MRD 样例显式把页面布局和功能点描述映射到不同章节。

样例绑定记录：PRD 为 `PRD-01` / `UI-0001`；MRD 为 `MRD-02` / `UI-0001`。MRD `reference_mapping.json` 中页面布局指向 `MRD-02`，功能点描述指向 `MRD-01`。MD 的图片位于相同章节标题区间，DOCX 图片位于对应章节段落内，图片资产哈希与绑定记录一致；规范性需求、规则及验收原文在两种格式均保留。

标准 `render_docx.py` 实际尝试因本机 PATH 没有 LibreOffice `soffice.exe` 退出码 1。随后用现有 `scripts/render_word.ps1` 的 Microsoft Word 页面表示渲染，PRD 2 页、MRD 2 页，命令均退出码 0。四页 PNG 已逐页查看：中文、图片、标题和页脚可读，无裁切、重叠或图片说明跨页。图片仍是合成低保真页面，不是业务系统截图。

## 限制

真实 DeepSeek 调用、其它真实服务方消费、27 次冻结 EV 与真人产品经理首审均未执行。`.env` 中存在 Key 不代表已授权付费评测或材料外发。本轮结果只证明上述局部工程行为，不将完整 V1 标记为产品验收通过。
