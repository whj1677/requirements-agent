"""Bounded document planning contract; canonical text and bookkeeping stay local."""
import copy
import jsonschema
from .core import digest, require
from .contracts import profile, describe_schema_error

NORMATIVE = ('requirement', 'rule', 'acceptance')
REFS = dict(type='array', items=dict(type='string', minLength=1, maxLength=120), maxItems=100, uniqueItems=True)
PLAN_SCHEMA = {
    '$schema':'https://json-schema.org/draft/2020-12/schema', 'type':'object',
    'required':['plan_version','title','sections','limitations'], 'additionalProperties':False,
    'properties':{
        'plan_version':{'const':'1'}, 'title':dict(type='string',minLength=1,maxLength=200),
        'limitations':dict(type='array',items=dict(type='string',maxLength=1000),maxItems=20),
        'sections':dict(type='array',minItems=1,maxItems=12,items={
            'type':'object','additionalProperties':False,
            'required':['title','normative_refs','discussion_refs','narration'],
            'properties':{'title':dict(type='string',minLength=1,maxLength=200),
                'normative_refs':REFS,'discussion_refs':REFS,
                'narration':dict(type='string',maxLength=1200)}})}}


def allowed(item):
    return item['kind'] in NORMATIVE and item['selection_status']=='selected' and item['applies_to']=='to_be'


def context(p):
    return dict(
        A_normative={kind:[copy.deepcopy(i) for i in p['items'] if allowed(i) and i['kind']==kind] for kind in NORMATIVE},
        B_statements_answers=dict(items=[copy.deepcopy(i) for i in p['items'] if not allowed(i) and i['selection_status']=='selected' and i['epistemic_status'] not in ('proposed','inferred')],
            answers=[copy.deepcopy(q) for q in p['questions'] if q['status']=='answered'],
            notice='selected仅为当前草稿采纳，不代表业务负责人批准；原条目和后续回答可能存在差异，必须并列保留。'),
        C_discussion=dict(items=[copy.deepcopy(i) for i in p['items'] if not allowed(i) and (i['selection_status']!='selected' or i['epistemic_status'] in ('proposed','inferred'))],
            options=copy.deepcopy(p['options']), notice='讨论方向不等于采纳条目；建议和假设不成为规范。'),
        D_unknowns_limits=dict(questions=[copy.deepcopy(q) for q in p['questions'] if q['status']!='answered'],
            source_status=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','excluded')} for s in p['sources']],
            ui='尚无原型' if not p.get('ui') else '已有低保真模拟原型，不能证明业务实现',
            recorded_limitations=[x for m in p['messages'] for x in (m.get('response') or {}).get('limitations',[])]))


def compile_plan(plan, p, kind, omitted=()):
    errors=list(jsonschema.Draft202012Validator(PLAN_SCHEMA).iter_errors(plan))
    require(not errors,'SCHEMA_INVALID','成文章节建议错误：'+(describe_schema_error(errors) if errors else ''))
    items={i['id']:i for i in p['items']}
    sections=[]; coverage={}; discussed=set()
    def section(title,blocks):
        sid=kind.upper()+'-'+digest(dict(title=title,index=len(sections)))[:12]
        sections.append(dict(section_id=sid,title=title,level=1,parent_section_id=None,blocks=blocks))
        return sid
    def text(value,refs=None,block_kind='narrative'):
        return dict(kind=block_kind,ref_ids=refs or [],text=value)
    def provenance(i):
        return '；来源：'+ ('、'.join(r['source_id']+'/'+r['excerpt_id'] for r in i.get('source_refs',[])) or '未提供')
    def discussion(i):
        status='未采纳' if i['selection_status']!='selected' else '已在草稿采纳，不代表业务负责人批准'
        return text(f'【{status}；{i["epistemic_status"]}；{i["applies_to"]}】{i["id"]} — {i["statement"]}'+provenance(i),[i['id']])
    for n,s in enumerate(plan['sections']):
        blocks=[]
        for j,r in enumerate(s['normative_refs']):
            at=f'sections[{n}].normative_refs[{j}]'
            require(r in items,'REFERENCE_INVALID',at+' 未知条目 '+r)
            require(allowed(items[r]),'SEMANTIC_BLOCKED',at+' '+r+' 不在本期已选规范白名单；需业务决定，不能自动采纳或改写为确定性叙述')
            blocks.append(dict(kind=items[r]['kind'],ref_ids=[r],text=None))
            blocks.append(text(f'{r}：{items[r]["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(items[r]),[r]))
        for j,r in enumerate(s['discussion_refs']):
            require(r in items,'REFERENCE_INVALID',f'sections[{n}].discussion_refs[{j}] 未知条目 '+r)
            blocks.append(discussion(items[r]));discussed.add(r)
        if s['narration']:
            blocks.append(text('【模型讨论说明，未核实；不构成规范或批准】'+s['narration']))
        sid=section(s['title'],blocks)
        for r in s['normative_refs']:coverage.setdefault(r,[]).append(sid)
    # Preserve omitted context and all questions independently of the model's choices.
    remaining=[discussion(i) for i in p['items'] if i['id'] not in coverage and i['id'] not in discussed]
    if remaining:section('材料陈述、未成文条目与讨论建议',remaining)
    questions=[]
    for q in p['questions']:
        if q['status']=='answered':
            original='；'.join(items[r]['statement'] for r in q.get('related_refs',[]) if r in items)
            answer_sources=[s['id']+'/'+e['id'] for s in p['sources'] for e in s['excerpts'] if e['text']==q['answer']]
            questions.append(text(f'【已有回答；真实性及与旧条目的一致性待核对，不代表业务负责人批准】{q["question"]}\n回答：{q["answer"]}\n回答来源：'+('、'.join(answer_sources) or '未找到独立回答来源，见问题记录')+f'\n关联旧条目原文：{original or "无关联条目"}'+provenance(q),[q['id']]))
        else:questions.append(text(q['question']+'（尚未决定）'+provenance(q),[q['id']], 'open_question'))
    if questions:section('已有回答与未决问题',questions)
    prof=profile(kind,p.get('reference_mode')=='builtin')
    limits=['讨论稿，不构成正式确认；章节语义覆盖待核对。',context(p)['D_unknowns_limits']['ui']]
    limits+=plan['limitations']+context(p)['D_unknowns_limits']['recorded_limitations']
    limits+=['材料限制：'+s['id']+' '+s['parse_status']+' '+(s.get('failure_reason') or '') for s in p['sources'] if not s['excluded'] and s['parse_status']!='read']
    if omitted:limits.append('本轮未完整送入的来源片段：'+'、'.join(omitted))
    limits+=['未成文规范条目：'+i['id'] for i in p['items'] if allowed(i) and i['id'] not in coverage]
    pending=section('限制及参考维度待核对',[text(x,block_kind='open_question') for x in dict.fromkeys(limits)])
    maps=[]
    reqs=[i['id'] for i in p['items'] if allowed(i) and i['kind']=='requirement']
    picture='PRD-4.F.1' if kind=='prd' else 'MRD-5.1.F.3'
    for d in prof['sections']:
        if not d['mapping_required']:continue
        for scope in (reqs or [None]) if d['repeat_per_function'] else [None]:
            visible=bool(d['id']==picture and scope in coverage and p.get('ui') and any(scope in page['requirement_refs'] for page in p['ui']['spec']['pages']))
            maps.append(dict(profile_section_id=d['id'],scope_ref=scope,disposition='merged' if visible else 'pending',
                output_section_ids=coverage[scope] if visible else [pending],reason='实际原型关联到已成文功能；低保真模拟。' if visible else '该内容维度的语义覆盖尚待核对；不以空章节视为覆盖。'))
    document=dict(document_type=kind,content_profile_id=prof['id'],content_profile_version=prof['version'],title=plan['title'],
        sections=sections,coverage=[dict(item_id=r,section_ids=list(dict.fromkeys(ids))) for r,ids in coverage.items()],reference_mapping=maps)
    incomplete=['未成文规范条目：'+i['id'] for i in p['items'] if allowed(i) and i['id'] not in coverage]
    return dict(schema_version='1.1',stage='prd',summary='已组装需求讨论稿；请核对规范原文、回答与未知。',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=plan['limitations']+incomplete,result=document)
