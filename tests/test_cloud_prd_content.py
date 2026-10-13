"""Cloud PRD behavior prose stays evidenced; no live data or model calls."""
import asyncio
import copy
import io
import json

import jsonschema
import pytest
from docx import Document

from app.contracts import document_gaps, validate_response
from app.core import Problem, dumps
from app.document_reader import export_readiness, reader_document
from app.exports import document_files
from app.prd import compile_plan, plan_contract, plan_schema
from app.provider import assemble, DEFAULT
from app.workflow import apply_response
from tests.test_prd01 import state


def cloud_project(tmp_path):
    _, p = state(tmp_path)
    p['name'] = '储能云平台联系人管理'
    p['product_context'] = {'product':'储能云平台'}
    p['questions'] = []
    item = p['items'][0]
    item['behavior'] = dict(actor='管理员',entry='从电站联系人列表进入编辑。',
        flow='选择联系人，修改姓名，提交后校验姓名并保存。',data='联系人姓名。',
        permissions='只读角色不能编辑联系人。',result='保存成功后列表显示新姓名。',
        exceptions='取消编辑不保存修改。')
    p['sources'][0]['excerpts'][0]['text'] += '\n'+'\n'.join(item['behavior'].values())
    return p


def proposal(p):
    rid = p['items'][0]['id']
    sections = [dict(section_key='function',context_refs=[],
        normative_refs=plan_contract(p,'prd')['normative_item_ids'],discussion_refs=[],explanations=[])]
    behavior = p['items'][0]['behavior']
    texts = {'interaction': '\n'.join(behavior[k] for k in ('actor','entry','data','permissions','result','exceptions')),
             'business_flow':'\n'.join(behavior[k] for k in ('actor','flow','result','exceptions'))}
    for key, text in texts.items():
        sections.append(dict(section_key=key,context_refs=[],normative_refs=[],discussion_refs=[],
            explanations=[dict(text=text,evidence_refs=dict(context_refs=[],normative_refs=[rid],business_claim_refs=[]))]))
    return dict(plan_version='4',sections=sections)


def test_wire_contract_requires_both_cloud_sections_and_valid_example(tmp_path):
    p = cloud_project(tmp_path)
    messages,_,_=assemble(p,'prd','生成PRD',DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：')[1])
    assert header['plan_contract']['required_analysis_sections']==['interaction','business_flow']
    assert header['plan_contract']['required_detail_requirement_ids']==['REQ-0001']
    jsonschema.validate(header['plan_contract']['example'],header['schema'])
    value=proposal(p)
    jsonschema.validate(value,header['schema'])
    value['sections'].pop()
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,header['schema'])
    value=proposal(p);value['sections'][1]['explanations']=[]
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,header['schema'])


@pytest.mark.parametrize('kind,product',[('mrd','储能云平台'),('prd','学校课程管理'),('prd','不是云平台产品')])
def test_mrd_and_explicit_other_product_keep_their_original_contract(tmp_path,kind,product):
    p=cloud_project(tmp_path);p['product_context']['product']=product
    contract=plan_contract(p,kind)
    assert not contract['required_detail_requirement_ids']
    assert not {'interaction','business_flow'} & set(contract['section_keys'])
    assert not {'interaction','business_flow'} & set(plan_schema(p,kind)['properties']['sections']['items']['properties']['section_key']['enum'])


def test_cloud_sections_survive_compilation_reader_and_export_without_business_edits(tmp_path):
    p=cloud_project(tmp_path);before=copy.deepcopy(p)
    result=compile_plan(proposal(p),p,'prd',include_sketch=False)
    assert p==before
    validate_response(result,'prd',p,p['sources'][0]['excerpts'])
    titles={s['title'] for s in result['result']['sections']}
    assert {'交互逻辑','业务流程说明'} <= titles
    assert not document_gaps(result['result'],p)
    apply_response(p,result)
    assert p['items']==before['items'] and p['questions']==before['questions']
    assert export_readiness(p,'prd')['ready']
    p['documents']['prd']['sketch_policy']='excluded'
    readable=reader_document(p['documents']['prd'])
    assert {'交互逻辑','业务流程说明'} <= {s['title'] for s in readable['sections']}
    assert '选择联系人，修改姓名，提交后校验姓名并保存。' in dumps(readable)
    files=asyncio.run(document_files(p,'prd'))
    markdown=files['PRD.md'].decode('utf-8')
    word='\n'.join(x.text for x in Document(io.BytesIO(files['PRD.docx'])).paragraphs)
    for text in ('交互逻辑','业务流程说明','从电站联系人列表进入编辑。','取消编辑不保存修改。'):
        assert text in markdown and text in word


@pytest.mark.parametrize('missing',['interaction','business_flow'])
def test_compiler_blocks_missing_cloud_section_without_mutating_items(tmp_path,missing):
    p=cloud_project(tmp_path);before=copy.deepcopy(p)
    value=proposal(p);value['sections']=[s for s in value['sections'] if s['section_key']!=missing]
    with pytest.raises(Problem) as error:compile_plan(value,p,'prd')
    assert error.value.code=='SEMANTIC_BLOCKED' and 'REQ-0001' in error.value.message
    assert p==before


def test_each_in_scope_requirement_needs_both_explanations_and_candidates_cannot_fill_them(tmp_path):
    p=cloud_project(tmp_path)
    value=proposal(p)
    p['items'].append(dict(copy.deepcopy(p['items'][0]),id='REQ-0002',title='另一个功能'))
    before=copy.deepcopy(p)
    with pytest.raises(Problem,match='REQ-0002'):compile_plan(value,p,'prd')
    assert p==before
    p=cloud_project(tmp_path/'candidate');value=proposal(p)
    value['sections'][1]['explanations'][0]['evidence_refs']['normative_refs']=['REQ-CANDIDATE']
    with pytest.raises(Problem) as error:compile_plan(value,p,'prd')
    assert error.value.code=='REFERENCE_INVALID'


def test_named_unknown_is_visible_but_blocks_delivery_without_answering_it(tmp_path):
    p=cloud_project(tmp_path);value=proposal(p)
    value['sections'][1]['explanations'][0]['text'] += '\n【资料待确认】姓名校验失败时的页面提示文案尚未明确。'
    before=copy.deepcopy(p)
    result=compile_plan(value,p,'prd')
    assert p==before
    apply_response(p,result)
    assert not p['questions'] and p['items']==before['items']
    assert '【资料待确认】' in dumps(reader_document(p['documents']['prd']))
    gaps=document_gaps(p['documents']['prd']['content'],p)
    assert any('交互逻辑' in message and '待确认' in message for message in gaps)
    readiness=export_readiness(p,'prd')
    assert not readiness['ready']
    assert any(i['code']=='DOCUMENT_CONTENT_REQUIRED' for i in readiness['issues'])


def test_legacy_cloud_document_is_preserved_and_missing_content_is_reported(tmp_path):
    p=cloud_project(tmp_path)
    result=compile_plan(proposal(p),p,'prd')
    apply_response(p,result)
    artifact=p['documents']['prd']
    artifact['content']['sections']=[s for s in artifact['content']['sections'] if s['title'] not in ('交互逻辑','业务流程说明')]
    before=copy.deepcopy(p)
    issues=export_readiness(p,'prd')['issues']
    assert len([i for i in issues if i['code']=='DOCUMENT_CONTENT_REQUIRED'])==2
    assert p==before


def test_section_titles_do_not_hide_missing_behavior_or_add_a_ui_to_background_jobs(tmp_path):
    p=cloud_project(tmp_path);value=proposal(p)
    p['items'][0]['behavior']['entry']=''
    result=compile_plan(value,p,'prd')
    assert any('入口及前置条件' in gap for gap in document_gaps(result['result'],p))
    contract=plan_contract(p,'prd')
    assert '不强行新增页面' in contract['rules']
    messages,_,_=assemble(p,'review','核对文档',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    assert '交互逻辑和业务流程说明' in context['review_scope']['instruction']


def test_detail_prose_uses_behavior_evidence_without_turning_future_behavior_into_existing_capability(tmp_path):
    p=cloud_project(tmp_path)
    p['items'][0]['behavior']['result']='仅正常保存成功分支更新联系人；超时或结果未知时查询同一请求结果，不自动重发。'
    p['items'][0]['behavior']['exceptions']='明确未提交失败时原记录不变；结果未知时不能断言原记录不变。'
    value=proposal(p)
    result=compile_plan(value,p,'prd')
    validate_response(result,'prd',p,p['sources'][0]['excerpts'])
    apply_response(p,result)
    assert export_readiness(p,'prd')['ready']
    value['sections'][1]['explanations'][0]['text']='云平台已经支持直接编辑联系人并保存。'
    with pytest.raises(Problem) as error:compile_plan(value,p,'prd')
    assert error.value.code=='SEMANTIC_BLOCKED'
