"""Row-scoped recommendations over the existing clarify lifecycle.

The assistant may propose changes, never confirm them. Shared clauses remain
read-only here because changing them would affect another requirement row.
"""
import copy

from .core import Problem, digest, require
from .understanding import active_question, answer_bindable, equivalent

RELATED_KINDS = ('rule', 'acceptance', 'constraint')


def scope(project, target, stage='clarify'):
    if stage != 'clarify' or not target or target.get('kind') != 'item':
        return None
    requirement = next((item for item in project['items'] if item['id'] == target['id']), None)
    if not requirement or requirement['kind'] != 'requirement':
        return None
    require(not requirement.get('target_item_id'), 'TARGET_INVALID',
            '请从原需求行发起完善建议，修订建议请先在该行核对', 409)
    rid = requirement['id']
    originals = [item for item in project['items'] if not item.get('target_item_id')]
    linked = [item for item in originals if item['kind'] in RELATED_KINDS
              and (rid in item.get('related_refs', []) or item['id'] in requirement.get('related_refs', []))]
    shared = {item['id'] for item in linked if any(
        other['kind'] == 'requirement' and other['id'] != rid
        and (other['id'] in item.get('related_refs', []) or item['id'] in other.get('related_refs', []))
        for other in originals)}
    writable = {rid} | {item['id'] for item in linked if item['id'] not in shared}
    visible = writable | shared
    questions = [q for q in project.get('questions', []) if active_question(q)
                 and visible.intersection(q.get('related_refs', []))]
    return dict(version='requirement-recommendation-1', requirement_id=rid,
                title=requirement['title'], writable_item_ids=sorted(writable),
                related_item_ids=sorted(visible), shared_readonly_item_ids=sorted(shared),
                question_ids=[q['id'] for q in questions],
                answered_question_ids=[q['id'] for q in questions if answer_bindable(project, q)],
                candidate_only=True)


def contract(local):
    return dict(**local,
        purpose='为一行需求准备供人核对的完整建议，不改变任何采纳状态或已确认内容。',
        saved_answer_binding=dict(
            allowed_question_ids=list(local['answered_question_ids']),
            instruction=(
                'answer_refs 只允许上列当前可绑定回答 ID；列表为空时省略或写 []。question_ids 只是本行相关问题，'
                '不代表已有回答。历史聊天原话若有独立真实来源，可通过 source_refs 提出待人工核对的建议，'
                '但不能把它冒充该问题已保存的回答，不能宣称已回答、已绑定或已落实；原问题保持未决。'
                '未答问题的关联说明可放 findings.related_refs，原需求 related_refs 原样保留。')),
        drafting=(
            '先读原需求、直接关联条款、所有当前已保存回答与真实来源。已有明确答案直接整理，'
            '不能再让用户重复回答。正文、适用业务说明和可观察验收一起提出候选。'
            '原需求用同 kind revise/target_item_id 保留身份。用户无需先填满业务字段或提供新资料：'
            '已有明确来源、但尚未体现在正文或 behavior 中的事实，也应整理成待人工核对的修订候选。'
            '同时处理用户纠正意见与真实新证据；已知部分先写清，未知部分单独保留，不因某一部分未知而把整个维度说成无依据。'
            '直接关联规则须回查其真实 source_refs；来源明确支持且适用于本行的内容可补入 behavior，'
            '但关联规则或历史模型陈述本身不替代真实来源，不把未采纳规则自动升级为人工确认。'
            '逐项提取来源已明确的角色动作、限定权限、触发与可观察结果；能明确某角色的一项动作，'
            '不要求该角色所有能力都已明确才起草。来源已写明入口触发后展示什么时，先整理该入口结果，'
            '不重复追问已描述的动作，也不顺带扩写另一功能的表单和保存子流程。'
            'summary、findings 和下一步只说明具体仍缺的部分，不能因部分账号或异常未知而宣称整个权限或操作结果无依据。'
            '若原正文和业务字段已经准确承载已有事实，就复用原条目，不强制输出冗余 revise。'
            '已有一致候选可复用，不重复创建。共享条款只读，需跨需求修改时说明影响，不能顺带改另一行。'),
        applicability=(
            '业务字段是表达工具，不是逐格问卷。actor、entry、data、flow、permissions、exceptions、result '
            '先整理来源已明确且与本需求有关的事实。缺失但可合理建议的流程、前置条件、异常处理与验收，'
            '可先给具体推荐，新增建议部分以“建议：”开头，条款 epistemic_status=proposed，引用本行真实场景来源，'
            '不把推荐冒充引用原文或已确认规则。未知不能写成没有或不适用；无法提出有意义建议时才保持空白或待定。'
            '只有实际来源支持时才能标不适用及原因。不得把推断的角色、权限、入口、时限或阈值说成既定事实。'
            '关键权限、收益或计费口径、控制操作和删除影响等实质取舍集中提出待决定问题，可给方案但不替用户选。'
            '普通建议随整条需求核对，不逐字段新建确认问题；仅有真正独立的业务决定才追问，可选建议不标为必需。'),
        acceptance=(
            '根据本条已知业务给出可验证验收建议。已有验收需变化用 revise，缺少时可 add acceptance，'
            'related_refs 必须含原需求 ID。写清前提、操作和预期结果；来源明确部分保持事实，'
            '有意义的建议部分显式以“建议：”开头并标 proposed，不能添加未标记的新业务规则。'
            '推荐验收引用的场景材料不代表新增推断已经由资料证实；关键业务取舍仍供人决定。'
            '验收正文也必须属于原需求的行为边界；本条只有另一功能入口时，'
            '可以核对有来源的入口点击后打开哪个页面或弹窗，'
            '不展开目标表单内部字段、保存校验及权限子流程。不能仅删除越界 related_refs 来保留越界正文。'
            '先比较本行已有 rule/acceptance/constraint；同 kind、同适用身份且正文一致时复用，'
            '不再 add 同义副本；确需改变已有内容才按原 ID revise。'
            '验收只是候选，不把已生成、已选需求或测试建议说成测试已通过。'),
        quotation=(
            'scope_evidence 只用于证明本期变化身份，不是用来罗列所有功能来源。'
            'change_type=unspecified 时 scope_evidence=[]，身份冲突写 classification_reason/findings；'
            '有充分身份依据时，quote 逐字复制同一非图片 excerpts.text 的连续原文，保留换行，不拼接。'
            'vision_observations 是此前模型对图片的观察，不能伪装为图片占位 excerpts.text 的逐字原文，'
            '也不能据界面截图认定本期属于新增或修改。图片观察可经 source_refs 作为待人工核对的界面建议依据；'
            '必须同时保留观察限制，不能把观察当用户确认或补出截图不能证明的点击结果、权限和后台规则。'),
        reading_status=(
            '图片读取情况同时核对 source_status 与本轮已有 vision_observations。'
            'saved_visual_observation_count 表示已保存的观察条数，不代表图片所有内容均已读取。'
            '图片 excerpts.text 仍是旧占位说明时，不据此否定已经提供的视觉观察；'
            '逐图区分已有可见观察、看不清的局部及没有观察的部分，避免无依据概括全部或多数尚未分析。'),
        recommended_answers=(
            '真正未决的问题可以通过 questions.options:string[] 提供简洁可编辑的推荐回答。'
            '有事实依据的推荐引用该 question.source_refs 的实际片段；仍是推荐，不自动标已回答。'
            '缺少事实依据的可选方案必须逐项以“建议：”开头，并说明待人工确认；不能伪装为默认、现状或来源已定规则。'
            '没有可靠建议时 options=[]，不为给选项编造数值。用户确认或修改后才成为其回答。'),
        output_boundary=(
            '只输出 writable_item_ids 的 revise 和与原需求直接相关的 rule/acceptance/constraint 新候选。'
            '不新增 non_goal、goal、actor 或其他类型条目。材料未规定的内容属于未知，'
            '只能记为 limitations/findings，不能把未知改写成业务排除项或非目标候选。'
            '原需求候选的 related_refs 必须逐字保留原需求现有列表；新增验收或规则通过自身 related_refs '
            '反向关联原需求 ID 即可，不在原需求上填写本轮临时编号或猜测未来 AC/RULE 编号。'
            '不新建另一条 requirement，不输出 product_context_proposal，不改变全局产品信息。'
            'questions 和 findings 仅限本行相关对象；没有实质新未知时 questions=[]。'))


def constrain_schema(schema, project, local):
    """Give the provider exact known IDs, without weakening output validation.

    New acceptance is linked back to the stable original requirement. Its
    future generated ID must never be guessed inside the requirement row.
    """
    schema = copy.deepcopy(schema)
    original = next(item for item in project['items'] if item['id'] == local['requirement_id'])
    allowed = sorted(set(local['related_item_ids']) | set(local['question_ids'])
                     | set(original.get('related_refs', [])))
    proposal = schema['$defs']['Proposal']
    proposal['properties']['kind']['enum'] = ['requirement', *RELATED_KINDS]
    proposal['properties']['target_item_id']['enum'] = [None, *local['writable_item_ids']]
    proposal['properties']['related_refs']['items']['enum'] = allowed
    # A visible image may support a UI suggestion, but is not a verbatim text
    # witness for an existing/new/modified business classification.
    text_pairs = [dict(source_id=source['id'], excerpt_id=excerpt['id'])
                  for source in project.get('sources', []) if not source.get('image_mime')
                  for excerpt in source.get('excerpts', [])]
    support = proposal['properties']['scope_evidence']['items']['properties']
    support['source_ref'] = ({'allOf': [support['source_ref'], {'enum': text_pairs}]}
                             if text_pairs else False)
    answers = sorted(local['answered_question_ids'])
    answer_refs = proposal['properties']['answer_refs']
    if answers:
        answer_refs['items']['enum'] = answers
    else:
        answer_refs['maxItems'] = 0
    proposal.setdefault('allOf', []).append({
        'if': {'properties': {'action': {'const':'add'}}, 'required':['action']},
        'then': {'properties': {'answer_refs': {'maxItems':0},
                               'related_refs': {'items': {'enum':local['related_item_ids']},
                                                'contains': {'const':local['requirement_id']}}}}})
    proposal.setdefault('allOf', []).append({
        'if': {'properties': {'change_type': {'const':'unspecified'}}, 'required':['change_type']},
        'then': {'properties': {'scope_evidence': {'maxItems':0}}}})
    proposal.setdefault('allOf', []).append({
        'if': {'properties': {'kind': {'const':'requirement'}}, 'required':['kind']},
        'then': {'properties': {'action': {'const':'revise'},
                                'target_item_id': {'const':local['requirement_id']},
                                'related_refs': {'const':copy.deepcopy(original.get('related_refs', []))}}}})
    schema['$defs']['Question']['properties']['related_refs']['items']['enum'] = allowed
    return schema


def input_context(project, local):
    ids = set(local['related_item_ids'])
    questions = [q for q in project.get('questions', []) if q['id'] in local['question_ids']]
    answer_ids = {q.get('decision_answer_id') for q in questions}
    return dict(scope=copy.deepcopy(local),
        clauses=[copy.deepcopy(item) for item in project['items']
                 if item['id'] in ids or item.get('target_item_id') in ids],
        questions=copy.deepcopy(questions),
        saved_answers=[copy.deepcopy(row) for row in project.get('decision_answers', [])
                       if row['id'] in answer_ids],
        notice='范围外材料可用于核对一致性，不授权改写范围外条款；当前原話只作待确认建议依据。')


def compact_context(context):
    """Deduplicate pure audit identity lists; retain all business/source content.

    Current questions, saved words and even historical business snapshots remain
    intact. Only lists made exclusively of source/excerpt identity hashes are
    interned, with every original value still available once in the registry.
    """
    registry = {}
    identity_keys = {'source_id', 'excerpt_id', 'source_hash', 'excerpt_hash', 'source_version'}

    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        result = {}
        for key, item in value.items():
            if (key == 'source_snapshot' and isinstance(item, list) and item
                    and all(isinstance(row, dict) and set(row) <= identity_keys
                            and {'source_id', 'excerpt_id'} <= set(row) for row in item)):
                reference = 'AUDIT-' + digest(item)
                registry.setdefault(reference, copy.deepcopy(item))
                result[key] = {'audit_snapshot_ref': reference}
            else:
                result[key] = project(item)
        return result

    result = project(context)
    observations = [item for response in context.get('vision_observations', [])
                    for item in response.get('result', {}).get('observations', [])]
    for source in result.get('source_status', []):
        source['saved_visual_observation_count'] = sum(
            item.get('source_ref', {}).get('source_id') == source['id'] for item in observations)
    if registry:
        result['audit_snapshot_registry'] = registry
        result['audit_snapshot_notice'] = 'source_snapshot 的 audit_snapshot_ref 指向此处原样保存的来源身份清单；仅去重技术审计副本，当前业务、回答、原资料和视觉观察均未删除。'
    return result


def validate_response(value, project, local):
    """Reject scope expansion before any candidate or question is persisted."""
    if not local:
        return

    def check(condition, message):
        if not condition:
            raise Problem('RECOMMENDATION_SCOPE', message, model_repair_allowed=False)

    check(not value.get('product_context_proposal'), '本次仅完善一条需求，不能同时改写全局产品信息')
    writable = set(local['writable_item_ids'])
    related = set(local['related_item_ids'])
    qids = set(local['question_ids'])
    answer_ids = {q['id'] for q in project.get('questions', [])
                  if q['id'] in qids and answer_bindable(project, q)}
    root = local['requirement_id']
    proposals = value.get('proposals', [])
    temps = {row['temp_id'] for row in proposals}
    refs = related | qids | temps
    for row in proposals:
        if row['action'] == 'revise':
            check(row.get('target_item_id') in writable, '修订超出了本次需求行；其他需求与共享条款保留原状')
        else:
            check(row['kind'] in RELATED_KINDS and root in row.get('related_refs', []),
                  '单条需求推荐只能新增直接关联本条的规则、约束或验收建议')
            check(set(row.get('related_refs', [])) <= related,
                  '新增条款只关联本行稳定对象；另一需求的子流程请在对应行处理，不能仅删关联保留越界正文')
            duplicate = next((item for item in project['items']
                if (item['id'] in related or item.get('target_item_id') in related)
                and item['kind'] == row['kind'] and item.get('applies_to') == row.get('applies_to')
                and item.get('statement', '').strip() == row.get('statement', '').strip()), None)
            check(not duplicate, '本行已有同文条款，请复用其编号；不要新增副本' +
                  (('：' + duplicate['id']) if duplicate else ''))
        check(set(row.get('answer_refs', [])) <= answer_ids,
              '只能绑定本行当前已保存的有效回答；历史原话独立来源请用 source_refs，原问题仍待核对')
        check(row['action'] == 'revise' or not row.get('answer_refs'),
              '新增条款不能绑定回答；请引用实际来源并关联原需求')
        # Preserve existing cross-row references, but do not invent new ones.
        original = next((item for item in project['items'] if item['id'] == row.get('target_item_id')), {})
        if row['kind'] == 'requirement':
            check(row.get('related_refs', []) == original.get('related_refs', []),
                  '原需求的关联列表须逐字保留；新增验收反向关联原需求，未答问题说明放 findings')
            check(row.get('change_type', 'unspecified') != 'unspecified' or not row.get('scope_evidence'),
                  '分类未确定时 scope_evidence 保持空，身份冲突写入分类说明或待核对问题')
            image_sources = {source['id'] for source in project.get('sources', []) if source.get('image_mime')}
            check(all(evidence.get('source_ref', {}).get('source_id') not in image_sources
                      for evidence in row.get('scope_evidence', [])),
                  '图片视觉观察不能充当分类依据的逐字原文；分类未确定时保留 unspecified 与空 scope_evidence')
        check(set(row.get('related_refs', [])) <= refs | set(original.get('related_refs', [])),
              '本次需求建议新增了范围外关联，请在相应需求行单独处理')
    answered = [q for q in project.get('questions', []) if q['id'] in local['answered_question_ids']]
    for parent in value.get('questions', []):
        check(not parent.get('target_question_id') or parent['target_question_id'] in qids,
              '本次不能拆分另一条需求的问题')
        for question in parent.get('decision_points') or [parent]:
            bound = set(question.get('related_refs', []))
            check(bool(bound & (related | temps)) and bound <= refs,
                  '新问题必须直接关联本次需求，不能增加其他需求的待办')
            check(not any(equivalent(q, question) for q in answered),
                  '本条问题已有保存回答，请整理该回答而不是重复追问')
            if not question.get('source_refs'):
                check(all(option.startswith('建议：') or option.startswith('建议:')
                          for option in question.get('options', [])),
                      '无来源的推荐回答须明确标为建议，不能伪装为已定事实')
    for finding in value.get('findings', []):
        check(set(finding.get('related_refs', [])) <= refs,
              '本次完善建议不能新增其他需求的核对事项')


def annotate(project, previous_ids, local):
    if not local:
        return
    for item in project['items']:
        if item['id'] not in previous_ids:
            item['recommendation_requirement_id'] = local['requirement_id']
