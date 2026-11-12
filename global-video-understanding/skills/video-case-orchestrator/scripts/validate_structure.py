#!/usr/bin/env python3
"""Validate discoverability, metadata and local references without running a Case."""
import argparse
import json
import re
from pathlib import Path
import yaml


def validate(skills):
    errors = []
    discovered = {path.parent.name: path for path in skills.glob("*/SKILL.md")}
    manifest = json.loads((skills.parent / "skill.json").read_text())["skills"]
    enabled = {name for name, item in manifest.items() if item.get("enabled")}
    if enabled != set(discovered):
        errors.append(f"manifest/discovery mismatch: {sorted(enabled ^ set(discovered))}")
    for name, path in discovered.items():
        text = path.read_text()
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        metadata = yaml.safe_load(match.group(1)) if match else {}
        if not isinstance(metadata, dict) or metadata.get("name") != name or not metadata.get("description"):
            errors.append(f"invalid Skill frontmatter: {name}")
            continue
        if manifest.get(name, {}).get("metadata", {}).get("description") != metadata["description"]:
            errors.append(f"manifest description differs: {name}")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            errors.append(f"invalid Skill name: {name}")
        for doc in path.parent.rglob("*.md"):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", doc.read_text()):
                if "://" in target or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if not (doc.parent / target).resolve().exists():
                    errors.append(f"missing reference: {doc.relative_to(skills)} -> {target}")
    return {"status": "failed" if errors else "passed", "skills": sorted(discovered), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = validate(args.skills_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["errors"]))

if __name__ == "__main__":
    raise SystemExit(main())
