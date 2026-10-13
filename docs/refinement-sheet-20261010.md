# 单表完善需求与一次确认（2026-10-10）

## 操作

1. 从“提供信息”进入“完善需求”，聊天和资料的已整理内容直接带入确认表。
2. 在表格内上下滚动，核对摘要、本期范围、需求说明、验收和业务决定。必填项标注“必填”，没有依据的可选项可以留空。
3. 可点击“AI 补充建议”，或采用问题中的 AI 推荐回答。模型任务仍先展示本次范围，点击运行才调用；建议不会自动确认。已有人工修改保留；收到新建议后可合并更新，改过的单元格优先。
4. 点击固定在表格底部的“确认并进入交付”。缺项就地提示并聚焦；全部校验成功后一次保存、确认并自动进入交付。正式文档与最终交付基线仍在下一阶段处理。

范围选择不是临时批量勾选。不纳入本期的需求保留内容，其专属问题随范围暂缓；重新纳入后恢复待核对。组原话只作参考，不自动充当每个子问题的答案。

## 实现与保护

- `RequirementTable.tsx` 取代逐条展开、批量选择、二次预览、阶段确认等重复操作；`ConnectedWorkspace.tsx` 和 `workspace.tsx` 移除不再使用的旧完善表单，防止隐藏编辑器误报脏状态。
- `POST /api/projects/{pid}/refinement-confirmation` 在同一事务处理本表内容、来源、问题覆盖和前三阶段检查。失败零部分保存，返回单元格定位；过期版本及幂等冲突不覆盖已有内容。
- PRD/MRD 的“准备生成”明确还需运行任务，现有任务状态显示在生成工具栏附近，按文档类型区分。
- 测试通过并不代表 AI 文本准确或用户已接受业务规则；没有替电价管理项目填写答案或建立正式确认。

## 验证

隔离回归命令：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_refinement_confirmation.py tests/test_requirement_batch_confirmation.py tests/test_requirement_confirmation.py tests/test_requirement_change_confirmation.py tests/test_manual_decision_review.py tests/test_document_task_progress_frontend.py tests/test_document_write_projection.py tests/test_task_progress_frontend.py tests/test_product_flow.py -q
```

结果：143 passed。TypeScript 与 Vite 构建通过。浏览器使用无监听服务的隔离 ASGI 路由与禁止模型调用的测试 provider，操作录像、API 记录、项目快照和构建哈希见 `evidence/refinement-sheet-20261010/`。

电价管理使用冻结 v13 的 10 条需求、15 份资料副本做只读核对，未改写原文。页面仍有真实未明确信息；本轮不以虚构答案强行通过。
