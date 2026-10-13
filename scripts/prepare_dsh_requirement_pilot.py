"""Export only an authorized frozen project's material into a read-only DSH workspace.

No credentials, databases, model results, internal oracles or other projects are
copied. This command prepares files only; it neither starts DSH nor calls a model.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = ROOT / 'evidence/tariff-entry-20261010/business-draft-v1-whole'
DEFAULT_OUTPUT = ROOT / 'evidence/dsh-pilot-20261010/workspace'
SKILL = ROOT / 'skills/requirements-material-review/SKILL.md'
IMAGE_EXTENSIONS = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                    'image/gif': '.gif', 'image/bmp': '.bmp', 'image/tiff': '.tiff'}
DOCUMENT_EXTENSIONS = {'.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.pdf', '.txt', '.md', '.csv'}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _safe_source_path(source_dir, source_id):
    if not re.fullmatch(r'SRC-[A-Za-z0-9_-]+', source_id):
        raise ValueError('Invalid source identity; refusing path construction')
    candidate = (source_dir / source_id).resolve()
    if candidate.parent != source_dir.resolve():
        raise ValueError('Source path escaped the explicitly supplied source directory')
    return candidate


def _question_projection(project, aliases, roots):
    from app.understanding import current_decision_answer
    by_id = {item['id']: key for key, item in roots.items()}
    rows = []
    for question in project.get('questions', []):
        if question.get('superseded_by') or question.get('out_of_scope_reason'):
            continue
        group = current_decision_answer(project, question)
        def refs(values):
            return [dict(source_id=ref['source_id'], excerpt_id=ref['excerpt_id'],
                         **aliases[(ref['source_id'], ref['excerpt_id'])])
                    for ref in values if (ref.get('source_id'), ref.get('excerpt_id')) in aliases]
        rows.append(dict(question_key='Q' + str(len(rows) + 1), text=question['question'],
            reason=question.get('why', ''), saved_answer=question.get('answer'),
            saved_group_words=(group or {}).get('text'),
            current_state=question.get('status', 'open'), blocking=bool(question.get('blocking')),
            related_rows=[by_id[value] for value in question.get('related_refs', []) if value in by_id],
            sources=refs(question.get('source_refs', [])),
            answer_sources=refs(question.get('answer_source_refs', []) +
                question.get('decision_answer_source_refs', []) + (group or {}).get('source_refs', [])),
            existing_options=copy.deepcopy(question.get('options', []))))
    return rows


def build_bundle(snapshot, source_dir, output, *, expected_project_id=None, expected_revision=None,
                 opaque_originals=False):
    """Pure source reads plus a new explicit output folder; never overwrite a run."""
    snapshot, source_dir, output = Path(snapshot).resolve(), Path(source_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; preserve earlier pilot evidence and choose a new workspace')
    if output == source_dir or output.is_relative_to(source_dir) or source_dir.is_relative_to(output):
        raise ValueError('Output must be isolated from source originals')
    raw_snapshot = snapshot.read_bytes()
    project = json.loads(raw_snapshot)
    if expected_project_id is not None and project['id'] != expected_project_id:
        raise ValueError('Frozen project identity does not match the authorized pilot')
    if expected_revision is not None and project['revision'] != expected_revision:
        raise ValueError('Frozen revision does not match the authorized pilot')
    # Set an absolute isolated path before importing any app module. No Store or
    # app factory is instantiated by this exporter.
    os.environ['RA_DATA_DIR'] = str(output.parent / 'preparation-runtime')
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.business_draft import SCHEMA, PROTOCOL, SUMMARY_FIELDS, _aliases, _input, _roots
    from app.business_context import selected_excerpts
    from app.domain_context import active_source_ids

    active = active_source_ids(project)
    sources = [source for source in project.get('sources', []) if source['id'] in active]
    if len({source['id'] for source in sources}) != len(sources):
        raise ValueError('Duplicate source identities in frozen snapshot')
    payloads, excerpts = {}, []
    # Preflight every source before writing any workspace files.
    for source in sources:
        if source.get('image_mime') and source['image_mime'] not in IMAGE_EXTENSIONS:
            raise ValueError('Image MIME is unsupported by pilot bundle: ' + source['image_mime'])
        path = _safe_source_path(source_dir, source['id'])
        data = path.read_bytes()
        if sha256(data) != source['sha256']:
            raise ValueError('Original source hash differs from frozen snapshot: ' + source['id'])
        payloads[source['id']] = data
        chosen = selected_excerpts(source) if source.get('business_context') else source.get('excerpts', [])
        for excerpt in chosen:
            if excerpt.get('source_id') != source['id']:
                raise ValueError('Excerpt belongs to a different source')
            if not isinstance(excerpt.get('text'), str):
                raise ValueError('Frozen excerpt text is not readable text')
            excerpts.append(copy.deepcopy(excerpt))
    aliases, _, source_keys = _aliases(excerpts)
    for source in sources:
        source_keys.setdefault(source['id'], 'S' + str(len(source_keys) + 1))
    input_project = copy.deepcopy(project)
    # Prior model replies, vision conclusions and audit snapshots are not a
    # ground-truth answer sheet for this independent material-reading pilot.
    input_project['messages'] = [message for message in project.get('messages', []) if message.get('role') == 'user']
    context = _input(input_project, excerpts, '', None)
    current = dict(project=dict(name=project['name'], revision=project['revision']),
        content_notice='原材料优先；现有条款/选项可能含旧AI理解，全部需要本次核对。已保存原话不代表相关决定已全部落实。',
        current_rows=context['current_rows'], readonly_current_state=context['readonly_current_state'],
        saved_context=context['saved_context'],
        context_field_states={field: {key: value for key, value in state.items()
            if key in ('state', 'status', 'reason', 'note') and isinstance(value, (str, bool))}
            for field, state in context['context_field_states'].items()
            if field in SUMMARY_FIELDS and isinstance(state, dict)},
        scope_profile={key: value for key, value in context['scope_profile'].items()
                       if key in ('kind', 'requirement_type', 'reason', 'note') and isinstance(value, (str, bool))},
        user_messages=context['recent_messages'],
        current_questions=_question_projection(project, aliases, _roots(project)))
    output.mkdir(parents=True)
    for folder in ('materials', 'images', 'originals'):
        (output / folder).mkdir()
    inventory, locations, all_material = [], [], []
    for source in sources:
        alias = source_keys[source['id']]
        is_image = bool(source.get('image_mime'))
        if is_image:
            extension = IMAGE_EXTENSIONS.get(source['image_mime'])
        else:
            extension = Path(source.get('title', '')).suffix.lower()
            extension = extension if extension in DOCUMENT_EXTENSIONS else '.bin'
        # Some enterprise file filters expose different bytes for an Office
        # extension. Preserve uploaded opaque bytes exactly, as data/sources does.
        original_rel = (Path('originals') / source['id'] if opaque_originals and not is_image else
            Path('images' if is_image else 'originals') / (alias + '-' + source['id'] + extension))
        (output / original_rel).write_bytes(payloads[source['id']])
        if sha256((output / original_rel).read_bytes()) != source['sha256']:
            raise ValueError('Written source hash differs from frozen original: ' + source['id'])
        text_rel = Path('materials') / (alias + '.md')
        text = ['# ' + alias + ' · ' + source.get('title', ''), '',
                'source_id: ' + source['id'], 'sha256: ' + source['sha256'],
                '原件: ' + original_rel.as_posix(), '读取状态: ' + source.get('parse_status', 'unknown'),
                '读取限制: ' + (source.get('failure_reason') or '原记录未提供；不代表内容已全部理解。'), '']
        if is_image:
            text += ['这是图片原件。下列占位/提取文字不是图片理解；请按需要用 read_image 打开原图。', '']
        for excerpt in [entry for entry in excerpts if entry['source_id'] == source['id']]:
            pair = aliases[(source['id'], excerpt['id'])]
            text += ['## ' + pair['source'] + '/' + pair['locator'],
                     'excerpt_id: ' + excerpt['id'], '原始位置: ' + str(excerpt.get('locator', '')),
                     '提取方式: ' + str(excerpt.get('method', '未记录')), '', excerpt['text'], '']
            locations.append(dict(**pair, source_id=source['id'], excerpt_id=excerpt['id'],
                original_locator=excerpt.get('locator'), source_sha256=source['sha256'],
                material_file=text_rel.as_posix(), original_file=original_rel.as_posix(),
                image=is_image, text=excerpt['text'], method=excerpt.get('method', '未记录')))
        rendered = '\n'.join(text)
        (output / text_rel).write_text(rendered, encoding='utf-8', newline='')
        all_material.append(rendered)
        inventory.append(dict(source=alias, source_id=source['id'], title=source.get('title', ''),
            sha256=source['sha256'], image=is_image, image_mime=source.get('image_mime'),
            original_file=original_rel.as_posix(), material_file=text_rel.as_posix(),
            reading_status=source.get('parse_status'), reading_limit=source.get('failure_reason', ''),
            reading=copy.deepcopy(source.get('reading', {})),
            excerpt_count=sum(entry['source_id'] == source['id'] for entry in excerpts),
            container_source_id=source.get('container_source_id'), container_locator=source.get('container_locator')))
    (output / 'material-text.md').write_text('\n\n---\n\n'.join(all_material), encoding='utf-8', newline='')
    write_json(output / 'source-map.json', dict(protocol=PROTOCOL, sources=inventory, locators=locations))
    write_json(output / 'current-workspace.json', current)
    write_json(output / 'business-draft.schema.json', SCHEMA)
    index = dict(bundle_version='dsh-requirements-pilot-1', project_name=project['name'],
        project_revision=project['revision'], snapshot_sha256=sha256(raw_snapshot),
        skill='requirements-material-review', response_protocol=PROTOCOL,
        files=dict(material_text='material-text.md', current_workspace='current-workspace.json',
                   source_map='source-map.json', response_schema='business-draft.schema.json'),
        source_count=len(sources), image_count=sum(source['image'] for source in inventory),
        excerpt_count=len(excerpts), sources=inventory,
        original_storage='opaque_source_id' if opaque_originals else 'named_files',
        response_contract='最终回复仅一个业务草稿JSON对象；不用写工具。实际读取覆盖和限制写在既有limitations数组，不新增根字段。',
        data_boundary='只含此冻结项目已选资料、用户原话和业务字段；未复制数据库、凭据、其他项目、模型旧答复或内部评审答案。')
    write_json(output / 'input-index.json', index)
    if snapshot.read_bytes() != raw_snapshot:
        raise ValueError('Frozen source snapshot changed during preparation')
    return dict(workspace=str(output), skill_file=str(SKILL), source_count=len(sources),
        image_count=index['image_count'], excerpt_count=len(excerpts),
        snapshot_sha256=index['snapshot_sha256'], model_requests=0,
        files=[dict(path=path.relative_to(output).as_posix(), sha256=sha256(path.read_bytes()))
               for path in sorted(output.rglob('*')) if path.is_file()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=DEFAULT_SESSION / 'live/project-before.json')
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SESSION / 'prepared/data/sources')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--expected-project-id', default='P-d4b61a83e6f14612')
    parser.add_argument('--expected-revision', type=int, default=13)
    parser.add_argument('--opaque-originals', action='store_true',
                        help='Keep non-image source bytes under source IDs without extensions')
    args = parser.parse_args()
    boundary = (ROOT / 'evidence/dsh-pilot-20261010').resolve()
    output = args.output.resolve()
    if not output.is_relative_to(boundary) or output == boundary:
        parser.error('Pilot output must be a new folder below evidence/dsh-pilot-20261010')
    report = build_bundle(args.snapshot, args.source_dir, output,
        expected_project_id=args.expected_project_id, expected_revision=args.expected_revision,
        opaque_originals=args.opaque_originals)
    report_path = output.parent / (output.name + '-preparation.json')
    if report_path.exists():
        raise ValueError('Preparation report already exists; do not overwrite evidence')
    write_json(report_path, report)
    print(json.dumps({key: value for key, value in report.items() if key != 'files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
