"""Shared deterministic primitives; no model permissions live here."""
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / 'requirements-agent-codex-kit-v1.1'
FROZEN = bool(getattr(sys, 'frozen', False))
USER_HOME = Path(os.environ['LOCALAPPDATA']) / 'RequirementsAgent' if FROZEN else ROOT
DATA = USER_HOME / 'data' if FROZEN else Path(os.environ.get('RA_DATA_DIR', str(ROOT / 'data'))).resolve()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).replace('\r\n', '\n').encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]


class Problem(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition, code, message, status=400):
    if not condition:
        raise Problem(code, message, status)


def read_json(path):
    return json.loads(Path(path).read_text('utf-8'))


def brief_hash(p):
    content={k:p[k] for k in ('items','questions','options')}
    # Old snapshots retain their original hash; new scope statements are real inputs.
    if 'product_context' in p:content['product_context']=p['product_context']
    return digest(content)


def execution_hash(p):
    """Input identity for paid work, including sources and derived artifacts."""
    return digest({k:p.get(k) for k in ('revision','items','questions','options','sources',
        'documents','ui','messages','reference_mode','mode','product_context','stage_checks',
        'sketch_review','delivery_scope','requirement_relations')})


def hashes(p):
    return {'brief_hash': brief_hash(p),
            'prd_content_hash': digest(p['documents']['prd']['content']) if 'prd' in p['documents'] else None,
            'mrd_content_hash': digest(p['documents']['mrd']['content']) if 'mrd' in p['documents'] else None,
            'ui_spec_hash': digest(p['ui']['spec']) if p.get('ui') else None}


def ui_view_hash(spec):
    """Visual/interaction content only; prose-only intent edits do not create a new UI draft."""
    return digest({'title':spec['title'],'pages':spec['pages']})
