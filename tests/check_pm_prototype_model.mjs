// Execute the actual pure TypeScript model; no browser, API, or model requests.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from '../web/node_modules/typescript/lib/typescript.js';
const source = fs.readFileSync(new URL('../web/src/demo/pm-prototype/model.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const M = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const results = [];
const test = (name, fn) => { fn(); results.push({ name, status: 'executed_pass' }); };
const start = (raw = M.SAMPLE) => { const s = M.initialState(); s.raw = raw; M.startDraft(s, 'direct'); return s; };
const manual = (s, qid) => { s.manual[qid] = { rule: `${qid} 的已确认规则`, acceptance: `${qid} 的可核对结果` }; M.manualSave(s, qid); };
const proposal = (s, answer) => { s.answers.concurrency = answer; M.beginJob(s, 'proposal', 'concurrency'); M.finishJob(s); };

test('原文可编辑且保存不触发 AI；已知缺口明确阻塞正式确认', () => {
  const s = start(); assert.equal(s.job, null); assert.equal(s.modules.length, 3); assert.equal(M.criticalQuestions(s).length, 2);
  M.confirmVersion(s); assert.equal(s.versions.length, 0);
});
test('同一人工答案重复保存幂等，修改答案替换旧片段', () => {
  const s = start(); manual(s, 'concurrency'); const body = s.modules[0].body;
  M.manualSave(s, 'concurrency'); assert.equal(s.modules[0].body, body);
  s.manual.concurrency.rule = '新的冲突处理规则'; M.manualSave(s, 'concurrency');
  assert.ok(!s.modules[0].body.includes('concurrency 的已确认规则'));
  assert.equal(s.modules[0].body.split('新的冲突处理规则').length, 2);
  assert.ok(M.isResolved(s, s.questions[0]));
});
test('采用前不写正文，拒绝不解决问题，修改后的候选才被采用', () => {
  const s = start(), before = s.modules[0].body; proposal(s, s.questions[0].choices[0]);
  assert.equal(s.modules[0].body, before); assert.equal(M.criticalQuestions(s).length, 2);
  M.reject(s); assert.equal(s.modules[0].body, before); assert.equal(M.criticalQuestions(s).length, 2);
  proposal(s, s.questions[0].choices[0]); s.batch.patches[1].text = '验收：被保留的输入能够重新提交。'; M.adopt(s);
  assert.ok(s.modules[0].body.includes('验收：被保留的输入能够重新提交。'));
  assert.ok(!s.modules[0].body.includes(s.questions[0].placeholder)); assert.equal(M.criticalQuestions(s).length, 1);
});
test('不同回答的规则与验收不能拼成已确认，补齐同一批后才解决', () => {
  const s = start(); proposal(s, '回答 A'); M.adopt(s, 'rule'); M.reject(s, 'acceptance');
  const firstEpoch = s.batch.epoch;
  proposal(s, '回答 B');
  assert.notEqual(s.batch.epoch, firstEpoch, 'independent jobs must have different identities');
  M.adopt(s, 'acceptance'); assert.ok(!M.isResolved(s, s.questions[0]));
  M.adopt(s, 'rule'); assert.ok(M.isResolved(s, s.questions[0])); assert.ok(!s.modules[0].body.includes('回答 A'));
});
test('过期建议先阻止写入；重新核对后保留新编辑', () => {
  const s = start(); proposal(s, '保留并发输入');
  s.editor = { moduleId: 'm1', text: s.modules[0].body + '\n\n人工新补充，必须保留。' }; M.saveEditor(s);
  const before = s.modules[0].body; M.adopt(s); assert.equal(s.modules[0].body, before); assert.ok(s.batch.conflict);
  M.rebase(s); assert.equal(s.modules[0].body, before); M.adopt(s); assert.ok(s.modules[0].body.includes('人工新补充，必须保留。'));
});
test('跨批次部分采用后手工补齐也替换旧规则，不遗留互斥片段', () => {
  const s = start(); proposal(s, '回答 A'); M.adopt(s, 'rule'); M.reject(s, 'acceptance');
  proposal(s, '回答 B'); M.adopt(s, 'acceptance'); M.reject(s, 'rule');
  assert.ok(!M.isResolved(s, s.questions[0]));
  manual(s, 'concurrency'); assert.ok(M.isResolved(s, s.questions[0]));
  assert.ok(!s.modules[0].body.includes('回答 A')); assert.ok(!s.modules[0].body.includes('回答 B'));
});
test('新编辑仅令受影响模块重核；旧核对不能静默继续有效', () => {
  const s = start(); manual(s, 'concurrency'); manual(s, 'visibility');
  s.editor = { moduleId: 'm1', text: s.modules[0].body + '\n\n另有新规则待核对。' }; M.saveEditor(s);
  assert.ok(!M.isResolved(s, s.questions[0])); assert.ok(M.isResolved(s, s.questions[1]));
});
test('失败不写结果；未知状态查询沿用原任务和旧回答', () => {
  const s = start(), before = s.modules[0].body; s.aiOutcome = 'failed'; proposal(s, '原回答');
  assert.equal(s.job.status, 'failed'); assert.equal(s.batch, null); assert.equal(s.modules[0].body, before);
  s.answers.concurrency = '尚未提交的新回答'; s.aiOutcome = 'unknown'; M.retryJob(s); const id = s.job.id; M.finishJob(s);
  assert.equal(s.job.status, 'unknown'); assert.equal(s.modules[0].body, before); M.finishJob(s, true);
  assert.equal(s.job.id, id); assert.ok(s.batch.patches[0].text.includes('原回答')); assert.ok(!s.batch.patches[0].text.includes('尚未提交的新回答'));
});
test('非本期未知不阻塞交付；文件重试同一快照；新草稿不改旧版', () => {
  const s = start(); manual(s, 'concurrency'); manual(s, 'visibility'); assert.equal(M.openQuestions(s).length, 1); assert.deepEqual(M.blockingReasons(s), []);
  s.fileOutcome = 'failed'; M.confirmVersion(s); const snapshot = JSON.stringify(s.versions[0].modules); M.finishFiles(s, 1);
  assert.equal(s.versions[0].status, 'failed'); s.fileOutcome = 'success'; M.retryFiles(s, 1); M.finishFiles(s, 1);
  assert.equal(s.versions.length, 1); assert.equal(JSON.stringify(s.versions[0].modules), snapshot);
  M.continueVersion(s, 1); assert.equal(s.version, 2); s.modules[2].body += '\n新增内容'; assert.equal(JSON.stringify(s.versions[0].modules), snapshot);
});
test('自定义材料不套入样例业务；讨论稿明确非正式', () => {
  const s = start('仓储盘点需求：记录货架编号。'); assert.equal(s.modules.length, 1); assert.equal(s.questions[0].id, 'custom');
  const md = M.documentMarkdown(s.modules, 1, false, s.questions, s.resolutions, 'MRD', s.sourceRaw);
  assert.ok(md.includes('仓储盘点')); assert.ok(md.includes('待确认 / 非正式交付')); assert.ok(md.includes('用户粘贴的原文'));
  assert.ok(!md.includes('销售团队')); assert.ok(!md.includes(M.PROJECT)); assert.ok(M.fileName(1, 'MRD', s.sourceRaw).startsWith('自定义需求示例'));
});
const out = new URL('../evidence/pm-prototype-20261008/model-checks.json', import.meta.url);
fs.mkdirSync(new URL('.', out), { recursive: true });
fs.writeFileSync(out, JSON.stringify({ scope: 'prototype_state_only', real_model_calls: 0, results }, null, 2));
console.log(JSON.stringify({ executed: results.length, passed: results.length, scope: 'prototype_state_only' }));
