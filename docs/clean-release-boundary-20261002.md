# 2026-10-02 干净源码与限定历史指代验收

任务输入为已独立验收的 `e6fdf7c9e38ca9bcfdcca9d76377e78c3404eab5`。本轮只核干净恢复、全量及五项合成Office、三类限定指代/时序和隔离成功导出；发现真实问题后最小修复并同步提交到干净检出。不扩业务或功能、不新增付费模型调用、不改Skill/UIAgent、正式配置、凭据、代理和网络，不推送/合并/部署。

起点原工作区仅有未跟踪 `work/`，活动任务0；正式8765可见聊天输入与搜索输入均为空，未刷新或改动输入。原分支保持 `codex/conversational-intake-20261001`。仅在本轮隔离证据目录生成结果，保护原有34,714个文件及日常五张业务表。

## 干净基准与环境

独立目录 `D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002`，本地共享Git对象克隆后detach检出e6fdf7c，不切原分支，不复制work、未归档源码、旧data或.env。测试按README用隔离RA_DATA_DIR和TestClient，暂未新增TCP监听。复用本机Python虚拟环境和已授权Office，不声称新机器安装已验收。

Python3.11.9，Node22.19.0、npm10.9.3，Office16.0.14334.20918 x64。requirements.lock全部已安装版本与固定版本一致，packaging/requirements-build固定工具亦一致；逐依赖版本见 `checkout-e6fdf7c.json`。前端从Git锁文件 `npm ci --offline --no-audit --no-fund` 退出0，TypeScript/Vite构建退出0，未复制原node_modules或web/dist。

e6fdf7c干净源码全量 **1134通过、5skip、2个既有依赖弃用警告，224.48秒**。五项Office opt-in另行实际执行 **5通过、0skip、2个既有警告，37.58秒**：合成Word/Excel/PowerPoint、Office相对数据目录和Excel回退预览定位。无真实保护材料，不访问或关闭用户编辑中的Office。JUnit和日志分开保存，不重复计数。

运行fingerprint基于磁盘原始字节：e6fdf7c正式原工作区为 `681e7f0487f404d6`，干净CRLF检出为 `4ded8a1daeddb6bb`；不是不同实现。Git对象和每文件磁盘哈希分别记录，源码等价按Git换行规范化核对，不伪装指纹相同。

## 八个冻结输入及实际问题

当前规则“备注最多100字符。”；审查均声明“本期计数口径未确定，需PM确认当前来源中的方案是否已经采纳。”。完整原文、来源位置、对象及期望理由冻存在 `probe-before.json`，后续复放不改其输入和期望。来源声称PM确认始终是材料事实，不能替代系统正式批准。

| 固定输入含义 | e6fdf7c实际 | 正确期望 |
| --- | --- | --- |
| 先建议后同对象明确确认 | SEMANTIC_BLOCKED | SEMANTIC_BLOCKED：虚报未决。 |
| 先建议后尚未明确确认 | accepted | accepted：允许追问。 |
| 明确确认后本期明确撤回该计数方案 | SEMANTIC_BLOCKED | accepted：撤回后应重新选择。 |
| 仅“没有证据表明已撤回” | SEMANTIC_BLOCKED | SEMANTIC_BLOCKED：不能把未证明撤回当已撤回。 |
| 明确确认后本期明确改按字素簇 | SEMANTIC_BLOCKED | SEMANTIC_BLOCKED：已有明确本期选择；保留历史原文及新决定证据。 |
| 已确认码点后，模型只建议改字素簇且待采纳 | accepted | SEMANTIC_BLOCKED：新候选不能撤销旧有效决定。 |
| REQ-0001与REQ-OTHER的同名计数建议，仅确认REQ-OTHER；审查REQ-0001 | SEMANTIC_BLOCKED | accepted：不能跨对象传递采纳。 |
| 同一来源；审查被明确确认的REQ-OTHER | SEMANTIC_BLOCKED | SEMANTIC_BLOCKED：这个对象已有采纳依据。 |

探针5/8符合预期，3个真实语义失败。新增同一8项公开review回归首次4失败/4通过：前三个错误行为及第四个联合来源事实未按明确对象分离；原接受/阻塞期望未改。

## 最小修复与提交前定向结果

只修改semantic_quality：明确同对象撤回记pending；仍有效的confirmed单位不被其它候选单位的pending汇总覆盖；来源原文明确写已有需求ID时，按该ID及随后局部指代分别分类。原文quote与来源location/source_refs不变，related_refs不再把同名其它需求的决定借给当前对象；decision_evidence原句都是真实来源子句。无明确ID仍沿用原路径，不猜业务对象或模型意图。策略版本升为semantic-quality-9，补同一最小政策说明。

响应Schema和事实字段不变；没有新增采纳、撤回或审批动作，程序只分类已有材料。当前条款、已有回答或实际文档仍独立核对；保留矛盾反例和PM选择，未改任何已确认历史或业务规则。固定输入修复后8/8符合预期，输入不变、模型调用0；相关回归 **375通过，2个既有警告，38.50秒**。

第一份修复提交携带实现、新8项测试及本报告；随后将该提交同步到同一隔离干净检出，再执行必要全量和隔离成功导出。最终执行结果、逐文件归档、源码/服务指纹、导出manifest/hash及发布边界由本轮后续收口记录补充，不预写未执行结果。

## 命令与证据归属

本轮证据目录 `evidence/clean-release-boundary-20261002-1947/`，含before保护清单、两套源码字节/对象身份、失败复现、定向日志、独立JUnit和前端安装/构建日志。原模型证据、备份、数据和凭据不入Git。

```powershell
git clone --shared --no-checkout --no-hardlinks . D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002
# 在隔离目录：
git checkout --detach e6fdf7c9e38ca9bcfdcca9d76377e78c3404eab5
npm.cmd --prefix web ci --offline --no-audit --no-fund
npm.cmd --prefix web run build -- --outDir ../evidence/verify-dist
# 使用现有Python；RA_DATA_DIR显式指向隔离目录evidence下的sandbox，PYTHONPATH为空。
python -X utf8 -m pytest tests -q --basetemp=evidence/pytest-base-e6fdf7c --junitxml=<本轮证据>/clean-e6fdf7c-full.xml
# RA_TEST_LOCAL_OFFICE=1，三个selector展开为五个用例：
python -X utf8 -m pytest tests/test_office_sources.py::test_installed_office_reads_synthetic_document tests/test_office_sources.py::test_installed_office_relative_data_directory tests/test_excel_preview_repair.py::test_local_office_fallback_locator_reaches_preview_helper -q --basetemp=evidence/office-base-e6fdf7c --junitxml=<本轮证据>/clean-e6fdf7c-office.xml
```

有限规则不能理解所有省略、跨对象描述、多份材料时间关系或隐含撤回；复杂歧义继续人工审查。只对这8个固定输入及既有回归收口，不追加无穷同义词。真实PM尚未验收；新机器安装、Windows安装器重构建、外部消费者与发布环境验证不在本轮执行范围。

## 最终执行收口

上述提交前准备状态已完成验证。实现/测试/初始说明提交为 **3535156dbf40db09baa62f778a6c783fa51c3ce4**，父e6fdf7c；本次补充只记录执行结果。后续说明提交不改变该提交的实现、测试、前端及打包输入，不把说明提交冒充重新执行过的源码版本。最终SHA/父链和逐文件对应见 `commit-receipt.json`。

- 从本地Git获取3535156后，同一隔离目录保持detached、tracked clean，未补入原work或旧data。8项冻结探针8/8匹配原预期、输入不变；旧B2仍SEMANTIC_BLOCKED，实际联系人复核仍accepted/needs_changes，两项计数矛盾、反例和PM选择保留。模型请求、自动模型修复及模型用量0。
- **3535156干净全量启用Office：1147通过、0失败、0错误、0skip，2个既有弃用警告，253.29秒**；退出0、JUnit1147项。包含新8项回归及五项实际Office用例。与e6fdf7c基准两套结果分开归属，不相加或重复计数。仅合成Office，结束后Word/Excel/PowerPoint进程查询为空。
- 记录332个Git检出输入，其中208个实现/测试/脚本/前端/依赖检查输入；所有测试期间冻结字节保持，原工作区和干净检出对应Git对象一致。依赖固定版本没有不匹配，未发现源码/参考模板/打包输入缺失；运行时和Office复用本机安装，不等于独立安装验证。
- 精简单机包108项文件逐项验证hash，manifest为source_commit=3535156、working_tree_dirty=false、included_untracked=[]；再次前端构建一致，包内应用工厂的会话及静态首页检查退出0。ZIP SHA-256 **4ed90318913c3315c61e191d2510e2032981a4f05f83afc7de25018a6df50c71**。JS/CSS与正式构建字节相同；index.html仅CRLF/LF差异，规范化后相同。没有重打Windows安装器或发布标签。

成功导出使用全新隔离合成项目 **P-31b6a46a9f394f7d**，与两个旧项目、原daily data独立。明确真值为可空备注最多100Unicode码点、复合emoji分别计码点、同租户权限、0/100允许、101拒绝并保留输入/原记录。来源原文由text-source接口保存，候选和行为明确输入，人工采纳/阶段核对/文档核对/确认均调用真实本地接口；成文和审查答复是明确标记的确定性合成夹具，未伪装真实模型或PM判断。全部门正常执行，gate bypass=0。

首次探针退出1因MRD不允许function章节；第二次退出1因MRD缺少独立优先级分析。两次脚本、日志、隔离数据保留。第三次按现有v4契约提供有出处的goal、priority及validation计划，退出0；没有降低计划版本、删除业务内容或放松校验。

- MRD/PRD的md/docx四次真实TestClient HTTP下载200，逐段核对明确业务正文及REQ-CLEAN-0001 v2；RULE/AC为v1。两个文档v1、底稿v10，文档ID、导出记录及下载SHA-256一致；两个错文档ID请求仍409/STALE_DOCUMENT。
- 合成确认 **CONF-fea9ac5f5e9e4ad0**，基线 **BASE-03416e15de9445ad**，交接 **EXPORT-19a7352fb58b4478**。交接包16项artifact哈希、三项实体ID/namespace/version/content_hash、两份文档ID/version及正文投影hash、baseline/confirmation/hash均核对；规范正文和两份阅读文档均保留三条合成真值。
- 合成交接ZIP SHA-256 **d80e4a73fbaa8b08b424ad6daf0586388f5b769f64f26af0d5fcbd3ddaecfa83**。输出和manifest见 `synthetic-export-attempt3/`；真实PM验收NOT_RUN，材料内“已确认”与该工程合成确认均不证明真实业务批准。

正式服务仅原8765、root data：重启前活动任务0、可见输入为空，先一致性SQLite备份，再正常stop/start一次。新正式fingerprint **7001cabd1554f22b** 与原项目磁盘一致；干净检出及合成TestClient为 **ad73a1a7adbea392**。二者各自绑定原始字节，Git规范化代码对象相同（canonical app SHA-256 **87185857ca4843962389952fe84c13e54f15a336b2ed8059b7e86f5b77def4cf**）。没有修改正式前端、凭据或启动模式。

18个原项目可读，SQLite integrity_check=ok、foreign_key_check空。旧P-12c6a9828d0743b4/P-d74273d61fae4b69的PRD md/docx四次请求仍409/SEMANTIC_BLOCKED，未创建导出或强放行；原聊天项目及人工核对提示保留。8765只有一个已核归属监听，Windows虚拟环境launcher PID和实际Python子进程PID不同，已核父子关系及相同serve.py路径；本轮探针仅TestClient，未创建第二个工作台监听。全量套件自有临时fixture服务由测试生命周期管理。

保护终检：**34,714个原有文件无变化**，projects/revisions/records/settings/migrations行数18/279/727/1/1及完整内容hash未变；work、已确认历史、原证据、来源、数据和配置均保留。本轮本地仅app/semantic_quality.py、新测试和本报告三文件归档；逐文件原始before/after、Git对象、两份提交补丁及hash存于file-archive。未推送、合并或部署。

最终测试实际命令（使用明确的现有解释器）：

```powershell
Set-Location -LiteralPath 'D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002'
$env:PYTHONUTF8='1'
$env:PYTHONPATH=''
$env:RA_DATA_DIR='D:/01_AI工程/01_工程项目/pm-req-clean-verify-e6fdf7c-20261002/evidence/final-test-import-sandbox'
$env:RA_TEST_LOCAL_OFFICE='1'
& 'D:/01_AI工程/01_工程项目/pm-req/.venv/Scripts/python.exe' -X utf8 -m pytest tests -q --basetemp=evidence/pytest-base-3535156 --junitxml='D:/01_AI工程/01_工程项目/pm-req/evidence/clean-release-boundary-20261002-1947/clean-3535156-full-office.xml'
# 同一干净目录内的包验证：
& 'D:/01_AI工程/01_工程项目/pm-req/.venv/Scripts/python.exe' -X utf8 scripts/package_local.py --web-dist evidence/verify-dist --output evidence/package-3535156
```

可交付边界是“本机既有环境上的干净Git源码恢复、固定语义验收、合成Office、精简包离线应用/静态就绪与合成导出机制已执行验证”。完整新机安装、Windows安装器、真实模型新输出质量、复杂自然语言全覆盖、真实PM业务验收及发布环境尚未验证；原业务计数矛盾仍须PM选择。满足本轮有限验收后停止，不宣称已正式发布或所有输入均可自动判定。
