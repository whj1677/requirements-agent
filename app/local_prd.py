"""Compile saved, version-bound content into a review PRD without a model call."""
import copy

from .core import digest, ident, now, require
from .prd import compile_plan, plan_contract
from .prewrite import status as prewrite_status
from .product_flow import execution_issues
from .response_application import apply_response

RECEIPT_KIND = 'local_prd_receipt'


def preview(store, pid, body):
    """Build the local confirmation view without changing state or calling AI."""
    require(set(body) == {'expected_revision'} and type(body['expected_revision']) is int,
            'INPUT_REQUIRED', '请提交当前版本以核对 PRD 变更预览')
    project = store.get(pid)
    require(project['revision'] == body['expected_revision'], 'STALE_REVISION',
            '内容版本已变化，请重新读取并核对预览', 409)
    from .document_preview import build_prd_preview
    return build_prd_preview(project)


def generate(store, pid, body):
    require(set(body) == {'expected_revision', 'idempotency_key', 'review_draft_confirmed', 'preview_hash'}
            and type(body['expected_revision']) is int
            and isinstance(body['idempotency_key'], str)
            and 8 <= len(body['idempotency_key']) <= 100
            and isinstance(body['preview_hash'], str),
            'INPUT_REQUIRED', '请先核对当前版本的 PRD 变更预览，并提交有效生成编号')
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
        from .document_preview import build_prd_preview
        current_preview = build_prd_preview(p)
        require(current_preview['preview_hash'] == body['preview_hash'], 'STALE_PREVIEW',
                '需求、未确认项或排除范围已变化，请重新核对 PRD 变更预览', 409)
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
        from .prd import saved_prd_plan
        plan=saved_prd_plan(p)
        from .requirement_confirmation import confirmation_current, row_fingerprint
        from .requirements import delivery_items
        current_items={item['id']:item for item in delivery_items(p)
                       if item['kind']=='requirement' and not item.get('detail_contract')
                       and confirmation_current(p,item)}
        charts=[]
        for section in p.get('documents',{}).get('prd',{}).get('content',{}).get('sections',[]):
            for block in section['blocks']:
                if block['kind']=='flowchart':
                    ref=block['ref_ids'][0]
                    if ref in current_items and block['source_hash']==row_fingerprint(p,current_items[ref]):
                        charts.append(dict(requirement_id=ref,flow=copy.deepcopy(block['flow'])))
        if charts:plan['flowcharts']=charts
        response = compile_plan(plan, p, 'prd', include_sketch=False, saved_content=True)
        from .contracts import validate_response
        validate_response(response, 'prd', p, [e for s in p['sources'] for e in s['excerpts']])
        response['summary'] = '已按已保存原文生成 PRD 评审稿，未调用 AI；未决事项保留，请核对后再正式交付。'
        apply_response(p, response)
        artifact = p['documents']['prd']
        from .detail_business_scope import current_reviews
        scoped_details=[review for item in delivery_items(p) if item.get('kind')=='requirement' and not item.get('target_item_id') for review in current_reviews(p,item)]
        if scoped_details:artifact['detail_unknown_scope_reviews']=scoped_details
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
