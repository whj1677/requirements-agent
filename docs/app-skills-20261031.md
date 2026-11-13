# 应用内成熟 Skills 集成记录（2026-10-31）

已将成熟技能的固定版本原件接入现有需求 Agent，按阶段加载审定片段，并保留应用自身的来源、人工确认与流程校验。**接入及离线回归已完成；真实生成质量尚未稳定，当前不能宣布 PRD 或流程图质量普遍提升。**

## 上游与应用边界

原件位于 `requirements-agent-codex-kit-v1.1/prompts/skills/vendor/`，固定版本如下；不是安装整个插件市场。

| 原件 | 固定 commit | 许可证 |
| --- | --- | --- |
| [Anthropic write-spec](https://github.com/anthropics/knowledge-work-plugins/blob/2574e29377c7173d0cecffef29a6ecbd7f8ccb5d/product-management/skills/write-spec/SKILL.md) | `2574e29377c7173d0cecffef29a6ecbd7f8ccb5d` | Apache-2.0 |
| [pm-skills create-prd](https://github.com/phuryn/pm-skills/blob/2a12295a7717569baa7be181b4cfebe3ca38a6bb/pm-execution/skills/create-prd/SKILL.md) | `2a12295a7717569baa7be181b4cfebe3ca38a6bb` | MIT |
| [pm-skills strategy-red-team](https://github.com/phuryn/pm-skills/blob/2a12295a7717569baa7be181b4cfebe3ca38a6bb/pm-execution/skills/strategy-red-team/SKILL.md) | 同上 | MIT |
| [mermaid-diagrams](https://github.com/softaworks/agent-toolkit/blob/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams/SKILL.md) 及其 flowcharts 参考 | `3027f20f3181758385a1bb8c022d4041dfb4de84` | MIT |

4 份 SKILL.md、1 份流程图参考与5份许可证均原样保留，包括 Anthropic 插件与仓库两份 LICENSE；适用祖先目录未发现 NOTICE，扫描范围有记录。各目录 `SOURCE.json` 与 [upstream.json](D:/01_AI工程/01_工程项目/pm-req/requirements-agent-codex-kit-v1.1/prompts/skills/upstream.json) 保存来源 URL、commit、字节数、Git blob SHA、SHA-256 和精确选段。许可证、来源及字节完整性不等同于模型质量通过。

加载器采用固定 ID、commit、路径白名单，核对文件 hash、UTF-8、标题与行号并限制大小；缺失或损坏报 `SKILL_RESOURCE_INVALID`，不借模型修复资源。实际路由为：

| 应用 stage | 加载内容 |
| --- | --- |
| `interview` | write-spec 的问题描述与未决问题片段 |
| `ingest`、`clarify`、`change` | write-spec 写作片段 |
| `prd` | write-spec 与 create-prd 的审定写作片段 |
| `review` | strategy-red-team 的具体检查与不编造问题片段 |
| 上述非 interview 阶段且当前流程适用 | 另加 mermaid-diagrams 流程参考；单条任务按该条范围判断，明确不适用时不强加 |
| 其他 stage | 不加载 |

只加载所需片段，不执行宿主命令、连接器、脚本或外搜步骤，不把固定目标数量、P0/P1/P2、指标和市场研究变成必填。技能不是需求来源，不能替用户决定业务。流程仍输出既有 JSON graph，由原 SVG/PNG/Word 渲染链表现，没有新增 Mermaid 运行依赖。

通用装配与 business-draft 两条路径均已接入。可信任务头的 `builtin_skills` 记录实际选中版本、文件与片段 hash、policy hash、最终技能文本 hash，并进入真实调用授权/证据。首次装配后冻结本次技能身份及文本；受限 repair 复用该快照，不重读变更后的资源、不扩大原修复协议。实现见 [skill_guidance.py](D:/01_AI工程/01_工程项目/pm-req/app/skill_guidance.py)、[workflow.py](D:/01_AI工程/01_工程项目/pm-req/app/workflow.py)。

源码包和 Windows 构建清单均已受控收集嵌套技能、来源记录、policy、manifest 和许可证；清单回归同时排除可执行脚本与凭据。此项证明打包覆盖，不代表本次另做了安装验收或发布。

## 离线验收与版本边界

完整 pytest **3200 passed、8 skipped、3 warnings，实际进程退出码0**。630个源码/资源文件前后仅 `application-policy.md` 有获授权的提示更新：`0f6d54…69ab7f42` → `b94fc6ffb18f37dc6a1e0fdfd8227e6865c7dcde305b5f2a9218e5e7b52b0c48`；因此不能说全量全程使用最终 policy。

随后用新进程、最终 policy 对9个技能/打包/流程/详细生成/PRD相关测试文件补验，**64 passed、0 skipped，实际退出码0**；期间630文件无变化。两组计数重叠，不相加。10份上游原件及 manifest 前后均匹配，manifest SHA-256 为 `66b570cd6399ab8e6eea7bdd660b28d420baf29405c4d7b44de272ab13ba86ac`。离线进程使用隔离数据与外连拒绝措施，没有记录到真实外连尝试。[完整哈希、日志、XML与边界汇总](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/app-skills-validation/summary.md)

## 有限真实试跑：有正例，也保留失败

以下均使用冻结的储能云平台合成资料，每试次仅一次真实请求，不自动修复、重试或采纳；不能据此推断统计意义上的提升或退化。

| flow 试次 | 实际结果 |
| --- | --- |
| no-skills | 程序保留候选，但把“服务端首个成功”写成“先到达成功”；刷新动作与仍未知结果分支表达不一致，并有未获来源支持的反向状态推导。语义未通过。 |
| skills 初版 | 程序保留候选，但漏释放请求的未知分支，认领成功唯一后继接释放、缺少本次认领独立结束；验收也有“首个请求成功”等偏差。图较紧凑不能抵消这些漏项。语义未通过。 |
| skills v2 | 仅细化通用应用 policy 后再试一次；拓扑校验拒绝唯一需求，`accepted=0`、`rejected=1`、候选0。原因包括开始节点有入边、结束节点继续连出；没有可验收流程图，不能称修复成功。 |

[前两组逐项语义及原 PNG 比较](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/app-skills-real/real-semantic-comparison.json)；[v2 原始运行及拒收原因](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/app-skills-real/skills-v2/real-skills/runs.json)。程序格式接纳、引用可解析、图片生成均不代替业务语义核验。

另有一个访谈正例：对冻结 revision3 的原补答进行 skills 回放，回复保留并发、本人释放、无自动超时、保留历史等已答规则，没有再要求决定这些规则；实际追问仅是**当时仍未知**的“每次认领—释放合并一行，还是各记一行”的记录布局。此为一次局部正例，不是整个访谈能力验收。[原输入与真实回复](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/interview-skills-replay/outcome.json)

负向评审随后完成一次真实调用（HTTP 200，238.75秒，0 repair，停在 `awaiting_user`）。独立核对确认满足冻结 golden 的“至少识别一项”最低条件：明确识别释放未知缺边，也发现首成功措辞与列表/详情验收覆盖问题；**认领成功独立结束仍漏判，列表 only 的范围问题仅部分识别**。23条引文全部逐字匹配当前字段，未见旧稿伪引；但原子条件、无查看权限通用拒绝等修订口径仍有未经冻结材料确认的推断，不能全部直接采纳。程序未改8个条目、未自动确认或生成文档。[独立语义核对](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/app-skills-real/negative-review/real/semantic-review.md)

该次指纹并非全部未变：daily DB 主文件变化与 root 并行重启8765、生成日常 PRD v4 的背景同时存在，不能归因隔离 runner。所列源码、policy、fixture与golden未变。

最终逐 payload 保护核对显示：原37个项目、2103条旧 records、settings 均未变，仅新增本次合成项目及其157条 records（含最终内容核对记录）；模型配置摘要一致。最终630个已测试源码/资源文件与最终 policy 定向补验基线也无差异。[日常数据保护记录](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/data-preservation-final.json)、[最终源码核对](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/app-skills-final-source-check.json)。这比主 DB 文件整体哈希更能说明原有项目是否受影响。

## 工作台真实生成的 PRD v4

在唯一入口 `http://127.0.0.1:8765/` 正常重启载入技能后，从既有合成项目的“核对并更新 PRD”执行一次真实请求。复用同版成文检查，没有重复审查或 repair。实际调用 `CALL-888aad77d82f46a2`，官方 DeepSeek 请求与返回均为 `deepseek-flash`，8.797秒；实际请求与授权均记录 write-spec、create-prd、mermaid-diagrams 三项，技能文本 hash 与 manifest 一致。[调用回执](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/skills-prd-v4-call-receipt.json)

文档 `DOC-97e70ba1747f4272`，document_version=4、draft_revision=33。已从真实界面下载 [MD](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/PRD-skills-v4.md)、[DOCX](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/PRD-skills-v4.docx)、[ZIP](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/PRD-skills-v4.zip)。独立检查确认1需求+16验收完整，业务条目与生成前全对象相同；437个正文/表格文本单元在Word中完整且顺序一致，ZIP CRC及内含MD、DOCX内部part绑定正确。PNG/SVG与v3逐字节相同，没有让写作技能重绘已确认流程。Office分页仍未验证。[独立内容与导出核对](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/skills-prd-v4-review.md)

本次可观察改善是目标解释回到第2章，第11章不再夹目标段；未见已答规则、优先级未知和指标不适用状态回退。**权限章节仍未闭合**：模型在正确的 `users` 键生成了完整权限解释，随后被 `program-rendered-detail-no-duplicate-explanation` 规范化删除；最终第3章仍只有使用者名单。权限条款在规范正文、范围和交互中保留完整，属于组装限制，不能写成模型没有生成或放错章节。本轮没有继续修改该组装协议。

独立内容检查后，真实UI记录该合成PRD v4已核对，项目revision变为34；规范条目未修改，没有进行客户或正式业务确认。[实际界面](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/skills-prd-v4-reviewed.jpg)、[最终项目快照](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/skills-final-project.json)。唯一8765监听、根目录数据与前端9文件构建匹配均已核对，见[运行入口记录](D:/01_AI工程/01_工程项目/pm-req/work/human-real-20261031/runtime-final.json)。

本次收口为固定上游接入、离线回归、有限真实试跑和实际评审稿下载；不把这些等同于通用质量提升、流程全自动验收、原生Office排版、安装验收或客户正式交付。流程生成漏项、审查漏判及上述权限组装限制均保留，不自动采纳失败候选。
