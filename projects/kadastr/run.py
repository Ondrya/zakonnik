#!/usr/bin/env python
"""CLI вход. Запуск: python run.py 50:14:0000000:156789"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from kadastr.interfaces.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
