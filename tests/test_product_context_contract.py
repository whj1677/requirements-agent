import copy
from app.provider import request_schema
from app.workflow import apply_response
from app.product_flow import INTAKE, SCOPE, status
from app.store import Store
from tests.test_runtime import empty_ingest, execute_run


def test_current_intake_requires_suggestions_and_repair_does_not_approve(tmp_path):
    missing=empty_ingest();del missing['product_context_proposal']
    valid=empty_ingest()
    valid['product_context_proposal']={k:'预置输入 '+v for k,v in {**INTAKE,**SCOPE}.items()}
    run,p,provider=execute_run(tmp_path,[missing,valid],max_calls=2)
    assert run['calls']==2 and run['attempts'][0]['error']=='SCHEMA_INVALID'
    assert run['attempts'][1].get('error') is None
    assert p['product_context_proposal']==valid['product_context_proposal']
    assert 'product_context' not in p and not any(s['complete'] for s in status(p))
    schema=request_schema('ingest')
    assert 'product_context_proposal' in schema['required']
    assert set(schema['properties']['product_context_proposal']['required'])==INTAKE.keys()|SCOPE.keys()


def test_new_suggestions_never_replace_saved_human_scope(tmp_path):
    p=Store(tmp_path).create('合成');p['product_context']={'preserve_scope':'保留原查询'}
    response=empty_ingest();response['product_context_proposal']['preserve_scope']='模型另一个建议'
    apply_response(p,response)
    assert p['product_context']['preserve_scope']=='保留原查询'


def test_clarification_receives_current_gaps_and_deferred_question_boundary(tmp_path):
    import json
    from app.provider import assemble, DEFAULT
    p=Store(tmp_path).create('联系人增量')
    p['questions']=[dict(id='Q-existing',question='并发技术实现？',status='open',blocking=True,
        out_of_scope_reason='业务结果已明确，实现留给技术设计',related_refs=[],answer=None)]
    p['messages']=[dict(role='assistant',stage='ingest',text='旧观察',response={'questions':['OLD-STRUCTURE']})]
    before=copy.deepcopy(p)
    messages,_,_=assemble(p,'clarify','',DEFAULT,tmp_path)
    ctx=json.loads(messages[1]['content'][0]['text'])
    assert ctx['stage_focus']['content_gaps']
    assert not any('并发技术实现' in gap for gap in ctx['stage_focus']['content_gaps'])
    assert ctx['stage_focus']['deferred_questions']==[dict(id='Q-existing',reason=p['questions'][0]['out_of_scope_reason'])]
    assert ctx['questions']==p['questions'] and 'response' not in ctx['recent_messages'][0]
    assert p==before


def test_ui_missing_page_array_close_is_diagnosed_without_rewriting_output():
    import json
    import pytest
    from app.provider import delimiter_hint
    # Same nested failure shape as the live sketch; synthetic strings only.
    bad='{"result":{"spec":{"pages":[{"regions":[{"components":[{"label":"a } b"}]}]}}}}'
    with pytest.raises(json.JSONDecodeError):json.loads(bad)
    hint=delimiter_hint(bad)
    assert '应先用 ]' in hint and '实际却为 }' in hint
    with pytest.raises(json.JSONDecodeError):json.loads(bad)
    assert delimiter_hint(json.dumps({'label':'quoted \\" ] text','pages':[]}))==''


def test_identical_revision_already_in_body_is_not_appended_as_pending(tmp_path):
    from app.prd import compile_plan
    from tests.helpers import prepared
    p=prepared(Store(tmp_path))
    target=p['items'][0]
    candidate=dict(copy.deepcopy(target),id='REQ-revision-copy',target_item_id=target['id'],selection_status='deferred')
    p['items'].append(candidate);before=copy.deepcopy(p)
    plan=dict(plan_version='2',sections=[dict(section_key='function',context_refs=[],normative_refs=[target['id']],discussion_refs=[])])
    doc=compile_plan(plan,p,'prd')['result']
    assert not any(candidate['id'] in b['ref_ids'] for s in doc['sections'] for b in s['blocks'])
    assert p==before
    candidate['statement']='另一条未采纳规则'
    doc=compile_plan(plan,p,'prd')['result']
    assert any(candidate['id'] in b['ref_ids'] for s in doc['sections'] for b in s['blocks'])
