"""Formal handoff acceptance: frozen scope, constraints and legacy compatibility."""
import copy
import hashlib
import json
import pytest
from app.core import Problem, digest, hashes
from app.delivery_scope import formal_scope, validate_scope
from app.exports import handoff_files
from app.requirements import exchange, validate_exchange, item_content
from app.store import Store
from tests.helpers import prepared


def baseline(tmp_path):
    p=prepared(Store(tmp_path))
    constraint=copy.deepcopy(p['items'][1])
    constraint.update(id='CONSTRAINT-SYNTHETIC', kind='constraint',
        title='Synthetic tenant boundary', statement='Reject cross-tenant actions.',
        content_version=3, related_refs=[])
    p['items'].append(constraint)
    candidate=copy.deepcopy(constraint)
    candidate.update(id='CANDIDATE', selection_status='candidate', statement='Unadopted statement.')
    p['items'].append(candidate)
    outside=copy.deepcopy(p['items'][0]);outside.update(id='OUTSIDE',related_refs=[])
    p['items'].append(outside)
    existing=copy.deepcopy(constraint);existing.update(id='AS-IS',applies_to='as_is')
    p['items'].append(existing)
    p['product_context']={'scope_ids':['REQ-0001']}
    return dict(id='BASE-SYNTHETIC',confirmation_id='CONF-SYNTHETIC',project=p,hashes=hashes(p))


def test_scope_preserves_formal_constraints_and_exact_identity(tmp_path):
    b=baseline(tmp_path);value=formal_scope(b)
    assert {e['id'] for e in value['entities']}=={'REQ-0001','RULE-0001','AC-0001','CONSTRAINT-SYNTHETIC'}
    c=next(e for e in value['entities'] if e['type']=='constraint')
    assert c['version']==3 and c['exchange_file'] is None
    assert c['content']['statement']=='Reject cross-tenant actions.'
    assert c['content_hash']==digest(item_content(b['project']['items'][3]))
    assert value['baseline_id']==b['id'] and value['confirmation_id']==b['confirmation_id']
    assert value['baseline_hash']==digest(b['hashes'])
    assert value['test_execution_status']=='not_run'


def test_handoff_scope_manifest_and_old_exchange_unchanged(tmp_path):
    b=baseline(tmp_path);before=exchange(b['project'],b['id']);files,manifest=handoff_files(b,[])
    legacy=json.loads(files['requirements-exchange.json'])
    assert {k:v for k,v in legacy.items() if k!='change_records'}==before
    validate_exchange(legacy)
    assert not any(e['type']=='constraint' for e in legacy['requirements'])
    scope=validate_scope(json.loads(files['delivery_scope.json']))
    assert manifest['schema_version']=='1.2'
    assert manifest['delivery_scope_version']==scope['scope_version']
    for name,record in manifest['artifacts'].items():
        assert hashlib.sha256(files[name]).hexdigest()==record['sha256']
    import jsonschema
    jsonschema.Draft202012Validator(json.loads(files['delivery_scope.schema.json'])).validate(scope)
    for doc in scope['documents']:
        assert doc['file_content_hash']==digest(json.loads(files[doc['file']]))
        assert doc['content_hash']==digest(b['project']['documents'][doc['type']]['content'])


def test_frozen_baseline_and_historic_legacy_version_are_explicit(tmp_path):
    b=baseline(tmp_path);b['project']['items'][0].pop('content_version',None);old=copy.deepcopy(b)
    b['project']['items'][3]['statement']='New unconfirmed edit.'
    scope=formal_scope(old)
    assert next(e for e in scope['entities'] if e['type']=='constraint')['content']['statement']=='Reject cross-tenant actions.'
    req=next(e for e in scope['entities'] if e['type']=='requirement')
    assert req['version']==1 and req['version_origin']=='legacy_v1'


@pytest.mark.parametrize('mutation', ['hash','baseline','duplicate','status','type','as_is'])
def test_invalid_scope_is_rejected(tmp_path,mutation):
    value=formal_scope(baseline(tmp_path));entity=value['entities'][0]
    if mutation=='hash':entity['content']['statement']='Tampered.'
    elif mutation=='baseline':value['baseline_hash']='0'*64
    elif mutation=='duplicate':value['entities'].append(copy.deepcopy(entity))
    elif mutation=='status':entity['status']='candidate'
    elif mutation=='type':entity['type']='constraint'
    else:
        entity['content']['applies_to']='as_is';entity['content_hash']=digest(entity['content'])
    with pytest.raises(Problem) as raised:validate_scope(value)
    assert raised.value.code=='DELIVERY_SCOPE_INVALID'
