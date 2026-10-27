"""Only concrete current recommendations enter editable answer drafts."""
from tests.test_scope_frontend import run_ts

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
