from typing import Any, ClassVar, List

from pydantic import Field

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.scene.element import Element
from mela_business_logic.scenario_preview.pydantic_classes.scene.road_segment import (
    RoadSegment,
)

from . import logger


class Road(Element):
    """
    A continuous road that can be attached to the network at two positions
    ('A' and 'B') and is composed of ordered `RoadSegment` objects.
    """

    # ------------------------------------------------------------------ #
    # Connectivity                                                       #
    # ------------------------------------------------------------------ #
    CONNECTION_POSITIONS: ClassVar[List[str]] = ["A", "B"]

    # ------------------------------------------------------------------ #
    # Data fields                                                        #
    # ------------------------------------------------------------------ #
    segments: List[RoadSegment] = Field(
        default_factory=list,
        description="Ordered list of segments that build the road",
        # exclude=True,
    )
    # assets: List[Asset] = Field(
    #     default_factory=list,
    #     description="Generic assets or/static objects (buildings, vegetation…)",
    #     # exclude=True,
    # )
    # obstacles: List[Obstacle] = Field(
    #     default_factory=list,
    #     description="Generic Obstacles or/static objects (fallen trees, construction barriers etc.)",
    #     # exclude=True,
    # )
    # `length` is derived, but we keep it as a proper field, so it is part
    # of serialization/deserialization.
    length: float = Field(
        0.0,
        ge=0,
        description="Total length of the road in metres (auto‑maintained)",
        # exclude=True,
    )

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Finalize object after Pydantic has performed the base validation."""
        super().model_post_init(__context)

        # Make sure length reflects the initial segments list.
        self._update_length()
        logger.info(f"Initialized Road name='{self.name}'.")

    # ------------------------------------------------------------------ #
    # Element API overrides                                              #
    # ------------------------------------------------------------------ #
    def get_available_connection_positions(self) -> List[str]:
        """Return a *copy* of the two allowed connection identifiers."""
        return self.CONNECTION_POSITIONS.copy()

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #
    def add_segment(self, segment: RoadSegment) -> None:
        """
        Append a ``RoadSegment`` to this road.

        Guard rails:
        • The segment must not already belong to another road.
        • Its `order_index` must be unique within this road.
        """
        if segment.parent_id and segment.parent_id != self.id:
            raise ModelValidationError(
                f"Cannot add RoadSegment '{segment.id}'; it already belongs to Road '{segment.parent_id}'",
                logger=logger,
            )

        order_indices = {s.order_index for s in self.segments}
        if segment.order_index in order_indices:
            raise ModelValidationError(
                f"Duplicate order_index '{segment.order_index}' for segment '{segment.id}' in Road '{self.id}'.",
                logger=logger,
            )

        segment.parent_id = self.id
        self.segments.append(segment)
        self.segments.sort(key=lambda s: s.order_index)  # keep list ordered

        logger.info(
            f"Added RoadSegment '{segment.name}' (order_index={segment.order_index}, length={segment.length} m) to Road '{self.name}'."
        )

        self._update_length()

    def get_segments(self) -> List[RoadSegment]:
        """
        Return a shallow **copy** of the segments list so callers cannot
        accidentally modify internal state.
        """
        return self.segments.copy()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _update_length(self) -> None:
        """Synchronize the ``length`` field with the sum of segment lengths."""
        calculated_length = sum(seg.length for seg in self.segments)
        if abs(calculated_length - self.length) > 1e-6:
            logger.info(
                f"Updating length of Road '{self.name}' from {self.length} to {calculated_length} m."
            )
            self.length = calculated_length

    # ------------------------------------------------------------------ #
    # Dunder methods                                                     #
    # ------------------------------------------------------------------ #
    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Road(name={self.name}"
            f"segments={self.segments}, length={self.length:.2f} m)"
        )
