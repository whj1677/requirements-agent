"""Answer-location and scope invariants using synthetic project data."""

from tests.test_scope_frontend import run_ts


def test_all_141_locations_are_bound_to_current_exact_original_text():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const load=async path=>{const built=await build({entryPoints:[path],bundle:true,write:false,format:'esm',platform:'node'});return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));};
      const {emptyDetailContract,detailWitnessOptions}=await load('src/pm-prototype/detail-contract.ts');
      const {implementationFieldValue,validImplementationRefs}=await load('src/answer-application-review.ts');
      const detail=emptyDetailContract();
      for(const section of ['interactions','flow','state_transitions'])detail.applicability[section].applicable=false;
      detail.fields=Array.from({length:140},(_,i)=>({id:'F'+String(i+1).padStart(3,'0'),name:'合成字段 '+String(i+1).padStart(3,'0')}));
      const item={id:'REQ-1',kind:'requirement',statement:'合成条款正文',behavior:{},detail_contract:detail};
      const options=[{field:'statement',label:'条款正文'},...detailWitnessOptions(detail)];
      assert.equal(options.length,141);
      assert.equal(new Set(options.map(option=>option.field)).size,141);
      for(const option of options){
        const quote=implementationFieldValue(item,option.field);
        assert(quote.trim(),option.field);
        assert(option.label.includes(quote)||option.field==='statement','location labels identify the corresponding field');
        const ref={item_id:item.id,field:option.field,quote};
        assert.deepEqual(validImplementationRefs([ref],[item]),[ref]);
      }
      const last=options.at(-1),ref={item_id:item.id,field:last.field,quote:implementationFieldValue(item,last.field)};
      detail.fields[139].name='人工修改后的原文';
      assert.deepEqual(validImplementationRefs([ref],[item]),[],'a changed original cannot silently reuse an older quote');
      assert.deepEqual(validImplementationRefs([],[item]),[],'offered locations are not selected witnesses');
    ''')


def test_scope_payload_contains_business_scope_only():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const load=async path=>{const built=await build({entryPoints:[path],bundle:true,write:false,format:'esm',platform:'node'});return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));};
      const {initial}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const {confirmationPayload}=await load('src/pm-prototype/prewrite-batch-state.ts');
      const p={id:'P1',revision:1,items:[{id:'REQ-1',kind:'requirement',title:'合成需求',statement:'展示结果',selection_status:'selected',applies_to:'to_be',behavior:{},related_refs:[]}],questions:[],product_context:{scope_ids:['REQ-1']},scope_profile:{scenario:'new_product',dependencies:'independent'}};
      const sheet=initial(p,[]),payload=confirmationPayload(p,sheet);
      assert.deepEqual(payload.context.scope_ids,['REQ-1']);
      assert.deepEqual(Object.keys(payload.context).sort(),['field_states','scope_ids','scope_profile','values']);
      assert.equal(payload.rows.length,1);
      const allowed=new Set(['requirement_id','candidate_id','statement','behavior','change_type','acceptance_items','confirmed_question_ids','detail_contract']);
      assert(Object.keys(payload.rows[0]).every(key=>allowed.has(key)),'display-only scope state must not enter the API row');
    ''')
