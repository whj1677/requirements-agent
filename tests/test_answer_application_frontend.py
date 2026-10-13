"""Exercise the real review helpers and rendered answer review controls."""
from tests.test_scope_frontend import run_ts


def test_real_api_single_adoption_then_fresh_answer_review(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_answer_review_readiness import review_client

    def edit(project):
        item = next(i for i in project['items'] if i['id'] == 'RULE-0001')
        item.update(kind='constraint', selection_status='candidate')

    app, pid, qid = review_client(tmp_path, edit)
    before = app.state.store.get(pid)
    path = f'/api/projects/{pid}/questions/{qid}/application-review'
    def body(review):
        return dict(expected_revision=review['expected_revision'], answer_hash=review['answer_hash'],
                    clause_hashes={t['item']['id']: t['clause_hash'] for t in review['targets']},
                    review_confirmed=True,
                    answer_implementations={qid: [dict(item_id=t['item']['id'], field='statement', quote=t['item']['statement']) for t in review['targets']]})

    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key': 'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
        review = client.get(path).json()
        assert client.post(path, json=body(review)).status_code == 409
        assert app.state.store.get(pid) == before
        receipt = client.post(f'/api/projects/{pid}/items/RULE-0001', json=dict(
            expected_revision=review['expected_revision'], selection_status='selected'))
        assert receipt.status_code == 200, receipt.text
        adopted = app.state.store.get(pid)
        assert adopted['questions'][0].get('understanding_status', 'pending') != 'applied'
        assert client.post(path, json=body(review)).status_code == 409
        assert app.state.store.get(pid) == adopted
        fresh = client.get(path).json()
        assert fresh['expected_revision'] == review['expected_revision'] + 1
        assert all(t['readiness']['can_confirm'] for t in fresh['targets'])
        result = client.post(path, json=body(fresh))
        assert result.status_code == 200, result.text
    after = app.state.store.get(pid)
    assert after['questions'][0]['understanding_status'] == 'applied'
    assert after['revision'] == before['revision'] + 2
    assert after['sources'] == before['sources']
    for item in after['items']:
        old = next(i for i in before['items'] if i['id'] == item['id'])
        assert item['statement'] == old['statement']
        assert item.get('behavior') == old.get('behavior')
    assert app.state.store.records(pid, 'run') == []


def test_record_requires_all_ready_current_clauses_and_explicit_confirmation():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/answer-application-review.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {answerReviewState,recordAnswerReview}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const row=(id,selected,ready)=>({item:{id,statement:'共用账号',selection_status:selected?'selected':'candidate'},clause_hash:'h-'+id,readiness:{can_confirm:ready,can_adopt:!selected,issues:ready?[]:['请先采纳'],behavior_review_required:false}});
      const review={expected_revision:4,question_id:'Q1',answer:'共用账号',answer_hash:'answer-hash',targets:[row('REQ-1',true,true),row('ITEM-1',false,false)]};
      assert.equal(answerReviewState(review,4).blocked.length,1);
      const noWrite=async()=>assert.fail('unready review written');
      await assert.rejects(()=>recordAnswerReview(review,4,true,noWrite),/先处理/);
      const ready={...review,targets:review.targets.map(t=>({...t,item:{...t.item,selection_status:'selected'},readiness:{...t.readiness,can_confirm:true}}))};
      await assert.rejects(()=>recordAnswerReview(ready,4,false,noWrite),/勾选/);
      await assert.rejects(()=>recordAnswerReview(ready,5,true,noWrite),/变化/);
      await assert.rejects(()=>recordAnswerReview({...ready,targets:[]},4,true,noWrite));
      await assert.rejects(()=>recordAnswerReview({...ready,targets:[{...ready.targets[0],readiness:{can_confirm:false,issues:['来源待核对']}}]},4,true,noWrite));
      let body;
      await assert.rejects(()=>recordAnswerReview(ready,4,true,noWrite),/位置|原文/);
      const witnesses={Q1:ready.targets.map(t=>({item_id:t.item.id,field:'statement',quote:t.item.statement}))};
      await assert.rejects(()=>recordAnswerReview(ready,4,true,noWrite,{Q1:witnesses.Q1.slice(0,1)}),/位置|原文/);
      await recordAnswerReview(ready,4,true,async value=>{body=value;},witnesses);
      assert.deepEqual(body,{expected_revision:4,answer_hash:'answer-hash',clause_hashes:{'REQ-1':'h-REQ-1','ITEM-1':'h-ITEM-1'},review_confirmed:true,answer_implementations:witnesses});
      assert(!('selection_status' in body),'recording answer must not adopt clauses');
    ''')


def test_explicit_single_adoption_reads_fresh_review_and_failure_never_records():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/answer-application-review.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {adoptAnswerClause}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const review={expected_revision:4,question_id:'Q1',answer:'共用账号',answer_hash:'a',targets:[{item:{id:'ITEM-1',selection_status:'candidate'},clause_hash:'h',readiness:{can_adopt:true,can_confirm:false,issues:['请先采纳']}}]};
      const fresh={...review,expected_revision:5,answer:'最新回答',answer_hash:'new-a',targets:[{...review.targets[0],item:{id:'ITEM-1',selection_status:'selected'},readiness:{can_adopt:false,can_confirm:true,issues:[]}}]};
      let calls=[];
      const write=async(path,body)=>{calls.push([path,body]);};
      const result=await adoptAnswerClause(review,'ITEM-1',write,async()=>{calls.push('read');return fresh;});
      assert.deepEqual(calls,[['/items/ITEM-1',{expected_revision:4,selection_status:'selected'}],'read']);
      assert.equal(result.expected_revision,5);assert.equal(result.answer_hash,'new-a');
      assert(!calls.some(c=>Array.isArray(c)&&c[0].includes('application-review')),'single adoption never records answer');
      await assert.rejects(()=>adoptAnswerClause(review,'OTHER',async()=>assert.fail(),async()=>assert.fail()));
      await assert.rejects(()=>adoptAnswerClause({...review,targets:[{...review.targets[0],readiness:{can_adopt:false}}]},'ITEM-1',async()=>assert.fail(),async()=>assert.fail()));
      calls=[];
      await assert.rejects(()=>adoptAnswerClause(review,'ITEM-1',async()=>{calls.push('write');throw Error('stale version');},async()=>assert.fail('read after failed write')),/stale version/);
      assert.deepEqual(calls,['write']);
      calls=[];
      await assert.rejects(()=>adoptAnswerClause(review,'ITEM-1',write,async()=>{calls.push('read');throw Error('network');}),/已采纳.*重新载入/);
      assert.equal(calls.filter(c=>Array.isArray(c)).length,1,'no duplicate write after failed read');
    ''')


def test_dialog_explains_disabled_reason_and_offers_precise_actions():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/answer-application.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const review={expected_revision:4,question:'账号怎样使用？',question_id:'Q1',answer:'共用一个账号',answer_hash:'a',targets:[{item:{id:'ITEM-1',kind:'constraint',title:'权限设置',statement:'家长与学生均可操作',selection_status:'candidate',behavior:{permissions:'相同权限'}},clause_hash:'h',sources:[{source_id:'S1',excerpt_id:'E1',title:'人工回答',text:'共用一个账号'}],readiness:{can_adopt:true,can_confirm:false,behavior_review_required:false,issues:['请先采纳此条款']}}]};
      const props={review,revision:4,confirmed:true,saving:'',busy:false,error:'',onConfirm:()=>{},onApply:()=>assert.fail('render submits'),onAdopt:()=>{},onEdit:()=>{},onReload:()=>{},onClose:()=>{}};
      const render=p=>renderToStaticMarkup(React.createElement(module.exports.AnswerReviewDialog,p));
      const blocked=render(props);
      for(const text of ['还有 1 条关联条款需处理','采纳此条款','定位并修改此条款','重新载入核对','关闭核对','权限设置','人工回答','共用一个账号','answer-review-header','answer-review-body','answer-review-footer','answer-review-confirm'])assert(blocked.includes(text),text);
      assert(/<input type="checkbox" disabled=""/.test(blocked),'blocked clause disables review acknowledgement');
      assert(/disabled="">记录回答落实<\/button>/.test(blocked));
      const allReady={...review,targets:[{...review.targets[0],item:{...review.targets[0].item,selection_status:'selected'},readiness:{can_adopt:false,can_confirm:true,behavior_review_required:false,issues:[]}}]};
      const missing=render({...props,review:allReady});
      assert(/disabled="">记录回答落实<\/button>/.test(missing),'a blanket acknowledgement without a quote is insufficient');
      const ready=render({...props,review:allReady,implementations:{Q1:[{item_id:'ITEM-1',field:'statement',quote:allReady.targets[0].item.statement}]}});
      assert(/class="primary">记录回答落实<\/button>/.test(ready));
      assert(!ready.includes('还有 1 条关联条款需处理'));
      const stale=render({...props,review:allReady,revision:5});
      assert(stale.includes('内容已变化'));assert(/disabled="">记录回答落实<\/button>/.test(stale));
      const behavior=render({...props,review:{...review,targets:[{...review.targets[0],item:{...review.targets[0].item,kind:'requirement'},readiness:{can_adopt:false,can_confirm:false,behavior_review_required:true,issues:['请先核对业务行为']}}]}});
      assert(behavior.includes('去核对业务行为'));assert(!/<button[^>]*>采纳此条款<\/button>/.test(behavior));
    ''')


def test_saved_table_witnesses_require_current_applied_targets_and_preserve_manual_drafts():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refinement-sheet-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {initial,mergeSuggestions,retainSheetImplementations}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const ref={item_id:'REQ-1',field:'statement',quote:'仅有权限站点可查看'};
      const q={id:'Q1',question:'权限如何限定？',related_refs:['REQ-1'],status:'answered',answer:'仅有权限站点可查看',understanding_status:'applied',applied_item_ids:['REQ-1'],answer_implementation_contract:2,answer_applications:{'REQ-1':{contract_version:2,witnesses:[{field:ref.field,quote:ref.quote}]}}};
      const p={id:'P1',revision:1,items:[{id:'REQ-1',kind:'requirement',applies_to:'to_be',selection_status:'selected',statement:ref.quote}],questions:[q],product_context:{scope_ids:['REQ-1']}};
      const applied=initial(p,[]);assert.deepEqual(applied.answer_implementations.Q1,[ref]);
      const changed={...p,revision:2,questions:[{...q,answer:'新版回答',understanding_status:'pending',applied_item_ids:[]}]};
      const pending=initial(changed,[]);assert.deepEqual(pending.answer_implementations.Q1,[],'old v2 records do not preselect after an external answer change');
      assert.deepEqual(mergeSuggestions(applied,structuredClone(applied),pending).answer_implementations.Q1,undefined,'unchanged local evidence follows current remote validity');
      assert.deepEqual(initial({...p,questions:[{...q,applied_item_ids:[]}]},[]).answer_implementations.Q1,[],'applied status alone cannot reuse an invalid target');
      const manual={...pending,answer_implementations:{Q1:[ref]}};
      assert.deepEqual(mergeSuggestions(manual,pending,pending).answer_implementations.Q1,[ref],'a new explicit draft selection remains restorable');
      assert.deepEqual(retainSheetImplementations({...manual,answers:{Q1:'再次变更'}},manual).answer_implementations,{});
      assert.deepEqual(retainSheetImplementations({...manual,rows:{'REQ-1':{...manual.rows['REQ-1'],statement:'正文变化'}}},manual).answer_implementations,{});
    ''')


def test_clause_locator_supports_constraint_identifiers():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/workbench.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const project={id:'P1',revision:4,items:[{id:'ITEM-1',kind:'constraint',title:'权限设置',statement:'共用一个账号',applies_to:'to_be',selection_status:'candidate'}],sources:[],questions:[],documents:{},options:[]};
      const html=renderToStaticMarkup(React.createElement(module.exports.ArtifactTabs,{project,tab:'summary',embedded:true,setTab:()=>{},onOption:()=>{},onItem:()=>{},onEditItem:()=>{},onStage:()=>{},onActivate:()=>{},onReject:()=>{},onNavigate:()=>{},previewUrl:'',runs:[],models:{},onCancel:()=>{},onResume:()=>{}}));
      assert(html.includes('按条款编号定位（需求、规则、约束、验收）'));
      assert(html.includes('id="clause-P1-ITEM-1"'));
      assert(html.includes('编辑原文'));assert(!html.includes('输入 REQ 编号'));
    ''')
