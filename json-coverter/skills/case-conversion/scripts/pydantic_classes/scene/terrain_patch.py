from enum import Enum
from typing import Any, Optional, Tuple

from pydantic import Field, model_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.scene.off_road_element import (
    OffRoadElement,
)

from . import logger


class TerrainType(str, Enum):
    STUBBLE_CORN = "stubble_corn"
    GRASS = "grass"
    SOIL = "soil"
    MUD = "mud"
    ASPHALT = "asphalt"


class MoistureLevel(str, Enum):
    DRY = "dry"
    DAMP = "damp"
    WET = "wet"
    ICY = "icy"


class Roughness(str, Enum):
    SMOOTH = "smooth"
    MEDIUM = "medium"
    ROUGH = "rough"


class SideEnum(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    FRONT = "front"
    BACK = "back"
    UNKNOWN = "unknown"


class SideEnum(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    FRONT = "front"
    BACK = "back"
    UNKNOWN = "unknown"


class TerrainPatch(OffRoadElement):
    """
    Homogeneous ground area.

    Choose ONE of
    • size        – physical (length, width)  +  pose
    • grid_cells  – inherited from OffRoadElement
    """

    # ----- physical size variant ------------------------------------- #
    size: Optional[Tuple[float, float]] = Field(
        None,
        min_length=2,
        max_length=2,
        description="Length × width in metres (free placement variant)",
    )

    # ----- ground state ---------------------------------------------- #
    terrain_type: TerrainType
    moisture: MoistureLevel = MoistureLevel.DRY
    roughness: Roughness = Roughness.SMOOTH
    # TODO: the below changes was added because of the claas implementation, we might have to revert it back in later point of time
    side: SideEnum | None = Field(
        None, description="Left/right/front/… w. r. t. TerrainPatch"
    )
    row_orientation: str | None = Field(
        None,
        description="Orientation of rows with reference to the ego vehicle driving direction / stubble such as (in driving direction, perpendicular to driving direction)",
    )

    # ------------------------------------------------------------------ #
    # Validation – geometry vs. grid                                     #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _check_geometry_variant(self) -> "TerrainPatch":
        uses_size = self.size is not None
        uses_grid = self.grid_cells is not None
        if uses_size == uses_grid:
            raise ModelValidationError(
                "TerrainPatch: supply *either* ('size' & 'pose') "
                "or 'grid_cells' – but not both / none.",
                logger=logger,
            )
        return self

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        detail = (
            f"{self.size[0]} × {self.size[1]} m"
            if self.size
            else f"{len(self.grid_cells)} grid cell(s)"
        )
        logger.info(
            f"Initialized TerrainPatch '{self.name or self.id}' ({self.terrain_type}, {detail})."
        )
