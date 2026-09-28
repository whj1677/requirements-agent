"""Explicit request policy; actual token usage always comes from the provider.

For documented DeepSeek models, UTF-8 input bytes are a conservative capacity
estimate, not measured tokens. Fixed mode retains the legacy character budget.
"""
import copy
from urllib.parse import urlsplit

from .core import dumps, require

MODEL_OUTPUT_LIMIT = 393216
MODEL_CONTEXT_LIMIT = 1048576
CONTEXT_MARGIN = 8192
# Shared with assembly: capacity reserve, NOT measured image tokens.
# Assembly separately bounds a request to 3 images, each at most 1600px/side.
IMAGE_INPUT_RESERVE = 12000
POLICY_DEFAULTS = dict(budget_mode='unlimited', review_max_tokens=65536,
                       review_retry_max_tokens=131072)


def automatic(config):
    url = urlsplit(config['base_url'])
    return (config.get('budget_mode', 'function_first') == 'function_first'
            and url.scheme == 'https' and url.netloc == 'api.deepseek.com'
            and config['model'] in ('deepseek-flash', 'deepseek-v4-pro')
            and not config.get('vision_request', False))


def stage_config(settings, stage):
    config = copy.deepcopy(settings)
    config.setdefault('budget_mode', POLICY_DEFAULTS['budget_mode'])
    for key in ('review_max_tokens', 'review_retry_max_tokens'):
        config.setdefault(key, POLICY_DEFAULTS[key])
    if stage == 'vision':
        config['vision_request'] = True
    unlimited = config['budget_mode'] == 'unlimited'
    if unlimited:
        config['max_calls'] = None
        url = urlsplit(config['base_url'])
        if url.scheme == 'https' and url.netloc == 'api.deepseek.com' and config['model'] in ('deepseek-flash', 'deepseek-v4-pro'):
            config['retry_max_tokens'] = MODEL_OUTPUT_LIMIT
    if stage == 'review' and (automatic(config) or unlimited and config.get('retry_max_tokens')):
        config['max_tokens'] = min(MODEL_OUTPUT_LIMIT,
                                  max(config['max_tokens'], config['review_max_tokens']))
        config['retry_max_tokens'] = min(MODEL_OUTPUT_LIMIT,
                                        max(config['max_tokens'], config['review_retry_max_tokens'], config.get('retry_max_tokens', 0)))
        config['timeout'] = max(config['timeout'], 300)
        config['action_seconds'] = max(config['action_seconds'], 900)
    return config


def output_ceiling(config):
    return max(config['max_tokens'], config.get('retry_max_tokens', config['max_tokens']))


def input_size(text, config):
    return len(text.encode('utf-8')) if automatic(config) else len(text)


def input_allowance(config):
    if config.get('budget_mode') == 'unlimited':
        # A character/UTF-8 estimate is not the provider's tokenizer. Do not
        # reject or silently omit sources using an artificial character quota.
        return None
    if automatic(config):
        # Reserve the approved retry output from the outset: a retry must not
        # silently lose sources or change the review scope to fit more output.
        return MODEL_CONTEXT_LIMIT - output_ceiling(config) - CONTEXT_MARGIN
    return config['context_chars'] - config['max_tokens'] * 4


def context_usage(messages, config):
    """Measure prose plus image reserve without counting base64 as prose.

    Only structured image_url parts are images. Data URLs in ordinary text or
    repair responses still count in full. Never modify the actual request.
    """
    measured = copy.deepcopy(messages)
    images = transport = 0
    for message in measured:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get('type') != 'image_url':
                continue
            image = part.get('image_url')
            if not isinstance(image, dict) or not isinstance(image.get('url'), str):
                continue
            images += 1
            url = image['url']
            if url.startswith('data:image/') and ';base64,' in url:
                header, payload = url.split(';base64,', 1)
                transport += len(payload.encode('utf-8'))
                image['url'] = header + ';base64,[image payload]'
    text_size = input_size(dumps(measured), config)
    return dict(text_size=text_size, image_count=images,
                image_reserve=images * IMAGE_INPUT_RESERVE,
                image_transport_bytes=transport,
                estimated_input=text_size + images * IMAGE_INPUT_RESERVE)


def check_context(messages, config):
    limit = input_allowance(config)
    if limit is None:
        return
    size = context_usage(messages, config)['estimated_input']
    require(size <= limit, 'BUDGET_EXHAUSTED',
            (f'完整输入的保守容量估算 {size} 超过本轮可用 {limit}；请按业务主题缩小范围，'
             '已有结果保留，未截断关键底稿。' if automatic(config) else
             f'输入及修复上下文超过固定额度；至少需要 {size + config["max_tokens"] * 4 + 4000} '
             '字符预算；请减少本次资料范围或调整对应模型的输入额度。未截断关键底稿。'))


def policy_summary(config):
    return dict(mode='unlimited' if config.get('budget_mode') == 'unlimited' else 'function_first' if automatic(config) else 'fixed',
                initial_output_tokens=config['max_tokens'],
                maximum_output_tokens=output_ceiling(config),
                truncation_escalations=1 if output_ceiling(config) > config['max_tokens'] else 0,
                action_seconds=None if config.get('budget_mode') == 'unlimited' else config['action_seconds'],
                input_measure='provider_enforced' if config.get('budget_mode') == 'unlimited' else 'utf8_bytes_conservative_estimate' if automatic(config) else 'characters',
                input_allowance=input_allowance(config))
