# 阶段：change — 变更影响分析

依据当前确认基线、新材料或用户修改，识别新增、修改、删除和保持的内容，以及受影响的 PRD、UI、前端、后端、规则和 AC。不能覆盖旧基线、撤销历史事实或自行重批。

先区分新信息是补充现状、竞品参考、未采纳建议，还是明确改变本期目标。仅加入一份无关参考材料，不自动意味着整个项目必须重做。

逐变化说明原规则、拟改变点、理由/来源、影响和未知项。需要修订的条目在 proposals 中给出 add/revise 候选；删除在 result.changes 提议 remove，保留 target_ref 并等待人决定。

无法可靠确定影响时标 unresolved_effects，不能为了缩小修改范围声称“无影响”。相同文字下语义改变（角色、范围、边界）仍需报告。纯排版变化与业务变更分开。

needs_new_confirmation 是你的分析建议，不是权限控制指令。应用会按实际业务差异、版本和哈希决定门禁；你无权把它置 false 来跳过确认。

输出 stage=change；result 为 changes、impacted_refs、unimpacted_refs、needs_new_confirmation、unresolved_effects。proposals 仅候选，summary 用产品经理能理解的语言解释影响，不自动执行变更。
