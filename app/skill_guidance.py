"""Bounded, packaged method guidance; never a source, tool, or decision engine."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

from .core import KIT, Problem, dumps

ROOT = KIT / 'prompts' / 'skills'
VERSION = 'builtin-skills-1'
PINNED = {
    'anthropic-write-spec': '2574e29377c7173d0cecffef29a6ecbd7f8ccb5d',
    'pm-create-prd': '2a12295a7717569baa7be181b4cfebe3ca38a6bb',
    'pm-strategy-red-team': '2a12295a7717569baa7be181b4cfebe3ca38a6bb',
    'mermaid-diagrams': '3027f20f3181758385a1bb8c022d4041dfb4de84',
}
PATHS = {name: {'vendor/' + name + '/SKILL.md'} for name in PINNED}
PATHS['mermaid-diagrams'].add('vendor/mermaid-diagrams/references/flowcharts.md')
MAX_FILE_BYTES = 128_000
MAX_SELECTED_BYTES = 24_000
BEGIN = '\n<application-skill-guidance>\n'
END = '\n</application-skill-guidance>\n'
STAGES = {'interview', 'ingest', 'clarify', 'change', 'prd', 'review'}


def _fail(reason):
    raise Problem('SKILL_RESOURCE_INVALID', '内置技能准备失败：' + reason,
                  model_repair_allowed=False)


def _read(relative):
    path = ROOT / relative
    try:
        parts = PurePosixPath(relative)
        if parts.is_absolute() or '..' in parts.parts or '\\' in relative:
            _fail('资源路径不合法')
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            _fail('资源不允许链接：' + relative)
        if not path.resolve().is_relative_to(ROOT.resolve()):
            _fail('资源超出内置目录')
        if path.stat().st_size > MAX_FILE_BYTES:
            _fail('资源超过大小上限：' + relative)
        raw = path.read_bytes()
        text = raw.decode('utf-8')
    except (OSError, UnicodeError, ValueError):
        _fail('资源缺失或不是有效 UTF-8：' + relative)
    if '可信任务头：' in text or BEGIN in text or END in text:
        _fail('资源含保留协议边界：' + relative)
    return text, hashlib.sha256(raw).hexdigest()


def flow_applicable(project, stage, scope=None, detail_requested=False):
    rows = [i for i in project.get('items', []) if i.get('kind') == 'requirement'
            and not i.get('superseded_by') and i.get('applies_to') == 'to_be'
            and i.get('selection_status') in ({'selected'} if stage in ('prd', 'review') else {'selected', 'candidate'})
            and not i.get('target_item_id')]
    if scope:
        rows = [i for i in rows if i.get('id') == scope.get('requirement_id')]
    for item in rows:
        detail = item.get('detail_contract') or {}
        applicable = detail.get('applicability', {}).get('flow', {}).get('applicable')
        if applicable is False:
            continue
        if applicable is True or detail.get('flow', {}).get('nodes') or (item.get('behavior') or {}).get('flow'):
            return True
        if detail_requested:
            return True
    return bool(detail_requested and not rows and not scope)


def load(project, stage, scope=None, detail_requested=False):
    if stage not in STAGES:
        return None
    choices = [('pm-strategy-red-team', 'review')] if stage == 'review' else [('anthropic-write-spec', 'writing')]
    if stage == 'prd':
        choices.append(('pm-create-prd', 'writing'))
    if stage != 'interview' and flow_applicable(project, stage, scope, detail_requested):
        choices.append(('mermaid-diagrams', 'flow'))
    try:
        manifest_text, manifest_hash = _read('upstream.json')
        records = json.loads(manifest_text)['skills']
        if len({r['id'] for r in records}) != len(records):
            _fail('上游技能 ID 重复')
        by_id = {r['id']: r for r in records}
        policy, policy_hash = _read('application-policy.md')
        selected, texts = [], []
        for skill_id, purpose in choices:
            record = by_id[skill_id]
            if (record['upstream_commit'] != PINNED[skill_id] or record['version'] != PINNED[skill_id]
                    or record['path'] not in PATHS[skill_id]):
                _fail('上游版本或主文件不在白名单：' + skill_id)
            main, main_hash = _read(record['path'])
            if main_hash != record['sha256']:
                _fail('上游文件 hash 不匹配：' + skill_id)
            sections = record['selection'][purpose]['sections']
            if stage == 'interview':
                sections = [s for s in sections if s['heading'] in ('### Problem Statement', '### Open Questions')]
            if not sections or len(sections) > 16:
                _fail('选段数量不合法：' + skill_id)
            selected_sections, excerpts = [], []
            for section in sections:
                relative = section['path']
                if relative not in PATHS[skill_id]:
                    _fail('选段文件不在白名单：' + skill_id)
                text, sha = (main, main_hash) if relative == record['path'] else _read(relative)
                file_record = next((f for f in record['files'] if f['path'] == relative), None)
                if not file_record or file_record['sha256'] != sha or file_record['upstream_commit'] != PINNED[skill_id]:
                    _fail('选段文件 hash 或版本不匹配：' + skill_id)
                lines = text.splitlines(keepends=True)
                start, end, heading = section['start_line'], section['end_line'], section['heading_line']
                if not all(type(n) is int for n in (start, end, heading)) or not 1 <= heading <= start <= end <= len(lines):
                    _fail('选段行号不合法：' + skill_id)
                if lines[heading-1].rstrip('\r\n') != section['heading'] or not section['heading'].startswith('#'):
                    _fail('选段标题与原件不匹配：' + skill_id)
                excerpt = ''.join(lines[start-1:end])
                selected_sections.append(dict(section, sha256=sha,
                    selected_sha256=hashlib.sha256(excerpt.encode('utf-8')).hexdigest()))
                excerpts.append(excerpt)
            selected.append(dict(id=skill_id, capability='business-flow' if purpose == 'flow' else 'prd-quality',
                                 version=record['version'], upstream_commit=record['upstream_commit'],
                                 sha256=main_hash, sections=selected_sections))
            texts.append('\n## 上游方法片段：' + skill_id + '\n' + '\n'.join(excerpts))
        # Policy comes last, so upstream examples cannot override app contracts.
        text = '\n'.join(texts) + '\n' + policy
        if len(text.encode('utf-8')) > MAX_SELECTED_BYTES:
            _fail('本次选段超过大小上限')
        return dict(manifest=dict(version=VERSION, upstream_manifest_sha256=manifest_hash,
                    policy=dict(version='application-policy-1', sha256=policy_hash), selected=selected,
                    text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest()), text=text)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        _fail('上游清单或选段结构无效')


def apply(header, system, guidance):
    if not guidance:
        return system
    header['builtin_skills'] = copy.deepcopy(guidance['manifest'])
    return system + BEGIN + guidance['text'] + END


def freeze(system):
    """Snapshot once after assembly; repairs never reread package files."""
    prefix, raw = system.split('可信任务头：', 1)
    manifest = json.loads(raw).get('builtin_skills')
    if not manifest:
        return None
    text = prefix.split(BEGIN, 1)[1].split(END, 1)[0]
    return dict(manifest=manifest, text=text)


def preserve(messages, guidance):
    if not guidance:
        return messages
    prefix, raw = messages[0]['content'].split('可信任务头：', 1)
    header = json.loads(raw)
    if header.get('builtin_skills') == guidance['manifest'] and BEGIN in prefix:
        return messages
    result = copy.deepcopy(messages)
    result[0]['content'] = apply(header, prefix, guidance) + '\n可信任务头：' + dumps(header)
    return result
