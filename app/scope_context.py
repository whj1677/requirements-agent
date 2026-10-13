"""Human selected applicability of product scope fields."""
from .core import require

INTAKE_FIELDS = ('product', 'module', 'intent')
SCOPE_FIELDS = ('current_state', 'users', 'value', 'change_scope',
                'preserve_scope', 'out_of_scope', 'priority')
FIELDS = INTAKE_FIELDS + SCOPE_FIELDS
SCENARIOS = ('unspecified', 'new_product', 'new_module', 'modify_existing')
DEPENDENCIES = ('unknown', 'independent', 'shared')
STATES = ('known', 'not_applicable', 'none', 'unknown')
MAX_NOTE = 2000


def validate_context_edit(values, profile=None, field_states=None):
    require(set(values) <= set(FIELDS), 'CONTEXT_INVALID', '未知核对字段')
    require(all(isinstance(v, str) and len(v) <= 6000 for v in values.values()),
            'CONTEXT_INVALID', '核对说明过长')
    if profile is not None:
        require(set(profile) == {'scenario', 'dependencies'} and
                profile['scenario'] in SCENARIOS and profile['dependencies'] in DEPENDENCIES,
                'CONTEXT_INVALID', '项目场景或依赖关系无效')
    if field_states is not None:
        require(set(field_states) <= set(FIELDS), 'CONTEXT_INVALID', '未知字段状态')
        for state in field_states.values():
            require(set(state) <= {'status', 'note'} and state.get('status') in STATES and
                    isinstance(state.get('note', ''), str) and len(state.get('note', '')) <= MAX_NOTE,
                    'CONTEXT_INVALID', '字段状态或说明无效')


def save_context_edit(p, values, scope_ids=None, profile=None, field_states=None):
    """Call after request validation and scope-id validation, inside store.edit."""
    if scope_ids is not None:
        p.setdefault('product_context', {})['scope_ids'] = list(dict.fromkeys(scope_ids))
    if values:
        p.setdefault('product_context', {}).update(values)
    if profile is not None:
        p['scope_profile'] = dict(profile)
    if field_states is not None:
        p.setdefault('context_field_states', {}).update(field_states)


def independent_new_scope(p):
    profile = p.get('scope_profile') or {}
    return profile.get('scenario') == 'new_product' or (
        profile.get('scenario') == 'new_module' and profile.get('dependencies') == 'independent')


def scope_field_rules(p):
    independent = independent_new_scope(p)
    result = {}
    for field in FIELDS:
        required = field in ('intent', 'users', 'change_scope') or (
            field in ('current_state', 'preserve_scope') and not independent)
        allowed = ['known', 'unknown']
        if field in ('value', 'priority', 'out_of_scope'):
            allowed.extend(('not_applicable', 'none'))
        if field in ('current_state', 'preserve_scope') and independent:
            allowed.append('not_applicable')
        if field == 'preserve_scope':
            allowed.append('none')
        result[field] = {'required': required, 'allowed_statuses': allowed}
    return result


def scope_field_issues(p, labels):
    context = p.get('product_context') or {}
    states = p.get('context_field_states') or {}
    rules = scope_field_rules(p)
    issues = []
    for field, label in labels.items():
        value = str(context.get(field, '')).strip()
        state = (states.get(field) or {}).get('status')
        rule = rules[field]
        if state and state not in rule['allowed_statuses']:
            issues.append('请核对' + label + '的适用状态')
        elif state == 'known' and not value:
            issues.append('请核对' + label)
        elif rule['required'] and (state == 'unknown' or
                                   (state != 'none' and state != 'known' and not value)):
            issues.append('请核对' + label)
        elif rule['required'] and state is None and not value:
            issues.append('请核对' + label)
    return issues
