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
_OUTCOME_VALUE=r'(?:成功|失败|结果未知|未知结果)'
OUTCOME_ENUM=re.compile(r'(?:派单结果|结果|状态)(?:字段)?(?:枚举)?(?:取值)?为'+_OUTCOME_VALUE+
    r'(?:[、/／]'+_OUTCOME_VALUE+r'){1,3}(?:三选一|之一|枚举)(?=[，,。；;]|$)')
NORMAL = re.compile(r'正常(?:成功|处理|受理|结果)?分支|成功(?:分支|场景)|正常成功(?:场景|情况下)|'
                    r'(?:服务|系统)(?:(?:正常受理|已受理|受理成功)(?:且|并且?)(?:已)?处理成功|处理成功|执行成功)|'
                    r'已确认(?:成功|完成)')
NON_ASSERTION = re.compile(r'建议|假设|待(?:澄清|核对|确认)|未采纳|尚未决定|是否|能否|不保证|'
                          r'不能[^，,。；;\n]{0,12}(?:推断|认定|断言)|并非|不代表|不意味着|不蕴含')
SUBJECTS = re.compile(r'云资源|资源|工单|订单|记录|备注|时段|配置|文件')
CONNECTOR = re.compile(r'则|便|就|即可|即会|即|将|会|能够|可以|可')
ASSERTION = re.compile(r'断言|声称|推断|认定|确认|反推|展示|显示|当作|当成|视为|判定为')
NEGATOR = r'并非|不是|没有|不能|不得|禁止|不允许|不应|不可|无法|不|未'
ASSERTION_MODIFIER = r'据此|因此|直接|自行|轻易|作出|进行|作|做|\s'
ASSERTION_NEGATION = re.compile(r'(?:'+NEGATOR+r')(?:(?:'+ASSERTION_MODIFIER+r')|(?:'+NEGATOR+r'))*$')
ASSERTION_PREMISE = re.compile(r'(?P<negation>(?:'+NEGATOR+r')(?:(?:'+ASSERTION_MODIFIER+r')|(?:'+NEGATOR+r'))*)(?:从|根据|依据)[^，,]{1,80}$')
ASSERTION_BREAK = re.compile(r'[，,]|然而|但是|但|却|实际上|同时|并且')


def assertion_scope(text, position):
    """A negated inference governs its local result list, not later clauses.

    Offsets refer to the untouched sentence. An even number of negators does
    not prohibit the assertion. This finite grammar does not parse all Chinese.
    """
    found=None
    for predicate in ASSERTION.finditer(text,0,position+1):
        boundary=ASSERTION_BREAK.search(text,predicate.end())
        end=boundary.start() if boundary else len(text)
        if position>=end:continue
        previous=list(ASSERTION_BREAK.finditer(text,0,predicate.start()))
        start=previous[-1].end() if previous else 0
        before=text[start:predicate.start()]
        negation=ASSERTION_NEGATION.search(before)
        premise=ASSERTION_PREMISE.search(before) if not negation else None
        # Negation inside the premise (e.g. quota not satisfied) does not
        # negate the governing inference prohibition a second time.
        modal=negation.group() if negation else premise['negation'] if premise else ''
        count=len(re.findall(NEGATOR,modal))
        if not modal and found and re.fullmatch(r'\s*(?:或|及|、|以及)\s*',
                text[found['start']+len(found['predicate']):predicate.start()]):
            count=found['negators']
        found=dict(predicate=predicate.group(),start=predicate.start(),end=end,
                   negators=count,denied=bool(count%2))
    return found


def rejection_branch(clause):
    """An explicit local refusal branch does not inherit an earlier unknown."""
    match=REJECTION.search(clause)
    return bool(match and not UNKNOWN.search(clause) and not re.search(
        r'不(?:是|能|会)?|未|没有',clause[max(0,match.start()-4):match.start()]))


def quoted_prior_assertion(text, position):
    """An explicitly corrected quotation ends at its original closing quote."""
    for match in re.finditer(r'(?:(?:原句|旧句|错误表述)[：:]?|纠正|更正)\s*[“「"]',text):
        closing={'“':'”','「':'」','"':'"'}[text[match.end()-1]]
        end=text.find(closing,match.end())
        if end!=-1 and match.end()<=position<end:return True
    return False


def subject(text, *, last=False):
    matches=list(SUBJECTS.finditer(text or ''))
    match=(matches[-1] if last else matches[0]) if matches else None
    return ('资源' if match.group()=='云资源' else match.group()) if match else None


def operation(text):
    match=EFFECT.search(text or '')
    return OPERATIONS[match.group()] if match else None


def normal_branch(text,expected_subject=None):
    """An affirmative processing branch belongs to its own clause/object."""
    for match in NORMAL.finditer(text):
        start=max(text.rfind('，',0,match.start()),text.rfind(',',0,match.start()))+1
        end=re.search(r'[，,]',text[match.end():])
        clause=text[start:match.end()+end.start() if end else len(text)]
        if re.search(r'(?:尚未|未|不|没有|并非|无法|未能)[^，,]{0,6}(?:受理|处理|执行|确认)[^，,]{0,4}(?:成功|完成)',clause):continue
        if re.search(r'(?:受理|处理|执行)(?:结果)?(?:失败|未知|未完成)',clause):continue
        if re.search(r'(?:并非|不是|不属于|未进入|没有)\s*$',text[start:match.start()]):continue
        branch_subject=subject(clause)
        if expected_subject and branch_subject and expected_subject!=branch_subject:continue
        return True
    return False


def scopes(text,expected_subject=None):
    result=set()
    if re.search(r'同(?:一)?租户|本租户',text):result.add('same_tenant')
    for n in re.findall(r'(?:最多|不超过|至多)\s*(\d+)\s*项',text):result.add('at_most:'+n)
    if normal_branch(text,expected_subject):result.add('normal_branch')
    return result


def confirmed_result(sentence):
    pattern=(r'(?:查询|收到|获得)(?P<lookup>[^，,。；]{0,12}?)(?:确认|明确)[^，,。；]{0,12}?(?:成功|拒绝|失败|未写入|未提交|未创建)'
             r'|查到(?P<found>[^，,。；]{0,12}?)已确认(?=则|时|后|的|记录|[，,。；：:]|$)')
    for match in re.finditer(pattern,sentence):
        boundary=max(sentence.rfind('，',0,match.start()),sentence.rfind(',',0,match.start()))+1
        prefix=sentence[boundary:match.start()]
        if re.search(r'(?:未|尚未|没有|不能|无法|未能)\s*$',prefix):continue
        lookup=match.group('lookup') if match.group('lookup') is not None else match.group('found')
        if re.search(r'未|没有|尚未|不能|无法',lookup):continue
        return match
    return None


def uncertainty_occurrence(target):
    """`保存时网络中断导致结果未知` names when uncertainty happened.

    Inspect only the effect's own clause. A later comma/semicolon clause still
    has its predicates checked, and an asserted result is never exempted.
    """
    clause = re.split(r'[，,]', target, maxsplit=1)[0]
    # Do not use arbitrary text here: it could hide an affirmative outcome
    # (e.g. 已经生成/处理完成) before the response was lost.
    return bool(re.fullmatch(r'(?:请求|操作|提交)?时(?:发生)?'
                r'(?:(?:网络|连接)(?:中断|断开)(?:导致(?:提交)?)?)?'
                r'(?:结果未知|未知结果|超时|响应丢失)\s*', clause))


def uncertainty_event(text):
    """An enum option names a possible value, not an event that just occurred.

    Exclude only the literal's own bounded declaration. Another unknown/timeout
    in the same sentence still governs assertions, as does inherited context.
    """
    declarations=list(OUTCOME_ENUM.finditer(text))
    return next((match for match in UNKNOWN.finditer(text)
        if not any(decl.start()<=match.start()<decl.end() for decl in declarations)),None)


def outcome_sentences(text,fallback_subject):
    """Semicolons end assertion scope, not the same object's unknown context.

    Carry only the original uncertainty quote within one sentence. A different
    object, explicit processing/refusal branch or confirmed query does not
    inherit that context. No strings or source records are rewritten.
    """
    for paragraph in re.split(r'[。\n]',text or ''):
        context=None
        for sentence in re.split(r'[；;]',paragraph):
            sentence=sentence.strip()
            if not sentence:continue
            obj=subject(sentence) or fallback_subject
            tentative=bool(re.match(r'(?:方案)?建议[：:]|假设[：:]|待(?:澄清|核对|确认)[：:]',sentence))
            confirmed=confirmed_result(sentence)
            preceding_context=context
            if confirmed and (not context or not obj or not context['subject'] or obj==context['subject']):context=None
            if uncertainty_event(sentence) and not confirmed and not tentative:
                context=dict(subject=obj,quote=sentence)
            carried=preceding_context if confirmed else context
            inherited=carried if (carried and (not obj or not carried['subject'] or obj==carried['subject'])
                and not normal_branch(sentence,obj) and not rejection_branch(sentence)) else None
            yield sentence,inherited


def facts(text, *, location, object_id=None, source_refs=(), fallback_operation=None, fallback_subject=None, origin='model'):
    """Preserve the sentence owning each predicate and its evidence role."""
    result=[]
    for sentence,context in outcome_sentences(text,fallback_subject):
        tentative=bool(re.match(r'(?:方案)?建议[：:]|假设[：:]|待(?:澄清|核对|确认)[：:]',sentence))
        local_subject=subject(sentence) or fallback_subject
        op=operation(sentence) or fallback_operation
        if not op:continue
        base=dict(object_id=object_id,location=location,source_refs=list(source_refs),origin=origin,
                  subject=local_subject,operation=op,quote=sentence,scope=sorted(scopes(sentence)))
        unknown=uncertainty_event(sentence) or context
        # A query with an affirmative confirmed result can resolve uncertainty.
        confirmed=confirmed_result(sentence)
        if unknown and not confirmed and not tentative:
            result.append(dict(base,quote=context['quote'] if context else sentence,
                               role='unknown_result',guard=None,conditions=[]))
        rejection=REJECTION.search(sentence)
        if rejection and not tentative and not re.search(r'不(?:是|能|会)?|未|没有',sentence[max(0,rejection.start()-4):rejection.start()]):
            result.append(dict(base,role='rejection_condition',guard=None,conditions=['service_rejects']))
        for effect in EFFECT.finditer(sentence):
            prefix=sentence[:effect.start()]
            part=ASSERTION_BREAK.split(prefix)[-1]
            # Rendering a value returned by a query is not a business write.
            # Keep this narrow: a timeout followed by an invented success or
            # an update of stored records must still be checked below.
            if (effect.group()=='更新'
                    and re.match(r'显示(?=[，,。；;\s]|$)',sentence[effect.end():])
                    and re.search(r'以(?:系统)?返回(?:的)?结果\s*$',prefix)):
                continue
            # Retaining local edit input is not the business transaction's effect.
            if re.match(r'\s*(?:输入|草稿|编辑内容)',sentence[effect.end():]):continue
            # A saved answer is workflow evidence, not a transaction outcome.
            if effect.group()=='保存' and re.match(r'\s*[^，,。；]{0,12}(?:回答|答案)(?=[，,。；：:\s]|$)',sentence[effect.end():]):continue
            inference=assertion_scope(sentence,effect.start())
            denied=bool(inference and inference['denied'])
            non_asserted=tentative or denied or (not inference and bool(NON_ASSERTION.search(part)))
            if quoted_prior_assertion(sentence,effect.start()):continue
            negative=bool(re.search(r'(?:不|未|不得|不能|禁止|没有|拒绝|阻止)(?:再|任何)?\s*$',prefix))
            # Bind this effect to its target/local clause, not another object's
            # successful branch mentioned before a comma.
            target=re.split(r'[，,]',sentence[effect.end():])[0]
            effect_subject=subject(target) or subject(part,last=True) or fallback_subject or local_subject
            current=dict(base,operation=OPERATIONS[effect.group()],subject=effect_subject,
                         scope=sorted(scopes(sentence,effect_subject)))
            confirmed_subject=subject(confirmed.group()) if confirmed else None
            resolved=bool(confirmed and effect.start()>=confirmed.start() and
                (not confirmed_subject or not effect_subject or confirmed_subject==effect_subject))
            if uncertainty_occurrence(target):
                continue
            if denied:
                result.append(dict(current,role='inference_constraint',guard=None,conditions=[],
                                   assertion_scope=inference))
                continue
            prohibition=re.search(r'(?:禁止|不得|不能|不可|不允许|不应)(?:再次|重新|重复|再)?\s*$',prefix)
            new_attempt=re.match(r'\s*(?:新(?:的)?|另一个)(?:申请|请求)',target)
            named_retry = (re.search(r'(?:禁止|不得|不能|不可|不允许|不应)(?:再次|重新|重复|再)?(?:发出|发送|提交)\s*$',prefix)
                           and re.match(r'\s*(?:提交|请求)(?=[，,。；;\s]|$)',target))
            if prohibition and new_attempt or named_retry:
                result.append(dict(current,role='proposal' if non_asserted else 'action_constraint',
                    guard=None,conditions=[],temporal='future_action',action_target='new_attempt'))
                continue
            if rejection and not negative and not non_asserted:
                result.append(dict(current,role='rejection_effect',guard=None,conditions=['service_rejects']))
            if unknown and not resolved and not non_asserted and not rejection_branch(part):
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
                    role='proposal' if non_asserted else 'normal_branch' if normal_branch(prefix,effect_subject) else 'sufficient_condition'
                    result.append(dict(current,role=role,guard=guard,conditions=sorted(scopes(prefix,effect_subject)|{guard+':positive'})))
            if negative and rejection and not unknown:
                result.append(dict(current,role='proposal' if non_asserted else 'rejection_exception',guard=None,conditions=['service_rejects']))
        if unknown and not confirmed and not tentative:
            for unchanged in re.finditer(r'(?:原|既有)(?:记录|数据|资源)[^，,。；]{0,4}(?:不变|未变)',sentence):
                inference=assertion_scope(sentence,unchanged.start())
                prefix=sentence[:unchanged.start()]
                part=ASSERTION_BREAK.split(prefix)[-1]
                if inference and inference['denied']:
                    result.append(dict(base,role='inference_constraint',guard=None,conditions=[],assertion_scope=inference))
                elif not rejection_branch(part) and (inference or not NON_ASSERTION.search(part)):
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
