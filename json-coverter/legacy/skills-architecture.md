# skills/ 目录导航

## 目录结构

```
skills/
  skill.md              通用转换规则 + 标准工作流（所有场景共用）
  README.md             本文件
  validate_server.py    FastAPI pydantic 校验服务（:8900）
  lib/                  共享代码库（所有场景复用）
    path_setup.py       sys.path 注入 + 路径常量（MEDIA_INPUT / MEDIA_OUTPUT）
    enums.py            枚举映射表（COLOR_MAP / VCLASS_MAP）
    builders.py         构造器（uid / mps_to_kmh / mk_pos / mk_orient /
                          mk_oriented_point / mk_action / mk_transition /
                          mk_maneuver / decide_direction）
    validators.py       校验（validate_data / check_downstream_road_network）
  scenes/
    intersection/       路口场景
      skill.md          路口专属规则
      convert.py        路口转换脚本
  samples/              正常 Mela 链路产出样例（参考）
  pydantic_classes/     完整 schema 定义
  mela_compat/           本地 Mela 校验兼容层
```

## 添加新场景类型

1. 在 `scenes/` 下建新目录（如 `scenes/highway/`）
2. 写 `scenes/{type}/skill.md` -- 场景专属规则
3. 写 `scenes/{type}/convert.py` -- 转换脚本：
   ```python
   import os, sys, json
   _LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
       os.path.abspath(__file__)))), "lib")
   if _LIB not in sys.path:
       sys.path.insert(0, _LIB)
   from path_setup import MEDIA_INPUT, MEDIA_OUTPUT
   from enums import COLOR_MAP, VCLASS_MAP
   from builders import uid, mps_to_kmh, mk_pos, mk_orient, \
       mk_oriented_point, mk_action, mk_transition, mk_maneuver
   from validators import validate_data, check_downstream_road_network
   ```
4. 若有新枚举值或通用规则，更新 `lib/enums.py` 和 `skills/skill.md`

## 标准工作流

```
1. 用户把输入 JSON 放入 media/input/
2. 运行: python skills/scenes/{type}/convert.py
3. 产出写入 media/output/，脚本自动执行双重校验
4. pydantic ✅ + 下游 ✅ = 完成
```

## lib/ 共享代码说明

| 模块 | 职责 | 关键导出 |
|------|------|---------|
| `path_setup` | sys.path 注入本地 mela_compat + pydantic_classes；路径常量 | `MEDIA_INPUT`, `MEDIA_OUTPUT`, `MELA_BL_DIR` |
| `enums` | 源 JSON 枚举值 -> Mela 枚举值映射 | `COLOR_MAP`, `VCLASS_MAP` |
| `builders` | OrientedPoint / Maneuver / Action 构造器 | `uid()`, `mps_to_kmh()`, `mk_pos()`, `mk_orient()`, `mk_oriented_point()`, `mk_action()`, `mk_transition()`, `mk_maneuver()`, `decide_direction()` |
| `validators` | pydantic 四部分校验 + 下游 OpenX 校验 | `validate_data()`, `check_downstream_road_network()` |
