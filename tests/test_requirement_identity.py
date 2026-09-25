import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import Problem
from app.store import Store
from app.requirements import exchange, validate_exchange, merge_exchange, requirement_ref


def requirement(rid='REQ-synthetic-one'):
    return dict(id=rid,kind='requirement',title='新增备注',statement='联系人可保存备注。',
        selection_status='candidate',applies_to='to_be',epistemic_status='reported',source_refs=[],related_refs=[])


def test_identity_survives_status_rename_and_content_revision(tmp_path):
    store=Store(tmp_path);p=store.create('联系人平台')
    with store.edit(p['id'],0,'合成新需求') as (p,_):p['items'].append(requirement())
    p=store.get(p['id']);item=copy.deepcopy(p['items'][0])
    assert item['content_version']==1
    for status in ('selected','rejected','candidate'):
        with store.edit(p['id'],p['revision'],'合成状态决定') as (p,_):p['items'][0]['selection_status']=status
        p=store.get(p['id'])
        assert requirement_ref(p,p['items'][0])==requirement_ref(p,item)
    with store.edit(p['id'],p['revision'],'合成修订') as (p,_):
        p['name']='新项目名称';p['items'][0]['statement']='联系人可保存选填备注。'
    p=store.get(p['id'])
    assert p['items'][0]['id']==item['id'] and p['items'][0]['content_version']==2
    assert p['items'][0]['source_namespace']==item['source_namespace']
    assert any(x['before'] and x['before']['statement']==item['statement'] for x in store.records(p['id'],'requirement_change'))


def test_exchange_roundtrip_conflict_and_many_to_many_are_not_test_pass(tmp_path):
    store=Store(tmp_path);p=store.create('合成交换')
    with store.edit(p['id'],0,'合成结构') as (p,_):
        p['items']=[requirement(),requirement('REQ-synthetic-child')]
    p=store.get(p['id'])
    a,b=(requirement_ref(p,i) for i in p['items'])
    p['requirement_relations']=[dict(type='refines',source=b,target=a),
        dict(type='verifies',source=dict(type='test_case',id='TC-1',version=1),target=a),
        dict(type='verifies',source=dict(type='test_case',id='TC-1',version=1),target=b),
        dict(type='verifies',source=dict(type='test_case',id='TC-2',version=1),target=b)]
    payload=exchange(p);validate_exchange(payload)
    assert payload['test_executions']==[]
    assert all(r['evidence_status']=='linked_only' for r in payload['relations'])
    merged=merge_exchange(payload,payload)
    assert len(merged['requirements'])==2 and merged==payload
    altered=copy.deepcopy(payload);altered['requirements'][0]['statement']='同号同版本另一个含义'
    with pytest.raises(Problem):merge_exchange(payload,altered)
    p['items'][0]['content_version']=2
    assert exchange(p)['relations'][0]['validity']=='needs_review'


def test_legacy_identity_is_read_only_and_parallel_revision_conflicts(tmp_path):
    store=Store(tmp_path);p=store.create('旧项目')
    p['items']=[requirement()];before=copy.deepcopy(p)
    assert exchange(p)['requirements'][0]['version']==1
    assert p==before
    def edit(text):
        try:
            with store.edit(p['id'],0,'并发写入') as (current,_):current['items'].append(requirement(text))
            return 'written'
        except Problem as e:return e.code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(edit,['REQ-a','REQ-b']))
    assert sorted(results)==['STALE_REVISION','written']
    assert len(store.get(p['id'])['items'])==1


def test_cross_project_duplicate_id_is_rejected_without_partial_write(tmp_path):
    store=Store(tmp_path);a=store.create('甲');b=store.create('乙')
    with store.edit(a['id'],0,'创建') as (p,_):p['items'].append(requirement())
    with pytest.raises(Problem) as error:
        with store.edit(b['id'],0,'冲突创建') as (p,_):p['items'].append(requirement())
    assert error.value.code=='IDENTITY_CONFLICT'
    assert store.get(b['id'])['items']==[] and store.get(b['id'])['revision']==0
    with pytest.raises(Problem) as malformed:validate_exchange({'exchange_version':'requirements-exchange-1'})
    assert malformed.value.code=='EXCHANGE_INVALID'


def test_temporary_refs_in_behavior_resolve_to_same_stable_identity():
    from app.workflow import replace_refs
    original={'permissions':'仅本人可修改（详见 TMP-rule-one）。','related_refs':['TMP-rule-one']}
    rewritten=replace_refs(original,{'TMP-rule-one':'RULE-permanent'})
    assert rewritten=={'permissions':'仅本人可修改（详见 RULE-permanent）。','related_refs':['RULE-permanent']}


def test_exchange_acceptance_link_from_either_side_is_one_unexecuted_relation(tmp_path):
    p=Store(tmp_path).create('双向关联')
    req=requirement();ac=dict(requirement('AC-one'),kind='acceptance')
    req['related_refs']=[ac['id']];p['items']=[req,ac]
    one=exchange(p);validate_exchange(one)
    assert len(one['relations'])==1 and one['relations'][0]['source']['id']==ac['id']
    ac['related_refs']=[req['id']]
    both=exchange(p)
    assert both['relations']==one['relations'] and both['test_executions']==[]
