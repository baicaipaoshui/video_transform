from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)

from . import logger


class MovementDirection(str, Enum):
    FORWARD = "forward"
    BACKWARD = "backward"
    LEFT = "left"
    RIGHT = "right"
    # STOP = "stop"


class Action(BaseClass):
    """
    Basic, atomic vehicle motion that forms the core of maneuvers.
    - Can be used for both on-road and off-road vehicles.
    - Parameters specify direction, magnitude, duration, etc.
    """

    # direction: MovementDirection = Field(..., description="Direction of the basic action relative to the ego vehicle.")
    direction: MovementDirection = Field(
        ...,
        description="Direction of the action movement flow based on the definition in the system message.",
    )
    start_position: Optional[OrientedPoint] = Field(
        ..., description="Starting position of the first (and only first) action."
    )
    start_longitudinal_velocity: float = Field(
        ...,
        description="Starting velocity of the basic action in km/h in the direction of movement.",
    )
    start_lateral_velocity: float = Field(
        ...,
        description="Starting velocity of the basic action in km/h perpendicular to the direction of movement.",
    )
    end_longitudinal_velocity: float = Field(
        ...,
        description="Ending velocity of the basic action in km/h in the direction of movement.",
    )
    # end_lateral_velocity: float = Field(..., description="Ending velocity of the basic action in km/h perpendicular to the direction of movement.")
    turning_angle: float = Field(
        ...,
        description="Turning angle in degrees (0–180) if turning left or right; otherwise 0.",
    )
    acceleration: float = Field(
        ...,
        description="Acceleration/deceleration of the actor in m/s^2 (None = inherited)",
    )

    # TODO: add validators (for example: ensure acceleration is 0 or none if start and end velocities are the same)

    # ------------------------------------------------------------------ #
    # pydantic validation                                                #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def check_acceleration_sign(self) -> "Action":
        if (self.start_longitudinal_velocity > self.end_longitudinal_velocity) and (
            self.acceleration >= 0.0
        ):
            raise ModelValidationError(
                "Ensure acceleration is negative when start_longitudinal_velocity is greater than end_longitudinal_velocity.",
                # "Ensure acceleration is negative when start_longitudinal_velocity > end_longitudinal_velocity."
                # "Acceleration must be less than zero when start_longitudinal_velocity > end_longitudinal_velocity",
                logger=logger,
            )
        elif (self.start_longitudinal_velocity < self.end_longitudinal_velocity) and (
            self.acceleration <= 0.0
        ):
            raise ModelValidationError(
                "Ensure acceleration is positive when start_longitudinal_velocity is smaller than end_longitudinal_velocity.",
                # "Acceleration must be greater than zero when start_longitudinal_velocity < end_longitudinal_velocity",
                logger=logger,
            )
        elif (self.start_longitudinal_velocity == self.end_longitudinal_velocity) and (
            self.acceleration not in [None, 0.0]
        ):
            self.acceleration = 0.0
            logger.warning(
                "Acceleration set to 0.0 m/s^2 because start and and longitudinal velocities are the same."
            )
        logger.debug("All pydantic validation for Action class passed successfully.")
        return self

    # TODO: The turning angle must be considered it is not concrete regarding the right or left
