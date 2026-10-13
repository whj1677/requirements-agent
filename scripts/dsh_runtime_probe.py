"""Initialize an isolated official DSH SDK runtime; never makes a model request."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.dsh_bridge import DshBridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dsh-home", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--document-python")
    parser.add_argument("--document-reader")
    args = vars(parser.parse_args())
    print(json.dumps(DshBridge(**args).probe(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
