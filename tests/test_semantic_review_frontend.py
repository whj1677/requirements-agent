"""Use shipped review components to verify refusal ownership and truthful scope."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
async function load(path){
  const built=await build({entryPoints:[path],bundle:true,write:false,format:'cjs',platform:'node',
    jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
  const module={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
  return module.exports;
}
const {latestDraftReview,DraftRowReview,DraftPackageStatus}=await load('src/pm-prototype/BusinessDraftReview.tsx');
const noWrite=()=>assert.fail('rendering must not submit, adopt, or navigate');
const text=node=>node==null||typeof node==='boolean'?'':typeof node==='string'||typeof node==='number'?String(node):
  Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
function all(node,type){
  if(!node||typeof node!=='object')return [];
  if(Array.isArray(node))return node.flatMap(value=>all(value,type));
  return [...(node.type===type?[node]:[]),...all(node.props?.children,type)];
}
const row=(collection,rid='REQ-current',reason='CURRENT-REASON')=>({
  [collection]:[{requirement_id:rid,reason,path:['requirements',0,'detail_contract'],
    raw:{label:'<script>synthetic-only</script>',flow:{nodes:[],edges:[]}}}]
});
const renderRow=(project,extra={})=>renderToStaticMarkup(React.createElement(DraftRowReview,
  {project,requirementId:'REQ-current',disabled:false,onRetry:noWrite,onSources:noWrite,...extra}));
const buttonHtml=(html,label)=>[...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)]
  .find(match=>match[2].replace(/<[^>]*>/g,'')===label);
'''


def test_latest_relevant_report_includes_partial_refusals_without_cross_requirement_leakage():
    run_ts(LOAD + r'''
      for(const collection of ['field_rejected','question_rejected','structure_rejected']){
        const old={accepted:[{requirement_id:'REQ-current'}],marker:'old'};
        const current={...row(collection),marker:collection};
        const other={...row(collection,'REQ-other','PRIVATE-OTHER-ROW'),marker:'other'};
        const project={business_draft_reviews:[old,current,other]};
        const before=JSON.stringify(project);
        assert.equal(latestDraftReview(project,'REQ-current'),current,collection);
        assert.equal(latestDraftReview(project,'REQ-other'),other,collection);
        assert.equal(latestDraftReview(project,'MISSING'),undefined,collection);
        assert.equal(latestDraftReview(project),other,'project summary still uses latest overall report');
        assert.equal(JSON.stringify(project),before,'review lookup must not reverse persisted history');
      }
      for(const collection of ['accepted','rejected']){
        const original={...row(collection),marker:collection};
        const project={business_draft_reviews:[original,row('field_rejected','REQ-other')]};
        assert.equal(latestDraftReview(project,'REQ-current'),original,collection);
      }
      assert.equal(latestDraftReview({}),undefined);
    ''')


def test_new_refusal_diagnosis_keeps_the_explicit_candidate_evidence_identity():
    run_ts(LOAD + r'''
      const evidence=text=>({statement:{basis:'fact',text,source_refs:[{source_id:'S1',excerpt_id:'E1'}]}});
      const project={business_draft_reviews:[
        {accepted:[{requirement_id:'REQ-current',candidate_temp_id:'C-SELECTED',field_evidence:evidence('SELECTED-CANDIDATE-EVIDENCE')}]},
        {accepted:[{requirement_id:'REQ-current',candidate_temp_id:'C-OTHER',field_evidence:evidence('UNSELECTED-EVIDENCE')}]},
        row('field_rejected','REQ-current','LATEST-DETAIL-DIAGNOSIS'),
        row('field_rejected','REQ-other','PRIVATE-OTHER-ROW')
      ]};
      const before=JSON.stringify(project),html=renderRow(project,{candidateId:'C-SELECTED'});
      assert(html.includes('LATEST-DETAIL-DIAGNOSIS'));
      assert(html.includes('SELECTED-CANDIDATE-EVIDENCE'));
      assert(!html.includes('UNSELECTED-EVIDENCE'));
      assert(!html.includes('PRIVATE-OTHER-ROW'));
      assert.equal(JSON.stringify(project),before);
    ''')


def test_retry_is_an_explicit_whole_requirement_action_with_disabled_state_preserved():
    run_ts(LOAD + r'''
      for(const collection of ['rejected','field_rejected','question_rejected']){
        let called=0;
        const project={business_draft_reviews:[row(collection)]};
        const props={project,requirementId:'REQ-current',disabled:false,onRetry:()=>called++,onSources:noWrite};
        const element=DraftRowReview(props);
        assert.equal(called,0,'constructing the review is not a retry');
        const buttons=all(element,'button').filter(button=>text(button)==='重新整理这条需求');
        assert.equal(buttons.length,1,collection);
        assert.equal(buttons[0].props.onClick,props.onRetry,'keep actual parent callback binding');
        assert.equal(buttons[0].props.disabled,false);
        const html=renderToStaticMarkup(element);
        assert(html.includes('不是只改失败字段'));
        assert(html.includes('已有内容保留'));
        assert(html.includes('决定是否采用'));
        assert(!html.includes('让 AI 调整未载入字段'));
        buttons[0].props.onClick();assert.equal(called,1,'only explicit click triggers callback once');
        const blocked=DraftRowReview({...props,disabled:true});
        assert.equal(all(blocked,'button').find(button=>text(button)==='重新整理这条需求').props.disabled,true);
        assert.equal(called,1,'disabled rendering cannot invoke retry');
      }
    ''')


def test_long_reason_is_complete_but_collapsed_and_retry_remains_explicit():
    run_ts(LOAD + r'''
      const longReason='建议内容与需求原话不一致。'+
        'requirements/0/detail_contract 与原话仍需核对；'.repeat(8)+
        '<img src=x onerror=synthetic-only>';
      for(const collection of ['rejected','field_rejected','question_rejected']){
        let called=0;
        const project={business_draft_reviews:[
          row(collection,'REQ-current',longReason),row(collection,'REQ-other','PRIVATE-OTHER-ROW')
        ]};
        const props={project,requirementId:'REQ-current',disabled:false,onRetry:()=>called++,onSources:noWrite};
        const element=DraftRowReview(props);
        const callout=all(element,'div').find(node=>node.props.className==='table-callout business-draft-row-callout');
        const diagnostic=all(callout,'details').find(node=>node.props.className==='business-draft-row-review-details');
        const compact=all(callout,'p').find(node=>node.props.className==='business-draft-row-review-reason');
        assert(callout&&diagnostic&&!diagnostic.props.open,collection);
        assert(text(diagnostic).includes(longReason),'full reason stays available');
        assert(compact&&text(compact).length<=72,'only a bounded reason is visible by default');
        assert(!text(compact).includes('requirements/0/detail_contract'));
        const children=React.Children.toArray(callout.props.children);
        assert(children.findIndex(node=>node.type==='button')<children.findIndex(node=>node.type==='details'),
          'expanded diagnostics cannot push the primary action above them');
        const retry=all(element,'button').filter(button=>text(button)==='重新整理这条需求');
        assert.equal(retry.length,1);assert.equal(retry[0].props.onClick,props.onRetry);
        retry[0].props.onClick();assert.equal(called,1);
        const blocked=DraftRowReview({...props,disabled:true});
        assert.equal(all(blocked,'button').find(button=>text(button)==='重新整理这条需求').props.disabled,true);
        assert.equal(called,1);
        const html=renderToStaticMarkup(element);
        assert(html.includes('&lt;script&gt;synthetic-only&lt;/script&gt;'));
        assert(html.includes('&lt;img src=x onerror=synthetic-only&gt;'));
        assert(!html.includes('<script>')&&!html.includes('<img'));
        assert(!html.includes('PRIVATE-OTHER-ROW')&&!html.includes('<details open'));
        assert(html.includes('不是只改失败字段')&&html.includes('决定是否采用'));
      }
    ''')


def test_field_diagnostic_is_readable_while_raw_json_and_path_are_collapsed_and_escaped():
    run_ts(LOAD + r'''
      const project={business_draft_reviews:[row('field_rejected')]};
      for(const element of [
        React.createElement(DraftRowReview,{project,requirementId:'REQ-current',disabled:false,onRetry:noWrite,onSources:noWrite}),
        React.createElement(DraftPackageStatus,{project,onLocate:noWrite,onCreate:noWrite})
      ]){
        const html=renderToStaticMarkup(element);
        assert(html.includes('新详细定义未载入：CURRENT-REASON'));
        assert(html.includes('已有详细定义保留'));
        const raw=html.match(/<details><summary>查看未载入字段的原稿与路径<\/summary><pre>([\s\S]*?)<\/pre><\/details>/);
        assert(raw,'technical data belongs inside a closed details element');
        assert(raw[1].includes('&quot;path&quot;')&&raw[1].includes('&quot;detail_contract&quot;'));
        assert(raw[1].includes('&lt;script&gt;synthetic-only&lt;/script&gt;'));
        assert(!html.includes('<script>'));
        assert(!html.includes('<details open'));
      }
      const summary=renderToStaticMarkup(React.createElement(DraftPackageStatus,
        {project:{business_draft_reviews:[row('structure_rejected')]},onLocate:noWrite,onCreate:noWrite}));
      assert(summary.includes('1 处格式差异已单独保留')&&summary.includes('CURRENT-REASON'));
    ''')


CHAPTER = r'''
const {ChapterDiscussion}=await load('src/chapter-discussion.tsx');
const artifact={id:'DOC-current',document_version:6,content:{document_type:'prd',sections:[
  {section_id:'BACKGROUND',title:'背景与现状',blocks:[{text:'这是原文，尚未修改。'}]}]}};
const target={id:artifact.id,section_id:'BACKGROUND',label:'背景与现状',artifact};
const requestTarget={kind:'document',id:artifact.id,section_id:'BACKGROUND'};
const task={id:'TASK-discussion',action:'change',label:'章节修改',status:'partial',message:'有建议待核对',
  calls:1,max_calls:1,cost:null,run_ids:['RUN-discussion'],source_ids:[],completed_steps:1,stages:['change'],
  retry_input:{action:'change',document_type:'prd',message:'只修正文推断，不改业务',target:requestTarget}};
const candidate={id:'C-not-adopted',kind:'requirement',title:'尚未采用的建议',statement:'此内容还没有应用',
  selection_status:'candidate',generation_evidence:{run_id:'RUN-discussion'},source_refs:[]};
const project={id:'P-chapter',revision:7,questions:[],items:[candidate],messages:[],documents:{prd:artifact},
  product_context:{scope_ids:[]},product_flow:[{step:3,complete:true,available:true,missing:[],blockers:[]}]};
const run={id:'RUN-discussion',result_applied:true,discussion_target:requestTarget,
  discussion_result:{item_ids:[candidate.id],question_ids:[]}};
const props={project,target,tasks:[task],runs:[run],busy:false,running:false,blocked:'',
  currentDocumentId:artifact.id,mutate:noWrite,onSubmit:noWrite,onClarify:noWrite,onRefresh:noWrite,
  onSources:noWrite,onScope:noWrite,onClose:noWrite,onUpdate:noWrite,onCancel:noWrite,onRetry:noWrite};
const renderChapter=(extra={})=>renderToStaticMarkup(React.createElement(ChapterDiscussion,{...props,...extra}));
'''


def test_chapter_candidate_without_questions_is_not_presented_as_already_applied():
    run_ts(LOAD + CHAPTER + r'''
      const before=JSON.stringify({project,task,run}),html=renderChapter();
      assert(html.includes('尚未采用的建议')&&html.includes('此内容还没有应用'));
      assert(html.includes('尚未确认'));
      assert(html.includes('以下建议尚未采用，不会自动写入文档'));
      assert(html.includes('章节讨论不会直接改写文档'));
      assert(html.includes('重新生成全文不会自动采纳尚未确认的建议'));
      assert(!html.includes('本次变更已核对'));
      const regenerate=buttonHtml(html,'按当前底稿重新生成全文');
      assert(regenerate&&!regenerate[1].includes('disabled'),'current draft can be explicitly regenerated, without implying adoption');
      assert(!buttonHtml(html,'更新当前文档'),'scope label must no longer imply a single-chapter edit');
      assert.equal(JSON.stringify({project,task,run}),before);
    ''')


def test_chapter_regeneration_keeps_busy_running_version_pending_and_stage_gates():
    run_ts(LOAD + CHAPTER + r'''
      const pending={id:'Q-pending',question:'还有一项修改待核对',status:'open',related_refs:[],
        generation_evidence:{run_id:'RUN-discussion'}};
      const pendingProject={...project,questions:[pending]};
      const variants=[{busy:true},{running:true},{currentDocumentId:'DOC-new'},
        {project:{...project,product_flow:[{step:3,complete:false,available:true,missing:[],blockers:[]}]}},
        {project:pendingProject,runs:[{...run,discussion_result:{item_ids:[candidate.id],question_ids:[pending.id]}}]}];
      for(const extra of variants){
        const html=renderChapter(extra),button=buttonHtml(html,'按当前底稿重新生成全文');
        assert(button&&button[1].includes('disabled'),JSON.stringify(extra));
      }
      assert(renderChapter({currentDocumentId:'DOC-new'}).includes('文档已有新版本'));
    ''')


def test_chapter_update_callback_runs_only_on_explicit_guarded_click():
    run_ts(LOAD + CHAPTER + r'''
      let updates=0,navigations=0,allowed=true;
      const html=renderChapter({onUpdate:()=>updates++});assert(html.includes('按当前底稿重新生成全文'));
      assert.equal(updates,0,'SSR must not update or plan');
      const source=ts.createSourceFile('chapter-discussion.tsx',fs.readFileSync('src/chapter-discussion.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
      let handler;
      function visit(node){
        if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(source)==='button'&&
          node.children.some(child=>ts.isJsxText(child)&&child.text.trim()==='按当前底稿重新生成全文')){
          const onClick=node.openingElement.attributes.properties.find(p=>ts.isJsxAttribute(p)&&p.name.getText(source)==='onClick');
          handler=onClick.initializer.expression.getText(source);
        }
        ts.forEachChild(node,visit);
      }
      visit(source);assert(handler,'exercise the shipped button callback');
      const click=new Function('navigate','onUpdate','return ('+handler+');')(
        next=>{navigations++;if(allowed)next();},()=>updates++);
      assert.equal(updates,0);assert.equal(navigations,0);
      click();assert.equal(updates,1);assert.equal(navigations,1);
      allowed=false;click();assert.equal(updates,1,'navigation guard still owns whether the action can start');
    ''')
