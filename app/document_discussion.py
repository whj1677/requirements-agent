"""Exact chapter ownership and optional closure of an explicit decision commit."""
import copy
import json

from .core import require
from .decision_review import decision_ids
from .product_flow import checkpoint, fingerprint, status
from .requirements import reconcile_items
from .understanding import invalidate_answer_applications


def validate_scope(value):
    require(isinstance(value, dict) and set(value) == {'document_id', 'section_id'}
            and all(isinstance(value[key], str) and 0 < len(value[key]) <= 200
                    for key in ('document_id', 'section_id')),
            'INPUT_REQUIRED', '章节讨论须指定当前文档及章节编号')
    return value


def section_question_ids(project, store, db, scope):
    """Follow persisted identities, excluding unrelated batch-review questions."""
    owned = set()
    processed = set()
    tasks = store.records(project['id'], 'user_task', db)
    runs = store.records(project['id'], 'run', db)

    def belongs(target):
        if not isinstance(target, dict):
            return False
        return (target.get('kind') == 'document'
                and target.get('id') == scope['document_id']
                and target.get('section_id') == scope['section_id']) or (
                    target.get('kind') == 'question' and target.get('id') in owned)

    changed = True
    while changed:
        previous = set(owned)
        legacy_runs = {rid for task in tasks if belongs(task.get('retry_input', {}).get('target'))
                       for rid in task.get('run_ids', [])}
        for run in runs:
            target = run.get('discussion_target')
            if (run['id'] in processed or run.get('result_applied') is not True
                    or not belongs(target) and (target or run['id'] not in legacy_runs)):
                continue
            processed.add(run['id'])
            result = run.get('discussion_result') or {}
            owned.update(qid for qid in result.get('question_ids', []) if isinstance(qid, str))
            # Historical runs did not persist result IDs. Their review IDs can
            # establish ownership only when absent from the actual input; an
            # unrelated old answer in the same review batch is never included.
            snapshot = None
            if 'discussion_result' not in run:
                snapshot = db.execute('SELECT payload FROM revisions WHERE project_id=? AND revision=?',
                                      (project['id'], run.get('revision'))).fetchone()
            if snapshot:
                old_ids = {q['id'] for q in json.loads(snapshot['payload'])['questions']}
                review = run.get('decision_review') or {}
                for key in ('proposal_question_ids', 'reviewed_question_ids', 'split_question_ids'):
                    owned.update(qid for qid in review.get(key, [])
                                 if isinstance(qid, str) and qid not in old_ids)
        for question in project['questions']:
            if (question.get('generation_evidence') or {}).get('run_id') in processed:
                owned.add(question['id'])
        groups = {q.get('decision_group_id') for q in project['questions']
                  if q['id'] in owned and q.get('decision_group_id')}
        for question in project['questions']:
            if question['id'] in owned:
                owned.update(question.get('superseded_by') or [])
            if question.get('prior_question_id') in owned or question.get('decision_group_id') in groups:
                owned.add(question['id'])
        changed = previous != owned
    return owned


def validate_review_scope(project, preview, store, db, scope):
    document = next((doc for doc in project['documents'].values()
                     if doc['id'] == scope['document_id']), None)
    require(document is not None and any(section['section_id'] == scope['section_id']
                                        for section in document['content']['sections']),
            'STALE_DOCUMENT', '文档或章节已变化，请保留输入并重新核对', 409)
    owned = section_question_ids(project, store, db, scope)
    reviewed = set(decision_ids(project, preview['question_id']))
    require(reviewed and reviewed <= owned, 'DOCUMENT_DISCUSSION_SCOPE',
            '本次问题不属于所选章节讨论，不能据此确认章节补充', 409)


def close_review(project, before, scope):
    """Calculate the checkpoint against Store.edit's final versioned payload."""
    finalized = copy.deepcopy(project)
    reconcile_items(before, finalized)
    invalidate_answer_applications(finalized, before)
    stages = status(finalized)
    pending = sorted({row['code'] for row in stages[2]['blockers']})
    from .requirements import delivery_items
    from .understanding import answer_targets, applied_targets
    delivered = {item['id'] for item in delivery_items(finalized)}
    if any(question.get('status') == 'answered' and not question.get('superseded_by')
           and not question.get('out_of_scope_reason')
           and ({item['id'] for item in answer_targets(finalized,question)} & delivered)
           and (question.get('understanding_status') != 'applied'
                or ({item['id'] for item in answer_targets(finalized,question)} & delivered)
                   - applied_targets(finalized,question)) for question in finalized['questions']):
        pending.append('answer_implementation_pending')
    if not all(stage['complete'] for stage in stages[:2]):
        pending.append('previous_checkpoint_pending')
    result = dict(scope, checkpoint_saved=False, complete=False, pending_codes=pending)
    if pending:
        return result
    finalized['revision'] += 1
    checkpoint(finalized, 3, fingerprint(finalized, 3))
    project.setdefault('stage_checks', {})['3'] = finalized['stage_checks']['3']
    result.update(checkpoint_saved=True, complete=True)
    return result
