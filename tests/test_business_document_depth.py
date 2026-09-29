"""Synthetic v3 document depth and provenance checks; no model or daily data."""
import asyncio
import copy
from types import SimpleNamespace

import jsonschema
import pytest

from app.core import Problem, dumps
from app.prd import PLAN_SCHEMA_V3, compile_plan, plan_contract, verified_business_context
from app.document_reader import reader_document
from app.workflow import apply_response
from app.business_context import build_source, selection_update
from tests.test_business_context_bundle import bundle, raw
from tests.test_prd_v2_semantics import checked_project


def basis(*, context=(), normative=(), claims=()):
    return dict(context_refs=list(context), normative_refs=list(normative),
                business_claim_refs=list(claims))


def v3(kind, evidence, text):
    return dict(plan_version='3',sections=[dict(
        section_key='goal' if kind=='mrd' else 'function',
        context_refs=[],normative_refs=['REQ-0001'],discussion_refs=[],
        explanations=[dict(text=text,evidence_refs=evidence)])])


def claim_source(tmp_path):
    source=build_source(SimpleNamespace(folder=tmp_path),'business-context.json',raw(bundle()))
    source['business_selection']=selection_update(source,['ui'],['c-ui'])
    source['business_active']=True
    return source


def test_v3_contract_and_distinct_document_jobs(tmp_path):
    p=checked_project(tmp_path)
    mrd=plan_contract(p,'mrd');prd=plan_contract(p,'prd')
    jsonschema.Draft202012Validator(PLAN_SCHEMA_V3).validate(mrd['example'])
    assert mrd['plan_version']==prd['plan_version']=='3'
    assert mrd['document_type']=='mrd' and prd['document_type']=='prd'
    assert '价值' in mrd['rules'] and '权限' in prd['rules']
    m=compile_plan(v3('mrd',basis(context=['users']),
        '使用者在当前场景中有核对需求。'),p,'mrd')['result']
    q=compile_plan(v3('prd',basis(normative=['REQ-0001']),
        '管理员在联系人页面执行已选的编辑操作。'),p,'prd')['result']
    assert '使用者在当前场景中有核对需求。' in dumps(m)
    assert '管理员在联系人页面执行已选的编辑操作。' in dumps(q)
    assert m['document_type']=='mrd' and q['document_type']=='prd'


def test_confirmed_claim_readable_and_exportable_with_canonical_basis(tmp_path):
    from app.exports import document_files
    p=checked_project(tmp_path);source=claim_source(tmp_path);p['sources'].append(source)
    ref=source['id']+'/c-ui'
    assert ref in verified_business_context(p)
    response=compile_plan(v3('prd',basis(claims=[ref]),
        '值班人员核对联系人信息。'),p,'prd')
    from app.contracts import validate_response
    validate_response(response,'prd',p,source['excerpts'],document_type='prd')
    apply_response(p,response)
    artifact=p['documents']['prd']
    canonical=dumps(artifact['content'])
    assert ref in canonical and '值班人员核对联系人信息。' in canonical
    reader=reader_document(artifact)
    assert '值班人员核对联系人信息。' in dumps(reader)
    assert ref not in dumps(reader)
    files=asyncio.run(document_files(p,'prd'))
    markdown=next(v for k,v in files.items() if k.endswith('.md'))
    if isinstance(markdown,bytes):markdown=markdown.decode('utf-8')
    assert '值班人员核对联系人信息。' in markdown


def test_unconfirmed_claim_and_candidate_cannot_be_explanation_basis(tmp_path):
    p=checked_project(tmp_path);source=claim_source(tmp_path);p['sources'].append(source)
    source['business_selection']['confirmed_claim_ids']=[]
    assert verified_business_context(p)=={}
    for invalid in (basis(claims=[source['id']+'/c-ui']),
                    basis(normative=['REQ-CANDIDATE']),basis()):
        with pytest.raises(Problem) as error:
            compile_plan(v3('prd',invalid,'合成未确认内容。'),p,'prd')
        assert error.value.code=='REFERENCE_INVALID'


def test_v3_preserves_original_norms_and_v2_remains_accepted(tmp_path):
    p=checked_project(tmp_path);before=copy.deepcopy(p)
    result=compile_plan(v3('prd',basis(normative=['REQ-0001']),
        '根据已选需求，管理员可执行编辑。'),p,'prd')
    assert p==before
    apply_response(p,result)
    assert p['items'][0]['statement'] in dumps(reader_document(p['documents']['prd']))
    assert all(item['id'] in {c['item_id'] for c in result['result']['coverage']}
               for item in p['items'] if item['id'] in plan_contract(p)['normative_item_ids'])
    from tests.test_prd_v2_semantics import v2
    old=compile_plan(v2(p),p,'prd')
    assert old['result']['coverage']
