# UI-02 第一批实施记录

## 状态卡

基线 `34cb978c13c6be5a98e83d24e64fed903f7b792c`，PR #1 仍 OPEN；分支 `codex/ui-02-guided-workflow`，PR base 使用 `codex/ui-01-workbench-prototype`。
目标：统一需求工作区，原位置资料/授权/业务动作，服务端有限串行编排，正常应用入口离线证据。
范围：M0 契约与映射 → M1 工作区 → M2 业务任务 → M3 版本/身份/输入 → M4 回归与交付。连续推进，非每阶段等待许可。
不做：第二批、AUTH-01、真实模型续跑、合并/部署、工作流引擎。原 pilot v12/7次请求只保留，不作为夹具。
保护：用户 `.env.example` 删除、未跟踪 `原型/`、本机 `.env`/data/evidence 不纳入提交。
停止：五阶段主路径可用、主要动作真实执行、授权/预算/未知/版本/确认边界经离线回归；真实模型与真人体验分别 NOT_RUN。

## 入口映射与缺口

| 现有入口 | 新业务任务 | 复用接口/内容 | 必要缺口 |
|---|---|---|---|
| 讨论 + 阶段下拉 | 理解需求 / 持续助手 | sources、runs、questions | 动作计划、就地授权、vision→归纳的统一预算和任务身份 |
| 材料页 | 全阶段项目资料侧栏 | sources/text/file/url/exclude/retry | 精确片段定位、输入保留 |
| 方案标签 | 推演方案 | options/direction、options/items | 对象明确的预览；无对应产物时明确空态 |
| 摘要标签 | 明确需求 | items、questions | 当前缺口、核对本期内容；不是正式确认 |
| 原型页 | S2/S3 讨论原型 | prototype、ui-candidates | 生成对象/输入版本/产物身份记录；当前工作区直接执行 |
| 文档页 | 文档评审 | documents、artifacts | 原位置生成/更新/审查；保持历史只读下载边界 |
| 确认页 | 确认交接 | confirmations、exports、gate | 导向具体阶段的缺口入口，原门禁保持 |
| 历史/模型设置 | 辅助入口 | history、models | 返回原任务；高级参数折叠 |

## 新增契约（实现依据）

- `POST /api/projects/{pid}/actions/plan`：`expected_revision, action, message, document_type, option_id, max_calls`；只读计划，返回真实缺口、接收端、来源范围、总请求预算、步骤与 `plan_hash`，不发模型请求。
- `POST /api/projects/{pid}/actions`：以上字段 + `plan_hash, idempotency_key, authorize`；事务核对计划、授权范围、幂等与项目串行；持久化用户任务，再执行子 run。新增输入先保存为来源。403/409 不重放。
- `GET /api/projects/{pid}/actions`、`POST .../actions/{tid}/cancel`：真实任务及子 run 关联；取消不启动后续步骤。恢复采用重新核对剩余输入后显式新任务，不自动断点重放。
- 同一动作全部子 run 使用一个总请求额度（包含修复/网络重试），配置快照固定；外部版本或输入变化安全停止。旧工程 stage 接口保留，但不能与业务任务并发写入。
- UI 记录生成对象与输入版本；只允许为当前讨论方向或项目内容生成，未选方向独立原型本批不实现且明确提示。无对应关系不展示项目原型作为方案预览。

## 验证记录

待 M4 填入实际命令、退出码、截图及限制；本节不预填成功。
