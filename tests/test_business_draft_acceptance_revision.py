import copy
import pytest
from app.business_draft import adapt,_item_aliases
from app.workflow import apply_response
from tests.test_business_draft import setup,draft_row,packet


def fixture(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path);root=p['items'][0]
    old=dict(id='AC-TARGET',kind='acceptance',title='边界验收',statement='相邻时段不得首尾相接。',
        applies_to='to_be',epistemic_status='reported',selection_status='selected',source_refs=copy.deepcopy(root['source_refs']),related_refs=[root['id']],content_version=1)
    p['items'].append(old);row=draft_row();row['acceptance']=[dict(text='相邻时段允许首尾相接。',basis='recommendation',sources=[dict(source='S1',locator='L1')],target_ref=_item_aliases(p)[old['id']])]
    from app.requirement_recommendation import scope
    local=scope(p,dict(kind='item',id=root['id']))
    return p,local,excerpts,old,row


def test_explicit_acceptance_revision_retains_target_identity_until_confirmation(tmp_path):
    p,local,ex,old,row=fixture(tmp_path);before=copy.deepcopy(p)
    value,report=adapt(packet(row),p,ex,'clarify',local)
    assert not report['rejected'],report
    acs=[i for i in value['proposals'] if i['kind']=='acceptance']
    assert len(acs)==1 and acs[0]['action']=='revise' and acs[0]['target_item_id']==old['id']
    assert p==before
    apply_response(p,value,identity_only=True)
    assert next(i for i in p['items'] if i['id']==old['id'])==old
    assert next(i for i in p['items'] if i.get('target_item_id')==old['id'])['selection_status']=='candidate'


@pytest.mark.parametrize('fault',['unknown','other_kind','unrelated','duplicate'])
def test_acceptance_revision_cannot_guess_or_cross_scope(tmp_path,fault):
    p,local,ex,old,row=fixture(tmp_path)
    if fault=='unknown':row['acceptance'][0]['target_ref']='C999'
    if fault=='other_kind':old['kind']='rule'
    if fault=='unrelated':old['related_refs']=[]
    if fault=='duplicate':row['acceptance'].append(copy.deepcopy(row['acceptance'][0]))
    before=copy.deepcopy(p);value,report=adapt(packet(row),p,ex,'clarify',local)
    assert not value['proposals'] and report['rejected'][0]['code']=='BUSINESS_DRAFT_ACCEPTANCE_TARGET',report
    assert p==before


def test_unchanged_explicit_target_does_not_add_duplicate_acceptance(tmp_path):
    p,local,ex,old,row=fixture(tmp_path);row['acceptance'][0].update(text=old['statement'],basis='fact')
    value,report=adapt(packet(row),p,ex,'clarify',local)
    assert not report['rejected'] and not any(i['kind']=='acceptance' for i in value['proposals'])
