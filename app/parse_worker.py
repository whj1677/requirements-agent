"""One-source parser subprocess. No shells, macros, external links, or model calls."""
import json
import sys
from .sources import parse_bytes,MAX_BYTES
if __name__=='__main__':
    payload=sys.stdin.buffer.read(MAX_BYTES+1)
    try:
        result=parse_bytes('input'+sys.argv[1],payload)
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf8'))
    except Exception:
        sys.exit(2)
