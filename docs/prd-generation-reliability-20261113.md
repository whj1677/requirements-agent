# PRD 成文可靠性修复（2026-11-13）

基于 `codex/dpi150-20261030` 的 `9f5a612dfa5090de936fc12e2be4679713cad0cf`。

## 问题与修改

1. 程序从已保存需求编排的章节，误用了模型建议的 `explanations.maxItems=12`、`text.maxLength=1200` 限制。13 条基础需求或较长行为描述会确定性报 `SCHEMA_INVALID`。现在分别处理模型自由建议与程序保存内容；本机入口显式选择保存内容编排，混合详细需求、流程未知的评审稿也适用。固定章节的模型 schema 与实际固定值保持一致。
2. 最终文档的解释块可容纳两项各最多 6000 字符的行为字段及中间换行，共 12001 字符。原文完整保存，继续执行来源、引用和语义校验；模型自由计划仍保留原有 12 条、1200 字符限制。规范条款与来源说明作为一组，按每章 100 块限制分章，覆盖映射指向实际所在章节。
3. 当前成文检查通过，或允许保留未决事项且没有硬阻塞时，常规 PRD 入口使用已有本机成文服务。正文不依赖新增模型流程图成功。成文检查页保留明确的可选 AI 绘图入口，沿用空任务消息，避免操作提示被存成新业务来源、使有效检查过期。
4. 旧图仅复用当前交付范围内、仍已确认且来源指纹匹配的基础需求。移出本期的需求不会因为旧图被带入而使整份新 PRD 报错。
5. 同一预览提交失败后重试，继续使用相同的幂等键、版本和预览哈希，复用后端已经完成的生成结果。

未决事项、过期检查、来源及结构问题的门禁继续生效。生成文档不等于正式交付确认。

## 定向验证

使用隔离 Store、合成资料和离线 Provider，验证了：

- 13 条基础需求，以及两项各 6000 字符的行为字段；本机生成零模型调用，原文和引用完整，Word、Markdown 均保留完整拼接正文。
- 51 条基础需求；规范章节按 100/2 块拆分，条款及来源相邻、无重复遗漏，覆盖映射和最终 schema 一致。
- 13 条基础需求的未知流程，以及 13 条基础需求加 1 条详细需求；均能使用本机编排路径。
- 移出本期后旧图不再复用，仍在范围内且有效的旧图保留。
- 通过真实 Actions 编排入口发起可选绘图，离线模型返回不存在的连线端点；失败后原 PRD、来源、检查记录及检查输入哈希保持有效，正式导出就绪状态不变，Word、Markdown 实际生成成功。
- 前端检查状态路由、保留人工确认，以及响应丢失后使用原提交身份重试。

整合运行以下 14 个文件：**121 项通过，1 项受环境限制未完成**。

```bash
.venv/bin/python -m pytest -q \
  tests/test_local_prd.py tests/test_local_prd_flow_recovery.py \
  tests/test_prd_history_chunks.py tests/test_document_assembly_failure.py \
  tests/test_cloud_prd_content.py tests/test_prd_contract_consistency.py \
  tests/test_prd_structured_explanation_recovery.py tests/test_prd_product_sections.py \
  tests/test_prd_readability.py tests/test_document_type_contract.py \
  tests/test_basic_prd_flow_scope.py tests/test_ai_direct_frontend.py \
  tests/test_prewrite_batch_frontend.py tests/test_prewrite_conflict_frontend.py
```

受限项为既有的 `test_current_ui_context_and_discussion_image_without_adoption`：先因 Playwright 浏览器程序缺失失败；下载未得到有效安装包，改用本环境 `/usr/bin/chromium` 单项复测时，宿主拒绝浏览器创建 socket（`Operation not permitted`）。该项没有被标记为通过，未修改断言或产品代码绕过限制。

前端另有 `npm run test:frontend` **5 项通过**，`npm run typecheck` 和 Vite 构建通过；验证构建输出到忽略目录 `web/node_modules/.validation-prd`。

这是源码修复和离线验证，未调用真实模型、未运行用户实际失败项目，也未更新用户电脑的日常工作台。前端、后端均经过独立只读复核。
