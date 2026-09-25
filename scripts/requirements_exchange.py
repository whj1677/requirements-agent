"""Offline exchange validator/minimal consumer. Does not write application data."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core import Problem
from app.requirements import validate_exchange, merge_exchange

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--merge-with',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        value=validate_exchange(json.loads(args.input.read_text('utf-8')))
        if args.merge_with:value=merge_exchange(json.loads(args.merge_with.read_text('utf-8')),value)
        if args.output:
            # Explicit output only; refuse accidental replacement of a user's exchange file.
            with args.output.open('x',encoding='utf-8') as out:json.dump(value,out,ensure_ascii=False,indent=2)
        print(json.dumps(dict(status='exchange_valid',requirements=len(value['requirements']),relations=len(value['relations']),test_execution='not_run'),ensure_ascii=False))
    except (Problem,ValueError,OSError) as error:
        print(getattr(error,'code','EXCHANGE_READ_FAILED')+': '+str(error),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
