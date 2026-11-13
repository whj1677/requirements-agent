# 记忆准备后的任务进度

观察进度修复，不作为记忆质量收益。memory_trace ready 的直接主请求，在原 before_send 配额预留和 dispatch 登记成功后，更新为“正在处理本轮需求（本步骤第 N 次模型请求）”。不改变模型消息、HTTP、配额或取消语义；零条记忆亦使用此中性文案。

auxiliary、DSH 和直接主请求复用一个事务写入：同 project/id/kind，任务和 run 均 running，任务包含 run，run.user_task_id 匹配。终态、其他项目或失去绑定不写。

隔离合成 Provider 回归覆盖主请求挂起、零记忆、取消、预算耗尽和原 DSH 进度回调；无真实模型、Office 或业务数据操作。
