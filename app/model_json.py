"""Narrow, auditable handling of identical members in complete model JSON."""
import json


class _Object(list):
    pass


class _Number(str):
    pass


def identical_members(content):
    """Return a derived value and duplicate paths, or None. Never choose conflicts.

    Compare a typed syntax tree before decoding numbers, preserving number tokens,
    object member order and array order. In particular true, 1, and 1.0 differ.
    This does not repair malformed JSON or remove unknown/schema-invalid fields.
    """
    paths = []

    def reject(token):
        raise ValueError(token)

    def inspect(node, path):
        if isinstance(node, _Object):
            seen = {}
            for key, item in node:
                current = inspect(item, path + [key])
                if key in seen:
                    if current != seen[key]:
                        raise ValueError('conflicting duplicate')
                    paths.append(path + [key])
                else:
                    seen[key] = current
            return ('object', tuple(seen.items()))
        if isinstance(node, list):
            return ('array', tuple(inspect(item, path + [i]) for i, item in enumerate(node)))
        return (type(node).__name__, node)

    try:
        tree = json.loads(content, object_pairs_hook=_Object, parse_int=_Number,
                          parse_float=_Number, parse_constant=reject)
        inspect(tree, [])
        if not paths:
            return None
        # All members have now been compared; no conflicting last-value wins.
        value = json.loads(content, parse_constant=reject)
        json.dumps(value, allow_nan=False)
        return value, paths
    except (ValueError, TypeError, RecursionError):
        return None
