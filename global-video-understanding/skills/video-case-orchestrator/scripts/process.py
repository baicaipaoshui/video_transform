#!/usr/bin/env python3
"""Stable command entry for the video Case orchestrator Skill."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def compatible_python() -> str:
    if sys.version_info >= (3, 10):
        return sys.executable
    qwenpaw_home = Path(os.environ.get("QWENPAW_HOME", str(Path.home()/".qwenpaw")))
    candidate = qwenpaw_home/"venv/bin/python"
    if candidate.is_file():
        return str(candidate)
    raise SystemExit("video pipeline requires Python 3.10+; no compatible QwenPaw runtime was found")


def main() -> None:
    runtime = compatible_python()
    if runtime != sys.executable:
        os.execv(runtime, [runtime, str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from _lib.runtime.orchestrator import main as run
    run()


if __name__ == "__main__":
    main()
