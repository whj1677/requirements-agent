"""Offline owner-cited semantic cases; all inputs and PM choices are synthetic."""
import copy
import pytest
from app.core import Problem
from app.contracts import validate_response
from app.source_fidelity import text_issues, item_issues, validate_item
from tests.test_result_conditions import case, NORMAL, SOURCE

PAIRS=[
 ('exclusive_visibility','只有租户管理员可以查看拒绝原因。','资源申请拒绝原因发给租户管理员，继续沿用现有权限。','资源申请拒绝原因只有租户管理员可以查看。'),
 ('budget_is_not_quota','资金不足时拒绝资源申请。','资源申请校验配额，任一项配额不足时拒绝。','资源申请需同时校验业务资金；资金不足时拒绝。'),
 ('existence_is_not_sufficiency','前提：资源配额字段齐全。','资源配额必须满足申请数量，满足也不能保证服务受理。','资源配额字段齐全时通过字段存在性校验。'),
 ('comparison_premise','批量申请降低逐条录入工作。','本期提供批量资源申请。','既有资源申请流程需要逐条录入。'),
]

@pytest.mark.parametrize('dimension,claim,basis,explicit',PAIRS)
def test_independent_source_pair(dimension,claim,basis,explicit):
    evidence=[dict(text=basis,ref=dict(source_id='S',excerpt_id='E'),locator='synthetic 1',source_hash='synthetic')]
    issues=text_issues(claim,evidence,'body',hint='资源')
    assert len(issues)==1 and issues[0]['dimension']==dimension
    assert issues[0]['quote']==claim.rstrip('。')
    assert issues[0]['source_refs']==[dict(source_id='S',excerpt_id='E')]
    assert issues[0]['evidence'][0]['quote']==basis
    assert not text_issues(claim,[dict(evidence[0],text=basis+' '+explicit)],'body',hint='资源')

@pytest.mark.parametrize('field',['title','statement','behavior.permissions','behavior.flow','summary','result.understanding','product_context_proposal.value'])
def test_public_validator_checks_title_body_and_prose(field):
    p,v,ex=case(NORMAL)
    claim='仅本租户管理员可见反馈。'
    if field.startswith('behavior.'):v['proposals'][0]['behavior'][field.split('.')[1]]=claim
    elif field in ('title','statement'):v['proposals'][0][field]=claim
    elif field=='summary':v[field]=claim
    elif field=='result.understanding':v['result']['understanding']=claim
    else:v['product_context_proposal']['value']=claim
    before=copy.deepcopy((p,v,ex))
    with pytest.raises(Problem) as e:validate_response(v,'ingest',p,ex)
    assert e.value.code=='SEMANTIC_BLOCKED' and field.split('.')[-1] in e.value.message
    assert not e.value.model_repair_allowed and (p,v,ex)==before
    ex[0]['text']+=' 仅本租户管理员可见反馈。'
    assert validate_response(v,'ingest',p,ex)==v

@pytest.mark.parametrize('basis',[
 '建议仅本租户管理员可见反馈。',
 '尚未确认仅本租户管理员可见反馈。',
 '不能断言仅本租户管理员可见反馈。',
 '只有审计员可以查看拒绝原因。',
 '工单反馈仅本租户管理员可见反馈。',
])
def test_tentative_negated_wrong_role_or_other_object_cannot_support(basis):
    found=text_issues('仅本租户管理员可见反馈。',[dict(text=basis,ref=None)],'permissions',hint='资源')
    assert found

@pytest.mark.parametrize('claim',[
 '不是只有租户管理员可以查看拒绝原因。',
 '不能断言仅本租户管理员可见反馈。',
 '建议仅本租户管理员可见反馈。',
 '是否只有租户管理员可以查看拒绝原因？',
 '预算不是配额；模型 token 预算不足。',
 '配额存在不代表满足申请量。',
 '不减少逐项提交操作。',
 '本期目标是申请更方便；收益未量化。',
])
def test_denial_question_suggestion_metadata_or_qualitative_goal_is_not_assertion(claim):
    assert not text_issues(claim,[dict(text=SOURCE,ref=None)],'summary',hint='资源')

@pytest.mark.parametrize('prefix',['建议','尚待确认','假设'])
def test_tentative_normative_rule_is_retained_but_not_adopted(prefix):
    p,v,ex=case(NORMAL);i=v['proposals'][0]
    i['behavior']['permissions']=prefix+'仅本租户管理员可见反馈。'
    assert validate_response(v,'ingest',p,ex)==v
    before=copy.deepcopy((i,p))
    with pytest.raises(Problem) as e:validate_item(i,p)
    assert e.value.status==409 and (i,p)==before

@pytest.mark.parametrize('status',['excluded','superseded_by'])
def test_retired_source_cannot_support_adoption(status):
    p,v,ex=case(NORMAL,source=SOURCE+'仅本租户管理员可见反馈。');i=v['proposals'][0]
    i['behavior']['permissions']='仅本租户管理员可见反馈。'
    validate_item(i,p)
    p['sources'][0][status]=True if status=='excluded' else 'S-new'
    with pytest.raises(Problem):validate_item(i,p)

@pytest.mark.parametrize('binding',['good','unrelated','not_answered','not_referenced'])
def test_only_current_directly_bound_manual_answer_is_evidence(binding):
    p,v,ex=case(NORMAL);i=v['proposals'][0];i['id']='REQ-test'
    i['behavior']['permissions']='仅本租户管理员可见反馈。';i['answer_refs']=['Q-test']
    ref=dict(source_id='S-answer',excerpt_id='E-answer')
    p['sources'].append(dict(id='S-answer',excerpts=[dict(id='E-answer',text='人工决定：仅本租户管理员可见反馈。',locator='synthetic PM',source_hash='synthetic')]))
    p['questions']=[dict(id='Q-test',status='answered',answer='本期采用仅管理员可见。',related_refs=['REQ-test'],answer_source_refs=[ref])]
    if binding=='unrelated':p['questions'][0]['related_refs']=['REQ-other']
    if binding=='not_answered':p['questions'][0]['status']='open'
    if binding=='not_referenced':i['answer_refs']=[]
    if binding=='good':validate_item(i,p)
    else:
        with pytest.raises(Problem):validate_item(i,p)

@pytest.mark.parametrize('bad_binding',[None,'object','source','dimension'])
def test_joint_source_bound_question_can_hold_candidate_only(bad_binding):
    p,v,ex=case(NORMAL);i=v['proposals'][0];i['behavior']['permissions']='仅本租户管理员可见反馈。'
    q=dict(temp_id='QTMP-permissions',topic_key='feedback-permissions',question='反馈是否仅管理员可见，权限是否应沿用既有角色？',why='收件人不能定义排他权限。',options=[],blocking=True,related_refs=[i['temp_id']],source_refs=v['used_source_refs'],blocking_stage=3,decision_points=[],target_question_id=None)
    v['questions']=[q]
    if bad_binding=='object':q['related_refs']=[]
    if bad_binding=='source':q['source_refs']=[]
    if bad_binding=='dimension':q['question']='反馈界面颜色如何选择？'
    if bad_binding is None:validate_response(v,'ingest',p,ex)
    else:
        with pytest.raises(Problem):validate_response(v,'ingest',p,ex)
    with pytest.raises(Problem):validate_item(i,p)

@pytest.mark.parametrize('kind',['mrd','prd'])
def test_public_document_explanations_do_not_upgrade_feedback_receiver(tmp_path,kind):
    from tests.test_semantic_quality_regressions import project
    from tests.test_semantic_quality_boundaries import compile_explanation
    p=project(tmp_path,SOURCE+'向本租户管理员反馈拒绝原因；沿用既有权限体系。')
    before=copy.deepcopy(p)
    with pytest.raises(Problem) as e:compile_explanation(p,'仅本租户管理员可见反馈。',kind)
    assert e.value.code=='SEMANTIC_BLOCKED' and 'explanations[0]' in e.value.message
    assert p==before

def test_review_checkbox_is_not_business_source():
    from app.requirements import require_behavior_review,behavior_fingerprint
    p,v,ex=case(NORMAL);i=v['proposals'][0];i['behavior']['permissions']='仅本租户管理员可见反馈。'
    i['behavior_review_required']=True;i['behavior_review']={'content_hash':behavior_fingerprint(i)}
    with pytest.raises(Problem):require_behavior_review(i,p)

def test_authentic_statement_edit_can_define_explicit_new_rule():
    p,v,ex=case(NORMAL);i=v['proposals'][0]
    i['statement']='云资源反馈仅本租户管理员可见反馈。'
    with pytest.raises(Problem):validate_item(i,p)
    validate_item(i,p,decision_text=i['statement'])

def test_selected_legacy_claim_blocks_gate_without_rewriting_history():
    from app.contracts import gate
    from app.store import Store
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as folder:
        p=Store(__import__('pathlib').Path(folder)).create('Synthetic gate')
        base,v,ex=case(NORMAL);i=v['proposals'][0]
        i.update(id='REQ-test',selection_status='selected');i['behavior']['permissions']='仅本租户管理员可见反馈。'
        p['items']=[i];p['sources']=[dict(row,excluded=False) for row in base['sources']];before=copy.deepcopy(p)
        assert any('behavior.permissions' in issue for issue in gate(p))
        assert p==before


@pytest.mark.parametrize('claim,blocked',[
 ('不是不减少逐项提交操作。',True),
 ('不能断言减少逐项提交操作。',False),
 ('不能不确认仅本租户管理员可见反馈。',True),
 ('错误表述：“仅本租户管理员可见反馈”已撤回。',False),
])
def test_negation_parity_and_corrected_quotation(claim,blocked):
    assert bool(text_issues(claim,[dict(text=SOURCE,ref=None)],'summary',hint='资源'))==blocked

def test_uncited_global_permission_does_not_forgive_document_explanation():
    from app.semantic_quality import validate_explanation
    with pytest.raises(Problem):validate_explanation('仅本租户管理员可见反馈。','资源申请向管理员反馈拒绝原因。','工单反馈仅本租户管理员可见反馈。','body')

def test_ready_review_cannot_bypass_selected_source_assertion(tmp_path):
    from tests.test_semantic_quality_regressions import project
    from tests.test_semantic_quality_boundaries import review_for_checks
    from app.semantic_quality import validate_review
    p=project(tmp_path,SOURCE);i=p['items'][0]
    i['behavior']={'permissions':'仅本租户管理员可见反馈。'}
    value=review_for_checks(p);value['result']['assessment']='ready_for_human_review'
    before=copy.deepcopy((p,value))
    with pytest.raises(Problem) as e:validate_review(value,p)
    assert e.value.code=='SEMANTIC_BLOCKED' and 'permissions' in e.value.message
    assert (p,value)==before


@pytest.mark.parametrize('text,blocked',[
 ('假设资源申请旧流程逐项录入，批量申请减少逐项录入操作；尚待核对。',False),
 ('若当前资源申请是人工录入，批量申请减少人工录入；前提尚未确认。',False),
 ('方案建议：提供批量资源申请，减少逐项提交；现状待验证。',False),
 ('假设工单流程为逐项提交，云资源批量申请减少逐项提交操作。',True),
 ('假设云资源现状逐项提交。批量申请减少逐项提交操作。',True),
 ('批量申请减少逐项提交操作；收益大小待验证。',True),
 ('批量申请减少逐项提交操作；现有流程尚未确认。',True),
])
def test_old_flow_hypothesis_is_local_not_a_global_disclaimer(text,blocked):
    assert bool(text_issues(text,[dict(text=SOURCE,ref=None)],'summary',hint='资源'))==blocked

def test_cross_clause_hypothesis_still_cannot_be_adopted_as_rule():
    p,v,ex=case(NORMAL);i=v['proposals'][0]
    i['statement']='假设云资源旧流程为逐项提交，批量申请减少逐项提交操作。'
    validate_response(v,'ingest',p,ex)
    with pytest.raises(Problem):validate_item(i,p)
