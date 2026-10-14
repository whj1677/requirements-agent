import type { JsonObject, Project } from './api';
import { detailPropertyLabels, detailRows, detailSectionLabels, type DetailSection } from './pm-prototype/detail-contract';
import { requirementBehaviorLabels } from './pm-prototype/requirement-fields';

/** Present the business object first; keep exact IDs and paths available as
 * evidence without making a source-code path the reader's primary label. */
export function prewriteQuoteLabel(project: Project, quote: JsonObject): string {
  const item = project.items.find(value => value.id === quote.object_id) ||
    project.questions.find(value => value.id === quote.object_id);
  if (!item) return '关联原文';
  const title = item.title || item.question || '关联需求';
  const rawPath = String(quote.path || '');
  const path = rawPath.startsWith(item.id + '.') ? rawPath.slice(item.id.length + 1) : rawPath;
  const parts = path.split('.');
  if (parts[0] === 'behavior') return title + ' / ' + (requirementBehaviorLabels[parts[1]] || '业务说明');
  if (parts[0] !== 'detail_contract') return title + ' / ' +
    (({ statement:'需求正文', answer:'业务回答', question:'待确认问题', title:'名称' } as Record<string,string>)[parts[0]] || '原文');
  if (parts[1] === 'applicability') return title + ' / ' + (detailSectionLabels[parts[2] as DetailSection] || '详细定义') + ' / ' + (parts[3] === 'reason' ? '不适用原因' : '适用情况');
  if (parts[1] === 'depends_on') return title + ' / 依赖的需求';
  const isFlow = parts[1] === 'flow', section = isFlow ? 'flow.' + parts[2] : parts[1];
  const id = parts[isFlow ? 3 : 2], property = parts[isFlow ? 4 : 3];
  const row = detailRows(item.detail_contract, section).find(value => value.id === id);
  let name = row?.name || row?.label;
  if (!name && row?.from_state) name = row.from_state + ' → ' + (row.to_state || '待确定');
  if (!name && row?.from) {
    const nodes = detailRows(item.detail_contract, 'flow.nodes');
    name = (nodes.find(value => value.id === row.from)?.label || '起点步骤') + ' → ' +
      (nodes.find(value => value.id === row.to)?.label || '终点步骤');
  }
  return [title, name || detailSectionLabels[parts[1] as DetailSection] || '详细定义',
    property ? detailPropertyLabels[property] || '属性原文' : undefined].filter(Boolean).join(' / ');
}

export const prewriteStatusLabels: Record<string,string> = {
  not_run:'尚未进行成文检查', running:'正在进行成文检查', failed:'成文检查尚未完成',
  stale:'内容已有变化，需要复检', needs_confirmation:'有业务问题需要确认', passed:'当前内容已通过成文检查',
};

/** A historical pass never becomes a pass for today's requirements. */
export function prewritePassed(review: JsonObject | undefined): boolean {
  return review?.status === 'passed' && review.current === true;
}
export function prewriteLabel(review: JsonObject | undefined): string {
  if (review?.status === 'passed' && !review.current) return prewriteStatusLabels.stale;
  return prewriteStatusLabels[review?.status || 'not_run'] || '检查状态暂未确认';
}
export function prewriteCanDraft(review: JsonObject | undefined): boolean {
  return review?.current === true && review.status === 'needs_confirmation' &&
    !(review.findings || []).some((finding: JsonObject) => finding.hard_block === true && finding.status !== 'resolved');
}
export function prewriteFindingLabel(finding: JsonObject): string {
  if (finding.status === 'resolved') return '复检已解决';
  if (finding.status === 'awaiting_recheck') return '已处理，待复检';
  if (finding.status === 'answered') return '回答已存，待落实';
  return finding.severity === 'info' ? '建议' : finding.severity === 'warning' ? '待核对' : '待确认';
}
