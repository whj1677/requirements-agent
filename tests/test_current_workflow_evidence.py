"""Paired public checks for reported current workflows, not inferred old flows."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from app.product_flow import checkpoint, status
from app.semantic_quality import current_document_checks
from app.workflow import apply_response
from tests.helpers import EXAMPLES
from tests.test_semantic_quality_boundaries import compile_explanation
from tests.test_semantic_quality_regressions import project

CURRENT='租户管理员目前逐项申请云资源。'
NEW='本期新增同租户批量云资源申请，每批最多20项。'

def case(tmp_path, existing='既有审计服务运行正常；现有申请流程没有提供。'):
    p=project(tmp_path,NEW)
    p['questions']=[]
    p['product_context']['current_state']=existing
    p['sources'][0]['excerpts'][0]['text']=NEW+' '+existing
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    return p

@pytest.mark.parametrize('kind',['mrd','prd'])
@pytest.mark.parametrize('existing,text,accepted',[
    ('审计服务运行正常。',CURRENT,False),
    (CURRENT,CURRENT,True),
    ('工单管理员目前逐项申请工单。',CURRENT,False),
    ('工单管理员目前逐项申请云资源。',CURRENT,False),
    ('本期决定租户管理员逐项申请云资源。',CURRENT,False),
    ('尚未确认租户管理员目前逐项申请云资源。',CURRENT,False),
    ('模型建议：租户管理员目前逐项申请云资源。',CURRENT,False),
    ('申请流程未提供。','模型假设：租户管理员目前逐项申请云资源，须核对，未当作事实。',True),
    ('申请流程未提供。','尚未确认租户管理员目前逐项申请云资源。',True),
    ('申请流程未提供。','建议本期新增批量申请，不反推现状申请方式。',True),
    ('申请流程未提供。',NEW,True),
    ('现有人员手工录入工单。','目前人员手工录入工单。',True),
    ('现有工单录入流程未提供。','目前人员手工录入工单。',False),
])
def test_current_method_needs_same_positive_workflow_evidence(tmp_path,kind,existing,text,accepted):
    p=case(tmp_path,existing);before=copy.deepcopy(p)
    if accepted:
        response=compile_explanation(p,text,kind,['current_state'])
        validate_response(response,'prd',p,p['sources'][0]['excerpts'],kind)
    else:
        with pytest.raises(Problem) as e:compile_explanation(p,text,kind,['current_state'])
        assert e.value.code=='SEMANTIC_BLOCKED'
    assert p==before

@pytest.mark.parametrize('stage',['ingest','clarify','change','prd'])
def test_generated_summary_cannot_promote_new_requirement_to_old_workflow(tmp_path,stage):
    p=case(tmp_path);value=copy.deepcopy(EXAMPLES[stage])
    if stage=='prd':value=compile_explanation(p,NEW,'prd',['current_state'])
    else:value.update(proposals=[],questions=[])
    value['summary']=CURRENT
    before=copy.deepcopy((p,value))
    with pytest.raises(Problem) as e:validate_response(value,stage,p,p['sources'][0]['excerpts'])
    assert e.value.code=='SEMANTIC_BLOCKED' and (p,value)==before

@pytest.mark.parametrize('block_kind',['narrative','explanation'])
def test_direct_document_validation_checks_actual_prose_without_compiler(tmp_path,block_kind):
    p=case(tmp_path);value=compile_explanation(p,NEW,'prd',['current_state'])
    block=next(b for s in value['result']['sections'] for b in s['blocks'] if b['kind']=='explanation')
    block.update(kind=block_kind,text=CURRENT)
    if block_kind=='narrative':block.pop('evidence_refs')
    with pytest.raises(Problem) as e:validate_response(value,'prd',p,p['sources'][0]['excerpts'])
    assert e.value.code=='SEMANTIC_BLOCKED'

def test_stored_finding_is_bound_only_to_real_unsupported_document_and_quote(tmp_path):
    p=case(tmp_path)
    for kind in ('mrd','prd'):apply_response(p,compile_explanation(p,NEW,kind,['current_state']))
    artifact=p['documents']['mrd']
    section=next(s for s in artifact['content']['sections'] if any(b['kind']=='explanation' for b in s['blocks']))
    idx=next(i for i,b in enumerate(section['blocks']) if b['kind']=='explanation')
    section['blocks'][idx]['text']=CURRENT
    checks=[c for c in current_document_checks(p) if c['dimension']=='current_workflow']
    assert len(checks)==1 and checks[0]['document_type']=='mrd'
    assert checks[0]['location']==f"{artifact['id']}.{section['section_id']}.blocks[{idx}]"
    assert checks[0]['quote']==CURRENT.rstrip('。')
