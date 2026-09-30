"""Additive, versioned formal handoff scope; legacy exchange stays unchanged."""
import copy
import jsonschema
from .core import digest, require
from .requirements import TRACKED, delivery_items, item_content, requirement_ref
from .document_reader import reader_document

SCOPE_SCHEMA = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
 'title': 'Formal requirement handoff scope v1',
 'type': 'object',
 'additionalProperties': False,
 'required': ['scope_version',
              'project_id',
              'draft_revision',
              'baseline_id',
              'confirmation_id',
              'baseline_hash',
              'hashes',
              'entities',
              'documents',
              'downstream_status',
              'test_execution_status'],
 'properties': {'scope_version': {'const': 'requirements-delivery-scope-1'},
                'project_id': {'type': 'string', 'minLength': 1},
                'draft_revision': {'type': 'integer', 'minimum': 0},
                'baseline_id': {'type': 'string', 'minLength': 1},
                'confirmation_id': {'type': 'string', 'minLength': 1},
                'baseline_hash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                'hashes': {'type': 'object'},
                'entities': {'type': 'array',
                             'items': {'type': 'object',
                                       'additionalProperties': False,
                                       'required': ['type',
                                                    'namespace',
                                                    'id',
                                                    'version',
                                                    'status',
                                                    'content',
                                                    'content_hash',
                                                    'version_origin',
                                                    'exchange_file'],
                                       'properties': {'type': {'type': 'string', 'minLength': 1},
                                                      'namespace': {'type': 'string',
                                                                    'minLength': 1},
                                                      'id': {'type': 'string', 'minLength': 1},
                                                      'version': {'type': 'integer', 'minimum': 1},
                                                      'status': {'const': 'selected'},
                                                      'content': {'type': 'object',
                                                                  'required': ['kind',
                                                                               'title',
                                                                               'statement',
                                                                               'applies_to']},
                                                      'content_hash': {'type': 'string',
                                                                       'pattern': '^[a-f0-9]{64}$'},
                                                      'version_origin': {'enum': ['recorded',
                                                                                  'legacy_v1']},
                                                      'exchange_file': {'enum': ['requirements-exchange.json',
                                                                                 None]}}}},
                'documents': {'type': 'array',
                              'items': {'type': 'object',
                                        'additionalProperties': False,
                                        'required': ['type',
                                                     'id',
                                                     'version',
                                                     'content_hash',
                                                     'file',
                                                     'file_content_kind',
                                                     'file_content_hash'],
                                        'properties': {'type': {'enum': ['mrd', 'prd']},
                                                       'id': {'type': 'string', 'minLength': 1},
                                                       'version': {'type': ['integer', 'null'],
                                                                   'minimum': 1},
                                                       'content_hash': {'type': 'string',
                                                                        'pattern': '^[a-f0-9]{64}$'},
                                                       'file': {'type': 'string', 'minLength': 1},
                                                       'file_content_kind': {'const': 'reader_projection'},
                                                       'file_content_hash': {'type': 'string',
                                                                             'pattern': '^[a-f0-9]{64}$'}}}},
                'downstream_status': {'type': 'object',
                                      'additionalProperties': False,
                                      'required': ['frontend', 'backend', 'test_design'],
                                      'properties': {'frontend': {'const': 'unbound'},
                                                     'backend': {'const': 'unbound'},
                                                     'test_design': {'const': 'unbound'}}},
                'test_execution_status': {'const': 'not_run'}}}


def formal_scope(baseline):
    p = baseline['project']
    entities = []
    for item in delivery_items(p):
        content = item_content(item)
        entities.append(dict(**requirement_ref(p, item), status='selected',
            content=content, content_hash=digest(content),
            version_origin='recorded' if 'content_version' in item else 'legacy_v1',
            exchange_file='requirements-exchange.json' if item['kind'] in TRACKED else None))
    value = dict(scope_version='requirements-delivery-scope-1',
        project_id=p['id'], draft_revision=p['revision'], baseline_id=baseline['id'],
        confirmation_id=baseline['confirmation_id'], baseline_hash=digest(baseline['hashes']),
        hashes=copy.deepcopy(baseline['hashes']), entities=entities,
        documents=[dict(type=kind, id=doc['id'], version=doc.get('document_version'),
            content_hash=digest(doc['content']), file_content_hash=digest(reader_document(doc)), file=kind.upper()+'.content.json',
            file_content_kind='reader_projection') for kind,doc in sorted(p['documents'].items())],
        downstream_status=dict(frontend='unbound', backend='unbound', test_design='unbound'),
        test_execution_status='not_run')
    return validate_scope(value)


def validate_scope(value):
    errors=list(jsonschema.Draft202012Validator(SCOPE_SCHEMA).iter_errors(value))
    require(not errors, 'DELIVERY_SCOPE_INVALID', 'Formal delivery scope schema invalid')
    seen=set()
    for entity in value['entities']:
        require(entity['id'] not in seen, 'DELIVERY_SCOPE_INVALID', 'Duplicate formal identity')
        seen.add(entity['id'])
        require(entity['content_hash']==digest(entity['content']) and
            entity['type']==entity['content']['kind'] and
            entity['content'].get('applies_to')=='to_be',
            'DELIVERY_SCOPE_INVALID', 'Formal entity content/hash mismatch')
    require(value['baseline_hash']==digest(value['hashes']),
        'DELIVERY_SCOPE_INVALID', 'Baseline hash mismatch')
    return value
