// This preview deliberately has no API client. All sources and model outcomes are synthetic.
export const STORAGE_KEY = 'pm-req:prototype:v1';
export const PROJECT = '客户跟进工作台';
export type Page = 'home' | 'intake' | 'workspace' | 'delivery' | 'history';
export type Module = { id: string; title: string; body: string };
export type Question = { id: string; moduleId: string; title: string; detail: string; critical: boolean; choices: string[]; placeholder?: string };
export type Resolution = { rule: string; acceptance: string; ruleEpoch?: number; acceptanceEpoch?: number; reviewed?: boolean };
export type Patch = { id: 'rule' | 'acceptance'; title: string; text: string; replace?: string; status: 'pending' | 'adopted' | 'rejected' };
export type Batch = { qid: string; epoch: number; baseBody: string; patches: Patch[]; conflict: boolean; rebased: boolean };
export type Job = { id: number; kind: 'organize' | 'proposal'; status: 'running' | 'unknown' | 'failed' | 'done'; outcome: 'success' | 'failed' | 'unknown'; due: number; qid?: string; answer?: string; baseBody?: string };
export type Version = { number: number; modules: Module[]; resolutions: Record<string, Resolution>; questions: Question[]; deferred: string[]; raw: string; files: ('PRD' | 'MRD')[]; confirmedAt: string; status: 'generating' | 'unknown' | 'failed' | 'ready'; due: number; outcome: 'success' | 'failed' };
export type State = {
  schema: 1; page: Page; mode: 'chat' | 'direct'; raw: string; sourceRaw: string; modules: Module[]; questions: Question[];
  answers: Record<string, string>; manual: Record<string, Resolution>; resolutions: Record<string, Resolution>; deferred: string[];
  selectedQ: string; editor: { moduleId: string; text: string } | null; batch: Batch | null; job: Job | null;
  version: number; revision: number; locked: boolean; versions: Version[]; includeMrd: boolean; returnToCheck: boolean;
  notice: string; aiOutcome: 'success' | 'failed' | 'unknown'; fileOutcome: 'success' | 'failed';
  events: { event: string; at: string }[];
};

export const SAMPLE = `# 客户资料
我们需要一个供小型销售团队使用的客户跟进工作台。本期只做客户资料、跟进记录和统计看板，先让现有的线索与后续跟进放在同一个地方。

成员手工建立客户卡片，填写客户名称、联系人、联系电话、当前进展和备注。客户名称必填；联系电话允许暂时为空，不能因为没有手机号而无法保存。用户可以从客户列表打开详情，修改现有资料。

列表按最近更新排序，支持按客户名称搜索。列表需要区分没有客户与搜索没有匹配结果，分别提示新建客户和调整搜索条件。编辑时可以取消，取消后回到原来的客户详情。

同一个客户可能由不同成员先后维护。如果两个人同时修改同一张卡片，如何处理尚未决定，需要讨论。这个决定会影响保存操作，正式交付前需要明确。

本期不做外部平台同步、智能去重与批量导入。无需为这些尚未纳入本期的能力填写流程。

# 跟进记录
成员在客户详情中新增跟进记录，记录跟进时间、沟通内容和下一步计划。沟通内容必填；下一步计划可以留空，保存后在该客户的时间线中展示。

成员可以修改自己录入的记录，界面显示最后修改时间。记录按跟进时间倒序展示，未有记录时提示添加首条跟进。填写过程中返回客户详情，仍应保留尚未提交的输入，方便继续填写。

不同成员能否看到彼此的记录尚未决定。是整个项目可见，还是仅创建人可见，需要产品负责人明确。此项影响数据的展示范围，应在正式交付前确认。

本期没有短信提醒、自动外呼或日历同步。不要因模板存在这些栏目而默认增加需求。

# 统计看板
看板展示本项目的客户总数和近七天新增客户数，以已保存的客户卡片为依据。统计范围为本项目，日期按工作台使用的本地日期计算。

看板每天打开时读取当前数据，也可以点击刷新。没有数据时显示数字零；读取失败时保留上次已展示结果并提供重试入口，不能把失败显示成没有客户。

本期不做成员业绩排行或销售额预测。是否在下一期导入历史客户还没有决定，该事项保留在后续计划，不阻塞本期三个模块的交付。`;

export const QUESTIONS: Question[] = [
  { id: 'concurrency', moduleId: 'm1', title: '两人同时修改客户，怎样保存？', detail: '影响客户资料的保存规则与验收；请明确发生冲突时的行为。', critical: true, choices: ['提示刷新后再提交，保留本次修改', '以后提交的内容覆盖之前的修改'], placeholder: '同一个客户可能由不同成员先后维护。如果两个人同时修改同一张卡片，如何处理尚未决定，需要讨论。这个决定会影响保存操作，正式交付前需要明确。' },
  { id: 'visibility', moduleId: 'm2', title: '谁能看到跟进记录？', detail: '影响跟进记录的展示范围与验收。', critical: true, choices: ['本项目所有成员可见', '仅创建人可见'], placeholder: '不同成员能否看到彼此的记录尚未决定。是整个项目可见，还是仅创建人可见，需要产品负责人明确。此项影响数据的展示范围，应在正式交付前确认。' },
  { id: 'next-phase', moduleId: 'm3', title: '下一期是否导入历史客户？', detail: '已明确不属于本期范围，可以留到后续规划。', critical: false, choices: [] },
];

export function initialState(): State {
  return { schema: 1, page: 'home', mode: 'chat', raw: '', sourceRaw: '', modules: [], questions: [], answers: {}, manual: {}, resolutions: {}, deferred: [], selectedQ: '', editor: null, batch: null, job: null, version: 1, revision: 0, locked: false, versions: [], includeMrd: false, returnToCheck: false, notice: '', aiOutcome: 'success', fileOutcome: 'success', events: [] };
}
export function note(s: State, event: string) { s.events.push({ event, at: new Date().toISOString() }); }
export function moduleFor(s: State, qid: string) { return s.modules.find(m => m.id === s.questions.find(q => q.id === qid)?.moduleId); }
export function isResolved(s: Pick<State, 'resolutions' | 'modules' | 'questions'>, q: Question) {
  const r = s.resolutions[q.id], body = s.modules.find(m => m.id === q.moduleId)?.body ?? '';
  return !!r?.reviewed && r.ruleEpoch !== undefined && r.ruleEpoch === r.acceptanceEpoch && !!r.rule.trim() && !!r.acceptance.trim() && body.includes(r.rule) && body.includes(r.acceptance);
}
export function openQuestions(s: State) { return s.questions.filter(q => !isResolved(s, q)); }
export function criticalQuestions(s: State) { return openQuestions(s).filter(q => q.critical); }
export function pendingPatches(s: State) { return s.batch?.patches.filter(p => p.status === 'pending') ?? []; }
export function dirty(s: State) { return !!s.editor && s.editor.text !== s.modules.find(m => m.id === s.editor?.moduleId)?.body; }
export function hasDraft(s: State) { return s.modules.length > 0; }
export function blockingReasons(s: State): string[] {
  const reasons = criticalQuestions(s).map(q => q.title);
  if (dirty(s)) reasons.push('正文编辑尚未保存');
  if (pendingPatches(s).length) reasons.push('还有待核对的建议');
  if (s.job && ['running', 'unknown'].includes(s.job.status)) reasons.push('本次演示任务还没有明确结果');
  return reasons;
}

export function startDraft(s: State, mode: State['mode']) {
  if (!s.raw.trim()) return;
  s.mode = mode; s.sourceRaw = s.raw;
  // Only the unmodified, explicitly loaded fixture has known predefined questions.
  if (s.raw.trim() === SAMPLE.trim()) {
    s.modules = SAMPLE.split(/^# /m).filter(Boolean).map((part, i) => ({ id: `m${i + 1}`, title: part.slice(0, part.indexOf('\n')), body: part.slice(part.indexOf('\n') + 1).trim() }));
    s.questions = structuredClone(QUESTIONS);
  } else {
    s.modules = [{ id: 'm1', title: '已保存的原文', body: s.raw.trim() }];
    s.questions = [{ id: 'custom', moduleId: 'm1', title: '确认这份材料的本期范围与验收', detail: '此原型不会分析自定义材料。请手工核对内容后补充本期规则与验收；这里只演示操作。', critical: true, choices: [] }];
  }
  s.selectedQ = s.questions[0].id; s.page = 'workspace'; s.revision = 1;
  s.notice = mode === 'direct' ? '原文已存入本浏览器，可直接编辑；尚未运行 AI。' : '先保留可编辑的原文草稿，正在演示整理过程。';
  note(s, 'first_editable_draft');
  if (mode === 'chat') beginJob(s, 'organize');
}

export function beginJob(s: State, kind: Job['kind'], qid?: string) {
  if (s.locked || (s.job && ['running', 'unknown'].includes(s.job.status))) return;
  if (kind === 'proposal' && (!qid || !s.answers[qid]?.trim() || pendingPatches(s).length)) return;
  s.job = { id: Math.max(Date.now(), (s.job?.id ?? 0) + 1), kind, status: 'running', outcome: s.aiOutcome, due: Date.now() + 1100, qid, answer: qid ? s.answers[qid].trim() : undefined, baseBody: qid ? moduleFor(s, qid)?.body : undefined };
  s.notice = ''; note(s, `simulated_${kind}_started`);
}
export function finishJob(s: State, queried = false) {
  const job = s.job;
  if (!job || !['running', 'unknown'].includes(job.status)) return;
  if (job.outcome === 'unknown' && !queried) { job.status = 'unknown'; s.notice = '模拟连接中断。原文与草稿保留；可查询这次任务，或继续手工编辑。'; return; }
  if (job.outcome === 'failed') { job.status = 'failed'; s.notice = '演示任务失败，未写入任何建议。现有草稿仍可编辑。'; note(s, 'simulated_failure'); return; }
  job.status = 'done';
  if (job.kind === 'proposal' && job.qid && job.answer) {
    const q = s.questions.find(q => q.id === job.qid)!;
    const previous = s.resolutions[job.qid];
    const specific: Record<string, string> = {
      '提示刷新后再提交，保留本次修改': '甲先保存后，乙提交旧版本时应收到刷新提示；乙的输入仍可查看，刷新后可重新提交。',
      '以后提交的内容覆盖之前的修改': '甲保存后乙再次保存，同一字段显示乙最后提交的值。',
      '本项目所有成员可见': '创建人保存记录后，本项目另一成员打开该客户详情也能看到该记录。',
      '仅创建人可见': '创建人能够查看自己的记录；本项目其他成员打开客户详情时看不到该记录。',
    };
    s.batch = { qid: job.qid, epoch: job.id, baseBody: job.baseBody ?? '', conflict: false, rebased: false, patches: [
      { id: 'rule', title: '补充业务规则', text: `${q.title}\n决定：${job.answer}。`, replace: previous?.rule || q.placeholder, status: 'pending' },
      { id: 'acceptance', title: '补充验收条件', text: `验收：${specific[job.answer] ?? `按“${job.answer}”检查实际操作和展示结果（需进一步人工细化）。`}`, replace: previous?.acceptance, status: 'pending' },
    ] };
    s.selectedQ = job.qid; s.notice = '2 处模拟建议已就绪。采用后才会写入正文；请特别核对验收是否足够明确。';
  } else s.notice = '示例整理完成。原文按模块展示；下面 2 项本期规则仍需你决定。';
  note(s, 'simulated_result_ready');
}
export function retryJob(s: State) {
  const job = s.job;
  if (!job || job.status !== 'failed') return;
  const snapshot = structuredClone(job);
  // Explicit retry uses the original answer and source snapshot, not newer unsent text.
  s.job = { ...snapshot, id: Math.max(Date.now(), snapshot.id + 1), status: 'running', outcome: s.aiOutcome, due: Date.now() + 1100 };
  s.notice = '正在重试上次的材料与回答；新输入没有替换原任务范围。'; note(s, 'explicit_retry_original_scope');
}
export function saveEditor(s: State) {
  if (!s.editor || s.locked || !s.editor.text.trim()) return;
  const m = s.modules.find(m => m.id === s.editor?.moduleId)!;
  let recheck = 0;
  if (m.body !== s.editor.text) {
    m.body = s.editor.text; s.revision++;
    for (const q of s.questions.filter(q => q.moduleId === m.id)) if (s.resolutions[q.id]) { s.resolutions[q.id].reviewed = false; if (q.critical) recheck++; }
    note(s, 'manual_body_saved');
  }
  s.editor = null; s.notice = recheck ? `正文已存入本浏览器。本模块 ${recheck} 项关键规则需要再核对，其余模块保留确认状态。` : '正文已存入本浏览器。其他模块的确认状态保持不变。';
  if (s.returnToCheck) { s.page = 'delivery'; s.returnToCheck = false; }
}
export function manualSave(s: State, qid: string) {
  const r = s.manual[qid], m = moduleFor(s, qid);
  if (s.locked || !m || !r?.rule.trim() || !r?.acceptance.trim() || dirty(s) || pendingPatches(s).length) return;
  const epoch = -Date.now();
  const resolution = { rule: `人工确认规则：${r.rule.trim()}`, acceptance: `人工确认验收：${r.acceptance.trim()}`, reviewed: true, ruleEpoch: epoch, acceptanceEpoch: epoch };
  const old = s.resolutions[qid], q = s.questions.find(q => q.id === qid)!;
  m.body = writeFragment(m.body, resolution.rule, old?.rule || q.placeholder);
  m.body = writeFragment(m.body, resolution.acceptance, old?.acceptance);
  s.resolutions[qid] = resolution; s.revision++; s.notice = '规则和验收已写入本模块，此项已完成核对。'; note(s, 'manual_question_resolved');
  if (s.returnToCheck) { s.page = 'delivery'; s.returnToCheck = false; }
}
export function adopt(s: State, patchId?: Patch['id']) {
  const batch = s.batch, m = batch && moduleFor(s, batch.qid);
  if (!batch || !m || s.locked || dirty(s)) return;
  if (m.body !== batch.baseBody) { batch.conflict = true; s.notice = '正文已更新，这批建议依据的是旧内容。请先按当前正文重新核对。'; return; }
  const selected = batch.patches.filter(p => p.status === 'pending' && (!patchId || p.id === patchId));
  if (!selected.length || selected.some(p => !p.text.trim())) return;
  for (const p of selected) { m.body = writeFragment(m.body, p.text.trim(), p.replace); p.status = 'adopted'; }
  s.revision++; batch.baseBody = m.body;
  // Keep each actual fragment for later replacement, while its own epoch controls readiness.
  const r = s.resolutions[batch.qid] ?? { rule: '', acceptance: '' };
  for (const p of selected) { r[p.id] = p.text.trim(); r[p.id === 'rule' ? 'ruleEpoch' : 'acceptanceEpoch'] = batch.epoch; }
  r.reviewed = true;
  s.resolutions[batch.qid] = r;
  s.notice = `${selected.length} 处修改已写入正文。${isResolved(s, s.questions.find(q => q.id === batch.qid)!) ? '此项已完成核对。' : '此问题仍有规则或验收待补充。'}`;
  note(s, 'candidate_adopted');
  if (!pendingPatches(s).length && isResolved(s, s.questions.find(q => q.id === batch.qid)!) && s.returnToCheck) { s.page = 'delivery'; s.returnToCheck = false; }
}
export function rebase(s: State) {
  if (!s.batch) return;
  s.batch.baseBody = moduleFor(s, s.batch.qid)?.body ?? ''; s.batch.conflict = false; s.batch.rebased = true;
  s.notice = '已列出当前正文和待追加建议。请重新核对后决定是否采用；尚未写入。';
}
export function reject(s: State, patchId?: Patch['id']) {
  if (!s.batch) return;
  for (const p of s.batch.patches) if (p.status === 'pending' && (!patchId || p.id === patchId)) p.status = 'rejected';
  s.notice = '建议已不采用。原正文保留；拒绝建议不会自动确认业务问题。'; note(s, 'candidate_rejected');
}
export function confirmVersion(s: State) {
  if (s.locked || !hasDraft(s) || blockingReasons(s).length) return;
  s.versions.push({ number: s.version, modules: structuredClone(s.modules), resolutions: structuredClone(s.resolutions), questions: structuredClone(s.questions), deferred: [...s.deferred], raw: s.sourceRaw, files: s.includeMrd ? ['PRD', 'MRD'] : ['PRD'], confirmedAt: new Date().toISOString(), status: 'generating', due: Date.now() + 1000, outcome: s.fileOutcome });
  s.locked = true; s.notice = `v${s.version} 正文已确认，正在生成所列示例文件。`; note(s, 'formal_snapshot_confirmed');
}
export function finishFiles(s: State, number: number) {
  const v = s.versions.find(v => v.number === number);
  if (!v || !['generating', 'unknown'].includes(v.status)) return;
  v.status = v.outcome === 'failed' ? 'failed' : 'ready';
  s.notice = v.status === 'ready' ? `v${number} 示例文件已就绪，可下载或继续形成新草稿。` : `文件生成模拟失败。已确认的 v${number} 正文保留，可重试同一版本。`;
  note(s, `file_generation_${v.status}`);
}
export function retryFiles(s: State, number: number) {
  const v = s.versions.find(v => v.number === number);
  if (!v || v.status !== 'failed') return;
  v.status = 'generating'; v.outcome = s.fileOutcome; v.due = Date.now() + 1000;
  s.notice = `正在重新生成 v${number} 的同一组文件，无需重新确认正文。`; note(s, 'same_version_retry');
}
export function continueVersion(s: State, number: number) {
  const v = s.versions.find(v => v.number === number);
  if (!v || !s.locked) { s.page = 'workspace'; return; }
  s.modules = structuredClone(v.modules); s.resolutions = structuredClone(v.resolutions); s.questions = structuredClone(v.questions);
  s.sourceRaw = v.raw; s.deferred = [...v.deferred]; s.version = Math.max(...s.versions.map(x => x.number)) + 1;
  s.locked = false; s.editor = null; s.batch = null; s.job = null; s.page = 'workspace'; s.returnToCheck = false;
  s.notice = `已基于 v${number} 建立 v${s.version} 草稿。原确认版与文件仍在“资料与版本”。`; note(s, 'new_draft_from_version');
}
function writeFragment(body: string, text: string, previous?: string) { return previous && body.includes(previous) ? body.replace(previous, text) : body.includes(text) ? body : `${body}\n\n${text}`; }
export function projectName(raw: string) { return raw.trim() === SAMPLE.trim() ? PROJECT : '自定义需求示例'; }
export function fileName(number: number, type: 'PRD' | 'MRD', raw: string) { return `${projectName(raw)}_${type}_v${number}.md`; }
export function documentMarkdown(modules: Module[], version: number, formal: boolean, questions: Question[], resolutions: Record<string, Resolution>, type: 'PRD' | 'MRD', raw: string) {
  const unresolved = questions.filter(q => !isResolved({ modules, questions, resolutions }, q));
  return `# ${projectName(raw)} · ${type} · v${version}${formal ? '' : ' 讨论稿'}\n\n> 交互原型示例文件，仅验证操作，不作为真实项目交付。\n> ${formal ? '示例已确认版本；内容取自确认时的快照。' : '待确认 / 非正式交付；不得作为研发实施依据。'}\n\n${type === 'MRD' ? `## 背景与范围\n\n本次原文包含：${modules.map(m => m.title).join('、')}。\n\n市场规模、收入预测与竞品证据尚未提供；此示例不补造这些内容。\n\n` : ''}${modules.map(m => `## ${m.title}\n\n${m.body}`).join('\n\n')}\n\n## 已知待确认\n\n${unresolved.map(q => `- ${q.title}（${q.critical ? '影响本期正式交付' : '后续规划，不影响本期'}）`).join('\n') || '本期已知关键问题已确认。'}\n\n## 来源与版本\n\n来源：${raw.trim() === SAMPLE.trim() ? '内置合成示例' : '用户粘贴的原文（本原型未自动分析）'}与人工确认；未访问外部资料。未采用的模拟建议不写入正文。\n`;
}

export function readSaved(): { state: State; storageError: boolean } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { state: initialState(), storageError: false };
    const s = JSON.parse(raw) as State;
    if (s.schema !== 1 || !Array.isArray(s.modules) || !Array.isArray(s.versions)) throw new Error('unsupported');
    if (s.job?.status === 'running') { s.job.status = 'unknown'; s.notice = '页面已恢复。请查询上次演示任务的状态，不会自动重新运行。'; }
    for (const v of s.versions) if (v.status === 'generating') v.status = 'unknown';
    return { state: s, storageError: false };
  } catch { return { state: initialState(), storageError: true }; }
}
