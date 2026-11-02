import copy
import pytest
from app.prd import compile_plan,plan_contract,DISCUSSION_APPENDIX_TITLE
from app.contracts import validate_response
from app.response_application import apply_response
from app.document_reader import reader_document
from tests.test_detail_document import project_fixture


@pytest.mark.parametrize('count',[1,100,101,118,200])
def test_internal_history_chunks_preserve_identity_order_and_reader_boundary(tmp_path,count):
    p=project_fixture(tmp_path);root=p['items'][0];ids=[]
    for n in range(count):
        old=copy.deepcopy(root);old.update(id='REQ-HISTORY-'+str(n),selection_status='deferred',statement='历史专有正文标记-'+str(n),action='add',target_item_id=None)
        p['items'].append(old);ids.append(old['id'])
    before=copy.deepcopy(p)
    plan=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],normative_refs=plan_contract(p,'prd')['normative_item_ids'],discussion_refs=[],explanations=[])])
    value=compile_plan(plan,p,'prd',include_sketch=False)
    validate_response(value,'prd',p,[e for s in p['sources'] for e in s['excerpts']])
    assert p==before
    chunks=[s for s in value['result']['sections'] if s['title']==DISCUSSION_APPENDIX_TITLE]
    assert all(len(s['blocks'])<=100 for s in chunks)
    assert len({s['section_id'] for s in chunks})==len(chunks)
    actual=[b['ref_ids'][0] for s in chunks for b in s['blocks']]
    assert [i for i in actual if i in ids]==ids
    for section in chunks:
        for block in section['blocks']:
            if block['ref_ids'][0] in ids:
                item=next(i for i in p['items'] if i['id']==block['ref_ids'][0])
                assert item['statement'] in block['text'] and item['epistemic_status'] in block['text']
    assert compile_plan(plan,p,'prd',include_sketch=False)==value
    apply_response(p,value);reader=reader_document(p['documents']['prd'])
    assert DISCUSSION_APPENDIX_TITLE not in str(reader)
    assert '历史专有正文标记-' not in str(reader)
    assert root['statement'] in str(reader)
