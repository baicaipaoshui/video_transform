"""Deterministic, bounded frame selection for intersection path review."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json


REVISION = "intersection_dense_evidence.v1"
MAX_FRAMES = 12


def _nearest(frames, target):
    return min(frames, key=lambda row: (abs(row["time_s"] - target), row["time_s"], row["frame_id"]))


def build_catalog(world, behavior, frames, *, max_frames=MAX_FRAMES):
    """Prefer existing evidence; retain required tracks, PTS endpoints and interval anchors."""
    if isinstance(frames, dict):
        frames = frames.values()
    ordered = sorted((copy.deepcopy(row) for row in frames), key=lambda row: (row["time_s"], row["frame_id"]))
    if not ordered:
        raise ContractError("intersection review has no evidence frames")
    required = {point["frame_id"] for actor in behavior.get("actors", [])
                for point in actor.get("trajectory", []) if point.get("frame_id")}
    known = {row["frame_id"] for row in ordered}
    if not required <= known:
        raise ContractError("intersection actor trajectory refers to unavailable evidence")
    if len(required) > max_frames:
        raise ContractError("intersection required trajectory evidence exceeds 12-frame review budget")

    reasons = {frame_id: {"actor_trajectory"} for frame_id in required}
    chosen = set(required)
    intervals = []
    for index, item in enumerate(world.get("critical_intervals", [])):
        start, end = item.get("start_s"), item.get("end_s")
        if (type(start) in (int, float) and type(end) in (int, float)
                and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
            intervals.append((f"critical_interval_{index + 1:03d}", float(start), float(end)))
    if not intervals:
        intervals = [("observed_intersection_window", ordered[0]["time_s"], ordered[-1]["time_s"])]

    # Six coarse anchors are enough to reveal where a relation changes; remaining
    # slots are deterministic midpoint refinements, never an unconditional dense dump.
    targets = []
    for interval_id, start, end in intervals:
        for fraction in (0.0, .2, .4, .6, .8, 1.0):
            targets.append((interval_id, start + (end - start) * fraction, "interval_anchor"))
        for fraction in (.1, .3, .5, .7, .9):
            targets.append((interval_id, start + (end - start) * fraction, "midpoint_refinement"))
    targets.extend(("observed_intersection_window", row["time_s"], "source_endpoint")
                   for row in (ordered[0], ordered[-1]))
    for interval_id, target, reason in targets:
        if len(chosen) >= max_frames:
            break
        row = _nearest(ordered, target)
        chosen.add(row["frame_id"])
        reasons.setdefault(row["frame_id"], set()).add(reason)

    selected = [row for row in ordered if row["frame_id"] in chosen]
    frame_rows = []
    for row in selected:
        memberships = [interval_id for interval_id, start, end in intervals
                       if start - 1e-9 <= row["time_s"] <= end + 1e-9]
        frame_rows.append({
            "frame_id": row["frame_id"],
            "source_pts_s": row["time_s"],
            "path": row.get("path"),
            "sha256": row.get("sha256"),
            "selection_reasons": sorted(reasons.get(row["frame_id"], {"bounded_fill"})),
            "critical_interval_ids": memberships,
            "frame": row,
        })
    payload = {
        "revision": REVISION,
        "max_frames": max_frames,
        "source_frame_catalog_sha256": preview_digest(ordered),
        "selection": "required_tracks_then_interval_anchors_then_midpoints",
        "frames": frame_rows,
    }
    payload["catalog_sha256"] = preview_digest(payload)
    return payload


def freeze(case, world, behavior, frames):
    path = Path(case) / "state/dense_evidence_catalog.json"
    payload = build_catalog(world, behavior, frames)
    if path.is_file():
        frozen = read_json(path)
        if frozen != payload:
            raise ContractError("frozen intersection evidence catalog changed; create a new Case")
        return frozen
    atomic_json(path, payload)
    return payload


def review_frames(catalog):
    return [copy.deepcopy(row["frame"]) for row in catalog["frames"]]
