"""Only concrete current recommendations enter editable answer drafts."""
from tests.test_scope_frontend import run_ts


def test_review_container_quotes_do_not_become_invalid_confirmation_locations():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const result=await build({entryPoints:['src/pm-prototype/prewrite-batch-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {initialBatch,batchImplementations}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
      const item={id:'REQ-1',kind:'requirement',title:'查询',statement:'只读查询已有结果。',behavior:{flow:'校验权限后读取。'},selection_status:'selected',applies_to:'to_be',source_refs:[],related_refs:[],detail_contract:{flow:{nodes:[{id:'N1',label:'读取'}],edges:[]}}};
      const q={id:'Q1',question:'权限如何处理？',answer:'无权限拒绝读取。',related_refs:['REQ-1'],status:'answered',blocking:true};
      const p={id:'P',revision:2,items:[item],sources:[],questions:[q],product_context:{scope_ids:['REQ-1']},prewrite_review:{findings:[{id:'F',question_id:'Q1',severity:'blocker',status:'open',related_refs:['REQ-1'],quotes:[{object_id:'REQ-1',path:'detail_contract.flow',text:'整个流程对象'}]}]}};
      const sheet=initialBatch(p,[]);sheet.scope=['REQ-1'];sheet.rows['REQ-1'].behavior.flow='前端和后端均校验权限，拒绝后不显示结果。';
      assert.deepEqual(batchImplementations(p,sheet).Q1,[{item_id:'REQ-1',field:'behavior.flow',quote:sheet.rows['REQ-1'].behavior.flow}]);
      p.prewrite_review.findings[0].quotes=[{object_id:'REQ-1',path:'detail_contract.flow.nodes.N1.label',text:'读取'}];
      assert.equal(batchImplementations(p,sheet).Q1[0].field,'detail_contract.flow.nodes.N1.label');
    ''')

def test_recommendation_display_priority_does_not_adopt_or_resurrect_stale_answers():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const result=await build({entryPoints:['src/pm-prototype/prewrite-batch-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {answerOffer,initialBatch}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
      const f={id:'F',question_id:'Q',severity:'blocker',status:'open',related_refs:[],suggested_resolution:'请由产品经理补充。'};
      const p={id:'P',revision:2,items:[],sources:[],questions:[{id:'Q',question:'角色权限如何确定？',answer:null,related_refs:[],status:'open'}],product_context:{},prewrite_review:{findings:[f]}};
      const before=structuredClone(p);
      assert.deepEqual(answerOffer(p,f),{text:'',kind:'missing'});
      assert.equal(initialBatch(p,[]).answers.Q,'','review instructions are not concrete answers');
      f.recommended_answer='建议本期只读查看，不新增写操作。';
      assert.equal(initialBatch(p,[]).answers.Q,f.recommended_answer);
      assert.equal(p.questions[0].answer,null);
      p.questions[0].answer='人工已确认角色权限。';
      assert.deepEqual(answerOffer(p,f),{text:'人工已确认角色权限。',kind:'saved'});
      p.questions[0].answer=null;
      p.refinement_completion={mode:'prewrite',applied_revision:2,answers:{Q:{text:'当前批量草稿。'}}};
      assert.equal(initialBatch(p,[]).answers.Q,'当前批量草稿。');
      p.refinement_completion.applied_revision=1;delete f.recommended_answer;
      assert.equal(initialBatch(p,[]).answers.Q,'','expired completions cannot repopulate the new check');
      p.questions[0].recommendation={text:'已有单题推荐。'};
      assert.equal(initialBatch(p,[]).answers.Q,'已有单题推荐。');
      assert.equal(before.questions[0].answer,null);
    ''')


def test_correction_reuses_explicit_saved_answer_without_confirming_new_question():
    run_ts(r''' 
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const result=await build({entryPoints:['src/pm-prototype/prewrite-batch-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {answerOffer}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
      const p={questions:[{id:'Q-old',answer:'三家提供统一信号清单。',status:'answered'},{id:'Q-new',answer:null,status:'open'}]};
      const f={question_id:'Q-new',decision_kind:'correction',message:'已保存回答 Q-old 尚未落实到正文。'};
      assert.deepEqual(answerOffer(p,f),{text:'三家提供统一信号清单。',kind:'draft',reused:true});
      assert.equal(p.questions[1].answer,null);
      assert.equal(answerOffer(p,{...f,message:'需要决定新问题。'}).kind,'missing');
      assert.equal(answerOffer(p,{...f,decision_kind:'business'}).kind,'missing');
      assert.equal(answerOffer(p,{...f,message:'已保存回答 Q-old-extra'}).kind,'missing');
    ''')
