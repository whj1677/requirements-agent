"""Scope classification pairs: separate evidence, keep rejection of mixed identity."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from app.understanding import contract
from tests.helpers import EXAMPLES
from tests.test_current_workflow_evidence import case

def test_generation_contract_describes_separate_preserved_constraint():
    guide=contract()
    assert guide['classification_separation_example']['change']['change_type']=='new'
    assert guide['classification_separation_example']['preserve']['kind']=='constraint'
    assert guide['classification_separation_example']['change']['scope_states']==['change']

@pytest.mark.parametrize('variant',['mixed','separated','new_only','preserve_only'])
def test_public_classification_keeps_mixed_rejected_and_pure_requirements_valid(tmp_path,variant):
    p=case(tmp_path)
    text='本期新增备注输入规则；既有权限保持不变。'
    p['sources'][0]['excerpts'][0]['text']=text
    ref=dict(source_id='SRC-0001',excerpt_id='EX-0001')
    change=dict(state='change',quote='本期新增备注输入规则',source_ref=ref)
    preserve=dict(state='preserve',quote='既有权限保持不变',source_ref=ref)
    req=dict(temp_id='TMP-CHANGE',kind='requirement',action='add',target_item_id=None,title='备注输入',
        statement='本期新增备注输入规则。',applies_to='to_be',epistemic_status='reported',source_refs=[ref],related_refs=[],
        change_type='new',scope_evidence=[change],classification_reason='有出处的本期新增。')
    value=copy.deepcopy(EXAMPLES['clarify']);value.update(proposals=[req],questions=[])
    if variant=='mixed':req['scope_evidence'].append(preserve)
    elif variant=='separated':value['proposals'].append(dict(req,temp_id='TMP-PRESERVE',kind='constraint',
        title='保持权限',statement='既有权限保持不变。',change_type='preserved',scope_evidence=[preserve],related_refs=['TMP-CHANGE']))
    elif variant=='preserve_only':req.update(statement='既有权限保持不变。',change_type='preserved',scope_evidence=[preserve])
    before=copy.deepcopy((p,value))
    if variant=='mixed':
        with pytest.raises(Problem) as e:validate_response(value,'clarify',p,p['sources'][0]['excerpts'])
        assert e.value.code=='SEMANTIC_BLOCKED'
    else:assert validate_response(value,'clarify',p,p['sources'][0]['excerpts'])==value
    assert (p,value)==before
