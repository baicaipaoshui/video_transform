from enum import Enum
from typing import Any, Optional, Union

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.action import Action
from mela_business_logic.scenario_preview.pydantic_classes.scenario.transition import (
    Transition,
    TransitionType,
)

from . import logger


# --------------------------------------------------------------------------- #
# Three independent enumerations – identify the maneuver "category" through  #
# the enum it belongs to.                                                    #
# --------------------------------------------------------------------------- #
class VehicleStateManeuverType(str, Enum):
    """
    High‑level maneuvers that only affect ego‑vehicle state.
    TODO: Off-road scenarios should choose from this enum!
    """

    MAINTAIN_SPEED = "maintain_speed"
    STOP = "stop"
    U_TURN = "u-turn"
    TURN = "turn"
    ACCELERATE = "accelerate"
    DECELERATE = "decelerate"
    REVERSE = "reverse"
    KEYHOLE_TURN = "keyhole_turn"
    # DRIVE = "drive"
    # WAIT = "wait"
    # PARK = "park"


class InfrastructureManeuverType(str, Enum):
    """Maneuvers that relate to road infrastructure."""

    # FOLLOW_LANE = "follow_lane"
    LANE_CHANGE = "lane_change"
    # TRAFFIC_LIGHT_STOP = "traffic_light_stop"
    # TRAFFIC_SIGN_STOP = "traffic_sign_stop"
    # MERGE = "merge"  # TODO: change to "enter"?  TODO: also re-add to maneuver types once supported in middleware(s)
    # EXIT = "exit"
    # SPLIT = "split"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    DRIVE_STRAIGHT = "drive_straight"


class ObjectRelatedManeuverType(str, Enum):
    """Maneuvers that depend on other moving objects."""

    OVERTAKE = "overtake"
    YIELD = "yield"
    # AVOID_COLLISION = "avoid_collision"
    FOLLOW_OBJECT = "follow_object"


class NonWheeledManeuverType(str, Enum):
    """Maneuvers typical for people or animals."""

    WALK = "walk"
    RUN = "run"
    WAIT = "wait"
    LIE_DOWN = "lie_down"
    SIT = "sit"
    CROUCH = "crouch"


# Convenient *union* type for the `type` field below
AnyManeuverType = Union[
    VehicleStateManeuverType,
    InfrastructureManeuverType,
    # ObjectRelatedManeuverType,
    NonWheeledManeuverType,
]


# --------------------------------------------------------------------------- #
# Maneuver                                                                    #
# --------------------------------------------------------------------------- #
class Maneuver(BaseClass):
    """
    Descriptive, high‑level maneuver segment that belongs to **one** Actor.

    Timing, triggers and detailed control are *not* held here; instead we
    store references (`action_ids`, `transition_ids`) which point to yet‑to‑be
    defined `Action` / `Transition` objects.
    """

    # -------------------------------- meta -------------------------------- #
    name: Optional[str] = Field(None, description="Human readable name")
    description: Optional[str] = Field(None, description="Optional comment")
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the Actor that owns this maneuver",
        # exclude=True,
    )

    # ------------------------ high‑level semantics ------------------------ #
    type: AnyManeuverType = Field(
        ...,
        description="Pre‑defined high‑level maneuver identifier "
        "(derived from one of the three category enums)",
    )

    action: Action = Field(
        ..., description="Description of the action associated with this maneuver."
    )
    transition: Transition = Field(
        ...,
        description="Description of the transition that terminates the action associated with this maneuver.",
    )

    # --------------------------------------------------------------------- #
    # Pydantic hook                                                         #
    # --------------------------------------------------------------------- #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized Maneuver {self.name} with id {self.id} (type={self.type})"
        )

    # ------------------------------------------------------------------ #
    # pydantic validation                                                #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def validate_motion_parameters(self) -> "Maneuver":
        match self.type:
            case VehicleStateManeuverType.MAINTAIN_SPEED.value:
                # 1) Force end velocity to equal start velocity
                if (
                    self.action.end_longitudinal_velocity
                    != self.action.start_longitudinal_velocity
                ):
                    logger.warning(
                        "For 'maintain_speed', changing end_longitudinal_velocity "
                        f"from {self.action.end_longitudinal_velocity} to {self.action.start_longitudinal_velocity}"
                    )
                    self.action.end_longitudinal_velocity = (
                        self.action.start_longitudinal_velocity
                    )
                # 2) Force acceleration to 0.0 (no error)
                if self.action.acceleration not in (None, 0.0):
                    logger.warning(
                        "For 'maintain_speed', changing acceleration "
                        f"from {self.action.acceleration} to 0.0"
                    )
                    self.action.acceleration = 0.0
                # 3) raise error if either start or end velocity is 0
                if self.action.start_longitudinal_velocity == 0.0:
                    # just need to check one velocity for 0.0 because they're set to the same in 1) anyway
                    raise ModelValidationError(
                        # "Ensure a non-zero positive value for start and end longitudinal velocities when type is 'maintain speed'.",
                        "Start and end longitudinal velocities MUST be nonzero and positive when maneuver type is 'maintain_speed'.",
                        logger=logger,
                    )

            case VehicleStateManeuverType.ACCELERATE.value:
                # 1) raise error if acceleration value is negative
                if self.action.acceleration is None or self.action.acceleration <= 0.0:
                    raise ModelValidationError(
                        # "Ensure a positive value for acceleration when type is 'accelerate'.",
                        "Acceleration MUST be nonzero and positive when maneuver type is 'accelerate'.",
                        logger=logger,
                    )
                # 2) raise error if start velocity is higher than end velocity
                if (
                    self.action.start_longitudinal_velocity
                    >= self.action.end_longitudinal_velocity
                ):
                    raise ModelValidationError(
                        # "Ensure end longitudinal velocity is greater than start longitudinal velocity when type is 'accelerate'.",
                        "End longitudinal velocity MUST be greater than start longitudinal velocity when maneuver type is 'accelerate'.",
                        logger=logger,
                    )

            case VehicleStateManeuverType.DECELERATE.value:
                # 1) opposite of accelerate, raise error if value is positive
                if self.action.acceleration is None or self.action.acceleration >= 0.0:
                    raise ModelValidationError(
                        "Ensure a negative value for acceleration when type is 'decelerate'.",
                        logger=logger,
                    )
                # 2) opposite of accelerate, raise error if end velocity is higher than start velocity
                if (
                    self.action.start_longitudinal_velocity
                    <= self.action.end_longitudinal_velocity
                ):
                    raise ModelValidationError(
                        "Ensure end longitudinal velocity is smaller than start longitudinal velocity when type is 'decelerate'.",
                        logger=logger,
                    )

            case VehicleStateManeuverType.STOP:
                # 1) set start longitudinal velocity to 0
                if self.action.start_longitudinal_velocity != 0.0:
                    self.action.start_longitudinal_velocity = 0.0
                    logger.warning(
                        f"For maneuver type 'stop', start longitudinal velocity was set to {self.action.start_longitudinal_velocity}."
                    )
                # 2) set end longitudinal velocity to 0
                if self.action.end_longitudinal_velocity != 0.0:
                    self.action.end_longitudinal_velocity = 0.0
                    logger.warning(
                        f"For maneuver type 'stop', end longitudinal velocity was set to {self.action.end_longitudinal_velocity}."
                    )
                # 3) set start lateral velocity to 0
                if self.action.start_lateral_velocity != 0.0:
                    self.action.start_lateral_velocity = 0.0
                    logger.warning(
                        f"For maneuver type 'stop', start lateral velocity was set to {self.action.start_lateral_velocity}."
                    )
                # 4) set acceleration to 0
                if self.action.acceleration not in [None, 0.0]:
                    self.action.acceleration = 0.0
                    logger.warning(
                        f"For maneuver type 'stop', acceleration was set to {self.action.start_longitudinal_velocity}."
                    )

            case InfrastructureManeuverType.LANE_CHANGE.value:
                # raise an error if lane change doesn't contain sideways movement (zero lateral velocity)
                if self.action.start_lateral_velocity in [None, 0.0]:
                    raise ModelValidationError(
                        "Ensure start lateral velocity is non-zero for type 'lane_change'.",
                        logger=logger,
                    )

        return self

    @model_validator(mode="after")
    def validate_action_transition_combination(self) -> "Maneuver":
        # 1) transition of achieved speed means end longitudinal velocity must have the same value
        if self.transition.type in [
            TransitionType.ACHIEVED_SPEED.value,
            TransitionType.END_LONGITUDINAL_VELOCITY.value,
        ]:
            if self.action.end_longitudinal_velocity != self.transition.value:
                logger.warning(
                    f"Setting transition value (type={self.transition.type}) to end longitudinal velocity value ({self.transition.value} → {self.action.end_longitudinal_velocity})."
                )
                self.transition.value = self.action.end_longitudinal_velocity
        return self

    @model_validator(mode="after")
    def validate_maneuver_transition_combination(self) -> "Maneuver":
        # 1) for maneuvers where actor stays still, no transition type other than simulation time makes sense
        if self.transition.type != TransitionType.SIMULATION_TIME.value:
            if self.type in [
                VehicleStateManeuverType.STOP.value,
                NonWheeledManeuverType.WAIT,
            ]:
                raise ModelValidationError(
                    "Transition type can only be simulation time for stationary maneuver types.",
                    logger=logger,
                )
        return self

    # --------------------------------------------------------------------- #
    # Dunder helpers                                                        #
    # --------------------------------------------------------------------- #
    def __str__(self) -> str:  # pragma: no cover
        return f"Maneuver(name={self.name}, type={self.type})"
