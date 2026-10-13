"""Render the shipped document controls with real task-state components (no model)."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url), React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
async function load(path) {
  const built=await build({entryPoints:[path],bundle:true,write:false,format:'cjs',platform:'node',
    jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
  const module={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
  return module.exports;
}
const {DocumentStudio,documentTaskForType}=await load('src/panels.tsx');
const {TaskStatus,ActionDialog}=await load('src/guided.tsx');
const task=(id,kind='prd',status='running',extra={})=>({id,action:'document',label:'生成文档讨论稿',
  status,message:'正在整理文档章节',calls:0,max_calls:null,cost:null,run_ids:[],source_ids:[],
  completed_steps:0,stages:['prd'],step_states:[{stage:'prd',status}],
  retry_input:{action:'document',message:'',document_type:kind},...extra});
const noWrite=()=>assert.fail('rendering must not plan, run, confirm, or navigate');
const p={id:'P-document',revision:4,documents:{},hashes:{brief_hash:'brief'},items:[],questions:[],
  product_flow:{4:{available:true}},document_review_status:{},reference_mode:'user'};
const props={p,docType:'prd',selectedDocId:'',artifacts:{document_artifact:[]},setDocType:noWrite,
  setSelectedDocId:noWrite,setStage:noWrite,setShowAdvanced:noWrite,setTab:noWrite,setOutcome:noWrite,
  root:'/projects/P-document',act:noWrite,mutate:noWrite,onGenerate:noWrite,busy:false};
const status=(entry,uncertain=false)=>React.createElement(TaskStatus,{task:entry,uncertain,
  onRefresh:noWrite,onCancel:noWrite,onRetry:noWrite});
const render=(entry,extra={})=>renderToStaticMarkup(React.createElement(DocumentStudio,
  {...props,taskStatus:entry?status(entry):undefined,...extra}));
'''


def test_document_tasks_bind_to_submitted_type_without_label_or_tab_guessing():
    run_ts(LOAD + r'''
      const tasks=[task('PRD-old','prd','succeeded'),task('MRD','mrd'),
        task('review','prd','running',{action:'review'}),
        task('unbound','prd','running',{retry_input:undefined,label:'PRD 已完成'}),
        task('item','prd','running',{action:'change',retry_input:{document_type:'prd',target:{kind:'item',id:'REQ-1'}}}),
        task('PRD-current','prd','succeeded')];
      const before=JSON.stringify(tasks);
      assert.equal(documentTaskForType(tasks,'prd').id,'PRD-current');
      assert.equal(documentTaskForType(tasks,'mrd').id,'MRD');
      assert.equal(documentTaskForType(tasks,'unknown'),undefined);
      const local=task('MRD-section','mrd','failed',{action:'change',
        retry_input:{document_type:'mrd',target:{kind:'document',id:'DOC-mrd',section_id:'S1'}}});
      assert.equal(documentTaskForType([...tasks,local],'mrd').id,'MRD-section');
      assert.equal(documentTaskForType([...tasks,local],'prd').id,'PRD-current');
      assert.equal(JSON.stringify(tasks),before);
    ''')


def test_first_generation_exposes_every_terminal_or_active_state_outside_details():
    run_ts(LOAD + r'''
      for(const state of ['queued','running','succeeded','partial','failed','cancelled','paused_budget']) {
        const html=render(task('T','prd',state));
        const start=html.indexOf('<section aria-label="PRD 生成任务状态">');
        assert(start>=0,state);
        assert(start>html.indexOf('历史版本与章节参考设置'),state);
        assert(start<html.indexOf('<div class="empty">'),state);
        assert.equal((html.match(/最近一次 PRD 生成或修改任务/g)||[]).length,1);
        assert(html.includes('生成文档讨论稿'),state);
        if(['queued','running'].includes(state))assert(html.includes('已完成 0/1 步'),state);
        if(state==='succeeded')assert(html.includes('已生成，待核对'));
        assert(!html.includes('100%'));
      }
      const uncertain=render(task('T'),{taskStatus:status(task('T'),true)});
      assert(uncertain.includes('最新任务状态暂未取得'));
      assert(uncertain.includes('刷新任务状态'));
      assert(uncertain.includes('不要重复发起'));
    ''')


def test_reviewed_or_historical_document_does_not_hide_task_in_review_sidebar():
    run_ts(LOAD + r'''
      const document={id:'DOC-prd',brief_hash:'brief',draft_revision:4,document_version:1,
        content:{title:'储能云需求',document_type:'prd',sections:[]},
        reader:{title:'储能云需求',document_type:'prd',sections:[],outline:[]}};
      const saved={...p,documents:{prd:document},document_review_status:{prd:{reviewed:true}}};
      const html=render(task('T','prd','succeeded'),{p:saved});
      const progress=html.indexOf('<section aria-label="PRD 生成任务状态">');
      assert(progress<html.indexOf('document-review-surface'));
      assert(html.includes('<details class="card review-sidebar"><summary>'),'review stays collapsed');
      assert(html.includes('此版本已核对'));
      const historical=render(task('T'),{p:saved,selectedDocId:'DOC-prd',artifacts:{document_artifact:[document]}});
      assert(historical.includes('当前正在查看历史文档'));
      assert(historical.includes('历史版本只读'));
      assert(!historical.includes('id="document-review-P-document-prd"'));
      const mrd=render(task('M','mrd','queued'),{docType:'mrd'});
      assert(mrd.includes('MRD 生成任务状态')&&!mrd.includes('PRD 生成任务状态'));
    ''')


def test_prepare_button_explains_the_explicit_run_and_authorization_step():
    run_ts(LOAD + r'''
      const html=render(undefined);
      assert(html.includes('准备生成 PRD'));
      assert(html.includes('aria-describedby="document-generate-help-P-document-prd"'));
      assert(html.includes('运行本次任务')&&html.includes('才会开始生成'));
      const plan={label:'生成文档讨论稿',expected_revision:4,stages:['prd'],max_calls:null,
        recipients:[],sources:[],context_scope:'已确认需求',missing:[]};
      const dialogProps={plan,input:{message:''},error:'',busy:false,onClose:noWrite,onSubmit:noWrite};
      const approved=renderToStaticMarkup(React.createElement(ActionDialog,dialogProps));
      assert(approved.includes('运行本次任务'));
      const unapproved=renderToStaticMarkup(React.createElement(ActionDialog,{...dialogProps,
        plan:{...plan,recipients:[{origin:'https://example.invalid',model:'offline',needs_authorization:true}]}}));
      assert(unapproved.includes('授权本次范围并运行'));
      assert(html.includes('授权本次范围并运行'));
    ''')


def test_workspace_atomic_confirmation_keeps_rejection_and_commit_receipts_distinct():
    run_ts(r'''
      import assert from 'node:assert/strict';import fs from 'node:fs';import ts from 'typescript';
      import {build} from 'esbuild';
      const source=ts.createSourceFile('workspace.tsx',fs.readFileSync('src/workspace.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
      const workspace=source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='Workspace');
      const actual=workspace.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='mutate');
      assert(actual,'exercise the actual shipped workspace mutation function');
      const built=await build({entryPoints:['src/project-update.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {mergeProjectWrite}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const harness=ts.transpileModule(`
        function createHarness(result, failRefresh, mergeProjectWrite) {
          const events=[], id='P', root='/projects/P';
          const reader={title:'已取得的正文'};
          const projectRef={current:{id,revision:4,items:[],documents:{prd:{id:'D',brief_hash:'h',reader}},sources:[]}};
          const setP=value=>events.push(['project',value.revision]);
          const api=async(path,method,body)=>{events.push(['api',path,body.expected_revision]);return result;};
          const beginActivity=()=>()=>events.push(['finish']);
          const refresh=async()=>{events.push(['refresh',projectRef.current.revision]);if(failRefresh)throw Error('offline');};
          const onRefreshFail=error=>events.push(['refresh-failed',error.message]);
          const activityNotice=(text)=>events.push(['notice',text]);
          class ApiError extends Error {}; class DraftConflictError extends Error {};
          const equal=()=>false,editValue=()=>undefined;
          ${actual.getText(source)}
          return {mutate,projectRef,events,reader};
        }
      `,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
      const createHarness=new Function(harness+';return createHarness;')();
      const rejected={confirmed:false,code:'REFINEMENT_INCOMPLETE',issues:[{field:'intent'}]};
      const blocked=createHarness(rejected,true,mergeProjectWrite);
      assert.equal(await blocked.mutate('/refinement-confirmation',{expected_revision:4}),rejected);
      assert.equal(blocked.projectRef.current.revision,4);
      assert.deepEqual(blocked.events.map(e=>e[0]),['api','finish'],
        'HTTP 200 validation failure must not refresh, change project, or claim saved');
      const receipt={confirmed:true,project:{id:'P',revision:5,items:[{id:'REQ-1'}],
        documents:{prd:{id:'D',brief_hash:'h'}},stage_checks:{'3':{content_hash:'confirmed'}}},
        receipt:{result_revision:5},destination:'delivery'};
      const committed=createHarness(receipt,true,mergeProjectWrite);
      assert.equal(await committed.mutate('/refinement-confirmation',{expected_revision:4}),receipt,
        'caller retains the complete atomic receipt and destination');
      assert.equal(committed.projectRef.current.revision,5);
      assert.equal(committed.projectRef.current.documents.prd.reader,committed.reader);
      assert.equal(committed.projectRef.current.stage_checks['3'].content_hash,'confirmed');
      assert.deepEqual(committed.events.slice(0,3),[['api','/projects/P/refinement-confirmation',4],['project',5],['refresh',5]],
        'server-confirmed canonical project must be installed before projection refresh');
      assert(committed.events.some(e=>e[0]==='notice'&&e[1].includes('修改已保存')));
      const plain={id:'P',revision:5,items:[],documents:{}};
      const existing=createHarness(plain,false,mergeProjectWrite);
      assert.equal(await existing.mutate('',{expected_revision:4},'PATCH'),plain);
      assert.equal(existing.projectRef.current.revision,5,'existing project edits keep their merge behavior');
    ''')
