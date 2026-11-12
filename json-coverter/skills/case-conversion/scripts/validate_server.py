"""
Mela JSON 验证服务
使用 pydantic_classes 中的模型校验 JSON 文件

运行方式:
    python validate_server.py
    或
    uvicorn validate_server:app --reload --port 8900

API 端点:
    GET  /               - 服务信息
    POST /validate       - 上传 JSON 文件校验 (multipart/form-data)
    POST /validate/json  - 发送 JSON body 校验 (application/json)
"""

import sys
import os
import json
import logging

# ============================================================================
# 路径设置（必须在任何 pydantic_classes 导入之前执行）
# ============================================================================

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MELA_BL_DIR = os.path.join(_THIS_DIR, "mela-business-logic")

# 添加当前 Skill 内正式 Mela model package 到 sys.path
if _MELA_BL_DIR not in sys.path:
    sys.path.insert(0, _MELA_BL_DIR)

# 直接导入正式包及其内置 pydantic_classes
import mela_business_logic
import mela_business_logic.scenario_preview

# ============================================================================
# 模型导入
# ============================================================================

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.scene import Scene
from mela_business_logic.scenario_preview.pydantic_classes.entities.entities import (
    Entities,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.scenario import (
    Scenario,
)
from mela_business_logic.scenario_preview.pydantic_classes.environment.environment import (
    Environment,
)
from mela_business_logic.error import ModelValidationError

from fastapi import FastAPI, UploadFile, File, Request
from pydantic import ValidationError

# ============================================================================
# FastAPI 应用
# ============================================================================

app = FastAPI(title="Mela JSON 验证服务", version="1.0.0")
logger = logging.getLogger(__name__)


# ============================================================================
# 辅助函数
# ============================================================================

def _rename_id_to_uid(obj):
    """递归地将所有 'id' 键重命名为 'UID'，匹配 pydantic 模型的 alias。"""
    if isinstance(obj, dict):
        return {
            ("UID" if k == "id" else k): _rename_id_to_uid(v)
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [_rename_id_to_uid(item) for item in obj]
    return obj


def _clear_registry():
    """清空 BaseClass 注册表，避免重复 ID 错误。"""
    BaseClass._registry.clear()


# ============================================================================
# 校验逻辑
# ============================================================================

_SECTION_MODELS = {
    "scene": Scene,
    "entities": Entities,
    "scenario": Scenario,
    "environment": Environment,
}


def _validate_data(data: dict) -> dict:
    """校验解析后的 JSON 数据，返回校验结果。"""
    # 重命名 id -> UID（pydantic 模型使用 alias="UID"）
    data = _rename_id_to_uid(data)

    results = {}
    all_valid = True

    for section_name, model_cls in _SECTION_MODELS.items():
        section_data = data.get(section_name)
        if section_data is None:
            results[section_name] = {
                "valid": False,
                "errors": [
                    {
                        "type": "missing_section",
                        "message": f"缺少 '{section_name}' 部分",
                    }
                ],
            }
            all_valid = False
            continue

        # 每个部分校验前清空注册表，避免重复 ID 错误
        _clear_registry()

        try:
            instance = model_cls.model_validate(section_data)
            results[section_name] = {
                "valid": True,
                "message": f"{model_cls.__name__} 校验通过",
                "id": instance.id,
            }
        except ValidationError as e:
            all_valid = False
            results[section_name] = {
                "valid": False,
                "errors": [
                    {
                        "type": err.get("type", "validation_error"),
                        "loc": " -> ".join(str(x) for x in err.get("loc", [])),
                        "message": err.get("msg", ""),
                    }
                    for err in e.errors()
                ],
            }
        except ModelValidationError as e:
            all_valid = False
            results[section_name] = {
                "valid": False,
                "errors": [
                    {
                        "type": "model_validation_error",
                        "message": str(e),
                    }
                ],
            }
        except Exception as e:
            all_valid = False
            results[section_name] = {
                "valid": False,
                "errors": [
                    {
                        "type": type(e).__name__,
                        "message": str(e),
                    }
                ],
            }

    # 清理注册表
    _clear_registry()

    return {
        "valid": all_valid,
        "sections": results,
    }


# ============================================================================
# API 端点
# ============================================================================

@app.get("/")
async def root():
    """服务信息"""
    return {
        "service": "Mela JSON 验证服务",
        "status": "running",
        "endpoints": {
            "POST /validate": "上传 JSON 文件校验 (multipart/form-data)",
            "POST /validate/json": "发送 JSON body 校验 (application/json)",
        },
    }


@app.post("/validate")
async def validate_file(file: UploadFile = File(...)):
    """上传 JSON 文件进行校验"""
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "errors": [{"type": "json_parse_error", "message": str(e)}],
        }

    return _validate_data(data)


@app.post("/validate/json")
async def validate_json_body(request: Request):
    """直接发送 JSON body 进行校验"""
    try:
        data = await request.json()
    except Exception as e:
        return {
            "valid": False,
            "errors": [{"type": "json_parse_error", "message": str(e)}],
        }

    return _validate_data(data)


# ============================================================================
# 启动
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8900)
