"""Execute the real confirmation callback; receipt lookup must never replay writes."""
from tests.test_scope_frontend import run_ts
from tests.test_detail_unknown_scope_frontend import SCOPE


RECOVERY = SCOPE.replace(
    "cancel:()=>{}});", "cancel:()=>{globalThis.__receiptCancelCount++;}});"
).replace(
    "export const api=(path)=>Promise.resolve(globalThis.__bulkApi(path));",
    "export const api=(path,options)=>Promise.resolve(globalThis.__bulkApi(path,options));",
) + r'''
globalThis.__receiptCancelCount=0;
function completeProject(p){return {...structuredClone(p),name:'合成确认恢复项目',mode:'requirements',reference_mode:'builtin',sources:[],options:[],messages:[],documents:{},hashes:{},confirmation_issues:[],baselines:[],exports:[]};}
function confirmedReply(ctx,destination='delivery'){
  const payload=ctx.posts[0].payload,written=completeProject(ctx.f.p);written.revision=ctx.f.p.revision+1;
  written.product_context={...written.product_context,...payload.context.values,scope_ids:payload.context.scope_ids};
  written.context_field_states=structuredClone(payload.context.field_states);written.scope_profile=structuredClone(payload.context.scope_profile);
  for(const row of payload.rows){const item=written.items.find(x=>x.id===row.requirement_id);Object.assign(item,{statement:row.statement,behavior:structuredClone(row.behavior),detail_contract:structuredClone(row.detail_contract)});}
  for(const answer of payload.answers){const q=written.questions.find(q=>q.id===answer.question_id);q.answer=answer.answer;q.status='answered';q.understanding_status='applied';q.applied_item_ids=['REQ-1'];}
  const pending=destination==='refinement'?['Q1']:[];
  const receipt={id:'RC-synthetic',idempotency_key:payload.idempotency_key,request_hash:'c'.repeat(64),result_revision:written.revision,results:[],confirmed_question_ids:['Q1'],confirmed_clause_ids:[],stage_checks:[1,2,3],preview_hash:'d'.repeat(64),destination,implementation_pending_ids:pending,implementation_issues:[]};
  return {confirmed:true,project:written,receipt,repeated:true,destination,implementation_pending_ids:pending,issues:[]};
}
async function receiptFixture(code='REQUEST_TIMEOUT',reply=ctx=>confirmedReply(ctx)){
  const f=scopeFixture();f.p=completeProject(f.p);
  const view=await tableFixture(f),ctx={f,view,posts:[],gets:[],refresh:0,nav:0,originalError:'合成确认请求结果未知，请保留草稿',cancelStart:globalThis.__receiptCancelCount};
  ctx.key=f.p.id+'-refinement-sheet';ctx.detailKey=f.p.id+'-table-draft-REQ-1';globalThis.__bulkSession.set(ctx.detailKey,'独立本地详细草稿');
  ctx.beforeSession=new Map(globalThis.__bulkSession);ctx.beforeSheet=structuredClone(view.view.sheet());
  globalThis.__bulkApi=(path,options)=>{
    if(path.includes('/refinement-confirmation/receipts/')){ctx.gets.push({path,options});return reply(ctx);}
    return path.endsWith('/state')?{revision:f.p.revision,rows:[]}:{status:'ready'};
  };
  const props={...view.props,onRefresh:()=>ctx.refresh++,onStageConfirmed:()=>ctx.nav++,mutate:async(path,payload)=>{
    ctx.posts.push({path,payload});if(code){const error=Object.assign(new Error(ctx.originalError),{code});throw error;}
    return reply(ctx);
  }};
  view.view.render(props);ctx.props=props;
  ctx.submit=async()=>{const primary=buttons(view.view.tree).find(n=>n.props.className==='primary');assert(primary&&!primary.props.disabled);click(primary);await view.view.settle();};
  return ctx;
}
function assertOriginalPayload(ctx){
  assert.equal(ctx.posts.length,1,'one original confirmation POST');assert.equal(ctx.posts[0].path,'/refinement-confirmation');
  const p=ctx.posts[0].payload;assert.equal(p.rows[0].statement,'人工正文保持');assert.equal(p.answers.find(x=>x.question_id==='Q1').answer,ctx.f.mine.answers.Q1);
  assert.equal(p.context.scope_profile.scenario,'modify_existing');assert.deepEqual(p.rows[0].detail_contract,ctx.f.contract);
  assert(!JSON.stringify(p).includes('detail_unknown_scope_reviews'));
}
function assertLookupBinding(ctx){
  assertOriginalPayload(ctx);assert.equal(ctx.gets.length,1,'one read-only receipt lookup');
  const key=ctx.posts[0].payload.idempotency_key;assert(key&&key.length>=8);
  assert.equal(ctx.gets[0].path,`/projects/${encodeURIComponent(ctx.f.p.id)}/refinement-confirmation/receipts/${encodeURIComponent(key)}`);
  assert(!ctx.gets[0].options?.method||ctx.gets[0].options.method==='GET','receipt lookup never POSTs');
}
function assertPreserved(ctx){
  assert.equal(globalThis.__bulkSession.get(ctx.key),ctx.beforeSession.get(ctx.key),'uncertain or malformed receipt must preserve primary local draft');
  assert.equal(globalThis.__bulkSession.get(ctx.key+'-base'),ctx.beforeSession.get(ctx.key+'-base'));
  assert.equal(globalThis.__bulkSession.get(ctx.detailKey),ctx.beforeSession.get(ctx.detailKey));
  assert.equal(globalThis.__receiptCancelCount,ctx.cancelStart,'invalid recovery must not cancel pending draft writer');
  assert.deepEqual(ctx.view.view.sheet(),ctx.beforeSheet,'manual text, Q1 and scope remain in actual state');
  assert.equal(ctx.nav,0);assert(text(ctx.view.view.tree).includes(ctx.originalError),'original uncertainty/error remains visible');
}
'''


def test_malformed_matching_receipt_never_clears_cache_before_project_derivation():
    run_ts(RECOVERY + r'''
      const c=await receiptFixture('REQUEST_TIMEOUT',ctx=>({confirmed:true,project:{id:ctx.f.p.id},receipt:{idempotency_key:ctx.posts[0].payload.idempotency_key},destination:'delivery',implementation_pending_ids:[]}));
      await c.submit();assertLookupBinding(c);assertPreserved(c);assert.equal(c.refresh,0);
    ''')


def test_timeout_and_network_use_one_original_key_lookup_then_normal_delivery_success():
    run_ts(RECOVERY + r'''
      for(const code of ['REQUEST_TIMEOUT','NETWORK']){
        const c=await receiptFixture(code);await c.submit();assertLookupBinding(c);
        assert.equal(c.nav,1);assert.equal(c.refresh,1);assert.equal(globalThis.__receiptCancelCount,c.cancelStart+1);
        assert(!globalThis.__bulkSession.has(c.key));assert(!globalThis.__bulkSession.has(c.detailKey));
        assert.equal(c.view.view.sheet().revision,c.f.p.revision+1);assert.equal(c.view.view.sheet().rows['REQ-1'].statement,'人工正文保持');
      }
    ''')


def test_normal_post_success_and_non_unknown_errors_never_query_receipt():
    run_ts(RECOVERY + r'''
      const normal=await receiptFixture(null);await normal.submit();assertOriginalPayload(normal);assert.equal(normal.gets.length,0);assert.equal(normal.nav,1);assert.equal(normal.refresh,0);
      for(const code of ['VALIDATION_ERROR','FORBIDDEN','STALE_REVISION']){
        const c=await receiptFixture(code);await c.submit();assertOriginalPayload(c);assert.equal(c.gets.length,0);assertPreserved(c);assert.equal(c.refresh,code==='STALE_REVISION'?1:0);
      }
      const denied=await receiptFixture(null,()=>({confirmed:false,message:'服务端未写入',issues:[]}));await denied.submit();assertOriginalPayload(denied);assert.equal(denied.gets.length,0);assert.equal(denied.nav,0);assert.equal(globalThis.__bulkSession.get(denied.key),denied.beforeSession.get(denied.key));
    ''')


def test_bad_missing_stale_or_failed_lookup_preserves_draft_without_post_replay():
    run_ts(RECOVERY + r'''
      const changes=[
        r=>{r.confirmed=false;r.status='not_found';},r=>{r.confirmed=false;r.status='not_current';},
        r=>r.project.id='P-other',r=>r.receipt.idempotency_key='other-original-key',
        r=>r.project.revision++,r=>{r.project.revision='2';r.receipt.result_revision='2';},
        r=>{delete r.project.revision;delete r.receipt.result_revision;},
        r=>r.destination='unknown-destination',r=>delete r.receipt,
        r=>r.project.items=null,r=>r.project.items=[null],r=>r.project.questions=null,
      ];
      for(const change of changes){const c=await receiptFixture('NETWORK',ctx=>{const r=confirmedReply(ctx);change(r);return r;});await c.submit();assertLookupBinding(c);assertPreserved(c);assert.equal(c.refresh,0);}
      const failed=await receiptFixture('REQUEST_TIMEOUT',()=>Promise.reject(new Error('synthetic GET failed')));await failed.submit();assertLookupBinding(failed);assertPreserved(failed);assert.equal(failed.refresh,0);
    ''')


def test_pending_answer_refinement_destination_keeps_normal_success_behavior():
    run_ts(RECOVERY + r'''
      const c=await receiptFixture('NETWORK',ctx=>confirmedReply(ctx,'refinement'));await c.submit();assertLookupBinding(c);
      assert.equal(c.nav,0);assert.equal(c.refresh,1);assert.equal(globalThis.__receiptCancelCount,c.cancelStart+1);
      const accepted=JSON.parse(globalThis.__bulkSession.get(c.key));assert.equal(accepted.revision,c.f.p.revision+1);
      assert.equal(accepted.answers.Q1,c.f.mine.answers.Q1);assert.equal(accepted.rows['REQ-1'].statement,'人工正文保持');
      assert(text(c.view.view.tree).includes('项回答待落实到正文'));assert(globalThis.__bulkSession.has(c.key+'-base'));
    ''')
