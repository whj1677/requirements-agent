"""Read-only validator for the portable business-context.json contract."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import jsonschema


def validate(bundle_path, roots):
    schema_path = Path(__file__).resolve().parents[1] / 'references' / 'business-context.schema.json'
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    raw = bundle_path.read_bytes()
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError('JSON must be nonempty and <= 2 MiB')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result

    bundle = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite number')))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(bundle)

    def ids(rows, name):
        result = [row['id'] for row in rows]
        if len(result) != len(set(result)):
            raise ValueError(f'duplicate {name} ID')
        return set(result)

    repositories = ids(bundle['source_snapshot']['repositories'], 'repository')
    modules = ids(bundle['modules'], 'module')
    claims = ids(bundle['claims'], 'claim')
    evidence_ids = ids(bundle['evidence'], 'evidence')
    ids(bundle['unknowns'], 'unknown')
    ids(bundle['conflicts'], 'conflict')
    modules_by_id = {row['id']: row for row in bundle['modules']}
    claims_by_id = {row['id']: row for row in bundle['claims']}
    for module in bundle['modules']:
        if not set(module['claim_ids']) <= claims or not set(module['depends_on']) <= modules - {module['id']}:
            raise ValueError('invalid module references')
        if any(module['id'] not in claims_by_id[cid]['module_ids'] for cid in module['claim_ids']):
            raise ValueError('module/claim references disagree')
    for claim in bundle['claims']:
        if not claim['module_ids'] or not claim['evidence_ids'] or not set(claim['module_ids']) <= modules or not set(claim['evidence_ids']) <= evidence_ids:
            raise ValueError('invalid claim references')
        if any(claim['id'] not in modules_by_id[mid]['claim_ids'] for mid in claim['module_ids']):
            raise ValueError('claim/module references disagree')
    for entry in bundle['evidence']:
        path = entry['path']
        if ('\\' in path or ':' in path or '\x00' in path or path.startswith('/') or
                any(part in ('', '.', '..') for part in path.split('/'))):
            raise ValueError(f'unsafe evidence path: {path}')
        if entry['repository_id'] not in repositories or entry['line_end'] < entry['line_start']:
            raise ValueError('invalid evidence identity or lines')
        if entry['repository_id'] in roots:
            root = roots[entry['repository_id']].resolve(strict=True)
            file = (root / path).resolve(strict=True)
            if not file.is_relative_to(root) or not file.is_file():
                raise ValueError(f'evidence escapes repository: {path}')
            raw_file = file.read_bytes()
            if hashlib.sha256(raw_file).hexdigest() != entry['sha256']:
                raise ValueError(f'evidence SHA-256 differs: {path}')
            if entry['line_end'] > len(raw_file.splitlines()):
                raise ValueError(f'evidence line out of range: {path}')
    for row in bundle['unknowns']:
        if not set(row['module_ids']) <= modules:
            raise ValueError('invalid unknown module')
    for row in bundle['conflicts']:
        if len(row['claim_ids']) < 2 or not set(row['claim_ids']) <= claims:
            raise ValueError('invalid conflict claims')
    for name in ('inspected_modules', 'indexed_modules'):
        if not set(bundle['coverage'][name]) <= modules:
            raise ValueError('invalid coverage module')
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--repository', action='append', default=[], metavar='ID=ROOT')
    args = parser.parse_args()
    roots = {}
    for item in args.repository:
        if '=' not in item:
            parser.error('--repository requires ID=ROOT')
        key, value = item.split('=', 1)
        if key in roots:
            parser.error(f'duplicate repository root: {key}')
        roots[key] = Path(value)
    try:
        bundle = validate(args.bundle, roots)
    except (ValueError, OSError, json.JSONDecodeError, jsonschema.ValidationError) as error:
        print(f'INVALID: {error}', file=sys.stderr)
        return 1
    print(f"VALID: {bundle['bundle_id']} ({len(bundle['modules'])} modules, {len(bundle['claims'])} claims; semantic review not performed)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
