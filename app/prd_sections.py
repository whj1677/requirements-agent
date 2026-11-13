"""Deterministic PRD summaries compiled only from saved project evidence."""
from .requirements import delivery_items


def function_inventory_block(project):
    requirements = [item for item in delivery_items(project) if item['kind'] == 'requirement']
    return dict(kind='table', ref_ids=[item['id'] for item in requirements],
        text='主要功能清单', title='主要功能清单', table_role='function_inventory',
        columns=['编号', '功能名称', '功能说明'],
        rows=[[item['id'], item.get('title') or item['id'], item['statement']] for item in requirements],
        column_weights=[.14, .24, .62])


def _question_risks(project):
    from .document_preview import _pending_confirmations
    current = delivery_items(project)
    rows, refs = [], []
    questions = {q['id']: q for q in project.get('questions', [])}
    for pending in _pending_confirmations(project, current):
        if pending['kind'] not in ('question', 'answer'):
            continue
        question = questions.get(pending['id'], {})
        related = [ref for ref in pending.get('related_refs', []) if ref]
        refs.extend([pending['id'], *related])
        impact = question.get('why') or '相关流程、规则或验收仍未形成一致口径。'
        if pending['kind'] == 'answer':
            impact = '已有回答尚未完整落实到关联条款。' + impact
            action = '核对回答关联修改，并同步需求与验收。'
        else:
            action = '确认该业务决定，并同步关联需求与验收。'
        rows.append(['未决业务规则', pending['text'], impact, action])
    return rows, refs


def _source_risks(project):
    rows = []
    for source in project.get('sources', []):
        if source.get('excluded') or source.get('parse_status') == 'read':
            continue
        title = source.get('title') or source['id']
        # Parser/internal failure details remain in source diagnostics, not in
        # the product document where they could look like a business assertion.
        reason = '部分内容尚未成功读取，具体限制请在来源资料中核对'
        rows.append(['资料读取限制', '《' + title + '》尚未完整读取',
            reason + '；未读取内容不能作为本版结论依据。',
            '补充读取或明确资料边界后，再核对受影响的需求。'])
    return rows


def _prewrite_risks(project, handled_questions):
    from .prewrite import status
    report = status(project)
    if not report.get('current'):
        return [], []
    rows, refs = [], []
    for finding in report.get('findings', []):
        if finding.get('status') == 'resolved' or finding.get('severity') not in ('blocker', 'warning'):
            continue
        if finding.get('question_id') and finding['question_id'] in handled_questions:
            continue
        related = list(finding.get('related_refs', []))
        refs.extend(related)
        severity = ('阻塞交付' if finding['severity'] == 'blocker' or finding.get('blocks_document')
                    else 'AI建议，待核对')
        action = finding.get('suggested_resolution') or finding.get('recommended_answer') or '请在成文检查中核对并处理。'
        rows.append(['成文检查 · ' + severity,
            finding.get('message') or finding.get('title') or '成文检查待核对',
            '关联对象：' + ('、'.join(related) or '当前文档'), action])
    return rows, refs


def risk_register_blocks(project):
    """Summarize recorded risks; never invent a category, level or resolution."""
    question_rows, question_refs = _question_risks(project)
    source_rows = _source_risks(project)
    prewrite_rows, prewrite_refs = _prewrite_risks(project, set(question_refs))
    rows = question_rows + source_rows + prewrite_rows
    if not rows:
        return [dict(kind='narrative', ref_ids=[],
            text='当前没有已记录的未决业务问题、资料读取限制或未解决成文检查风险；这不替代后续业务评审。')]
    refs = list(dict.fromkeys(question_refs + prewrite_refs))
    return [dict(kind='table', ref_ids=refs, text='风险点与待确认', title='风险点与待确认',
        table_role='risk_register', columns=['风险来源', '风险点', '影响及状态', '建议处理'],
        rows=rows, column_weights=[.16, .30, .27, .27])]
