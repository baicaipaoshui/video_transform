from enum import Enum
from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.lane import (
    Lane,
    LaneDirection,
    LaneMarking,
    LaneMarkingColor,
    LaneType,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.shoulder import (
    Shoulder,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.side_walk import (
    Sidewalk,
)

from . import logger


class RoadSegmentShapeType(str, Enum):
    """Supported geometric types for a road segment."""

    STRAIGHT = "straight"
    CURVED_RIGHT = "curved_right"
    CURVED_LEFT = "curved_left"  # TODO: add curved_left and curved_right
    # PARAMETERIZED = "parameterized"


class RoadSegment(BaseClass):
    """
    Portion of a road with homogeneous geometry and lane configuration.

    The segment runs from the parent road’s *A*‑side toward the *B*‑side
    and owns all of its sub‑components (lanes, shoulders, sidewalks).
    """

    # ------------------------------------------------------------------ #
    # Basic identifiers                                                  #
    # ------------------------------------------------------------------ #
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="ID of the parent road",
        # exclude=True
    )

    # ------------------------------------------------------------------ #
    # Descriptive meta‑data                                              #
    # ------------------------------------------------------------------ #
    name: Optional[str] = Field(None, description="Optional human readable name")
    description: Optional[str] = Field(None, description="Detailed description")

    # ------------------------------------------------------------------ #
    # Geometry / ordering                                                #
    # ------------------------------------------------------------------ #
    order_index: int = Field(
        ...,
        ge=0,
        description="Logical order within the parent road (0‑based)",
    )
    length: float = Field(
        ...,
        gt=0,
        description="Segment length in metres",
    )
    shape_type: RoadSegmentShapeType = Field(
        default=RoadSegmentShapeType.STRAIGHT,
        description="Geometric primitive used for this segment",
    )
    road_radius: float | None = Field(
        None,
        ge=300,
        le=2000,
        description="Curvature radius of the road segment in meters (only for curved RoadSegment), else it must be null/None",
    )
    # infrastructures: List[Union[Crosswalk, TrafficLight, TrafficSign]] = Field(default_factory=list, description="List of crosswalks, traffic lights, or traffic signs in this road segment.")

    # crosswalk: Optional[Crosswalk] = Field(
    #         None,
    #         description=(
    #             "Optional detailed crosswalk metadata for this asset, including type "
    #             "(e.g., zebra, ladder) and its exact position/orientation. "
    #             "Present only if this asset represents a pedestrian crossing."
    #     )
    # )

    # traffic_light: Optional[TrafficLight] = Field(
    #         None,
    #         description=(
    #             "Optional detailed traffic light metadata for this asset, including its state "
    #             "(red, yellow, green), position, and other attributes. "
    #             "Present only if this asset is a traffic light."
    #     )
    # )

    # traffic_sign: Optional[TrafficSign] = Field(
    #         None,
    #         description=(
    #             "Optional detailed traffic sign metadata for this asset, such as the specific sign "
    #             "type or code. Present only if this asset is a traffic sign."
    #     )
    # )

    # ------------------------------------------------------------------ #
    # Sub‑components                                                     #
    # ------------------------------------------------------------------ #
    lanes: List[Lane] = Field(default_factory=list, description="Contained lanes")
    left_shoulder: Optional[Shoulder] = Field(None, description="Left shoulder")
    right_shoulder: Optional[Shoulder] = Field(None, description="Right shoulder")
    left_sidewalk: Optional[Sidewalk] = Field(None, description="Left sidewalk")
    right_sidewalk: Optional[Sidewalk] = Field(None, description="Right sidewalk")

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    @field_validator("road_radius", mode="before")
    def check_road_radius(cls, v):
        if v is not None and (v < 300 or v > 2000):
            logger.warning(f"Setting invalid road radius value: {v} → None.")
            return None
        return v

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Post‑initialization checks."""
        super().model_post_init(__context)
        self._validate_shape_parameters()

    def validate_road_segment(self) -> "RoadSegment":
        self.__validate_lane_existence_in_all_road_segments()
        self.__validate_lane_numbering()
        logger.info("All assembly checks for road segment passed successfully.")
        return self

    def __validate_lane_existence_in_all_road_segments(self) -> None:
        """
        Ensure all road segments have at least one lane.
        """
        if len(self.lanes) == 0:
            logger.warning(
                f"Road segment did not contain a single lane. Manually adding a lane to {self.name} with default values."
            )
            manual_lane = Lane(
                name=f"{self.name}_Lane_1",
                description="Default Lane",
                parent_id=self.id,
                index=0,
                width=3.5,
                type=LaneType.DRIVING.value,
                direction=LaneDirection.FORWARD.value,
                left_lane_marking_type=LaneMarking.SOLID.value,
                left_lane_marking_color=LaneMarkingColor.WHITE.value,
                right_lane_marking_type=LaneMarking.SOLID.value,
                right_lane_marking_color=LaneMarkingColor.WHITE.value,
                allowed_maneuvers=["straight"],
            )
            self.add_lane(manual_lane)

    def __validate_lane_numbering(self) -> None:
        """
        Ensure lane numbering convention is complied with.
        """
        forward_lane_present = False
        for lane in self.lanes:
            if lane.direction == LaneDirection.FORWARD.value:
                forward_lane_present = True
                break
        if forward_lane_present:
            if self.lanes[0].direction != LaneDirection.FORWARD.value:
                # rearrange lanes
                logger.warning(
                    "Rearrangement of lanes necessary because first lane was not a forward lane."
                )
                forwards = [
                    lane
                    for lane in self.lanes
                    if lane.direction == LaneDirection.FORWARD.value
                ]
                bidirectionals = [
                    lane
                    for lane in self.lanes
                    if lane.direction == LaneDirection.BIDIRECTIONAL.value
                ]
                backwards = [
                    lane
                    for lane in self.lanes
                    if lane.direction == LaneDirection.BACKWARD.value
                ]
                sorted_lanes = forwards + bidirectionals + backwards
                logger.warning(
                    "First lane(s) are forward, followed by bidirectional lanes (if any), followed by backward lanes."
                )
                for idx, sorted_lane in enumerate(sorted_lanes):
                    sorted_lane.index = idx
                logger.warning(
                    "Reassigned order indices for lanes based on rearrangement."
                )
                # no need to change lane names here, as scene assembly validation takes care of it anyway
                self.lanes = sorted_lanes

    # ------------------------------------------------------------------ #
    # Private helpers                                                    #
    # ------------------------------------------------------------------ #
    def _validate_shape_parameters(self) -> None:
        """Ensure ``shape_parameters`` matches ``shape_type`` requirements."""
        if self.shape_type == RoadSegmentShapeType.STRAIGHT.value:
            # No mandatory parameters
            return

        if self.shape_type in [
            RoadSegmentShapeType.CURVED_RIGHT.value,
            RoadSegmentShapeType.CURVED_LEFT.value,
        ]:
            radius = self.road_radius
            if radius is None or radius <= 0:
                raise ModelValidationError(
                    f"Curved segments require a positive 'radius' parameter (segment={self.name})",
                    logger=logger,
                )

        # if self.shape_type == RoadSegmentShapeType.PARAMETERIZED.value:
        #     if "function" not in self.shape_parameters:
        #         raise ModelValidationError(
        #             f"Parameterized segments require a 'function' parameter (segment={self.name})",
        #             logger=logger,
        #         )

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def add_lane(self, lane: Lane) -> None:
        """Attach a lane to this segment and update its parent reference."""
        lane.parent_id = self.id
        self.lanes.append(lane)
        logger.info(
            f"Added Lane '{lane.name}' to RoadSegment '{self.name}' (now {len(self.lanes)} lanes)"
        )

    def set_shoulders(
        self,
        *,
        left: Optional[Shoulder] = None,
        right: Optional[Shoulder] = None,
    ) -> None:
        """Configure left / right shoulders (either may be omitted)."""
        if left is not None:
            left.parent_id = self.id
            self.left_shoulder = left
            logger.info(f"Set left shoulder for RoadSegment '{self.name}'.")

        if right is not None:
            right.parent_id = self.id
            self.right_shoulder = right
            logger.info(f"Set right shoulder for RoadSegment '{self.name}'.")

    def set_sidewalks(
        self,
        *,
        left: Optional[Sidewalk] = None,
        right: Optional[Sidewalk] = None,
    ) -> None:
        """Configure left / right sidewalks (either may be omitted)."""
        if left is not None:
            left.parent_id = self.id
            self.left_sidewalk = left
            logger.info(f"Set left sidewalk for RoadSegment '{self.name}'.")

        if right is not None:
            right.parent_id = self.id
            self.right_sidewalk = right
            logger.info(f"Set right sidewalk for RoadSegment '{self.name}'.")

    # ------------------------------------------------------------------ #
    # Computed properties                                                #
    # ------------------------------------------------------------------ #
    def get_total_width(self) -> float:
        """
        Return the full cross‑section width of the segment.

        Width = lane widths + shoulders # (+ sidewalks)
        """
        width = sum(lane.width for lane in self.lanes)

        for component in (
            self.left_shoulder,
            self.right_shoulder,
            # self.left_sidewalk,
            # self.right_sidewalk,
        ):
            if component is not None:  # mypy friendly
                width += component.width

        return width

    # ------------------------------------------------------------------ #
    # Dunder methods                                                     #
    # ------------------------------------------------------------------ #
    def __str__(self) -> str:  # pragma: no cover
        return (
            f"RoadSegment(Segment: order_index={self.order_index}, "
            f"length={self.length:.1f} m, shape_type={self.shape_type}, "
            f"lanes={self.lanes}),"
            f"left_shoulder={self.left_shoulder}, right_shoulder={self.right_shoulder},"
            f"left_sidewalk={self.left_sidewalk}, right_sidewalk={self.right_sidewalk},"
        )
