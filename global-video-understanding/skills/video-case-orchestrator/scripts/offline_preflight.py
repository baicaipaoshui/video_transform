#!/usr/bin/env python3
"""Compatibility CLI; canonical preflight belongs to case-delivery."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[2] /
                      "case-delivery/scripts/offline_preflight.py"), run_name="__main__")
