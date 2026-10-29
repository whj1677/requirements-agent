"""Finite inference scope, checked through public response and human review gates."""
import copy
import pytest
from app.contracts import validate_response
from app.core import Problem
from app.requirements import behavior_fingerprint, require_behavior_review
from app.result_conditions import facts, issues
from tests.test_result_conditions import case, NORMAL

ORIGINAL='响应丢失或超时为结果未知，不断言创建、未创建或既有资源未变。'


@pytest.mark.parametrize('text',[
    '超时或结果未知保留备注，不自动重发，先查询同一告警确认记录：查到已确认则展示且不重复写入，确认未写入才允许再次提交，查询仍失败保持待核实并可再次查询。',
    '超时或结果未知先查询同一告警确认记录，查到已确认则展示确认人、时间和备注，不重复写入。',
    '超时结果未知；查询确认未写入后，允许再次提交。',
])
def test_explicit_confirmed_query_branch_resolves_only_its_later_effects(text):
    before=str(text)
    assert not issues(facts(text,location='synthetic.flow',fallback_operation='write',fallback_subject='记录'))
    assert text==before


@pytest.mark.parametrize('text',[
    '结果未知，原记录已写入，之后查询确认成功。',
    '结果未知，未查到已确认则原记录已写入。',
    '结果未知，没有查到已确认，原记录未写入。',
    '结果未知，查询未明确成功，原记录已写入。',
    '结果未知，查到另一工单已确认则原记录已写入。',
    '结果未知，查询仍未知，原记录未写入。',
])
def test_query_words_cannot_launder_unknown_earlier_negated_or_other_object_results(text):
    found=issues(facts(text,location='synthetic.flow',fallback_operation='write',fallback_subject='记录'))
    assert found and any(row['reason']=='unknown_result_asserted' for row in found)


@pytest.mark.parametrize('text',[
    ORIGINAL,
    '结果未知时不得展示或断言申请失败、本批未创建或既有资源未变。',
    '结果未知时不能断言或展示创建、未创建或既有资源不变。',
    '结果未知，不允许推断原批创建、原批未创建及既有资源不变。',
    '不得认定已创建、未创建或既有资源不变，超时仍为结果未知。',
    '结果未知，不能据此断言已创建或未创建，也不能认定既有资源不变。',
    '超时未知，不推断原记录已写入或未写入；先查询原请求ID。',
    '结果未知时禁止再次创建新的申请；先查询原申请ID。',
    '结果未知，不断言创建或未创建；服务明确拒绝时本批不创建且既有资源不变。',
    '不能从配额不满足则拒绝反推配额全部满足就保证创建。',
    '超时未知，不能根据未收到响应推断原批未创建。',
    '超时结果未知，沿原申请ID查询、确认前不重复；明确拒绝时才断言本批不创建且既有资源不变。',
    '超时结果未知，沿原申请ID查询、确认前不重复，明确拒绝时才断言本批不创建且既有资源不变。',
    '已保存合成测试操作者回答：超时为结果未知，不能断言创建或未创建。',
    '纠正“配额全部满足即保证创建”的外推；超时结果未知，不能断言创建或未创建。',
    '正常成功分支，配额满足则创建资源；超时结果未知，不断言创建或未创建。',
])
@pytest.mark.parametrize('field',['statement','behavior.result','summary','result.understanding'])
def test_local_inference_prohibition_passes_complete_validation(text,field):
    p,v,ex=case(NORMAL);item=v['proposals'][0]
    if field.startswith('behavior.'):item['behavior']['result']=text
    elif field=='statement':item['statement']=text
    elif field=='summary':v['summary']=text
    else:v['result']['understanding']=text
    before=copy.deepcopy((p,v,ex))
    assert validate_response(v,'ingest',p,ex)==v and (p,v,ex)==before
    reviewed=copy.deepcopy(item);reviewed['behavior_review_required']=True
    reviewed['behavior_review']={'content_hash':behavior_fingerprint(reviewed)}
    require_behavior_review(reviewed)


@pytest.mark.parametrize('text',[
    '结果未知，原批已创建。',
    '结果未知，原批未创建且既有资源不变。',
    '结果未知，不能不断言原批未创建。',
    '结果未知，并非不能认定原批已创建。',
    '超时未知，不能不根据未收到响应推断原批未创建。',
    '结果未知，不是不推断既有资源不变。',
    '结果未知，并非不得展示或断言原批已创建。',
    '结果未知，不得展示创建，但断言原批未创建。',
    '结果未知，未明确拒绝时断言原批未创建。',
    '结果未知，明确拒绝时仍创建资源。',
    '结果未知时已保存原申请数据。',
    '结果未知，纠正“创建”的旧表述，但原批已创建。',
    '结果未知，原句“未创建”需纠正，但既有资源不变。',
    '结果未知，不能断言创建，但原批已创建。',
    '结果未知，不能断言创建但原批已创建。',
    '结果未知，不推断创建同时既有资源不变。',
    '结果未知，不能断言创建，既有资源不变。',
    '结果未知，不推断已创建，并且原批未创建。',
    '不能断言已创建，超时结果未知时原批未创建。',
    '结果未知时禁止创建新的申请，但原申请已创建。',
    '结果未知时禁止创建新的申请，原申请未创建。',
])
def test_positive_outcome_in_other_clause_or_double_negation_stays_blocked(text):
    p,v,ex=case(text);before=copy.deepcopy((p,v,ex))
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED' and error.value.model_repair_allowed is False
    assert (p,v,ex)==before
    item=copy.deepcopy(v['proposals'][0]);item['behavior_review_required']=True
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    with pytest.raises(Problem) as reviewed:require_behavior_review(item)
    assert reviewed.value.code=='SEMANTIC_BLOCKED'


def test_prohibition_keeps_original_location_object_refs_and_result_list():
    refs=[{'source_id':'SRC-synthetic','excerpt_id':'EX-synthetic'}]
    parsed=facts(ORIGINAL,location='proposals[0].behavior.result',object_id='TMP-original',source_refs=refs)
    constraints=[f for f in parsed if f['role']=='inference_constraint']
    assert len(constraints)==3 and not issues(parsed)
    assert all(f['object_id']=='TMP-original' and f['location']=='proposals[0].behavior.result' and f['source_refs']==refs for f in constraints)
    assert all(f['quote']==ORIGINAL.rstrip('。') and f['assertion_scope']['predicate']=='断言' for f in constraints)
    assert all(f['assertion_scope']['negators']==1 for f in constraints)
    unsafe=facts(ORIGINAL.rstrip('。')+'，但原批已创建。',location='same field',source_refs=refs)
    assert issues(unsafe)[0]['reason']=='unknown_result_asserted'


def test_inference_prohibition_does_not_change_review_identity_or_adopt_pending():
    p,v,ex=case(ORIGINAL);validate_response(v,'ingest',p,ex)
    item=v['proposals'][0];item['behavior_review_required']=True
    with pytest.raises(Problem) as caught:require_behavior_review(item)
    assert caught.value.code=='BEHAVIOR_REVIEW_REQUIRED'
    p,v,ex=case('待澄清：配额满足则创建资源。')
    validate_response(v,'ingest',p,ex)
    with pytest.raises(Problem):require_behavior_review(v['proposals'][0])
