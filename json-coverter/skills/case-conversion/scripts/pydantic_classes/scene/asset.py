from enum import Enum
from typing import Any, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)

from . import logger


class AssetCategory(str, Enum):
    """
    Very coarse classification of static scene assets.

    Add more items as needed – the enum is intentionally generic so that
    it can be used for both on-road and off-road scenes.
    """

    TREE = "tree"
    TREE_GROUP = "tree_group"
    POWER_POLE = "power_pole"
    BUILDING = "building"
    LARGE_BUILDING = "large_building"
    BUS_STOP = "bus_stop"
    GAS_STATION = "gas_station"
    FENCE_POST = "fence_post"
    ROCK = "rock"
    # TRAFFIC_SIGN = "traffic_sign"
    SHORT_HEIGHT_VEGETATION = "short_height_vegetation"
    # TRAFFIC_LIGHT = "traffic_light"
    # CROSSWALK = "crosswalk"
    # OTHER = "other" # Other class is removed because of identification of vehicles as assets under OTHER attribute


class Asset(BaseClass):
    """
    Generic *static* object that can be placed either directly in a `Scene`
    (`Scene.assets`) **or** on an `OffRoadSurface` (`OffRoadSurface.assets`).

    • Geometry is deliberately kept simple – many assets are basically
      “points of interest” for perception tests.  If you need a detailed
      mesh or bounding box, extend this class later.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the parent container (Scene *or* OffRoadSurface)",
        # exclude=True,
    )
    name: Optional[str] = Field(None, description="Human readable asset name")
    description: Optional[str] = Field(None, description="Optional free text")
    category: AssetCategory = Field(
        ...,
        description="High-level asset category, choose the asset category mentioned in AssetCategory class",
    )

    # ------------------------------------------------------------------ #
    # Pose / size                                                        #
    # ------------------------------------------------------------------ #
    # TODO: SkipJsonSchema ??
    pose: OrientedPoint = Field(
        ...,
        description=(
            "Position & orientation expressed *relatively* " "(see oriented_point.py)"
        ),
    )

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(f"Initialized Asset '{self.name}' (category={self.category}).")

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return f"Asset(name={self.name}, category={self.category}, " f"id={self.id})"
