# 聊天入口与需求草稿：实现和验证

本轮已实现用户确认的交互：先自由聊天，随后核对需求草稿；专业用户可以直接进入草稿页填写。日常工作台已更新到同一个 `http://127.0.0.1:8765/`。启动时冻结的范围见 `conversational-intake-20261001.md`，实际结果以本文件及本机证据为准。

## 实现

- `app/interview.py`：独立访谈提示与响应契约。访谈不能创建条款、回答正式问题或批准范围；用户原话先保存为来源和聊天记录。无效结构、虚假引用、HTML/代码输出不作为有效回复保存。
- `app/actions.py`、`app/workflow.py`、`app/provider.py`、`app/contracts.py`、`app/main.py`：接入现有授权、版本、幂等提交、任务取消、调用证据和预算流程。访谈无效响应不进行格式重写。旧 run 入口不能绕过原话保存；人工直接填写保存真实输入来源，只完成输入阶段，后续核对门槛保持。
- `web/src/intake-chat.tsx`、`interview-request.ts`、`workspace.tsx`、`api.ts`、`main.tsx`、`guided.tsx`、`style.css`：聊天入口、资料入口、直接填写、未发送输入保护；发送按钮明确展示接收端和本项目内容范围。接收端或来源范围变化不会发送；提交响应不确定时保留批准请求和幂等标识，恢复时不另建请求。
- `web/src/product-flow.tsx`：分别显示现状、目标、改动、保持项和范围外内容。新建议与已保存字段并列，进入编辑框后再人工保存；切换卡片先处理未保存修改。空白字段不显示为已保存事实。
- `tests/test_conversational_intake.py`、`test_interview_frontend.py`：17项新增检查，覆盖成功/拒绝、取消/保留、授权/变更、重复提交/原请求恢复、直填/不能跳过确认、目标与改动分开、旧范围与新建议并列。

未修改语义质量引擎、历史设计参考、既有测试预期或历史基线，也没有增加HTML/代码生成能力。

## 已执行检查

```powershell
$env:RA_TEST_LOCAL_OFFICE='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/conversational-intake-20261001/full-release.xml
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_interview_frontend.py -q
npm --prefix web run build -- --outDir ../evidence/conversational-intake-20261001/frontend-package
.\.venv\Scripts\python.exe -X utf8 evidence/conversational-intake-20261001/run_real.py
.\.venv\Scripts\python.exe -X utf8 evidence/conversational-intake-20261001/verify.py
```

全量882项通过，失败/错误/跳过均为0，233.35秒。包含17项新增检查和5项实际本机Office合成文件测试，不能重复相加。末次前端卡片处理与授权文案调整后，两项前端测试再次通过；TypeScript/Vite生产构建成功，正式 `web/dist` 与验证构建逐文件一致。测试库、Office输入和付费流程使用隔离数据，未对历史项目执行修改。

正式浏览器另验证：从空项目发送聊天，原话与真实回复显示；专业入口直接进入第二页，目标保存为原始来源；未发送输入“保存后离开”后仍保留，不触发模型。浏览器新增两个明确标记的验收项目，未删除记录掩盖测试痕迹。浏览器窄屏与企业实际业务未验证。

## 真实付费模型结果

当前配置为官方 `https://api.deepseek.com` 的 `deepseek-flash`。隔离公开流程执行3轮访谈和1次整理，正式页面另外执行1轮访谈，总计7次真实HTTP200请求；没有手工重放语义失败。已知用量69004 tokens（输入54617，输出14387），实际扣费金额未知。

- 4轮访谈回复均通过访谈结构与引用校验。隔离任务状态分别为 `succeeded/partial/partial`，正式页面任务为 `succeeded`；partial保留缺少图片等限制，并未宣称图片已读取。未确定的长度、修改权限和审计保留，未自动生成或采纳需求条款。
- 隔离“整理需求草稿”任务失败：首次引用校验失败，随后两次程序自动格式修复，最终 `SEMANTIC_BLOCKED`。模型改写了备注权限问题及其拆分内容，保真检查拒绝采纳。调用结果与原始证据完整保留；这次真实草稿闭环未成功，不作为产品验收通过。
- 未为凑成功修改回复、减少原始内容、放宽校验或人工追加付费重试。程序原有自动修复发生的请求全部计入7次。

## 保护与实际边界

原有32807个保护清单文件未变；原有16个项目、275条版本、723条记录及settings/migrations行逐行保持。日常库新增2个验收项目、4条版本、4条记录；原始文档、已确认基线与既有模型证据未重写。用户原Word进程保持。存在日常数据库一致性备份及三次前端替换前的回滚副本。

日常运行指纹与磁盘源码一致：`b868563153235535`。仍使用根目录 `app/`、`web/dist/`、`data/`，只监听8765，原有本机无令牌运行方式保持。没有推送或发布。

有限规则能约束结构、引用、代码输出和状态转换，不能保证访谈问法足够轻松、推断都准确或遗漏已穷尽；带标签的示例也可能被保守拒绝，需核对原话。真实第一轮仍一次问了多个相关细节，问法和收敛属于提示引导，不是确定性语义证明。附件在访谈中主要提供已有可读文字；图片视觉处理沿用资料整理流程，上传成功不能计为看过图片。聊天长历史受上下文容量限制，原始来源持续保留；排除来源并不自动撤回历史讨论，沿用现有项目语义。不把这些检查解释为真实企业需求、人工业务批准或发布验收通过。

本轮在 `181339ef973fc89b3441e653bf2c9e9781c35909` 之上形成本地提交，保留原有 `work/`。本机完整证据位于 `evidence/conversational-intake-20261001/`：full-release.xml/log、frontend-delivery-tests.log、build-package.log、real-summary.json、real-data/evidence/model-calls、verification.json、数据库备份和页面截图。正式页面的真实调用另在 `data/evidence/model-calls/CALL-5dd0a88988354a0c.json`；不提交数据、模型证据或密钥。
