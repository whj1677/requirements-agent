"""Unified editing routes and explicit stage checks retain business gates."""
from tests.test_scope_frontend import run_ts


def test_refinement_routes_never_fall_back_to_old_context_or_stage_editors():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refine-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {refinementDestination}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={items:[{id:'R',kind:'requirement',applies_to:'to_be'},{id:'A',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',related_refs:['R']}],questions:[{id:'Q'}],product_context:{scope_ids:['R']}};
      assert.deepEqual(refinementDestination(p,{kind:'context',field:'users'}),{step:'refine',field:'users',itemId:undefined,questionId:undefined,scope:false});
      for(const step of [2,3])assert.deepEqual(refinementDestination(p,{kind:'stage',step}),{step:'refine',review:true});
      assert.equal(refinementDestination(p,{kind:'stage',step:1})?.step,'information');
      assert.equal(refinementDestination(p,{kind:'acceptance',item_id:'R'})?.itemId,'A');
      assert.equal(refinementDestination(p,{kind:'document',document:'prd'}),null);
      assert.throws(()=>refinementDestination(p,{kind:'context',field:'invented'}));
    ''')


def test_confirmation_checks_saved_snapshots_and_stops_on_gaps_or_concurrent_edits():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refine-readiness.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {confirmRefinement,refinementBlockers}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const make=()=>({revision:7,items:[{id:'candidate',selection_status:'candidate'}],product_context_proposal:{priority:'AI only'},product_flow:[0,1,2].map(n=>({complete:n===0,available:n<=1,missing:[],blockers:[],content_hash:'h'+n}))});
      let p=make(),calls=[];
      const check=async(step,revision,hash)=>{calls.push([step,revision,hash]);assert.equal(revision,p.revision);p.product_flow[step-1].complete=true;if(step<3)p.product_flow[step].available=true;p.revision++;return structuredClone(p.product_flow);};
      await confirmRefinement(structuredClone(p),async()=>structuredClone(p),check);
      assert.deepEqual(calls,[[2,7,'h1'],[3,8,'h2']]);
      assert.equal(p.items[0].selection_status,'candidate');assert.equal(p.product_context_proposal.priority,'AI only');
      p=make();calls=[];p.product_flow[1].blockers=[{key:'gap',message:'unknown'}];
      await assert.rejects(()=>confirmRefinement(p,async()=>p,check));assert.equal(calls.length,0);
      p=make();calls=[];const snapshot=structuredClone(p);p.revision++;
      await assert.rejects(()=>confirmRefinement(snapshot,async()=>p,check),/内容已变化/);assert.equal(calls.length,0);
      p=make();calls=[];
      await assert.rejects(()=>confirmRefinement(structuredClone(p),async()=>structuredClone(p),async(s,r,h)=>{const result=await check(s,r,h);p.product_flow[2].content_hash='changed';return result;}),/内容已变化/);
      assert.equal(calls.length,1);assert.equal(p.product_flow[2].complete,false);
    ''')
