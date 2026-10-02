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
