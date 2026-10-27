"""Narrow, auditable handling of identical members in complete model JSON."""
import json


def schema_closers(content, schema):
    """Insert only forced container closers in a completed response.

    No quote/comma/value is invented, no literal is removed or reordered.
    A mismatched close must have an existing matching opener. Every required
    field must already exist and the complete derived value must pass Schema.
    The caller proves HTTP completion, stop and intact evidence, not truncation.
    """
    if not isinstance(content, str) or not content.lstrip().startswith('{'):
        return None
    from .core import strict_model_json
    import jsonschema
    decoder = json.JSONDecoder()
    stack, edits, position = [], [], 0
    try:
        while position < len(content):
            char = content[position]
            if char.isspace() or char in ',:':
                position += 1
                continue
            if char in '{[':
                stack.append(char)
                position += 1
                continue
            if char in '}]':
                opener = '{' if char == '}' else '['
                if opener not in stack:
                    return None
                closing = ''
                while stack[-1] != opener:
                    closing += '}' if stack.pop() == '{' else ']'
                if closing:
                    # A trailing comma/colon still means missing data, not a closer.
                    if content[:position].rstrip().endswith((',', ':', '{', '[')):
                        return None
                    edits.append((position, closing))
                stack.pop()
                position += 1
                continue
            literal, position = decoder.raw_decode(content, position)
            if isinstance(literal, (dict, list)):
                return None
        if stack:
            if content.rstrip().endswith((',', ':', '{', '[')):
                return None
            edits.append((len(content), ''.join('}' if x == '{' else ']' for x in reversed(stack))))
        if not edits or sum(len(s) for _, s in edits) > 4:
            return None
        derived = content
        for offset, closing in reversed(edits):
            derived = derived[:offset] + closing + derived[offset:]
        value = strict_model_json(derived)
        json.dumps(value, allow_nan=False)
        jsonschema.Draft202012Validator(schema).validate(value)
        return value, dict(insertions=[dict(offset=n, inserted=s) for n, s in edits])
    except (ValueError, TypeError, IndexError, RecursionError, jsonschema.ValidationError):
        return None


def question_root_close(content):
    """Append one missing root close to an otherwise complete single answer.

    Only the five-field question packet, empty summary/requirements and exactly
    Q1 qualify. The transport must prove completed, untruncated evidence first;
    business validation still checks the answer and every source. No text,
    comma, inner closer, value or duplicate member is repaired here.
    """
    if not isinstance(content,str) or not content.strip().startswith('{') or not content.rstrip().endswith('}'):
        return None
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:raise ValueError('duplicate member')
            obj[key]=value
        return obj
    def reject(token):raise ValueError(token)
    decoder=json.JSONDecoder(object_pairs_hook=unique,parse_constant=reject)
    try:
        decoder.decode(content)
        return None
    except json.JSONDecodeError:
        pass
    except (ValueError,TypeError,RecursionError):
        return None
    try:
        value=decoder.decode(content+'}')
        if (not isinstance(value,dict) or set(value)!={'summary','summary_fields','requirements','limitations','completion'}
                or value['summary_fields']!={} or value['requirements']!=[]
                or not isinstance(value['completion'],dict) or set(value['completion'])!={'answers'}
                or not isinstance(value['completion']['answers'],dict) or set(value['completion']['answers'])!={'Q1'}):
            return None
        # Reject non-finite numbers even when expressed as a large exponent.
        json.dumps(value,allow_nan=False)
        return value,dict(insertions=[dict(offset=len(content),inserted='}')],identical_duplicate_paths=[])
    except (ValueError,TypeError,RecursionError):
        return None


def business_containers(content):
    """Recover only two schema-unambiguous missing closers in business packets.

    Insert an object close before its owning array closes, or close the sole
    requirement row/array before a root-only member. Never alter a literal,
    remove text, add a value, or finish truncated output. Repeated members may
    collapse only after the existing typed-equality proof finds them identical.
    The caller must still run every business/schema/reference validator.
    """
    if not isinstance(content,str) or not content.strip().startswith('{') or not content.strip().endswith('}'):
        return None
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:raise ValueError('duplicate member')
            obj[key]=value
        return obj
    def reject(token):raise ValueError(token)
    decoder=json.JSONDecoder(object_pairs_hook=unique,parse_constant=reject)
    try:
        decoder.decode(content)
        return None
    except json.JSONDecodeError:
        pass
    except (ValueError,TypeError,RecursionError):
        return None
    stack=[];edits=[];pos=0
    try:
        while pos<len(content):
            char=content[pos]
            if char.isspace() or char in ',:':pos+=1;continue
            if char=='"':
                value,end=decoder.raw_decode(content,pos)
                after=end
                while after<len(content) and content[after].isspace():after+=1
                if after<len(content) and content[after]==':' and value in ('summary','summary_fields','requirements','limitations','completion'):
                    if stack!=['{']:
                        # These names occur only at the packet root, never in a
                        # declared requirement row. No arbitrary nesting repair.
                        if stack!=['{','[','{'] or value not in ('summary_fields','limitations','completion'):return None
                        comma=pos-1
                        while comma>=0 and content[comma].isspace():comma-=1
                        if comma<0 or content[comma]!=',':return None
                        edits.append((comma,'}]'))
                        stack=['{']
                pos=end;continue
            if char in '[{':stack.append(char);pos+=1;continue
            if char in ']}':
                if char==']' and len(stack)>=2 and stack[-2:]==['[','{']:
                    edits.append((pos,'}'));stack.pop()
                if not stack or (stack.pop(),char) not in (('[',']'),('{','}')):return None
                pos+=1;continue
            value,end=decoder.raw_decode(content,pos)
            if isinstance(value,(dict,list,str)):return None
            pos=end
        if stack or not edits or len(edits)>2 or sum(len(s) for _,s in edits)>3:return None
        derived=content
        for offset,text in reversed(edits):derived=derived[:offset]+text+derived[offset:]
        duplicates=[]
        try:
            value=decoder.decode(derived)
        except ValueError:
            repeated=identical_members(derived)
            if repeated is None:return None
            value,duplicates=repeated
        if not isinstance(value,dict) or not set(('summary','summary_fields','requirements','limitations'))<=set(value) or set(value)-set(('summary','summary_fields','requirements','limitations','completion')):return None
        if not isinstance(value['requirements'],list) or not value['requirements']:return None
        return value,dict(insertions=[dict(offset=offset,inserted=text) for offset,text in edits],
                          identical_duplicate_paths=duplicates)
    except (ValueError,TypeError,RecursionError):
        return None


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
