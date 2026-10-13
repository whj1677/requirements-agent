"""Host-only read_document command. Model tools expose file_path only."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'evidence/dsh-pilot-20261010'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--file-path', required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace == PILOT.resolve() or not workspace.is_relative_to(PILOT.resolve()):
        parser.error('Workspace must be an explicitly prepared folder under evidence/dsh-pilot-20261010')
    os.environ['RA_DATA_DIR'] = str(PILOT / 'document-reader-runtime')
    sys.path.insert(0, str(ROOT))
    from app.dsh_document_reader import read_document
    from app.core import Problem
    try:
        result = read_document(workspace, args.file_path, temp_root=PILOT / 'office-temp')
    except Problem as error:
        print(json.dumps(dict(status='error', code=error.code, message=error.message), ensure_ascii=False))
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not silently fall back to cached Markdown or expose local exception
        # strings containing filesystem details or document contents.
        print(json.dumps(dict(status='error', code='DOCUMENT_READ_FAILED',
                              message='读取器未取得有效结果', error_type=type(error).__name__), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
