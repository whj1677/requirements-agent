"""Five-step product path; historical sketches remain immutable compatibility data."""
import asyncio
import copy
import io
import json
from unittest.mock import patch
from docx import Document
from app.contracts import gate, validate_document, review_target
from app.exports import document_files
from app.prd import compile_plan, context
from app.product_flow import status, execution_issues
from tests.helpers import prepared
from tests.product_flow_helpers import check_prepared
from tests.test_ui_workflow_contract import client


def test_documents_and_confirmation_do_not_require_sketch_or_fabricate_approval(tmp_path):
    c, app=client(tmp_path);p=prepared(app.state.store)
    p=check_prepared(app.state.store,p['id'],through=3)
    with app.state.store.edit(p['id'],p['revision'],'isolated no-sketch fixture',bump=False) as (draft,_):
        draft['ui']=None;draft.pop('sketch_review',None)
        draft['review']['target_hash']=review_target(draft)
    p=app.state.store.get(p['id']);root='/api/projects/'+p['id']
    assert [s['step'] for s in status(p) if not s['retired']]==[1,2,3,5,6]
    assert [s['display_step'] for s in status(p) if not s['retired']]==[1,2,3,4,5]
    assert not execution_issues(p,'prd') and '4' not in p['stage_checks']
    assert gate(p)  # document review is still mandatory
    for kind in ('prd','mrd'):
        current=c.get(root).json()
        assert c.post(root+'/documents/'+kind+'/review',json=dict(expected_revision=current['revision'],document_id=current['documents'][kind]['id'])).status_code==200
    current=c.get(root).json()
    assert c.post(root+'/stage-checks/5',json=dict(expected_revision=current['revision'],expected_hash=current['product_flow'][4]['content_hash'])).status_code==200
    current=c.get(root).json()
    assert not current['confirmation_issues'] and not current['active_baseline_id']
    assert '4' not in current['stage_checks'] and current['ui'] is None
    response=c.post(root+'/confirmations',json=dict(expected_revision=current['revision'],expected_hashes=current['hashes'],scope_ids=[i['id'] for i in current['items']],idempotency_key='synthetic-no-sketch'))
    assert response.status_code==200
    baseline=app.state.store.get_record(p['id'],response.json()['baseline_id'],'baseline')
    assert baseline['project']['ui'] is None and '4' not in baseline['project']['stage_checks']


def test_legacy_ui_does_not_block_new_documents_and_stays_unchanged(tmp_path):
    c,app=client(tmp_path);p=prepared(app.state.store)
    p=check_prepared(app.state.store,p['id'],through=3)
    p['ui']['brief_hash']='historical-brief'
    before=copy.deepcopy(p)
    assert status(p)[4]['available'] and not execution_issues(p,'prd')
    assert not any('线框对应旧底稿' in x for x in gate(p))
    assert p==before
    p['questions'][0].update(blocking=True)
    assert execution_issues(p,'prd') and p['questions'][0]['question'] in gate(p)


def test_retired_generation_stops_before_any_run(tmp_path):
    c,app=client(tmp_path);p=prepared(app.state.store)
    p=check_prepared(app.state.store,p['id'],through=3);root='/api/projects/'+p['id']
    r=c.post(root+'/runs',json=dict(expected_revision=p['revision'],stage='ui'))
    assert r.status_code==409 and '草图功能已取消' in r.json()['message']
    r=c.post(root+'/actions/plan',json=dict(expected_revision=p['revision'],action='prototype',message='',document_type='prd',max_calls=1))
    assert r.status_code==200 and any('草图功能已取消' in x for x in r.json()['missing'])
    assert not app.state.store.records(p['id'],'run')


def test_new_document_assembly_and_exports_exclude_old_sketch_without_rewriting_it(tmp_path):
    c,app=client(tmp_path);p=prepared(app.state.store);before=copy.deepcopy(p)
    ctx=context(p,include_sketch=False)
    assert ctx['D_unknowns_limits']['active_ui'] is None and not ctx['sketch_check']
    assert ctx['D_unknowns_limits']['questions']==p['questions']
    plan=dict(plan_version='2',sections=[dict(section_key='function',context_refs=[],normative_refs=[i['id'] for i in p['items']],discussion_refs=[])])
    value=compile_plan(plan,p,'prd',include_sketch=False)
    validate_document(value['result'],p,'prd')
    picture=next(m for m in value['result']['reference_mapping'] if m['profile_section_id']=='PRD-4.F.1')
    assert picture['disposition']=='not_applicable' and picture['output_section_ids']==[]
    assert p==before
    p['documents']['prd'].update(content=value['result'],sketch_policy='excluded')
    with patch('app.exports.capture',side_effect=AssertionError('retired sketch must not render')):
        files=asyncio.run(document_files(p,'prd'))
    assert json.loads(files['document_asset_bindings.json'])==[]
    assert not any(k.startswith('assets/') for k in files)
    md=files['PRD.md'].decode();word='\n'.join(x.text for x in Document(io.BytesIO(files['PRD.docx'])).paragraphs)
    for i in p['items']:assert i['statement'] in md and i['statement'] in word
    assert p['questions'][0]['question'] in md and p['questions'][0]['question'] in word
