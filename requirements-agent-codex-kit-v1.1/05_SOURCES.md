# 官方依据、历史原则与未验证事项

技术来源记录沿用 v1.0（原记录日期 2026-09-23），本次 v1.1 仅核对用户提供的两份 Word 章节内容，没有重新检索或实测 API 能力。技术配置实施时仍须复核，不将继承记录当本轮新增验证。

## 外部官方技术依据

| ID | 官方资料 | 本包采用的有限事实 |
|---|---|---|
| S01 | DeepSeek Your First API Call — `https://api-docs.deepseek.com/` | 官方提供兼容 API 接入格式；OpenAI 格式基础地址为 api.deepseek.com；模型名称由配置传入 |
| S02 | DeepSeek Models & Pricing — `https://api-docs.deepseek.com/quick_start/pricing/` | 查阅时 deepseek-flash 支持视觉，deepseek-v4-pro 不支持；兼容同一服务不代表模型能力相同 |
| S03 | DeepSeek JSON Output — `https://api-docs.deepseek.com/guides/json_mode/` | json_object 模式需提示词说明 JSON；官方提示可能返回空内容，因此仍需程序处理 |
| S04 | DeepSeek Vision — `https://api-docs.deepseek.com/guides/vision/` | Flash 可接收文本与图像，支持常见图片格式；有真实图像 content 输入方式与限制 |
| S05 | DeepSeek Chat Completions API — `https://api-docs.deepseek.com/api/create-chat-completion/` | 有 finish_reason、usage、推理参数及工具字段；截断、参数差异、无效工具参数需应用校验 |
| S06 | FastAPI — `https://fastapi.tiangolo.com/` | 可用于 Python API 服务，并基于类型信息提供验证/接口描述；选择它是本项目工程默认值 |
| S07 | Pydantic Models — `https://docs.pydantic.dev/latest/concepts/models/` | 提供数据模型与验证机制；用于结构校验，不当作业务真实性证明 |
| S08 | OWASP SSRF Prevention — `https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html` | 地址、DNS、重定向、允许列表与网络策略是抓取安全设计的重要部分 |
| S09 | OWASP LLM Prompt Injection Prevention — `https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html` | 外部资料可能携带间接注入；提示词之外还需要最小权限和确定性限制 |
| S10 | MDN iframe — `https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe` | iframe sandbox 可以限制预览能力；需要正确组合策略，不把任意生成代码当可信页面 |
| S11 | Playwright Python Isolation — `https://playwright.dev/python/docs/browser-contexts` | BrowserContext 可隔离浏览器会话；它不等于完整网络 egress 隔离 |
| S12 | DeepSeek Tool Calls — `https://api-docs.deepseek.com/guides/tool_calls/` | 模型提出工具调用，实际函数由应用提供和执行；本项目 V1 不以原生工具调用为必需依赖 |

模型能力和参数可能变化。没有尝试真实 Key，不能把 documented 状态预填成 verified。也没有依据声称 DeepSeek 是此类需求分析最优模型。

本包没有价格预算结论，不写死外部服务单价；费用估算依赖实际 usage 与实施时配置的价格表。

## 延续的用户既有设计原则

用户历史文件《产品经理需求澄清与成文Skill_Codex设计任务书_v0.1.md》（2026-09-17）中，统一底稿、材料来源、定向追问、人工确认、从同版底稿派生文档等原则已在本次对话中继续明确。

该历史文件曾要求“只做 Skill 设计、不开发独立应用”；本次用户明确要求 Codex 开发独立 Agent，因此不沿用旧阶段限制。历史文件不是当前应用已实现或已验证的证据。

本包已含两份本次上传的原始 PRD／MRD Word，已依据其章节、正文和可见图表整理内容 profile。仅参考章节内容，不声称复制版式；原件 SHA-256 见 references/content_profiles.json。没有读取用户本地最新 ai-engineer-context 代码，仍由 Codex 在授权环境核实适配。

## 本次实际完成与未完成

完成：更新开发任务书、启动／增量提示词、运行时阶段提示词、文档 Schema、两份实际参考文件的章节内容映射与合成评测设计；包内检查见 08_PACKAGE_CHECK.md。

未完成：应用实现、真实 API 调用、浏览器产品测试、实际业务 PRD／MRD 导出及渲染验收、真实用户首审。不可将本包描述为可运行成品。


## 本次用户提供的内容参考来源

REF-PRD：《产品需求文档PRD模板(1).docx》。依据：前置基本信息／修订表、目录与正文第一至第五章；第四章的逐功能原型／概述／流程／清单／详述／接口／数据／边界／异常；第五章非功能维度。原可见第 4 页角色表、第 5 页结构和流程图只作表达参考，不继承例子中的对象和规则。

REF-MRD：《百度产品需求管理文档（MRD）模板(1).docx》。依据：前置审核／重要性／紧迫性／变更控制及修改记录；正文第 1～7 章；5.1 的功能类型、优先级、流程、页面布局、详述及 5.2 非功能；多处明示可裁剪。保留文件中的术语和组织，不用外部通用 MRD 定义替换。

来源定位优先原章节名称和内容，不用模板已有页码当生成后页码。工作流、验收条件映射、版本门禁等新增要求是本项目设计，不冒称为原模板已有内容。
