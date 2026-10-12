"""Stateless boundaries for a user-directed comparison with external material."""

import copy

import jsonschema

from .core import Problem, require


def ordinary_response(response):
    """Ignore only the exact, content-free legacy template, without mutating raw output."""
    if not isinstance(response, dict) or 'benchmark_analysis' not in response:
        return response, False
    if response['benchmark_analysis'] == {'summary': '', 'observations': [], 'limitations': []}:
        return {key: value for key, value in response.items() if key != 'benchmark_analysis'}, True
    raise Problem('SCHEMA_INVALID',
                  '普通整理包含未请求的对标内容，本次结果未写入；无需补充竞品资料。',
                  model_repair_allowed=False)


def analysis_schema():
    """The comparison is evidence, not an adoption or product decision."""
    short = lambda limit: {'type': 'string', 'minLength': 1, 'maxLength': limit}
    optional_text = lambda limit: {'type': 'string', 'maxLength': limit}
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['summary', 'observations', 'limitations'],
        'properties': {
            'summary': short(2000),
            'observations': {
                'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['title', 'reference_quote', 'source_ref', 'difference',
                                 'suggested_application', 'proposal_refs'],
                    'properties': {
                        'title': short(200),
                        'reference_quote': short(6000),
                        'source_ref': {
                            'type': 'object', 'additionalProperties': False,
                            'required': ['source_id', 'excerpt_id'],
                            'properties': {'source_id': short(120), 'excerpt_id': short(120)},
                        },
                        'difference': optional_text(3000),
                        'suggested_application': optional_text(3000),
                        'proposal_refs': {'type': 'array', 'items': short(120)},
                    },
                },
            },
            'limitations': {'type': 'array', 'items': {'type': 'string'}},
        },
    }


def validate_request(p, benchmark):
    """Resolve source IDs against the current project without changing their purpose."""
    require(isinstance(benchmark, dict), 'BENCHMARK_INVALID', '对标请求格式无效')
    target, focus, source_ids = (benchmark.get(key) for key in ('target', 'focus', 'source_ids'))
    require(isinstance(target, str) and 0 < len(target.strip()) <= 200,
            'BENCHMARK_INVALID', '请填写不超过 200 字的对标对象')
    require(isinstance(focus, str) and len(focus) <= 2000,
            'BENCHMARK_INVALID', '对标关注点不得超过 2000 字')
    require(isinstance(source_ids, list) and source_ids and
            all(isinstance(sid, str) and sid for sid in source_ids) and
            len(source_ids) == len(set(source_ids)),
            'BENCHMARK_INVALID', '请明确选择至少一份不重复的对标资料')
    sources = {s['id']: s for s in p.get('sources', [])}
    for sid in source_ids:
        source = sources.get(sid)
        require(source is not None, 'BENCHMARK_SOURCE_INVALID', '对标资料不属于当前项目：' + sid)
        require(not source.get('excluded') and not source.get('superseded_by'),
                'BENCHMARK_SOURCE_INVALID', '对标资料已排除或被新版本替代：' + sid)
        require('business_context' not in source and source.get('purpose') != 'template',
                'BENCHMARK_SOURCE_INVALID', '工程上下文或文档模板不能作为外部对标资料：' + sid)
        readable = source.get('parse_status') in ('read', 'partial') and any(
            isinstance(e.get('text'), str) and e['text'].strip()
            for e in source.get('excerpts', []))
        pending_image = source.get('parse_status') == 'awaiting_vision' and bool(source.get('image_mime'))
        require(readable or pending_image, 'BENCHMARK_SOURCE_UNREADABLE',
                '对标资料尚未读到可用内容，请查看读取状态或更换资料：' + sid)
    return {'target': target.strip(), 'focus': focus.strip(), 'source_ids': source_ids.copy()}


def validate_analysis(response, benchmark, excerpts, p):
    """Reject invented observations and promotion of reference-only claims."""
    require(isinstance(response, dict) and 'benchmark_analysis' in response,
            'SCHEMA_INVALID', '模型未返回对标分析')
    result = response['benchmark_analysis']
    errors = list(jsonschema.Draft202012Validator(analysis_schema()).iter_errors(result))
    require(not errors, 'SCHEMA_INVALID', '对标分析结构无效：' + errors[0].message if errors else '')
    selected = set(benchmark['source_ids'])
    sources={s['id']:s for s in p.get('sources',[])}
    evidence={}
    for e in excerpts:
        sid=e.get('source_id');source=sources.get(sid,{})
        if sid not in selected or not isinstance(e.get('text'),str):continue
        pair=(sid,e['id'])
        if not source.get('image_mime'):evidence[pair]=[e['text']];continue
        if not source.get('vision_run_id'):continue
        for message in p.get('messages',[]):
            if message.get('stage')!='vision' or message.get('role')!='assistant':continue
            if message.get('run_id')!=source['vision_run_id']:continue
            for observation in message.get('response',{}).get('result',{}).get('observations',[]):
                ref=observation.get('source_ref',{})
                if (ref.get('source_id'),ref.get('excerpt_id'))==pair and observation.get('uncertain') is False:
                    evidence.setdefault(pair,[]).append(observation['observation'])
    if not evidence:
        raise Problem('SEMANTIC_BLOCKED', '本轮缺少可核对的对标内容；图片需要当前读取的可靠视觉观察。请在参考资料中核对或重新读取后再对标',
                      model_repair_allowed=False)
    require(result['observations'] or result['limitations'], 'SCHEMA_INVALID', '对标分析没有观察内容或读取限制说明')
    for index, observation in enumerate(result['observations'], 1):
        ref = observation['source_ref']
        pair = (ref['source_id'], ref['excerpt_id'])
        require(pair in evidence, 'REFERENCE_INVALID',
                f'对标观察 {index} 的来源不属于本轮送入的指定资料')
        require(any(observation['reference_quote'] in quote for quote in evidence[pair]), 'SEMANTIC_BLOCKED',
                f'对标观察 {index} 的引文不是该片段原文或已读取视觉观察的连续内容')
    problems=repair_issues(response,p)
    require(not problems,'REFERENCE_INVALID','对标草稿需修正以下关联或问题结构：\n'+'\n'.join(problems))
    intent_sources={s['id'] for s in p.get('sources',[]) if s.get('origin')=='benchmark_intake'}
    validate_proposal_roles(response, selected|intent_sources)
    return copy.deepcopy(result)


def reference_sets(response, p):
    proposal_ids={row['temp_id'] for row in response.get('proposals',[])}
    item_ids={row['id'] for row in p.get('items',[])}
    question_ids={row['id'] for row in p.get('questions',[])}
    question_ids.update(row['temp_id'] for row in response.get('questions',[]))
    question_ids.update(child['temp_id'] for row in response.get('questions',[]) for child in row.get('decision_points',[]))
    return proposal_ids|item_ids, proposal_ids|item_ids|question_ids


def repair_issues(response, p):
    """Report the complete, bounded reference graph so one repair sees all defects."""
    proposals,all_refs=reference_sets(response,p)
    problems=[]
    for index,row in enumerate(response.get('benchmark_analysis',{}).get('observations',[])):
        bad=[ref for ref in row['proposal_refs'] if ref not in proposals]
        if bad:problems.append(f'benchmark_analysis.observations[{index}].proposal_refs 不存在或类型错误：{bad}；只能移除无效引用，保留所有有效关联及观察原文。')
    for kind in ('proposals','questions'):
        for index,row in enumerate(response.get(kind,[])):
            rows=[(f'{kind}[{index}]',row)]+[(f'{kind}[{index}].decision_points[{i}]',child)
                for i,child in enumerate(row.get('decision_points',[]))]
            for path,item in rows:
                bad=[ref for ref in item.get('related_refs',[]) if ref not in all_refs]
                if bad:problems.append(f'{path}.related_refs 引用不存在：{bad}；只能移除无效引用，不得新增候选或替换为猜测关联。')
                question=item.get('question','')
                if not item.get('decision_points') and question.count('？')+question.count('?')>1:
                    problems.append(f'{path} {item["temp_id"]} 含多个独立问句，请用decision_points分别表达；完整保留父问题。')
    return problems


def validate_repair_anchor(original):
    """Require a readable comparison and link graph before requesting a repair."""
    result=original.get('benchmark_analysis') if isinstance(original,dict) else None
    readable=isinstance(result,dict) and jsonschema.Draft202012Validator(analysis_schema()).is_valid(result)
    for kind in ('proposals','questions'):
        rows=original.get(kind) if isinstance(original,dict) else None
        readable=readable and isinstance(rows,list)
        if not isinstance(rows,list):continue
        ids=[]
        for row in rows:
            if not isinstance(row,dict):readable=False;continue
            ident=row.get('temp_id');ids.append(ident)
            refs=row.get('related_refs',[])
            readable=readable and isinstance(ident,str) and bool(ident) and isinstance(refs,list) and all(isinstance(ref,str) for ref in refs)
            children=row.get('decision_points',[])
            if not isinstance(children,list):readable=False;continue
            for child in children:
                readable=readable and isinstance(child,dict) and isinstance(child.get('temp_id'),str)
        if all(isinstance(ident,str) for ident in ids):readable=readable and len(set(ids))==len(ids)
    require(readable,'SEMANTIC_BLOCKED',
        '对标观察或关联结构不完整，无法保证格式修复保留原义；原始回复已保留，请重新生成草稿')


def preserve_analysis(original, repaired, p, excerpts):
    """A format repair can remove bad links, never rewrite comparison conclusions."""
    validate_repair_anchor(original)
    before=original.get('benchmark_analysis');after=repaired.get('benchmark_analysis')
    require(isinstance(after,dict),'SEMANTIC_BLOCKED','格式修复删除了对标分析')
    pairs={(e['source_id'],e['id']) for e in excerpts}
    proposals,all_refs=reference_sets(original,p)
    for field in ('summary','limitations'):
        require(before.get(field)==after.get(field),'SEMANTIC_BLOCKED','格式修复改变了对标结论或限制：'+field)
    old_rows=before.get('observations',[]);new_rows=after.get('observations',[])
    require(len(old_rows)==len(new_rows),'SEMANTIC_BLOCKED','格式修复增删了对标观察')
    for old,new in zip(old_rows,new_rows):
        for field in ('title','reference_quote','difference','suggested_application'):
            require(old.get(field)==new.get(field),'SEMANTIC_BLOCKED','格式修复改变了对标观察原文：'+field)
        ref=old.get('source_ref',{})
        if (ref.get('source_id'),ref.get('excerpt_id')) in pairs:
            require(ref==new.get('source_ref'),'SEMANTIC_BLOCKED','格式修复改变了对标有效来源')
        expected=[ref for ref in old.get('proposal_refs',[]) if ref in proposals]
        require(new.get('proposal_refs',[])==expected,'SEMANTIC_BLOCKED','格式修复改变了对标有效关联或新增猜测关联')
    for kind in ('proposals','questions'):
        by_id={row['temp_id']:row for row in repaired.get(kind,[])}
        for old in original.get(kind,[]):
            new=by_id.get(old['temp_id'],{})
            expected=[ref for ref in old.get('related_refs',[]) if ref in all_refs]
            require(new.get('related_refs',[])==expected,'SEMANTIC_BLOCKED','格式修复改变了草稿有效关联或新增猜测关联')


def validate_proposal_roles(response, source_ids):
    """Reusable guard for later stages while benchmark sources stay marked."""
    selected = set(source_ids)
    for proposal in response.get('proposals', []):
        if proposal.get('applies_to') not in ('to_be','as_is'):
            continue
        refs = proposal.get('source_refs', [])
        ref_ids = {ref.get('source_id') for ref in refs if isinstance(ref, dict)}
        external_only=not ref_ids or ref_ids<=selected
        if external_only and (proposal.get('applies_to')=='as_is' or proposal.get('epistemic_status') in ('reported','observed')):
            raise Problem('SEMANTIC_BLOCKED',
                          '候选缺少独立业务来源；对标资料和对标任务意图不能证明本产品现状或已决定采用该规则',
                          model_repair_allowed=False)
    return response


def contract():
    return (
        '只对用户指定的对标对象、关注点和本轮实际送入的来源片段分析。'
        '每条观察引用对标资料的连续原文；未读到、看不清或无法比较的内容写入 limitations，'
        '图片仅可引用 vision_observations 中该来源已读取且 uncertain=false 的连续观察文字，仍待人工核对；不能将图片像素占位说明当作观察。'
        '差异未知时留空，不把竞品做法当成本产品现状或本期决定。'
        '保留用户已填写内容，不覆盖原文；有充分依据时可在 proposals 中提出 requirement、rule、'
        'acceptance 及 behavior 待审核候选，使用原条目身份提出修订，不自动采纳。'
        '仅有对标来源支撑的本期候选标 proposed 或 inferred；未知数值、权限和结果不得编造。'
        '每个功能需求候选尽量给出关联的可观察验收候选，覆盖已知主流程和失败处理；资料不足的验收维度明确列入 limitations，不以虚构阈值补齐。'
        '每个澄清问题只问一个可独立回答的业务决定；确需保留复合原话时使用 decision_points。资料列出多个能力形态时，不遗漏中间形态制造二选一。'
        'proposal_refs 只放本轮 proposals.temp_id 或当前项目条目 ID，不放问题 ID。related_refs 只引用实际存在的条目或问题，不预留不存在的 ID。'
    )
