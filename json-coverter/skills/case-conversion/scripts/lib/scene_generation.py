"""Converter-owned scene generation replay; no lifecycle or model calls."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


RECEIPT_VERSION = "staged-fallbacks.v1"


def validate_fallback_list(records, stage):
    if not isinstance(records, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("field"), str) or not row["field"]
            or not isinstance(row.get("reason"), str) or not row["reason"]
            or "value" not in row for row in records):
        raise ValueError(stage + " fallback records must be an array of field/value/reason objects")
    return records


def read_scene_fallbacks(path):
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("scene fallback records missing or invalid: " + str(error)) from error
    return validate_fallback_list(records, "scene")


def fallback_receipt(execution, scene, excluded):
    validate_fallback_list(execution, "execution")
    validate_fallback_list(scene, "scene")
    return dict(fallback_receipt_version=RECEIPT_VERSION,
                execution_fallbacks=execution, scene_fallbacks=scene,
                fallbacks=execution + scene, excluded_actor_ids=excluded)


def replay_scene_conversion(projected, scene_type, workflow_contract="video-case-world-behavior"):
    """Run only the existing scene script, avoiding wrapper/validator recursion."""
    if scene_type not in {"highway", "intersection"}:
        raise ValueError("unsupported scene replay type: " + str(scene_type))
    script = Path(__file__).resolve().parents[1] / "scenes" / scene_type / "convert.py"
    with tempfile.TemporaryDirectory(prefix="scene-replay-") as folder:
        folder = Path(folder)
        source, target, receipt = (folder / name for name in
                                   ("analysis.json", "target.json", "scene_fallbacks.json"))
        source.write_text(json.dumps(projected, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False), encoding="utf-8")
        env = dict(os.environ, SCENE_ANALYSIS_INPUT=str(source),
                   SCENE_TARGET_OUTPUT=str(target), SCENE_FALLBACKS_OUTPUT=str(receipt),
                   SCENE_WORKFLOW_CONTRACT=workflow_contract)
        try:
            completed = subprocess.run([sys.executable, str(script)], env=env,
                                       capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("scene replay failed: " + str(error)) from error
        if completed.returncode:
            raise ValueError("scene replay failed: " + (completed.stderr or completed.stdout)[-2000:])
        records = read_scene_fallbacks(receipt)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("scene replay target missing or invalid: " + str(error)) from error
        if not isinstance(data, dict):
            raise ValueError("scene replay target must be an object")
        return data, records
