"""Eight fixed offline history/reference cases; no business decisions or models."""
import copy
import pytest
from app.contracts import validate_response
from app.core import Problem
from app.semantic_quality import counting_definition_facts
from tests.test_counting_definition_classification import adoption_review
from tests.test_semantic_quality_regressions import project


CASES=[
 ('suggestion_then_confirmation','方案建议：备注可以按Unicode码点计数；产品经理已明确确认采纳这个计数建议。','REQ-0001',False,'confirmed',None),
 ('suggestion_still_pending','方案建议：备注可以按Unicode码点计数；产品经理尚未明确确认采纳这个计数建议。','REQ-0001',True,'pending',None),
 ('confirmation_then_withdrawal','产品经理已确认：备注按Unicode码点计数，最多100码点；本期产品经理明确撤回该计数方案，重新选择计数口径。','REQ-0001',True,'pending',None),
 ('withdrawal_unproven','产品经理已确认：备注按Unicode码点计数，最多100码点；没有证据表明产品经理已撤回该计数方案。','REQ-0001',False,'confirmed',None),
 ('confirmation_then_explicit_change','产品经理已确认：备注按Unicode码点计数，最多100码点；产品经理已明确确认本期改按扩展字素簇计数，最多100字素簇。','REQ-0001',False,'confirmed','graphemes'),
 ('change_still_only_suggested','产品经理已确认：备注按Unicode码点计数，最多100码点；模型建议：本期可以改按扩展字素簇计数，该变更需产品经理确认后才采纳。','REQ-0001',False,'confirmed',None),
 ('same_named_other_object_confirmed','REQ-0001备注的计数建议：按Unicode码点计数；REQ-OTHER标题的计数建议：按Unicode码点计数；产品经理已明确确认采纳REQ-OTHER的该计数建议。','REQ-0001',True,'pending',None),
 ('same_named_target_object_confirmed','REQ-0001备注的计数建议：按Unicode码点计数；REQ-OTHER标题的计数建议：按Unicode码点计数；产品经理已明确确认采纳REQ-OTHER的该计数建议。','REQ-OTHER',False,'confirmed',None),
]


@pytest.mark.parametrize('name,source,object_id,accepted,decision,last_unit',CASES,ids=[c[0] for c in CASES])
def test_fixed_history_and_object_adoption(tmp_path,name,source,object_id,accepted,decision,last_unit):
    p=project(tmp_path,'备注最多100字符。')
    if object_id=='REQ-OTHER' or 'REQ-OTHER' in source:
        other=copy.deepcopy(p['items'][0]);other.update(id='REQ-OTHER',title='标题计数建议',statement='标题最多100字符。')
        p['items'].append(other)
    p['sources'][0]['excerpts'][0]['text']=source
    value=adoption_review(p);value['findings'][0]['related_refs']=[object_id]
    before=copy.deepcopy((p,value))
    if accepted:validate_response(value,'review',p,p['sources'][0]['excerpts'])
    else:
        with pytest.raises(Problem) as error:validate_response(value,'review',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    facts=[f for f in counting_definition_facts(p) if f['origin']=='source' and object_id in f['related_refs']]
    assert facts and {f['decision_status'] for f in facts}=={decision}
    for fact in facts:
        assert fact['location']=='SRC-0001/EX-0001' and fact['quote']==source
        assert fact['source_refs']==p['items'][0]['source_refs']
        assert all(e['quote'] in source for e in fact['decision_evidence'])
        if 'REQ-OTHER' in source:
            assert fact['related_refs']==[object_id]
            assert all(('REQ-OTHER' not in e['quote']) if object_id=='REQ-0001' else ('REQ-0001' not in e['quote']) for e in fact['decision_evidence'])
    if last_unit:
        choices=[e['declared_units'] for f in facts for e in f['decision_evidence'] if e['decision_status']=='confirmed']
        assert choices[-1]==[last_unit]
    assert (p,value)==before
