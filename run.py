#!/usr/bin/env python
"""Convenience launcher: python run.py <command>. Works without installing the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vi_precip.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
