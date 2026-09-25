"""One-source parser subprocess. No shells, macros, external links, or model calls."""
import json
import sys
from .sources import parse_bytes,MAX_BYTES
from .core import Problem
if __name__=='__main__':
    payload=sys.stdin.buffer.read(MAX_BYTES+1)
    try:
        result=parse_bytes('input'+sys.argv[1],payload)
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf8'))
    except Exception as error:
        failure = dict(code=error.code if isinstance(error,Problem) else 'SOURCE_FAILED',
                       message=error.message if isinstance(error,Problem) else '当前读取方式无法解析；原件已保留，可重新读取')
        sys.stdout.buffer.write(json.dumps(failure,ensure_ascii=False).encode('utf8'))
        sys.exit(2)
