"""Display changes cannot merge different clauses, decide unknowns or mutate evidence."""
import asyncio
import copy
import io
import json

from docx import Document

from app.document_reader import reader_document, export_readiness
from app.exports import document_files
from tests.test_detail_document import project_fixture, compile_fixture


def test_compact_acceptance_retains_each_exact_clause_and_source_mapping(tmp_path):
    p=project_fixture(tmp_path)
    original=next(i for i in p['items'] if i['kind']=='acceptance')
    original['title']='验收：同一功能'
    second=copy.deepcopy(original)
    second.update(id='AC-REVISED-LONG-IDENTITY',statement=original['statement']+'；其余站点继续处理。')
    p['items'].append(second)
    p['items'][0]['detail_contract']['interactions'][0]['acceptance_refs']=[original['id'],second['id']]
    # Only fixture setup; this test does not assert human acceptance.
    compile_fixture(p)
    before=copy.deepcopy(p)
    view=reader_document(p['documents']['prd'],include_locations=True)
    tables=[n for s in view['sections'] for n in s['reading_nodes'] if n.get('table_role')=='acceptance']
    rows=[r for t in tables for r in t['rows']]
    assert [r[1] for r in rows]==[i['statement'] for i in (original,second)]
    assert tables[0]['title']=='验收清单 · '+original['title']
    assert original['title'] not in str(rows)
    aliases={v['item_id']:v['label'] for v in view['reference_labels']}
    assert aliases[original['id']]!=aliases[second['id']]
    nodes=[n for s in view['sections'] for n in s['reading_nodes']]
    assert not any(n['kind']=='heading' and n.get('item_kind')=='acceptance' for n in nodes)
    assert not any(second['id'] in str(n.get('rows','')) for n in nodes)
    assert any(second['id'] in b['text'] and b.get('source_location')
               for s in view['sections'] for b in s['blocks'] if b['kind']=='detail')
    assert p==before

    distinct=copy.deepcopy(p['documents']['prd'])
    changed=next(i for i in distinct['item_snapshot'] if i['id']==second['id'])
    changed['title']='核实失败后允许主动重试'
    varied=reader_document(distinct)
    varied_tables=[n for s in varied['sections'] for n in s['reading_nodes'] if n.get('table_role')=='acceptance']
    assert varied_tables[0]['title']=='验收清单'
    assert [r[1] for r in varied_tables[0]['rows']]==[
        original['title']+'\n'+original['statement'],changed['title']+'\n'+changed['statement']]


def test_unknowns_and_review_gates_survive_friendly_export(tmp_path):
    p=project_fixture(tmp_path,confirmed=False)
    req=p['items'][0]
    req['detail_contract']['fields'][0]['constraints']='待确认：字符上限'
    compile_fixture(p)
    before=copy.deepcopy(p)
    assert not export_readiness(p,'prd')['ready']
    files=asyncio.run(document_files(p,'prd',mode='review'))
    md=files['PRD.md'].decode()
    doc=Document(io.BytesIO(files['PRD.docx']))
    assert '待确认：字符上限' in md and '详细需求未确认' in md
    assert md.index('评审核对清单\n')>md.index('状态变化')
    assert not any(x.text.startswith('待核对：') for x in doc.paragraphs[:8])
    mapping=json.loads(files['PRD.references.json'])
    assert mapping['document_id']==p['documents']['prd']['id']
    assert len({v['label'] for v in mapping['references']})==len(mapping['references'])
    assert not export_readiness(p,'prd')['ready'] and p==before


def test_basic_prd_compacts_only_repeated_structure_preserving_exact_rules(tmp_path):
    from tests.test_prd_v2_semantics import checked_project
    from app.prd import saved_prd_plan, compile_plan
    from app.workflow import apply_response
    p=checked_project(tmp_path)
    req=p['items'][0]
    req.update(title='复制告警编号',statement='用户可复制当前告警编号。',behavior=dict(
        actor='告警查看者',entry='点击当前告警的复制按钮。',
        flow='发起复制后进入处理中，仅禁用本按钮；收到结果后恢复本按钮。',
        result='成功提示“已复制”；失败提示“复制失败，请重试”。',
        exceptions='失败后由用户主动重试，不自动重试。',
        data='按原文复制告警编号；不写告警，不新增记录。',
        permissions='沿用现有权限，不新增权限或接口。'))
    acceptance=next(i for i in p['items'] if i['kind']=='acceptance')
    acceptance.update(title='复制验收',statement=req['behavior']['result'])
    second=copy.deepcopy(acceptance)
    second.update(id='AC-COPY-LONG-IDENTITY',statement=req['behavior']['flow'])
    p['items'].append(second)
    from app.product_flow import checkpoint, status
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    apply_response(p,compile_plan(saved_prd_plan(p),p,'prd',include_sketch=False))
    p['documents']['prd']['assembly_version']='prd-plan-4'
    artifact=p['documents']['prd'];before=copy.deepcopy(artifact)
    view=reader_document(artifact,include_locations=True)
    titles=[s['title'] for s in view['sections']]
    assert '需求概述' in titles and '主要功能清单' not in titles
    assert '交互逻辑' not in titles and '业务流程说明' not in titles
    nodes=[n for s in view['sections'] for n in s['reading_nodes']]
    behavior=next(n for n in nodes if n.get('table_role')=='behavior')
    assert set(r[1] for r in behavior['rows'])==set(req['behavior'].values())
    rows=[r for n in nodes if n.get('table_role')=='acceptance' for r in n['rows']]
    assert [r[1] for r in rows]==[i['statement'] for i in (acceptance,second)]
    assert next(n for n in nodes if n.get('table_role')=='acceptance')['title']=='验收清单 · 复制验收'
    assert all(len(r)==2 for r in rows)
    assert any(b.get('source_location') for s in view['sections'] if s['title']=='需求概述' for b in s['blocks'])
    assert p['questions'][0]['question'] in str(view)
    local=copy.deepcopy(artifact);local['assembly_version']='prd-plan-4-local'
    assert reader_document(local,include_locations=True)==view
    files=asyncio.run(document_files(p,'prd',mode='review'))
    md=files['PRD.md'].decode();word=Document(io.BytesIO(files['PRD.docx']))
    word_text='\n'.join([x.text for x in word.paragraphs]+[c.text for t in word.tables for r in t.rows for c in r.cells])
    for value in [req['statement'],*req['behavior'].values(),acceptance['statement'],second['statement']]:
        assert value in md and value in word_text
    assert artifact==before


def test_multiple_features_keep_context_and_complete_definitions(tmp_path):
    p=project_fixture(tmp_path)
    other=copy.deepcopy(p['items'][0]);other.update(id='REQ-SECOND',title='另一电站联系人')
    p['items'].append(other)
    compile_fixture(p)
    p['documents']['prd']['assembly_version']='prd-plan-4'
    artifact=p['documents']['prd'];before=copy.deepcopy(artifact)
    view=reader_document(artifact,include_locations=True)
    nodes=[n for s in view['sections'] for n in s['reading_nodes']]
    inventory=next(n for n in nodes if n.get('table_role')=='function_inventory')
    assert inventory['item_ids']==['REQ-0001','REQ-SECOND']
    assert inventory['rows']==[['R01',p['items'][0]['title']],['R02',other['title']]]
    for ref in inventory['item_ids']:
        assert any(n.get('item_id')==ref and n['kind']=='diagram' for n in nodes)
        assert any(b['kind']=='detail' and b['ref_ids']==[ref] and '1 至 30 个字符' in b['text']
                   and b.get('source_location') for s in view['sections'] for b in s['blocks'])
    assert artifact==before
