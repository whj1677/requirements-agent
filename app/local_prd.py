"""Compile saved, version-bound content into a review PRD without a model call."""
import copy

from .core import digest, ident, now, require
from .prd import compile_plan, plan_contract
from .prewrite import status as prewrite_status
from .product_flow import execution_issues
from .response_application import apply_response

RECEIPT_KIND = 'local_prd_receipt'


def generate(store, pid, body):
    require(set(body) == {'expected_revision', 'idempotency_key', 'review_draft_confirmed'}
            and type(body['expected_revision']) is int
            and isinstance(body['idempotency_key'], str)
            and 8 <= len(body['idempotency_key']) <= 100,
            'INPUT_REQUIRED', '请提交当前版本与有效生成编号')
    require(body['review_draft_confirmed'] is True, 'REVIEW_REQUIRED',
            '请明确选择保留未决事项的 PRD 评审稿', 409)
    request_hash = digest(body)

    def previous(db=None):
        receipt = next((r for r in store.records(pid, RECEIPT_KIND, db)
                        if r['idempotency_key'] == body['idempotency_key']), None)
        if receipt:
            require(receipt['request_hash'] == request_hash, 'IDEMPOTENCY_CONFLICT',
                    '生成编号已用于另一份内容，请重新核对', 409)
        return receipt

    old = previous()
    if old:
        return dict(project=store.get(pid), artifact_id=old['artifact_id'], receipt=old, repeated=True)
    with store.edit(pid, body['expected_revision'], '本机将已保存内容编排为 PRD 评审稿，保留未决事项') as (p, db):
        require(previous(db) is None, 'IDEMPOTENCY_CONFLICT', '相同生成请求正在处理', 409)
        require(not any(r.get('status') in ('queued', 'running') for kind in ('user_task', 'run')
                        for r in store.records(pid, kind, db)),
                'TASK_RUNNING', '当前项目任务仍在处理，请等待完成后成文', 409)
        report = prewrite_status(p)
        require(report['current'] and report['status'] in ('passed', 'needs_confirmation')
                and not any(f.get('hard_block') and f.get('status') != 'resolved'
                            for f in report['findings']),
                'PREWRITE_REQUIRED', '请先完成当前版本成文检查；失败、过期或引用及结构错误不能直接成文', 409)
        issues = execution_issues(p, 'prd', 'prd')
        require(not issues, 'STAGE_BLOCKED', '；'.join(issues), 409)
        contract = plan_contract(p, 'prd')
        require(contract['normative_item_ids'], 'INPUT_REQUIRED', '当前没有可成文的需求，请先核对需求范围', 409)
        from .requirement_confirmation import _sources_valid
        for item in p['items']:
            if item['id'] in contract['normative_item_ids']:
                _sources_valid(p, item)
        plan = dict(plan_version='4', sections=[dict(section_key='function',
            context_refs=contract['context_ref_ids'], normative_refs=contract['normative_item_ids'],
            discussion_refs=[], explanations=[])])
        response = compile_plan(plan, p, 'prd', include_sketch=False)
        from .contracts import validate_response
        validate_response(response, 'prd', p, [e for s in p['sources'] for e in s['excerpts']])
        response['summary'] = '已按已保存原文生成 PRD 评审稿，未调用 AI；未决事项保留，请核对后再正式交付。'
        apply_response(p, response)
        artifact = p['documents']['prd']
        artifact.update(prewrite_required=True, sketch_policy='excluded',
            assembly_version='prd-plan-4-local', assembly_origin='saved_content', model_calls=0)
        p['stale_document_kinds'] = [k for k in p.get('stale_document_kinds', []) if k != 'prd']
        p['document_update_needed'] = bool(p['stale_document_kinds'])
        store.record(pid, 'document_artifact', copy.deepcopy(artifact), db=db)
        task_id = ident('TASK')
        store.record(pid, 'user_task', dict(action='document', label='本机生成 PRD 评审稿',
            status='succeeded', message=response['summary'], created=now(), completed=now(),
            input_revision=p['revision'], source_ids=[], stages=['prd'], run_ids=[],
            idempotency_key='local-prd:'+body['idempotency_key'], request_hash=request_hash,
            max_calls=0, calls=0, cost=0, completed_steps=1,
            step_states=[dict(stage='prd', status='succeeded')],
            assembly_origin='saved_content', document_type='prd', document_artifact_id=artifact['id']), task_id, db)
        receipt = dict(idempotency_key=body['idempotency_key'], request_hash=request_hash,
            input_revision=p['revision'], result_revision=p['revision'] + 1,
            artifact_id=artifact['id'], plan_hash=digest(plan), prewrite_id=report['id'],
            unresolved_finding_ids=[f['id'] for f in report['findings'] if f['status'] != 'resolved'],
            model_calls=0, task_id=task_id, created=now())
        receipt['id'] = store.record(pid, RECEIPT_KIND, receipt, db=db)
    return dict(project=p, artifact_id=artifact['id'], receipt=receipt, repeated=False)
