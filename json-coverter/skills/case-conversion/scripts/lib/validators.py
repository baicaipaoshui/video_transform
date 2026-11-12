"""
校验函数库 -- pydantic 四部分校验 + 下游 OpenX middleware 校验。所有场景共用。

用法：
    from validators import validate_data, check_downstream_road_network
    result = validate_data(target_json)          # pydantic 四部分
    errors = check_downstream_road_network(target_json)  # 下游硬约束
"""
import os
import sys
import json

# 自注入 lib/ 到 sys.path（确保可独立运行也可被 import）
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
import path_setup  # noqa: 确保 mela sys.path 已设置

from pydantic import ValidationError  # noqa: E402

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (  # noqa: E402
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.scene import Scene  # noqa: E402
from mela_business_logic.scenario_preview.pydantic_classes.entities.entities import (  # noqa: E402
    Entities,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.scenario import (  # noqa: E402
    Scenario,
)
from mela_business_logic.scenario_preview.pydantic_classes.environment.environment import (  # noqa: E402
    Environment,
)
from mela_business_logic.error import ModelValidationError  # noqa: E402


# ---- 内部工具 ----

def _rename_id_to_uid(obj):
    """递归将 JSON 中 "id" 键重命名为 "UID"（匹配 pydantic alias）。"""
    if isinstance(obj, dict):
        return {
            ("UID" if k == "id" else k): _rename_id_to_uid(v)
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [_rename_id_to_uid(item) for item in obj]
    return obj


def _clear_registry():
    """清空 BaseClass._registry，避免重复 ID 报错。"""
    BaseClass._registry.clear()


_SECTION_MODELS = {
    "scene": Scene,
    "entities": Entities,
    "scenario": Scenario,
    "environment": Environment,
}


# ---- pydantic 四部分校验 ----

def validate_data(data):
    """
    对 target JSON 的四部分分别执行 pydantic model_validate。

    返回 {"valid": bool, "sections": {section: {"valid": bool, ...}}}
    """
    data = _rename_id_to_uid(data)
    results = {}
    all_valid = True
    for section_name, model_cls in _SECTION_MODELS.items():
        section_data = data.get(section_name)
        if section_data is None:
            results[section_name] = {"valid": False,
                                     "errors": ["missing section"]}
            all_valid = False
            continue
        _clear_registry()
        try:
            instance = model_cls.model_validate(section_data)
            results[section_name] = {"valid": True, "id": instance.id}
        except ValidationError as e:
            all_valid = False
            results[section_name] = {
                "valid": False,
                "errors": [
                    {"loc": " -> ".join(str(x) for x in err.get("loc", [])),
                     "msg": err.get("msg", ""),
                     "type": err.get("type", "")}
                    for err in e.errors()
                ],
            }
        except ModelValidationError as e:
            all_valid = False
            results[section_name] = {"valid": False,
                                     "errors": [str(e)]}
        except Exception as e:
            all_valid = False
            results[section_name] = {"valid": False,
                                     "errors": [f"{type(e).__name__}: {e}"]}
    _clear_registry()
    return {"valid": all_valid, "sections": results}


# ---- 下游 OpenX middleware 校验 ----

def check_downstream_road_network(target_json):
    """
    校验 target JSON 是否满足下游 mela2mancera_preview_convert.py
    的 convert_roads 硬约束。返回错误列表（空 = 通过）。

    约束清单见 skill.md 6.3 节。
    """
    errors = []
    scene = target_json["scene"]
    if scene.get("type") == "off_road":
        errors.append("scene.type='off_road' 不被下游支持")
    roads = scene["road_network"]["roads"]
    # mela-openx-generator currently indexes lane_number with a segment index
    # that restarts at zero for every road.  Different lane counts at the same
    # per-road segment index therefore lead to item=None and item['direction'].
    # Reject that shape deterministically instead of claiming OpenX readiness.
    reference_segment_lane_counts = []
    if roads and scene.get("type") != "intersection":
        reference_segment_lane_counts = [
            len(segment.get("lanes", [])) for segment in roads[0].get("segments", [])
        ]
    for road_index, road in enumerate(roads):
        if road_index and reference_segment_lane_counts:
            for segment_index, segment in enumerate(road.get("segments", [])):
                if (segment_index < len(reference_segment_lane_counts)
                        and len(segment.get("lanes", [])) != reference_segment_lane_counts[segment_index]):
                    errors.append(
                        "OpenX generator incompatible road/segment lane shape: "
                        f"road index {road_index}, segment index {segment_index} has "
                        f"{len(segment.get('lanes', []))} lanes; expected "
                        f"{reference_segment_lane_counts[segment_index]} due to generator indexing")
    for junction in scene["road_network"].get("junctions", []):
        connections=junction.get("num_connections",0)
        if connections and len(roads)<connections:
            errors.append(
                f"Intersection has {len(roads)} roads but junction requires "
                f"{connections} connections")
    for road in roads:
        if not road.get("segments"):
            errors.append(f"Road {road.get('name')}: segments 为空或缺失")
            continue
        for seg in road["segments"]:
            if not seg.get("lanes"):
                errors.append(
                    f"Lane empty for segment {seg['name']} - "
                    f"需按 4.3 规则7 生成默认 lane")
            if "length" not in seg:
                errors.append(f"Segment {seg['name']}: 缺少 length 字段")
            for lane in seg.get("lanes", []):
                if "name" not in lane or "id" not in lane:
                    errors.append(f"Segment {seg['name']}: lane 缺少 name/id")
                if lane.get("direction") not in ("forward", "backward",
                                                  "bidirectional"):
                    errors.append(
                        f"Segment {seg['name']} Lane {lane.get('name')}: "
                        f"direction 非法")
    return errors
