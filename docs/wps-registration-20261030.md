# 1.2.12 WPS 本机接口识别修复与实测

## 原因与修改

用户确认 1.2.11 重新读取后仍提示缺少接口。本机随后安装的 WPS 为 12.1.0.29161；只读核查确认三个组件的 ProgID 可见，但 `LocalServer32` 仅存在于 32 位 CLSID 注册视图。表格和演示的实际服务器也为 `wps.exe /prometheus /et`、`wps.exe /prometheus /wpp`，并非旧版独立的 `et.exe`、`wpp.exe`。此前只检查默认视图及独立进程名，会在创建 COM 前漏检。

- 修改前 `36af7294299b99558613f34fc01baf371e3a3e68` 已归档并推送至 `codex/archive-pm-req-20261030-wps-discovery`。
- 同时检查 64／32 位注册视图，支持 CurVer 和 ServerExecutable；使用已读取的 CLSID 创建实例，不依赖再次解析 ProgID。不写入或修复系统注册表。
- 识别当前 WPS 的统一进程及明确组件启动参数；不能确认组件的注册不用于提取。
- 后台驻留进程不再直接视为用户正在编辑。已有可见 WPS 窗口仍拒绝接管；自动化必须创建一个新的、文档集合为空的进程组，清理只针对记录中的精确 PID、创建时间及映像匹配项。
- 保留宏禁用、只读验证、外链不更新、原件不保存及权限拒绝停止。没有删除校验，也不将缺少能力的接口当作成功。
- 增加 `RequirementsAgent.exe --office-diagnostics --output <指定文件>`。仅报告版本、进程位数、三个组件的注册检查及选择结果；不启动 Office、不读取文档、不记录用户安装路径、不调用模型。

Microsoft 说明 [CLSID 注册键存在位数视图重定向](https://learn.microsoft.com/en-us/windows/win32/winprog64/shared-registry-keys)；[LocalServer32 支持应用路径及 ServerExecutable](https://learn.microsoft.com/en-us/windows/win32/com/localserver32)。WPS 官方列出 [KWPS Application 自动化及 AutomationSecurity](https://open.wps.cn/documents/app-integration-dev/wps365/client/wpsoffice/jsapi/wps/Application/obj) 和 [ET AutomationSecurity](https://open.wps.cn/documents/app-integration-dev/wps365/client/wpsoffice/jsapi/et/Application/member/AutomationSecurity)。

## 实测证据与边界

- 修改前，三个“仅 32 位注册”的合成回归全部失败，复现漏检。
- 本机真实 WPS 的 Word 正文／表格、Excel 单元格／公式、Word 内嵌图片及相对目录读取已经通过；没有将这些合成内容当作客户资料或业务规则。
- 本机 WPS 演示设置 `AutomationSecurity=3` 后，底层 COM 属性读回仍为 1，无法确认宏已禁用。程序在打开 PPT 前返回 `OFFICE_SAFE_MODE_UNSUPPORTED`。普通 PPTX 仍由内置解析器处理；本机 WPS 演示后备提取尚不具备验收条件，不移除此检查来获得成功。
- 用户提到的《襄州柳堰集项目.docx》《自主BMS绝缘阻抗检测电路.docx》未在本机两个工作台的数据中找到；不能声称读取了这些客户原件。
- 模型调用和客户文档语义核对不包含在离线回归中。

## 最终验证结果

- 构建源码提交：`443038c24890d700bbf62852eadc369d2bbc299b`；[Windows CI 41004400297](https://github.com/whj1677/requirements-agent/actions/runs/41004400297) 成功。没有合并 main、发布标签或创建公开 Release。
- 全量回归：3110 项，初跑 3108 通过、2 失败、0 跳过，620.20 秒。其中 Unicode 快捷方式测试因临时 `WinError 32` 文件占用失败；释放后单独复验 1 项通过，没有修改生产行为或放宽断言。剩余 1 项为上述真实 WPS 演示后备读取限制，因此全功能验收结论仍为**部分通过**。
- 分发边界 CI：125 通过、1 跳过；跳过的 opt-in Word 图片实测已在本机真实 WPS 中通过。
- 新用户实际安装、同版本重装、桌面与开始菜单快捷方式真实打开及实例复用、打包后的原生 COM 组件与只读注册诊断、1.2.11→1.2.12 升级、卸载及项目／历史／附件／配置／DPAPI 密钥保留全部通过。隔离验证不调用模型。
- 1689／1689 个包内文件的 Python 与 Windows 原生字节校验一致，无不匹配；下载后的安装器及交付目录四个文件再次通过相同的原生校验。
- 日常数据只读对比：36 项目、770 修订、2031 记录、1 设置、1 迁移，五表完整内容哈希未变，SQLite 完整性为 ok。未重启日常服务，未迁移数据。
- 本轮调试中创建的精确已知空白 COM 进程已清理；原先四个 WPS 后台进程及其创建时间保持不变。没有按进程名批量结束办公软件。

安装器：`RequirementsAgent-1.2.12-windows-x64-setup.exe`，143872362 字节。

SHA-256：`bddf698617fa991b76f7923743b20a6f3c25f3ba1212aed87890e03cb882d847`。

本机交付目录：`output/需求Agent-1.2.12-WPS实测候选版-20261030/`，包含安装器、SHA256.json、客户复验说明及只读注册诊断脚本。状态为 `CANDIDATE_WITH_WPS_PPT_LIMITATION`，不标记为三类保护文件全部通过。

证据位于忽略目录 `evidence/wps-discovery-20261030/`：原始全量 XML／日志、快捷方式复验、真实 .doc／.xls 导入记录、最终注册诊断、五表前后哈希、下载工件及安装／升级报告、交付目录原生字节校验报告。客户原件和模型质量均未验收。
