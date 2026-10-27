"""Bounded punctuation recovery; real parser/workflow, synthetic HTTP only."""
import asyncio
import json

import pytest

from app.model_json import business_containers
from app.provider import DEFAULT, Provider, origin
from app.workflow import Workflow
from tests.test_business_draft import draft_row, packet
from tests.test_deepseek_responses import completed, mock_transport
from tests.test_requirement_recommendation import project, target


def text(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def fixture():
    row=dict(row_key='R1',objects=[dict(id='F1',name='原文含 } ] 与转义引号 "，仍原样保留')])
    return dict(summary='合成材料，非业务验收',summary_fields={},requirements=[row],limitations=['未知仍未知'])


@pytest.mark.parametrize('kind',['object','row','both','duplicate'])
def test_only_missing_closers_are_inserted_and_every_original_character_is_retained(kind):
    value=fixture();raw=text(value)
    obj=text(value['requirements'][0]['objects'][0])
    if kind in ('object','both','duplicate'):raw=raw.replace(obj,obj[:-1],1)
    if kind in ('row','both'):raw=raw.replace('}],"limitations":',',"limitations":',1)
    if kind=='duplicate':raw=raw[:-1]+',"summary_fields":{}}'
    derived,proof=business_containers(raw)
    assert derived==value
    reconstructed=raw
    for edit in reversed(proof['insertions']):
        assert set(edit)=={'offset','inserted'} and edit['inserted'] in ('}','}]')
        reconstructed=reconstructed[:edit['offset']]+edit['inserted']+reconstructed[edit['offset']:]
    assert json.loads(reconstructed)==value
    assert proof['identical_duplicate_paths']==([['summary_fields']] if kind=='duplicate' else [])


@pytest.mark.parametrize('kind',['valid','truncated','comma','quote','extra_root','conflict','typed','too_many'])
def test_ambiguous_incomplete_conflicting_or_other_errors_are_not_normalized(kind):
    value=fixture();raw=text(value);obj=text(value['requirements'][0]['objects'][0])
    if kind=='truncated':raw=raw[:-1]
    elif kind=='comma':raw=raw.replace(',"summary_fields"','"summary_fields"',1)
    elif kind=='quote':raw=raw.replace('"row_key":"R1"','"row_key":"R1',1)
    elif kind in ('extra_root','conflict','typed'):
        raw=raw.replace(obj,obj[:-1],1)
        if kind=='extra_root':raw=raw[:-1]+',"approved":true}'
        elif kind=='conflict':raw=raw[:-1]+',"summary_fields":{"intent":"新增原文"}}'
        else:raw=raw.replace('"summary_fields":{}','"summary_fields":{"n":true,"n":1}',1)
    elif kind=='too_many':
        value['requirements'][0]['objects']=[dict(id=f'F{n}') for n in range(3)]
        raw=text(value)
        for row in value['requirements'][0]['objects']:raw=raw.replace(text(row),text(row)[:-1],1)
    assert business_containers(raw) is None


@pytest.mark.parametrize('kind',['normal','bad_source','limited','incomplete'])
def test_workflow_keeps_exact_raw_one_request_and_unchanged_source_gates(tmp_path,monkeypatch,kind):
    store,before=project(tmp_path)
    row=draft_row()
    if kind=='bad_source':row['statement']['sources'][0]['source']='S404'
    raw=text(packet(row))
    ref=text(row['statement']['sources'][0])
    raw=raw.replace(ref,ref[:-1],1)
    reply=completed(raw)
    if kind=='incomplete':reply.update(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
    requests=mock_transport(monkeypatch,reply)
    provider=Provider(tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-test-key'
    workflow=Workflow(store,provider)
    if kind=='limited':
        monkeypatch.setattr('app.model_evidence.MAX_EVIDENCE_CHARS',10)
    async def execute():
        result=workflow.start(before['id'],before['revision'],'clarify','',discussion_target=target())
        await workflow.tasks[result['id']]
        return store.get_record(before['id'],result['id'],'run')
    result=asyncio.run(execute())
    assert len(requests)==result['calls']==1 and result['repair_count']==0
    call=json.loads(next((tmp_path/'evidence/model-calls').glob('*.json')).read_text('utf-8'))
    after=store.get(before['id'])
    assert after['items'][:len(before['items'])]==before['items']
    if kind in ('normal','bad_source'):
        assert call['final_output']==raw
        assert call['syntax_normalization']['insertions']
        if kind=='normal':
            assert result['result_applied'] and any(i['kind']=='acceptance' for i in after['items'][len(before['items']):])
        else:
            assert not after['items'][len(before['items']):]
            assert result['business_draft_review']['rejected'][0]['code']=='BUSINESS_DRAFT_SOURCE'
    else:
        assert not result.get('result_applied') and 'syntax_normalization' not in call
