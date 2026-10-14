"""Document discussions reuse the real preview/commit gates without another form."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
async function load(path,states) {
  const built=await build({entryPoints:[path],bundle:true,write:false,format:'cjs',platform:'node',
    jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
  const module={exports:{}};
  const dependency=name=>name==='react'&&states?{...React,useState:()=>{
    assert(states.length,'supply every component state in this read-only render');
    return [states.shift(),()=>assert.fail('render may not update state')];
  }}:require(name);
  new Function('require','module','exports',built.outputFiles[0].text)(dependency,module,module.exports);
  return module.exports;
}
const source=ts.createSourceFile('AnswerConfirmation.tsx',
  fs.readFileSync('src/pm-prototype/AnswerConfirmation.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='AnswerConfirmation');
const actual=name=>component.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(source);
const identity=source.statements.find(n=>ts.isVariableStatement(n)&&
  n.declarationList.declarations.some(d=>d.name.getText(source)==='identity')).getText(source);
const compile=(text,name)=>new Function(ts.transpileModule(text,
  {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText+';return '+name+';')();
const clause=(id='REQ-1')=>({id,title:'告警确认权限',kind:'requirement',selection_status:'selected',
  statement:'仅具有告警确认权限的用户可以确认告警。',behavior:{actor:'具备告警确认权限的用户'},
  source_refs:[{source_id:'SRC-1',quote:'告警确认权限'}],content_version:1});
const question=(id='Q-1',related='REQ-1')=>({id,question:'哪些用户可以确认告警？',related_refs:[related]});
const project={id:'P-document',revision:11,items:[clause()],questions:[question()],
  decision_answers:[{id:'A-1',question_id:'Q-1',text:'沿用告警确认权限规则。'}]};
'''


def test_document_discussion_initial_surface_is_a_compact_continuation():
    run_ts(LOAD + r'''
      const {AnswerConfirmation}=await load('src/pm-prototype/AnswerConfirmation.tsx');
      const noWrite=()=>assert.fail('rendering may not save, organize, confirm, or navigate');
      const props={project,question:project.questions[0],decisions:project.questions,
        answer:'沿用告警确认权限规则。',answerDirty:false,onAnswerChange:noWrite,onSaveAnswer:noWrite,
        busy:false,running:false,blockReason:'',proposalCurrent:false,onOrganize:noWrite,
        mutate:noWrite,onSaved:noWrite,onSources:noWrite,onScope:noWrite,onRefresh:noWrite};
      const render=extra=>renderToStaticMarkup(React.createElement(AnswerConfirmation,{...props,...extra}));
      const standard=render({});
      assert(standard.includes('我的回答原文')&&standard.includes('rows="3"'));
      assert(standard.includes('让助手整理')&&standard.includes('直接修改需求'));
      assert(!standard.includes('document_discussion'));
      const document=render({variant:'document',documentDiscussion:{document_id:'DOC-1',section_id:'S-1'}});
      assert(document.includes('继续补充这次讨论')&&document.includes('rows="2"'));
      assert(document.includes('发送补充并查看修改'));
      assert(document.includes('核对原文的落实位置'));
      assert(!document.includes('沿用原文并查看修改'),'entry opens explicit quote review before preparing a preview');
      assert(document.includes('需要时直接编辑条款'));
      assert(!document.includes('我的回答原文')&&!document.includes('aria-label="需求修改方式"'));
      assert(!document.includes('（修改后）')&&!document.includes('业务说明 · 核对或修改'),
        'the continuation must not open the clause editor before an explicit choice');
      assert(!document.includes('确认本次变更'),'confirmation only becomes available after a real preview');
      const blank=render({variant:'document',answer:''});
      assert(blank.includes('class="primary" disabled=""'));
      const unrelated=render({variant:'document',project:{...project,items:[{...clause(),selection_status:'candidate'}]}});
      assert(!unrelated.includes('核对原文的落实位置'),'unadopted candidates cannot use the original-clause path');
    ''')


def test_document_preview_and_optional_manual_editor_keep_real_confirmation_gates():
    run_ts(LOAD + r'''
      const noWrite=()=>assert.fail('read-only rendering cannot save or organize');
      const props={project,question:project.questions[0],decisions:project.questions,
        answer:'沿用告警确认权限规则。',answerDirty:false,onAnswerChange:noWrite,onSaveAnswer:noWrite,
        busy:false,running:false,blockReason:'',proposalCurrent:false,onOrganize:noWrite,
        mutate:noWrite,onSaved:noWrite,onSources:noWrite,onScope:noWrite,onRefresh:noWrite,variant:'document'};
      const original=clause(),edited={...original,statement:'仅告警管理角色可以确认告警。',
        source_refs:[{source_id:'SRC-2',quote:'告警管理角色'}]};
      const group={id:'G-1',title:'告警权限',targets:[{id:original.id,title:original.title,before:original,after:edited}]};
      const preview={expected_revision:11,preview_hash:'REAL-PREVIEW-HASH',groups:[group],decisions:[]};
      const render=async({review=preview,mode='assistant',expanded=false,chosen=[],extra={}}={})=>{
        const states=[mode,expanded,{[original.id]:{statement:original.statement,behavior:original.behavior}},
          {},'',[],{},false,review,chosen,'',false,false,'',false,[],{},
          // Detail conflict state belongs to the parent; the optional detailed
          // editor adds two local expansion states before the witness editor.
          ...(review===null&&mode==='manual'?[...(expanded?[{},{}]:[]),{}]:[])];
        const {AnswerConfirmation}=await load('src/pm-prototype/AnswerConfirmation.tsx',states);
        const html=renderToStaticMarkup(React.createElement(AnswerConfirmation,{...props,...extra}));
        assert.equal(states.length,0);return html;
      };
      const confirm=html=>html.match(/<button[^>]*>确认本次变更<\/button>/)?.[0];
      const shown=await render();
      assert(shown.includes('修改前')&&shown.includes(original.statement));
      assert(shown.includes('确认后')&&shown.includes(edited.statement));
      assert(shown.includes('来源引用也将变化')&&shown.includes('SRC-1')&&shown.includes('SRC-2'));
      assert(shown.includes('业务说明（缺项继续保留）')&&shown.includes('待补充'));
      assert(shown.includes('核对来源')&&shown.includes('返回补充'));
      assert(confirm(shown)&&!confirm(shown).includes('disabled'));
      for(const review of [
        {...preview,expected_revision:10},
        {...preview,blockers:[{message:'来源尚未核对'}]},
        {...preview,groups:[]},
        {...preview,groups:[group,{...group,id:'G-2'}]}
      ]) assert(confirm(await render({review})).includes('disabled'),
        'revision, source blockers, absent groups and unchosen groups all keep confirmation blocked');
      assert(!confirm(await render({review:{...preview,groups:[group,{...group,id:'G-2'}]},chosen:['G-1']})).includes('disabled'));
      const folded=await render({review:null,mode:'manual'});
      assert(folded.includes('展开条款编辑')&&folded.includes('aria-expanded="false"'));
      assert(folded.includes(original.statement)&&!folded.includes('（修改后）'));
      assert.equal((folded.match(/<textarea /g)||[]).length,1,'only the continuation input remains visible');
      assert(folded.includes('请选择已表达答案的位置')&&folded.includes('答案落实到哪里'));
      assert(!folded.includes('checked=""'),'original reuse never preselects witnesses');
      const expanded=await render({review:null,mode:'manual',expanded:true});
      assert(expanded.includes('（修改后）')&&expanded.includes('业务说明 · 核对或修改'));
    ''')


MANUAL = LOAD + r'''
const {directReviewTargets}=await load('src/pm-prototype/answer-actions.ts');
const {requirementBehaviorLabels:fields}=await load('src/pm-prototype/requirement-fields.ts');
const {validImplementationRefs}=await load('src/answer-application-review.ts');
const createManual=compile(`
  ${identity}
  function createManual(config, directReviewTargets, fields, validImplementationRefs) {
    const {project,question,decisions,answer,answerDirty=false,latest=project,confirmed=[],implementations={}}=config;
    const root='/projects/'+project.id,qid=question.decision_group_id||question.id;
    const grouped=decisions.length>1,lock={current:false},events=[],errors=[];
    const targets=[...new Map(decisions.flatMap(d=>directReviewTargets(project,d)).map(i=>[i.id,i])).values()];
    const activeDecisions=grouped?decisions.filter(d=>confirmed.includes(d.id)):decisions;
    const activeTargetIds=new Set(activeDecisions.flatMap(d=>directReviewTargets(project,d).map(i=>i.id)));
    const baseline=Object.fromEntries(targets.map(item=>[item.id,identity(item)]));
    const edits=Object.fromEntries(targets.map(item=>[item.id,{statement:item.statement,behavior:{...item.behavior}}]));
    const setWorking=value=>events.push(['working',value]),setError=value=>{if(value)errors.push(value);};
    const setNotice=()=>{},setSelectedGroups=()=>{},setEditingField=()=>{},setImplementationDirty=()=>{};
    const implementationPayload=()=>Object.fromEntries(activeDecisions.map(d=>[d.id,validImplementationRefs(implementations[d.id]||[],directReviewTargets(project,d))]));
    let review=null;
    const setReview=value=>{review=value;};
    const onSaveAnswer=async()=>{events.push(['save-answer']);};
    const api=async(path,method='GET',body)=>{events.push(['api',path,method,body]);
      return method==='GET'?latest:{preview_id:'PREVIEW',preview_hash:'HASH',expected_revision:latest.revision,groups:[]};};
    ${actual('latestAfterSave')}
    ${actual('prepareManual')}
    return {prepareManual,events,errors,get review(){return review;}};
  }
`,'createManual');
const manual=extra=>createManual({project,question:project.questions[0],decisions:project.questions,
  answer:'沿用告警确认权限规则。',...extra},directReviewTargets,fields,validImplementationRefs);
'''


def test_unchanged_originals_preview_exact_saved_content_without_filling_business_gaps():
    run_ts(MANUAL + r'''
      const witnesses={'Q-1':[{item_id:'REQ-1',field:'statement',quote:clause().statement}]};
      const before=structuredClone(project),h=manual({implementations:witnesses});
      await h.prepareManual();
      assert.deepEqual(h.errors,[]);
      const request=h.events.find(e=>e[0]==='api'&&e[2]==='POST');
      assert.equal(request[1],'/projects/P-document/decision-review/manual-preview');
      assert.equal(request[3].expected_revision,11);
      assert.equal(request[3].answer_id,'A-1');
      assert.equal(request[3].expected_answer_text,'沿用告警确认权限规则。');
      assert.deepEqual(request[3].edits,[{item_id:'REQ-1',statement:clause().statement,behavior:clause().behavior}]);
      assert.deepEqual(request[3].review_behavior_item_ids,[],
        'missing business fields remain missing instead of being filled or marked reviewed');
      assert(!Object.hasOwn(request[3],'decisions'),'one decision preserves the established request');
      assert.deepEqual(request[3].answer_implementations,witnesses,'unchanged original reuse supplies the explicitly checked quote');
      const unverified=manual({});await unverified.prepareManual();
      assert.deepEqual(unverified.events.find(e=>e[0]==='api'&&e[2]==='POST')[3].answer_implementations,{'Q-1':[]},'a pending preview may save work but does not claim original reuse');
      assert.equal(h.review.preview_hash,'HASH');
      assert(!h.events.some(e=>e[0]==='api'&&e[1].endsWith('/commit')),'preview never confirms or writes clauses');
      assert.deepEqual(project,before,'the shortcut cannot mutate the saved project');
      const dirty=manual({answerDirty:true,latest:{...project,revision:12}});
      await dirty.prepareManual();
      assert(dirty.events.findIndex(e=>e[0]==='save-answer')<dirty.events.findIndex(e=>e[0]==='api'));
      assert.equal(dirty.events.find(e=>e[0]==='api'&&e[2]==='POST')[3].expected_revision,12);
    ''')


def test_original_preview_preserves_explicit_group_choice_and_rejects_concurrent_changes():
    run_ts(MANUAL + r'''
      const groupedProject={...project,items:[clause(),clause('REQ-2')],questions:[question(),question('Q-2','REQ-2')]};
      const h=manual({project:groupedProject,decisions:groupedProject.questions,confirmed:['Q-1']});
      await h.prepareManual();
      const body=h.events.find(e=>e[0]==='api'&&e[2]==='POST')[3];
      assert.deepEqual(body.decisions,[
        {decision_id:'Q-1',confirmed:true,answer_quote:'沿用告警确认权限规则。'},
        {decision_id:'Q-2',confirmed:false,answer_quote:''}]);
      assert.deepEqual(body.edits.map(value=>value.item_id),['REQ-1'],
        'the unchosen decision and its clause must remain pending');
      for(const latest of [
        {...project,revision:12,items:[{...clause(),statement:'其他人已经修改原文。'}]},
        {...project,revision:12,items:[{...clause(),source_refs:[{source_id:'SRC-2'}]}]},
        {...project,revision:12,decision_answers:[{id:'A-2',question_id:'Q-1',text:'其他回答。'}]}
      ]) {
        const stale=manual({latest});await stale.prepareManual();
        assert(stale.errors.length,'concurrent answer/clause/source changes must be reported');
        assert(!stale.events.some(e=>e[0]==='api'&&e[2]==='POST'));
        assert.equal(stale.review,null);
      }
    ''')


def test_commit_attaches_only_precise_document_context_and_preserves_preview_gates():
    run_ts(LOAD + r'''
      const createCommit=compile(`
        function createCommit(config) {
          const {project,review,selectedGroups=[],documentDiscussion,documentMode=false}=config;
          const root='/projects/'+project.id,stale=review.expected_revision!==project.revision;
          const lock={current:false},intent={current:null},events=[],errors=[],implementationDirty=!!config.implementationDirty;
          const setWorking=()=>{},setTouched=()=>{},setReview=()=>{};
          const setError=value=>{if(value)errors.push(value);};
          const mutate=async(path,body)=>{events.push(['mutate',path,body]);return {confirmed:true};};
          const api=async(path)=>{events.push(['refresh',path]);return project;};
          const onSaved=(latest,notice)=>events.push(['saved',latest,notice]);
          ${actual('commit')}
          return {commit,events,errors};
        }
      `,'createCommit');
      const preview={expected_revision:11,preview_id:'PREVIEW-1',preview_hash:'VISIBLE-HASH',groups:[{id:'G-1'}]};
      const submit=async extra=>{const h=createCommit({project,review:preview,...extra});await h.commit();return h;};
      const standard=await submit({});
      const body=standard.events.find(e=>e[0]==='mutate')[2];
      assert.deepEqual(Object.keys(body).sort(),['expected_revision','idempotency_key','preview_hash',
        'preview_id','review_confirmed','selected_group_ids'].sort(),'default commit keeps the original contract');
      assert.equal(body.preview_hash,'VISIBLE-HASH');assert.equal(body.review_confirmed,true);
      assert.deepEqual(body.selected_group_ids,['G-1']);
      const contextual=await submit({documentMode:true,documentDiscussion:{document_id:'DOC-1',section_id:'S-1'}});
      assert.deepEqual(contextual.events.find(e=>e[0]==='mutate')[2].document_discussion,
        {document_id:'DOC-1',section_id:'S-1'});
      assert.equal(contextual.events.find(e=>e[0]==='saved')[2],'已确认本次变更。');
      for(const section_id of ['', '   ']) {
        const historical=await submit({documentMode:true,documentDiscussion:{document_id:'DOC-1',section_id}});
        assert(!Object.hasOwn(historical.events.find(e=>e[0]==='mutate')[2],'document_discussion'),
          'a discussion without an exact chapter cannot request automatic chapter completion');
      }
      for(const review of [{...preview,expected_revision:10},{...preview,groups:[]},
        {...preview,groups:[{id:'G-1'},{id:'G-2'}]}]) {
        const blocked=await submit({review,documentMode:true,documentDiscussion:{document_id:'DOC-1',section_id:'S-1'}});
        assert.deepEqual(blocked.events,[],'stale or unchosen previews cannot commit or close a chapter');
      }
      const changed=await submit({implementationDirty:true});
      assert.deepEqual(changed.events,[],'editing witness choices requires a fresh real preview before commit');
    ''')
