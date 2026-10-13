import React, { useEffect, useState } from 'react';
import type { JsonObject as Obj } from '../api';
import { implementationFieldValue, validImplementationRefs, type AnswerImplementationRef } from '../answer-application-review';
import { requirementBehaviorLabels } from './requirement-fields';
import './answer-implementation.css';
import {detailWitnessOptions} from './detail-contract';

/** Select existing final clause text and explicitly attest to its meaning. */
export function AnswerImplementation({ questionId, questionText, answer, targets, value = [], disabled = false, onChange }: {
  questionId: string; questionText?: string; answer: string; targets: Obj[]; value?: AnswerImplementationRef[];
  disabled?: boolean; onChange: (refs: AnswerImplementationRef[]) => void;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  useEffect(() => { setChoices({}); }, [questionId, answer]);
  const valid = validImplementationRefs(value, targets);
  if (!answer.trim()) return null;
  return <fieldset className="answer-implementation"><legend>答案落实到哪里</legend>
    <p>选择已经表达答案的正文、业务说明或详细定义，再核对原文。尚未写入时可以先保存，回答会保留为待落实。</p>
    <div className="answer-implementation-context">{questionText&&<p><strong>当前问题</strong>{questionText.length>100?questionText.slice(0,100)+'…':questionText}</p>}<strong>当前回答摘要</strong><span>{answer.length > 180 ? answer.slice(0,180) + '…（完整回答见上方输入框）' : answer}</span></div>
    {!targets.length && <p role="status">尚无可核对的直接关联条款，请先核对问题与本期需求的关联。</p>}
    {targets.map(item => {
      const saved = value.find(ref => ref.item_id === item.id);
      const field = choices[item.id] ?? saved?.field ?? '';
      const options = [{ field: 'statement', label: item.kind === 'acceptance' ? '验收条件' : '条款正文' },
        ...(item.kind === 'acceptance' ? [] : Object.entries(requirementBehaviorLabels).map(([key, label]) => ({ field: 'behavior.' + key, label }))),
        ...(item.kind === 'acceptance' ? [] : detailWitnessOptions(item.detail_contract))];
      const quote = implementationFieldValue(item, field);
      const checked = valid.some(ref => ref.item_id === item.id && ref.field === field);
      const stale = !!saved && !valid.some(ref => ref.item_id === item.id);
      const available = options.filter(option => implementationFieldValue(item, option.field).trim());
      const chooseField = (next: string) => {
        setChoices(previous => ({ ...previous, [item.id]: next }));
        onChange(value.filter(ref => ref.item_id !== item.id));
      };
      return <div className="answer-implementation-target" key={item.id}>
        {!!available.length && <details className="answer-quote-catalog"><summary>预览已有原文：{item.title || item.id}（{available.length} 处）</summary>
          <p>先比较原句摘要；选择位置后查看完整原文，再勾选核对。</p>
          <dl>{available.map(option => { const text = implementationFieldValue(item, option.field); return <React.Fragment key={option.field}>
            <dt>{option.label}<button type="button" disabled={disabled} onClick={() => chooseField(option.field)}>选择此位置：{option.label}</button></dt>
            <dd>{text.length > 160 ? text.slice(0, 160) + '…' : text}</dd>
          </React.Fragment>; })}</dl>
        </details>}
        <label>{item.title || item.id} · 承载答案的位置<select aria-label={(item.title || item.id) + ' 答案落文位置'} disabled={disabled} value={field} onChange={event => {
          chooseField(event.target.value);
        }}><option value="">请选择已表达答案的位置</option>{options.map(option => <option key={option.field} value={option.field} disabled={!implementationFieldValue(item, option.field).trim()}>{option.label}</option>)}</select></label>
        {field && <blockquote aria-label={(item.title || item.id) + ' 承载答案原文'}>{quote || '此处尚未填写，请先补入正文或业务说明。'}</blockquote>}
        {stale && <p role="status">此前选择的原文已变化，请重新核对；系统没有自动替换引用。</p>}
        {field && !!quote.trim() && <label className="answer-implementation-check"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange([
          ...value.filter(ref => ref.item_id !== item.id), ...(event.target.checked ? [{ item_id: item.id, field, quote }] : []),
        ])}/>我已核对此原文表达了答案</label>}
      </div>;
    })}
  </fieldset>;
}
