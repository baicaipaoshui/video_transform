"""
共享路径设置 —— 所有脚本统一 import 此模块，自动完成 sys.path 注入。

用法（在 scenes/{type}/convert.py 开头）：
    import sys, os
    _LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
    sys.path.insert(0, _LIB)
    from path_setup import *          # 路径常量 + mela sys.path
"""
import os
import sys

# ---- 目录定位（基于本文件位置，不依赖 cwd）----
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))   # lib/
_SCRIPTS_DIR = os.path.dirname(_THIS_DIR)                   # case-conversion/scripts/
_SKILL_DIR   = os.path.dirname(_SCRIPTS_DIR)                # case-conversion/
_SKILLS_DIR  = os.path.dirname(_SKILL_DIR)                  # skills/
_WORKSPACE   = os.path.dirname(_SKILLS_DIR)                 # 工作区根目录

MELA_BL_DIR          = os.path.join(_SCRIPTS_DIR, "mela-business-logic")
MEDIA_INPUT          = os.path.join(_WORKSPACE, "media", "input")
MEDIA_OUTPUT         = os.path.join(_WORKSPACE, "media", "output")
_SHARED_SKILLS = os.path.join(os.path.dirname(_WORKSPACE), "global-video-understanding", "skills")
if _SHARED_SKILLS not in sys.path:
    sys.path.insert(0, _SHARED_SKILLS)

# ---- sys.path 注入当前 Skill 内正式 Mela model package ----
if MELA_BL_DIR not in sys.path:
    sys.path.insert(0, MELA_BL_DIR)

import mela_business_logic                       # noqa: E402
import mela_business_logic.scenario_preview       # noqa: E402
