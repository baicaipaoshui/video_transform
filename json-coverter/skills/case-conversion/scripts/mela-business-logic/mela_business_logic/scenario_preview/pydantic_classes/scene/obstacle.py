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


class ObstacleCategory(str, Enum):
    """
    Very coarse classification of static scene Obstacles.

    Add more items as needed – the enum is intentionally generic so that
    it can be used for both on-road and off-road scenes.
    """

    FALLEN_TREE = "fallen_tree"
    BLOCKING_ROCK = "blocking_rock"
    BROKEN_DOWN_VEHICLE = "broken_down_vehicle"
    CONSTRUCTION_BARRIER = "construction_barrier"
    UNIDENTIFIED_OBJECT = "unidentified_object"


class Obstacle(BaseClass):
    """
    Generic *static* obstacle that could be present or be placed either directly in a `Scene`
    (`Road.obstacles`) **or** on an `OffRoadSurface` (`OffRoadSurface.assets`).

    • Geometry is deliberately kept simple – many obstacles are basically
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
    name: Optional[str] = Field(None, description="Human readable obstacle name")
    description: Optional[str] = Field(None, description="Optional free text")
    category: ObstacleCategory = Field(
        ...,
        description="High-level obstacle category, choose the obstacle category mentioned in AssetObstacleCategory class",
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
        logger.info(f"Initialized Obstacle '{self.name}' (category={self.category}).")

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return f"Obstacle(name={self.name}, category={self.category}, " f"id={self.id})"
