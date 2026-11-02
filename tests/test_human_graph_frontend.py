"""Human-readable deterministic graph feedback before requirement confirmation."""
from tests.test_scope_frontend import run_ts

LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const result=await build({entryPoints:['src/pm-prototype/detail-contract.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {emptyDetailContract,detailEditingIssues}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
const graph=()=>{const d=emptyDetailContract();for(const k of Object.keys(d.applicability))d.applicability[k]={applicable:false,reason:'本次只核对流程'};d.applicability.flow={applicable:true,reason:''};d.flow={nodes:[{id:'S1',label:'开始认领',kind:'start'},{id:'A1',label:'核对负责人',kind:'action',actor:'值班人员'},{id:'E1',label:'认领完成',kind:'end'}],edges:[{id:'E-a',from:'S1',to:'A1'},{id:'E-b',from:'A1',to:'E1'}]};return d;};
'''


def test_graph_errors_use_business_labels_without_rewriting_business_paths():
    run_ts(LOAD + r'''
const d=graph();assert.deepEqual(detailEditingIssues(d),[]);
d.flow.edges.push({id:'E-back',from:'E1',to:'S1',condition:'重新处理'});
const before=JSON.stringify(d),issues=detailEditingIssues(d);
assert(issues.some(i=>i.path==='detail_contract.flow.nodes.S1'&&i.message.includes('开始认领')&&i.message.includes('入边')));
assert(issues.some(i=>i.path==='detail_contract.flow.nodes.E1'&&i.message.includes('认领完成')&&i.message.includes('不能继续连出')));
assert.equal(JSON.stringify(d),before);
''')


def test_cycles_have_bounded_feedback_and_inapplicable_flow_is_not_required():
    run_ts(LOAD + r'''
const d=graph();d.flow.edges=[{id:'E-a',from:'S1',to:'A1'},{id:'E-loop',from:'A1',to:'A1',condition:'继续检查'}];
const issues=detailEditingIssues(d);
assert(issues.some(i=>i.message.includes('核对负责人')&&i.message.includes('无法到达结束或等待')));
assert(issues.some(i=>i.message.includes('认领完成')&&i.message.includes('无法从开始')));
d.applicability.flow={applicable:false,reason:'本需求没有流程'};d.flow={nodes:[],edges:[]};assert.deepEqual(detailEditingIssues(d),[]);
''')


def test_distinct_branch_errors_remain_visible_together():
    run_ts(LOAD + r'''
const d=graph();d.flow.nodes[1].kind='decision';d.flow.edges[1].condition='';
const issues=detailEditingIssues(d).filter(i=>i.path==='detail_contract.flow.nodes.A1');
assert(issues.some(i=>i.message.includes('至少需要两条条件分支')));
assert(issues.some(i=>i.message.includes('分支条件不能为空')));
assert.equal(new Set(issues.map(i=>i.path+'|'+i.message)).size,issues.length);
''')
