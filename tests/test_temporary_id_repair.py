"""Synthetic alpha-renaming cases; no live business output is a test fixture."""
import copy

import pytest

from app.contracts import VALIDATOR
from app.core import Problem
from app.ingest import preserve_response, response_anchor, temporary_id_repair


EXCERPTS=[dict(source_id='SRC-synthetic',id='EX-synthetic',text='合成规则依据。'),
          dict(source_id='SRC-other',id='EX-other',text='另一份合成规则依据。')]


def question(temp_id='QTMP-question'):
    return dict(temp_id=temp_id,topic_key='synthetic-decision',question='边界是否允许？',
                why='影响合成验收',options=['允许','拒绝'],blocking=True,blocking_stage=3,
                related_refs=['RULE-original'],source_refs=[])


def response():
    proposal=dict(temp_id='TMP-rule-ab',action='revise',target_item_id='RULE-original',
                  kind='rule',title='合成规则',statement='只有已勾选记录参与导出。',
                  applies_to='to_be',epistemic_status='reported',
                  source_refs=[dict(source_id='SRC-synthetic',excerpt_id='EX-synthetic')],
                  related_refs=['REQ-original'],answer_refs=['Q-original'])
    value=dict(schema_version='1.1',stage='clarify',summary='合成澄清，不代表正式采纳。',
               proposals=[proposal,dict(proposal,temp_id='TMP-other',target_item_id='RULE-other',
                                        statement='空选择时提示先选择记录。')],
               questions=[question()],findings=[],used_source_refs=copy.deepcopy(proposal['source_refs']),
               limitations=['仍需人工核对。'],result=dict(scope_summary='合成范围',
               outstanding_decisions=['边界规则待决定。'],next_focus='核对边界规则。'))
    assert VALIDATOR.is_valid(value)
    return value


def pair():
    corrected=response();original=copy.deepcopy(corrected)
    original['proposals'][0]['temp_id']='TMP-rule a-b'
    return original,corrected


def assert_preserved(original,corrected):
    before=copy.deepcopy(original);after=copy.deepcopy(corrected)
    anchor=response_anchor(original,EXCERPTS,'clarify')
    normalized=temporary_id_repair(original,corrected,'clarify')
    assert normalized==corrected and normalized is not corrected
    preserve_response(anchor,corrected,EXCERPTS,'clarify',original=original)
    assert original==before and corrected==after


def test_pure_invalid_temp_id_fix_preserves_complete_response_and_evidence():
    original,corrected=pair()
    assert_preserved(original,corrected)
    with pytest.raises(Problem,match='格式修复改变'):
        preserve_response(response_anchor(original,EXCERPTS,'clarify'),corrected,EXCERPTS,'clarify')


def test_declarations_and_explicit_temporary_references_must_move_together():
    original,corrected=pair()
    original['proposals'][1]['related_refs'].append(original['proposals'][0]['temp_id'])
    corrected['proposals'][1]['related_refs'].append(corrected['proposals'][0]['temp_id'])
    original['questions'][0]['related_refs'].append(original['proposals'][0]['temp_id'])
    corrected['questions'][0]['related_refs'].append(corrected['proposals'][0]['temp_id'])
    assert_preserved(original,corrected)
    corrected['questions'][0]['related_refs'][-1]=original['proposals'][0]['temp_id']
    assert temporary_id_repair(original,corrected,'clarify') is None


@pytest.mark.parametrize('location',['question','decision_point','long_id'])
def test_other_declared_temp_id_syntax_repairs(location):
    original=response();corrected=copy.deepcopy(original)
    if location=='question':original['questions'][0]['temp_id']='QTMP-que stion'
    elif location=='decision_point':
        original['questions'][0]['decision_points']=[question('QTMP-child-one'),question('QTMP-child-two')]
        corrected=copy.deepcopy(original)
        original['questions'][0]['decision_points'][0]['temp_id']='QTMP-child one'
    else:original['proposals'][0]['temp_id']='TMP-'+'a'*121
    assert_preserved(original,corrected)


@pytest.mark.parametrize('change',[
    'business_text','blocking','blocking_stage','source','stable_target','stable_question',
    'answer_reference','summary','limitation','result','add','remove','reorder',
    'collision','rename_valid_id','extra_field','still_invalid_id',
])
def test_temp_id_exception_cannot_hide_any_other_change(change):
    original,corrected=pair()
    if change=='business_text':corrected['proposals'][0]['statement']='所有记录参与导出。'
    elif change=='blocking':corrected['questions'][0]['blocking']=False
    elif change=='blocking_stage':corrected['questions'][0]['blocking_stage']=5
    elif change=='source':corrected['proposals'][0]['source_refs']=[dict(source_id='SRC-other',excerpt_id='EX-other')]
    elif change=='stable_target':corrected['proposals'][0]['target_item_id']='RULE-other'
    elif change=='stable_question':corrected['questions'][0]['target_question_id']='Q-other'
    elif change=='answer_reference':corrected['proposals'][0]['answer_refs']=['Q-other']
    elif change=='summary':corrected['summary']='已正式确认。'
    elif change=='limitation':corrected['limitations']=[]
    elif change=='result':corrected['result']['outstanding_decisions']=[]
    elif change=='add':corrected['proposals'].append(dict(corrected['proposals'][0],temp_id='TMP-extra'))
    elif change=='remove':corrected['proposals'].pop()
    elif change=='reorder':corrected['proposals'].reverse()
    elif change=='collision':corrected['proposals'][1]['temp_id']=corrected['proposals'][0]['temp_id']
    elif change=='rename_valid_id':corrected['proposals'][1]['temp_id']='TMP-renamed'
    elif change=='extra_field':corrected['unrecognized_field']='synthetic'
    else:corrected['proposals'][0]['temp_id']='TMP-still invalid'
    assert temporary_id_repair(original,corrected,'clarify') is None
    with pytest.raises(Problem,match='格式修复改变'):
        preserve_response(response_anchor(original,EXCERPTS,'clarify'),corrected,EXCERPTS,'clarify',original=original)


@pytest.mark.parametrize('change',['duplicate_original','stable_id_as_temp','reference_capture','extra_original_defect','narrative_rewrite'])
def test_ambiguous_or_nonlocal_identity_repairs_are_rejected(change):
    original,corrected=pair()
    if change=='duplicate_original':original['proposals'][1]['temp_id']=original['proposals'][0]['temp_id']
    elif change=='stable_id_as_temp':original['proposals'][0]['temp_id']='RULE-original'
    elif change=='reference_capture':
        original['questions'][0]['related_refs'].append(corrected['proposals'][0]['temp_id'])
        corrected['questions'][0]['related_refs'].append(corrected['proposals'][0]['temp_id'])
    elif change=='extra_original_defect':original['proposals'][0]['unrecognized_field']='synthetic'
    else:
        original['summary']='保留业务原句：'+original['proposals'][0]['temp_id']
        corrected['summary']='保留业务原句：'+corrected['proposals'][0]['temp_id']
    assert temporary_id_repair(original,corrected,'clarify') is None


def test_normal_format_repair_keeps_legacy_guard_when_original_has_other_defects():
    corrected=response();original=copy.deepcopy(corrected);original['unrecognized_field']=True
    anchor=response_anchor(original,EXCERPTS,'clarify')
    assert temporary_id_repair(original,corrected,'clarify') is None
    preserve_response(anchor,corrected,EXCERPTS,'clarify',original=original)
    corrected['questions'][0]['blocking']=False
    with pytest.raises(Problem,match='格式修复改变'):
        preserve_response(anchor,corrected,EXCERPTS,'clarify',original=original)


def test_mismatched_original_snapshot_cannot_replace_the_actual_repair_anchor():
    original,corrected=pair();anchor=response_anchor(original,EXCERPTS,'clarify')
    other=copy.deepcopy(original);other['proposals'][0]['statement']='所有记录参与导出。'
    corrected['proposals'][0]['statement']=other['proposals'][0]['statement']
    assert temporary_id_repair(other,corrected,'clarify') is not None
    with pytest.raises(Problem,match='格式修复改变'):
        preserve_response(anchor,corrected,EXCERPTS,'clarify',original=other)
