"""Evidence-bound mechanical fixes; never rewrite business text or stable IDs."""
import copy

import jsonschema

from .contracts import SCHEMA
from .core import digest
from .ingest import TEMPORARY_REFERENCE_FIELDS, STABLE_REFERENCE_FIELDS
from .understanding import answer_bindable, answer_target, current_decision_answer


def normalize(value, project, excerpts, stage):
    result = copy.deepcopy(value)
    changes = []
    if stage not in ('ingest', 'clarify', 'change') or not isinstance(result, dict):
        return result, changes
    declarations = []
    for key, definition in (('proposals', 'Proposal'), ('questions', 'Question')):
        rows = result.get(key, [])
        if not isinstance(rows, list):
            return result, changes
        for row in rows:
            if not isinstance(row, dict):
                return result, changes
            declarations.append((row, SCHEMA['$defs'][definition]['properties']['temp_id'], key))
            if key == 'questions':
                children = row.get('decision_points', [])
                if not isinstance(children, list) or not all(isinstance(c, dict) for c in children):
                    return result, changes
                declarations.extend((c, SCHEMA['$defs']['Question']['properties']['temp_id'], key) for c in children)
    ids = [row.get('temp_id') for row, _, _ in declarations]
    if not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        return result, changes
    occupied = set(ids) | {i['id'] for i in project.get('items', []) + project.get('questions', [])}

    def collect(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in STABLE_REFERENCE_FIELDS and isinstance(child, str):
                    occupied.add(child)
                if key in TEMPORARY_REFERENCE_FIELDS and isinstance(child, list):
                    occupied.update(x for x in child if isinstance(x, str))
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(result)
    mapping = {}
    for row, rule, key in declarations:
        old = row['temp_id']
        prefix = 'TMP-' if key == 'proposals' else 'QTMP-'
        if not old.startswith(prefix) or jsonschema.Draft202012Validator(rule).is_valid(old):
            continue
        index = 0
        new = prefix + digest([old, index])[:24]
        while new in occupied:
            index += 1
            new = prefix + digest([old, index])[:24]
        occupied.add(new)
        mapping[old] = new
        row['temp_id'] = new
        changes.append(dict(rule='temporary-id-syntax-1', field='temp_id', before=old, after=new))

    def rename(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in TEMPORARY_REFERENCE_FIELDS and isinstance(child, list):
                    node[key] = [mapping.get(x, x) if isinstance(x, str) else x for x in child]
                elif key in ('proposal_ref', 'candidate_temp_id') and isinstance(child, str):
                    node[key] = mapping.get(child, child)
                else:
                    rename(child)
        elif isinstance(node, list):
            for child in node:
                rename(child)
    rename(result)
    pairs = {(e['source_id'], e['id']) for e in excerpts}
    by_id, by_claim = {}, {}
    for e in excerpts:
        by_id.setdefault(e['id'], set()).add(e['source_id'])
        if isinstance(e.get('business_claim_id'), str):
            by_claim.setdefault((e['source_id'], e['business_claim_id']), set()).add(e['id'])

    def repair_pair(ref):
        if (not isinstance(ref, dict) or set(ref) != {'source_id', 'excerpt_id'}
                or not all(isinstance(x, str) for x in ref.values())):
            return
        pair = ref['source_id'], ref['excerpt_id']
        if pair in pairs:
            return
        before = dict(ref)
        sources = by_id.get(ref['excerpt_id'], set())
        claims = by_claim.get(pair, set())
        if len(sources) == 1:
            ref['source_id'] = next(iter(sources))
        elif len(claims) == 1:
            ref['excerpt_id'] = next(iter(claims))
        if ref != before:
            changes.append(dict(rule='unique-sent-source-pair-1', before=before, after=dict(ref)))

    def references(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in ('source_refs', 'used_source_refs') and isinstance(child, list):
                    for ref in child:
                        repair_pair(ref)
                elif key == 'source_ref':
                    repair_pair(child)
                else:
                    references(child)
        elif isinstance(node, list):
            for child in node:
                references(child)
    references(result)
    questions = {q['id']: q for q in project.get('questions', [])}
    actual = {(e['source_id'], e['id']): e['text'] for e in excerpts}
    for row in result.get('proposals', []):
        refs = row.get('answer_refs')
        if not isinstance(refs, list):
            continue
        target = next((i for i in project.get('items', []) if i['id'] == row.get('target_item_id')
                       and not i.get('target_item_id') and i['kind'] == row.get('kind')), None)
        if row.get('action') != 'revise' or target is None:
            continue
        retained = []
        for qid in refs:
            q = questions.get(qid) if isinstance(qid, str) else None
            # Only a current, actually cited answer may support an indirect revision.
            # Unknown/stale answers and wrong-kind/target revisions still fail validation.
            group = current_decision_answer(project, q) if q else None
            answer = group.get('text') if group else q.get('answer') if q else None
            sources = group.get('source_refs', []) if group else q.get('answer_source_refs', []) if q else []
            cited = row.get('source_refs', [])
            if not isinstance(cited, list):
                continue
            backed = isinstance(answer, str) and bool(answer.strip()) and any(
                ref in cited and answer in actual.get((ref.get('source_id'), ref.get('excerpt_id')), '')
                for ref in sources if isinstance(ref, dict))
            if q and answer_bindable(project, q) and answer_target(project, q, row) is None and backed:
                changes.append(dict(rule='indirect-answer-source-only-1', temp_id=row['temp_id'],
                                    field='answer_refs', removed=qid, target_item_id=target['id']))
            else:
                retained.append(qid)
        if retained != refs:
            row['answer_refs'] = retained
    return result, changes
