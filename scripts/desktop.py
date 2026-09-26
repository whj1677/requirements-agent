"""Frozen Windows entry. No application/database import before device licensing."""
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.desktop_runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
