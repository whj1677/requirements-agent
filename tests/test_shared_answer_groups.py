"""The browser's shared-revision grouping is accepted by the real review logic."""
import json
import shutil
import subprocess
import copy

import pytest

from app.core import Problem
from app.decision_review import apply_review, build_preview, save_raw_answer
from app.requirements import behavior_review_current
from app.store import Store
from app.understanding import annotate_candidates, applied_targets
from tests.helpers import prepared
from tests.test_decision_qg_binding import group_project, revision
from tests.test_scope_frontend import WEB


def test_shared_candidate_frontend_output_commits_once_for_both_bound_answers(tmp_path):
    store, project, _ = group_project(tmp_path)
    row = save_raw_answer(project, 'QG-boundary', '相接边界允许。例外情况拒绝保存。', store)
    candidate = revision(project, row)
    candidate.update(answer_refs=['Q-boundary','Q-exception'], statement=row['text'])
    annotate_candidates(project, [candidate])
    project['items'].append(candidate)
    coverage = {'Q-boundary':dict(status='clear',answer_quote='相接边界允许。',candidate_item_ids=[candidate['id']]),
                'Q-exception':dict(status='clear',answer_quote='例外情况拒绝保存。',candidate_item_ids=[candidate['id']])}
    script = r'''
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/answer-actions.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {previewGroups}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      let data='';for await(const part of process.stdin)data+=part;
      const {project,coverage}=JSON.parse(data);
      process.stdout.write(JSON.stringify(previewGroups(project,project.questions,coverage)));
    '''
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script], cwd=WEB,
                            input=json.dumps(dict(project=project, coverage=coverage)),
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    groups = json.loads(result.stdout)
    assert len(groups) == 1 and groups[0]['candidate_item_ids'] == [candidate['id']]
    view = build_preview(project, dict(expected_revision=project['revision'], question_id='QG-boundary',
        answer_id=row['id'], coverage=[dict(decision_id=qid,**value) for qid,value in coverage.items()], groups=groups))
    summary = apply_review(project, view, [groups[0]['id']])
    assert summary['pending_group_ids'] == [] and summary['pending_decision_ids'] == []
    assert candidate['selection_status'] == 'deferred'
    assert all(q['understanding_status']=='applied' and applied_targets(project,q)=={'RULE-0001'}
               for q in project['questions'])


def model_requirement_preview(tmp_path, review_ids=None):
    store = Store(tmp_path)
    project = prepared(store)
    answer = save_raw_answer(project, 'Q-0001', '相接边界允许保存。', store)
    candidate = dict(copy.deepcopy(project['items'][0]), id='C-model', action='revise',
        target_item_id='REQ-0001', answer_refs=['Q-0001'], statement='管理员维护电价时段，相接边界允许保存。',
        selection_status='candidate', behavior_review_required=True,
        behavior={k:'合成已明确内容' for k in ['actor','entry','flow','data','permissions','result','exceptions']})
    annotate_candidates(project, [candidate])
    project['items'].append(candidate)
    body = dict(expected_revision=project['revision'], question_id='Q-0001',
        coverage=[dict(decision_id='Q-0001',status='clear',answer_quote=answer['text'])],
        groups=[dict(id='model-group',decision_ids=['Q-0001'],candidate_item_ids=['C-model'],target_item_ids=['REQ-0001'])])
    if review_ids is not None:
        body['review_behavior_item_ids'] = review_ids
    return project, candidate, body


def test_model_requirement_behavior_is_only_reviewed_after_explicit_commit(tmp_path):
    project, candidate, body = model_requirement_preview(tmp_path, ['C-model'])
    before = copy.deepcopy(project)
    view = build_preview(project, body)
    assert project == before and not behavior_review_current(candidate)
    target = view['groups'][0]['targets'][0]
    assert target['review_behavior'] is True
    assert target['before'] == project['items'][0] and target['after'] == candidate
    result = apply_review(project, view, ['model-group'])
    assert result['applied_group_ids'] == ['model-group']
    original = project['items'][0]
    assert original['id'] == 'REQ-0001' and original['statement'] == candidate['statement']
    assert original['behavior'] == candidate['behavior']
    assert behavior_review_current(original) and behavior_review_current(candidate)
    assert project['questions'][0]['understanding_status'] == 'applied'


def test_model_behavior_review_cannot_be_assumed_or_cover_unseen_items(tmp_path):
    project, candidate, body = model_requirement_preview(tmp_path)
    before = copy.deepcopy(project)
    view = build_preview(project, body)
    with pytest.raises(Problem) as exc:
        apply_review(project, view, ['model-group'])
    assert exc.value.code == 'BEHAVIOR_REVIEW_REQUIRED' and project == before
    # The original ID is not the model candidate whose complete after-state is reviewed.
    for ids in [['REQ-0001'], ['unrelated'], ['C-model','C-model']]:
        with pytest.raises(Problem) as exc:
            build_preview(project, dict(body, review_behavior_item_ids=ids))
        assert exc.value.code == 'REFERENCE_INVALID'
    view = build_preview(project, dict(body, review_behavior_item_ids=['C-model']))
    apply_review(project, view, [])
    assert not behavior_review_current(candidate), 'unselected group must never be approved'
