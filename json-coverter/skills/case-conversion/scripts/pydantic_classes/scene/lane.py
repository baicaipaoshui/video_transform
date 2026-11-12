from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


# --------------------------------------------------------------------------- #
# Enumerations                                                                #
# --------------------------------------------------------------------------- #
class LaneType(str, Enum):
    """Functional category of a lane."""

    DRIVING = "driving"
    SHOULDER = "shoulder"
    BICYCLE = "bicycle"
    PARKING = "parking"
    BUS = "bus"
    AUXILIARY = "auxiliary"
    EMERGENCY = "emergency"


class LaneDirection(str, Enum):
    """Allowed direction(s) of travel."""

    FORWARD = "forward"
    BACKWARD = "backward"
    BIDIRECTIONAL = "bidirectional"


class LaneMarking(str, Enum):
    """Visual delimitation style of the lane’s borders."""

    # NONE = "none"  # no marking on either side
    SOLID = "solid"  # single solid line on both sides
    DASHED = "dashed"  # single dashed line on both sides

    SOLID_DASHED = "solid_dashed"  # solid (left) – dashed (right)
    DASHED_SOLID = "dashed_solid"
    DOUBLE_SOLID = "double_solid"
    DOUBLE_DASHED = "double_dashed"


class LaneMarkingColor(str, Enum):
    """Visual color of the lane marking"""

    WHITE = "white"
    YELLOW = "yellow"
    BLUE = "blue"
    GREEN = "green"
    RED = "red"


# --------------------------------------------------------------------------- #
# Data model                                                                  #
# --------------------------------------------------------------------------- #
class Lane(BaseClass):
    """
    A Lane is an atomic part of a RoadSegment cross‑section.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="ID of the parent RoadSegment",
        # exclude=True
    )

    name: Optional[str] = Field(None, description="Human readable lane name")
    description: Optional[str] = Field(None, description="Optional description")

    # ------------------------------------------------------------------ #
    # Geometry / semantics                                               #
    # ------------------------------------------------------------------ #
    index: int = Field(
        ...,
        ge=0,
        description="0‑based index from centre line outwards",
    )

    width: float = Field(
        ...,
        gt=0,
        le=10.0,
        description="Lane width in metres",
    )

    type: LaneType = Field(
        default=LaneType.DRIVING,
        description="Functional lane category",
    )

    direction: LaneDirection = Field(
        default=LaneDirection.FORWARD,
        description="Permitted travel direction",
    )

    # speed_limit: Optional[float] = Field(
    #     None,
    #     ge=0,
    #     description="Maximum allowed speed in km/h (None = inherited)",
    # )

    allowed_maneuvers: List[str] = Field(
        default_factory=list,
        description="Permitted maneuvers (straight, left, …)",
    )

    # TODO: use lane markings enum?
    left_lane_marking_type: LaneMarking = Field(
        ..., description="Left lane marking type (eg: dashed, solid, etc)"
    )
    left_lane_marking_color: LaneMarkingColor = Field(
        ..., description="Left lane marking color (eg: white, yellow, etc)"
    )
    right_lane_marking_type: LaneMarking = Field(
        ..., description="Right lane marking type (eg: dashed, solid, etc)"
    )
    right_lane_marking_color: LaneMarkingColor = Field(
        description="Right lane marking color (eg: white, yellow, etc)"
    )

    # ------------------------------------------------------------------ #
    # Pydantic post‑init hook                                            #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized Lane '{self.name}' (index={self.index}, width={self.width} m, type={self.type})."
        )

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #
    def __str__(self) -> str:  # pragma: no cover
        return (
            f"name={self.name}, index={self.index}, width={self.width} m, "
            f"type={self.type.value}, direction={self.direction.value})"
        )
