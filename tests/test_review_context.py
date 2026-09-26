"""Review real readable versions once; never hide a contradiction to fit context."""
import copy
import json

import jsonschema
import pytest

from app.core import Problem, dumps
from app.prd import compile_plan
from app.provider import DEFAULT, assemble, request_schema
from app.store import Store
from app.workflow import apply_response
from tests.helpers import EXAMPLES, prepared


def documents(tmp_path):
    p=prepared(Store(tmp_path))
    p['questions'][0].update(status='answered',answer='相接边界允许。',understanding_status='applied')
    for kind in ('mrd','prd'):
        plan=dict(plan_version='2',sections=[dict(section_key='rules',context_refs=[],
            normative_refs=[i['id'] for i in p['items']],discussion_refs=[])])
        apply_response(p,compile_plan(plan,p,kind))
        # A stored artifact can contain an erroneous older/manual narrative.
        # The review input must expose it even though new plans cannot generate it.
        p['documents'][kind]['content']['sections'][0]['blocks'].append(dict(
            kind='narrative',ref_ids=[],
            text='管理员不得修改联系人权限；该表述仅为审查反例，不得被程序静默删除。'))
    return p


def test_review_fits_two_documents_without_losing_current_or_frozen_text(tmp_path):
    p=documents(tmp_path)
    # Full historical responses and snapshots used to exceed context without even
    # sending a request. They repeat facts already present in current structures.
    p['messages'].append(dict(role='assistant',stage='prd',created='synthetic',text='OLD_SUMMARY',response={'duplicate':'REPEATED_OLD_ARTIFACT'*10000}))
    old=p['items'][0]['statement']
    p['items'][0]['statement']='当前规则已变：只有授权管理员能够维护电价时段。'
    before=copy.deepcopy(p)
    messages,excerpts,omitted=assemble(p,'review','',dict(DEFAULT,max_tokens=16000),tmp_path)
    text=dumps(messages);ctx=json.loads(messages[1]['content'][0]['text'])
    assert p==before
    assert 'REPEATED_OLD_ARTIFACT' not in text and 'OLD_SUMMARY' not in text
    assert old in text and p['items'][0]['statement'] in text
    assert '相接边界允许。' in text
    assert '不得被程序静默删除' in ctx['documents']['mrd']['content']['sections'][0]['blocks'][-1]['text']
    assert set(ctx['documents'])=={'mrd','prd'}
    assert all(not doc['current'] for doc in ctx['documents'].values())
    assert not omitted and excerpts==p['sources'][0]['excerpts']
    for item in p['items']:
        assert item['id'] in text
    assert len(text)+16000*4+4000<DEFAULT['context_chars']


def test_review_schema_is_self_contained_and_cannot_propose_mutations():
    schema=request_schema('review')
    jsonschema.Draft202012Validator(schema).validate(EXAMPLES['review'])
    assert '$ref' not in dumps(schema) and 'wireframe.schema.json' not in dumps(schema)
    bad=copy.deepcopy(EXAMPLES['review'])
    bad['proposals']=[dict(temp_id='TMP-new',action='add',target_item_id=None,kind='requirement',
        title='越权新增',statement='审查阶段试图新增需求。',applies_to='to_be',epistemic_status='proposed',
        source_refs=[],related_refs=[])]
    with pytest.raises(jsonschema.ValidationError) as error:jsonschema.validate(bad,schema)
    assert error.value.validator=='const' and list(error.value.path)==['proposals']


def test_review_still_refuses_to_truncate_large_current_facts(tmp_path):
    p=documents(tmp_path)
    p['items'][0]['statement']='必须保留的当前规范原文'*22000
    with pytest.raises(Problem) as error:
        assemble(p,'review','',DEFAULT,tmp_path)
    assert error.value.code=='BUDGET_EXHAUSTED'
