# 2026-10-02 确认后独立文档下载的有界核验

本轮只补“原合成项目正式确认后，独立 MRD/PRD MD/DOCX 下载的状态是否正确”这一验收。四次下载均 HTTP 200，四份文件明确标注“产品经理指定版本内容确认”。业务内容、文档身份和冻结基线一致。这里的确认来自原隔离合成数据库，真实 PM 业务验收仍为 NOT_RUN。

源码、契约、业务规则和正式数据未修改。本报告是新增说明，不改写旧报告、原下载或先前失败夹具。没有重复全量、Office、时序回归或付费模型批次；上轮已独立核过的 1147 通过 / 0 skip（含五项实际 Office）保留原身份，本轮不将它们计为新执行结果。

## 输入及边界

- 执行源码提交：`7cb5e6fe77d4b8c258d0a9a15a1a9937b8384997`，父提交 `3535156dbf40db09baa62f778a6c783fa51c3ce4`。
- 原干净检出：`D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002`；执行前后 Git 均干净。
- 原隔离数据：该检出的 `evidence/synthetic-export-3535156-attempt3/requirements.sqlite3`。
- 项目：`P-31b6a46a9f394f7d`，项目 revision 13。
- 原确认：`CONF-fea9ac5f5e9e4ad0`，时间 `2026-10-02T12:01:56.923903+00:00`。
- 原活动基线：`BASE-03416e15de9445ad`，基线哈希 `a988b2f66b4c25b4b5304c35833bd9ada23b67d76bfccaac310996b5d6d93c90`。
- 原证据：`evidence/clean-release-boundary-20261002-1947/synthetic-export-attempt3/`，全部保留。
- 协调器原审计：`C:/Users/20240082/Documents/Codex/2026-09-30/task-2/clean-synthetic-export-independent-audit-20261002.json`；只读使用。
- 新证据目录：`evidence/confirmed-export-boundary-20261002-2037/`。没有覆盖旧文件。

开始前正式及隔离数据库均无 queued/running 任务，正式工作台可见聊天、搜索输入长度均为 0。原工作区只有已有未跟踪 `work/`，本轮保留。正式 8765 未重启；验证采用现有环境中的应用工厂和进程内 TestClient，不启动临时 TCP 服务或 Office。

## 执行命令及结果

从原干净检出目录执行以下命令（最后两个参数分别为原工作区和干净检出）：

```powershell
& 'D:/01_AI工程/01_工程项目/pm-req/.venv/Scripts/python.exe' -X utf8 `
  'D:/01_AI工程/01_工程项目/pm-req/evidence/confirmed-export-boundary-20261002-2037/verify-confirmed-downloads.py' `
  'D:/01_AI工程/01_工程项目/pm-req' `
  'D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002'
```

实际退出码 0，日志见新目录的 `execution.log`，结果为 `CONFIRMED_SYNTHETIC_DOWNLOADS_VERIFIED`。依次调用一次合成本机会话登录，再调用原下载 API：

```text
GET /api/projects/P-31b6a46a9f394f7d/documents/mrd/md?document_id=DOC-61fe41a87fd34d29
GET /api/projects/P-31b6a46a9f394f7d/documents/mrd/docx?document_id=DOC-61fe41a87fd34d29
GET /api/projects/P-31b6a46a9f394f7d/documents/prd/md?document_id=DOC-60d5b28adb49458f
GET /api/projects/P-31b6a46a9f394f7d/documents/prd/docx?document_id=DOC-60d5b28adb49458f
```

四次 GET 均 HTTP 200。没有项目创建、确认、交接包生成、规则更新、模型请求、校验绕过或重试。执行器有一次 Starlette TestClient/httpx 弃用提示，不影响本轮退出码；没有因此修改依赖。

详细原始核验收据：[confirmed-downloads.json](../evidence/confirmed-export-boundary-20261002-2037/confirmed-downloads.json)，SHA-256 `2ec6341cafabd1d0a2c25c8c5c76be6a87afa9ddff56fd6e63e8c549831f7704`。另一遍从文件和原始 SQLite 导出行独立核对哈希、DOCX CRC 和正文 XML，结果见同目录 `independent-file-verification.json`；没有再次调用下载接口。

## 四份新文件

MRD 身份 `DOC-61fe41a87fd34d29`；PRD 身份 `DOC-60d5b28adb49458f`。均文档 v1、底稿 v10。MD 状态行分别为“产品经理指定版本内容确认 · MRD/PRD · 文档 v1 · 底稿 v10”；DOCX 封面包含相同信息，按行排列。

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [MRD.md](../evidence/confirmed-export-boundary-20261002-2037/MRD.md) | 2946 | `50e991c444eebf2bcbcc4172a5836ebf9f135ebde7b68fc3ac1c5bc8afc2ec6c` |
| [MRD.docx](../evidence/confirmed-export-boundary-20261002-2037/MRD.docx) | 38789 | `4d9d14eece40158e73f7e93ec43aa6aca811f36100e339b7e2532184b6a397a6` |
| [PRD.md](../evidence/confirmed-export-boundary-20261002-2037/PRD.md) | 2729 | `a86ab1bec0efd2422a67686a045cc2c588861773d9b662207a116721a637e04f` |
| [PRD.docx](../evidence/confirmed-export-boundary-20261002-2037/PRD.docx) | 38754 | `6ac6421ad242ecc4a5136f341f33b5e2d67de60f0c5dc92715bd9c235d9fd4cb` |

新旧 MD 比较仅允许精确替换原封面状态。新旧 DOCX 的所有正文段落、表格相同；移除唯一封面状态段落后，`word/document.xml` 完全相同，两份新 DOCX 的 ZIP CRC 均有效。需求原句、拒绝条件和可复算反例均保留：99 个 A 加 👍🏽 是 101 个 Unicode 码点，应拒绝保存且保留原输入和原记录。状态文字已经变化，DOCX 容器元数据也可能影响文件哈希，不能要求与确认前文件哈希相同。

## 基线、范围和记录的实际对应

当前项目的 `active_baseline_id` 为原 BASE；`hashes(project)` 与该基线冻结哈希完全相同。原确认指向该 BASE，基线反向指向原 CONF。原 `delivery_scope` 的文档 ID、版本、内容哈希及 reader_projection 哈希与当前文档、冻结文档一致；本轮没有重写 scope/manifest 或旧 ZIP。

| 实体 | 版本 | 冻结内容 SHA-256 |
| --- | ---: | --- |
| REQ-CLEAN-0001 | 2 | `9c50cdea6baa55468c38b979b162a97d48dac0996930bc3ddd03eaaa4c082154` |
| RULE-CLEAN-0001 | 1 | `9ba1b6b5b8f7ea7b078ca823424e620dbcbd69aecc6b02306367690cb0dad49e` |
| AC-CLEAN-0001 | 1 | `ce960d2af42d427908195094cc31f9d44cc08288479f18da4580e628a47f8b0b` |

每份文件包含上述实体编号、版本和原句。对应文档内容哈希为 MRD `261ce7b03565cf3e0099f9eff1c21dd2dbda0b57d4032db6fb0e76af283ed393`、PRD `df9bf1971763024f91927e6c256e43181e1cc1d7ef886fb1eab61e36da833170`；与旧 scope、基线冻结值一致。

新增下载行如下，时间来自 SQLite `records.created` 列，均晚于原确认时间；不是新增的 payload 字段：

| 文件 | document_export 记录 ID | created (UTC) |
| --- | --- | --- |
| MRD.md | DOCUMENT_EXPORT-2f97e577fc2a4b43 | 2026-10-02T12:39:34.973818+00:00 |
| MRD.docx | DOCUMENT_EXPORT-813fc4a11a3244fe | 2026-10-02T12:39:35.038359+00:00 |
| PRD.md | DOCUMENT_EXPORT-2c1db0b657944030 | 2026-10-02T12:39:35.125085+00:00 |
| PRD.docx | DOCUMENT_EXPORT-78b7499861f44317 | 2026-10-02T12:39:35.185322+00:00 |

导出 payload 实际字段为 `artifact_hashes`、`asset_bindings`、`document_artifact_id`、`document_type`、`generator_version`、`style_version`；Store 返回时附加 `id`。**没有 `baseline_id`、`confirmation_id` 或 `document_version` 字段。四份阅读文档也没有打印 BASE/CONF/文档 artifact ID。** 本轮关联证据来自 API 请求 ID、导出行 ID/文件哈希、原活动基线及冻结内容、原 scope，而非声称新增持久化字段或文内基线编号。

## 保护和运行对应

- 隔离项目仍 1 个、revision 行仍 14 条、确认和基线各仍 1 条。records 28 → 32，只新增上述四条 `document_export`；此前所有数据库行仍存在且完全相同，另见新目录 `independent-preservation-check.json` 的原始行独立比较。执行前一致性备份保存在新证据目录，未提交 Git。
- 原根目录证据、两份旧失败夹具的数据目录、当前隔离目录中已有非数据库文件均逐文件哈希不变。两个旧项目的 409 状态没有强放行或改数据。
- 正式根 `data/requirements.sqlite3` 执行前后逻辑快照完全相同，逻辑 SHA-256 为 `1eafb3112e7ce3af7e5153fcdbeadb08ac7bd8e36d8e00cf88472c4507dac50f`。
- 正式 8765 仍是 PID 12964；实际 `/api/session` 返回 fingerprint `7001cabd1554f22b`，与原工作区 app 源码字节指纹一致。没有重启、新增监听或遗留 Office。
- 本轮进程内隔离 app fingerprint `ad73a1a7adbea392`，与干净检出 app 字节一致。36 个 app 顶层 Python 文件逐个规范化 CRLF/LF 后相同，统一指纹 `87185857ca484396`；两个原始字节指纹反映检出换行差异，不能混称同一个运行实例。
- 仅本新增报告纳入本地 Git；原证据、合成数据库、配置、凭据、模型证据、`work/` 均不提交。没有 push/merge/deploy。

本轮已经验证确认后独立文件的状态和内容绑定。新 DOCX 仅做结构、CRC、正文和哈希检查，本轮没有新 Office 打开/渲染验收。真实 PM 批准、真实业务系统执行、新机安装和安装器验收均未验证；文中“产品经理指定版本内容确认”仅反映这个隔离合成项目原有的系统确认状态。到此停止，不扩大功能或追加用例。
