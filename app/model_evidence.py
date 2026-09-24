"""Local, bounded record of each attempted model request's final response."""
import json
import os
import re
from pathlib import Path

from .core import now

MAX_FINAL_CHARS = 100_000


class ModelCallEvidence:
    def __init__(self, folder: Path, call_id: str, run_id: str, stage: str, model: str,
                 origin: str, repair_of: str | None, secret: str):
        self.path = folder / 'evidence' / 'model-calls' / (call_id + '.json')
        self.secret = secret
        self.limitations = []
        self.value = dict(call_id=call_id, run_id=run_id, stage=stage, model=model,
                          origin=origin, repair_of=repair_of, started_at=now(),
                          ended_at=None, elapsed_seconds=None, finish_reason=None,
                          usage=None, http_status=None, response_received=False,
                          final_output=None, validation_result='pending', evidence_limitations=[])
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            os.replace(temporary, self.path)
        except OSError:
            if 'evidence_save_failed' not in self.limitations:
                self.limitations.append('evidence_save_failed')

    def response(self, content=None, *, http_status=None, finish_reason=None, usage=None,
                 response_model=None, elapsed_seconds=None):
        safe_finish = finish_reason if finish_reason in ('stop','length','content_filter','tool_calls') else None
        safe_model = response_model if isinstance(response_model, str) and len(response_model) <= 200 else None
        if safe_model:
            safe_model = safe_model.replace(self.secret, '[REDACTED]') if self.secret else safe_model
            safe_model = re.sub(r'Bearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}\b', '[REDACTED]', safe_model, flags=re.I)
        self.value.update(response_received=True, http_status=http_status,
                          finish_reason=safe_finish, response_model=safe_model,
                          elapsed_seconds=elapsed_seconds)
        if isinstance(usage, dict):
            self.value['usage'] = {k:v for k,v in usage.items()
                                   if k in ('prompt_tokens','completion_tokens','total_tokens')
                                   and isinstance(v, int) and not isinstance(v, bool)}
        if isinstance(content, str):
            redacted = content.replace(self.secret, '[REDACTED]') if self.secret else content
            redacted = re.sub(r'Bearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}\b', '[REDACTED]', redacted, flags=re.I)
            if redacted != content:
                self.limitations.append('final_output_redacted')
            if len(redacted) > MAX_FINAL_CHARS:
                redacted = redacted[:MAX_FINAL_CHARS]
                self.limitations.append('final_output_truncated')
            self.value['final_output'] = redacted
        self.value['evidence_limitations'] = self.limitations[:]
        self.save()

    def finish(self, result: str, elapsed_seconds=None):
        self.value['ended_at'] = now()
        if self.value['elapsed_seconds'] is None:
            self.value['elapsed_seconds'] = elapsed_seconds
        self.value['validation_result'] = result
        self.value['evidence_limitations'] = self.limitations[:]
        self.save()
