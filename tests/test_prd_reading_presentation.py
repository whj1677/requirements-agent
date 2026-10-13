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
    assert [r[1] for r in rows]==[i['title']+'\n'+i['statement'] for i in (original,second)]
    aliases={v['item_id']:v['label'] for v in view['reference_labels']}
    assert aliases[original['id']]!=aliases[second['id']]
    nodes=[n for s in view['sections'] for n in s['reading_nodes']]
    assert not any(n['kind']=='heading' and n.get('item_kind')=='acceptance' for n in nodes)
    assert not any(second['id'] in str(n.get('rows','')) for n in nodes)
    assert any(second['id'] in b['text'] and b.get('source_location')
               for s in view['sections'] for b in s['blocks'] if b['kind']=='detail')
    assert p==before


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
