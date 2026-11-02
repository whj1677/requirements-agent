"""Exercise shipped grounding progress and draft notices without HTTP or a model."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
globalThis.fetch=()=>assert.fail('displaying grounding progress must not send HTTP');
async function load(path){
  const built=await build({entryPoints:[path],bundle:true,write:false,format:'cjs',platform:'node',
    jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
  const module={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
  return module.exports;
}
const {TaskStatus}=await load('src/guided.tsx');
const {taskCurrentStage}=await load('src/task-progress-state.ts');
const {ProgressContents}=await load('src/task-progress.tsx');
const {TaskFailureNotice,canRecoverDraft,taskFailure}=await load('src/task-failure.tsx');
const {DraftPackageStatus,DraftRowReview}=await load('src/pm-prototype/BusinessDraftReview.tsx');
const noWrite=()=>assert.fail('rendering must not retry, cancel, recover, confirm, or navigate');
const text=node=>node==null||typeof node==='boolean'?'':typeof node==='string'||typeof node==='number'?String(node):
  Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
function nodes(node){
  if(node==null||typeof node!=='object')return [];
  if(Array.isArray(node))return node.flatMap(nodes);
  return [...(node.props?[node]:[]),...nodes(node.props?.children)];
}
const html=element=>renderToStaticMarkup(element);
const grounding=(status,extra={})=>({protocol:'intake-grounding-1',status,
  corrections:['correcting','rechecking','repaired'].includes(status)?1:0,
  review_call_ids:['CALL-synthetic'],reason:'',original_candidate_hash:'original-synthetic',
  candidate_hash:'candidate-synthetic',input_hash:'input-synthetic',
  candidate_applied:['passed','repaired'].includes(status),...extra});
const task=(status,extra={})=>({id:'TASK-synthetic',idempotency_key:'KEY-synthetic',action:'organize',
  label:'整理需求草稿',status:['passed','repaired'].includes(status)?'succeeded':['blocked','unverified'].includes(status)?'failed':'running',
  message:'本次执行状态',calls:2,max_calls:null,cost:null,run_ids:['RUN-synthetic'],source_ids:['SOURCE-synthetic'],
  completed_steps:['passed','repaired'].includes(status)?1:0,stages:['ingest'],step_states:[{stage:'ingest',status:'running'}],
  retry_input:{action:'organize',message:'已保存的合成原始输入'},intake_grounding:grounding(status),...extra});
const statusProps=entry=>({task:entry,busy:false,uncertain:false,onRefresh:noWrite,onRetry:noWrite,
  onCancel:noWrite,onViewResult:noWrite,onRecover:noWrite,onOpenDraft:noWrite});
const statusHtml=entry=>html(React.createElement(TaskStatus,statusProps(entry)));
const progressHtml=entry=>html(React.createElement(ProgressContents,{entry:{projectId:'P-synthetic',
  projectName:'合成储能云平台',task:entry,busy:false,uncertain:false},now:0}));
const report=(status,extra={})=>({accepted:[{requirement_id:'REQ-synthetic',candidate_temp_id:'C-synthetic'}],
  rejected:[],source_validation:'model_semantic_checked_not_human_approval',intake_grounding:grounding(status),...extra});
const draftHtml=review=>html(React.createElement(DraftPackageStatus,{project:{business_draft_reviews:[review]},onLocate:noWrite,onCreate:noWrite}));
'''


def test_grounding_phases_reach_inline_expanded_and_compact_stage_without_actions():
    run_ts(LOAD + r'''
      for(const [state,pattern] of [
        ['checking',/核对资料与已答信息/],
        ['correcting',/根据.*依据修正/],
        ['rechecking',/修正后.*(核对|复核)/],
      ]){
        const entry=task(state),before=JSON.stringify(entry);
        assert.match(taskCurrentStage(entry),pattern,'compact stage '+state);
        assert.match(statusHtml(entry),pattern,'project status '+state);
        assert.match(progressHtml(entry),pattern,'expanded status '+state);
        assert.equal(JSON.stringify(entry),before,'display cannot mutate task or saved input');
      }
      const legacy=task('checking',{intake_grounding:undefined});
      assert.equal(taskCurrentStage(legacy),'整理材料和目标','legacy stage is preserved');
      assert(!statusHtml(legacy).includes('核对完成'));
    ''')


def test_completed_grounding_remains_an_unconfirmed_model_draft_and_requires_applied_fact():
    run_ts(LOAD + r'''
      for(const state of ['passed','repaired']){
        const entry=task(state),review=report(state),before=JSON.stringify({entry,review});
        const views=[taskCurrentStage(entry),statusHtml(entry),progressHtml(entry),draftHtml(review)];
        for(const view of views){
          assert(/(待.*确认|仍.*确认|待.*核对)/.test(view),'completed candidate needs user decision: '+view);
          assert(!/来源全部准确|已通过人工确认|用户已确认/.test(view));
        }
        if(state==='repaired')assert(views.every(view=>view.includes('修正')),'repair is visible in all surfaces');
        assert.equal(JSON.stringify({entry,review}),before);
        const unapplied=grounding(state,{candidate_applied:false});
        for(const view of [taskCurrentStage({...entry,intake_grounding:unapplied}),
          statusHtml({...entry,intake_grounding:unapplied}),draftHtml({...review,intake_grounding:unapplied})]){
          assert(!/核对完成|完成核对|已修正并/.test(view),'unapplied candidate cannot claim a usable completed result');
        }
      }
    ''')


def test_blocked_or_unverified_candidate_is_evidence_only_with_existing_input_preserved():
    run_ts(LOAD + r'''
      for(const [state,error] of [['blocked','INTAKE_GROUNDING_BLOCKED'],['unverified','INTAKE_GROUNDING_UNVERIFIED']]){
        const reason='合成规则仍与已答信息不一致 <script>synthetic-only</script>';
        const entry=task(state,{error,local_recovery_available:true,intake_grounding:grounding(state,{reason})});
        const before=JSON.stringify(entry);
        assert.equal(canRecoverDraft(entry),false,'failed source candidate cannot be adopted by generic recovery');
        assert(taskFailure(entry),'failure uses the actual failure notice');
        for(const view of [statusHtml(entry),progressHtml(entry),html(React.createElement(TaskFailureNotice,{task:entry}))]){
          assert(view.includes('未写入'),'candidate not applied is explicit');
          assert(view.includes('保留'),'input and previous content retention is explicit');
          assert(!view.includes('恢复草稿（不调用 AI）'));
          assert(!view.includes('<script>synthetic-only</script>'),'reason is escaped');
          assert(!/请重新回答全部|需要重新回答全部|重新描述全部/.test(view));
        }
        assert.equal(JSON.stringify(entry),before,'failure display preserves the original retry input');
      }
    ''')


def test_draft_notice_does_not_upgrade_old_locator_or_missing_evidence_to_semantic_success():
    run_ts(LOAD + r'''
      const old=report('passed',{intake_grounding:undefined,source_validation:'locator_checked_not_semantic_approval'});
      const unknown=report('passed',{intake_grounding:undefined,source_validation:undefined});
      const wrongProtocol=report('passed',{intake_grounding:grounding('passed',{protocol:'future-unknown'})});
      for(const [review,isLocator] of [[old,true],[unknown,false],[wrongProtocol,false]]){
        const before=JSON.stringify(review),view=draftHtml(review);
        assert(!view.includes('资料引用已检查'),'blanket old claim removed');
        assert(!/已核对资料与已答信息|核对完成|完成核对/.test(view),'evidence cannot be upgraded by rendering');
        if(isLocator)assert(/基础引用/.test(view),'known locator check may describe its actual scope');
        assert.equal(JSON.stringify(review),before);
      }
      const sourceOnly=report('passed',{source_validation:'locator_checked_not_semantic_approval'});
      assert(!/已核对资料与已答信息|核对完成|完成核对/.test(draftHtml(sourceOnly)),
        'an unrelated source validation marker cannot claim semantic review completion');
    ''')


def test_display_keeps_explicit_cancel_retry_result_and_source_callbacks():
    run_ts(LOAD + r'''
      const calls=[];
      const explicit=(state,extra={})=>{
        const props={...statusProps(task(state,extra)),onCancel:()=>calls.push('cancel'),
          onRetry:()=>calls.push('retry'),onViewResult:()=>calls.push('result')};
        const tree=TaskStatus(props);return {props,tree};
      };
      const running=explicit('checking'),stop=nodes(running.tree).find(n=>n.type==='button'&&text(n)==='停止后续步骤');
      assert.equal(stop.props.onClick,running.props.onCancel);assert.equal(calls.length,0);
      stop.props.onClick();assert.deepEqual(calls,['cancel']);
      const failed=explicit('blocked',{error:'INTAKE_GROUNDING_BLOCKED'}),retry=nodes(failed.tree).find(n=>n.type==='button'&&text(n)==='手动重试');
      assert.equal(retry.props.onClick,failed.props.onRetry);retry.props.onClick();assert.deepEqual(calls,['cancel','retry']);
      const success=explicit('passed'),result=nodes(success.tree).find(n=>n.type==='button'&&text(n)==='查看分析结果');
      assert.equal(result.props.onClick,success.props.onViewResult);result.props.onClick();assert.deepEqual(calls,['cancel','retry','result']);
      const refs=[{source_id:'SOURCE-synthetic',excerpt_id:'EXCERPT-synthetic'}],seen=[];
      const review=report('passed',{accepted:[{requirement_id:'REQ-synthetic',candidate_temp_id:'C-synthetic',
        field_evidence:{statement:{basis:'fact',text:'合成已引资料',source_refs:refs}}}]});
      const row=DraftRowReview({project:{business_draft_reviews:[review]},requirementId:'REQ-synthetic',
        candidateId:'C-synthetic',disabled:false,onRetry:noWrite,onSources:got=>seen.push(got)});
      const source=nodes(row).find(n=>n.type==='button'&&text(n)==='查看这项来源');
      assert.equal(seen.length,0);source.props.onClick();assert.deepEqual(seen,[refs]);
    ''')


def test_body_source_notice_uses_parent_marker_and_preserves_its_limited_scope():
    run_ts(LOAD + r'''
      const marker='body_source_semantic_checked_not_human_approval';
      const source={protocol:'noninitial-source-only-1',
        scope:'generated_requirement_statement_behavior_acceptance_only',
        source_only:true,coverage_review:false,status:'passed',corrections:1,
        candidate_hash:'candidate-synthetic',input_hash:'input-synthetic',history:[]};
      const review=report('passed',{intake_grounding:undefined,
        source_validation:marker,business_source_review:source});
      const before=JSON.stringify(review),view=draftHtml(review);
      assert.match(view,/正文、行为、验收各自来源核对已通过/);
      assert.match(view,/不代表全部已答信息已覆盖/);
      assert(!view.includes('已核对资料与已答信息'),'source-only is not a coverage review');
      assert.equal(JSON.stringify(review),before,'rendering cannot approve or rewrite evidence');
      const forged=report('passed',{intake_grounding:undefined,source_validation:undefined,
        business_source_review:{...source,source_validation:marker}});
      assert(!draftHtml(forged).includes('各自来源核对已通过'),
        'a nested marker cannot substitute for the parent validation result');
    ''')


def test_body_source_notice_cannot_promote_pending_or_mismatched_evidence():
    run_ts(LOAD + r'''
      const source={protocol:'noninitial-source-only-1',
        scope:'generated_requirement_statement_behavior_acceptance_only',
        source_only:true,coverage_review:false,status:'passed',corrections:0,
        candidate_hash:'candidate-synthetic',input_hash:'input-synthetic',history:[]};
      for(const change of [{status:'pending'},{status:'blocked'},
        {scope:'unrecognized-scope'},{source_only:false},{coverage_review:true},
        {protocol:'unrecognized-protocol'}]){
        const review=report('passed',{intake_grounding:undefined,
          source_validation:'body_source_semantic_checked_not_human_approval',
          business_source_review:{...source,...change}});
        const before=JSON.stringify(review),view=draftHtml(review);
        assert(!view.includes('各自来源核对已通过'),'incomplete evidence cannot claim success');
        assert(!view.includes('已核对资料与已答信息'),'scope cannot be silently upgraded');
        assert.equal(JSON.stringify(review),before);
      }
    ''')
