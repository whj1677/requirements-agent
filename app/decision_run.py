"""A single optional semantic audit inside an explicitly requested draft task."""
import asyncio
import time

from .budgets import check_context
from .core import Problem, digest, dumps, ident, now, require
from .decision_semantics import apply_audit, build_request
from .model_evidence import ModelCallEvidence
from .provider import origin


async def review_generated_draft(store, provider, original, staged, run, config,
                                 excerpts, budget, attempts, events, calls):
    """Keep the valid primary draft even if the extra audit cannot complete.

    This never starts a repair or retries an unknown paid result. The original
    task's provider, sources, budget and cancellation remain authoritative.
    """
    pid, rid = original['id'], run['id']
    started = time.monotonic()
    result = dict(status='not_needed', run_id=rid, calls=0, elapsed_seconds=0)
    evidence = None
    attempt = None
    try:
        local=run.get('requirement_recommendation_scope')
        question_ids=None
        if local:
            original_ids={q['id'] for q in original['questions']}
            question_ids=set(local['question_ids']) | {q['id'] for q in staged['questions'] if q['id'] not in original_ids}
        request = build_request(staged, original, excerpts, question_ids=question_ids)
        if request is None:
            return calls, result
        result['target_count'] = len(request['targets'])
        require(store.get_record(pid, rid, 'run')['status'] != 'cancelled',
                'CANCELLED', '任务已取消；未发送决定复核请求')
        messages = request['messages']
        check_context(messages, config)
        if budget:
            budget.available()
        authorization = dict(store.get_record(pid, rid, 'run')['authorization'],
            subphase='decision_semantics', schema_hash=digest(request['schema']),
            prompt_hash=digest(messages[0]['content']), target_hash=digest(request['targets']))
        call_id = ident('CALL')
        raw_input = dumps(messages)
        metrics = dict(message_count=len(messages), characters=len(raw_input),
                       utf8_bytes=len(raw_input.encode('utf-8')), token_estimate=None)
        attempt = dict(call=len(attempts)+1, call_id=call_id, subphase='decision_semantics',
            max_tokens=config['max_tokens'], authorization_hash=digest(authorization),
            actual_input_hash=digest(messages))
        evidence = ModelCallEvidence(store.folder, call_id, rid, 'decision_semantics',
            config['model'], origin(config), None, provider.key(config), request_budget=budget)
        evidence.request_context(config, messages, authorization, metrics)
        events.append(dict(time=now(), phase='逐项核对问题与回答覆盖', call_id=call_id))
        if not budget:
            calls += 1
        store.update_run(pid, rid, events=events, decision_review=dict(result, status='running'),
                         **({} if budget else {'calls': calls}))
        value, meta = await asyncio.wait_for(provider.request(config, messages, evidence=evidence),
                                              timeout=config['timeout'])
        if budget:
            require(evidence.value.get('request_state') == 'response_received',
                    'BUDGET_AUTH_REQUIRED', '决定复核响应缺少出站计数绑定，未采纳复核建议')
        attempt.update(meta)
        require(store.get_record(pid, rid, 'run')['status'] != 'cancelled',
                'CANCELLED', '任务已取消；未采纳决定复核建议')
        checked = apply_audit(staged, value, request['targets'])
        result.update(status='proposal_ready', **checked)
        evidence.finish('accepted', round(time.monotonic()-started, 3))
        attempt['validation_result'] = 'accepted'
    except asyncio.CancelledError:
        if evidence and attempt:
            evidence.finish('CANCELLED', round(time.monotonic()-started, 3))
            attempt.update(error='CANCELLED', validation_result='CANCELLED')
        raise
    except Exception as error:
        error = (Problem('TIMEOUT', '决定复核等待超时；请求结果未知，未自动重发')
                 if isinstance(error, asyncio.TimeoutError) else error
                 if isinstance(error, Problem) else Problem('INTERNAL_ERROR', '决定复核未完成，请在原处人工核对'))
        if evidence and attempt:
            evidence.finish(error.code, round(time.monotonic()-started, 3))
            attempt.update(error=error.code, validation_result=error.code)
        if error.code == 'CANCELLED':
            raise error
        result.update(status='pending_manual_review', error=error.code,
            message='有效草稿已保留；问题拆分与回答覆盖尚待核对。'+error.message)
        events.append(dict(time=now(), phase='草稿已形成，问题与回答覆盖仍待人工核对'))
    finally:
        if attempt and evidence:
            attempt.setdefault('usage', evidence.value.get('usage'))
            attempt.setdefault('finish_reason', evidence.value.get('finish_reason'))
            attempt['evidence_limitations'] = evidence.limitations[:]
            attempts.append(attempt)
        if budget:
            calls = store.get_record(pid, rid, 'run')['calls']
        result.update(calls=int(bool(evidence) and evidence.value.get('request_state') != 'not_sent'),
                      elapsed_seconds=round(time.monotonic()-started, 3))
        if result['status'] != 'not_needed':
            staged['decision_review_status'] = result
        store.update_run(pid, rid, calls=calls, attempts=attempts, events=events, decision_review=result)
    return calls, result
