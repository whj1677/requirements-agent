# 2026-10-03 显式请求上限：离线修复与正式入口绑定

本轮仅修复可选 `max_calls` 契约，真实模型预算 **0**。起点 `ec3aacf4de087bcceb5eeac2a2b7278c45254f12`，父 `f55b085e5f9f58679b140a1d3708745e4d773a9a`；此前实现 `29341eaca5337acc63f20fc0c0d1363b15710c8d`。原分支保持 `codex/conversational-intake-20261001`。本报告随实现和测试提交，精确提交、父链、逐文件 Git 对象/磁盘哈希及完整 diff 存于本轮 `final-binding.json` / `committed.diff`。未推送、合并或部署。

证据目录：`evidence/request-cap-fix-20261003-0044/`。原 `real-minimal-retest-20261003-0012` 全部保留，冻结真值 SHA256 `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 不变。两个准备项目 `P-f73f8130d36040af` / `P-f31d1a6267f041bc` 仍只有来源，未启动动作、保存答案、生成、确认或导出。原 needs_changes、未知反馈及历史409不改。

## 问题与最小契约

旧公开计划提交上限2，却返回 `max_calls=null`；执行又清空此字段，HTTP出站没有计数边界。此次不改变全局配置、内容范围、token容量或模型质量政策。

- `ActionPlan` 和 `RunInput` 接受空值或严格正整数；布尔值、字符串、浮点数、零和负值被422拒绝。省略/null沿用原无限默认。旧全局历史上限仍不自动成为动作或连接测试上限，不改其保存值。
- 计划的有效上限、审批hash、授权配置快照、任务、run及响应一致。开始时冻结副本，后续设置变化不改已批准执行。旧任务存在后，新的任务必须重新取得计划hash；同幂等标识只返回原任务，不重发。有限额度子run不能通过旧resume自动续跑。
- 新增 `app/request_budget.py`，仅显式有限任务初始化 `request_budget={limit,used,reservations}`。旧行不迁移、不回填。新任务另保存每一步的 `step_states`；未开始为 `NOT_RUN`。默认恢复输入的旧结构保持，只有有限上限额外保存 `retry_input.max_calls`。
- 真正的 `Provider.request` 在 `client.post` 前同步调用证据对象的预算钩子。`BEGIN IMMEDIATE` 原子核对项目/run/任务、快照、输入和来源授权，再共同占用run和整个动作的额度；全部stage、首次请求、修复、已知HTTP重试和截断升级共用动作总上限。单步不重置额度，并发最后名额不能重复取得。
- `used` 是保守占用额度，`calls` 是已进入发送边界的请求次数；两者不是token或账单。预留到发送之间中断，记录 `reserved_unknown` 并占用额度；进入发送边界但无响应是 `dispatch_unknown`；实际收到HTTP响应是 `response_received`，不代表JSON、语义或业务通过。明确在发送前被拒可在CALL证据记 `not_sent`；已预留槽位仍保守占用，不释放未知额度。进程在进入HTTP代码与实际传输之间死亡也不能证明零费用。
- 上限耗尽返回终态 `paused_budget/BUDGET_EXHAUSTED`、原因和已用/有效上限，保留完成stage、原失败/usage，后续步骤 `NOT_RUN`。UI显示上限、占用、未知和未执行，终态没有进度转圈。继续需要新可审阅计划和明确发起，不自动扩额度。网络未知停止原有盲重试；重复输出、严格JSON、引用、人工采纳和语义检查保持。

未增加配额平台、恢复接口、审批角色或业务规则。内部预算字段为可选增量；省略上限的旧证据鸭子对象仍兼容。纯测试替身不能在有限任务中绕开出站绑定后让工作流采纳结果。

## 实际验证

| 检查 | 实际结果与证据 |
|---|---|
| 首轮全量 | 1281通过/13失败/5skip，退出1；`full.xml/full.log` 原样保留。发现省略上限证据钩子兼容缺口及空恢复字段兼容缺口；另有旧忽略上限契约与绕Provider替身的测试输入不相容。 |
| 受影响复验 | 265通过，退出0；`affected-final.xml`。业务/语义真值未改。通用动作helper默认省略上限；专测显式上限的测试保留有限输入。旧无限容量测试改用省略上限；原PRD截断显式2现严格停止于2，原文档不变。 |
| 最终预算专项 | 59通过，退出0；`cap-targeted-final.xml`，包含42项新Provider边界/mock回归及既有动作回归。 |
| 最终全量 | **1296通过/0失败/5skip**，退出0，275.39秒；`full-final.xml/full-final.log`。两个既有依赖弃用警告。 |
| 前端 | `npm run build -- --outDir ../evidence/request-cap-fix-20261003-0044/web-build`，退出0，现有依赖，无安装。实际React组件合成props渲染核上限2、占用2、未知1、后续未执行及终态无spinner；`ui-render.json/rendered-cap-ui.html`，不是实际收费任务UI证据。 |
| 正式8765 | 原服务无在途任务、可见输入为空后，用原脚本停/启一次；原dist先备份并逐字节核对，再保留原目录、复制已验证构建。初始包含删除的替换命令被执行策略拒绝，未执行；改为保留原目录的方式成功。无第二长期入口，实际监听仅127.0.0.1:8765。 |
| 无收费公开接口 | `public-contract.json`：当前合成主项目rev2，plan提交2→有效2、无前置缺口；true→422；省略→null；两个准备项目无run/task。首页及JS/CSS实际响应hash与新dist一致；正式界面读取22个项目。未调用actions开始或models/test。 |

命令（根目录）：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_request_cap.py tests/test_ui02_actions.py -q --junitxml=evidence/request-cap-fix-20261003-0044/cap-targeted-final.xml
.venv/Scripts/python.exe -X utf8 -m pytest tests -q --junitxml=evidence/request-cap-fix-20261003-0044/full-final.xml
.venv/Scripts/python.exe -X utf8 evidence/request-cap-fix-20261003-0044/public-contract.py
```

测试计数权威为JUnit。新测试实际拦截Provider的MockTransport出站：cap1/2均不越界，HTTP429/503/混合错误停止；合法有限格式修复、截断升级保持批准的token政策与失败usage；超时/网络未知只一次；8个线程争抢2名额只2次MockTransport请求；冻结分配2+2+2+1+1共8次，第九次不能发送。多图片stage cap1/2分别保存已完成的3/4个图片结果，后续 `NOT_RUN`。预留后取消有明确未发证据，槽位保守占用。旧审批hash换提交标识被409拒绝；后续设置变化仍执行原快照。严格已答目标错误只1次且不进入修复；重复错误输出在第2次停止，即使上限3仍不发第3次。MockTransport的合成usage不是DeepSeek实际用量。

Office相关实现/夹具未变。本次五skip为Word/Excel/PowerPoint合成读取、相对目录COM和Excel预览回退opt-in，未启动Office。复用 `evidence/clean-release-boundary-20261002-1947/clean-3535156-full-office.xml` 已独立核过的五项实际Office证据，保留旧身份；不计成本轮新Office执行。

## 反例工作流：只读结论与未批准重分配

目前两个准备项目皆rev2、无问题/条目，不能把旧已答问题ID移过来，也没有公开项目级回答恢复入口。来源材料写了测试决定，不等于新项目中已有问题/保存回答。只读API证据 `counter-flow-readonly.json` 保留旧反例两问题 `Q-285b7d4a7c7744a8/Q-a2378286a6454466` 的当前状态、原句、回答hash、出处版本和原目标版本。答案没有独立实体ID，以question ID+回答hash+answer_source_refs/来源版本识别，旧值由answer_history保留；新项目实际问题ID列表为空，不套旧ID。

1. 新问题：现有 `/actions/plan` + `/actions` 的 `organize`，对应 `ingest`，至少1个模型动作。模型给候选及问题，经校验保存本项目实际IDs；原引用source/excerpt成对保持。UI“整理当前信息”。若模型合理认为完整来源已明确决定，不强迫制造问题。
2. 保存测试回答：`POST /api/projects/{pid}/answers`，提交当前revision和实际qid→原冻结测试答案。**0模型动作**，保留qid及 `answer_history`，生成独立回答source/excerpt及受影响需求版本；不直接改条款或采纳。UI“保存本次回答”。
3. 候选落实：`clarify` 计划/开始，**至少另1个模型动作**。已答决定只能按原目标同kind `revise/target_item_id/answer_refs`；工作流保存 `answer_versions` 回答hash和新候选ID，旧条款/问题/答案保持，不自动采纳。
4. 逐项人工核对并采纳：`POST /items/{candidate_id}`，当前revision、`selection_status=selected` 及需要的行为核对，**0模型动作**。检查来源、原目标/同kind、当前回答hash；更新原业务ID的内容版本，候选转deferred，登记answer application。旧问题ID和回答历史保留。`GET/POST /questions/{qid}/application-review` 是当前条款已经表达答案时的人工核对，不替代模型候选落实证明。UI“核对采纳”和“核对回答落实”。所有操作者仍是synthetic test operator，不是用户真实PM或发布批准。

完整反例答案落实至少两个模型动作，原“主4+反1”的五动作计划不能同时覆盖完整主链和完整反例；合法路径不存在时仍 `NOT_RUN`。本轮未操作这四步、未排除来源、未代填答案。

仅供父审核的最小选项，**不是已批准新计划**：

- 优先主链：保持主整理2/MRD2/PRD2/审查1（4动作、最多7请求）；反例剩1动作/1请求最多验证新问题生成，答案落实/后续成文审查 `NOT_RUN`。完整来源可能已给决定而不产生未知，需如实记录。若父要历史原输入单独形成问题，需要另冻来源时序/范围；不得覆盖原冻结计划或擅自排除已保存决定。
- 优先修复相关反例闭环：主整理2/PRD2/审查1，反例问题生成2/落实1，共5动作/8请求；主MRD `NOT_RUN`，因此主正式确认/交接也 `NOT_RUN`。仍须父确认新的来源时序、前置条件与分配，不能宣称主全链通过。

## 保护、版本与剩余限制

`before.json/after.json` 逐行hash一致：**22项目、314 revisions、792 records、1 setting、1 migration** 全保留；188份CALL文件逐文件SHA不变，原证据/冻结真值/work/和.env哈希不变。零真实模型动作、零DeepSeek实际请求、零成功/失败/unknown请求，实际token **NOT_APPLICABLE**，金额无账单 **UNKNOWN**。代码/测试中的合成请求、失败和usage另计，绝不冒充真实生成。

磁盘原始字节fingerprint与正式服务同为 **a985c172d208bcf5**，此前运行c6e25f01e6d832a8已正常停服。新前端响应hash与验证构建一致；所有已归档文件按Git换行规范化与提交对象核对，不要求换行不同的干净检出有同一个原始字节指纹。未知 `work/` 仍未跟踪，原配置、历史文档、合成DB和模型原始证据未入Git。

此次能证明的是显式上限的离线契约、mock出站上界及正式入口版本绑定，**未做新付费复验**。保守预留可能少用额度；未知发送不证明未计费；没有自动恢复/续跑平台。Provider未来新增真实发送入口必须复用同一计数边界。自然语言质量仍是有限来源/语义规则，不能覆盖所有表述或替PM决定。旧付费失败仍成立，两个准备项目成文/业务审查/正式确认/导出均 `NOT_RUN`；机器消费者、新机、安装器、真实PM及发布验收仍未验。离线收口后停止，等待父独立复核，不自动追加下一轮。
