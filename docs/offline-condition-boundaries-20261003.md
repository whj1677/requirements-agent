# 条件映射误报的最小离线收尾

本轮仅修复未来新尝试的禁止约束和同对象肯定成功分支两类误报。真实模型调用预算/实际均为0，不派Agent、不启动观察器，不改日常项目，不继续真实整理、MRD/PRD、审查、确认或导出。完成离线验证、精确本地Git及唯一8765运行绑定后停止，交父任务复放。

起点 `cf4c19e7cf329c690bec0b69ae66df3981e751c7`，父 `049361d0150eaffbdb0b5b7d84e096dd86d91907`，分支 `codex/conversational-intake-20261001`，正式/磁盘指纹 `26a6eaf8897d9f86`，dirty仅未知 `work/`。协调原回合 `01a0fdc8-fd9e-7030-8f3c-48e3b33a6fd9` completed；协调收口回执记录观察器13192自然exit0，本轮核PID不存在。精确需求GUI主项目空输入、发送禁用、无Stop，活动任务检查退出0。

## 依据与复现

权威失败是原回合结束后的 `condition-semantic-initial-final-probes-20261002.json`：HEAD cf4c19e、指纹26a6eaf8897d9f86、四项结果为两合法例误拦/一非法例正确拦/一正常分支正确放行，SHA256 `837e1451d16bb12ba301ff6a374806d09c25efcfc790271d36ddfa719ead67f7`。中间探针 `condition-semantic-independent-probes-20261002.json` SHA256 `abf5d7629d3551b80f9c05d3c3f58dcabfdf4af6257841ee696d9e22977b1cb1` 仅作历史，不以中间失败替代最终失败。

本轮新目录 `evidence/offline-condition-boundaries-20261003-0252/` 中 `old-final-false-positives.xml/log/json` 在cf4c19e实际执行两原句的完整 `validate_response` 回归，**2 failed，退出1**，原输入不变。后续所有执行回执、日志、JUnit及保护快照均独立新名/create-only，前轮证据只读。

| 原句 | 最终完整校验及行为核对边界 |
|---|---|
| 结果未知时禁止再次创建新的申请；先查询原申请ID。 | 接受动作约束候选，仍不知道原申请结果；不能据此确认失败 |
| 系统受理成功且处理成功的分支，配额满足则整批创建；明确拒绝时不创建；超时结果未知。 | 接受同对象肯定正常成功分支，拒绝/未知例外保持 |
| 响应丢失时既有资源不变。 | SEMANTIC_BLOCKED，仍是未证实结果断言 |
| 正常成功分支：整批创建；服务明确拒绝时本批不创建；超时结果未知，先查询原申请ID，状态确认前不重复提交。 | 接受，无需强造新问题 |

上述四项结果来自 `final-fixed-probes.json`，逐项保存原句、固定期望、实际结果及输入未变证明。它同时重放完整原 `CALL-a88590ed94784688`，仍在 `proposals[0].behavior.result` 的“全部项配额满足则整批创建”精确阻塞；`model_repair_allowed=false`。原CALL文件SHA `cfea68ca78a5759443a9d199dcc28525771d4e412ddc780da4c90366aedf5b91`、final_output UTF8 SHA `314f6c8e3870796c3258d20f049ff3d2af0ebb00a58e50760f141fe1de2264d3`、冻结真值SHA `2556a553fe1a23d5787e4dfe0856747421dd3967a896f072d370634ec7100adc` 不变。原exceptions确实保留拒绝规则，不把它说成全部丢失。

## 最小实现及兼容边界

只改 `app/result_conditions.py`。按禁止模态、重复/重新动作及新申请/请求目标，增加内部 `action_constraint`，标注 `temporal=future_action`、`action_target=new_attempt`，保留原对象/来源/字段位置/原句。约束未来新尝试不再被投影成对原交易效果的 `unknown_certainty`。其他历史结果断言仍逐动作检查，不以整句白名单豁免。

正常分支按服务/系统共同的受理及处理成功条件识别；仅受理、登录成功、尚未受理/处理成功、处理失败不能提供成功处理依据。正常标记先核所属分句的肯定/否定及业务对象，再映射本效果的目标/最近分句；另一工单的成功不支持云资源的充分结果。条件集合和作用域使用同一判断，原始原句不替换。明确正常分支、充分来源及作用域、普通定性目标、来源冲突联合追问仍复用原69项回归。

投影版本仍为内部 `result-conditions-1`，这是兼容的可选事实角色/属性补充，不新增模型响应或数据库字段，不改公共schema，不迁移旧行，不把材料中的确认变成正式系统审批。未决条件仍须澄清，未核对候选仍拒绝采纳，只有精确当前版本的人工核对可解除核对门。未知结果不能因禁止重复而被确认为失败。request cap、无限默认、JSON/引用/版本与格式修复保护代码没有放松。

## 实际测试

- 新 `tests/test_result_condition_false_positives.py` 有29项：两原句、未来动作变体与历史结果、肯定/否定/仅受理、同对象/其他对象、必要→充分误推、三种其他公共文本字段、未决/未核对门及隔离API真实核对/采纳边界。所有fixtures为纯合成，不写日常Store，不调用Provider。
- `targeted-final.xml/log/json`：**145 passed，退出0**，含上述29项、前轮69项、既有6项人工行为核对和41项request cap回归。最后一处调整仅使事实scope与效果对象一致；最终全量绑定最终源码，定向回执保留当时源码hash，不混为同一运行。
- `full-final.xml/log/json`：最终源码全量 **1394 passed / 5 skipped / 0 failed，退出0**，pytest耗时277.89秒；本轮29项包含在其中。五项Office opt-in本轮保持skip，未重跑Office，前轮实际Office证据保持其旧身份。两条现有弃用警告保留，未升级依赖。

全量命令：`.venv\Scripts\python.exe -X utf8 -m pytest tests -q --junitxml=evidence/offline-condition-boundaries-20261003-0252/full-final.xml`。`run_tests.py` 保存完整argv、cwd、准确退出码、耗时、app源码SHA256和测试源码SHA256，不用旧1365冒充本轮结果。旧的cap2/无限默认各一次MockTransport语义拒绝、repair0及usage保留例纳入全量，不追加付费格式修复。

首个工具准备命令因PowerShell引号解析错误而未写文件，改用明确新文件补丁；错误记入本轮任务卡，未执行旧脚本或改旧回执。前轮全部失败日志和本轮最初2项失败保留，不修改旧测试预期。

## 保护、归档与有限结论

`before.json` / `after.json` 逐行核对22项目/315 revisions/798 records/settings1/migrations1；189旧CALL逐文件hash、全部既有仓库evidence（含上一轮完整失败/JUnit/备份）、冻结材料、work/、原dist及配置仅哈希核对。七份明确旧协调回执/探针/测试源码亦只读对照。主 `P-f73f8130d36040af` v3的7候选、问题、来源和文档状态保留，反例 `P-f31d1a6267f041bc` 不动。

`source-file-integrity-before-restart.json` 另按未变项目保存的原始sha256核对119份来源，均匹配；只做字节哈希、不解析正文、不外传，data/sources目录共131文件。该补充快照在目标/全量测试之后、重启之前保存，不冒充任务起点文件快照；重启后再次比较整个131文件目录和原始来源hash。原项目row和原始sha元数据均按本轮起点逐行哈希保护。

仅本轮模块、新增回归和本说明三个明确路径本地Git归档；不add-A，不纳入数据、模型输出、配置或work/。最终SHA/父链/stat、精确git show、完整新增测试源码、JUnit及源码/唯一8765指纹绑定见本轮 `final-binding.json`。必要重启沿原脚本，在无inflight、GUI空输入后保留日常data/config/dist；无前端修改，不构建/另开入口或观察器。

最终app源码SHA256 `60455f5fbffededdafbd1dd83663b1d983c76c6b96c05271daa0924bcb6557d3`，正式8765 runtime_id `60455f5fbffededd`，与最终全量及四项复放一致。`scripts/stop.ps1`、`scripts/start.ps1 -NoAuth` 沿用原本机模式，只有127.0.0.1:8765监听；重启前在本轮忽略目录另存一致性SQLite备份，不提交或上传。重启后GUI空输入/无Stop；只读API确认主项目v3/七候选/开放Q-1e44e50ac69c4bb0/无文档，反例v2；旧TASK仍partial/calls1，未事后改成新拒绝或成功。

协调事故原样保留：`request-cap-independent-public-contract-20261002.json` 旧索引SHA `df31c785fcf178eefdc2fb81ed2c937cdad89e615f6322ba166e709e9b141cb7`，当前SHA `c8da0d5befb1f2a94e009ec81244292227d2bc214aa805151a1a7604c781e9a9`。恢复旧hash曾被自动审批拒绝，理由为可能破坏证据链；不恢复、不换路由，追加更正不改。审计首版Windows路径误判及exit1也保留。保护本轮当前回执不代表历史从未发生该事故，也不等于业务语义通过。

有限中文对象/动作/分句映射仍不能证明任意复杂指代、嵌套否定和跨句隐含条件；边界外继续需要人工审查。合成操作者核对仅发生在隔离测试，不是用户真实PM批准。真实整理效果、MRD/PRD、审查登记、确认、基线、MD/DOCX和机器交接均未进行；真实PM、新机/安装器、机器消费者及发布验证仍未验。完成本轮后停止，不自动扩展模型批次或声称全面ready。
