"""Finite, source-bound outcome conditions, not inferred business success rules.

Facts are an input-only projection. Original strings/identities are never edited.
An unqualified sufficient assertion is different from a named normal branch.
"""
import re
from .core import Problem

VERSION = 'result-conditions-1'
OPERATIONS = {'创建':'create','写入':'write','保存':'write','更新':'update','应用':'apply','删除':'delete'}
EFFECT = re.compile('|'.join(OPERATIONS))
GUARDS = {'quota':r'配额|额度', 'permission':r'权限', 'validation':r'校验|检查'}
POSITIVE = r'满足|充足|充裕|足够|通过|合格'
NEGATIVE = r'不满足|不足|不够|不通过|未通过|失败'
REJECTION = re.compile(r'(?:服务|系统|请求)[^，,。；;\n]{0,8}(?:明确)?(?:拒绝|失败)|(?:服务)?明确拒绝|拒绝受理')
UNKNOWN = re.compile(r'结果未知|未知结果|超时|响应丢失')
NORMAL = re.compile(r'正常(?:成功|处理|受理|结果)?分支|成功(?:分支|场景)|正常成功(?:场景|情况下)|'
                    r'服务(?:正常受理|已受理|受理成功|处理成功|执行成功)|已确认(?:成功|完成)')
NON_ASSERTION = re.compile(r'建议|假设|待(?:澄清|核对|确认)|未采纳|尚未决定|是否|能否|不保证|'
                          r'不能[^，,。；;\n]{0,12}(?:推断|认定|断言)|并非|不代表|不意味着|不蕴含')
SUBJECTS = re.compile(r'云资源|资源|工单|订单|记录|备注|时段|配置|文件')
CONNECTOR = re.compile(r'则|便|就|即可|即会|即|将|会|能够|可以|可')


def subject(text):
    match=SUBJECTS.search(text or '')
    return ('资源' if match.group()=='云资源' else match.group()) if match else None


def operation(text):
    match=EFFECT.search(text or '')
    return OPERATIONS[match.group()] if match else None


def scopes(text):
    result=set()
    if re.search(r'同(?:一)?租户|本租户',text):result.add('same_tenant')
    for n in re.findall(r'(?:最多|不超过|至多)\s*(\d+)\s*项',text):result.add('at_most:'+n)
    if NORMAL.search(text):result.add('normal_branch')
    return result


def facts(text, *, location, object_id=None, source_refs=(), fallback_operation=None, fallback_subject=None, origin='model'):
    """Preserve the sentence owning each predicate and its evidence role."""
    result=[]
    for sentence in re.split(r'[。；;\n]',text or ''):
        sentence=sentence.strip()
        if not sentence:continue
        tentative=bool(re.match(r'(?:方案)?建议[：:]|假设[：:]|待(?:澄清|核对|确认)[：:]',sentence))
        local_subject=subject(sentence) or fallback_subject
        op=operation(sentence) or fallback_operation
        if not op:continue
        base=dict(object_id=object_id,location=location,source_refs=list(source_refs),origin=origin,
                  subject=local_subject,operation=op,quote=sentence,scope=sorted(scopes(sentence)))
        unknown=UNKNOWN.search(sentence)
        # A query with an affirmative confirmed result can resolve uncertainty.
        confirmed=re.search(r'(?:查询|收到|获得)[^，,。；]{0,12}(?:确认|明确)[^，,。；]{0,12}(?:成功|拒绝|失败|未写入|未提交|未创建)',sentence)
        if confirmed and re.search(r'未|没有|尚未',sentence[:confirmed.start()]):confirmed=None
        if unknown and not confirmed and not tentative:
            result.append(dict(base,role='unknown_result',guard=None,conditions=[]))
        rejection=REJECTION.search(sentence)
        if rejection and not tentative and not re.search(r'不(?:是|能|会)?|未|没有',sentence[max(0,rejection.start()-4):rejection.start()]):
            result.append(dict(base,role='rejection_condition',guard=None,conditions=['service_rejects']))
        for effect in EFFECT.finditer(sentence):
            prefix=sentence[:effect.start()]
            part=re.split(r'[，,]',prefix)[-1]
            # Retaining local edit input is not the business transaction's effect.
            if re.match(r'\s*(?:输入|草稿|编辑内容)',sentence[effect.end():]):continue
            non_asserted=tentative or bool(NON_ASSERTION.search(part))
            if re.search(r'(?:原句|旧句|错误表述)[：:]?\s*[“「"]',prefix):continue
            negative=bool(re.search(r'(?:不|未|不得|不能|禁止|没有|拒绝|阻止)(?:再|任何)?\s*$',prefix))
            current=dict(base,operation=OPERATIONS[effect.group()])
            if rejection and not negative and not non_asserted:
                result.append(dict(current,role='rejection_effect',guard=None,conditions=['service_rejects']))
            if unknown and not confirmed and not tentative and not NON_ASSERTION.search(prefix):
                result.append(dict(current,role='unknown_certainty',guard=None,conditions=[],outcome='no_effect' if negative else 'effect'))
                continue
            for guard,pattern in GUARDS.items():
                # The guard must be before this effect, not an unrelated later field.
                matches=list(re.finditer('('+pattern+r')[^，,。；;\n]{0,16}?('+NEGATIVE+'|'+POSITIVE+')',prefix))
                if not matches:continue
                match=matches[-1];tail=prefix[match.end():]
                is_negative=bool(re.fullmatch(NEGATIVE,match.group(2)))
                if is_negative and negative:
                    result.append(dict(current,role='proposal' if non_asserted else 'necessary_condition',guard=guard,conditions=[guard+':positive']))
                elif not is_negative and not negative and re.search(r'只有.*才|只是必要条件',prefix):
                    result.append(dict(current,role='proposal' if non_asserted else 'necessary_condition',guard=guard,conditions=[guard+':positive']))
                elif not is_negative and not negative and CONNECTOR.search(tail):
                    role='proposal' if non_asserted else 'normal_branch' if NORMAL.search(prefix) else 'sufficient_condition'
                    result.append(dict(current,role=role,guard=guard,conditions=sorted(scopes(prefix)|{guard+':positive'})))
            if negative and rejection and not unknown:
                result.append(dict(current,role='proposal' if non_asserted else 'rejection_exception',guard=None,conditions=['service_rejects']))
        if unknown and not confirmed and not tentative and re.search(r'(?:原|既有)(?:记录|数据|资源)[^，,。；]{0,4}不变',sentence) and not NON_ASSERTION.search(sentence):
            result.append(dict(base,role='unknown_certainty',guard=None,conditions=[],outcome='no_effect'))
    return result


def item_facts(item, location=None):
    oid=item.get('temp_id') or item.get('id');base=location or oid or 'item'
    text=' '.join([item.get('title',''),item.get('statement','')]+list((item.get('behavior') or {}).values()))
    op=operation(text);obj=subject(item.get('title','')+' '+item.get('statement','')) or subject(text)
    fields=[('statement',item.get('statement',''))]+[('behavior.'+k,v) for k,v in (item.get('behavior') or {}).items() if isinstance(v,str)]
    return [f for field,value in fields for f in facts(value,location=base+'.'+field,object_id=oid,
        source_refs=item.get('source_refs',[]),fallback_operation=op,fallback_subject=obj)]


def compatible(a,b):
    return a['operation']==b['operation'] and (not a['subject'] or not b['subject'] or a['subject']==b['subject'])


def issues(claims,basis=(), *, inherited_scope=()):
    result=[];all_facts=[*claims,*basis]
    for claim in claims:
        if claim['role']=='unknown_certainty':
            result.append(dict(claim,dimension='outcome_condition',reason='unknown_result_asserted',evidence=[f for f in all_facts if f['role']=='unknown_result' and compatible(claim,f)]))
            continue
        if claim['role']=='rejection_effect':
            forbidden=[f for f in all_facts if f['role']=='rejection_exception' and compatible(claim,f)]
            if forbidden:result.append(dict(claim,dimension='outcome_condition',reason='effect_despite_explicit_rejection',evidence=forbidden))
            continue
        if claim['role']!='sufficient_condition':continue
        relevant=[f for f in all_facts if compatible(claim,f)]
        necessary=[f for f in basis if compatible(claim,f) and f['role']=='necessary_condition' and f['guard']==claim['guard']]
        exceptions=[f for f in relevant if f['role'] in ('rejection_exception','unknown_result')]
        declared=[f for f in basis if compatible(claim,f) and f['role']=='sufficient_condition' and f['guard']==claim['guard']]
        supported=any(set(f['conditions'])<=set(claim['conditions'])|set(inherited_scope) for f in declared)
        reason='unqualified_success_conflicts_with_exception' if exceptions else 'necessary_is_not_sufficient' if necessary and not supported else 'sufficient_scope_weakened' if declared and not supported else None
        if reason:result.append(dict(claim,dimension='outcome_condition',reason=reason,evidence=exceptions or necessary or declared))
    return result


def evidence_facts(item,excerpts):
    pairs={(r['source_id'],r['excerpt_id']) for r in item.get('source_refs',[])}
    local=item_facts(item);op=next((f['operation'] for f in local),operation(item.get('statement','')))
    obj=subject(item.get('title','')+' '+item.get('statement',''))
    return [f for e in excerpts if (e['source_id'],e['id']) in pairs
        for f in facts(e['text'],location='source:'+e['source_id']+':'+e['id'],object_id=item.get('temp_id') or item.get('id'),
            source_refs=[dict(source_id=e['source_id'],excerpt_id=e['id'])],fallback_operation=op,fallback_subject=obj,origin='source_statement')]


def raise_issues(found):
    if found:
        detail='；'.join(f"{f['location']} 原句『{f['quote']}』：{f['reason']}" for f in found[:4])
        raise Problem('SEMANTIC_BLOCKED','结果条件不一致：'+detail+'。必要条件不能反推充分成功；明确正常分支并保留拒绝/未知例外，或按真实冲突追问；不代定成功规则。',model_repair_allowed=False)


def validate_item(item):
    """A review checkbox cannot make an unchanged local conflict consistent."""
    local=item_facts(item)
    raise_issues(issues(local)+[dict(f,reason='undecided_condition_cannot_be_adopted') for f in local
        if f['role']=='proposal' and re.search(r'待澄清|待决定|尚未决定|未采纳',f['quote'])])


def source_conflicts(basis):
    return issues([f for f in basis if f['role']=='sufficient_condition'],basis)


def cited_text_issues(text,evidence):
    """The explanation's own cited evidence, never unrelated global rules."""
    op=operation(evidence) or operation(text)
    obj=subject(evidence) or subject(text)
    basis=facts(evidence,location='cited_evidence',fallback_operation=op,
                fallback_subject=obj,origin='cited_statement')
    return issues(facts(text,location='explanation',fallback_operation=op,fallback_subject=obj),basis)


def question_bound(conflict,item,questions):
    oid=item.get('temp_id') or item.get('id')
    pairs={(r['source_id'],r['excerpt_id']) for f in [conflict,*conflict['evidence']] for r in f['source_refs']}
    for q in questions:
        text=q.get('question','')
        if (q.get('blocking') and {oid,item.get('target_item_id')} & set(q.get('related_refs',[]))
                and pairs<={(r['source_id'],r['excerpt_id']) for r in q.get('source_refs',[])}
                and re.search(r'(?:成功|创建|保存|写入|应用|更新|删除)(?:条件|口径)|'
                              r'(?:满足|充足|拒绝|未知)[^？?]{0,35}(?:成功|创建|保存|写入|应用|更新|删除)',text)):
            return True
    return False


def validate_response_conditions(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change','prd'):return
    from .understanding import leaves
    questions=leaves(value.get('questions',[]))+[q for q in p.get('questions',[]) if q.get('status')!='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    owners=value.get('proposals',[]) or [i for i in p.get('items',[]) if i.get('selection_status')=='selected' and i.get('applies_to')=='to_be']
    bindings=[]
    for index,item in enumerate(owners):
        base=f'proposals[{index}]' if value.get('proposals') else item['id']
        local=item_facts(item,base);basis=evidence_facts(item,excerpts)
        raise_issues(issues(local,basis,inherited_scope=scopes(item.get('statement',''))))
        for conflict in source_conflicts(basis):
            if not question_bound(conflict,item,questions):
                raise_issues([dict(conflict,reason='source_conditions_conflict_requires_bound_question')])
        bindings.append((item,local,basis))
    prose=[('summary',value.get('summary','')),('result.understanding',value.get('result',{}).get('understanding',''))]
    for field,text in prose:
        for item,local,basis in bindings:
            for claim in facts(text,location=field,object_id=item.get('temp_id') or item.get('id'),
                    fallback_operation=next((f['operation'] for f in local),None),fallback_subject=subject(item.get('title','')+' '+item.get('statement',''))):
                # Explicit other objects do not inherit this item's conditions.
                if any(compatible(claim,f) for f in [*local,*basis]):
                    raise_issues(issues([claim],[*basis,*local],inherited_scope=scopes(item.get('statement',''))))


def projection(p,excerpts):
    """Input-only facts with original IDs/quotes; never a system PM approval."""
    items=p.get('items',[])
    raw=[dict(f,source_locator=e.get('locator'),source_hash=e.get('source_hash')) for e in excerpts
         for f in facts(e['text'],location='source:'+e['source_id']+':'+e['id'],
             source_refs=[dict(source_id=e['source_id'],excerpt_id=e['id'])],origin='source_statement',
             fallback_operation=operation(e['text']),fallback_subject=subject(e['text']))]
    return dict(version=VERSION,scope='finite predicates; source statements are not formal adoption',
        facts=raw+[f for item in items for f in [*item_facts(item),*evidence_facts(item,excerpts)]])
