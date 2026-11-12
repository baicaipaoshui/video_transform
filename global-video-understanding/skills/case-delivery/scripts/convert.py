#!/usr/bin/env python3
"""Stable Skill command; implementation lives in the internal domain package."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.delivery.converter import main

if __name__ == "__main__":
    raise SystemExit(main())
