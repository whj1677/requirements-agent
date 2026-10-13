"""Local recovery of a complete, unambiguous ordinary ingest response."""
import hashlib
import json

from . import ingest
from .benchmark import ordinary_response
from .contracts import validate_response
from .core import digest, execution_hash, ident, now, require
from .model_json import identical_members
from .understanding import normalize_classification_labels


def derive(project, task, run, evidence):
    require(task['action'] == 'organize' and task['status'] == 'failed' and
            not task.get('benchmark') and not task.get('retry_input', {}).get('target') and
            run['stage'] == 'ingest' and run['status'] == 'failed' and
            not run.get('benchmark') and not run.get('result_applied') and
            run.get('user_task_id') == task['id'] and task['run_ids'][-1] == run['id'],
            'RECOVERY_UNAVAILABLE', '该任务不支持本地恢复，请保留原输入继续核对。', 409)
    require(execution_hash(project) == run.get('input_state_hash'), 'STALE_REVISION',
            '聊天、资料或草稿已有变化，不能将旧结果写入当前版本；已有内容保留。', 409)
    attempts = run.get('attempts', [])
    require(len(attempts) == 1 and not attempts[0].get('repair_of'), 'RECOVERY_UNAVAILABLE',
            '该输出涉及多轮修复，不能直接恢复到当前草稿。', 409)
    attempt = attempts[0]
    require(evidence.get('call_id') == attempt['call_id'] and evidence.get('run_id') == run['id'] and
            evidence.get('stage') == 'ingest' and evidence.get('response_received') and
            evidence.get('http_status') == 200 and evidence.get('finish_reason') == 'stop' and
            not evidence.get('evidence_limitations') and not attempt.get('evidence_limitations'),
            'RECOVERY_UNAVAILABLE', '原始输出证据不完整，不能安全恢复；已保存资料保留。', 409)
    request = json.loads(evidence['request_input'])
    require(digest(request) == evidence.get('actual_input_hash') == attempt.get('actual_input_hash'),
            'RECOVERY_UNAVAILABLE', '原始调用输入校验不一致，不能恢复。', 409)
    context = json.loads(request[1]['content'][0]['text'])
    excerpts = context['excerpts']
    require([e['id'] for e in excerpts] == run['sent_excerpt_ids'], 'RECOVERY_UNAVAILABLE',
            '原始调用实际读取范围不一致，不能恢复。', 409)
    current = {(s['id'], e['id']): e for s in project['sources'] if not s['excluded'] for e in s['excerpts']}
    require(all((e['source_id'], e['id']) in current and
                e['text'] == current[(e['source_id'], e['id'])]['text'] for e in excerpts),
            'RECOVERY_UNAVAILABLE', '原始调用引用的来源内容已变化，不能恢复。', 409)
    normalized = identical_members(evidence.get('final_output'))
    require(normalized is not None, 'RECOVERY_UNAVAILABLE',
            '重复字段内容有冲突或输出不完整，无法无损恢复；不自动选择或改写业务内容。', 409)
    value, paths = normalized
    value, empty_benchmark = ordinary_response(value)
    ingest.validate(value, excerpts)
    value, gates = ingest.inherit_new_question_gates(value, project)
    value, classifications = normalize_classification_labels(value, excerpts)
    validate_response(value, 'ingest', project, excerpts, run['document_type'])
    return value, dict(rule='identical-json-members-1', paths=paths,
        omitted_empty_benchmark=empty_benchmark, inherited_question_gates=gates,
        classification_corrections=classifications, raw_hash=digest(evidence['final_output']),
        derived_response_hash=digest(value))


def recover(store, pid, tid, revision):
    # An independent record is the idempotency key. Original runs/calls stay failed.
    previous = next((r for r in store.records(pid, 'local_recovery') if r['task_id'] == tid), None)
    if previous:
        return previous
    task = store.get_record(pid, tid, 'user_task')
    require(bool(task.get('run_ids')), 'RECOVERY_UNAVAILABLE', '任务没有可恢复的输出。', 409)
    run = store.get_record(pid, task['run_ids'][-1], 'run')
    require(len(run.get('attempts', [])) == 1, 'RECOVERY_UNAVAILABLE', '没有唯一的原始输出可供恢复。', 409)
    call_id = run['attempts'][0]['call_id']
    import re
    require(bool(re.fullmatch(r'CALL-[A-Za-z0-9_-]+', call_id)), 'RECOVERY_UNAVAILABLE', '调用证据身份无效。', 409)
    path = store.folder / 'evidence' / 'model-calls' / (call_id + '.json')
    require(path.is_file(), 'RECOVERY_UNAVAILABLE', '原始调用证据不存在，不能恢复。', 409)
    raw_file = path.read_bytes()
    try:
        evidence = json.loads(raw_file)
    except (ValueError, UnicodeError):
        require(False, 'RECOVERY_UNAVAILABLE', '原始调用证据无法读取，不能恢复。', 409)
    record_id = ident('RECOVERY')
    with store.edit(pid, revision, '本地恢复待核对需求草稿') as (project, db):
        tasks = store.records(pid, 'user_task', db)
        runs = store.records(pid, 'run', db)
        require(not any(r['status'] in ('queued', 'running') for r in tasks + runs),
                'TASK_ACTIVE', '当前项目仍有任务运行，结束后才能恢复草稿。', 409)
        require(not any(r['task_id'] == tid for r in store.records(pid, 'local_recovery', db)),
                'RECOVERY_ALREADY_APPLIED', '本次草稿已恢复，请刷新查看。', 409)
        task = next(t for t in tasks if t['id'] == tid)
        run = next(r for r in runs if r['id'] == run['id'])
        require(path.read_bytes() == raw_file, 'RECOVERY_UNAVAILABLE', '调用证据已变化，请重新核对。', 409)
        try:
            value, proof = derive(project, task, run, evidence)
        except (ValueError, KeyError, TypeError, IndexError):
            require(False, 'RECOVERY_UNAVAILABLE', '原始调用证据结构不完整，不能恢复。', 409)
        from .workflow import apply_response
        before = {i['id'] for i in project['items']}
        apply_response(project, value)
        project['messages'][-1].update(local_recovery_id=record_id, run_id=run['id'])
        record = dict(task_id=tid, run_id=run['id'], call_id=call_id,
            created=now(), input_state_hash=run['input_state_hash'], input_revision=revision,
            output_revision=revision + 1, evidence_sha256=hashlib.sha256(raw_file).hexdigest(),
            validation_result='validated_candidates', normalization=proof, external_model_calls=0,
            candidate_ids=[i['id'] for i in project['items'] if i['id'] not in before])
        store.record(pid, 'local_recovery', record, record_id, db)
    store.update_record(pid, record_id, 'local_recovery', output_state_hash=execution_hash(project))
    return store.get_record(pid, record_id, 'local_recovery')
