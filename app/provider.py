"""Model credentials, HTTP transport and bounded response decoding."""
import copy
import json
import time
from urllib.parse import urlsplit

import httpx
import jsonschema

from .core import Problem, digest, require
from .config import ProjectEnvironment, usable_key
from .budgets import POLICY_DEFAULTS
# Preserve the existing Python import surface while assembly has one owner.
from .request_context import (STAGES, assemble, input_metrics, request_schema,
                              review_documents, review_unknown_evidence,
                              ui_structure_example, review_structure_example,
                              answer_binding_tasks, response_boundary)

DEFAULT = dict(name='DeepSeek 官方', base_url='https://api.deepseek.com', model='deepseek-flash',
               key_env='RA_DEEPSEEK_API_KEY', vision='documented', json_mode=True, timeout=120,
               max_calls=None, max_tokens=48000, context_chars=520000, action_seconds=300,
               thinking_disabled=True, review_thinking=True, documented_at='2026-09-27', local_allowed=False, proxy='', input_price=None, output_price=None,
               **POLICY_DEFAULTS)


def delimiter_hint(content):
    """Diagnose bracket mismatches without repairing or accepting invalid output."""
    stack=[];quoted=False;escaped=False
    for pos,char in enumerate(content):
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
            continue
        if char=='"':quoted=True
        elif char in '[{':stack.append((char,pos))
        elif char in ']}':
            if not stack:return f'字符偏移 {pos} 的 {char} 没有对应开括号。'
            opener,start=stack[-1];expected=']' if opener=='[' else '}'
            if char!=expected:return f'括号层级错误：字符偏移 {start} 的 {opener} 尚未关闭；偏移 {pos} 处应先用 {expected} 关闭它，实际却为 {char}。不要只在结尾增加大括号。'
            stack.pop()
    if stack:return '输出结束时仍有未关闭容器：'+str(stack[-4:])+'；按从内到外次序关闭数组与对象。'
    return ''


def normalize_review_result_tail(content):
    """Remove one premature root close before a sole, complete result field.

    Every existing field retains its decoded value. This does not repair nested
    grouping, missing text, duplicate keys, or a partial result.
    """
    def unique_object(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('duplicate key')
            value[key]=item
        return value
    decoder=json.JSONDecoder(object_pairs_hook=unique_object,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    try:
        stripped=content.lstrip()
        prefix,end=decoder.raw_decode(stripped)
        if not isinstance(prefix,dict) or prefix.get('stage')!='review' or 'result' in prefix:return None
        tail=stripped[end:].lstrip()
        if not tail.startswith(','):return None
        suffix=decoder.decode('{'+tail[1:])
        if not isinstance(suffix,dict) or set(suffix)!={'result'}:return None
        candidate=decoder.decode(stripped[:end-1]+stripped[end:])
        if set(candidate)!=set(prefix)|{'result'}:return None
        if any(candidate[key]!=item for key,item in prefix.items()):return None
        jsonschema.Draft202012Validator(request_schema('review')).validate(candidate)
        return candidate,len(content)-len(stripped)+end-1
    except (ValueError,TypeError,jsonschema.ValidationError):
        return None


def normalize_ingest_result_tail(content):
    """Join complete ingest root fields separated by one premature root close.

    Only the three declared trailing metadata fields may follow the first
    object. No member, scalar value, nested grouping, or ordering is rewritten.
    The original response remains the evidence; all semantic checks still run.
    """
    from .core import unique_json_object
    decoder=json.JSONDecoder(object_pairs_hook=unique_json_object,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    try:
        stripped=content.lstrip()
        prefix,end=decoder.raw_decode(stripped)
        if not isinstance(prefix,dict) or prefix.get('stage')!='ingest':return None
        tail=stripped[end:].lstrip()
        if not tail.startswith(','):return None
        suffix=decoder.decode('{'+tail[1:])
        if (not isinstance(suffix,dict) or not suffix or
                not set(suffix)<={'product_context_proposal','benchmark_analysis','result'} or
                set(prefix)&set(suffix)):return None
        candidate=decoder.decode(stripped[:end-1]+stripped[end:])
        if list(candidate)!=list(prefix)+list(suffix):return None
        if candidate!={**prefix,**suffix}:return None
        jsonschema.Draft202012Validator(request_schema('ingest')).validate(candidate)
        return candidate,len(content)-len(stripped)+end-1
    except (ValueError,TypeError,jsonschema.ValidationError):
        return None


def origin(config):
    u = urlsplit(config['base_url'])
    require(u.scheme in ('https', 'http') and u.hostname and not u.username and not u.password and not u.query and not u.fragment, 'CONFIG_INVALID', '模型地址无效')
    require(u.scheme == 'https' or config.get('local_allowed'), 'CONFIG_INVALID', 'HTTP 模型需要显式本地白名单授权')
    return f'{u.scheme}://{u.netloc}'


def business_draft_schema(messages):
    """Only the application-owned first system header may select this protocol."""
    if not messages or messages[0].get('role') != 'system':
        return None
    content = messages[0].get('content')
    if not isinstance(content, str) or '可信任务头：' not in content:
        return None
    from .core import strict_model_json
    try:
        header = strict_model_json(content.split('可信任务头：', 1)[1])
    except ValueError:
        raise Problem('SCHEMA_INVALID', '可信任务头无法读取；未发送模型请求', model_repair_allowed=False)
    if not isinstance(header, dict) or header.get('response_protocol') != 'business-draft-v1':
        return None
    schema = header.get('schema')
    if not isinstance(schema, dict):
        raise Problem('SCHEMA_INVALID', '业务草稿缺少结构化输出 Schema；未发送模型请求', model_repair_allowed=False)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        raise Problem('SCHEMA_INVALID', '业务草稿输出 Schema 无效；未发送模型请求', model_repair_allowed=False)
    return schema


def responses_input(messages):
    """Translate message content without discarding unsupported input parts."""
    result = []
    for message in messages:
        role, content = message.get('role'), message.get('content')
        require(role in ('system', 'developer', 'user', 'assistant'),
                'PARAMETER_UNSUPPORTED', '业务草稿只支持普通消息输入；未发送模型请求')
        if isinstance(content, list):
            parts = []
            for part in content:
                require(isinstance(part, dict), 'PARAMETER_UNSUPPORTED', '模型输入内容块无法转换')
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    parts.append(dict(type='output_text' if role == 'assistant' else 'input_text', text=part['text']))
                elif part.get('type') == 'image_url' and role in ('user', 'developer'):
                    image = part.get('image_url')
                    require(isinstance(image, dict) and isinstance(image.get('url'), str),
                            'PARAMETER_UNSUPPORTED', '模型图片输入缺少有效地址')
                    parts.append(dict(type='input_image', image_url=image['url']))
                    if 'detail' in image:
                        parts[-1]['detail'] = image['detail']
                else:
                    raise Problem('PARAMETER_UNSUPPORTED', '业务草稿含不支持的输入内容块；未发送模型请求')
            content = parts
        else:
            require(isinstance(content, str), 'PARAMETER_UNSUPPORTED', '业务草稿消息必须包含文本')
        result.append(dict(role=role, content=content))
    return result


def structured_response_schema(messages):
    """Read only our first system task header, never a user's pasted schema."""
    if not messages or messages[0].get('role') != 'system':
        return None
    content = messages[0].get('content')
    if not isinstance(content, str) or '可信任务头：' not in content:
        return None
    from .core import strict_model_json
    try:
        header = strict_model_json(content.split('可信任务头：', 1)[1])
    except ValueError:
        raise Problem('SCHEMA_INVALID', '可信任务头无法读取；未发送模型请求', model_repair_allowed=False)
    if not isinstance(header, dict) or header.get('transport_protocol') != 'responses-json-schema-v1':
        return None
    schema = header.get('schema')
    require(isinstance(schema, dict), 'SCHEMA_INVALID', '缺少结构化输出 Schema；未发送请求')
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        raise Problem('SCHEMA_INVALID', '结构化输出 Schema 无效；未发送请求', model_repair_allowed=False)
    return schema


def responses_usage(usage):
    """Preserve documented numeric usage, with aliases for existing billing."""
    if not isinstance(usage, dict):
        return None, None
    valid = lambda value: isinstance(value, int) and not isinstance(value, bool) and value >= 0
    original = {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
                if valid(usage.get(key))}
    for key, field in (('input_tokens_details', 'cached_tokens'), ('output_tokens_details', 'reasoning_tokens')):
        details = usage.get(key)
        if isinstance(details, dict) and valid(details.get(field)):
            original[key] = {field: details[field]}
    mapped = {target: original[source] for source, target in (
        ('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('total_tokens', 'total_tokens'))
        if source in original}
    if 'output_tokens_details' in original:
        mapped['completion_tokens_details'] = copy.deepcopy(original['output_tokens_details'])
    return mapped, original


def responses_result(raw):
    """Read final text only. Reasoning bodies never enter metadata or evidence."""
    if not isinstance(raw, dict) or not isinstance(raw.get('output'), list):
        raise ValueError('invalid Responses envelope')
    status = raw.get('status')
    details = raw.get('incomplete_details')
    reason = details.get('reason') if isinstance(details, dict) else None
    rejection = None
    finish = 'stop' if status == 'completed' else None
    if status == 'incomplete':
        finish = 'length' if reason == 'max_output_tokens' else 'content_filter' if reason == 'content_filter' else None
        rejection = ('OUTPUT_TRUNCATED' if reason == 'max_output_tokens' else 'PROVIDER_ERROR',
                     '模型业务草稿未完整生成；本次结果未采纳')
    elif status != 'completed' or raw.get('error'):
        rejection = ('PROVIDER_ERROR', '模型未完成业务草稿请求；本次结果未采纳')
    text, count = [], 0
    for item in raw['output']:
        if not isinstance(item, dict):
            rejection = rejection or ('SCHEMA_INVALID', '模型返回了无法读取的输出内容块')
            continue
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role', 'assistant') != 'assistant':
            rejection = rejection or ('PARAMETER_UNSUPPORTED', '业务草稿只接受最终文本，不接受工具或其它非文本输出')
            continue
        count += 1
        if item.get('status', 'completed') != 'completed':
            rejection = rejection or ('OUTPUT_TRUNCATED', '模型最终消息尚未完成；本次结果未采纳')
        parts = item.get('content')
        if not isinstance(parts, list):
            rejection = rejection or ('SCHEMA_INVALID', '模型最终消息内容无法读取')
            continue
        for part in parts:
            if isinstance(part, dict) and part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                text.append(part['text'])
            elif isinstance(part, dict) and part.get('type') == 'refusal':
                finish = 'content_filter'
                rejection = rejection or ('PROVIDER_ERROR', '模型拒绝生成本次业务草稿；结果未采纳')
            else:
                rejection = rejection or ('PARAMETER_UNSUPPORTED', '业务草稿包含非文本输出；结果未采纳')
    if count > 1:
        rejection = rejection or ('SCHEMA_INVALID', '模型返回多个最终消息，不能合并为一份业务草稿')
    usage, original = responses_usage(raw.get('usage'))
    return ''.join(text), dict(usage=usage, provider_usage=original, finish_reason=finish,
                              response_status=status if status in ('in_progress', 'completed', 'incomplete', 'failed') else None,
                              response_model=raw.get('model')), rejection


class Provider:
    business_draft_protocol = 'business-draft-v1'

    def __init__(self, env_path=None, credential_dir=None):
        self.keys = {}
        self.environment = ProjectEnvironment(env_path)
        self.env_origins = {'RA_DEEPSEEK_API_KEY':'https://api.deepseek.com'}
        from .credentials import ProtectedKeyStore
        self.credential_store = ProtectedKeyStore(credential_dir) if credential_dir is not None else None
        self.key_storage_errors = {}

    def resolve_key(self, config):
        endpoint=origin(config)
        if endpoint in self.keys:
            value=self.keys[endpoint]
            return (value,'session') if usable_key(value) else ('','none')
        self.key_storage_errors.pop(endpoint, None)
        if self.credential_store:
            try:
                saved = self.credential_store.load(endpoint)
            except Problem as error:
                self.key_storage_errors[endpoint] = error.message
                return '', 'protected_store_unavailable'
            if saved is not None:
                return (saved, 'protected_store') if usable_key(saved) else ('', 'none')
        env=config.get('key_env','')
        return self.environment.credential(env) if self.env_origins.get(env)==endpoint else ('','none')

    def key(self, config):
        return self.resolve_key(config)[0]

    def key_status(self, config):
        value,source=self.resolve_key(config)
        return dict(key_configured=bool(value),key_env=config.get('key_env',''),key_source=source,bound_origin=origin(config),
                    persistent_key_supported=bool(self.credential_store and self.credential_store.supported),
                    key_storage_error=self.key_storage_errors.get(origin(config), ''))

    def set_key(self, config, key, persist=False):
        endpoint = origin(config)
        key = key.strip()
        require(not key or usable_key(key), 'CONFIG_INVALID', '请输入有效的 API Key')
        if persist:
            require(self.credential_store is not None, 'KEY_STORAGE_UNSUPPORTED', '当前运行方式不支持保存 Key')
            self.credential_store.save(endpoint, key)
            self.keys.pop(endpoint, None)
        elif key:
            self.keys[endpoint] = key
        else:
            self.keys.pop(endpoint, None)
        return self.key_status(config)

    async def request(self, config, messages, evidence=None):
        key = self.key(config)
        require(key, 'CONFIG_MISSING', '尚未配置此接收端的 API Key；未发送材料')
        endpoint = origin(config)
        business_schema = business_draft_schema(messages) if endpoint == 'https://api.deepseek.com' else None
        response_schema = (business_schema or structured_response_schema(messages)) if endpoint == 'https://api.deepseek.com' else None
        if response_schema is not None:
            # DeepSeek documents json_schema here; Chat Completions only has json_object.
            body = dict(model=config['model'], input=responses_input(messages),
                        max_output_tokens=config['max_tokens'], stream=False,
                        reasoning={'effort': 'none' if config.get('thinking_disabled') else 'high'},
                        text={'format': {'type': 'json_schema', 'name': 'business_draft_v1' if business_schema is not None else 'requirements_runtime_v1', 'schema': response_schema}})
            route = '/responses'
        else:
            body = dict(model=config['model'], messages=messages, max_tokens=config['max_tokens'], stream=False)
            if config['json_mode']:
                body['response_format'] = {'type': 'json_object'}
            if endpoint == 'https://api.deepseek.com':
                body['thinking'] = {'type': 'disabled' if config.get('thinking_disabled') else 'enabled'}
            route = '/chat/completions'
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=config['timeout'], follow_redirects=False, trust_env=False, proxy=config.get('proxy') or None) as client:
                if config.get('max_calls') is not None:
                    require(evidence is not None and getattr(evidence,'request_budget',None) is not None
                            and hasattr(evidence,'before_send'),
                            'BUDGET_AUTH_REQUIRED', '有限请求上限必须绑定已授权任务计数器；未发送请求', 409)
                if evidence and hasattr(evidence,'before_send'):
                    evidence.before_send(config)
                response = await client.post(config['base_url'].rstrip('/') + route, json=body, headers={'Authorization': 'Bearer ' + key})
        except httpx.TimeoutException:
            raise Problem('TIMEOUT', '模型请求超时；已发请求可能计费')
        except httpx.HTTPError:
            raise Problem('NETWORK_ERROR', '无法连接模型接收端')
        codes = {400:'PARAMETER_UNSUPPORTED', 401:'AUTH_FAILED', 403:'AUTH_FAILED', 402:'PROVIDER_QUOTA', 404:'MODEL_UNSUPPORTED', 429:'RATE_LIMITED'}
        if evidence and response.status_code != 200:
            evidence.response(http_status=response.status_code, elapsed_seconds=round(time.monotonic()-started, 3))
        if response.status_code == 400:
            # Classify only; do not echo provider bodies that may contain inputs.
            try:
                error = response.json().get('error', {})
                detail = (str(error.get('code', '')) + ' ' + str(error.get('message', ''))).lower() if isinstance(error, dict) else ''
            except (ValueError, AttributeError):
                detail = ''
            if any(marker in detail for marker in ('context_length_exceeded', 'maximum context length', 'exceeds the context', 'too many tokens')):
                raise Problem('MODEL_CONTEXT_LIMIT', '材料超过模型单次上下文容量；请按业务主题分段处理。已有内容保留，未截断关键底稿。')
        require(response.status_code == 200, codes.get(response.status_code, 'PROVIDER_ERROR'), '模型接口 HTTP ' + str(response.status_code))
        rejection = None
        try:
            raw = response.json()
            if response_schema is not None:
                content, meta, rejection = responses_result(raw)
                meta['response_protocol'] = 'responses'
            else:
                choice = raw['choices'][0]
                content = choice['message'].get('content')
                meta = dict(response_model=raw.get('model'), usage=raw.get('usage'), finish_reason=choice.get('finish_reason'))
            meta.update(request_model=config['model'], elapsed_seconds=round(time.monotonic()-started, 3), origin=endpoint)
        except (KeyError, ValueError, IndexError, TypeError):
            if evidence:
                evidence.response(http_status=response.status_code, elapsed_seconds=round(time.monotonic()-started, 3))
            raise Problem('SCHEMA_INVALID', '模型协议响应无法读取')
        if evidence:
            # Persist only final content, before JSON parsing and all validation.
            evidence.response(content, http_status=response.status_code, finish_reason=meta['finish_reason'],
                              usage=meta['usage'], response_model=meta['response_model'], elapsed_seconds=meta['elapsed_seconds'])
            if response_schema is not None:
                evidence.value.update({name: copy.deepcopy(meta[name]) for name in
                                       ('response_protocol', 'response_status', 'provider_usage')})
                evidence.save()
        if rejection:
            if evidence:
                evidence.value['transport_rejection'] = rejection[0]
                evidence.save()
            raise Problem(*rejection, model_repair_allowed=business_schema is None and rejection[0]=='OUTPUT_TRUNCATED')
        require(meta['finish_reason'] != 'length', 'OUTPUT_TRUNCATED', '模型单次输出截断；请分段生成或调整模型单次输出上限')
        require(isinstance(content, str) and content.strip(), 'OUTPUT_EMPTY', '模型返回空内容')
        require(key not in content, 'SECURITY_BLOCKED', '模型输出包含凭据信息，已拒绝保存')
        # Never persist hidden reasoning; only final response is processed.
        try:
            from .core import strict_model_json, DuplicateJsonMember
            value = strict_model_json(content)
        except json.JSONDecodeError as error:
            stage=evidence.value.get('stage') if evidence else None
            if (evidence and meta.get('finish_reason') == 'stop'
                    and (response_schema is None or meta.get('response_status') == 'completed')
                    and not evidence.limitations and evidence.value.get('final_output') == content):
                from .model_json import schema_closers
                spec = response_schema or (request_schema(stage) if stage in (*STAGES, 'interview') else None)
                from .model_json import question_root_close
                recovered = question_root_close(content) if business_schema is not None else None
                recovery_rule = 'question-missing-root-close-1'
                if recovered is None:
                    recovered = schema_closers(content, spec) if spec is not None else None
                    recovery_rule = 'schema-forced-container-closers-1'
                if recovered is not None:
                    value, normalization = recovered
                    meta['syntax_normalization'] = dict(rule=recovery_rule,
                        **normalization, raw_hash=digest(content), derived_hash=digest(value))
                    evidence.value['syntax_normalization'] = copy.deepcopy(meta['syntax_normalization'])
                    evidence.save()
                    return value, meta
            if (response_schema is not None and evidence and meta.get('finish_reason')=='stop'
                    and meta.get('response_status')=='completed' and not evidence.limitations
                    and evidence.value.get('final_output')==content):
                from .model_json import business_containers, question_root_close
                recovered=business_containers(content)
                rule='business-missing-container-closers-1'
                if recovered is None:
                    recovered=question_root_close(content)
                    rule='question-missing-root-close-1'
                if recovered is not None:
                    value,normalization=recovered
                    meta['syntax_normalization']=dict(rule=rule,
                        **normalization,raw_hash=digest(content),derived_hash=digest(value))
                    evidence.value['syntax_normalization']=copy.deepcopy(meta['syntax_normalization'])
                    evidence.save()
                    return value,meta
            normalizer={'review':normalize_review_result_tail,'ingest':normalize_ingest_result_tail}.get(stage)
            normalized=(normalizer(content) if response_schema is None and normalizer and evidence.value.get('final_output')==content else None)
            if normalized is None:
                raise Problem('SCHEMA_INVALID', f'模型最终内容不是完整 JSON：第 {error.lineno} 行，第 {error.colno} 列，字符偏移 {error.pos}；{error.msg}。'+delimiter_hint(content)+'请检查该位置的引号、逗号与括号配对，重新输出完整对象；不得删除业务内容。')
            value,offset=normalized
            rule=('review-premature-root-close-before-result-1' if stage=='review'
                  else 'ingest-premature-root-close-before-metadata-1')
            meta['syntax_normalization']=dict(rule=rule,
                removed_character='}',removed_offset=offset,raw_hash=digest(content),derived_hash=digest(value))
            evidence.value['syntax_normalization']=copy.deepcopy(meta['syntax_normalization'])
            evidence.save()
        except DuplicateJsonMember:
            from .model_json import identical_members
            # Review uses the same complete-evidence, typed equality proof as
            # ingest/vision. This only removes identical syntax repetition;
            # the normal review and prewrite validators still run afterwards.
            normalized = (identical_members(content) if evidence and meta.get('finish_reason') == 'stop' and
                (response_schema is None or meta.get('response_status') == 'completed') and
                evidence.value.get('stage') in ('ingest','vision','review','clarify','change','interview','prd')
                and evidence.value.get('final_output') == content and not evidence.limitations else None)
            if normalized is None:
                if evidence:
                    evidence.value['json_integrity_failure']='duplicate_member'
                    evidence.save()
                raise Problem('SCHEMA_INVALID', '模型 JSON 有重复键；原成员含义不唯一，不能作为格式修复依据')
            value, paths = normalized
            meta['syntax_normalization'] = dict(rule='identical-json-members-1', paths=paths,
                raw_hash=digest(content), derived_hash=digest(value))
            evidence.value['syntax_normalization'] = copy.deepcopy(meta['syntax_normalization'])
            evidence.save()
        except ValueError:
            raise Problem('SCHEMA_INVALID', '模型最终内容不是严格完整 JSON（不得有重复键或非 JSON 常量）')
        if response_schema is not None:
            # The business adapter validates the envelope and isolates bad rows;
            # transport must not discard usable rows because one row is invalid.
            require(isinstance(value, dict), 'SCHEMA_INVALID', '模型业务草稿必须是完整 JSON 对象')
        return value, meta
