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
POLICY_DEFAULTS = dict(budget_mode='function_first', review_max_tokens=65536,
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
    if stage == 'review' and automatic(config):
        config['max_tokens'] = min(MODEL_OUTPUT_LIMIT,
                                  max(config['max_tokens'], config['review_max_tokens']))
        config['retry_max_tokens'] = min(MODEL_OUTPUT_LIMIT,
                                        max(config['max_tokens'], config['review_retry_max_tokens']))
        config['timeout'] = max(config['timeout'], 300)
        config['action_seconds'] = max(config['action_seconds'], 900)
    return config


def output_ceiling(config):
    return max(config['max_tokens'], config.get('retry_max_tokens', config['max_tokens']))


def input_size(text, config):
    return len(text.encode('utf-8')) if automatic(config) else len(text)


def input_allowance(config):
    if automatic(config):
        # Reserve the approved retry output from the outset: a retry must not
        # silently lose sources or change the review scope to fit more output.
        return MODEL_CONTEXT_LIMIT - output_ceiling(config) - CONTEXT_MARGIN
    return config['context_chars'] - config['max_tokens'] * 4


def check_context(messages, config):
    size = input_size(dumps(messages), config)
    limit = input_allowance(config)
    require(size <= limit, 'BUDGET_EXHAUSTED',
            (f'完整输入的保守容量估算 {size} 超过本轮可用 {limit}；请按业务主题缩小范围，'
             '已有结果保留，未截断关键底稿。' if automatic(config) else
             f'输入及修复上下文超过固定额度；至少需要 {size + config["max_tokens"] * 4 + 4000} '
             '字符预算，或切换功能优先；未截断关键底稿。'))


def policy_summary(config):
    return dict(mode='function_first' if automatic(config) else 'fixed',
                initial_output_tokens=config['max_tokens'],
                maximum_output_tokens=output_ceiling(config),
                truncation_escalations=1 if output_ceiling(config) > config['max_tokens'] else 0,
                action_seconds=config['action_seconds'],
                input_measure='utf8_bytes_conservative_estimate' if automatic(config) else 'characters',
                input_allowance=input_allowance(config))
