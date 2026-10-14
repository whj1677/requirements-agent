"""Local assembly defects must not cause another paid model repair."""
import copy

from app.contracts import describe_schema_error, VALIDATOR
from app.core import dumps
from tests.helpers import EXAMPLES
from tests.test_prd01 import prepare_http, precheck_http
from tests.test_ui02_actions import case, plan as action_plan, start, finished


def test_capacity_diagnostic_reports_limit_without_echoing_business_content():
    value=copy.deepcopy(EXAMPLES['prd'])
    block=dict(kind='narrative',ref_ids=[],text='private business body')
    value['result']['sections'][0]['blocks']=[block]*101
    message=describe_schema_error(list(VALIDATOR.iter_errors(value)))
    assert "'blocks'" in message and 'maxItems=100' in message and '实际长度=101' in message
    assert 'private business body' not in message


def test_compiled_document_shape_failure_stops_without_model_repair(case,monkeypatch):
    import app.workflow as workflow
    original=workflow.compile_plan
    def overflow(*args,**kwargs):
        value=original(*args,**kwargs)
        value['result']['sections'][0]['blocks']=[dict(kind='narrative',ref_ids=[],text='合成组装故障')]*101
        return value
    monkeypatch.setattr(workflow,'compile_plan',overflow)
    client,app,model,root=prepare_http(case)
    precheck_http(client,root)
    before=client.get(root).json()
    calls=[]
    def capture(value,body,ctx):
        calls.append(body)
        return value
    model['transform']=capture
    body,preview=action_plan(client,root,'document','',max_calls=3,allow_review_draft=True)
    assert start(client,root,body,preview).status_code==200
    task=finished(client,root)
    assert task['status']=='failed'
    assert task['error']=='DOCUMENT_ASSEMBLY_INVALID'
    assert task['calls']==len(calls)==1
    assert '本地组装' in task['message'] and 'maxItems=100' in task['message']
    after=client.get(root).json()
    for field in ('items','questions','documents','active_baseline_id'):
        assert dumps(after[field])==dumps(before[field])
    run=app.state.store.get_record(root.rsplit('/',1)[-1],task['run_ids'][0])
    assert run['attempts'][0]['validation_result']=='DOCUMENT_ASSEMBLY_INVALID'
