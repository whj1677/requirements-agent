"""Synthetic protocol checks only; no model/business acceptance oracle."""
import copy
import pytest
from app import detail_semantics as review, detail_review_repair as repair
from app.core import Problem,digest
from tests.test_detail_semantics import fixture,model_response


def sample(tmp_path):
    p,local,excerpts,raw,canonical,report,_=fixture(tmp_path)
    from tests.test_detail_semantic_workflow import generated_detail
    raw['requirements'][0]['detail_contract']=generated_detail()
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']['required']=dict(status='source',sources=[dict(source='S1',locator='L1')])
    from app.business_draft import adapt
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts,protocol_version=3)
    good=model_response(plan)
    review.validate(plan,good)
    return plan,good


def patch_for(packet,good):
    return dict(protocol=repair.PROTOCOL,plan_hash=packet['plan_hash'],review_hash=packet['review_hash'],
        replacements=[dict(slot_id=repair.slot_id(s),original_hash=s['original_hash'],
            replacement=copy.deepcopy(good['rows'][s['row_index']][s['group']][s['index']])) for s in packet['slots']])


def broken(good):
    bad=copy.deepcopy(good)
    bad['rows'][0]['source_checks'][0]['source_quote_keys']=[]
    trace=next(c for c in bad['rows'][0]['checks'] if c['traces'])['traces'][0]
    trace['edge_ids']=['E1','E3','E2']
    return bad


def test_multiple_defects_one_patch_preserves_untouched_values_and_hashes(tmp_path):
    plan,good=sample(tmp_path);bad=broken(good);before=copy.deepcopy((plan,bad))
    with pytest.raises(Problem):review.validate(plan,bad)
    packet=repair.prepare(plan,bad)
    codes={e['code'] for s in packet['slots'] for e in s['errors']}
    assert {'trace_disconnected','source_binding'}<=codes
    merged,outcomes,audit=repair.apply(plan,packet,patch_for(packet,good))
    assert merged==good and outcomes['R1']['status']=='passed'
    assert (plan,bad)==before and audit['full_strict_validation']=='passed'
    assert len(audit['replacements'])==2 and audit['preserved_slots']
    for item in audit['preserved_slots']:
        ri,g,ci=item['slot_id'].split(':')
        assert item['hash']==digest(bad['rows'][int(ri)][g][int(ci)])
    assert repair.prepare(plan,good) is None


@pytest.mark.parametrize('fault',['plan_hash','review_hash','slot_hash','extra','duplicate','identity','candidate_change','packet_slots','cross_row'])
def test_patch_cannot_change_frozen_identity_or_unauthorized_entries(tmp_path,fault):
    plan,good=sample(tmp_path);packet=repair.prepare(plan,broken(good));patch=patch_for(packet,good)
    if fault in ('plan_hash','review_hash'):patch[fault]='tampered'
    elif fault=='slot_hash':patch['replacements'][0]['original_hash']='tampered'
    elif fault=='extra':patch['replacements'].append(copy.deepcopy(patch['replacements'][0]))
    elif fault=='duplicate':patch['replacements'][1]=copy.deepcopy(patch['replacements'][0])
    elif fault=='identity':patch['replacements'][0]['replacement']['scope']='I-other'
    elif fault=='candidate_change':plan['rows'][0]['detail_contract']['interactions'][0]['name']='changed'
    elif fault=='packet_slots':packet['slots'][0]['errors']=[]
    elif fault=='cross_row':patch['replacements'][0]['slot_id']='1:checks:0'
    with pytest.raises(Problem):repair.apply(plan,packet,patch)


@pytest.mark.parametrize('fault',['pool_hash','candidate_hash','missing','duplicate','row_extra'])
def test_global_identity_defects_do_not_authorize_partial_repair(tmp_path,fault):
    plan,good=sample(tmp_path);bad=broken(good)
    if fault=='pool_hash':bad['quote_pool_hash']='bad'
    elif fault=='candidate_hash':bad['rows'][0]['candidate_hash']='bad'
    elif fault=='missing':bad['rows'][0]['checks'].pop()
    elif fault=='duplicate':bad['rows'][0]['checks'][1]=copy.deepcopy(bad['rows'][0]['checks'][0])
    elif fault=='row_extra':bad['rows'][0]['extra']=True
    assert repair.prepare(plan,bad) is None


def test_model_may_reject_source_instead_of_program_selecting_source(tmp_path):
    plan,good=sample(tmp_path);bad=copy.deepcopy(good);check=bad['rows'][0]['source_checks'][0];check['source_quote_keys']=[]
    packet=repair.prepare(plan,bad);patch=patch_for(packet,good)
    replacement=patch['replacements'][0]['replacement']
    replacement.update(verdict='unsupported',source_quote_keys=[],affected_keys=[check['key']],correction='引用不支持此断言，需业务纠正。')
    merged,result,_=repair.apply(plan,packet,patch)
    assert result['R1']['status']=='failed' and merged['rows'][0]['source_checks'][0]['verdict']=='unsupported'


def test_failed_patch_keeps_assembled_evidence_and_is_still_rejected(tmp_path):
    plan,good=sample(tmp_path);bad=broken(good);packet=repair.prepare(plan,bad);patch=patch_for(packet,bad);assembly={}
    with pytest.raises(Problem):repair.apply(plan,packet,patch,assembly=assembly)
    assert assembly['merged_detail_review']==bad
    assert assembly['review_entry_repair']['full_strict_validation']=='pending'


def test_normal_coverage_authorizes_only_two_related_checks(tmp_path):
    plan,good=sample(tmp_path);bad=copy.deepcopy(good)
    for c in bad['rows'][0]['checks']:
        for t in c['traces']:t['kind']='exception'
    packet=repair.prepare(plan,bad)
    assert len(packet['slots'])==2
    assert all(s['group']=='checks' and s['errors'][0]['code']=='normal_coverage' for s in packet['slots'])
    repair.apply(plan,packet,patch_for(packet,good))


@pytest.mark.parametrize('case',['boundary','boundary_first','recovery_normal','recovery_unknown'])
def test_legal_recovery_and_shared_boundary_are_not_marked_defective(case):
    from tests.test_interaction_trace_boundary import review_boundary_case,shared_recovery_case
    if case.startswith('recovery'):
        plan,expanded,recovery=shared_recovery_case()
        recovery['traces'][0]['kind']=case.split('_')[1]
    else:
        plan,expanded=review_boundary_case()
        if case=='boundary_first':expanded['rows'][0]['checks'].sort(key=lambda c:c['scope']!='detail')
    plan['version']=review.POOL_VERSION
    plan['quote_pool']=review.quote_pool(plan);plan['quote_pool_hash']=digest(plan['quote_pool'])
    for row in plan['rows']:
        row['candidate_hash']=digest(row['detail_contract']);row['source_claims']=[]
        row['property_refs']={f'P{i:03d}':dict(path=p,value=v) for i,(p,v) in enumerate(sorted(row['property_text'].items()),1)}
    value=model_response(plan,expanded)
    assert review.validate(plan,value)['R1']['status']=='passed'
    assert repair.prepare(plan,value) is None
