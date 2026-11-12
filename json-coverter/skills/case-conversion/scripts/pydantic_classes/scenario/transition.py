from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


class TransitionType(str, Enum):
    SIMULATION_TIME = "simulation_time"
    TRAVELED_DISTANCE = "traveled_distance"
    # TIME_HEADWAY = "time_headway"
    # TIME_TO_COLLISION = "time_to_collision"
    ACHIEVED_SPEED = "achieved_speed"
    END_LONGITUDINAL_VELOCITY = "end_longitudinal_velocity"
    # SIMULATION_STOP = "stop"  # why comment this out?
    # Add more if needed (e.g., for on-road or special context)


class Transition(BaseClass):
    """
    Basic definition that defines the trigger of actions in maneuvers.
    - Can be used for both on-road and off-road vehicles.
    - Parameters specify criterion to move to the next action (distance/speed/time based...)
    """

    reference_name: str = Field(
        ..., description="Name of the referenced object element with 'name' as key."
    )
    # reference_id: str = Field(..., description="UID of the referenced object element with 'id' as key.")  # for now, provided by the LLM
    type: TransitionType = Field(
        ...,
        description="Type of trigger to transition from the current action to the next.",
    )
    value: Optional[float] = Field(
        None,
        description="Physical quantity associated with the type of transition - for example: number of seconds if transition type is 'simulation_time'.",
    )
    # TODO: add specifier for value (optional) -- like {value} {unit} {specifier} {reference_id} -- 50 m behind Vehicle_2

    # TODO: add validators

    # --------------------------------------------------------------------- #
    # Pydantic validator                                                    #
    # --------------------------------------------------------------------- #

    @model_validator(mode="after")
    def validate_transition(self) -> "Transition":
        """
        Validating the Transition class for GPT output
        """
        match self.type:
            case TransitionType.SIMULATION_TIME.value:
                if self.value in [None, 0.0]:
                    raise ModelValidationError(
                        # "If the type is 'simulation_time', ensure a realistic, plausible, nonzero value (in seconds).",
                        "Transition value MUST be nonzero and positive (in seconds) when transition type is 'simulation_time'.",
                        logger=logger,
                    )
            case TransitionType.TRAVELED_DISTANCE.value:
                if self.value in [None, 0.0]:
                    raise ModelValidationError(
                        # "If the type is 'traveled_distance', ensure a realistic, plausible, nonzero value (in meters).",
                        "Transition value MUST be nonzero and positive (in meters) when transition type is 'traveled_distance'.",
                        logger=logger,
                    )

        logger.debug(
            "All pydantic validation for Transition class passed successfully."
        )
        return self

    # TODO: add the validation with the reference name
