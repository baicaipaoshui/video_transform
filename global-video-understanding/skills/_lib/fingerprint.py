"""Fingerprint maintained production code, contracts and rendered instructions."""
import hashlib
from .paths import SKILLS as HERE, SCENARIO_ROOT


def pipeline_fingerprint():
    files = {p for p in (HERE / "_lib").rglob("*.py")
             if "experimental" not in p.relative_to(HERE).parts}
    files.update(HERE.glob("*/scripts/*.py"))
    files.update(HERE.glob("schemas/*.json"))
    files.update(HERE.glob("references/*.json"))
    files.update(HERE.glob("*/references/*prompt.md"))
    files.update((SCENARIO_ROOT/'json-coverter/skills/case-conversion/references').glob('*prompt.md'))
    files.update({HERE/'behavior-understanding/references/intersection-review.md',
                  HERE/'_lib/experimental/constraint_solver.py',
                  HERE/'_lib/experimental/requirements-solver.txt'})
    digest = hashlib.sha256()
    for path in sorted(files):
        relative = path.relative_to(HERE) if path.is_relative_to(HERE) else path.relative_to(SCENARIO_ROOT)
        digest.update(str(relative).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
