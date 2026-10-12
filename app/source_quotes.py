"""Narrow, source-verifiable quote normalization; never a semantic approval."""
import copy

from .core import digest


def newline_match(quote, text):
    """Find one contiguous source span differing only in CR/LF representation."""
    def indexed(value):
        normalized, spans = [], []
        index = 0
        while index < len(value):
            end = index + (2 if value[index:index + 2] == '\r\n' else 1)
            normalized.append('\n' if value[index] == '\r' else value[index])
            spans.append((index, end))
            index = end
        return ''.join(normalized), spans

    needle, _ = indexed(quote)
    haystack, spans = indexed(text)
    if not needle.strip() or '\n' not in needle:
        return None
    start = haystack.find(needle)
    if start < 0 or haystack.find(needle, start + 1) >= 0:
        return None
    begin, end = spans[start][0], spans[start + len(needle) - 1][1]
    return text[begin:end], begin, end


def normalize_scope_quotes(value, excerpts):
    """Restore unique source line endings or the existing terminal semicolon case.

    Only requirement scope quotes in the current, exact source/excerpt pair are
    eligible. No fuzzy matching or changes to business text, source identities,
    internal punctuation, numbers or permissions. Raw provider evidence is kept
    by the caller; the returned copy and proof describe the derived response.
    """
    result = copy.deepcopy(value)
    changes = []
    if not isinstance(result, dict) or result.get('stage') not in ('ingest', 'clarify', 'change'):
        return result, changes
    if not isinstance(result.get('proposals'), list):
        return result, changes
    actual = {}
    for excerpt in excerpts:
        pair = excerpt.get('source_id'), excerpt.get('id')
        if all(isinstance(part, str) for part in pair) and isinstance(excerpt.get('text'), str):
            actual.setdefault(pair, []).append(excerpt['text'])
    for index, item in enumerate(result['proposals']):
        if not isinstance(item, dict) or item.get('kind') != 'requirement':
            continue
        support = item.get('scope_evidence')
        if not isinstance(support, list):
            continue
        for position, evidence in enumerate(support):
            if not isinstance(evidence, dict) or not isinstance(evidence.get('source_ref'), dict):
                continue
            ref, quote = evidence['source_ref'], evidence.get('quote')
            pair = ref.get('source_id'), ref.get('excerpt_id')
            if not all(isinstance(part, str) for part in pair):
                continue
            texts = actual.get(pair, [])
            if len(texts) != 1 or not isinstance(quote, str):
                continue
            text = texts[0]
            if quote in text:
                continue
            matched = newline_match(quote, text)
            if matched:
                exact, start, end = matched
                rule = 'scope-quote-line-endings-1'
            else:
                if not quote.endswith('。') or not quote[:-1].strip():
                    continue
                exact = quote[:-1] + '；'
                start = text.find(exact)
                if start < 0 or text.find(exact, start + 1) >= 0:
                    continue
                end = start + len(exact)
                rule = 'scope-quote-terminal-semicolon-1'
            evidence['quote'] = exact
            changes.append(dict(
                rule=rule,
                path=['proposals', index, 'scope_evidence', position, 'quote'],
                temp_id=item.get('temp_id'), source_ref=copy.deepcopy(ref),
                original_hash=digest(quote), normalized_hash=digest(exact),
                excerpt_hash=digest(text), match_start=start, match_end=end,
            ))
    return result, changes
