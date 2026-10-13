"""Two fixed public-flow counterexamples, pure synthetic inputs, zero live calls."""
import copy
import pytest
from app.contracts import validate_response
from app.core import Problem
from app.requirements import behavior_fingerprint,require_behavior_review
from tests.test_result_conditions import case

FUTURE='结果未知时禁止再次创建新的申请；先查询原申请ID。'
SUCCESS='系统受理成功且处理成功的分支，配额满足则整批创建；明确拒绝时不创建；超时结果未知。'

@pytest.mark.parametrize('text',[FUTURE,SUCCESS])
def test_fixed_valid_phrases_pass_full_response_and_reviewed_adoption(text):
    p,v,ex=case(text);before=copy.deepcopy((p,v,ex))
    assert validate_response(v,'ingest',p,ex)==v and (p,v,ex)==before
    item=copy.deepcopy(v['proposals'][0]);item['behavior_review_required']=True
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    require_behavior_review(item)


@pytest.mark.parametrize('text',[
    '超时结果未知时不得重新创建新请求；先查询原申请ID。',
    '响应丢失时不允许再次创建新的申请，先查询原申请ID。',
    '结果未知时禁止创建另一个请求；状态确认前不重复提交。',
    '服务受理成功且处理成功的分支，配额满足则创建资源；超时结果未知。',
    '云资源的系统受理成功且处理成功的分支，配额满足则整批创建；明确拒绝时不创建。',
])
def test_same_object_future_constraint_or_affirmative_branch_is_allowed(text):
    test_fixed_valid_phrases_pass_full_response_and_reviewed_adoption(text)


@pytest.mark.parametrize('text',[
    '结果未知时本批不创建。',
    '响应丢失时既有资源不变。',
    '结果未知时已创建新的申请。',
    '超时后没有创建原申请。',
    '系统尚未受理成功，配额满足则创建。',
    '系统受理成功但尚未处理成功，配额满足则创建。',
    '系统受理成功但处理失败，配额满足则创建。',
    '系统受理成功，配额满足则创建。',
    '系统登录成功，配额满足则创建。',
    '工单的系统受理成功且处理成功的分支，云资源配额满足则整批创建。',
    '并非正常成功分支，配额满足则创建资源。',
    '全部配额满足就保证创建。',
    '服务明确拒绝时仍创建资源。',
])
def test_historical_result_or_negated_other_object_branch_is_still_blocked(text):
    p,v,ex=case(text);before=copy.deepcopy((p,v,ex))
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED' and not error.value.model_repair_allowed
    assert (p,v,ex)==before
    item=copy.deepcopy(v['proposals'][0]);item['behavior_review_required']=True
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    with pytest.raises(Problem) as adoption:require_behavior_review(item)
    assert adoption.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('field',['statement','summary','result.understanding'])
@pytest.mark.parametrize('text',[FUTURE,SUCCESS])
def test_scoped_conditions_are_consistent_in_other_public_fields(field,text):
    p,v,ex=case(text,field=field);before=copy.deepcopy((p,v,ex))
    assert validate_response(v,'ingest',p,ex)==v and (p,v,ex)==before


def test_pending_or_unreviewed_candidate_cannot_bypass_human_boundary():
    p,v,ex=case(FUTURE);item=copy.deepcopy(v['proposals'][0]);item['behavior_review_required']=True
    with pytest.raises(Problem) as error:require_behavior_review(item)
    assert error.value.code=='BEHAVIOR_REVIEW_REQUIRED'
    p,v,ex=case('待澄清：系统受理成功且处理成功的分支，配额满足则整批创建。')
    validate_response(v,'ingest',p,ex)
    item=v['proposals'][0];item['behavior_review_required']=True
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    with pytest.raises(Problem) as error:require_behavior_review(item)
    assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('text',[FUTURE,SUCCESS])
def test_public_isolated_adoption_requires_exact_human_review(tmp_path,text):
    from app.workflow import apply_response
    from tests.test_project_summary_repair import client
    from tests.test_behavior_review_guard import post
    browser,app=client(tmp_path);p=app.state.store.create('纯合成边界核对')
    _,value,ex=case(text)
    with app.state.store.edit(p['id'],p['revision'],'隔离纯合成输入/响应夹具') as (draft,_):
        draft['sources']=[dict(id='SRC-condition',title='纯合成来源',excluded=False,parse_status='read',excerpts=ex)]
        validate_response(value,'ingest',draft,ex)
        apply_response(draft,value)
    root='/api/projects/'+p['id'];iid=browser.get(root).json()['items'][0]['id']
    before=copy.deepcopy(app.state.store.get(p['id']))
    result=post(browser,root,'/items/'+iid,selection_status='selected')
    assert result.status_code==409 and result.json()['code']=='BEHAVIOR_REVIEW_REQUIRED'
    assert app.state.store.get(p['id'])==before
    assert post(browser,root,'/items/'+iid+'/behavior',values={},change_type='new',review_confirmed=True).status_code==200
    assert post(browser,root,'/items/'+iid,selection_status='selected').status_code==200
    selected=browser.get(root).json()['items'][0]
    assert selected['selection_status']=='selected' and selected['behavior']['result']==text
    assert selected['source_refs']==value['proposals'][0]['source_refs']
    assert not app.state.store.records(p['id'],'run')


@pytest.mark.parametrize('text',[
    '前提：提交保存时网络中断导致提交结果未知；此时应先查询原提交是否完成，未确认结果前不得再次发出保存提交，也不得当作保存失败处理。',
    '结果未知，不得当作保存成功或保存失败；先查询原提交。',
    '结果未知，禁止重新发送保存请求，先查询原提交。',
    '保存时响应丢失；不能视为保存成功。',
    '结果未知，不得判定为保存失败。',
])
def test_unknown_occurrence_and_local_negated_inference_are_not_results(text):
    from app.result_conditions import facts, issues
    before=str(text)
    found=facts(text,location='synthetic',fallback_operation='write',fallback_subject='记录')
    assert not issues(found),found
    assert any(f['role']=='unknown_result' for f in found)
    assert text==before


@pytest.mark.parametrize('text',[
    '结果未知，当作保存成功处理。',
    '结果未知，当作保存失败处理。',
    '结果未知，不得不当作保存失败。',
    '结果未知，不得当作保存失败，但本次保存成功。',
    '结果未知，不得当作保存失败；本次未保存。',
    '结果未知，禁止重新发送保存请求，但原记录不变。',
    '保存时响应丢失，仍保存成功。',
    '保存时结果未知；本次没有保存。',
    '结果未知，已发送保存请求。',
    '结果未知，禁止再次发出保存提交；原记录不变。',
    '保存时业务已处理完成却响应丢失。',
    '保存时工单已经生成但结果未知。',
])
def test_retry_prohibition_cannot_hide_a_later_or_positive_result(text):
    from app.result_conditions import facts, issues
    found=facts(text,location='synthetic',fallback_operation='write',fallback_subject='记录')
    assert any(i['reason']=='unknown_result_asserted' for i in issues(found)),found


def test_live_phrase_passes_full_protocol_and_preserves_source_and_text():
    text=('前提：提交保存时网络中断导致提交结果未知；此时应先查询原提交是否完成，'
          '未确认结果前不得再次发出保存提交，也不得当作保存失败处理。')
    source='本期新增记录保存功能。超时结果未知，先查询原提交，状态确认前不重复提交。'
    p,v,ex=case(text,source=source,field='statement')
    v['proposals'][0]['title']='记录保存'
    v['proposals'][0]['behavior']={}
    v['proposals'][0]['scope_evidence'][0]['quote']='本期新增记录保存功能。'
    before=copy.deepcopy((p,v,ex))
    assert validate_response(v,'ingest',p,ex)==v
    assert (p,v,ex)==before
