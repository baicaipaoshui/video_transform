from enum import Enum
from typing import Any, Optional

from pydantic import Field, model_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.scene.off_road_element import (
    OffRoadElement,
)

from . import logger


class BorderType(str, Enum):
    TREE_ROW = "tree_row"
    FOREST_EDGE = "forest_edge"
    BUSH_ROW = "bush_row"
    FENCE = "fence"
    WALL = "wall"


# class VegetationType(str, Enum):
#     DECIDUOUS = "deciduous"
#     CONIFEROUS = "coniferous"
#     MIXED_FOREST = "mixed_forest"
#     BUSHES = "bushes"
#     GRASS = "grass"


class SideEnum(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    FRONT = "front"
    BACK = "back"
    UNKNOWN = "unknown"


class BorderFeature(OffRoadElement):
    """
    Continuous boundary feature (tree row, fence, wall …).

    Geometry variants
    -----------------
    1. length + pose       – free placement
    2. grid_cells          – inherited sequence of grid indices
    """

    # what it is
    feature_type: BorderType
    # vegetation: VegetationType | None = None

    # physical props (variant #1)
    length: Optional[float] = Field(
        None, ge=0.0, description="Approx. length [m] (variant #1 only)"
    )

    # relation hint
    side: SideEnum | None = Field(None, description="Left/right/front/… w. r. t. ego")

    # ------------------------------------------------------------------ #
    # Validation – exactly one geometry variant                          #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _check_geometry_variant(self) -> "BorderFeature":
        uses_len = self.length is not None
        uses_grid = self.grid_cells is not None
        if uses_len == uses_grid:
            raise ModelValidationError(
                "BorderFeature: provide *either* ('length' & 'pose') OR 'grid_cells'.",
                logger=logger,
            )
        return self

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        detail = (
            f"{len(self.grid_cells)} grid cell(s)"
            if self.grid_cells
            else f"{self.length:.1f} m"
        )
        logger.info(
            f"Initialized BorderFeature '{self.name or self.id}' ({self.feature_type}, {detail})."
        )
