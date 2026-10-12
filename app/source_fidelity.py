"""Four finite, owner-cited source witnesses; never rewrite or adopt content.

This is a review boundary, not a general entailment engine. Model suggestions
and review checkboxes cannot create evidence. No persistent schema is added.
"""
import re
from .core import Problem
from .result_conditions import assertion_scope

EXCLUSIVE = re.compile(r'(?:仅|只有|仅限)(?P<role>[^，,。；;：:\n]{1,18}?)(?:可见|可以查看|可查看|能查看|可以查阅|可查阅)(?P<object>[^，,。；;\n]{0,22})')
FEEDBACK = re.compile(r'反馈|拒绝原因|拒绝结果|失败原因')
BUDGET = re.compile(r'(?:预算|经费|资金)(?:不足|不满足|充足|足够|满足)|预算[^，,。；;\n]{0,5}(?:拒绝|验收)')
QUOTA = re.compile(r'配额|资源额度')
PRESENCE = re.compile(r'配额(?:值|字段|信息)?[^，,。；;\n]{0,8}?(?:不缺失|不为空|非空|齐全|完整|已存在|存在)')
METHOD = r'逐项|逐个|逐条|单项|手工|人工|线下|邮件'
ACTION = r'提交|申请|录入|保存|编辑|处理|审批'
BENEFIT = re.compile(r'(?:减少|降低|节省|避免|免去)[^，,。；;\n]{0,8}?(?P<mode>'+METHOD+r')[^，,。；;\n]{0,5}?(?P<action>'+ACTION+r')')
HISTORY = re.compile(r'(?:目前|当前|现有|既有|原有|原先|此前|现在)[^，,。；;\n]{0,24}?(?P<mode>'+METHOD+r')[^，,。；;\n]{0,6}?(?P<action>'+ACTION+r')')
TENTATIVE = re.compile(r'假设|假定|推测|建议|待确认|待核对|尚未确认|未决定|是否|可能|如果|倘若')
DENIAL = re.compile(r'没有(?:规定|依据|证据)|未(?:规定|明确|确认|证实)|不(?:声称|宣称|表示|意味着|代表|要求|认定)|不能(?:认定|证明)|无证据|删除|撤回|不要')
MODEL_BUDGET = re.compile(r'模型|LLM|Provider|token|请求预算|调用预算|测试预算|本轮预算',re.I)
TOPICS = re.compile(r'云资源|资源|工单|电价|联系人|备注|附件|文件|设备|报销|账号')
DIMENSIONS = dict(exclusive_visibility='反馈可见权限',budget_is_not_quota='业务预算与配额',existence_is_not_sufficiency='配额存在性与满足性',comparison_premise='旧流程比较前提')

def segments(text):
    from .semantic_quality import clauses
    return clauses(text or '')

def state(part,match):
    from .result_conditions import quoted_prior_assertion
    if quoted_prior_assertion(part,match.start()):return 'denied'
    prefix=part[:match.start()];inference=assertion_scope(part,match.start())
    if inference and inference['denied']:return 'denied'
    local_negation=re.search(r'(?:(?:不是|并非|没有|未|不)(?:能|可|会)?)+\s*$',prefix)
    if not inference and local_negation:
        if len(re.findall(r'不是|并非|没有|未|不',local_negation.group()))%2:return 'denied'
    elif not inference and DENIAL.search(prefix):return 'denied'
    if re.search(r'已撤回|未采纳|不是本期决定',part[match.end():]):return 'denied'
    if TENTATIVE.search(prefix) or '?' in part or '？' in part:return 'tentative'
    return 'asserted'

def topic(text,hint=None):
    match=TOPICS.search(text or '')
    return ('资源' if match.group()=='云资源' else match.group()) if match else hint

def mode(value):return '逐项' if value in ('逐个','逐条','单项') else '人工' if value=='手工' else value

def witnesses(text,hint=None):
    from .semantic_quality import METHOD_HYPOTHESIS
    result=[];cursor=0
    for part in segments(text):
        position=text.find(part,cursor);cursor=position+len(part)
        sentence_start=max([text.rfind(mark,0,position) for mark in ('。','；',';','\n')])+1
        prior=text[sentence_start:position]
        for m in EXCLUSIVE.finditer(part):
            if FEEDBACK.search(m['object']) or FEEDBACK.search(part[:m.start()]):result.append(dict(dimension='exclusive_visibility',key=(re.sub(r'^(?:本|该|同一)','',m['role'].strip()),'反馈',topic(part,hint)),quote=part,state=state(part,m)))
        for m in BUDGET.finditer(part):
            if not MODEL_BUDGET.search(part):result.append(dict(dimension='budget_is_not_quota',key=('budget',topic(part,hint)),quote=part,state=state(part,m)))
        for m in PRESENCE.finditer(part):
            if not re.search(r'不等于|不代表|不表示|不能(?:代替|等同)',part[m.end():]):result.append(dict(dimension='existence_is_not_sufficiency',key=('presence',topic(part,hint)),quote=part,state=state(part,m)))
        for m in BENEFIT.finditer(part):
            qualification=state(part,m)
            # Reuse the existing operation-premise hypothesis boundary; a
            # benefit amount disclaimer alone does not qualify the old flow.
            if qualification=='asserted' and METHOD_HYPOTHESIS.search(prior) and topic(prior,hint)==topic(part,hint):qualification='tentative'
            result.append(dict(dimension='comparison_premise',key=(mode(m['mode']),m['action'],topic(part,hint)),quote=part,state=qualification))
    return result

def support(claim,evidence,hint):
    for row in evidence:
        text=row['text']
        if claim['dimension']=='comparison_premise':
            for part in segments(text):
                for m in HISTORY.finditer(part):
                    key=(mode(m['mode']),m['action'],topic(part,topic(text,hint)))
                    if state(part,m)=='asserted' and key==claim['key']:return row
        else:
            if any(f['dimension']==claim['dimension'] and f['key']==claim['key'] and f['state']=='asserted' for f in witnesses(text,topic(text,hint))):return row
    return None

def text_issues(text,evidence,location,*,hint=None,adoption=False,normative=True):
    found=[];cited='\n'.join(r['text'] for r in evidence)
    for claim in witnesses(text,hint):
        if claim['state']=='denied':continue
        if claim['dimension'] in ('budget_is_not_quota','existence_is_not_sufficiency') and not QUOTA.search(cited):continue
        if claim['state']=='tentative' and not (adoption and normative):continue
        if support(claim,evidence,hint) and claim['state']=='asserted':continue
        found.append(dict(claim,location=location,source_refs=[r['ref'] for r in evidence if r.get('ref')],evidence=[dict(source_ref=r.get('ref'),locator=r.get('locator'),source_hash=r.get('source_hash'),quote=r['text']) for r in evidence],reason='unconfirmed_decision' if claim['state']=='tentative' else 'cited_source_does_not_support_assertion'))
    return found

def source_rows(p,excerpts,refs):
    pairs={(r.get('source_id'),r.get('excerpt_id')) for r in refs}
    excluded={s['id'] for s in p.get('sources',[]) if s.get('excluded') or s.get('superseded_by')}
    return [dict(text=e['text'],ref=dict(source_id=e['source_id'],excerpt_id=e['id']),locator=e.get('locator'),source_hash=e.get('source_hash')) for e in excerpts if (e['source_id'],e['id']) in pairs and e['source_id'] not in excluded]

def project_excerpts(p):return [dict(e,source_id=s['id']) for s in p.get('sources',[]) if not s.get('excluded') and not s.get('superseded_by') for e in s.get('excerpts',[])]

def item_issues(item,p,excerpts,*,adoption=False,decision_text=None):
    refs=list(item.get('source_refs',[]));ids={item.get('id'),item.get('temp_id'),item.get('target_item_id')}-{None}
    from .understanding import current_answer
    for q in p.get('questions',[]):
        if current_answer(q) and q['id'] in item.get('answer_refs',[]) and ids & set(q.get('related_refs',[])):refs+=q.get('answer_source_refs',[])
    evidence=source_rows(p,excerpts,refs)
    if decision_text:evidence.append(dict(text=decision_text,ref=None,locator='authenticated manual statement edit',source_hash=None))
    hint=topic(item.get('title','')+' '+item.get('statement',''));base=item.get('temp_id') or item.get('id') or 'item'
    fields=[('title',item.get('title','')),('statement',item.get('statement',''))]+[('behavior.'+k,v) for k,v in (item.get('behavior') or {}).items() if isinstance(v,str)]
    return [dict(f,object_id=base) for field,text in fields for f in text_issues(text,evidence,base+'.'+field,hint=hint,adoption=adoption)]

def raise_issues(found,*,adoption=False):
    if not found:return
    detail='；'.join(f"{f['location']} 原句『{f['quote']}』：{DIMENSIONS[f['dimension']]}缺少明确来源/绑定人工决定；引用="+','.join(r['source_id']+'/'+r['excerpt_id'] for r in f['source_refs']) for f in found[:4])
    raise Problem('SEMANTIC_BLOCKED','来源含义待确认：'+detail+'。请补真实来源或明确业务决定，或保留为显式待确认建议；程序不会删除/改写原文或代为采纳。',409 if adoption else 400,model_repair_allowed=False)

def validate_item(item,p,*,decision_text=None):raise_issues(item_issues(item,p,project_excerpts(p),adoption=True,decision_text=decision_text),adoption=True)

def question_bound(issue,questions):
    patterns=dict(exclusive_visibility=r'(?=.*(?:反馈|可见|查看))(?=.*(?:权限|只有|仅|排他))',budget_is_not_quota=r'(?=.*(?:预算|经费))(?=.*配额)',existence_is_not_sufficiency=r'(?=.*(?:存在|不缺失|非空))(?=.*(?:满足|足够))',comparison_premise=r'(?=.*(?:逐项|逐个|手工|单项))(?=.*(?:现有|既有|原|来源|假设|确认))')
    pairs={(r['source_id'],r['excerpt_id']) for r in issue['source_refs']}
    return any(q.get('blocking') and issue.get('object_id') in q.get('related_refs',[]) and pairs and pairs<={(r['source_id'],r['excerpt_id']) for r in q.get('source_refs',[])} and re.search(patterns[issue['dimension']],q.get('question','')) for q in questions)

def validate_response(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change','prd'):return
    from .understanding import leaves
    questions=leaves(value.get('questions',[]))+[q for q in p.get('questions',[]) if q.get('status')!='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    found=[f for i in value.get('proposals',[]) for f in item_issues(i,p,excerpts)]
    blocked=[f for f in found if not question_bound(f,questions)]
    evidence=source_rows(p,excerpts,value.get('used_source_refs',[]));hint=topic(' '.join(i.get('title','')+' '+i.get('statement','') for i in value.get('proposals',[])))
    prose=[('summary',value.get('summary','')),('result.understanding',value.get('result',{}).get('understanding',''))]+[('product_context_proposal.'+k,v) for k,v in value.get('product_context_proposal',{}).items() if isinstance(v,str)]
    blocked += [f for name,text in prose for f in text_issues(text,evidence,name,hint=hint,normative=False)]
    raise_issues(blocked)

def project_issues(p):
    ex=project_excerpts(p)
    found=[f for i in p.get('items',[]) if i.get('selection_status')=='selected' and i.get('applies_to')=='to_be' for f in item_issues(i,p,ex,adoption=True)]
    evidence=source_rows(p,ex,[dict(source_id=e['source_id'],excerpt_id=e['id']) for e in ex])
    return found+text_issues(p.get('product_context',{}).get('value',''),evidence,'product_context.value',hint=topic(str(p.get('product_context',{}))),normative=False)
