#!/usr/bin/env python3
"""List immutable video Case IDs in human creation-time order."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def case_health(folder: Path, progress: dict) -> tuple[str, str]:
    """Classify persisted output claims without changing the Case."""
    status = progress.get("status", "unknown")
    phase = progress.get("phase", "unknown")
    artifacts = progress.get("artifacts") or {}
    required = {"analysis.json", "report.md", "report.docx", "target.json"}
    completed = status in {"done", "done_degraded"}

    if completed:
        if phase != "done":
            return "problem", "completed status with non-done phase"
        if not isinstance(artifacts, dict) or set(artifacts) != required:
            return "problem", "completed Case has incomplete artifact claims"
        for name, record in artifacts.items():
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                return "problem", f"invalid artifact record: {name}"
            path = Path(record["path"])
            if not path.is_file():
                return "problem", f"missing published artifact: {name}"
            if sha256(path) != record.get("sha256"):
                return "problem", f"published artifact hash mismatch: {name}"
        return "healthy", "published artifacts verified"

    if artifacts:
        return "problem", "non-completed Case still claims published artifacts"
    if status == "placeholder_generated":
        return ("healthy" if phase == "done" else "problem",
                "diagnostic placeholder; no formal target" if phase == "done" else "placeholder has non-done phase")
    if status in {"failed", "needs_review"}:
        if (folder / "final/target.json").is_file():
            return "problem", "terminal non-success Case has an unclaimed local target"
        return "terminal", "retry or review required"
    if status in {"planning", "batching", "aggregating", "converting"}:
        active = next((item for item in reversed(progress.get("sessions", []))
                       if item.get("status") in {"submitted", "pending", "running"}), None)
        if active:
            stamp = active.get("last_polled_at") or active.get("last_poll_error_at") or progress.get("updated_at")
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                age = (datetime.now().astimezone() - moment.astimezone()).total_seconds()
                if age > 35 * 60:
                    return "problem", f"active task lease has no persisted heartbeat for {int(age // 60)} minutes"
            except (AttributeError, TypeError, ValueError):
                return "problem", "active task lease has no valid heartbeat timestamp"
        return "active", "pipeline not terminal"
    return "problem", "unknown persisted status"


def delivery_class(folder: Path, progress: dict, health: str) -> str:
    """Count only verified formal artifacts as source-bound delivery."""
    if health == 'healthy' and progress.get('status') == 'done':
        return 'formal_success'
    if health == 'healthy' and progress.get('status') == 'done_degraded':
        return 'source_bound_degraded'
    if health not in {'healthy', 'terminal'} or progress.get('artifacts'):
        return 'not_delivered'
    synthetic = (progress.get('diagnostics') or {}).get('synthetic_target') or {}
    if not isinstance(synthetic, dict):
        return 'not_delivered'
    target = folder/'synthetic_delivery/target.json'
    manifest = folder/'synthetic_delivery/manifest.json'
    if (synthetic.get('delivery_level') == 'synthetic_unverified'
            and synthetic.get('target_path') == str(target)
            and synthetic.get('manifest') == str(manifest)
            and target.is_file() and manifest.is_file()
            and synthetic.get('target_sha256') == sha256(target)
            and read_json(manifest).get('target_sha256') == synthetic['target_sha256']):
        return 'synthetic_only'
    return 'not_delivered'


def rows(cases_root: Path) -> list[dict]:
    result = []
    for folder in cases_root.glob("case_*"):
        if not folder.is_dir():
            continue
        case = read_json(folder / "case.json")
        progress = read_json(folder / "state/progress.json")
        binding = read_json(folder / "state/binding.json")
        created = case.get("created_at")
        if not isinstance(created, str):
            created = datetime.fromtimestamp(folder.stat().st_mtime).astimezone().isoformat()
        source = binding.get("source_path")
        health, health_detail = case_health(folder, progress)
        result.append({
            "created_at": created,
            "case_id": case.get("case_id", folder.name),
            "status": progress.get("status", "unknown"),
            "phase": progress.get("phase", "unknown"),
            "source": Path(source).name if isinstance(source, str) else "unknown",
            "health": health,
            "health_detail": health_detail,
            "delivery_class": delivery_class(folder, progress, health),
        })
    return sorted(result, key=lambda item: (item["created_at"], item["case_id"]), reverse=True)


def local_stamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description="List video Cases newest first")
    parser.add_argument("--limit", type=int, default=30, help="maximum rows; 0 means all")
    parser.add_argument("--status", action="append", help="filter by status; repeatable")
    parser.add_argument("--oldest-first", action="store_true")
    parser.add_argument("--problems-only", action="store_true", help="show only inconsistent persisted Cases")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0")

    scenario_root = Path(__file__).resolve().parents[4]
    values = rows(scenario_root / "video_extractor/cases")
    if args.status:
        values = [item for item in values if item["status"] in set(args.status)]
    if args.problems_only:
        values = [item for item in values if item["health"] == "problem"]
    if args.oldest_first:
        values.reverse()
    if args.limit:
        values = values[:args.limit]
    if args.json:
        print(json.dumps(values, ensure_ascii=False, indent=2))
        return

    headings = ("CREATED (LOCAL)", "STATUS", "PHASE", "HEALTH", "DELIVERY", "CASE_ID", "SOURCE")
    table = [(local_stamp(item["created_at"]), item["status"], item["phase"], item["health"],
              item["delivery_class"], item["case_id"], item["source"]) for item in values]
    widths = [len(value) for value in headings]
    for row in table:
        widths = [max(width, len(str(value))) for width, value in zip(widths, row)]
    print("  ".join(value.ljust(width) for value, width in zip(headings, widths)))
    for row in table:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)))


if __name__ == "__main__":
    main()
