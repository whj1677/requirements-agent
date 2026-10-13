"""Refinement context follows actual links while preserving shared-answer groups."""
from tests.test_scope_frontend import run_ts


def test_related_questions_keep_group_siblings_but_exclude_unrelated_or_retired_questions():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refine-context.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {questionsForItem}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={items:[{id:'REQ-1'},{id:'REQ-2'},{id:'REV-1',target_item_id:'REQ-1',answer_refs:['Q-answer']}],questions:[
        {id:'Q-1',related_refs:['REQ-1'],decision_group_id:'G-1',status:'open'},
        {id:'Q-sibling',related_refs:['REQ-2'],decision_group_id:'G-1',status:'open'},
        {id:'Q-other',related_refs:['REQ-2'],status:'open'},
        {id:'Q-answer',related_refs:[],status:'open'},
        {id:'Q-affected',affected_requirements:[{id:'REQ-1'}],status:'open'},
        {id:'Q-retired',related_refs:['REQ-1'],superseded_by:['Q-1'],status:'open'},
        {id:'Q-deferred',related_refs:['REQ-1'],status:'deferred'}]};
      const before=JSON.stringify(p);
      assert.deepEqual(questionsForItem(p,'REQ-1').map(q=>q.id),['Q-1','Q-sibling','Q-affected']);
      assert.deepEqual(questionsForItem(p,p.items[2]).map(q=>q.id),['Q-1','Q-sibling','Q-answer','Q-affected']);
      assert.deepEqual(questionsForItem(p,'missing'),[]);
      assert.equal(JSON.stringify(p),before,'choosing context must not edit questions or business state');
    ''')
