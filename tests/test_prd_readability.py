import copy
import pytest
from app.core import Problem, dumps
from app.prd import context, compile_plan
from tests.test_prd01 import state, plan


def test_historical_limits_are_not_current_facts(tmp_path):
    _,p=state(tmp_path)
    p['messages'].append(dict(stage='ingest',created='2026-01-01',response={'limitations':['历史仅一份材料。','日志留存期尚未知。']}))
    ctx=context(p)
    assert 'recorded_limitations' not in ctx['D_unknowns_limits']
    assert ctx['historical_notes'][-1]['limitations']==['历史仅一份材料。','日志留存期尚未知。']
    r=compile_plan(plan(),p,'prd')
    current=next(s for s in r['result']['sections'] if s['title']=='限制及参考维度待核对')
    assert '历史仅一份材料' not in dumps(current)
    history=next(s for s in r['result']['sections'] if s['title']=='历史分析提示（非当前事实）')
    assert '日志留存期尚未知' in dumps(history) and '2026-01-01' in dumps(history)


def test_repeated_items_have_one_full_statement(tmp_path):
    _,p=state(tmp_path)
    value=plan(['REQ-0001'],['REQ-CANDIDATE'])
    value['sections'].append(dict(value['sections'][0],title='关联功能'))
    before=copy.deepcopy(p)
    r=compile_plan(value,p,'prd')
    assert p==before
    blocks=[b for s in r['result']['sections'] for b in s['blocks']]
    assert sum(b['kind']=='requirement' and b['ref_ids']==['REQ-0001'] for b in blocks)==1
    assert sum(p['items'][-1]['statement'] in (b['text'] or '') for b in blocks)==1
    assert any('参见' in (b['text'] or '') for b in blocks)


def test_count_error_does_not_echo_entire_response(tmp_path):
    _,p=state(tmp_path);value=plan();value['sections']*=13
    value['sections'][0]['narration']='PRIVATE-LONG-BODY'
    with pytest.raises(Problem) as e:compile_plan(value,p,'prd')
    assert 'sections' in e.value.message and '12' in e.value.message and '13' in e.value.message
    assert 'PRIVATE-LONG-BODY' not in e.value.message


def test_current_ui_context_and_discussion_image_without_adoption(tmp_path):
    import asyncio,json
    from app.core import brief_hash
    from app.contracts import validate_response,gate
    from app.workflow import apply_response
    from app.exports import document_files
    from tests.test_ui01_preview import contacts_spec
    _,p=state(tmp_path)
    for i in p['items']:i['selection_status']='candidate'
    spec=json.loads(json.dumps(contacts_spec()).replace('REQ-1','REQ-0001'))
    spec['draft_revision']=p['revision']
    p['ui']=dict(spec=spec,brief_hash=brief_hash(p))
    assert context(p)['D_unknowns_limits']['active_ui']['pages'][0]['components'][4]['type']=='drawer'
    value=compile_plan(plan(discussion=['REQ-0001']),p,'prd')
    validate_response(value,'prd',p,[])
    assert value['result']['coverage']==[]
    binding=next(m for m in value['result']['reference_mapping'] if m['disposition']=='merged')
    assert '不表示已采纳' in binding['reason']
    before=copy.deepcopy(p['items'])
    apply_response(p,value)
    files=asyncio.run(document_files(p,'prd'))
    assert len(json.loads(files['document_asset_bindings.json']))==1
    assert '低保真模拟' in files['PRD.md'].decode()
    from docx import Document
    from docx.shared import Inches
    import io
    word=Document(io.BytesIO(files['PRD.docx']))
    assert all(s.height<=Inches(8.21) for s in word.inline_shapes)
    assert p['items']==before and gate(p)
