#!/usr/bin/env python3
"""Stable command entry for one deterministic Case conversion."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    scenario_root = Path(__file__).resolve().parents[4]
    implementation = scenario_root / "global-video-understanding" / "skills" / "case-delivery" / "scripts" / "convert.py"
    if not implementation.is_file():
        raise SystemExit(f"conversion implementation not found: {implementation}")
    os.execv(sys.executable, [sys.executable, str(implementation), *sys.argv[1:]])


if __name__ == "__main__":
    main()
