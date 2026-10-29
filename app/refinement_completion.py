"""Explicit AI drafting of review fields; never human answers or confirmations."""
import copy
from .core import require


def questions(project, request):
    rows=[q for q in project.get('questions',[]) if not q.get('superseded_by') and q.get('kind')!='parent'
          and (not q.get('out_of_scope_reason') or q.get('scope_exclusion'))
          and (q.get('status') not in ('deferred','out_of_scope') or q.get('scope_exclusion'))]
    if request.get('mode')=='question':
        return [q for q in rows if q['id']==request.get('question_id')]
    return [q for q in rows if not q.get('answer')]


def validate(project, request):
    if not request:return None
    require(request.get('mode') in ('all','required','question','prewrite'),'DRAFT_REQUEST_INVALID','请选择整表补全或单题建议')
    require(set(request)<={'mode','question_id'},'DRAFT_REQUEST_INVALID','补全请求含不支持的设置')
    if request['mode']=='question':
        require(bool(questions(project,request)),'DRAFT_REQUEST_INVALID','这道问题已变化，请刷新后重新选择',409)
    if request['mode']=='prewrite':
        require(any(f.get('status')!='resolved' for f in (project.get('prewrite_review') or {}).get('findings',[])),
            'DRAFT_REQUEST_INVALID','当前没有待处理的成文检查项，请先核对检查结果。',409)
    return copy.deepcopy(request)


def configure(header, context, project, request):
    if not request:return
    from .business_draft import VALUE, ACCEPTANCE, SUMMARY_FIELDS, _object, _input_refs, _aliases
    aliases=_aliases(context['_completion_excerpts'])[0]
    context.pop('_completion_excerpts')
    pending=questions(project,request)
    context['completion_questions']=[dict(question_key='Q'+str(n),text=q['question'],reason=q.get('why',''),
        current_answer=q.get('answer'),sources=_input_refs(q.get('source_refs',[]),aliases)) for n,q in enumerate(pending,1)]
    answer=copy.deepcopy(VALUE);answer['properties']['text']['minLength']=1
    answer['properties']['basis']={'enum':['fact','recommendation']}
    properties={'answers':_object({'Q'+str(n):copy.deepcopy(answer) for n in range(1,len(pending)+1)})}
    if request['mode']!='question':
        keys=context['required_row_keys']
        properties.update(profile=_object(dict(scenario={'enum':['new_product','new_module','modify_existing']},
            dependencies={'enum':['independent','shared']})),
            change_types=_object({key:{'enum':['new','modified','preserved']} for key in keys}),
            scope_rows=dict(type='array',items={'enum':keys} if keys else {'type':'string'},uniqueItems=True,minItems=1))
        properties['change_types']['additionalProperties']={'enum':['new','modified','preserved']}
    header['schema']['properties']['completion']=_object(properties)
    header['schema']['required'].append('completion')
    header['refinement_completion']=request
    context['refinement_completion']=request
    if request['mode']=='prewrite':
        # One whole-table draft consumes all check points and saved answers.
        # Opening/confirming that table never calls the model.
        context['prewrite_confirmation_points']=[{k:copy.deepcopy(f.get(k)) for k in
            ('message','suggested_resolution','recommended_answer','quotes','related_refs','question_id','severity')}
            for f in (project.get('prewrite_review') or {}).get('findings',[]) if f.get('status')!='resolved']
        context['saved_business_answers']=[dict(question=q['question'],answer=q['answer'],related_refs=q.get('related_refs',[]))
            for q in project.get('questions',[]) if q.get('answer') and not q.get('superseded_by')]
        header['prewrite_drafting_rules']=(
            '这是已有需求的整批修订：只落实检查点，不重新设计无关流程。'
            'recommended_answer 是表格显示的推荐草稿，尚非用户决定；没有另选方案时据此起草一套可直接确认的完整修订。'
            'completion.answers 与需求、验收 text 写清拟采纳的具体业务口径；推荐与未确认身份由 basis 和草稿状态表达，'
            '不要在方案正文重复“建议方案待人工确认”“此为建议待确认”“若确认不成立再另改”等编辑状态，不能把待确认继续留为必填内容。'
            '用户已保存的原话保留；不得把资料真正未明确的事实伪装成既有规则。'
            '检查指出某条旧验收有冲突时，用 acceptance.target_ref 明确修订该旧条款；不能只新增一条正确验收而保留冲突的旧验收。'
            '不涉及的已保存详细定义沿用原文及有效引用。需要详细定义但尚未保存时，'
            '仅按真实功能给出最小完整定义，无关集合用 applicability=false 和理由。'
            'detail_contract 根节点没有 field_refs；field_refs 仅在 schema 明确允许的对象中使用。'
            'evidence 的键必须是所属对象的真实属性名，节点仅 label/kind/actor 等属性，'
            '不得以空列表提示、无版本提示等业务词作 evidence 的键。'
            '每个必填业务属性及 evidence 均按本次 schema 输出。')
        header['required_summary_fields']=context['required_summary_fields']=[]
        header['schema']['properties']['summary_fields']=_object({})
        row=header['schema']['properties']['requirements']['items']['properties']
        row['row_key']={'enum':context['required_row_keys']}
    if request['mode']=='question':
        header['schema']['properties']['requirements'].update(minItems=0,maxItems=0)
        header['schema']['properties']['summary_fields']=_object({})
        header['required_row_keys']=context['required_row_keys']=[]
        header['required_summary_fields']=context['required_summary_fields']=[]
    else:
        # An explicitly requested draft is concrete, even when its basis is a
        # proposed choice. Its facts and confirmation state remain separate.
        if request['mode']=='all':
            saved=project.get('product_context') or {};states=project.get('context_field_states') or {}
            keys=[key for key in SUMMARY_FIELDS if states.get(key,{}).get('status') not in ('none','not_applicable')
                  and (not str(saved.get(key) or '').strip() or states.get(key,{}).get('status')=='unknown')]
            header['required_summary_fields']=context['required_summary_fields']=keys
            header['schema']['properties']['summary_fields']['required']=keys
        for field in header['required_summary_fields']:
            header['schema']['properties']['summary_fields']['properties'][field]=copy.deepcopy(answer)
        row=header['schema']['properties']['requirements']['items']['properties']
        header['schema']['properties']['requirements']['minItems']=1
        row['statement']=copy.deepcopy(answer)
        for key in row['behavior']['properties']:row['behavior']['properties'][key]=copy.deepcopy(answer)
        row['acceptance']['minItems']=1
        row['acceptance']['items']=copy.deepcopy(ACCEPTANCE)
        row['acceptance']['items']['properties']['text']['minLength']=1
        row['acceptance']['items']['properties']['basis']={'enum':['fact','recommendation']}
        # This operation closes draft gaps instead of creating a new set of
        # unanswered mandatory fields. New uncertainty belongs in a concrete
        # proposed choice for human review.
        row['questions']['maxItems']=0


def adapt(value, project, excerpts, request):
    from .business_draft import _aliases, _value, _roots, _validate_shape, VALUE
    result=dict(answers={},change_types={},scope_ids=[],rejected=[],missing_answer_ids=[])
    if not request:return result
    pending=questions(project,request);mapping={'Q'+str(n):q['id'] for n,q in enumerate(pending,1)}
    packet=value.get('completion') or {};aliases=_aliases(excerpts)[1]
    for key,part in packet.get('answers',{}).items():
        try:
            require(key in mapping,'DRAFT_ANSWER_INVALID','推荐回答不属于本次问题')
            _validate_shape(part,VALUE)
            require(part['basis']!='unknown','DRAFT_ANSWER_INVALID','请给出具体待确认方案，不能只返回未知')
            text,evidence=_value(part,aliases)
            result['answers'][mapping[key]]=dict(evidence,text=text)
        except Exception as error:
            from .core import Problem
            if not isinstance(error,Problem):raise
            result['rejected'].append(dict(key=key,code=error.code,reason=error.message))
    result['missing_answer_ids']=[qid for qid in mapping.values() if qid not in result['answers']]
    if request['mode']!='question':
        roots=_roots(project)
        result['change_type_rows']={key:kind for key,kind in packet.get('change_types',{}).items() if kind in ('new','modified','preserved')}
        result['scope_rows']=packet.get('scope_rows',[])
        for key,kind in packet.get('change_types',{}).items():
            if key in roots and kind in ('new','modified','preserved'):result['change_types'][roots[key]['id']]=kind
        result['scope_ids']=[roots[key]['id'] for key in packet.get('scope_rows',[]) if key in roots]
        profile=packet.get('profile') or {}
        if profile.get('scenario') in ('new_product','new_module','modify_existing') and profile.get('dependencies') in ('independent','shared'):
            result['profile']=profile
    return result
