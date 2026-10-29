"""Complete single-answer values with exactly one missing outer brace."""
import asyncio,json
import pytest
from app.model_json import question_root_close
from app.provider import Provider,DEFAULT,origin
from app.actions import Actions
from app.workflow import Workflow
from tests.test_requirement_recommendation import project
from tests.test_deepseek_responses import completed,mock_transport

def packet():
    return dict(summary='',summary_fields={},requirements=[],limitations=['合成建议，待确认。'],
        completion={'answers':{'Q1':dict(text='建议列表仅展示确认标识，详情展示确认人、时间和备注。',basis='fact',sources=[dict(source='S1',locator='L1')])}})

def raw(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def test_single_answer_only_gets_its_missing_outer_close():
    value=packet();value['completion']['answers']['Q1']['text']+='原文保留括号 } ] 和引号 "。'
    damaged=raw(value)[:-1]+'\n'
    result,proof=question_root_close(damaged)
    assert result==value and proof['insertions']==[dict(offset=len(damaged),inserted='}')]
    assert json.loads(damaged+'}')==value

@pytest.mark.parametrize('kind',['valid','inner','text','comma','duplicate','extra','multiple_answers','rows','nonfinite'])
def test_other_json_errors_or_unscoped_packets_are_rejected(kind):
    value=packet();damaged=raw(value)[:-1]
    if kind=='valid':damaged=raw(value)
    elif kind=='inner':damaged=damaged[:-1]
    elif kind=='text':damaged=damaged[:damaged.index('建议列表')+3]
    elif kind=='comma':damaged=damaged.replace(',"summary_fields"','"summary_fields"')
    elif kind=='duplicate':damaged=damaged.replace('"summary":""','"summary":"","summary":""')
    elif kind=='extra':damaged+=',"approved":true'
    elif kind=='multiple_answers':value['completion']['answers']['Q2']=value['completion']['answers']['Q1'];damaged=raw(value)[:-1]
    elif kind=='rows':value['requirements']=[{}];damaged=raw(value)[:-1]
    elif kind=='nonfinite':damaged=damaged.replace('"summary":""','"summary":1e9999')
    assert question_root_close(damaged) is None

@pytest.mark.parametrize('kind',['normal','bad_source','limited','incomplete'])
def test_actual_transport_and_task_fill_only_valid_drafts_without_another_request(tmp_path,monkeypatch,kind):
    store,before=project(tmp_path);value=packet()
    if kind=='bad_source':value['completion']['answers']['Q1']['sources'][0]['source']='S404'
    damaged=raw(value)[:-1];reply=completed(damaged)
    if kind=='incomplete':reply.update(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
    requests=mock_transport(monkeypatch,reply)
    provider=Provider(tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
    if kind=='limited':monkeypatch.setattr('app.model_evidence.MAX_EVIDENCE_CHARS',10)
    actions=Actions(store,Workflow(store,provider));qid=before['questions'][0]['id']
    async def execute():
        body=dict(expected_revision=before['revision'],action='organize',message='',document_type='prd',draft_request={'mode':'question','question_id':qid})
        plan=actions.plan(before['id'],body);task=actions.start(before['id'],body,plan['plan_hash'],'question-tail-proof-'+kind,True)
        await actions.jobs[task['id']]
        return store.get_record(before['id'],task['id'],'user_task')
    task=asyncio.run(execute());after=store.get(before['id'])
    assert len(requests)==1 and task['calls']==1
    assert after['items']==before['items'] and after['questions']==before['questions'] and after['documents']==before['documents']
    call=json.loads(next((tmp_path/'evidence/model-calls').glob('*.json')).read_text('utf-8'))
    if kind in ('normal','bad_source'):
        assert call['final_output']==damaged and call['syntax_normalization']['rule']=='question-missing-root-close-1'
        if kind=='normal':assert after['refinement_completion']['answers'][qid]['text']==value['completion']['answers']['Q1']['text']
        else:assert not after.get('refinement_completion',{}).get('answers')
    else:
        assert task['status']=='failed' and 'syntax_normalization' not in call
