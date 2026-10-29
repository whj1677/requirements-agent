import React from 'react';
import type { UserTask } from './api';

export function canRecoverDraft(task:UserTask) {
  return !task.local_recovery&&task.status==='failed'&&(task.local_recovery_available===true||task.action==='organize'&&
    !task.retry_input?.target&&!task.retry_input?.benchmark&&/JSON 有重复键/.test(task.message));
}

export function taskFailure(task: UserTask) {
  if (task.local_recovery) return null;
  if(task.status==='failed'&&task.error==='PREWRITE_DECISION_BINDING_REQUIRED')return {
    summary:'AI 检查结果没有正确关联到需求，这次成文检查未完成。',
    next:'你的回答和已有需求都已保留，不用重新填写。先核对已有修改，再重新进行成文检查；下方旧检查项仅供继续处理。',
    detail:task.message,
  };
  if (task.status !== 'failed' || !['SCHEMA_INVALID','REFERENCE_INVALID','SEMANTIC_BLOCKED'].includes(task.error || '')) return null;
  const repairChanged = task.error === 'SEMANTIC_BLOCKED' && /格式修复改变/.test(task.message);
  const unrequestedBenchmark = task.action === 'organize' && /本次没有对标任务|普通整理包含未请求的对标内容/.test(task.message);
  return {
    summary: canRecoverDraft(task) ? '原始输出可在本机恢复为待核对草稿，已有内容保留。'
      : unrequestedBenchmark ? '普通整理混入了未请求的对标内容，本次草稿未生成。'
      : repairChanged ? 'AI 修复输出格式时同时改写了需求文本，本次生成未写入。'
      : task.error === 'SEMANTIC_BLOCKED' ? '本次生成未通过来源与内容核对，结果未写入。'
      : 'AI 返回的格式或引用未能通过校验，本次生成未写入。',
    next: canRecoverDraft(task) ? '先点击“恢复草稿”：系统会检查保存的输出，内容相同且校验有效时直接进入核对页，不调用 AI，也不用重新描述。内容有冲突时会说明原因并保留原文。'
      : unrequestedBenchmark ? '这不是缺少必填资料，无需补充竞品信息。已保存的资料和需求保留，可前往需求草稿继续填写；重新生成会再次调用模型。'
      : '已保存的资料和现有需求保留。可以继续核对已有草稿；需要重新生成时，请核对任务范围后重试。',
    detail: task.message,
  };
}

export function TaskFailureNotice({task}: {task: UserTask}) {
  const failure = taskFailure(task);
  if (!failure) return null;
  return <div className="task-failure-notice"><p>{failure.summary}</p><p>{failure.next}</p><details><summary>查看本次失败详情</summary><p className="preserve-lines">{failure.detail}</p></details></div>;
}
