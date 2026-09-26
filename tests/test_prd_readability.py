import copy
import pytest
from app.core import Problem, dumps
from app.prd import context, compile_plan, plan_contract
from tests.test_prd01 import state, plan


def test_historical_limits_are_not_current_facts(tmp_path):
    _,p=state(tmp_path)
    p['messages'].append(dict(stage='ingest',created='2026-01-01',response={'limitations':['历史仅一份材料。','日志留存期尚未知。']}))
    ctx=context(p)
    assert 'recorded_limitations' not in ctx['D_unknowns_limits']
    assert 'historical_notes' not in ctx
    assert '历史仅一份材料' not in dumps(ctx)
    r=compile_plan(plan(['REQ-0001']),p,'prd')
    current=next(s for s in r['result']['sections'] if s['title']=='文档边界与资料限制')
    assert '历史仅一份材料' not in dumps(current)
    history=next(s for s in r['result']['sections'] if s['title']=='历史分析提示（非当前事实）')
    assert '日志留存期尚未知' in dumps(history) and '2026-01-01' in dumps(history)


def test_applied_revision_is_audited_but_not_reintroduced_as_discussion(tmp_path):
    _,p=state(tmp_path)
    target=p['items'][0]
    applied=copy.deepcopy(target)
    applied.update(id='REQ-APPLIED',action='revise',target_item_id=target['id'],selection_status='deferred')
    same_candidate=copy.deepcopy(target)
    same_candidate.update(id='REQ-SAME-CANDIDATE',action='revise',target_item_id=target['id'],selection_status='candidate')
    pending=copy.deepcopy(target)
    pending.update(id='REQ-PENDING',action='revise',target_item_id=target['id'],selection_status='candidate',
                   statement='待核对的不同修订内容。')
    rejected=copy.deepcopy(target)
    rejected.update(id='REQ-REJECTED',action='revise',target_item_id=target['id'],selection_status='rejected',
                    epistemic_status='inferred',statement='已拒绝的推断版本。')
    deferred_other=copy.deepcopy(target)
    deferred_other.update(id='REQ-DEFERRED-OTHER',action='revise',target_item_id=target['id'],selection_status='deferred',
                          statement='暂缓的不同修订内容。')
    p['items'] += [applied,same_candidate,pending,rejected,deferred_other]
    before=copy.deepcopy(p)
    ctx=context(p);contract=plan_contract(p)
    visible={i['id'] for i in ctx['C_discussion']['items']}
    assert 'REQ-APPLIED' not in visible and 'REQ-APPLIED' not in contract['discussion_item_ids']
    assert {'REQ-SAME-CANDIDATE','REQ-PENDING','REQ-REJECTED','REQ-DEFERRED-OTHER'}<=visible
    assert {'REQ-SAME-CANDIDATE','REQ-PENDING','REQ-REJECTED','REQ-DEFERRED-OTHER'}<=set(contract['discussion_item_ids'])
    with pytest.raises(Problem) as e:
        compile_plan(plan(['REQ-0001'],['REQ-APPLIED']),p,'prd')
    assert e.value.code=='SEMANTIC_BLOCKED' and 'REQ-0001' in e.value.message
    result=compile_plan(plan(['REQ-0001'],['REQ-SAME-CANDIDATE','REQ-PENDING','REQ-REJECTED','REQ-DEFERRED-OTHER']),p,'prd')
    text=dumps(result)
    assert all(x in text for x in ('待核对的不同修订内容。','已拒绝的推断版本。','暂缓的不同修订内容。'))
    assert 'REQ-APPLIED' not in text and p==before


def test_repeated_items_have_one_full_statement(tmp_path):
    _,p=state(tmp_path)
    value=plan(['REQ-0001'],['REQ-CANDIDATE'])
    value['sections'].append(dict(value['sections'][0],section_key='rules'))
    before=copy.deepcopy(p)
    r=compile_plan(value,p,'prd')
    assert p==before
    blocks=[b for s in r['result']['sections'] for b in s['blocks']]
    assert sum(b['kind']=='requirement' and b['ref_ids']==['REQ-0001'] for b in blocks)==1
    assert sum(p['items'][-1]['statement'] in (b['text'] or '') for b in blocks)==1
    assert any('参见' in (b['text'] or '') for b in blocks)


def test_count_error_does_not_echo_entire_response(tmp_path):
    _,p=state(tmp_path);value=plan(['REQ-0001']);value['sections']*=13
    value['sections'][0]['narration']='PRIVATE-LONG-BODY'
    with pytest.raises(Problem) as e:compile_plan(value,p,'prd')
    assert 'sections' in e.value.message and '12' in e.value.message and '13' in e.value.message
    assert 'PRIVATE-LONG-BODY' not in e.value.message


def test_existing_platform_is_context_not_new_scope(tmp_path):
    from app.prd import plan_contract
    _,p=state(tmp_path)
    p['items'][0].update(applies_to='as_is', selection_status='selected',
        statement='现有平台的工单列表已支持查看；本次不重建列表。')
    old=copy.deepcopy(p)
    assert p['items'][0]['id'] not in plan_contract(p)['normative_item_ids']
    with pytest.raises(Problem) as e:
        compile_plan(plan([p['items'][0]['id']]),p,'prd')
    assert e.value.code=='SEMANTIC_BLOCKED'
    result=compile_plan(plan(discussion=[p['items'][0]['id']]),p,'prd')
    assert p==old
    assert p['items'][0]['statement'] in dumps(result)
    assert 'as_is' in dumps(result) and p['items'][0]['id'] not in {c['item_id'] for c in result['result']['coverage']}


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
