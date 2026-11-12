from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)

from . import logger


class InfrastructureType(str, Enum):
    """
    Categories for traffic infrastructure type
    """

    TRAFFIC_LIGHT = "traffic_light"
    TRAFFIC_SIGN = "traffic_sign"
    CROSSWALK = "crosswalk"


class TrafficLightColor(str, Enum):
    """
    Traffic light status
    """

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    # UNKNOWN = "unknown"


# TODO: Later this has to be move to Scenario
class TrafficLightMode(str, Enum):
    """
    Modes in Traffic light
    """

    ON = "on"
    OFF = "off"
    BLINKING = "blinking"


class TrafficLightShape(str, Enum):
    CIRCLE = "circle"
    ARROW_UP = "arrow_up"
    ARROW_LEFT = "arrow_left"
    ARROW_RIGHT = "arrow_right"
    DIAGONAL_UPPER_LEFT = "diagonal_upper_left"
    DIAGONAL_UPPER_RIGHT = "diagonal_upper_right"


class TrafficLightStructureType(str, Enum):
    HORIZONTAL_2SPOTS = "Horizontal_2spots"
    HORIZONTAL_3SPOTS = "Horizontal_3spots"
    # HORIZONTAL_4SPOTS = "Horizontal_4spots"
    # HORIZONTAL_5SPOTS = "Horizontal_5spots"
    VERTICAL_2SPOTS = "Vertical_2spots"
    VERTICAL_3SPOTS = "Vertical_3spots"
    # VERTICAL_4SPOTS = "Vertical_4spots"
    # VERTICAL_5SPOTS = "Vertical_5spots"
    # DOGHOUSE = "doghouse"
    ONE_SPOT = "1spot"


# TODO: Add more sign types to this enum list later
class TrafficSignMain(str, Enum):
    """
    Traffic sign types
    """

    SPEED_LIMIT_5 = "speed_limit_5"
    SPEED_LIMIT_10 = "speed_limit_10"
    SPEED_LIMIT_20 = "speed_limit_20"
    SPEED_LIMIT_30 = "speed_limit_30"
    SPEED_LIMIT_40 = "speed_limit_40"
    SPEED_LIMIT_50 = "speed_limit_50"
    SPEED_LIMIT_60 = "speed_limit_60"
    SPEED_LIMIT_70 = "speed_limit_70"
    SPEED_LIMIT_80 = "speed_limit_80"
    SPEED_LIMIT_90 = "speed_limit_90"
    SPEED_LIMIT_100 = "speed_limit_100"
    SPEED_LIMIT_110 = "speed_limit_110"
    SPEED_LIMIT_120 = "speed_limit_120"
    SPEED_LIMIT_130 = "speed_limit_130"
    END_OF_SPEED_LIMIT = "end_of_speed_limit"
    SCHOOL = "school"
    CURVED_ROAD_LEFT = "curved_road_left"
    CURVED_ROAD_RIGHT = "curved_road_right"
    JUNCTION = "junction"
    ROUNDABOUT = "roundabout"
    MERGE_LEFT = "merge_left"  # Lane end
    MERGE_RIGHT = "merge_right"  # Lane end
    CONSTRUCTION = "construction"
    CROSSWALK = "crosswalk"
    NO_OVERTAKING = "no-overtaking"
    PARKING = "parking"
    BUS_STOP = "bus_stop"
    YIELD = "yield"
    DIVERSION_LEFT = "diversion_left"
    DIVERSION_RIGHT = "diversion_right"
    TOWN_SIGN = "town_sign"  # Ortsschild


class TrafficSignSupplementary(str, Enum):
    AFTER_50_M = "after_50_m"
    AFTER_100_M = "after_100_m"
    AFTER_200_M = "after_200_m"
    AFTER_500_M = "after_500_m"
    FOR_NEXT_50_M = "for_next_50_m"
    FOR_NEXT_100_M = "for_next_100_m"
    FOR_NEXT_200_M = "for_next_200_m"
    FOR_NEXT_500_M = "for_next_500_m"
    BUS = "bus"
    CAR = "car"
    CAR_WITH_TRAILER = "car_with_trailer"
    TRUCK = "truck"
    SCHOOL = "school"
    # TODO: add more supplementary signs if middleware requests it


class SignSize(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


class PoleType(str, Enum):
    SINGLE_POLE = "single_pole"
    DOUBLE_POLE = "double_pole"
    GANTRY = "gantry"


class TrafficInfrastructureLayoutType(str, Enum):
    """
    Traffic Infrastructure Layout type
    """

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class SignPanel(BaseModel):
    """A single physical sign board / panel."""

    description: str = Field(
        ..., description="Human description, e.g. 'Speed 60' or 'After ... km'"
    )
    size: SignSize = Field(..., description="Sign size S/M/L")
    numeric_value: Optional[float] = Field(
        None,
        description="Numeric value (e.g. 60, 400). Use None for non-numeric signs.",
    )


class SignSupport(BaseModel):
    """
    Supportive structure of the sign holding Sign
    """

    pole_height: Optional[float] = Field(None, description="Height of pole in meters")
    gantry_length: Optional[float] = Field(
        None, description="Length of gantry in meters (if gantry)"
    )
    pole_type: PoleType = Field(..., description="single pole, double pole, gantry")


class Infrastructure(BaseClass):

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the parent container (Scene *or* RoadNetwork)",
    )
    name: Optional[str] = Field(None, description="Human readable Infrastructure name")
    description: Optional[str] = Field(None, description="Optional free text")
    type: InfrastructureType = Field(..., description="Type of infrastructure")
    pose: OrientedPoint = Field(..., description="Position of the infrastructure")

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        logger.info(
            f"Initialized Infrastructure '{self.name or self.id}' (type={self.type})."
        )

    def __str__(self) -> str:
        return f"Infrastructure(name={self.name}, type={self.type}, id={self.id})"


class TrafficLight(Infrastructure):
    type: Literal[InfrastructureType.TRAFFIC_LIGHT] = Field(
        ..., description="type of the Traffic Light infrastructure"
    )
    color: TrafficLightColor = Field(
        default=TrafficLightColor.GREEN, description="Color of the traffic light"
    )
    duration: Optional[float] = Field(
        None,
        ge=0.0,
        description="Duration (in seconds) for traffic light to stay at this color",
    )
    mode: TrafficLightMode = Field(
        default=TrafficLightMode.ON,
        description="Traffic light glowing mode On/Off/Blinking",
    )
    shape: TrafficLightShape = Field(
        default=TrafficLightShape.CIRCLE,
        description="Shape depicted in traffic light, which indicates allowed directions for vehicles to move",
    )
    structure_type: TrafficLightStructureType = Field(
        default=TrafficLightStructureType.VERTICAL_3SPOTS,
        description="Traffic light mounting layout",
    )


class TrafficSign(Infrastructure):
    type: Literal[InfrastructureType.TRAFFIC_SIGN] = Field(
        ..., description="type of the Traffic Sign infrastructure"
    )
    main_sign: TrafficSignMain = Field(..., description="Main traffic sign type")
    main_sign_size: SignSize = Field(
        default=SignSize.MEDIUM, description="Size of the main traffic sign"
    )
    supplementary_signs: List[TrafficSignSupplementary] = Field(
        default_factory=list,
        description="Up to two supplementary signs below the main sign",
        max_length=2,
    )


class Crosswalk(Infrastructure):
    type: Literal[InfrastructureType.CROSSWALK] = Field(
        ..., description="type of the crosswalk infrastructure"
    )
