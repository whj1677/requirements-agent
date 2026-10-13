"""Install a reviewed shared background package without altering projects."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import DATA, Problem
from app.platform_knowledge import install


def main():
    parser = argparse.ArgumentParser(description='发布储能云平台共用领域知识，保留旧版本')
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    args = parser.parse_args()
    try:
        status = install(args.data_dir.resolve(), args.bundle.read_bytes())
    except (OSError, Problem) as error:
        parser.exit(1, str(error) + '\n')
    print('已发布：' + status['bundle_id'] + ' SHA-256=' + status['sha256'])


if __name__ == '__main__':
    main()
