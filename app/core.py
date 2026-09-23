"""Shared deterministic primitives; no model permissions live here."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / 'requirements-agent-codex-kit-v1.1'
DATA = Path(os.environ.get('RA_DATA_DIR', str(ROOT / 'data'))).resolve()


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
    return digest({k: p[k] for k in ('items', 'questions', 'options')})


def hashes(p):
    return {'brief_hash': brief_hash(p),
            'prd_content_hash': digest(p['documents']['prd']['content']) if 'prd' in p['documents'] else None,
            'ui_spec_hash': digest(p['ui']['spec']) if p.get('ui') else None}


def ui_view_hash(spec):
    """Visual/interaction content only; prose-only intent edits do not create a new UI draft."""
    return digest({'title':spec['title'],'pages':spec['pages']})
