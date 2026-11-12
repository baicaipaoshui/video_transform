"""枚举映射表 —— 源 JSON 枚举值 -> Mela pydantic 枚举值。所有场景共用。"""

COLOR_MAP = {
    "red": "red", "white": "white", "silver": "silver", "black": "black",
    "dark": "black", "dark_gray": "grey", "gray": "grey", "grey": "grey",
    "maroon": "red", "blue": "blue", "green": "green", "brown": "brown",
    "orange": "orange", "yellow": "yellow",
    "red_with_black_roof": "red",
}

VCLASS_MAP = {
    "car": "sedan", "compact_car": "sedan", "sedan": "sedan", "coupe": "sedan",
    "mpv": "van", "minivan": "van", "suv": "suv", "box_truck": "truck",
    "truck": "truck",
}
