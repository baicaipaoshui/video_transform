from enum import Enum
from typing import Any, List, Optional

from pydantic import Field, model_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.scene.off_road_element import (
    OffRoadElement,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)

from . import logger


class PathType(str, Enum):
    GRAVEL_TRACK = "gravel_track"
    TIRE_RUT = "tire_rut"
    DIRT_PATH = "dirt_path"
    FOOTPATH = "footpath"
    GRASS = "grass"
    UNKNOWN = "unknown"


class PathFeature(OffRoadElement):
    """
    Linear feature (gravel track, tyre rut …).

    Geometry variants
    -----------------
    1. waypoints   – list[OrientedPoint] (free curve)
    2. grid_cells  – inherited sequence of grid indices
    """

    path_type: PathType = Field(PathType.UNKNOWN, description="Kind of path / track")
    width: float = Field(..., gt=0.0, description="Approx. width [m]")

    # variant #1
    waypoints: Optional[List[OrientedPoint]] = Field(
        None, min_length=2, description="Centre line (≥ 2 points)"
    )

    # ------------------------------------------------------------------ #
    # Validation – exactly one geometry variant                          #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _check_geometry_variant(self) -> "PathFeature":
        uses_wp = self.waypoints is not None
        uses_grid = self.grid_cells is not None
        if uses_wp == uses_grid:
            raise ModelValidationError(
                "PathFeature: provide *either* 'waypoints' OR 'grid_cells'.",
                logger=logger,
            )
        return self

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        if self.grid_cells:
            detail = f"{len(self.grid_cells)} grid cell(s)"
        elif self.waypoints:
            detail = f"{len(self.waypoints)} waypoint(s)"
        else:
            detail = "Without grid cells or waypoints - expect error to be raised."
        logger.info(
            f"Initialized PathFeature '{self.name or self.id}' ({self.path_type}, {detail})."
        )
