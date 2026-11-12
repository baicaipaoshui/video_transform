#!/usr/bin/env python3
"""Deterministic Case primitives. Model calls remain the Agent's responsibility."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat
from _lib.paths import SKILLS, SCENARIO_ROOT

BUDGET = 12 * 1024 * 1024
OVERHEAD = 1024 * 1024
GLOBAL_TIERS = ((12, 512, 75), (12, 448, 70), (8, 384, 65))
DETAIL_TIERS = ((4, 1176, 85), (4, 960, 75), (2, 768, 65))
ACTOR_DETAIL_TIERS = ((4, 2560, 85), (4, 1920, 85), *DETAIL_TIERS)
VIEWER_IMAGE_LIMIT = 2 * 1024 * 1024
PHASES = ("planning", "batching", "aggregating", "converting", "done")
RETRYABLE = {"timeout", "gateway_429", "gateway_5xx", "empty_response", "invalid_json", "model_stream_interrupted", "schema_validation_failed", "missing_output", "incomplete_parts", "part_too_large", "task_status_unavailable"}
WORKFLOW_CONTRACT = "video-case-v2-short-aware-packed"
V3_WORKFLOW_CONTRACT = "video-case-world-behavior"
SHORT_VIDEO_MAX_S = 5.0
SHORT_VIDEO_ONE_BATCH_MAX_S = 2.5


def global_request_text(case_dir):
    case_dir=Path(case_dir).resolve(); schemas=SKILLS/"schemas"
    fast=""; interval_hint=""
    frames_path=case_dir/"state/global_frames.json"; probe_path=case_dir/"state/probe.json"
    visual_instruction=f"查看 {frames_path} 列出的预算后 JPEG"
    if frames_path.is_file() and probe_path.is_file():
        try:
            frame_record=read_json(frames_path); frames=frame_record.get("frames",[]); duration=read_json(probe_path).get("probe",{}).get("duration_s",999)
            sheets=frame_record.get("contact_sheet_paths") or ([frame_record["contact_sheet_path"]] if frame_record.get("contact_sheet_path") else [])
            if sheets:
                anchors=frame_record.get("anchor_frame_paths",[])
                visual_instruction=f"按顺序各调用一次看图工具查看这 {len(sheets)} 张联系表：{sheets}；每张表按从左到右、从上到下排列，标签给出 frame_id 和 time_s。再各调用一次看图工具查看这 {len(anchors)} 张原尺寸锚点帧以核对细车道线和远车：{anchors}；不要打开其他单帧"
            if duration<=SHORT_VIDEO_ONE_BATCH_MAX_S:
                interval_hint=" 本视频不超过2.5秒，critical_intervals 最多给1个覆盖全部关键问题的时间窗。"
            elif duration<=SHORT_VIDEO_MAX_S:
                interval_hint=" 本视频不超过5秒，critical_intervals 最多给2个时间窗；相同时间范围内的多个问题合并表达，不为不同文字 focus 重复分窗。"
            if len(frames)<=12 and duration<=SHORT_VIDEO_MAX_S:
                fast=(" 本 Case 符合短视频确定性编译路径（时长≤5秒且全局帧≤12）。只写 global_context candidate，不写 analysis；编排器会从视觉事实确定性生成 analysis。scene_facts 必须给出 travel_lane_count、ego_lane_from_right（最右行驶车道为1）、左右是否有 parking strip、stop_line_x_m、ego_speed_mps、crosswalk_count、traffic_light_count；若画面显示 Ego 越过停止线，再给 ego_stop_line_crossing_s。ego_speed_mps 与 stop_line_x_m 是单目估计，写成 {value,status:'estimated',confidence}；越线时间用帧计数，写成 {value,status:'observed',time_s或frame_id}。actor_hypotheses 中每个非自车 Actor 必须包含 actor_id、actor_type、first_seen_frame_id、identity_description、is_static；每辆运动车还必须给 lane_from_right、initial_longitudinal_m、speed_mps，若视频内进入路口再给 observed intersection_entry_s；三者必须时空一致。停放车只需 parking_side=left|right。车辆统一 actor_type=vehicle，具体车型写 identity_description。行人和 vehicle_group 默认 simulation_role=background，只进入分析/报告；只有确属事件执行参与者时才写 simulation_role=participant，参与行人还必须给 initial_position_ego_m、final_position_ego_m（均含 numeric x/y、status、confidence）和 estimated speed_mps。无法确认参与者轨迹时保持 background，不得补猜。从近到远逐辆核对，不能漏掉同一车道的远车。")
        except (OSError,ValueError,TypeError): pass
    return f"你已经由确定性编排器置于本阶段唯一的全新 session；直接在当前 session 执行 Case {case_dir.name} 的全局视频理解，禁止创建 subagent、禁止调用其他 Agent、禁止另开 session。只读取 {case_dir/'state/global_frames.json'}、{case_dir/'case.json'} 和只读 schema {schemas/'global_context.schema.json'}；{visual_instruction}，实际看图后直接写 JSON 到 {case_dir/'state/global_context.candidate.json'}。固定工具顺序：读完输入并看完联系表后，不输出推理、草稿或字段清单，立即调用且只调用一次 write_file 写 global_context candidate，然后停止。禁止读取 converter、analysis schema、history、sessions、runs、旧 workspaces、实现代码或其他文件。全局理解只做一次且必须覆盖四类核心语义：先用可见纵向分隔线划分行驶车道并明确 ego 在最左/中间/最右车道；路缘停车区只有在存在纵向分隔线且有运动交通时才算行驶车道；再从近到远枚举全部运动车辆，同车道近车和远车分别建 actor，静止路边车辆单独标记；透视导致车辆在画面中靠近 Ego 方向不代表变道，只有车辆中心在连续帧中实际跨过虚线才能记录 lane_change；然后按时间顺序记录各 actor 越过停止线、进入路口、变道、碰撞等关键时刻；最后逐项检查 road、actor、event 是否互相一致。不可确认事实放 questions_to_verify，不得交给后级重新猜测。规划有明确 focus 的时间窗口：短视频严格遵守上述1/2个上限，长视频最多4个；长视频窗口允许轻微重叠，短视频由确定性代码压缩重复覆盖；不机械四等分。{interval_hint}{fast} 只写要求的 candidate，不输出分析长文、不等待人工复核、不处理下一阶段。源文件和 manifest 哈希由确定性编排器校验，不要自行计算。"


def batch_request_text(case_dir, batch_id):
    case_dir=Path(case_dir).resolve(); candidate=case_dir/"batches"/f".{batch_id}.candidate.json"; schema_path=SKILLS/"schemas/batch.schema.json"
    target=case_dir/"batches"/f"{batch_id}.json"
    prompt = f"你已经由确定性编排器置于本批唯一的全新 session；禁止创建 subagent、禁止调用其他 Agent、禁止另开 session。处理一个视频详细批次：case_id={case_dir.name}, batch_id={batch_id}。只读取 {case_dir/'manifest.json'}、{case_dir/'state/global_context.json'}、上一已完成 batch JSON（若存在）和只读 schema {schema_path}；只查看 manifest 为本批列出的最多4张预算后 JPEG，不读取其他图片。禁止读取 converter、history、sessions、runs、workspaces、实现代码或其他批次帧。本批只验证 global_context 的问题和不确定项，不从零重建另一套场景。按时间顺序观察，发现与全局车道、Actor 或事件语义冲突时必须写 challenge，不得静默覆盖。registry_after 必须完整保留历史 actor，actor_id/actor_type/first_seen_frame_id/identity_description 逐字不变；新增事实写可变字段或 challenge。observations 只用 status=observed|estimated|unknown，禁止 kind。不要写 session_id；哈希由确定性编排器校验，不要自行计算。实际看图后只调用一次 write_file，把 JSON 写到唯一允许的绝对路径 {candidate}；不得创建 .candidates 目录，不得写正式检查点 {target}，正式检查点只由编排器校验并原子提交。写完该 candidate 后立即停止，不输出分析长文、不等待人工复核、不处理下一批、聚合或报告。"
    from _lib.legacy.v2.batch_contract import prompt_contract
    return prompt + prompt_contract(case_dir)


def retryable(code):
    return code in RETRYABLE or code in {'world_compilation_repairable', 'behavior_scope_review_incomplete', 'behavior_bounded_repairable'}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def raw_read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def read_json(path):
    path = Path(path)
    if (path.parent.name == 'state'
            and path.name in {'world_context.json', 'actor_registry.json', 'reconciliation.json'}
            and (path.parent/'fact_versions/active.json').is_file()):
        from _lib.runtime.fact_versions import current_facts
        view = current_facts(path.parent.parent)
        return {'world_context.json': view.world, 'actor_registry.json': view.registry,
                'reconciliation.json': view.reconciliation}[path.name]
    return raw_read_json(path)


def atomic_json(path, data):
    path = Path(path)
    if (path.parent.name == 'state'
            and path.name in {'world_context.json', 'actor_registry.json',
                              'reconciliation.json', 'census_adjudication.accepted.json'}
            and (path.parent/'fact_versions/active.json').is_file()):
        raise ValueError('Versioned Case source facts are immutable; publish a complete fact version')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def atomic_bytes(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def probe(video):
    command = ["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration", "-select_streams", "v:0", "-of", "json", str(video)]
    raw = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    return {"duration_s": float(raw["format"]["duration"]), "width": int(raw["streams"][0]["width"]), "height": int(raw["streams"][0]["height"]), "probe_fps": 2.0}


def probe_times(duration, fps=2.0):
    values = [min(duration, n / fps) for n in range(int(math.floor(duration * fps)) + 1)]
    if not values or not math.isclose(values[-1], duration): values.append(duration)
    return sorted(set(round(value, 6) for value in values))


def uniform(times, count=8):
    if len(times) <= count: return list(times)
    return [times[index] for index in sorted(set(round(n * (len(times) - 1) / (count - 1)) for n in range(count)))]


def frame_diff(a, b):
    with Image.open(a) as left, Image.open(b) as right:
        diff = ImageChops.difference(left.convert("L").resize((160, 90)), right.convert("L").resize((160, 90)))
        return float(ImageStat.Stat(diff).mean[0])


def change_frames(frames, count=4, gap=0.5):
    ranked = [(frame_diff(old["path"], new["path"]), new["time_s"], new) for old, new in zip(frames, frames[1:])]
    picked = []
    for _, _, frame in sorted(ranked, key=lambda row: (-row[0], row[1])):
        if all(abs(frame["time_s"] - item["time_s"]) >= gap for item in picked):
            picked.append(frame)
            if len(picked) == count: break
    return sorted(picked, key=lambda item: item["time_s"])


def intervals(items, duration, merge_overlaps=True, max_items=4):
    clean = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("start_s"), (int, float)) or not isinstance(item.get("end_s"), (int, float)):
            raise ValueError("schema_validation_failed: interval")
        start, end = max(0.0, float(item["start_s"])), min(duration, float(item["end_s"]))
        if end > start: clean.append({"start_s": start, "end_s": end})
    clean.sort(key=lambda item: (item["start_s"], item["end_s"]))
    if not merge_overlaps:
        selected = clean if max_items is None else clean[:max_items]
        return selected or [{"start_s": duration*n/4, "end_s": duration*(n+1)/4} for n in range(4)]
    merged = []
    for item in clean:
        if merged and item["start_s"] <= merged[-1]["end_s"]: merged[-1]["end_s"] = max(merged[-1]["end_s"], item["end_s"])
        else: merged.append(item)
    selected = merged if max_items is None else merged[:max_items]
    return selected or [{"start_s": duration*n/4, "end_s": duration*(n+1)/4} for n in range(4)]


def has_effective_intervals(items, duration):
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get("start_s"),(int,float)) or not isinstance(item.get("end_s"),(int,float)):
            raise ValueError("schema_validation_failed: interval")
        if min(duration,float(item["end_s"]))>max(0.0,float(item["start_s"])): return True
    return False


def plan_batch_intervals(items, duration, max_batches=4):
    """Bound Batch sessions while covering every World issue window."""
    if not isinstance(max_batches, int) or not 1 <= max_batches <= 4:
        raise ValueError('Batch count must be between one and four')
    effective=has_effective_intervals(items,duration)
    if duration>SHORT_VIDEO_MAX_S:
        planned=intervals(items,duration,merge_overlaps=False,max_items=None)
        while len(planned)>max_batches:
            # Merge the closest neighboring focus windows. Merging only the
            # execution windows preserves every exact World issue interval.
            index=min(range(len(planned)-1),key=lambda i:(
                max(0.0,planned[i+1]['start_s']-planned[i]['end_s']),
                max(planned[i]['end_s'],planned[i+1]['end_s'])-planned[i]['start_s'],i))
            planned[index:index+2]=[{'start_s':planned[index]['start_s'],
                                     'end_s':max(planned[index]['end_s'],planned[index+1]['end_s'])}]
        return planned
    if effective:
        valid=intervals(items,duration,merge_overlaps=False,max_items=None)
        start=min(item["start_s"] for item in valid); end=max(item["end_s"] for item in valid)
    else:
        start,end=0.0,float(duration)
    cap=min(max_batches,1 if duration<=SHORT_VIDEO_ONE_BATCH_MAX_S else 2)
    count=min(cap,max(1,math.ceil((end-start)/SHORT_VIDEO_ONE_BATCH_MAX_S)))
    width=(end-start)/count
    return [{"start_s":start+index*width,"end_s":end if index==count-1 else start+(index+1)*width} for index in range(count)]


def batch_times(interval, available, low=False):
    start, end = interval["start_s"], interval["end_s"]
    targets = (start, end) if low else (start, start+(end-start)/3, start+2*(end-start)/3, end)
    chosen = [min(available, key=lambda value: (abs(value-target), value)) for target in targets]
    return list(dict.fromkeys(chosen))


def _jpeg(path, width, quality):
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width > width: image.thumbnail((width, 100000), Image.Resampling.LANCZOS)
        output = BytesIO(); image.save(output, "JPEG", quality=quality, optimize=True)
        return output.getvalue()


def _limited(paths, limit):
    paths=list(paths)
    if len(paths)<=limit: return paths
    if limit==2: return [paths[0],paths[-1]]
    indices=sorted(set(round(n*(len(paths)-1)/(limit-1)) for n in range(limit)))
    return [paths[index] for index in indices]


def _pack(paths, tiers, text="", schema=None, budget=BUDGET, protected_paths=(), max_image_bytes=None):
    """Return metadata and JPEG bytes for the selected tier; do not cache tiers."""
    paths = list(paths)
    protected = {str(path) for path in protected_paths}
    for number, (limit, width, quality) in enumerate(tiers, 1):
        chosen=_limited(paths,limit)
        if not protected <= {str(path) for path in chosen}:
            continue
        payloads = [_jpeg(path, width, quality) for path in chosen]
        if max_image_bytes is not None and any(len(value) > max_image_bytes for value in payloads):
            del payloads
            continue
        size = sum(4 * math.ceil(len(value)/3) for value in payloads) + len(text.encode()) + len(json.dumps(schema or {}).encode()) + OVERHEAD
        if size <= budget: return {"tier": number, "width": width, "quality": quality, "paths": [str(p) for p in chosen], "estimated_bytes": size}, payloads
        del payloads
    raise RuntimeError("packing_budget_exceeded")


def pack(paths, tiers, text="", schema=None, budget=BUDGET, protected_paths=(), max_image_bytes=None):
    """Keep the public metadata-only result while sharing tier selection."""
    return _pack(paths, tiers, text=text, schema=schema, budget=budget, protected_paths=protected_paths,
                 max_image_bytes=max_image_bytes)[0]


def materialize_pack(paths, tiers, output_dir, prefix, text="", schema=None, budget=BUDGET, protected_paths=(), max_image_bytes=None):
    """Choose a tier and atomically persist the exact JPEGs the Agent must read."""
    packed, payloads = _pack(paths, tiers, text=text, schema=schema, budget=budget, protected_paths=protected_paths,
                             max_image_bytes=max_image_bytes)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for source, payload in zip(map(Path, packed["paths"]), payloads):
        target = output_dir/f"{prefix}__{source.stem}.jpg"
        atomic_bytes(target, payload)
        frames.append({"frame_id":source.stem,"path":str(target),"sha256":sha256(target)})
    return {**{key:value for key,value in packed.items() if key != "paths"}, "frames":frames}


def validate_registry(previous, current, references=()):
    from _lib.contracts.fidelity import validator
    validator('actor_registry.schema.json').validate(current)
    immutable = ("actor_id", "actor_type", "first_seen_frame_id", "identity_description")
    old, new = {a["actor_id"]: a for a in previous}, {}
    for actor in current:
        actor_id = actor.get("actor_id")
        if not actor_id or actor_id in new: raise ValueError("duplicate actor ID")
        new[actor_id] = actor
        if actor_id in old and any(actor.get(key) != old[actor_id].get(key) for key in immutable): raise ValueError("actor identity changed")
    if not old.keys() <= new.keys(): raise ValueError("historical actor omitted")
    if set(references) - new.keys(): raise ValueError("dangling actor reference")


class EvidenceValidationError(ValueError):
    """Evidence error with a stable JSON path for targeted shard repair."""
    def __init__(self, path, message):
        self.path=tuple(path); self.absolute_path=self.path
        location=".".join(map(str,self.path)) or "<root>"
        super().__init__(f"{location}: {message}")


def validate_evidence(node, path=(), inherited_frame_id=None, inherited_time_s=None):
    if isinstance(node, dict):
        frame_id=node.get("frame_id") or inherited_frame_id
        time_s=node.get("time_s") if node.get("time_s") is not None else inherited_time_s
        status = node.get("status")
        nested_observed = any(
            isinstance(item,dict) and item.get("status")=="observed"
            and (item.get("frame_id") or item.get("time_s") is not None)
            for item in (node.get("evidence",[]) if isinstance(node.get("evidence"),list) else [])
        )
        # direction_evidence (behavior.schema.json) binds observations through a
        # frame_ids array; execution_scope.observed_opposing consumes that shape.
        frame_ids=node.get("frame_ids")
        frame_ids_bound=(isinstance(frame_ids,list) and bool(frame_ids)
                         and all(isinstance(item,str) and item.strip() for item in frame_ids))
        if status == "observed" and not (frame_id or time_s is not None or nested_observed or frame_ids_bound):
            raise EvidenceValidationError(path,"observed evidence missing")
        confidence = node.get("confidence")
        if status == "estimated" and (type(confidence) not in (int, float) or not math.isfinite(confidence)):
            raise EvidenceValidationError(path,"estimated confidence missing")
        if status == "estimated" and not 0 <= confidence <= 1: raise EvidenceValidationError(path,"confidence outside 0..1")
        if status in ("unknown", None) and "status" in node and not node.get("reason"): raise EvidenceValidationError(path,"unknown reason missing")
        for key,value in node.items(): validate_evidence(value,path+(key,),frame_id,time_s)
    elif isinstance(node, list):
        for index,value in enumerate(node): validate_evidence(value,path+(index,),inherited_frame_id,inherited_time_s)


def validate_evidence_items(items, path=()):
    """Strict boundary validator; every evidence item must declare its epistemic status."""
    if not isinstance(items, list): raise ValueError("evidence must be an array")
    for index,item in enumerate(items):
        if not isinstance(item, dict) or item.get("status") not in {"observed","estimated","unknown"}:
            raise ValueError("evidence status missing")
        if "kind" in item: raise ValueError("kind cannot replace status")
        validate_evidence(item,path+(index,))


def reconcile_batches(case_dir, validator):
    case_dir = Path(case_dir); progress_path = case_dir/"state/progress.json"; progress = read_json(progress_path)
    complete = set(progress.get("completed_batches", []))
    for path in sorted((case_dir/"batches").glob("batch_*.json")):
        try: validator(read_json(path)); complete.add(path.stem)
        except Exception as error: progress.setdefault("checkpoint_errors", []).append({"batch_id":path.stem,"message":str(error),"at":now()})
    progress["completed_batches"] = sorted(complete); progress["updated_at"] = now(); atomic_json(progress_path, progress)
    return progress["completed_batches"]


def publish(case_dir, output_dir):
    case_dir, output_dir = Path(case_dir), Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    case_id = case_dir.name; names = ("analysis.json","report.md","report.docx","target.json"); artifacts = {}
    for name in names:
        source = case_dir/"final"/name
        if not source.is_file(): raise RuntimeError("publish_failed")
        suffix = name if name.startswith("report.") else name
        target = output_dir/f"{case_id}_{suffix}"
        data = source.read_bytes(); fd, temporary = tempfile.mkstemp(prefix="."+target.name,dir=output_dir)
        with os.fdopen(fd,"wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,target)
        directory=os.open(output_dir,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        artifacts[name]={"path":str(target),"sha256":sha256(target)}
    return artifacts


def transition(progress, phase, code=None):
    current = progress["phase"]
    if phase != "failed" and (phase not in PHASES or PHASES.index(phase) not in (PHASES.index(current), PHASES.index(current)+1)): raise ValueError("invalid state transition")
    result = dict(progress); result.update({"phase": phase, "status": phase, "error": {"code": code} if code else None, "updated_at": now()})
    return result


class Cases:
    def __init__(self, root): self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
    def records(self):
        for path in self.root.glob("case_*/case.json"):
            try: yield path.parent, read_json(path)
            except (OSError, ValueError): continue
    @staticmethod
    def _reuse_rank(folder):
        """Prefer valid completion, active lease, checkpoint, then explicit retry."""
        try: progress=read_json(folder/"state/progress.json")
        except (OSError,ValueError): return (-1,"")
        artifacts=progress.get("artifacts",{})
        done=progress.get("status") in {"done","done_degraded"} and bool(artifacts) and all(
            Path(item.get("path","")).is_file() and sha256(item["path"])==item.get("sha256")
            for item in artifacts.values())
        lease=any(item.get("status") in {"submitted","pending","running"} and item.get("task_id") for item in progress.get("sessions",[]))
        checkpoint=any((folder/name).is_file() for name in ("state/world_context.json","state/global_context.json","state/behavior.json","final/analysis.json","state/conversion_result.json"))
        retry=progress.get("status")=="failed" and progress.get("retry_requested") is True
        return (4 if done else 3 if lease else 2 if checkpoint else 1 if retry else 0,progress.get("updated_at",""))
    def bind(self, source, parameters=None, force_new=False):
        from _lib.contracts.v3 import normalize_parameters
        from _lib.runtime.execution_policy import require_active_policy
        from _lib.runtime.execution_scope import EGO_POLICY as scope_policy, SUPPORTED_POLICIES
        require_active_policy(parameters)
        source, digest = Path(source).resolve(strict=True), sha256(source); parameters=normalize_parameters(parameters)
        contract=V3_WORKFLOW_CONTRACT if parameters.get("workflow_version")=="v3" else WORKFLOW_CONTRACT
        matches=[]
        for folder, case in self.records():
            if not force_new and case.get("workflow_contract") == contract and case["source"]["sha256"] == digest and case.get("parameters",{})==parameters: matches.append((folder,case))
        if matches:
            if contract == V3_WORKFLOW_CONTRACT:
                matches = [(f,c) for f,c in matches if (f/'state/execution_policy.json').exists()
                           and read_json(f/'state/execution_policy.json').get('execution_scope') in SUPPORTED_POLICIES
                           and read_json(f/'state/execution_policy.json').get('execution_planning') == 'ego_scene_approximation.v1']
        if matches:
            folder,_case=max(matches,key=lambda item:self._reuse_rank(item[0])); binding=read_json(folder/"state/binding.json")
            if binding["source_path"] != str(source): binding.update({"source_path": str(source), "updated_at": now()}); atomic_json(folder/"state/binding.json", binding)
            return folder, False
        for folder, case in self.records():
            binding = read_json(folder/"state/binding.json")
            if binding["source_path"] == str(source) and case["source"]["sha256"] != digest:
                progress = read_json(folder/"state/progress.json"); atomic_json(folder/"state/progress.json", transition(progress, "failed", "source_changed"))
        from _lib.fingerprint import pipeline_fingerprint
        creation_fingerprint = pipeline_fingerprint()
        stamp = now()
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        # Lexicographically sortable UTC timestamp + collision-resistant suffix.
        # Keep old case_<16hex> directories readable through the schemas.
        time_id = created.strftime("%Y%m%dT%H%M%S") + f"{created.microsecond // 1000:03d}Z"
        suffix = hashlib.sha256((digest + stamp).encode()).hexdigest()[:8]
        case_id = f"case_{time_id}_{suffix}"; folder = self.root/case_id
        for name in ("frames", "batches", "state", "final"): (folder/name).mkdir(parents=True)
        atomic_json(folder/"case.json", {"schema_version":"1.0","workflow_contract":contract,"case_id":case_id,"created_at":stamp,"source":{"sha256":digest,"size_bytes":source.stat().st_size},"parameters":parameters})
        atomic_json(folder/"state/binding.json", {"schema_version":"1.0","source_path":str(source),"source_sha256":digest,"updated_at":stamp})
        atomic_json(folder/"state/progress.json", {"schema_version":"1.0","case_id":case_id,"phase":"planning","status":"planning","attempts":{},"sessions":[],"completed_batches":[],"error":None,"artifacts":{},"pipeline_fingerprint_at_creation":creation_fingerprint,"updated_at":stamp})
        if contract == V3_WORKFLOW_CONTRACT:
            from _lib.behavior.intersection import POLICY as intersection_policy
            from _lib.world.gaps import REVISION as fact_revision
            from _lib.contracts.conversion_policy import digest as policy_digest
            from _lib.contracts.semantic_motion import REVISION as semantic_gap_revision
            from _lib.world.actor_facts import STRICT_REVISION as actor_fact_revision
            from _lib.contracts.actor_identity_revision import REVISION as identity_revision
            atomic_json(folder/'state/execution_policy.json', {
                        'automatic_routing': True,
                        'fact_contract_revision': fact_revision,
                        'execution_scope': scope_policy,
                        'execution_planning': 'ego_scene_approximation.v1',
                        'intersection_path_contract': 'intersection_path.v1',
                        'world_admission_revision': 'world-admission.v1',
                        'actor_fact_review': actor_fact_revision,
                        'actor_identity_review': identity_revision,
                        'actor_identity_split': 'actor-identity-split.v1',
                        'vehicle_census': 'vehicle-census.v1',
                        'census_admission': 'vehicle-census-admission.v3',
                        'batch_evidence_coverage': 'question-bound-coverage.v2',
                        'batch_evidence_capacity': 'batch-evidence-capacity.v1',
                        'batch_behavior_owner_routing': 'batch-behavior-owner-routing.v1',
                        'world_census_adjudication': 'world-census-adjudication.v6',
                        'census_lane_position_evidence': 'census-lane-position-evidence.v1',
                        'census_evidence_revision': 'census-evidence.v3',
                        'behavior_census_scope': 'behavior-census-scope.v4',
                        'census_execution': 'census-execution.v2',
                        'behavior_handoff': 'behavior-handoff.v1',
                        'behavior_census_read_view': 'behavior-census-read-view.v2',
                        'unused_association': 'unused-association.v1',
                        'execution_assumptions': 'execution-assumptions.v1',
                        'peripheral_uncertainty': 'peripheral-uncertainty.v1',
                        'retained_unobserved_motion': 'retained-unobserved-motion.v1',
                        'bounded_execution': 'bounded-execution.v1',
                        'early_association_independence': 'early-association-independence.v2',
                        'execution_gap_recovery': 'execution-gap-recovery.v1',
                        'semantic_compilation': 'visual-answers.v2',
                        'lane_layout_closure': 'lane-layout-decision.v7',
                        'conversion_policy_revision': 'conversion-policy.v1',
                        'conversion_semantic_gap': semantic_gap_revision,
                        'conversion_continuation': 'conversion-continuation.v1',
                        'analysis_reconciliation': 'analysis-reconciliation.v1',
                        'synthetic_target': 'synthetic-target.v1',
                        'conversion_parameters_sha256': policy_digest(parameters),
                        'behavior_input_binding': 'behavior-input.v1',
                        'behavior_evidence_timeline': 'actor-time-evidence.v1',
                        'ego_lane_observations': 'ego-lane-observations.v1',
                        'ego_lane_point_handoff': 'ego-lane-point-handoff.v1',
                        'reconciliation_alignment': 'reconciliation-alignment.v1',
                        'fact_versions': 'case-facts.v1',
                        'planned_preview_review': True, 'recovery_limit': 2,
                        'bounded_recovery': 'bounded-recovery.v1',
                        'recovery_limits': {'transport_retries': 2, 'content_repairs': 2, 'total_extra_tasks': 8},
                        'intersection_approximation': intersection_policy})
        else:
            from _lib.legacy.v2.batch_contract import POLICY, new_policy
            atomic_json(folder/POLICY, new_policy())
            from _lib.legacy.v2.minimum_fallback import POLICY as minimum_policy, REVISION as minimum_revision
            atomic_json(folder/minimum_policy, dict(revision=minimum_revision, max_attempts=1))
        return folder, True


def contact_sheet(frames, target, columns=2):
    """Pack temporal context into one labelled visual tool call."""
    opened=[]
    try:
        for frame in frames:
            with Image.open(frame["path"]) as source:
                opened.append((frame,source.convert("RGB")))
        cell_width=max(image.width for _,image in opened); cell_height=max(image.height for _,image in opened)+24
        rows=math.ceil(len(opened)/columns)
        sheet=Image.new("RGB",(cell_width*columns,cell_height*rows),(18,18,18)); draw=ImageDraw.Draw(sheet)
        for index,(frame,image) in enumerate(opened):
            column=index%columns; row=index//columns; x=column*cell_width; y=row*cell_height
            sheet.paste(image,(x,y+24)); draw.text((x+6,y+5),f'{frame["frame_id"]}  t={frame["time_s"]:.2f}s',fill=(255,255,255))
        encoded=BytesIO(); sheet.save(encoded,format="JPEG",quality=88,optimize=True); atomic_bytes(target,encoded.getvalue())
    finally:
        for _,image in opened: image.close()


def global_pack_contract(case_dir):
    """Budget the selected observation task; legacy Global rules stay isolated.

    This only renders instructions/schema, never freezes a request or schedules
    a model. Packed evidence remains available to downstream consumers.
    """
    from _lib.world import single_world
    if single_world.enabled(case_dir):
        from _lib.prompts import world_request_text
        from _lib.world.actor_facts import enabled as actor_fact_enabled
        request = Path(case_dir)/single_world.REQUEST
        selected_schema = (read_json(request)['response_schema'] if request.is_file()
                           else single_world.response_schema(layout_review=True,
                                actor_fact_review=actor_fact_enabled(case_dir)))
        return world_request_text(case_dir), selected_schema
    return global_request_text(case_dir), read_json(SKILLS/'schemas/global_context.schema.json')


def prepare(case_dir):
    """Extract probe/global frames. Manifest is deliberately not frozen yet."""
    case_dir = Path(case_dir).resolve(); binding = read_json(case_dir/"state/binding.json"); source = Path(binding["source_path"])
    if sha256(source) != binding["source_sha256"]: raise RuntimeError("source_changed")
    probe_path = case_dir/"state/probe.json"; globals_path = case_dir/"state/global_frames.json"
    if probe_path.exists() and globals_path.exists(): return {"probe":read_json(probe_path),"global_frames":read_json(globals_path)}
    metadata = probe(source); frame_dir = case_dir/"frames"/"probe"; frame_dir.mkdir(parents=True,exist_ok=True)
    pattern = frame_dir/"frame_%04d.png"
    command = ['ffprobe','-v','error','-select_streams','v:0','-show_frames',
               '-show_entries','frame=best_effort_timestamp_time','-of','json',str(source)]
    raw = json.loads(subprocess.run(command,check=True,capture_output=True,text=True,timeout=120).stdout)
    timestamps = [float(f['best_effort_timestamp_time']) for f in raw['frames'] if 'best_effort_timestamp_time' in f]
    if not timestamps or len(timestamps) != len(raw['frames']): raise RuntimeError('media_timestamps_missing')
    if any(not math.isfinite(t) for t in timestamps) or any(b <= a for a,b in zip(timestamps,timestamps[1:])):
        raise RuntimeError('media_timestamps_invalid')
    origin = timestamps[0]; timestamps = [t-origin for t in timestamps]
    indices = {0,len(timestamps)-1}
    for t in probe_times(metadata['duration_s']):
        indices.add(min(range(len(timestamps)),key=lambda i:abs(timestamps[i]-t)))
    indices = sorted(indices); selected_times = [timestamps[i] for i in indices]
    selection = '+'.join(f'eq(n,{i})' for i in indices)
    vf = "select='"+selection+"'"
    metadata.update(timestamp_origin_s=origin,last_frame_time_s=timestamps[-1],timestamp_source='decoded_best_effort_pts')
    subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-vf",vf,
                    '-fps_mode','passthrough',str(pattern)],check=True,timeout=120)
    paths = sorted(frame_dir.glob("frame_*.png"))
    if not paths: raise RuntimeError("frame extraction failed")
    if len(paths)!=len(selected_times): raise RuntimeError('media_frame_timestamp_count_mismatch')
    decoded = [{"frame_id":path.stem,"time_s":selected_times[index],"path":str(path)} for index,path in enumerate(paths)]
    global_indices = {decoded.index(min(decoded,key=lambda frame:(abs(frame["time_s"]-time),frame["time_s"]))) for time in uniform([frame["time_s"] for frame in decoded])}
    global_indices.update(decoded.index(item) for item in change_frames(decoded))
    global_indices.update({0,len(decoded)-1})
    if len(global_indices)>12:
        middle=sorted(global_indices-{0,len(decoded)-1})
        global_indices={0,len(decoded)-1,*uniform(middle,10)}
    global_frames = [decoded[index] for index in sorted(global_indices)][:12]
    request_text, schema_document = global_pack_contract(case_dir)
    packed_global = materialize_pack(
        [Path(item["path"]) for item in global_frames],GLOBAL_TIERS,case_dir/"frames/packed/global","global",
        text=request_text,schema=schema_document)
    packed_by_id={item["frame_id"]:item for item in packed_global["frames"]}
    global_frames=[{**item,"source_path":item["path"],"path":packed_by_id[item["frame_id"]]["path"],"sha256":packed_by_id[item["frame_id"]]["sha256"]}
                   for item in global_frames if item["frame_id"] in packed_by_id]
    sheet_paths=[]
    for index,start in enumerate(range(0,len(global_frames),6),1):
        sheet_path=case_dir/f"frames/packed/global/contact_sheet_{index:02d}.jpg"
        contact_sheet(global_frames[start:start+6],sheet_path); sheet_paths.append(sheet_path)
    anchor_indices=sorted({0,len(global_frames)//2,len(global_frames)-1}); anchor_paths=[]
    anchor_dir=case_dir/"frames/packed/global/anchors"; anchor_dir.mkdir(parents=True,exist_ok=True)
    for order,index in enumerate(anchor_indices,1):
        target=anchor_dir/f"anchor_{order:02d}.jpg"
        atomic_bytes(target,_jpeg(Path(global_frames[index]["source_path"]),1176,85)); anchor_paths.append(target)
    visual_bytes=sum(path.stat().st_size for path in sheet_paths+anchor_paths)
    if visual_bytes+OVERHEAD>BUDGET: raise RuntimeError("packing_budget_exceeded")
    probe_record={"schema_version":"1.0","case_id":case_dir.name,"source_sha256":binding["source_sha256"],"probe":metadata,"decoded_frames":decoded}
    global_record={"schema_version":"1.0","case_id":case_dir.name,"frames":global_frames,"contact_sheet_paths":[str(path) for path in sheet_paths],"contact_sheet_sha256":[sha256(path) for path in sheet_paths],"anchor_frame_paths":[str(path) for path in anchor_paths],"anchor_frame_sha256":[sha256(path) for path in anchor_paths],"packing":{"budget_bytes":BUDGET,"tier":packed_global["tier"],"width":packed_global["width"],"quality":packed_global["quality"],"estimated_bytes":visual_bytes}}
    atomic_json(probe_path,probe_record); atomic_json(globals_path,global_record)
    return {"probe":probe_record,"global_frames":global_record}


def freeze(case_dir):
    """Validate global context, plan its intervals, then freeze the manifest."""
    case_dir=Path(case_dir).resolve(); manifest_path=case_dir/"manifest.json"
    if manifest_path.exists():
        manifest=read_json(manifest_path)
        if not manifest.get("frozen"): raise RuntimeError("schema_validation_failed")
        return manifest
    binding=read_json(case_dir/"state/binding.json"); probe_record=read_json(case_dir/"state/probe.json"); global_record=read_json(case_dir/"state/global_frames.json")
    context=read_json(case_dir/"state/global_context.json"); scene_type=context.get("scene_type")
    if scene_type not in ("highway","intersection"): raise RuntimeError("unsupported_scene_type")
    metadata=probe_record["probe"]; decoded=probe_record["decoded_frames"]
    # Short clips compact overlapping textual focuses into at most two temporal
    # batches; longer clips preserve up to four model-planned focus windows.
    planned=plan_batch_intervals(context.get("critical_intervals",[]),metadata["duration_s"])
    fallback=not has_effective_intervals(context.get("critical_intervals",[]),metadata["duration_s"]); available=[frame["time_s"] for frame in decoded]; batch_specs=[]
    detail_schema=read_json(SKILLS/"schemas/batch.schema.json")
    for index, interval in enumerate(planned,1):
        selected_times = batch_times(interval,available)
        selected = [min(decoded,key=lambda frame:(abs(frame["time_s"]-value),frame["time_s"])) for value in selected_times]
        batch_id=f"batch_{index:04d}"
        packing = materialize_pack([Path(frame["path"]) for frame in selected],DETAIL_TIERS,case_dir/"frames/packed"/batch_id,batch_id,
                                   text=batch_request_text(case_dir,batch_id),schema=detail_schema)
        selected_by_id={frame["frame_id"]:frame for frame in selected}
        packed_frames=[{**item,"time_s":selected_by_id[item["frame_id"]]["time_s"],"source_path":selected_by_id[item["frame_id"]]["path"]} for item in packing["frames"]]
        batch_specs.append({"batch_id":batch_id,"start_s":interval["start_s"],"end_s":interval["end_s"],"frames":packed_frames,"packing":{key:value for key,value in packing.items() if key!="frames"}})
    if sum(len(batch["frames"]) for batch in batch_specs)>16: raise RuntimeError("unsupported_v1_limit")
    manifest={"schema_version":"1.0","case_id":case_dir.name,"frozen":True,"source_sha256":binding["source_sha256"],"scene_type":scene_type,"probe":metadata,"global_frames":global_record["frames"],"critical_intervals":planned,"interval_fallback":fallback,"batches":batch_specs,"packing":{"budget_bytes":BUDGET,"global_tier":global_record["packing"]["tier"],"global_estimated_bytes":global_record["packing"]["estimated_bytes"]}}
    atomic_json(manifest_path,manifest); progress=transition(read_json(case_dir/"state/progress.json"),"batching"); progress["manifest_sha256"]=sha256(manifest_path); atomic_json(case_dir/"state/progress.json",progress); return manifest


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan"); plan.add_argument("--video", required=True); plan.add_argument("--cases-root"); plan.add_argument("--new",action="store_true")
    ready = sub.add_parser("prepare"); ready.add_argument("--case-dir", required=True)
    frozen = sub.add_parser("freeze"); frozen.add_argument("--case-dir",required=True)
    args = parser.parse_args()
    if args.command == "plan":
        workspace = SCENARIO_ROOT; root = Path(args.cases_root) if args.cases_root else workspace/"video_extractor/cases"
        folder, created = Cases(root).bind(args.video,force_new=args.new); metadata = probe(Path(args.video)); times = probe_times(metadata["duration_s"])
        print(json.dumps({"case_id":folder.name,"created":created,"status":read_json(folder/"state/progress.json")["status"],"probe":metadata,"probe_times":times}, ensure_ascii=False))
    elif args.command == "prepare":
        prepared=prepare(args.case_dir); print(json.dumps({"case_id":prepared["probe"]["case_id"],"status":"planning","global_frames":len(prepared["global_frames"]["frames"]),"packing":prepared["global_frames"]["packing"]},ensure_ascii=False))
    elif args.command == "freeze":
        manifest=freeze(args.case_dir); print(json.dumps({"case_id":manifest["case_id"],"status":"batching","batches":len(manifest["batches"]),"detail_frames":sum(len(b["frames"]) for b in manifest["batches"]),"fallback":manifest["interval_fallback"]},ensure_ascii=False))


if __name__ == "__main__": main()
