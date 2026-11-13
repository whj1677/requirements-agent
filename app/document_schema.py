"""Shared response schemas and document profiles, independent of orchestration."""
import copy
import hashlib

import jsonschema
from referencing import Registry, Resource

from .core import KIT, read_json, require

SCHEMA = read_json(KIT / 'examples/runtime_response.schema.json')
from .requirement_details import DETAIL_SCHEMA
SCHEMA['$defs']['Proposal']['properties']['detail_contract'] = copy.deepcopy(DETAIL_SCHEMA)
SCHEMA['$defs']['Proposal']['properties']['detail_required'] = {'type':'boolean'}
SCHEMA['$defs']['Finding']['properties'].update(
    topic_key={'type':'string','minLength':1,'maxLength':200},
    decision_question={'type':'string','maxLength':2000},
    decision_options={'type':'array','maxItems':4,'uniqueItems':True,'items':{'type':'string','minLength':1,'maxLength':1000}},
    recommended_answer={'type':'string','minLength':1,'maxLength':6000},
    evidence_quotes={'type':'array','maxItems':20,'items':dict(type='object',additionalProperties=False,
        required=['object_id','path','quote'],properties={
            'object_id':{'type':'string','minLength':1,'maxLength':120},
            'path':{'type':'string','minLength':1,'maxLength':400},
            'quote':{'type':'string','minLength':1,'maxLength':4000}})})
# These blocks are compiled locally from item snapshots, never requested from
# the model's plan schema. Historical document blocks remain byte-compatible.
_document_blocks = SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['items']['oneOf']
# Saved behavior fields allow 6000 characters each; cloud PRD joins two
# with one newline. Model-authored plan text remains capped independently.
next(block for block in _document_blocks if block.get('properties', {}).get('kind', {}).get('const') == 'explanation')['properties']['text']['maxLength'] = 12001
_document_blocks.append(dict(type='object',additionalProperties=False,
    required=['kind','ref_ids','text','flow','source_hash'],properties=dict(
        kind={'const':'flowchart'},ref_ids=dict(type='array',minItems=1,maxItems=1,items=dict(type='string')),
        text=dict(type='string'),flow=copy.deepcopy(DETAIL_SCHEMA['properties']['flow']),
        source_hash=dict(type='string',pattern='^[0-9a-f]{64}$'))))
_document_blocks.append(dict(type='object', additionalProperties=False,
    required=['kind','ref_ids','text','detail_group','detail_hash'], properties={
        'kind':{'const':'detail'},
        'ref_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':1},
        'text':{'type':'string'},
        'detail_group':{'enum':['fields','interactions','flow','state_transitions']},
        'detail_hash':{'type':'string','pattern':'^[0-9a-f]{64}$'}}))
_document_blocks.append(dict(type='object', additionalProperties=False,
    required=['kind','ref_ids','text','title','table_role','columns','rows','column_weights'], properties={
        'kind':{'const':'table'},
        # Saved questions can accumulate links across multiple legal updates.
        # References are bounded by the current project's existing IDs in
        # validate_response; retain every link without duplicating risk rows.
        'ref_ids':{'type':'array','items':{'type':'string'},'uniqueItems':True},
        'text':{'type':'string','maxLength':200},
        'title':{'type':'string','minLength':1,'maxLength':200},
        'table_role':{'enum':['function_inventory','risk_register']},
        'columns':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'string','minLength':1,'maxLength':100}},
        'rows':{'type':'array','maxItems':200,'items':{'type':'array','maxItems':6,'items':{'type':'string','maxLength':12000}}},
        'column_weights':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'number','exclusiveMinimum':0,'maximum':1}}}))
from .benchmark import analysis_schema
SCHEMA['properties']['benchmark_analysis'] = analysis_schema()
WIRE = read_json(KIT / 'examples/wireframe.schema.json')
REGISTRY = Registry().with_resource('wireframe.schema.json', Resource.from_contents(WIRE))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, registry=REGISTRY)
try:
    PROFILES = read_json(KIT / 'references/content_profiles.json')
except (OSError, ValueError):
    PROFILES = {'profiles':[], 'sources':[]}


def profile(kind, fallback=False):
    if fallback:
        return dict(id='builtin-'+kind,version='1.1',document_type=kind,notice='用户明确选择内置参考；未按原 Word 参考生成',sections=[dict(id=kind.upper()+'-BUILTIN-'+str(n),reference_heading=title,content_dimension=title,mapping_required=True,repeat_per_function=False) for n,title in enumerate(['背景与目标','本期范围与非目标','功能 规则与验收','重要未知与风险'],1)])
    p = next((x for x in PROFILES['profiles'] if x['document_type'] == kind),None)
    require(p is not None,'REFERENCE_UNAVAILABLE','章节参考 profile 读取失败；可在文档区明确选择内置回退后继续')
    src = next(x for x in PROFILES['sources'] if x['reference_id'] == p['source_reference_id'])
    path = KIT / src['file']
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == src['sha256'],
            'REFERENCE_UNAVAILABLE', '原始章节参考不可用；其它阶段可继续，不能声称按该模板成文')
    return p
