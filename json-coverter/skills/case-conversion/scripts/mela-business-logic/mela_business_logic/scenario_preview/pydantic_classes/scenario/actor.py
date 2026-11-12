from typing import Any, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.maneuver import (
    Maneuver,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    PositionSpecifier,
)

from . import logger


class Actor(BaseClass):
    """
    Common ancestor for all dynamic scenario participants.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    name: Optional[str] = Field(None, description="Human readable actor name")
    description: Optional[str] = Field(None, description="Optional comment")
    is_ego_actor: bool = Field(
        ..., description="Flag for whether the actor is the ego actor."
    )

    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the Scenario this actor belongs to",
        # exclude=True
    )

    # ------------------------------------------------------------------ #
    # Behavioral FSM                                                    #
    # ------------------------------------------------------------------ #
    tracking_id: Optional[int] = Field(
        None,
        description="Tracking ID of the object from the video",
        # exclude=True
    )

    timeline: List[Maneuver] = Field(
        default_factory=list,
        description="Ordered list of maneuver steps, each defined by one action-transition pair.",
    )

    # ------------------------------------------------------------------ #
    # Lifecycle hooks                                                    #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized '{self.__class__.__name__}' '{self.name}' (timeline={len(self.timeline)})."
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def add_maneuver_to_timeline(self, maneuver: Maneuver) -> None:
        maneuver.parent_id = self.id
        self.timeline.append(maneuver)
        logger.info(
            f"Added Maneuver {maneuver.name} to Actor {self.name or self.id} (total={len(self.timeline)})."
        )

    # --------------------------------------------------------------------- #
    # Pydantic validator                                                    #
    # --------------------------------------------------------------------- #
    def detect_tautological_referencing(self) -> "Actor":
        for maneuver in self.timeline:
            if maneuver.action.start_position:
                if self.name == maneuver.action.start_position.position.reference_name:
                    raise ModelValidationError(
                        f"An actor's start position cannot reference itself. Use a lane reference for ego actor, or use another actor as a reference.",
                        logger=logger,
                    )
                if (
                    self.name
                    == maneuver.action.start_position.orientation.reference_name
                ):
                    raise ModelValidationError(
                        f"An actor's start orientation cannot reference itself. Use a lane reference for ego actor, or use another actor as a reference.",
                        logger=logger,
                    )
        return self

    def correct_ego_position(self) -> "Actor":
        if self.is_ego_actor:
            for maneuver in self.timeline:
                if maneuver.action.start_position:
                    if (
                        maneuver.action.start_position.position.longitudinal_specifier
                        == PositionSpecifier.BEHIND
                        and maneuver.action.start_position.position.longitudinal_value
                        > 0
                    ):
                        raise ModelValidationError(
                            f"Ego vehicle's start longitudinal position CANNOT be behind a Road. It must be either at its beginning (0.0 m) or further on (x m ahead).",
                            logger=logger,
                        )
        return self

    # ------------------------------------------------------------------ #
    # Dunder                                                             #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return f"Actor(name={self.name}, timeline={len(self.timeline)} maneuvers)"
