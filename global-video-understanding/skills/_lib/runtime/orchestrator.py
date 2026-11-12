#!/usr/bin/env python3
"""Durable task backend and workflow router, retaining the v2 lifecycle base."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import tempfile
import os
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from jsonschema import Draft202012Validator

from _lib.paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
SCHEMAS=HERE/"schemas"
from _lib.runtime.case_core import BUDGET, OVERHEAD, SHORT_VIDEO_MAX_S, Cases, atomic_json, batch_request_text, freeze, global_request_text, intervals, publish, read_json, retryable, sha256, transition, validate_evidence, validate_evidence_items, validate_registry  # noqa:E402
from _lib.delivery.converter import converter_fingerprint, mela_valid  # noqa:E402
from _lib.legacy.v2.short_scene_compiler import ShortCompileError, compile_short_intersection_analysis as compile_short_intersection_analysis_pure, measurement_confidence, measurement_value  # noqa:E402

ERRORS={"unsupported_v1_limit","unsupported_scene_type","source_changed","packing_budget_exceeded","gateway_413","model_attempts_exhausted","schema_validation_failed","conversion_failed","publish_failed","retry_required","missing_output","incomplete_parts","part_too_large","short_compile_failed"}
PART_LIMIT=16*1024
PART_FIELDS={
    "scene": ({"scene_type"},{"environment","metadata"}),
    "road": ({"road"},{"junctions"}),
    "actors": ({"ego","actors"},set()),
    "events": ({"events"},{"interactions"}),
    "simulation": ({"simulation_scenarios"},set()),
    "quality": ({"quality_report"},{"limitations"}),
}
_HELD_AGENT_LEASES=set()


class ProgressNotice:
    """Human-visible waiting status on stderr; stdout remains the result protocol."""
    def __init__(self,case,stage):
        self.case_id=Path(case).name;self.stage=stage;self.previous=None;self.sent_at=float('-inf')

    def emit(self,status,**details):
        now=time.monotonic()
        if status!=self.previous or now-self.sent_at>=30:
            print(json.dumps(dict(event='pipeline_progress',case_id=self.case_id,stage=self.stage,status=status,**details)),file=sys.stderr,flush=True)
            self.previous=status;self.sent_at=now


class AgentLeaseTimeout(RuntimeError):
    code='agent_lease_timeout'

    def __init__(self,message):
        self.diagnostics=dict(retry_kind='task_reconcile')
        super().__init__(message)


@contextmanager
def agent_task_lease(case, agent, *, timeout_s=60., poll_s=1.):
    """Serialize QwenPaw routes across Cases for one target Agent.

    QwenPaw currently exposes a mutable from-agent/to-agent session route. Two
    concurrent video Cases targeting the same Agent can replace that route and
    produce session_id mismatch/404 responses. An OS file lock is process-safe
    and automatically released if the owner exits.
    """
    case=Path(case).resolve()
    root=case.parent.parent/"state/agent_leases" if case.parent.name=="cases" else case/"state/.agent_leases"
    root.mkdir(parents=True,exist_ok=True)
    safe=re.sub(r"[^A-Za-z0-9_.-]","_",agent)
    path=root/f"{safe}.lock"; token=str(path)
    if token in _HELD_AGENT_LEASES:
        yield
        return
    stream=path.open("a+")
    acquired=False; start=time.monotonic();notice=ProgressNotice(case,'agent_lease')
    diagnostic=case/'diagnostics/agent_leases'/f'{safe}.json'
    try:
        while True:
            try:
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True;break
            except BlockingIOError:
                elapsed=time.monotonic()-start
                notice.emit('waiting_for_agent' if elapsed<timeout_s else 'agent_lease_timeout',agent=agent,elapsed_s=round(elapsed,1),timeout_s=timeout_s)
                atomic_json(diagnostic,dict(agent=agent,status='waiting' if elapsed<timeout_s else 'timeout',
                    elapsed_s=elapsed,timeout_s=timeout_s,lock_path=str(path),retry_kind='task_reconcile'))
                if elapsed>=timeout_s:raise AgentLeaseTimeout('Agent route is busy; no new task submitted. Reconcile the existing task before retrying.')
                time.sleep(min(poll_s,timeout_s-elapsed))
        atomic_json(diagnostic,dict(agent=agent,status='acquired',elapsed_s=time.monotonic()-start,lock_path=str(path)))
        _HELD_AGENT_LEASES.add(token)
        yield
    finally:
        original_error=sys.exc_info()[1]
        try:
            if acquired:
                _HELD_AGENT_LEASES.discard(token)
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
                try:
                    atomic_json(diagnostic,dict(agent=agent,status='released',lock_path=str(path)))
                except OSError as error:
                    if original_error is None:
                        raise
                    print(json.dumps(dict(event='lease_diagnostic_write_failed',agent=agent,
                                          error_type=type(error).__name__)),file=sys.stderr)
        finally:
            stream.close()



def validation_feedback(error):
    """Carry exact paths/rules to repairs rather than truncating them into prose."""
    if isinstance(error, dict):
        message = error.get('message', '')
        details = error.get('validation_details', {})
    else:
        message = str(error)
        details = getattr(error, 'diagnostics', {})
    feedback = '\n上一次 candidate 未通过确定性门禁：' + message[:500]
    if isinstance(details, dict) and details:
        feedback += '\n结构化失败定位：' + json.dumps(details, ensure_ascii=False, allow_nan=False)
    return feedback + '。仅修正定位问题，保留其他事实与证据并重新写指定 candidate；不要解释。'


def quarantine_candidate(case, key, candidate_path, attempt):
    """Remove invalid model output from the live path while retaining audit evidence."""
    if candidate_path is None or key == "aggregation":
        return None
    source = Path(candidate_path)
    if not source.is_file():
        return None
    folder = Path(case)/"diagnostics/invalid_candidates"
    folder.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    digest = sha256(source)[:16]
    target = folder/f"{safe_key}.attempt_{attempt}.{digest}{source.suffix}"
    sequence = 1
    while target.exists():
        target = folder/f"{safe_key}.attempt_{attempt}.{digest}.{sequence}{source.suffix}"
        sequence += 1
    source.replace(target)
    return target


def candidate_snapshot(case, stage, path):
    """Hash, archive and parse the exact same bytes for reproducible validation."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    archive = Path(case)/"diagnostics/candidate_snapshots"/f"{stage}.{digest}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if archive.read_bytes() != raw:
            raise OutputValidationError("source_changed", "Candidate snapshot hash/content mismatch")
    else:
        with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
    return json.loads(raw), digest


def resolve_workflow_parameters(parameters, case_id=None):
    """Default new work to v3; an empty resume request inherits its Case contract."""
    from _lib.contracts.v3 import normalize_parameters
    from _lib.runtime.execution_policy import require_active_policy
    if case_id:
        case=(EXTRACTOR/"cases"/case_id).resolve()
        if case.parent!=(EXTRACTOR/"cases").resolve() or not (case/"case.json").is_file():
            raise ValueError("unknown case_id")
        saved_parameters=read_json(case/"case.json").get("parameters",{})
        require_active_policy(saved_parameters)
        if not parameters:
            parameters=saved_parameters
    require_active_policy(parameters)
    return normalize_parameters(parameters)


class OutputValidationError(ValueError):
    def __init__(self,code,message):
        super().__init__(message); self.code=code


def schema(name,data):
    from _lib.contracts.fidelity import validator
    validator(name).validate(data)


class FastPathOutcome(str, Enum):
    COMPILED = "compiled"
    NOT_APPLICABLE = "not_applicable"


def validate_global_context(data, duration):
    schema("global_context.schema.json",data); validate_evidence(data); intervals(data["critical_intervals"],duration)
    if duration<=SHORT_VIDEO_MAX_S and data.get("scene_type")=="intersection":
        invalid=[actor.get("actor_id") for actor in data.get("actor_hypotheses",[]) if actor.get("actor_type") not in {"vehicle","pedestrian","vehicle_group"}]
        if invalid:
            raise ValueError(f"short intersection actor_type must use canonical vehicle/pedestrian taxonomy: {invalid}")
        unclassified=[actor.get("actor_id") for actor in data.get("actor_hypotheses",[]) if not isinstance(actor.get("is_static"),bool)]
        if unclassified:
            raise ValueError(f"short intersection actors require explicit is_static classification: {unclassified}")
        facts=data.get("scene_facts") or {}
        required=("travel_lane_count","ego_lane_from_right","left_parking_strip","right_parking_strip","stop_line_x_m","ego_speed_mps","crosswalk_count","traffic_light_count")
        if (not all(key in facts for key in required)
                or facts["ego_lane_from_right"]>facts["travel_lane_count"]
                or measurement_value(facts.get("stop_line_x_m"),0)<=0
                or measurement_value(facts.get("ego_speed_mps"),-1)<0):
            raise ValueError("short intersection scene_facts missing or inconsistent")
        ego_crossing=measurement_value(facts.get("ego_stop_line_crossing_s"))
        if ego_crossing is not None and (ego_crossing<0 or ego_crossing>duration or measurement_value(facts["ego_speed_mps"],0)<=0):
            raise ValueError("short intersection ego crossing time is inconsistent")
        if isinstance(facts.get("ego_speed_mps"),dict) and facts["ego_speed_mps"].get("status")!="estimated":
            raise ValueError("short intersection ego speed must be estimated")
        if isinstance(facts.get("ego_stop_line_crossing_s"),dict) and facts["ego_stop_line_crossing_s"].get("status")!="observed":
            raise ValueError("short intersection ego crossing time must be observed")
        for actor in data.get("actor_hypotheses",[]):
            if actor.get("actor_type")!="vehicle": continue
            speed=measurement_value(actor.get("speed_mps")); entry=measurement_value(actor.get("intersection_entry_s"))
            initial=measurement_value(actor.get("initial_longitudinal_m"))
            if not actor["is_static"] and (speed is None or (initial is None and entry is None)):
                raise ValueError(f"short intersection vehicle metrics missing: {actor.get('actor_id')}")
            if not actor["is_static"] and not isinstance(actor.get("lane_from_right"),int):
                raise ValueError(f"moving vehicle lane_from_right missing: {actor.get('actor_id')}")
            if entry is not None and (entry<0 or entry>duration or not speed or speed<=0):
                raise ValueError(f"moving vehicle entry time inconsistent: {actor.get('actor_id')}")
            if isinstance(actor.get("speed_mps"),dict) and actor["speed_mps"].get("status")!="estimated":
                raise ValueError(f"moving vehicle speed must be estimated: {actor.get('actor_id')}")
            if isinstance(actor.get("intersection_entry_s"),dict) and actor["intersection_entry_s"].get("status")!="observed":
                raise ValueError(f"moving vehicle entry time must be observed: {actor.get('actor_id')}")


def aggregate_prompt(case,part=None,valid_parts=(),feedback=None):
    part=part or "scene"; required,optional=PART_FIELDS[part]
    destination=case/"final/.analysis_parts"/f"{part}.json"
    allowed=sorted({"part","complete"}|required|optional)
    batches=sorted((case/"batches").glob("batch_*.json")); part_dir=case/"final/.analysis_parts"
    inputs={
        "scene":[case/"state/global_context.json",*batches],
        "road":[case/"manifest.json",case/"state/global_context.json",*batches],
        "actors":[case/"state/global_context.json",case/"state/actor_registry.json",*batches],
        "events":[case/"state/global_context.json",case/"state/actor_registry.json",*batches,part_dir/"actors.json"],
        "simulation":[part_dir/"road.json",part_dir/"actors.json",part_dir/"events.json"],
        "quality":[case/"state/global_context.json",*batches],
    }[part]
    correction=f" 上一次完整门禁错误：{feedback[:500]}。本次在该分片中修正。" if feedback else ""
    concise=" road 分片只表达可转换的最小道路拓扑；不要推断或争论街名、城市、绝对方位、信号相位历史。名称不确定就省略，尺寸使用简短 estimated 值。" if part=="road" else ""
    lane_rule=" road 分片必须以批次中实际可见的纵向分隔线为边界划分行驶车道，明确 ego 所在车道；停车带单列为 parking，不得吞并相邻行驶车道。actors 分片必须保留同一车道内近、远不同运动车辆，且每个待仿真车辆轨迹首点明确 lane_id、position_ego_m.x 和 speed_mps 的观测或估计。" if part in {"road","actors"} else ""
    simulation_rule=" simulation 分片只建立原视频基线，不创建反事实 variant。involved_actor_ids 只放本次回放需要实例化的参与者；initial_conditions 必须逐字/逐值继承 ego/actors 轨迹首点的 lane_id、position_ego_m，不得独立猜另一套位置。初始速度使用 speed_kmh（km/h），值为轨迹 speed_mps.value 或 speed_mps 数值乘以 3.6，保留 estimated/confidence。路边静止车辆作为背景保留，不放入动态 involved_actor_ids；缺乏可执行位置和速度的背景行人保留在 actors 与观察事件中。" if part=="simulation" else ""
    contact_rule=" 若连续帧或源视频帧直接显示车体接触、接触反应、碎片或扬尘，不得仅因更晚帧已经分离、出现其他部位损伤而否定先前碰撞；必须按时间顺序表达主碰撞、次生碰撞和碰撞后分离。不要把已观察到的次生碰撞另建为假设 variant。" if part in {"events","simulation","quality"} else ""
    return f"你处于 Case {case.name} 的确定性聚合阶段。禁止创建 subagent、调用其他 Agent、另开 session。只允许读取这些输入：{', '.join(map(str,inputs))}，以及只读 schema {SCHEMAS/'analysis.schema.json'}；禁止读取 converter、history、sessions、runs、workspaces、实现代码或其他文件。已有有效分片：{list(valid_parts)}。本次只生成缺失分片 {part}。固定工具顺序：读取上述输入后，立即调用且只调用一次 write_file；不要继续分析或解释。写到 {destination} 后立即停止。文件必须是 UTF-8 JSON、最多 {PART_LIMIT} bytes，且只能含字段 {allowed}；part 必须等于 {part!r}，complete 必须为 true，必填业务字段为 {sorted(required)}。{correction}{concise}{lane_rule}{simulation_rule}{contact_rule} 不要写完整 analysis、不要转换、不要报告、不要输出自评长文。合并观察须按时间顺序并裁决 challenges；最终 registry 的 actor_id、actor_type、first_seen_frame_id、identity_description 逐字复制。scene_type 仅 highway/intersection；路口 road_001 为自车进口并顺时针编号，车道按道路顺序且每条道路从右到左全局连续编号。event 引用最终 registry 且带 evidence；无法实测的工程量用 estimated+confidence/range，无法支持则 unknown+reason。"


def aggregate_request_size(case):
    paths=[case/"manifest.json",case/"state/global_context.json",case/"state/actor_registry.json"]+sorted((case/"batches").glob("batch_*.json"))
    return sum(len(path.read_bytes()) for path in paths if path.is_file())+len(aggregate_prompt(case).encode())+len((SCHEMAS/"analysis.schema.json").read_bytes())+OVERHEAD


def inspect_analysis_parts(directory):
    directory=Path(directory); valid={}; errors={}
    for name,(required,optional) in PART_FIELDS.items():
        path=directory/f"{name}.json"
        if not path.is_file(): continue
        if path.stat().st_size>PART_LIMIT:
            errors[name]={"code":"part_too_large","message":f"{name}.json exceeds {PART_LIMIT} bytes"}; continue
        try: data=read_json(path)
        except Exception as error:
            errors[name]={"code":"schema_validation_failed","message":f"{name}.json is invalid JSON: {error}"}; continue
        allowed={"part","complete"}|required|optional
        if not isinstance(data,dict) or data.get("part")!=name or data.get("complete") is not True:
            errors[name]={"code":"incomplete_parts","message":f"{name}.json lacks matching part/complete"}; continue
        missing=required-set(data); extra=set(data)-allowed
        if missing or extra:
            errors[name]={"code":"schema_validation_failed","message":f"{name}.json missing={sorted(missing)} extra={sorted(extra)}"}; continue
        try: validate_evidence(data)
        except Exception as error:
            errors[name]={"code":"schema_validation_failed","message":str(error)}; continue
        valid[name]=data
    return valid,errors


def assemble_analysis_parts(directory):
    valid,errors=inspect_analysis_parts(directory); missing=[name for name in PART_FIELDS if name not in valid]
    if missing:
        code=next((errors[name]["code"] for name in missing if name in errors),"incomplete_parts")
        detail="; ".join(errors[name]["message"] for name in missing if name in errors) or f"missing parts: {missing}"
        raise OutputValidationError(code,detail)
    analysis={}
    for name in PART_FIELDS:
        analysis.update({key:value for key,value in valid[name].items() if key not in {"part","complete"}})
    return analysis


def part_for_analysis_error(error):
    path=list(getattr(error,"absolute_path",[]) or getattr(error,"path",[]))
    if path:
        field=str(path[0])
        for name,(required,optional) in PART_FIELDS.items():
            if field in required|optional: return name
    message=str(error).lower()
    if any(word in message for word in ("initial condition", "initial_conditions", "scenario", "baseline", "participant")): return "simulation"
    if any(word in message for word in ("trajectory", "longitudinal x")): return "actors"
    if any(word in message for word in ("road","lane","clockwise")): return "road"
    if any(word in message for word in ("actor identity","historical actor","duplicate actor")): return "actors"
    if any(word in message for word in ("event","dangling actor")): return "events"
    return "quality"


def normalize_initial_speeds(data):
    """Normalize explicit m/s aliases without discarding measurement provenance."""
    for scenario in data.get("simulation_scenarios", []):
        for actor_id, condition in (scenario.get("initial_conditions") or {}).items():
            if not isinstance(condition, dict) or "speed_mps" not in condition:
                continue
            source=condition["speed_mps"]
            value=source.get("value") if isinstance(source,dict) else source
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError(f"invalid scenario initial speed_mps for {actor_id}")
            converted=round(value*3.6,10)
            if "speed_kmh" in condition:
                existing=measurement_value(condition["speed_kmh"])
                if existing is None or not math.isfinite(existing) or abs(existing-converted)>0.05:
                    raise ValueError(f"conflicting scenario initial speed units for {actor_id}")
                continue
            if isinstance(source,dict):
                normalized=dict(source); normalized["value"]=converted
                if "range" in normalized:
                    normalized["range"]=[round(bound*3.6,10) for bound in normalized["range"]]
                if "unit" in normalized: normalized["unit"]="km/h"
                condition["speed_kmh"]=normalized
            else:
                condition["speed_kmh"]=converted


def validate_analysis(data, registry=None):
    schema("analysis.schema.json",data)
    projection_pending = (
        data.get("quality_report", {}).get("execution_ready") is False
        and data.get("quality_report", {}).get("execution_projection")
        == "analysis_values_then_hardcoded_fallbacks"
    )
    from _lib.runtime.execution_scope import validate_analysis_scope
    scope_excluded = validate_analysis_scope(data)
    normalize_initial_speeds(data)
    from _lib.behavior.compile import validate_preview_analysis
    validate_preview_analysis(data)
    from _lib.behavior.intersection import validate_projection
    validate_projection(data)
    validate_evidence(data)
    actors={actor["actor_id"] for actor in data.get("actors",[])}
    if len(actors)!=len(data.get("actors",[])): raise ValueError("duplicate actor ID")
    reference_actors=actors|({data.get("ego",{}).get("actor_id")} if data.get("ego",{}).get("actor_id") else set())
    for event in data.get("events",[]):
        if set(event.get("involved_actor_ids",[]))-reference_actors: raise ValueError("dangling actor reference")
        validate_evidence_items(event["evidence"])
    if registry is not None:
        analysis_actors={actor["actor_id"]:actor for actor in data.get("actors",[])}
        ego=data.get("ego",{}); ego_id=ego.get("actor_id")
        mapped_ego_id=ego.get("registry_actor_id")
        if mapped_ego_id: analysis_actors[mapped_ego_id]=ego
        known=set(analysis_actors)|({ego_id} if ego_id else set())
        if {actor["actor_id"] for actor in registry}-known-scope_excluded: raise ValueError("historical actor omitted from analysis")
        for actor in registry:
            current=analysis_actors.get(actor["actor_id"])
            current_type=current.get("source_actor_type",current.get("actor_type")) if current else None
            if current and (current_type!=actor.get("actor_type") or current.get("identity_description")!=actor.get("identity_description")):
                raise ValueError("actor identity changed in analysis")
    road=data["road"]; roads=road["roads"]; lanes=road["lanes"]
    from _lib.contracts.lane_locations import validate_actor_locations
    validate_actor_locations([data['ego'], *data.get('actors', [])], road)
    road_ids=[item["road_id"] for item in roads]
    if len(set(road_ids))!=len(road_ids): raise ValueError("duplicate road ID")
    if data["scene_type"]=="intersection":
        if not road_ids or road_ids[0]!="road_001" or road_ids != [f"road_{n:03d}" for n in range(1,len(road_ids)+1)]:
            raise ValueError("invalid clockwise road numbering")
    lane_ids={item["lane_id"] for item in lanes}
    if len(lane_ids)!=len(lanes): raise ValueError("duplicate lane ID")
    lane_number=1
    for item in roads:
        owned=item["lane_ids"]
        if not owned and not projection_pending:
            raise ValueError("executable road requires non-empty lane_ids")
        if any(value not in lane_ids for value in owned): raise ValueError("dangling lane reference")
        expected=[f'lane_{n:03d}' for n in range(lane_number,lane_number+len(owned))]; lane_number+=len(owned)
        if owned!=expected: raise ValueError("invalid right-to-left lane numbering")
    if data["scene_type"]=="intersection" and len(roads)<3 and not projection_pending:
        raise ValueError("intersection road network requires at least three road arms")
    # One physical bidirectional arm legitimately appears in both directional
    # memberships. Use the same executable topology contract as World/Builder
    # instead of the obsolete "exactly once across both arrays" shortcut.
    from _lib.world.topology import validate_topology
    if any(any(field in junction for field in ('incoming_roads','outgoing_roads','lane_connections'))
           for junction in data.get('junctions',[]) if isinstance(junction,dict)):
        if projection_pending:
            from _lib.world.topology import validate_observed_topology
            validate_observed_topology(data)
        else:
            validate_topology(data)
    for actor in [data["ego"], *data.get("actors", [])]:
        for point in actor.get("trajectory", []):
            if point.get("lane_id") is not None and point["lane_id"] not in lane_ids:
                raise ValueError(f"unknown trajectory lane for {actor['actor_id']}")
    compiled=bool(data.get("quality_report",{}).get("deterministic_compilation"))
    if data["scene_type"]=="intersection" and compiled:
        travel_count=road.get("travel_lane_count"); ego_index=road.get("ego_lane_from_right")
        if not isinstance(travel_count,int) or not isinstance(ego_index,int) or not 1<=ego_index<=travel_count:
            raise ValueError("compiled intersection lane facts missing")
        if len(roads)<3 or len(roads[0]["lane_ids"])!=travel_count or len(roads[2]["lane_ids"])!=travel_count:
            raise ValueError("compiled intersection travel lane count diverges")
        compiled_lane_ids=roads[0]["lane_ids"]+roads[2]["lane_ids"]
        lane_by_id={lane["lane_id"]:lane for lane in lanes}
        if any(lane_by_id[lane_id].get("lane_type")!="driving" for lane_id in compiled_lane_ids):
            raise ValueError("compiled intersection travel lanes must be driving")
        if any(lane.get("lane_type")=="parking" for lane in lanes):
            raise ValueError("compiled intersection parking areas must not be lanes")
        if data["ego"]["trajectory"][0].get("lane_id")!=roads[0]["lane_ids"][ego_index-1]:
            raise ValueError("compiled ego lane diverges from ego_lane_from_right")
        crossing=data.get("metadata",{}).get("ego_stop_line_crossing_s")
        duration=data.get("metadata",{}).get("duration_s")
        if isinstance(crossing,(int,float)) and isinstance(duration,(int,float)) and crossing<duration:
            first=data["ego"]["trajectory"][0]; speed=first.get("speed_mps")
            road_length=measurement_value(roads[0].get("road_geometry",{}).get("visible_length_m"))
            if not isinstance(speed,(int,float)) or road_length is None or speed*duration<=road_length:
                raise ValueError("observed ego crossing is unreachable in compiled road geometry")
            if data["ego"]["trajectory"][-1].get("lane_id")!=roads[2]["lane_ids"][ego_index-1]:
                raise ValueError("observed ego crossing must end on straight continuation lane")
        lane_road={lane_id:item for item in roads for lane_id in item["lane_ids"]}
        for scenario in data.get("simulation_scenarios",[]):
            for actor_id,condition in (scenario.get("initial_conditions") or {}).items():
                lane_id=condition.get("lane_id"); position=condition.get("position_ego_m") or {}
                source_road=lane_road.get(lane_id); x=position.get("x")
                length=measurement_value((source_road or {}).get("road_geometry",{}).get("visible_length_m"))
                if source_road and isinstance(x,(int,float)) and length is not None and not 0<=x<length:
                    raise ValueError(f"compiled initial position outside referenced road for {actor_id}")
    if data["scene_type"] == "intersection" and not projection_pending:
        actor_by_id={actor["actor_id"]:actor for actor in data.get("actors",[])}
        scenarios=data.get("simulation_scenarios") or []
        if not scenarios:
            raise ValueError("intersection analysis requires one original-video baseline scenario")
        for scenario in scenarios:
            participants=scenario.get("involved_actor_ids")
            conditions=scenario.get("initial_conditions")
            if participants is None and conditions is None: continue
            if not isinstance(participants,list) or not participants:
                raise ValueError("intersection scenario requires involved_actor_ids")
            if not isinstance(conditions,dict) or set(conditions)!=set(participants):
                raise ValueError("intersection initial_conditions must exactly match involved_actor_ids")
            ego_aliases={"ego",data["ego"]["actor_id"]}
            if not (set(participants)&ego_aliases):
                raise ValueError("intersection baseline must include ego")
            moving_vehicle_ids={actor["actor_id"] for actor in data.get("actors",[]) if actor.get("actor_type")=="vehicle" and actor.get("simulation_role","participant")=="participant" and not actor.get("is_static") and (measurement_value((actor.get("trajectory") or [{}])[0].get("speed_mps"),0) or 0)>0}
            if moving_vehicle_ids-set(participants):
                raise ValueError(f"intersection baseline omits moving vehicles: {sorted(moving_vehicle_ids-set(participants))}")
            for actor_id in participants:
                actor=data["ego"] if actor_id in {"ego",data["ego"]["actor_id"]} else actor_by_id.get(actor_id)
                if actor is None: raise ValueError(f"unknown scenario participant {actor_id}")
                if actor is not data["ego"] and actor.get("actor_type") != "vehicle":
                    continue
                trajectory=actor.get("trajectory") or []
                first=trajectory[0] if trajectory else {}
                condition=conditions[actor_id]
                lane=condition.get("lane_id")
                position=condition.get("position_ego_m") or {}
                speed=condition.get("speed_kmh")
                speed=speed.get("value") if isinstance(speed,dict) else speed
                if lane not in lane_ids or not isinstance(position.get("x"),(int,float)) or not isinstance(speed,(int,float)):
                    raise ValueError(f"executable initial condition missing for {actor_id}")
                first_position=first.get("position_ego_m") or {}
                first_speed=measurement_value(first.get("speed_mps"))
                if first.get("lane_id")!=lane or not isinstance(first_position.get("x"),(int,float)) or first_speed is None:
                    raise ValueError(f"trajectory initial state missing for {actor_id}")
                if abs(float(first_position["x"])-float(position["x"]))>0.05 or abs(first_speed*3.6-float(speed))>0.05:
                    raise ValueError(f"scenario initial state diverges from trajectory for {actor_id}")
                # Direction and executable motion are checked by the motion
                # contract, not inferred here from a positive speed magnitude.


def validate_target_semantics(analysis, target):
    """Reject a valid-looking intersection target that changed analysis semantics."""
    if analysis.get("scene_type") != "intersection": return
    # This wrapper also serves legacy v2 targets, whose timelines are not
    # compiled by the v3 standard-motion compiler. Keep that contract isolated.
    # The formal v3 delivery gate always checks source constraints, even when
    # simulation_scenarios is empty (see delivery/checks.py).
    if analysis.get('quality_report', {}).get('builder') == 'v3-deterministic':
        from _lib.delivery.source_constraints import check as check_source_constraints
        check_source_constraints(analysis, target)
    scenarios=analysis.get("simulation_scenarios") or []
    if not scenarios: return
    primary=scenarios[0]; participants=primary.get("involved_actor_ids") or []
    conditions=primary.get("initial_conditions") or {}
    if not participants or not conditions: return
    lane_names={}
    target_roads=target["scene"]["road_network"]["roads"]
    source_lanes={lane["lane_id"]:lane for lane in analysis["road"]["lanes"]}
    if len(target_roads)!=len(analysis["road"]["roads"]): raise ValueError("target road count diverges from analysis")
    for road_index,(source_road,target_road) in enumerate(zip(analysis["road"]["roads"],target_roads),1):
        target_lanes=target_road["segments"][0]["lanes"]
        if len(target_lanes)!=len(source_road["lane_ids"]): raise ValueError("target lane count diverges from analysis")
        for lane_index,(lane_id,target_lane) in enumerate(zip(source_road["lane_ids"],target_lanes),1):
            lane_names[lane_id]=f"Road_{road_index}_Seg_1_Lane_{lane_index}"
            if target_lane.get("type")!=source_lanes[lane_id].get("lane_type","driving"):
                raise ValueError(f"target lane type diverges for {lane_id}")
    source_roads=analysis["road"]["roads"]
    parking_sides_by_road={}
    if source_roads:
        ego_road_id=source_roads[0]["road_id"]; default_parking_roads={ego_road_id}
        for junction in analysis.get("junctions",[]):
            for connection in junction.get("lane_connections",[]):
                if connection.get("incoming_road_id")==ego_road_id and connection.get("outgoing_road_id"):
                    default_parking_roads.add(connection["outgoing_road_id"])
        for area in analysis.get("road",{}).get("roadside_areas",[]):
            side=str(area.get("side","")).lower()
            from _lib.contracts.parking import parking_area_type
            if parking_area_type(area)!="curbside_parking" or side not in {"left","right"}: continue
            for road_id in ({area["road_id"]} if area.get("road_id") else default_parking_roads):
                parking_sides_by_road.setdefault(road_id,set()).add(side)
    for source_road,target_road in zip(source_roads,target_roads):
        segment=target_road["segments"][0]; expected_sides=parking_sides_by_road.get(source_road["road_id"],set())
        for side in ("left","right"):
            shoulder=segment.get(f"{side}_shoulder")
            if (shoulder is not None)!=(side in expected_sides):
                raise ValueError(f"target roadside parking shoulder diverges for {source_road['road_id']}:{side}")
            if shoulder is not None and (shoulder.get("type")!="paved" or not isinstance(shoulder.get("width"),(int,float)) or shoulder["width"]<=0):
                raise ValueError(f"target roadside parking shoulder invalid for {source_road['road_id']}:{side}")
    actor_by_id={actor["actor_id"]:actor for actor in analysis.get("actors",[])}
    vehicle_name_by_actor_id={actor_id:actor_id.replace("vehicle_","Vehicle_") for actor_id in actor_by_id}
    expected_vehicles={"Ego"}; expected_pedestrians=set()
    for actor_id in participants:
        if actor_id in {"ego",analysis["ego"]["actor_id"]}: continue
        actor=actor_by_id[actor_id]
        if actor.get("actor_type")=="vehicle": expected_vehicles.add(vehicle_name_by_actor_id[actor_id])
        elif actor.get("actor_type")=="pedestrian": expected_pedestrians.add(actor_id.replace("pedestrian_","Pedestrian_"))
    background_static_ids={actor_id for actor_id,actor in actor_by_id.items() if actor.get("actor_type")=="vehicle" and actor.get("is_static") and actor_id not in participants}
    expected_vehicles.update(vehicle_name_by_actor_id[actor_id] for actor_id in background_static_ids)
    target_vehicles={vehicle["name"]:vehicle for vehicle in target["scenario"]["vehicles"]}
    target_pedestrians={actor["name"] for actor in target["scenario"]["pedestrians"]}
    if set(target_vehicles)!=expected_vehicles: raise ValueError("target vehicle set diverges from dynamic participants plus static background vehicles")
    if target_pedestrians!=expected_pedestrians: raise ValueError("target pedestrian set diverges from involved_actor_ids")
    for actor_id in participants:
        if actor_id in {"ego",analysis["ego"]["actor_id"]}:
            target_actor=target_vehicles["Ego"]
        elif vehicle_name_by_actor_id.get(actor_id) in expected_vehicles:
            target_actor=target_vehicles[vehicle_name_by_actor_id[actor_id]]
        else:
            continue
        condition=conditions[actor_id]; position=condition["position_ego_m"]
        speed=condition["speed_kmh"]
        speed=speed.get("value") if isinstance(speed,dict) else speed
        maneuver=target_actor["timeline"][0]; action=maneuver["action"]
        target_position=action["start_position"]["position"]
        if target_position["reference_name"]!=lane_names[condition["lane_id"]]:
            raise ValueError(f"target lane diverges for {actor_id}")
        if abs(float(target_position["longitudinal_value"])-float(position["x"]))>0.05:
            raise ValueError(f"target position diverges for {actor_id}")
        if abs(float(action["start_longitudinal_velocity"])-float(speed))>0.05:
            raise ValueError(f"target speed diverges for {actor_id}")
        source_actor=analysis["ego"] if target_actor["name"]=="Ego" else actor_by_id[actor_id]
        motion=str(condition.get("motion_state","")).lower()
        is_static=bool(source_actor.get("is_static")) or motion in {"parked","stationary","stopped"}
        if (maneuver["type"]=="stop") != is_static:
            raise ValueError(f"target motion state diverges for {actor_id}")
    incoming_lane_ids=analysis["road"]["roads"][0]["lane_ids"]
    for actor_id in background_static_ids:
        actor=actor_by_id[actor_id]; target_actor=target_vehicles[vehicle_name_by_actor_id[actor_id]]
        trajectory=actor.get("trajectory") or []
        first=trajectory[0] if trajectory else {}
        position=first.get("position_ego_m") or {}
        maneuver=target_actor["timeline"][0]; action=maneuver["action"]
        target_position=action["start_position"]["position"]
        if maneuver.get("type")!="stop" or maneuver.get("transition",{}).get("type")!="simulation_time":
            raise ValueError(f"static background vehicle must remain stopped for {actor_id}")
        if abs(float(action.get("start_longitudinal_velocity",0)))>0.05 or abs(float(action.get("end_longitudinal_velocity",0)))>0.05:
            raise ValueError(f"static background vehicle speed diverges for {actor_id}")
        if not isinstance(position.get("x"),(int,float)) or abs(float(target_position["longitudinal_value"])-float(position["x"]))>0.05:
            raise ValueError(f"static background vehicle position diverges for {actor_id}")
        source_lane=first.get("lane_id")
        if source_lane is not None:
            if target_position["reference_name"]!=lane_names[source_lane]:
                raise ValueError(f"static background vehicle lane diverges for {actor_id}")
            continue
        side=str(actor.get("parking_side") or ("left" if float(position.get("y",0))>0 else "right")).lower()
        reference_lane=incoming_lane_ids[-1] if side=="left" else incoming_lane_ids[0]
        expected_specifier="left_of" if side=="left" else "right_of"
        if target_position["reference_name"]!=lane_names[reference_lane] or target_position.get("lateral_specifier")!=expected_specifier or target_position.get("lateral_unit")!="m" or float(target_position.get("lateral_value",0))<=0:
            raise ValueError(f"static background vehicle roadside placement diverges for {actor_id}")
    expected_description=primary.get("description")
    if expected_description and target["scenario"].get("description")!=expected_description:
        raise ValueError("target scenario description diverges from analysis")
    source_junction=(analysis.get("junctions") or [{}])[0]
    if len(target["entities"]["traffic_lights"])!=len(source_junction.get("traffic_signals",[])):
        raise ValueError("target traffic-light count diverges from analysis")
    if len(target["entities"]["crosswalks"])!=len(source_junction.get("crosswalks",[])):
        raise ValueError("target crosswalk count diverges from analysis")


def compile_short_intersection_analysis(case, context):
    """Compatibility wrapper around the shared, pure short-scene compiler."""
    duration = float(read_json(Path(case) / "state/probe.json")["probe"]["duration_s"])
    return compile_short_intersection_analysis_pure(context, duration)


BACKEND_STOP_CODES = {"service_access_denied", "task_cancelled", "submission_unresolved", "backend_unavailable"}
BACKEND_CODES = BACKEND_STOP_CODES | {"backend_protocol_error", "task_status_unavailable",
    "model_stream_interrupted", "timeout", "gateway_413", "gateway_429", "gateway_5xx", "empty_response"}


class BackendError(RuntimeError):
    def __init__(self, code, message, *, cancel_source="unknown", telemetry=None, submission_not_sent=False):
        super().__init__(message)
        self.code = code
        self.cancel_source = cancel_source
        self.telemetry = telemetry
        self.submission_not_sent = submission_not_sent


def backend_error_code(error):
    return getattr(error, "code", None) or (str(error) if str(error) in BACKEND_CODES else classify(str(error)))


def submission_not_sent(error):
    """A partial task receipt takes precedence over a rejection classification."""
    if getattr(error, 'task_id', None) or '[task_id:' in str(error).lower():
        return False
    return bool(getattr(error, 'submission_not_sent', False) or backend_error_code(error) in {
        'service_access_denied', 'task_cancelled', 'backend_unavailable', 'gateway_413', 'gateway_429'})


class QwenPawBackend:
    """Durable task backend; fresh sessions unless explicitly continuing one."""
    def __init__(self,from_agent="video-coordinator",timeout=3600,poll_interval=5):
        self.from_agent=from_agent; self.timeout=timeout; self.poll_interval=poll_interval
        # A lost QwenPaw task/session route must not hold the Case lease forever.
        self.max_poll_errors=6

    @staticmethod
    def _run(command,timeout):
        try:
            done=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise BackendError("timeout", "Backend command timed out; remote acceptance is unknown") from error
        output=done.stdout+done.stderr
        if done.returncode:
            code = 'backend_protocol_error' if '[task_id:' in output.lower() else classify(output)
            raise BackendError(code, output[-4000:])
        return output

    def submit(self,agent,prompt,*,session_id=None):
        command=["qwenpaw","agents","chat","--background","--from-agent",self.from_agent,"--to-agent",agent,"--text",prompt,"--task-timeout",str(self.timeout)]
        if session_id:
            command.extend(["--session-id",session_id])
        output=self._run(command,30)
        submitted=self._parse_submission(output)
        if session_id and submitted['session_id'] != session_id:
            raise BackendError('backend_protocol_error', 'Submitted task returned a different session: ' + output[-1000:])
        return submitted

    @staticmethod
    def _parse_submission(output):
        task=re.search(r"\[TASK_ID:\s*([^\]]+)\]",output); session=re.search(r"\[SESSION:\s*([^\]]+)\]",output)
        if not task or not session:
            code = 'backend_protocol_error' if task else classify(output)
            raise BackendError(code, output[-4000:] or "Missing task/session receipt")
        return {"task_id":task.group(1).strip(),"session_id":session.group(1).strip()}

    def poll(self,task_id,agent):
        command=["qwenpaw","agents","chat","--background","--task-id",task_id,"--to-agent",agent,"--json-output"]
        output=self._run(command,getattr(self,"poll_timeout",90))
        return self._parse_poll(output)

    def _parse_poll(self, output):
        start=output.find("{")
        if start<0: raise BackendError(classify(output), output[-4000:] or "Missing backend JSON response")
        try: data=json.JSONDecoder().raw_decode(output[start:])[0]
        except json.JSONDecodeError as error: raise BackendError("backend_protocol_error", "Malformed backend response: " + output[-1000:]) from error
        if not isinstance(data,dict): raise BackendError("backend_protocol_error", "Backend response must be an object")
        state=data.get("status","unknown")
        telemetry=self._telemetry(data)
        if state in {"submitted","pending","running"}: return {"status":state,**telemetry}
        if state in {"cancelled", "canceled"}:
            raise BackendError("task_cancelled", "Task cancelled",
                cancel_source=data.get("cancel_source") or "unknown", telemetry=telemetry.get("telemetry"))
        if state!="finished": raise BackendError("backend_protocol_error", "Unexpected backend status: " + str(state))
        result=data.get("result") or {}
        if not isinstance(result,dict): raise BackendError("backend_protocol_error", "Backend result must be an object")
        result_status=result.get("status")
        if result_status=="completed": return {"status":"completed","session_id":result.get("session_id"),**telemetry}
        if result_status=="failed":
            error=result.get("error") or {}
            message=error.get("message","") if isinstance(error,dict) else str(error)
            structured = str(error.get("code", error.get("status_code", ""))) if isinstance(error,dict) else ""
            code = structured if structured in BACKEND_CODES else classify(structured + " " + message)
            extra = {}
            if isinstance(error, dict):
                if error.get("cancel_source"): extra["cancel_source"] = error["cancel_source"]
                if error.get("retry_after_s") is not None: extra["retry_after_s"] = error["retry_after_s"]
            return {"status":"failed","error":code,"message":message or structured,**telemetry,**extra}
        raise BackendError("backend_protocol_error", "Unexpected backend result status: " + str(result_status))

    @staticmethod
    def _telemetry(data):
        """Normalize optional QwenPaw runtime telemetry without inventing values."""
        result=data.get("result") or {}
        if not isinstance(result,dict): return {}
        usage=result.get("usage") or data.get("usage") or {}
        if not isinstance(usage,dict): usage={}
        value={}
        aliases={"prompt_tokens":("prompt_tokens","input_tokens"),"completion_tokens":("completion_tokens","output_tokens"),
                 "cached_tokens":("cached_tokens","cache_read_tokens"),"model_requests":("model_requests",),"tool_calls":("tool_calls",),
                 "finish_reason":("finish_reason",),"first_write_token":("first_write_token","first_file_write_token")}
        for target,names in aliases.items():
            found=next((result.get(name,usage.get(name)) for name in names if result.get(name,usage.get(name)) is not None),None)
            if found is not None: value[target]=found
        events=result.get("tool_events") or data.get("tool_events")
        if isinstance(events,list): value["tool_events"]=events
        return {"telemetry":value} if value else {}

    def call(self,agent,prompt):
        task=self.submit(agent,prompt)
        while True:
            result=self.poll(task["task_id"],agent)
            if result["status"]=="completed": return result.get("session_id") or task["session_id"]
            time.sleep(self.poll_interval)


def classify(text):
    lower=text.lower()
    if "failed to submit task" in lower and ("connection refused" in lower or
            "failed to submit task: [errno 1] operation not permitted" in lower):
        return "backend_unavailable"
    if any(value in lower for value in ("access denied", "permission denied by service", "invalid api key", "unauthorized", "forbidden")) or re.search(r'\b(401|403)\b', lower):
        return "service_access_denied"
    if 'task cancelled' in lower or 'task canceled' in lower: return 'task_cancelled'
    if 'incomplete chunked read' in lower or 'remoteprotocolerror' in lower: return 'model_stream_interrupted'
    if "413" in lower: return "gateway_413"
    if "429" in lower: return "gateway_429"
    if "timeout" in lower: return "timeout"
    if re.search(r'\b5\d\d\b',lower): return "gateway_5xx"
    return "empty_response" if not text.strip() else "backend_protocol_error"


class Orchestrator:
    def __init__(self,backend,sleep=time.sleep): self.backend=backend; self.sleep=sleep
    def progress(self,case):
        value=read_json(case/"state/progress.json")
        for session in value.get("sessions",[]): session.setdefault("status","completed")
        from _lib.runtime.bounded_recovery import validate_progress
        if not validate_progress(case,value): schema("progress.schema.json",value)
        return value
    def save_progress(self,case,value):
        from _lib.runtime.bounded_recovery import validate_progress
        if not validate_progress(case,value): schema("progress.schema.json",value)
        atomic_json(case/"state/progress.json",value)
    def fail(self,case,code,message):
        progress=self.progress(case); progress=transition(progress,"failed",code); progress["error"]["message"]=message; self.save_progress(case,progress)

    def before_model_attempt(self, case, key):
        """Version-specific budgets run only after durable output adoption."""
        from _lib.legacy.v2.batch_contract import enabled, require_attempt
        if enabled(case, key):
            require_attempt(case, key, self.progress(case))

    def stage_validation_feedback(self, case, key, error):
        from _lib.legacy.v2.batch_contract import feedback
        value = feedback(case, key, error)
        return validation_feedback(error) if value is None else value

    def stage_validation_details(self, case, key, error):
        from _lib.legacy.v2.batch_contract import failure_details
        return failure_details(case, key, error)

    def abandon_model_reservation(self, case, key, reason):
        """Version-specific budget owners may release a submission with no task ID."""

    def world_field_repair_prompt(self, case, key, candidate, archived, error):
        from _lib.world import admission, field_repair
        if key == 'world_closure':
            from _lib.runtime.reconciliation_review import ROOT as POINT_ROOT
            if candidate is not None and (case/POINT_ROOT).resolve() in Path(candidate).resolve().parents:
                return None
            from _lib.runtime.world_adjudication import ROOT
            if candidate is not None and (case/ROOT).resolve() in Path(candidate).resolve().parents:
                # Combined census has its own response contract and cannot be
                # repaired by the unrelated legacy World field-patch prompt.
                return None
            request = case/'state/world_closure.request.json'
            if request.is_file() and read_json(request).get('lane_layout_contract'):
                # Layout closure has its own bounded candidate repair and
                # conflict adjudication. The generic field-repair stop would
                # otherwise consume its one focused retry before submission.
                return None
        if (key == 'world_closure' and admission.enabled(case) and
                not isinstance(error,admission.AdmissionError) and not field_repair.prompt(case,key)):
            request = case/'state/world_closure.request.json'
            if request.is_file() and archived is not None:
                from _lib.world.closure import ClosureStopped
                code = self.candidate_error_code(error,candidate)
                if code in {'schema_validation_failed','invalid_json'}:
                    field_repair.record_closure_stop(case,request,
                        ClosureStopped('world_patch_contract_blocked',str(error)))
            return None
        if (key not in {'world', 'world_closure'} or not admission.enabled(case) or
                (key == 'world_closure' and not isinstance(error, admission.AdmissionError)
                 and not field_repair.prompt(case, key))):
            return None
        from _lib.world.compilation_repair import active as initial_repair_active
        if (key == 'world' and archived is None and initial_repair_active(case) and
                (error is None or isinstance(error, (BackendError, FileNotFoundError)))):
            # A transport failure supplies no new candidate. Keep the frozen
            # initial-answer packet; it is not a compiled World to re-admit.
            return field_repair.prompt(case, key)
        return field_repair.prepare(case, candidate, archived, error, key)

    def candidate_error_code(self, error, candidate_path=None):
        """Classify validation errors separately from model transport errors."""
        explicit = getattr(error, "code", None)
        if explicit:
            return explicit
        if isinstance(error, json.JSONDecodeError):
            return "invalid_json"
        if str(error) in BACKEND_CODES:
            return str(error)
        if isinstance(error, FileNotFoundError):
            if candidate_path is not None and error.filename and Path(error.filename).resolve() == Path(candidate_path).resolve():
                return "missing_output"
            return "local_dependency_missing"
        if isinstance(error, OSError):
            return "local_io_error"
        if isinstance(error, (ImportError, AttributeError, TypeError, KeyError, IndexError,
                              AssertionError, ArithmeticError, StopIteration)):
            return "internal_pipeline_error"
        return "schema_validation_failed"

    def background_model_stage(self,case,key,agent,prompt,validate_output,precheck=None,candidate_path=None,*,max_attempts=3):
        """Run or resume exactly one durable background task for a model stage."""
        from _lib.behavior import input_binding
        last="model_attempts_exhausted"
        prior = next((row for row in reversed(self.progress(case).get('sessions', []))
                      if row.get('stage') == key), None)
        if prior and prior.get('status') == 'invalid_output':
            prompt += self.stage_validation_feedback(case, key, prior)
        notice=ProgressNotice(case,key)
        while True:
            progress=self.progress(case); attempts=progress.setdefault("attempts",{}).get(key,0)
            unresolved = progress.get("diagnostics", {}).get("unresolved_submissions", {}).get(key)
            if unresolved:
                # Older versions classified a local socket refusal as unknown.
                # Reconcile only explicit pre-submission OS failures; a timeout
                # or missing receipt still may conceal an accepted remote task.
                if classify(unresolved['message']) == 'backend_unavailable' and '[task_id:' not in unresolved['message'].lower():
                    progress['diagnostics'].setdefault('submission_reconciliations', []).append({
                        'stage': key, 'resolution': 'not_submitted_local_os_rejection',
                        'original': unresolved, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
                    del progress['diagnostics']['unresolved_submissions'][key]
                    self.save_progress(case, progress)
                    self.abandon_model_reservation(case, key, 'backend_unavailable')
                else:
                    raise BackendError("submission_unresolved", unresolved["message"])
            expired=next((item for item in reversed(progress.get('sessions',[]))
                          if item.get('stage')==key and item.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                          and not item.get('deadline_task_resolved')),None)
            if expired is not None:
                # A local timeout does not cancel the remote producer. Resolve
                # its lifecycle before a retry can write to the same candidate.
                try: remote=self.backend.poll(expired['task_id'],agent)
                except Exception as error: raise RuntimeError('task_deadline_exceeded') from error
                if remote.get('status') not in {'completed','failed'}:
                    raise RuntimeError('task_deadline_exceeded')
                expired['deadline_task_resolved']=True
                self.save_progress(case,progress)
                if remote['status']=='completed':
                    if (getattr(self, '_core_transport_retry', False) and candidate_path is not None
                            and not Path(candidate_path).is_file()):
                        expired['terminal_output_missing']=True
                        self.save_progress(case,progress)
                    else:
                        validate_output(expired['session_id'])
                        expired['status']='completed'; self.save_progress(case,progress)
                        return
                if remote.get('error') in BACKEND_STOP_CODES: raise BackendError(remote['error'], remote.get('message') or remote['error'])
            active=next((item for item in reversed(progress.get("sessions",[])) if item.get("stage")==key and item.get("status") in {"submitted","pending","running"}),None)
            if active is not None:
                if 'deadline_unix_s' not in active:
                    active['deadline_unix_s']=time.time()+getattr(self.backend,'timeout',1800)
                    self.save_progress(case,progress)
                if time.time() >= active['deadline_unix_s']:
                    active.update({'status':'failed','error_code':'task_deadline_exceeded',
                                   'message':'Local persisted deadline exceeded; backend task may still be active. Do not submit a duplicate until it is stopped.'})
                    self.save_progress(case,progress)
                    raise RuntimeError('task_deadline_exceeded')
            if active is None:
                # A completed task can publish after the previous status poll. Adopt
                # that durable output before spending another model attempt.
                if precheck is not None:
                    try: precheck(); return
                    except Exception as error:
                        if not retryable(self.candidate_error_code(error, candidate_path)):
                            raise
                        archived=quarantine_candidate(case,key,candidate_path,attempts)
                        repair = self.world_field_repair_prompt(case,key,candidate_path,archived,error)
                        if repair: prompt = repair
                        else: prompt += self.stage_validation_feedback(case, key, error)
                        if key == 'intersection_review' and archived is not None:
                            from _lib.stages.intersection import repair_prompt
                            prompt=repair_prompt(case,candidate_path,archived,error) or prompt
                        if key == 'preview_review' and archived is not None:
                            from _lib.behavior.preview_repair import repair_prompt
                            prompt=repair_prompt(case,candidate_path,archived,error) or prompt
                if attempts>=max_attempts: raise RuntimeError("model_attempts_exhausted") from RuntimeError(last)
                self.before_model_attempt(case, key)
                if attempts: self.sleep((0,2,5)[min(attempts,2)])
                submission_try=0
                while True:
                    request = input_binding.task_request(case, key, candidate_path)
                    try:
                        submitted=self.submit_model_task(case,key,agent,prompt)
                        break
                    except Exception as submission_error:
                        submission_try+=1
                        raw=str(submission_error); code=backend_error_code(submission_error)
                        progress=self.progress(case)
                        progress.setdefault("diagnostics",{}).setdefault("submission_errors",[]).append({
                            "stage":key,"planned_attempt":attempts+1,"submission_try":submission_try,
                            "error_code":code,"message":raw[:500],
                            "at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                        })
                        self.save_progress(case,progress)
                        # Only an explicit rate-limit rejection proves non-acceptance.
                        # Timeout, empty/protocol/5xx responses can hide an accepted task.
                        if submission_not_sent(submission_error):
                            if code != "gateway_429" or submission_try >= 3:
                                self.abandon_model_reservation(case, key, code)
                                raise BackendError(code, raw) from submission_error
                        else:
                            progress.setdefault("diagnostics", {}).setdefault("unresolved_submissions", {})[key] = {
                                "code": code, "message": raw, "planned_attempt": attempts+1}
                            self.save_progress(case, progress)
                            raise BackendError("submission_unresolved", raw) from submission_error
                        self.sleep((2,5)[submission_try-1])
                attempts+=1; progress=self.progress(case); progress.setdefault("attempts",{})[key]=attempts
                epoch=progress.get("retry_epochs",{}).get(key,0)
                active={"stage":key,"attempt":attempts,"session_id":submitted["session_id"],"task_id":submitted["task_id"],"status":"submitted",
                        "deadline_unix_s":time.time()+getattr(self.backend,'timeout',1800)}
                if epoch: active["retry_epoch"]=epoch
                from _lib.world.compilation_repair import bind_task_receipt
                bind_task_receipt(progress, key, active)
                from _lib.runtime.world_adjudication import bind_continuation_receipt
                bind_continuation_receipt(case, progress, key, active)
                from _lib.runtime.census_task_accounting import bind_receipt
                bind_receipt(progress, key, active)
                from _lib.runtime.behavior_scope_review import bind_receipt as bind_scope_receipt
                bind_scope_receipt(progress, key, active)
                from _lib.runtime.bounded_recovery import bind_receipt as bind_bounded_receipt
                bind_bounded_receipt(progress, key, active)
                progress.setdefault("sessions",[]).append(active); self.save_progress(case,progress)
                input_binding.bind_session(case, active['session_id'], request)
            if input_binding.task_request(case, key, candidate_path) is not None:
                input_binding.require_session(case, active['session_id'])
            try:
                result=self.backend.poll(active["task_id"],agent)
            except Exception as error:
                progress=self.progress(case)
                current=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                if backend_error_code(error) in {"service_access_denied", "task_cancelled"}:
                    current.update(error_code=backend_error_code(error), message=str(error), status="failed")
                    self.save_progress(case,progress)
                    raise BackendError(backend_error_code(error), str(error)) from error
                count=current.get("consecutive_poll_errors",0)+1
                notice.emit('poll_error',task_id=active['task_id'],consecutive_errors=count)
                current.update({"consecutive_poll_errors":count,
                                "last_poll_error":str(error)[:500],
                                "last_poll_error_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
                self.save_progress(case,progress)
                if count < getattr(self.backend,"max_poll_errors",6):
                    self.sleep(getattr(self.backend,"poll_interval",5)); continue
                # A worker may have written its durable candidate before the
                # task-status route disappeared. Adopt it once before expiring
                # the lease, avoiding a duplicate model call when possible.
                try:
                    validate_output(current["session_id"])
                except Exception as validation_error:
                    progress=self.progress(case)
                    current=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                    current.update({"status":"failed","error_code":"task_status_unavailable",
                                    "message":f"polling unavailable after {count} attempts: {str(error)[:300]}; candidate: {str(validation_error)[:150]}"})
                    archived=quarantine_candidate(case,key,candidate_path,current.get("attempt",attempts))
                    if archived is not None: current["invalid_candidate_path"]=str(archived)
                    self.save_progress(case,progress)
                    raise BackendError("task_status_unavailable", current["message"]) from error
                progress=self.progress(case)
                current=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                current["status"]="completed"
                current["recovered_from_poll_failure"]=True
                self.save_progress(case,progress)
                return
            status=result["status"]
            notice.emit(status,task_id=active['task_id'])
            progress=self.progress(case)
            active=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
            active["status"]=status
            active["last_polled_at"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
            if "consecutive_poll_errors" in active:
                active["consecutive_poll_errors"]=0
            if result.get("telemetry"): active["telemetry"]=result["telemetry"]
            if candidate_path is not None and Path(candidate_path).is_file(): active["output_file_size_bytes"]=Path(candidate_path).stat().st_size
            self.save_progress(case,progress)
            if status in {"submitted","pending","running"}:
                self.sleep(getattr(self.backend,"poll_interval",5)); continue
            if status=="failed":
                candidate_error=None
                last=result.get("error") or "invalid_json"
                active["error_code"]=last; active["message"]=result.get("message") or last
                self.save_progress(case,progress)
                if last in BACKEND_STOP_CODES:
                    self.save_progress(case,progress)
                    raise BackendError(last, active['message'])
                # Runtime envelopes are not the artifact contract. A worker can
                # finish its file before its final response/connection fails.
                if candidate_path is not None and Path(candidate_path).is_file():
                    try:
                        validate_output(active["session_id"])
                    except Exception as error:
                        candidate_error=error
                        progress=self.progress(case)
                        active=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                        active["candidate_validation_error"]=str(error)[:4000]
                        validation_details = self.stage_validation_details(case, key, error)
                        if isinstance(validation_details,dict): active['validation_details']=validation_details
                        for field in ('rule_id','actor_id'):
                            if isinstance(getattr(error,field,None),str):
                                active[field]=getattr(error,field)
                        validation_code = self.candidate_error_code(error, candidate_path)
                        if not retryable(validation_code):
                            last=validation_code
                            active['error_code']=last
                            active['message']=str(error)
                        from _lib.legacy.v2.batch_contract import enabled
                        if enabled(case, key):
                            prompt += self.stage_validation_feedback(case, key, error)
                        else:
                            prompt += f"\n已落盘 candidate 的确定性校验错误：{str(error)[:4000]}。依据证据修正，禁止仅删除局限性绕过校验。"
                    else:
                        progress=self.progress(case)
                        active=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                        active.update({"status":"completed","recovered_from_task_failure":True})
                        self.save_progress(case,progress)
                        return
                archived=quarantine_candidate(case,key,candidate_path,active.get("attempt",attempts))
                if archived is not None: active["invalid_candidate_path"]=str(archived)
                self.save_progress(case,progress)
                if not retryable(last):
                    if key=='intersection_review' and candidate_error is not None and isinstance(getattr(candidate_error,'diagnostics',None),dict):raise candidate_error
                    raise BackendError(last, active["message"])
                repair = self.world_field_repair_prompt(case,key,candidate_path,archived,candidate_error)
                if repair:
                    prompt = repair
                    continue
                if key == 'intersection_review' and archived is not None and candidate_error is not None:
                    from _lib.stages.intersection import repair_prompt
                    prompt=repair_prompt(case,candidate_path,archived,candidate_error) or prompt
                if key == 'preview_review' and archived is not None and candidate_error is not None:
                    from _lib.behavior.preview_repair import repair_prompt
                    repair=repair_prompt(case,candidate_path,archived,candidate_error)
                    if repair:
                        prompt=repair
                        continue
                if key == 'behavior' and archived is not None and active.get('candidate_validation_error'):
                    from _lib.behavior.repair import repair_prompt
                    from _lib.contracts.v3 import ContractError
                    repair=repair_prompt(case,candidate_path,archived,candidate_error or ContractError(
                        active['candidate_validation_error'], rule_id=active.get('rule_id'),
                        actor_id=active.get('actor_id')))
                    if repair:
                        prompt=repair
                        continue
                prompt += f"\n上一次任务明确失败：{last}。仅修正该错误并重新写同一 candidate；不要解释。"
                continue
            try:
                validate_output(active["session_id"])
            except Exception as error:
                progress=self.progress(case)
                current=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
                valid,part_errors=inspect_analysis_parts(case/"final/.analysis_parts") if key=="aggregation" else ({},{})
                candidate_exists=Path(candidate_path).is_file() if candidate_path is not None else (case/"final/analysis.candidate.json").is_file()
                last=self.candidate_error_code(error, candidate_path)
                if last == "schema_validation_failed" and not candidate_exists and not part_errors:
                    last = "missing_output"
                details={"status":"invalid_output","error_code":last,"message":str(error)[:500],"candidate_exists":candidate_exists}
                validation_details = self.stage_validation_details(case, key, error)
                if isinstance(validation_details,dict): details['validation_details']=validation_details
                for field in ('rule_id','actor_id'):
                    if isinstance(getattr(error,field,None),str):
                        details[field]=getattr(error,field)
                archived=quarantine_candidate(case,key,candidate_path,current.get("attempt",attempts))
                if archived is not None: details["invalid_candidate_path"]=str(archived)
                if key=="aggregation":
                    details.update({"valid_parts":sorted(valid),"missing_parts":[name for name in PART_FIELDS if name not in valid]})
                current.update(details)
                self.save_progress(case,progress)
                if not retryable(last): raise
                repair = self.world_field_repair_prompt(case,key,candidate_path,archived,error)
                if repair:
                    prompt = repair
                    continue
                if key == 'intersection_review' and archived is not None:
                    from _lib.stages.intersection import repair_prompt
                    prompt=repair_prompt(case,candidate_path,archived,error) or prompt
                if key == 'preview_review' and archived is not None:
                    from _lib.behavior.preview_repair import repair_prompt
                    repair=repair_prompt(case,candidate_path,archived,error)
                    if repair:
                        prompt=repair
                        continue
                if key == 'behavior' and archived is not None:
                    from _lib.behavior.repair import repair_prompt
                    repair=repair_prompt(case,candidate_path,archived,error)
                    if repair:
                        prompt=repair
                        continue
                prompt += self.stage_validation_feedback(case, key, error)
                continue
            progress=self.progress(case)
            current=next(item for item in progress["sessions"] if item.get("task_id")==active["task_id"])
            current["status"]="completed"; self.save_progress(case,progress); return

    def submit_model_task(self,case,key,agent,prompt):
        return self.backend.submit(agent,prompt)

    def model_stage(self,case,key,agent,prompt,validate_output,precheck=None,candidate_path=None,*,max_attempts=None):
        from _lib.runtime.bounded_recovery import enabled as bounded_enabled, policy as bounded_policy
        if max_attempts is None:
            max_attempts = 1 + bounded_policy(case)['total_extra_tasks'] if bounded_enabled(case) else 3
        from _lib.behavior import input_binding
        binding = input_binding.task_request(case, key, candidate_path)
        if binding is not None:
            prompt += '\nFrozen Behavior input request: ' + str(input_binding.request_path(case, binding)) + '\nrequest_sha256=' + binding['request_sha256']
        from _lib.runtime.execution_policy import require_active_policy
        case_record = Path(case)/'case.json'
        if case_record.is_file():
            require_active_policy(read_json(case_record).get('parameters'))
        from _lib.world import admission, field_repair
        if key in {'world','world_closure'} and admission.enabled(case):
            layout_request = case/'state/world_closure.request.json'
            layout_closure = (key == 'world_closure' and layout_request.is_file()
                              and read_json(layout_request).get('lane_layout_contract'))
            from _lib.runtime.world_adjudication import ROOT as CENSUS_ROOT
            from _lib.runtime.reconciliation_review import ROOT as POINT_ROOT
            census_task = (key == 'world_closure' and candidate_path is not None
                           and Path(candidate_path).resolve().is_relative_to((Path(case)/CENSUS_ROOT).resolve()))
            point_task = (key == 'world_closure' and candidate_path is not None
                          and Path(candidate_path).resolve().is_relative_to((Path(case)/POINT_ROOT).resolve()))
            if not layout_closure and not census_task and not point_task:
                prompt = field_repair.prompt(case,key) or prompt
        active_repair=Path(case)/'state/preview_repair.active.json'
        intersection_repair=Path(case)/'state/intersection_repair.active.json'
        if key == 'intersection_review' and intersection_repair.exists() and read_json(intersection_repair).get('candidate_path')==str(candidate_path):
            prompt=read_json(intersection_repair)['prompt']
        if key == 'preview_review' and active_repair.exists():
            prompt=read_json(active_repair)['prompt']
        if hasattr(self.backend,"submit") and hasattr(self.backend,"poll"):
            with agent_task_lease(case,agent):
                return self.background_model_stage(case,key,agent,prompt,validate_output,precheck,candidate_path,max_attempts=max_attempts)
        progress=self.progress(case); attempts=progress.setdefault("attempts",{}).get(key,0)
        last="model_attempts_exhausted"
        while True:
            if precheck is not None:
                try: precheck(); return
                except Exception as error:
                    if not retryable(self.candidate_error_code(error, candidate_path)):
                        raise
                    archived=quarantine_candidate(case,key,candidate_path,attempts)
                    repair = self.world_field_repair_prompt(case,key,candidate_path,archived,error)
                    if repair: prompt = repair
                    else: prompt += self.stage_validation_feedback(case, key, error)
                    if key == 'intersection_review' and archived is not None:
                        from _lib.stages.intersection import repair_prompt
                        prompt=repair_prompt(case,candidate_path,archived,error) or prompt
                    if key == 'preview_review' and archived is not None:
                        from _lib.behavior.preview_repair import repair_prompt
                        prompt=repair_prompt(case,candidate_path,archived,error) or prompt
            if attempts >= max_attempts:
                raise RuntimeError("model_attempts_exhausted") from RuntimeError(last)
            self.before_model_attempt(case, key)
            progress = self.progress(case)
            attempts+=1; progress["attempts"][key]=attempts; self.save_progress(case,progress)
            session=None
            try:
                request = input_binding.task_request(case, key, candidate_path)
                session=self.backend.call(agent,prompt)
                progress=self.progress(case); record={"stage":key,"attempt":attempts,"session_id":session,"status":"returned"}
                epoch=progress.get("retry_epochs",{}).get(key,0)
                if epoch: record["retry_epoch"]=epoch
                from _lib.world.compilation_repair import bind_task_receipt
                bind_task_receipt(progress, key, record)
                from _lib.runtime.bounded_recovery import bind_receipt as bind_bounded_receipt
                bind_bounded_receipt(progress, key, record)
                progress.setdefault("sessions",[]).append(record); self.save_progress(case,progress)
                input_binding.bind_session(case, session, request)
                validate_output(session)
                progress=self.progress(case); progress["sessions"][-1]["status"]="completed"; self.save_progress(case,progress); return
            except Exception as error:
                if session:
                    progress=self.progress(case)
                    if progress.get("sessions") and progress["sessions"][-1].get("session_id")==session:
                        progress["sessions"][-1]["status"]="invalid_output"; self.save_progress(case,progress)
                if key == "conversion":
                    result_path=case/"state/conversion_result.json"
                    if result_path.is_file():
                        record=read_json(result_path)
                        if record.get("status")=="done":
                            validate_output(session)
                            return
                        if record.get("status")=="failed":
                            raise RuntimeError(record.get("error_code") or "conversion_failed") from error
                code=self.candidate_error_code(error,candidate_path) if session else (getattr(error,"code",None) or (str(error) if str(error) in ERRORS or str(error) in {"timeout","gateway_429","gateway_5xx","empty_response","invalid_json"} else "schema_validation_failed"))
                if session is None and submission_not_sent(error):
                    from _lib.world.compilation_repair import task_binding
                    if task_binding(case, key):
                        self.abandon_model_reservation(case, key, code)
                last=code
                if session and progress.get("sessions"):
                    progress["sessions"][-1].update({"error_code":code,"message":str(error)[:500]})
                    validation_details = self.stage_validation_details(case, key, error)
                    if isinstance(validation_details,dict): progress['sessions'][-1]['validation_details']=validation_details
                    for field in ('rule_id', 'actor_id'):
                        if isinstance(getattr(error, field, None), str):
                            progress['sessions'][-1][field] = getattr(error, field)
                    self.save_progress(case,progress)
                archived=quarantine_candidate(case,key,candidate_path,attempts)
                if archived is not None and session and progress.get("sessions"):
                    progress["sessions"][-1]["invalid_candidate_path"]=str(archived); self.save_progress(case,progress)
                if not retryable(code):
                    if key in {'world','world_closure'} and admission.enabled(case): raise
                    if key=='intersection_review' and isinstance(getattr(error,'diagnostics',None),dict): raise
                    if code == 'core_motion_semantic_recovery_required': raise
                    raise RuntimeError(code) from error
                if attempts<max_attempts:
                    repair=self.world_field_repair_prompt(case,key,candidate_path,archived,error)
                    if key == 'intersection_review' and archived is not None:
                        from _lib.stages.intersection import repair_prompt
                        repair=repair_prompt(case,candidate_path,archived,error)
                    if key == 'preview_review' and archived is not None:
                        from _lib.behavior.preview_repair import repair_prompt
                        repair=repair_prompt(case,candidate_path,archived,error)
                    if key == 'behavior' and archived is not None:
                        from _lib.behavior.repair import repair_prompt
                        repair=repair_prompt(case,candidate_path,archived,error)
                    if repair: prompt=repair
                    else: prompt += self.stage_validation_feedback(case, key, error)
                    self.sleep((0,2,5)[min(attempts,2)])
        raise RuntimeError("model_attempts_exhausted") from RuntimeError(last)

    def global_understanding(self,case):
        target=case/"state/global_context.json"; candidate=case/"state/global_context.candidate.json"
        duration=read_json(case/"state/probe.json")["probe"]["duration_s"]
        if target.exists(): validate_global_context(read_json(target),duration); return
        def check(_session):
            data=read_json(candidate)
            try: validate_global_context(data,duration)
            except Exception as cause:
                if duration<=SHORT_VIDEO_MAX_S and data.get("scene_type")=="intersection":
                    error=ShortCompileError(str(cause),field="global_context")
                    self._record_short_compile_failure(case,error); raise error from cause
                raise
            atomic_json(target,data); candidate.unlink(missing_ok=True)
        if candidate.exists():
            try: check(None); return
            except ShortCompileError: raise
            except Exception: pass
        prompt=global_request_text(case)
        self.model_stage(case,"global", "global-video-understanding",prompt,check,lambda:check(None),candidate_path=candidate)

    def adopt_short_video_analysis(self,case):
        """Adopt a candidate or deterministically compile short visual facts."""
        candidate=case/"final/analysis.candidate.json"
        if not (case/"state/global_frames.json").is_file() or not (case/"state/probe.json").is_file(): return FastPathOutcome.NOT_APPLICABLE
        global_frames=read_json(case/"state/global_frames.json").get("frames",[])
        duration=read_json(case/"state/probe.json").get("probe",{}).get("duration_s",999)
        if len(global_frames)>12 or duration>SHORT_VIDEO_MAX_S: return FastPathOutcome.NOT_APPLICABLE
        context=read_json(case/"state/global_context.json"); registry=[]
        for actor in context.get("actor_hypotheses",[]):
            required=("actor_id","actor_type","first_seen_frame_id","identity_description")
            if not all(actor.get(key) for key in required):
                error=ShortCompileError("actor identity fields are incomplete",actor_id=actor.get("actor_id"),field="actor_hypotheses")
                self._record_short_compile_failure(case,error); raise error
            if context.get("scene_type")=="intersection" and actor.get("actor_type") not in {"vehicle","pedestrian","vehicle_group"}:
                error=ShortCompileError("unsupported actor type",actor_id=actor.get("actor_id"),field="actor_type")
                self._record_short_compile_failure(case,error); raise error
            registry.append(dict(actor))
        if not candidate.is_file() and (context.get("scene_type")!="intersection" or not context.get("scene_facts")):
            return FastPathOutcome.NOT_APPLICABLE
        try:
            if candidate.is_file():
                if candidate.stat().st_size > 32 * 1024: raise ValueError("short analysis exceeds 32768 bytes")
                analysis=read_json(candidate)
            else:
                analysis=compile_short_intersection_analysis(case,context); atomic_json(candidate,analysis)
            validate_analysis(analysis,registry)
        except ShortCompileError as error:
            candidate.unlink(missing_ok=True)
            self._record_short_compile_failure(case,error); raise
        except Exception as cause:
            candidate.unlink(missing_ok=True)
            error=ShortCompileError(str(cause),field="analysis")
            self._record_short_compile_failure(case,error); raise error from cause
        atomic_json(case/"state/actor_registry.json",registry); schema("actor_registry.schema.json",registry)
        progress=self.progress(case); progress["execution_mode"]="short_video_global"; self.save_progress(case,progress)
        return FastPathOutcome.COMPILED

    def _record_short_compile_failure(self,case,error):
        progress=self.progress(case)
        progress["execution_mode"]="short_video_fast"
        diagnostics=progress.setdefault("diagnostics",{})
        diagnostics["short_compile"]=error.diagnostics()
        self.save_progress(case,progress)

    def batches(self,case):
        manifest=read_json(case/"manifest.json"); schema("manifest.schema.json",manifest); context=read_json(case/"state/global_context.json")
        if len(manifest["batches"])>4 or sum(len(x["frames"]) for x in manifest["batches"])>16: raise RuntimeError("unsupported_v1_limit")
        outcome=self.adopt_short_video_analysis(case)
        if outcome == FastPathOutcome.COMPILED: return
        previous=[]
        for batch in manifest["batches"]:
            batch_id=batch["batch_id"]; target=case/"batches"/f"{batch_id}.json"; candidate=case/"batches"/f".{batch_id}.candidate.json"
            def check(session=None,target=target,candidate=candidate,previous=previous):
                source=candidate if candidate.is_file() else target
                data=read_json(source)
                if source==candidate:
                    if not session:
                        history=[item for item in self.progress(case).get("sessions",[]) if item.get("stage")==batch_id]
                        session=history[-1]["session_id"] if history else None
                    if session: data["session_id"]=session
                if data.get("case_id")!=case.name or data.get("batch_id")!=batch_id: raise ValueError("batch identity mismatch")
                schema("batch.schema.json",data); validate_evidence_items(data["observations"]); refs=[]
                for item in data.get("observations",[]): refs.extend(item.get("actor_ids",[]))
                validate_registry(previous,data["registry_after"],refs); validate_evidence(data)
                if source==candidate: atomic_json(target,data); candidate.unlink(missing_ok=True)
                return data
            def check_candidate(session=None,candidate=candidate,target=target):
                if not candidate.is_file():
                    # A model worker never owns the authoritative checkpoint. Do not
                    # let an out-of-contract direct write become resumable state.
                    target.unlink(missing_ok=True)
                    raise OutputValidationError("missing_output",f"missing {candidate}")
                return check(session)
            if target.exists():
                try: data=check()
                except Exception as error:
                    progress=self.progress(case); progress.setdefault("checkpoint_errors",[]).append({"batch_id":batch_id,"message":str(error),"at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
                    progress["completed_batches"]=[value for value in progress["completed_batches"] if value!=batch_id]; self.save_progress(case,progress)
                    candidate.unlink(missing_ok=True); data=None
            else: data=None
            if data is None:
                prompt=batch_request_text(case,batch_id)
                self.model_stage(case,batch_id,"video_extractor",prompt,check_candidate,lambda:check_candidate(None),candidate_path=candidate); data=check()
            previous=data["registry_after"]
            progress=self.progress(case)
            if batch_id not in progress["completed_batches"]: progress["completed_batches"].append(batch_id); progress["completed_batches"].sort(); self.save_progress(case,progress)
        atomic_json(case/"state/actor_registry.json",previous)
        schema("actor_registry.schema.json",previous)

    def aggregate(self,case):
        target=case/"final/analysis.json"; candidate=case/"final/analysis.candidate.json"
        if aggregate_request_size(case)>BUDGET: raise RuntimeError("packing_budget_exceeded")
        registry=read_json(case/"state/actor_registry.json")
        fast_mode=((case/"state/progress.json").is_file() and
                   self.progress(case).get("execution_mode") in {"short_video_fast","short_video_global"})
        if target.exists(): validate_analysis(read_json(target),registry); return
        def adopt_monolith(_session=None):
            data=read_json(candidate); validate_analysis(data,registry); atomic_json(target,data); candidate.unlink(missing_ok=True)
        if candidate.exists():
            try: adopt_monolith(); return
            except Exception as cause:
                if fast_mode:
                    error=ShortCompileError(str(cause),field="analysis")
                    self._record_short_compile_failure(case,error); raise error from cause
        if fast_mode:
            error=ShortCompileError("short-video fast path cannot enter model aggregation",field="aggregation")
            self._record_short_compile_failure(case,error); raise error
        parts=case/"final/.analysis_parts"; parts.mkdir(parents=True,exist_ok=True)
        feedback=None; gate_failures=0
        while True:
            valid,_errors=inspect_analysis_parts(parts)
            missing=[name for name in PART_FIELDS if name not in valid]
            if not missing:
                data=assemble_analysis_parts(parts)
                try: validate_analysis(data,registry)
                except Exception as error:
                    gate_failures+=1
                    if gate_failures>=3: raise
                    expected=part_for_analysis_error(error); (parts/f"{expected}.json").unlink(missing_ok=True); feedback=str(error)
                    progress=self.progress(case); progress.setdefault("checkpoint_errors",[]).append({"stage":"aggregation","error_code":"schema_validation_failed","part":expected,"message":feedback[:500],"at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}); self.save_progress(case,progress)
                    continue
                atomic_json(candidate,data); adopt_monolith()
                for path in parts.glob("*.json"): path.unlink(missing_ok=True)
                try: parts.rmdir()
                except OSError: pass
                return
            expected=missing[0]
            def check_part(_session=None,expected=expected):
                current,errors=inspect_analysis_parts(parts)
                if expected not in current:
                    if expected in errors: raise OutputValidationError(errors[expected]["code"],errors[expected]["message"])
                    raise OutputValidationError("missing_output",f"missing {expected}.json")
            prompt=aggregate_prompt(case,expected,sorted(valid),feedback); feedback=None
            self.model_stage(case,"aggregation","global-video-understanding",prompt,check_part,lambda:check_part(None),candidate_path=parts/f"{expected}.json")
            progress=self.progress(case); progress.setdefault("attempts",{})["aggregation"]=0; self.save_progress(case,progress)

    def convert(self,case):
        analysis=case/"final/analysis.json"; scene=read_json(analysis)["scene_type"]; target=case/"final/target.json"; result=case/"state/conversion_result.json"
        try: self.validate_conversion(case); return
        except Exception: pass
        if result.is_file() and read_json(result).get("status")!="done":
            result.unlink()
        converter=ROOT/"json-coverter"/"skills"/"case-conversion"/"scripts"/"process.py"
        completed=subprocess.run(
            [sys.executable,str(converter),"--input",str(analysis),"--output",str(target),"--scene-type",scene,"--result",str(result)],
            capture_output=True,text=True,
        )
        if completed.returncode:
            raise RuntimeError("conversion_failed")
        self.validate_conversion(case)

    def validate_conversion(self,case):
        analysis=case/"final/analysis.json"; analysis_data=read_json(analysis); scene=analysis_data["scene_type"]
        target=case/"final/target.json"; result=case/"state/conversion_result.json"
        if not target.is_file() or not result.is_file(): raise RuntimeError("conversion_failed")
        record=read_json(result)
        script=ROOT/"json-coverter"/"skills"/"case-conversion"/"scripts"/"scenes"/scene/"convert.py"
        if record.get("status")!="done" or record.get("scene_type")!=scene or record.get("input_sha256")!=sha256(analysis) or record.get("converter_sha256")!=converter_fingerprint(script) or record.get("target_sha256")!=sha256(target) or not mela_valid(target):
            raise RuntimeError("conversion_failed")
        try: validate_target_semantics(analysis_data,read_json(target))
        except (KeyError,TypeError,ValueError) as error: raise RuntimeError("conversion_failed") from error

    def finalize(self,case):
        analysis=case/"final/analysis.json"
        report_md,report_docx=case/"final/report.md",case/"final/report.docx"
        if subprocess.run([sys.executable,str(HERE/'case-delivery/scripts/report.py'),"--input",str(analysis),"--markdown",str(report_md),"--docx",str(report_docx)]).returncode: raise RuntimeError("publish_failed")
        artifacts=publish(case,EXTRACTOR/"media/output"); progress=self.progress(case); progress=transition(progress,"done"); progress["artifacts"]=artifacts; self.save_progress(case,progress)

    def verify_done(self,case):
        progress=self.progress(case)
        if progress["phase"]!="done": return False
        binding=read_json(case/"state/binding.json")
        if sha256(binding["source_path"])!=binding["source_sha256"]: return False
        required={"analysis.json","report.md","report.docx","target.json"}
        if set(progress["artifacts"])!=required: return False
        if not all(Path(info["path"]).is_file() and sha256(info["path"])==info["sha256"] for info in progress["artifacts"].values()): return False
        try: self.validate_conversion(case)
        except Exception: return False
        return True

    def recover_failed(self,case):
        """Return a failed Case to its last durable checkpoint, never skipping work."""
        progress=self.progress(case)
        if progress.get("phase")!="failed": return progress
        manifest_path=case/"manifest.json"; analysis=case/"final/analysis.json"
        analysis_valid=False
        if analysis.exists():
            try: validate_analysis(read_json(analysis),read_json(case/"state/actor_registry.json")); analysis_valid=True
            except Exception:
                analysis.unlink(missing_ok=True)
                for path in (case/"final/target.json",case/"state/conversion_result.json",case/"final/report.md",case/"final/report.docx"): path.unlink(missing_ok=True)
        batches_valid=False
        if manifest_path.exists() and (case/"state/global_context.json").exists() and (case/"state/actor_registry.json").exists():
            try:
                manifest=read_json(manifest_path); schema("manifest.schema.json",manifest)
                schema("actor_registry.schema.json",read_json(case/"state/actor_registry.json"))
                for batch in manifest["batches"]:
                    data=read_json(case/"batches"/f'{batch["batch_id"]}.json'); schema("batch.schema.json",data)
                    if data["case_id"]!=case.name or data["batch_id"]!=batch["batch_id"]: raise ValueError("batch identity mismatch")
                batches_valid=True
            except Exception: batches_valid=False
        if analysis_valid: phase="converting"; stage="conversion"
        elif batches_valid: phase="aggregating"; stage="aggregation"
        elif manifest_path.exists() and (case/"state/global_context.json").exists(): phase="batching"; stage="batching"
        else: phase="planning"; stage="global"
        progress.setdefault("retry_epochs",{})[stage]=progress.setdefault("retry_epochs",{}).get(stage,0)+1
        if stage=="batching":
            for key in list(progress.setdefault("attempts",{})):
                if key.startswith("batch_") and key not in progress.get("completed_batches",[]):
                    from _lib.legacy.v2.batch_contract import enabled
                    if enabled(case, key):
                        progress['attempts'][key] = max(progress['attempts'][key],
                            sum(row.get('stage') == key for row in progress.get('sessions', [])))
                    else:
                        progress["attempts"].pop(key,None)
        else:
            progress.setdefault("attempts",{}).pop(stage,None)
        progress.update({"phase":phase,"status":phase,"error":None,"updated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
        self.save_progress(case,progress); return progress

    def validate_case_files(self,case):
        schema("case.schema.json",read_json(case/"case.json")); schema("binding.schema.json",read_json(case/"state/binding.json"))

    def validate_manifest(self,case):
        manifest=read_json(case/"manifest.json"); schema("manifest.schema.json",manifest)
        binding=read_json(case/"state/binding.json")
        expected=self.progress(case).get("manifest_sha256")
        if not expected or sha256(case/"manifest.json")!=expected: raise RuntimeError("schema_validation_failed")
        if manifest["source_sha256"]!=binding["source_sha256"]: raise RuntimeError("source_changed")
        for frame in manifest["global_frames"]+[frame for batch in manifest["batches"] for frame in batch["frames"]]:
            path=Path(frame["path"])
            if not path.is_file() or sha256(path)!=frame["sha256"]: raise RuntimeError("schema_validation_failed")
        return manifest

    def advance(self,case,phase):
        progress=self.progress(case)
        if progress["phase"]!=phase: self.save_progress(case,transition(progress,phase))


    def run(self,video,force_new=False,case_id=None,parameters=None,retry_failed=False):
        from _lib.runtime.execution_policy import require_active_policy
        require_active_policy(parameters)
        # This class is the explicit legacy implementation. The public router
        # defaults new requests to v3, while direct legacy use stays v2-bound.
        parameters={"workflow_version":"v2",**(parameters or {})}
        if parameters["workflow_version"]!="v2": raise ValueError("legacy Orchestrator requires workflow_version=v2")
        if case_id:
            case=(EXTRACTOR/"cases"/case_id).resolve()
            if case.parent!=(EXTRACTOR/"cases").resolve() or not (case/"case.json").is_file(): raise ValueError("unknown case_id")
            require_active_policy(read_json(case/"case.json").get('parameters'))
            binding=read_json(case/"state/binding.json")
            if Path(video).resolve()!=Path(binding["source_path"]).resolve() or sha256(video)!=binding["source_sha256"]:
                self.fail(case,"source_changed","source path or SHA-256 changed"); raise RuntimeError("source_changed")
        else:
            case,_=Cases(EXTRACTOR/"cases").bind(Path(video),parameters=parameters,force_new=force_new)
        if self.verify_done(case): return self.progress(case)
        progress=self.progress(case)
        if progress.get("phase")=="done":
            # A completed Case may become stale after deterministic converter
            # code changes or published artifact loss. Reopen only the local
            # conversion/finalization stage; never rerun video model sessions.
            if not (case/"final/analysis.json").is_file():
                raise RuntimeError("schema_validation_failed")
            progress.update({"phase":"converting","status":"converting","error":None,"updated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
            self.save_progress(case,progress)
        if self.progress(case).get("phase")=="failed":
            if not retry_failed: raise RuntimeError("retry_required")
            self.recover_failed(case)
        self.validate_case_files(case)
        try:
            phase=self.progress(case)["phase"]
            if phase=="planning":
                subprocess.run([sys.executable,str(HERE/'video-case-orchestrator/scripts/case_tools.py'),"prepare","--case-dir",str(case)],check=True)
                self.global_understanding(case); manifest=freeze(case); schema("manifest.schema.json",manifest)
                phase="batching"
            if phase=="batching":
                self.validate_manifest(case)
                self.batches(case); self.advance(case,"aggregating"); phase="aggregating"
            if phase=="aggregating":
                self.validate_manifest(case)
                self.aggregate(case); self.advance(case,"converting"); phase="converting"
            if phase=="converting":
                # Converter output is a stage checkpoint, never the terminal
                # response. Keep control here through report, publish and done.
                self.convert(case)
                self.validate_conversion(case)
                self.finalize(case)
                if not self.verify_done(case): raise RuntimeError("publish_failed")
            return self.progress(case)
        except Exception as error:
            code=getattr(error,"code",None) or (str(error) if str(error) in ERRORS else "schema_validation_failed")
            message=str(error)
            if code=="conversion_failed":
                result_path=case/"state/conversion_result.json"
                if result_path.is_file(): message=read_json(result_path).get("message") or message
            self.fail(case,code,message)
            error.case_id = case.name
            raise


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--video"); parser.add_argument("--case-id"); parser.add_argument("--new",action="store_true"); parser.add_argument("--retry-failed",action="store_true"); parser.add_argument("--authorize-transport-retry"); parser.add_argument("--enable-straight-preview",action="store_true"); parser.add_argument("--parameters-json",default="{}"); parser.add_argument("--json",action="store_true"); parser.add_argument('--execution-scope', choices=['ego_vehicle_scene.v1', 'ego_vehicle_scene.v2', 'ego_vehicle_scene.v3', 'ego_vehicle_scene.v4']); parser.add_argument('--execution-scope-bindings'); parser.add_argument('--execution-planning',choices=['ego_scene_approximation.v1']); parser.add_argument('--execution-evidence'); parser.add_argument('--scene-corrections')
    parser.add_argument('--enable-conversion-defaults', action='store_true')
    parser.add_argument('--enable-behavior-census-scope', action='store_true')
    parser.add_argument('--review-behavior-scope', action='store_true')
    parser.add_argument('--review-execution-scope', action='store_true')
    parser.add_argument('--core-scene-actors', nargs='+', metavar='ACTOR_ID')
    parser.add_argument('--core-scene-approximate', action='store_true')
    parser.add_argument('--strict-delivery', action='store_true')
    parser.add_argument('--description-fallback', choices=('auto', 'prepare', 'resume', 'check', 'off'))
    parser.add_argument('--description-root', help='Independent output directory for explicit description fallback')
    parser.add_argument('--accept-early-association-independence', action='store_true')
    parser.add_argument('--reauthorize-behavior-after-timeout', metavar='FAILED_TASK_ID')
    parser.add_argument('--reauthorize-behavior-scope-after-quota', metavar='FAILED_TASK_ID')
    parser.add_argument('--scope-quota-retry-generation', type=int, choices=(1, 2), default=1)
    parser.add_argument('--resume-conversion-session')
    parser.add_argument('--resume-world-closure-session')
    args=parser.parse_args()
    if args.description_root and args.description_fallback not in {'prepare', 'resume', 'check'}:
        parser.error('--description-root requires --description-fallback prepare/resume/check')
    # Explicit archive operations never enter A or instantiate a video worker.
    if args.description_fallback in {'prepare', 'resume', 'check'}:
        if not args.case_id or args.new or args.retry_failed:
            parser.error('description fallback requires --case-id without --new/--retry-failed')
        from _lib.runtime.description_fallback import run as run_description
        try:
            result = run_description(args.description_fallback, args.case_id, root=args.description_root)
        except Exception as error:
            result = {'case_id': args.case_id, 'status': 'blocked', 'delivery_level': 'unverified',
                      'generation_status': 'not_generated', 'target_path': None,
                      'error': {'code': getattr(error, 'code', type(error).__name__), 'message': str(error)}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not args.video:
        parser.error('--video is required for video processing')
    try: parameters=json.loads(args.parameters_json)
    except json.JSONDecodeError as error: parser.error(f"--parameters-json is not valid JSON: {error.msg}")
    if not isinstance(parameters,dict): parser.error("--parameters-json must decode to an object")
    if args.strict_delivery and args.core_scene_approximate:
        parser.error('--strict-delivery cannot combine with --core-scene-approximate')
    saved_approximate = False
    if args.case_id:
        cases=EXTRACTOR/'cases'
        case=(cases/args.case_id).resolve()
        saved_approximate=(case.parent==cases.resolve() and
                           (case/'state/core_approximate_mode.json').is_file())
        if saved_approximate and args.strict_delivery:
            parser.error('A core approximation Case cannot resume as strict delivery')
        args.core_scene_approximate = args.core_scene_approximate or saved_approximate
    elif args.new and parameters.get('workflow_version','v3')=='v3' and not args.strict_delivery:
        args.core_scene_approximate=True
    if args.scope_quota_retry_generation != 1 and not args.reauthorize_behavior_scope_after_quota:
        parser.error('--scope-quota-retry-generation 2 requires --reauthorize-behavior-scope-after-quota')
    if args.retry_failed and not args.case_id: parser.error("--retry-failed requires --case-id")
    if args.core_scene_approximate and (
            args.new == bool(args.case_id) or (args.retry_failed and not saved_approximate) or args.core_scene_actors
            or args.review_execution_scope or args.review_behavior_scope
            or args.reauthorize_behavior_scope_after_quota
            or args.accept_early_association_independence or args.execution_scope
            or args.execution_scope_bindings or args.execution_planning or args.execution_evidence
            or args.enable_straight_preview or args.enable_conversion_defaults
            or args.enable_behavior_census_scope or args.authorize_transport_retry
            or args.resume_conversion_session or args.resume_world_closure_session
            or args.scene_corrections):
        parser.error('--core-scene-approximate requires --new or --case-id without other modes')
    if args.retry_failed and args.new: parser.error("--retry-failed cannot be combined with --new")
    if args.authorize_transport_retry and not (args.case_id and args.retry_failed):
        parser.error("--authorize-transport-retry requires --case-id --retry-failed")
    if args.resume_conversion_session and args.authorize_transport_retry != 'conversion_semantic_gap':
        parser.error('--resume-conversion-session requires --authorize-transport-retry conversion_semantic_gap')
    if args.resume_world_closure_session and (not args.case_id or not args.retry_failed or args.new
            or args.resume_conversion_session or args.authorize_transport_retry or args.scene_corrections
            or args.enable_straight_preview or args.enable_conversion_defaults or args.enable_behavior_census_scope or args.execution_scope or args.execution_planning):
        parser.error('--resume-world-closure-session requires saved v3 retry without other recovery/migration flags')
    try:
        parameters=resolve_workflow_parameters(parameters,args.case_id)
        if args.core_scene_approximate and parameters['workflow_version'] != 'v3':
            parser.error('--core-scene-approximate requires v3')
        if args.reauthorize_behavior_after_timeout and (parameters['workflow_version'] != 'v3'
                or args.scene_corrections):
            parser.error('--reauthorize-behavior-after-timeout requires ordinary saved v3 retry')
        if args.accept_early_association_independence and (parameters['workflow_version'] != 'v3'
                or args.scene_corrections):
            parser.error('--accept-early-association-independence requires ordinary saved v3 retry')
        if args.review_execution_scope and (parameters['workflow_version'] != 'v3'
                or not args.case_id or not args.retry_failed or args.new or args.scene_corrections
               ):
            parser.error('--review-execution-scope requires ordinary saved v3 retry')
        if args.core_scene_actors and (parameters['workflow_version'] != 'v3'
                or not args.case_id or not args.retry_failed or args.new or args.scene_corrections
               ):
            parser.error('--core-scene-actors requires ordinary saved v3 retry')
        if (args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota) and (parameters['workflow_version'] != 'v3'
                or args.scene_corrections):
            parser.error('--review-behavior-scope requires ordinary saved v3 retry')
        if args.enable_behavior_census_scope and (not args.case_id or not args.retry_failed or args.new
                or parameters['workflow_version'] != 'v3' or args.scene_corrections
                or args.resume_world_closure_session or args.resume_conversion_session or args.authorize_transport_retry
                or args.enable_conversion_defaults or args.enable_straight_preview or args.execution_scope or args.execution_planning):
            parser.error('--enable-behavior-census-scope requires ordinary saved v3 retry')
        if args.resume_world_closure_session and parameters['workflow_version'] != 'v3':
            parser.error('--resume-world-closure-session requires v3')
        if args.resume_conversion_session and (parameters['workflow_version'] != 'v3' or args.scene_corrections):
            parser.error('--resume-conversion-session requires ordinary v3 recovery')
        if args.enable_straight_preview and not (args.case_id and args.retry_failed and parameters['workflow_version']=='v3'):
            parser.error('--enable-straight-preview requires --case-id --retry-failed and workflow v3')
        if args.enable_conversion_defaults and not (args.case_id and args.retry_failed and parameters['workflow_version']=='v3'):
            parser.error('--enable-conversion-defaults requires --case-id --retry-failed and workflow v3')
        if args.enable_conversion_defaults and (args.scene_corrections or args.execution_scope or args.execution_planning or args.enable_straight_preview):
            parser.error('--enable-conversion-defaults cannot combine other policy migrations')
        if args.execution_evidence and not args.execution_planning: parser.error('--execution-evidence requires --execution-planning')
        if args.execution_planning and (parameters['workflow_version']!='v3' or (args.case_id and not args.retry_failed)):
            parser.error('--execution-planning requires v3 and explicit retry for saved cases')
        if args.execution_scope_bindings and not args.execution_scope:
            parser.error('--execution-scope-bindings requires --execution-scope')
        if args.execution_scope and (parameters['workflow_version'] != 'v3' or (args.case_id and not args.retry_failed)):
            parser.error('--execution-scope requires v3 and --retry-failed for a saved Case')
        if args.scene_corrections:
            if not args.case_id or args.new or parameters["workflow_version"]!="v3" or args.execution_scope or args.execution_planning:
                parser.error("--scene-corrections requires a completed v3 Case without migration flags")
            from _lib.runtime.scene_revision import run as run_revision
            result=run_revision(args.video,args.case_id,args.scene_corrections,parameters)
        elif args.core_scene_approximate:
            from _lib.runtime.core_approximate_delivery import CoreApproximateOrchestrator, run as run_core_approximate
            result=run_core_approximate(CoreApproximateOrchestrator(QwenPawBackend()), args.video,
                                        parameters, new=args.new, case_id=args.case_id,
                                        retry_failed=args.retry_failed,
                                        reauthorize_behavior_after_timeout=args.reauthorize_behavior_after_timeout)
        elif parameters["workflow_version"] == "v3":
            from _lib.runtime.v3 import V3Orchestrator
            result=V3Orchestrator(QwenPawBackend()).run_v3(args.video,args.new,args.case_id,parameters,args.retry_failed,
                                                       enable_straight_preview=args.enable_straight_preview, enable_conversion_defaults=args.enable_conversion_defaults, execution_scope=args.execution_scope,
                                                       enable_behavior_census_scope=args.enable_behavior_census_scope,
                                                       execution_scope_bindings=args.execution_scope_bindings, execution_planning=args.execution_planning, execution_evidence=args.execution_evidence,
                                                       authorize_transport_retry=args.authorize_transport_retry,
                                                       resume_conversion_session=args.resume_conversion_session,
                                                       resume_world_closure_session=args.resume_world_closure_session,
                                                       review_execution_scope=args.review_execution_scope,
                                                       core_scene_actors=args.core_scene_actors,
                                                       review_behavior_scope=args.review_behavior_scope,
                                                       accept_early_association_independence=args.accept_early_association_independence,
                                                       reauthorize_behavior_after_timeout=args.reauthorize_behavior_after_timeout,
                                                       reauthorize_behavior_scope_after_quota=args.reauthorize_behavior_scope_after_quota,
                                                       scope_quota_retry_generation=args.scope_quota_retry_generation)
        else:
            result=Orchestrator(QwenPawBackend()).run(args.video,args.new,args.case_id,parameters,args.retry_failed)
    except Exception as error:
        message=str(error)
        code=getattr(error,"code",None) or (message if message in ERRORS else "internal_pipeline_error")
        result={"case_id":getattr(error, 'case_id', args.case_id),"status":"failed","generation_status":"not_generated",
                "verification_level":"not_verified","source_contract_validation":"not_passed",
                "render_verification":"not_executed","artifacts":{},
                "error":{"code":code,"message":message,"exception_type":type(error).__name__}}
    from _lib.delivery.synthetic_target import public_result
    result = public_result(result)
    description_mode = args.description_fallback or ('auto' if args.new or args.core_scene_approximate else 'off')
    if (((result.get('error') or {}).get('rule_id') == 'core_scene.motion_continuity'
         or (result.get('error') or {}).get('code') == 'core_motion_semantic_recovery_required')
            and args.description_fallback is None):
        description_mode = 'auto'
    if args.description_fallback is None and result.get('case_id'):
        from _lib.legacy.v2.minimum_fallback import eligible as v2_minimum_eligible
        if v2_minimum_eligible(result):
            description_mode = 'auto'
    if description_mode == 'auto' and result.get('case_id'):
        from _lib.runtime.description_fallback import eligible_failure, run as run_description
        if eligible_failure(result):
            original_result = result
            try:
                result = run_description('resume', original_result['case_id'])
                result['path_a_result'] = original_result
                if (result.get('generation_status') == 'not_generated'
                        and original_result.get('target_path')):
                    result = dict(original_result, description_fallback_result=result)
            except Exception as error:
                result = dict(original_result, description_fallback_error={
                    'code': getattr(error, 'code', type(error).__name__), 'message': str(error)})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
