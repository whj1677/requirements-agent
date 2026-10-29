"""Apply validated model output to a project snapshot without dispatching work."""
import copy
import re

from .core import brief_hash, digest, ident, now, require, ui_view_hash
from .document_schema import PROFILES
from .contracts import review_target
from .requirements import TRACKED, namespace, requirement_ref, delivery_items
from .understanding import question_ids, save_questions, annotate_candidates

PREFIX = {'requirement':'REQ','rule':'RULE','acceptance':'AC','ui_decision':'UID','goal':'GOAL','actor':'ROLE'}


def replace_refs(value, mapping):
    if isinstance(value, str):
        if value in mapping:return mapping[value]
        if not mapping:return value
        pattern=r'(?<![\w-])(?:'+'|'.join(re.escape(k) for k in sorted(mapping,key=len,reverse=True))+r')(?![\w-])'
        return re.sub(pattern,lambda m:mapping[m.group()],value)
    if isinstance(value, list):
        return [replace_refs(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k:replace_refs(v,mapping) for k,v in value.items()}
    return value


def replace_business_ids(value, mapping):
    """Map program-owned identities, never arbitrary words in business text."""
    scalar = {'temp_id', 'target_item_id', 'target_question_id', 'requirement_id',
              'candidate_temp_id', 'existing_item_id', 'existing_question_id'}
    plural = {'related_refs', 'answer_refs', 'candidate_temp_ids', 'question_temp_ids',
              'depends_on', 'rule_refs', 'acceptance_refs'}
    if isinstance(value, list):
        return [replace_business_ids(child, mapping) for child in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in ('raw', 'raw_summary'):
            result[key] = copy.deepcopy(child)
        elif key in scalar and isinstance(child, str):
            result[key] = mapping.get(child, child)
        elif key in plural and isinstance(child, list):
            result[key] = [mapping.get(item, item) if isinstance(item, str) else item for item in child]
        else:
            result[key] = replace_business_ids(child, mapping)
    return result


def apply_response(p, response, *, identity_only=False, preserve_context_proposal=False):
    mapping = {x['temp_id']:ident(PREFIX.get(x['kind'], 'ITEM')) for x in response['proposals']}
    mapping.update(question_ids(p,response['questions'],mapping))
    r = (replace_business_ids if identity_only else replace_refs)(response, mapping)
    if 'benchmark_analysis' in r:p['benchmark_analysis']=r['benchmark_analysis']
    valid={x['id'] for x in p['items']}|{q['id'] for q in p['questions']}|set(mapping.values())
    for row in r['proposals']+[child for parent in r['questions'] for child in (parent.get('decision_points') or [parent])]:
        require(set(row.get('related_refs',[]))<=valid,'REFERENCE_INVALID','问题或候选存在未落地关联')
    if r.get('product_context_proposal') and not preserve_context_proposal:
        p['product_context_proposal']=r['product_context_proposal']
        # Old field citations must not be attached to a later unrelated summary.
        p.pop('product_context_proposal_evidence', None)
    for item in r['proposals']:
        # A proposed revision is a separate candidate, never an overwrite.
        identity=dict(content_version=1,source_namespace=namespace(p)) if item['kind'] in TRACKED else {}
        row=dict(id=item['temp_id'], **{k:v for k,v in item.items() if k != 'temp_id'}, **identity, selection_status='candidate', revision=p['revision']+1)
        if row['kind']=='requirement':
            row.setdefault('change_type','unspecified')
            row['behavior_review_required']=True
        p['items'].append(row)
    save_questions(p,r['questions'])
    known={x['id'] for x in p['items']}|{q['id'] for q in p['questions']}
    for row in p['items'][-len(r['proposals']):] if r['proposals'] else []:
        require(set(row.get('related_refs',[]))<=known,'REFERENCE_INVALID','候选存在悬空关联')
    annotate_candidates(p,p['items'][-len(r['proposals']):] if r['proposals'] else [])
    stage = r['stage']
    if stage == 'brainstorm':
        for option in r['result']['options']:
            p['options'].append(dict(option, id=ident('OPT'), selection_status='candidate'))
    if stage == 'ui':
        next_ui = dict(spec=r['result']['spec'], brief_hash=brief_hash(p), created=now(),
            requirement_versions=[requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED])
        if p['ui'] and ui_view_hash(p['ui']['spec']) != ui_view_hash(next_ui['spec']):
            p.setdefault('ui_candidates', []).append(dict(id=ident('UIC'), **next_ui,
                base_ui_hash=digest(p['ui']['spec']), status='candidate'))
        elif not p['ui']:
            p['ui'] = next_ui
        elif p['ui']['brief_hash'] != next_ui['brief_hash']:
            # A regenerated identical view verifies the new brief without replacing its visual version.
            p['ui']['brief_hash'] = next_ui['brief_hash']
            p['ui']['requirement_versions']=next_ui['requirement_versions']
            p['ui']['verification'] = dict(brief_hash=next_ui['brief_hash'],
                draft_revision=p['revision'], generated_spec_hash=digest(next_ui['spec']), created=next_ui['created'])
    if stage == 'prd':
        kind = r['result']['document_type']
        previous_document=copy.deepcopy(p['documents'].get(kind))
        document_version=p['documents'].get(kind,{}).get('document_version',0)+1
        p['documents'][kind] = dict(id=ident('DOC'), content=r['result'], requirement_name=p['name'], question_snapshot=copy.deepcopy(p['questions']), brief_hash=brief_hash(p), draft_revision=p['revision'], item_snapshot=copy.deepcopy(p['items']), created=now(), style_version='1', generator_version='1.1', reference_hashes={s['reference_id']:s['sha256'] for s in PROFILES['sources']} if p.get('reference_mode')!='builtin' else {}, limitations=r['limitations'])
        p['documents'][kind]['source_snapshot']=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','sha256')} for s in p['sources']]
        p['documents'][kind]['product_context_snapshot']=copy.deepcopy(p.get('product_context',{}))
        from .business_context import model_context
        if model_context(p):p['documents'][kind]['business_context_snapshot']=copy.deepcopy(model_context(p))
        from .prd import verified_business_context
        cited={ref for section in r['result']['sections'] for block in section['blocks']
               for ref in block.get('evidence_refs',{}).get('business_claim_refs',[])}
        if cited:
            facts=verified_business_context(p)
            p['documents'][kind]['business_fact_snapshot']={ref:copy.deepcopy(facts[ref]) for ref in cited}
        p['documents'][kind].update(document_version=document_version,document_family_id=p['id']+':'+kind,
            delivery_item_ids=[i['id'] for i in delivery_items(p)],
            requirement_versions=[requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED])
        from .prewrite import document_change_summary, status as prewrite_status
        p['documents'][kind]['base_document_id']=(previous_document or {}).get('id')
        p['documents'][kind]['change_summary']=document_change_summary(previous_document,p['documents'][kind])
        if kind=='prd' and p.get('prewrite_review'):
            p['documents'][kind]['prewrite_snapshot']=prewrite_status(p)
    if stage == 'review':
        p['review'] = dict(target_hash=review_target(p), response=r, created=now())
    p['messages'].append(dict(role='assistant', stage=stage, text=r['summary'], response=r, created=now()))
    return mapping
