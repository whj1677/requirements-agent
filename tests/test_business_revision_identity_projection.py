"""Identity-only context projection; old revision candidates remain read-only."""
import copy
import json
import pytest
from app.business_draft import _item_input,_item_aliases,_aliases,assemble,adapt
from tests.test_business_draft import setup,draft_row,packet,business_value


def revision_project(tmp_path):
    p,scope,config,_,excerpts,_=setup(tmp_path)
    original=copy.deepcopy(p['items'][0])
    original.update(id='AC-original',kind='acceptance',action='add',target_item_id=None,
        related_refs=[scope['requirement_id']],statement='原验收原文')
    revision=copy.deepcopy(original)
    revision.update(id='AC-revision',action='revise',target_item_id=original['id'],
        selection_status='candidate',statement='旧修订候选原文')
    p['items'] += [original,revision]
    return p,scope,config,excerpts,original,revision


def test_revision_identity_preserves_values_aliases_and_editable_boundary(tmp_path):
    p,scope,config,excerpts,original,revision=revision_project(tmp_path);before=copy.deepcopy(p)
    aliases=_item_aliases(p);source_aliases,_,_=_aliases(excerpts)
    projected=_item_input(revision,source_aliases,aliases)
    assert projected['action']=='revise' and projected['item_ref']==aliases[revision['id']]
    assert projected['revision_target_ref']==aliases[original['id']]
    assert projected['reference_role']=='readonly_revision_candidate'
    assert projected['statement']==revision['statement'] and projected['behavior']==revision.get('behavior',{})
    messages,_,_=assemble(p,'clarify','',config,local_scope=scope)
    context=json.loads(messages[1]['content'][0]['text']);header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    row=context['current_rows'][0]
    assert aliases[original['id']] in row['editable_acceptance_refs']
    assert aliases[revision['id']] not in row['editable_acceptance_refs']
    assert aliases[revision['id']] in context['detail_reference_options']['acceptance_refs']
    candidates=[r for r in row['related_clauses'] if r['item_ref']==aliases[revision['id']]]
    assert len(candidates)==1 and candidates[0]['revision_target_ref']==aliases[original['id']]
    target_schema=header['schema']['properties']['requirements']['items']['properties']['acceptance']['items']['properties']['target_ref']
    assert target_schema['enum']==row['editable_acceptance_refs'] and p==before


def test_invalid_candidate_edit_is_not_remapped_to_original(tmp_path):
    p,scope,_,excerpts,original,revision=revision_project(tmp_path);before=copy.deepcopy(p)
    aliases=_item_aliases(p);row=draft_row()
    row['acceptance']=[dict(business_value('原验收修订。','fact'),target_ref=aliases[original['id']]),
        dict(business_value('旧候选修订。','fact'),target_ref=aliases[revision['id']])]
    canonical,report=adapt(packet(row),p,excerpts,'clarify',scope)
    assert report['rejected'] and not canonical['proposals'] and p==before


@pytest.mark.parametrize('action',[None,'add','revise','remove'])
def test_projection_does_not_invent_actions_or_unavailable_target_aliases(action):
    item=dict(id='AC-candidate',kind='acceptance',statement='原文',target_item_id='not-visible')
    if action is not None:item['action']=action
    before=copy.deepcopy(item);value=_item_input(item,{}, {'AC-candidate':'C23'})
    assert value.get('action')==action and ('action' in value)==(action is not None)
    if action=='revise':assert value['revision_target_ref']=='' and value['item_ref']=='C23'
    else:assert 'reference_role' not in value and 'revision_target_ref' not in value
    assert item==before
