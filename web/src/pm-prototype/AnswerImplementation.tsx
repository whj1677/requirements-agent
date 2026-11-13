import React, { useEffect, useState } from 'react';
import type { JsonObject as Obj } from '../api';
import { implementationFieldValue, validImplementationRefs, type AnswerImplementationRef } from '../answer-application-review';
import { requirementBehaviorLabels } from './requirement-fields';
import './answer-implementation.css';
import {detailWitnessOptions} from './detail-contract';

type AnswerLocationSuggestion = { item_id: string; field: string; quote: string; score: number; reason: string };

type Location = { item: Obj; option: { field: string; label: string }; text: string };

function optionsFor(item: Obj) {
  return [{ field: 'statement', label: item.kind === 'acceptance' ? '验收条件' : '条款正文' },
    ...(item.kind === 'acceptance' ? [] : Object.entries(requirementBehaviorLabels).map(([key, label]) => ({ field: 'behavior.' + key, label }))),
    ...(item.kind === 'acceptance' ? [] : detailWitnessOptions(item.detail_contract))];
}

function lexicalTerms(text: string) {
  const words = text.toLowerCase().match(/[a-z0-9]+/g) || [];
  const pairs = (text.match(/[\u3400-\u9fff]+/g) || []).flatMap(run =>
    Array.from({ length: Math.max(0, run.length - 1) }, (_, index) => run.slice(index, index + 2)));
  return [...new Set([...words, ...pairs].filter(term => term.length > 1))];
}

/** Select existing final clause text and explicitly attest to its meaning. */
export function AnswerImplementation({ questionId, questionText, answer, targets, value = [], disabled = false, suggestions = [], onChange }: {
  questionId: string; questionText?: string; answer: string; targets: Obj[]; value?: AnswerImplementationRef[];
  disabled?: boolean; suggestions?: AnswerLocationSuggestion[]; onChange: (refs: AnswerImplementationRef[]) => void;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [catalogSearch, setCatalogSearch] = useState('');
  useEffect(() => { setChoices({}); }, [questionId, answer]);
  const valid = validImplementationRefs(value, targets);
  const locations: Location[] = targets.flatMap(item => optionsFor(item).flatMap(option => {
    const text = implementationFieldValue(item, option.field);
    return text.trim() ? [{ item, option, text }] : [];
  }));
  const answerTerms = lexicalTerms(answer);
  const related = locations.map(location => {
    const lexical = answerTerms.reduce((score, term) => score + (location.text.toLowerCase().includes(term) ? 1 : 0), 0);
    const suggested = suggestions.some(suggestion => suggestion.item_id === location.item.id &&
      suggestion.field === location.option.field && suggestion.quote === location.text);
    return { ...location, lexical, score: lexical + (suggested ? 1 : 0) };
  }).filter(location => location.lexical > 0).sort((a, b) => b.score - a.score).slice(0, 5);
  const search = catalogSearch.trim().toLowerCase();
  const searchResults = locations.filter(location =>
    (location.item.title || location.item.id).toLowerCase().includes(search) ||
    location.option.label.toLowerCase().includes(search) || location.text.toLowerCase().includes(search));
  const chooseField = (itemId: string, next: string) => {
    setChoices(previous => ({ ...previous, [itemId]: next }));
    onChange(value.filter(ref => ref.item_id !== itemId));
  };
  const renderLocationCard = (location: Location) => <article className="answer-location-card" key={location.item.id + ':' + location.option.field}>
    <strong>{location.item.title || location.item.id} · {location.option.label}</strong>
    <p>{location.text.length > 160 ? location.text.slice(0, 160) + '…' : location.text}</p>
    <button type="button" disabled={disabled} onClick={() => {
      chooseField(location.item.id, location.option.field);
      document.getElementById(`answer-target-${questionId}-${location.item.id}`)?.scrollIntoView({ block: 'center' });
    }}>选择此位置</button>
  </article>;
  if (!answer.trim()) return null;
  return <fieldset className="answer-implementation"><legend>答案落实到哪里</legend>
    <p>选择已经表达答案的正文、业务说明或详细定义，再核对原文。尚未写入时可以先保存，回答会保留为待落实。</p>
    <div className="answer-implementation-context">{questionText&&<p><strong>当前问题</strong>{questionText.length>100?questionText.slice(0,100)+'…':questionText}</p>}<strong>当前回答摘要</strong><span>{answer.length > 180 ? answer.slice(0,180) + '…（完整回答见上方输入框）' : answer}</span></div>
    {!!related.length && <section className="answer-location-related">
      <h3>相关位置，需核对含义</h3>
      <p>按当前回答词面及已有导航建议排列，不代表答案已经落实。</p>
      <div className="answer-location-list">{related.map(renderLocationCard)}</div>
    </section>}
    {!related.length && !!locations.length && <p role="status">未找到与当前回答词面匹配的位置，请搜索完整目录。</p>}
    {!!locations.length && <details className="answer-location-catalog">
      <summary>搜索完整目录（{locations.length} 处）</summary>
      <label>搜索位置名称或原文
        <input type="search" value={catalogSearch} disabled={disabled} onChange={event => setCatalogSearch(event.target.value)} placeholder="输入位置名称或原文词语"/>
      </label>
      {search && !searchResults.length && <p role="status">没有匹配的位置，请换用其他词语。</p>}
      {search && <div className="answer-location-list">{searchResults.map(renderLocationCard)}</div>}
    </details>}
    {!targets.length && <p role="status">尚无可核对的直接关联条款，请先核对问题与本期需求的关联。</p>}
    {targets.map(item => {
      const saved = value.find(ref => ref.item_id === item.id);
      const field = choices[item.id] ?? saved?.field ?? '';
      const options = optionsFor(item);
      const quote = implementationFieldValue(item, field);
      const checked = valid.some(ref => ref.item_id === item.id && ref.field === field);
      const stale = !!saved && !valid.some(ref => ref.item_id === item.id);
      return <div className="answer-implementation-target" id={`answer-target-${questionId}-${item.id}`} key={item.id}>
        <label>{item.title || item.id} · 承载答案的位置<select aria-label={(item.title || item.id) + ' 答案落文位置'} disabled={disabled} value={field} onChange={event => {
          chooseField(item.id, event.target.value);
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
