"""
OrientedPoint encodes relative position and orientation information.
There are no absolute coordinates here – the translation to world
coordinates is done later by whoever needs it (simulation backend,
preview tool, …).
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from mela_business_logic.error import ModelValidationError

from . import logger

# TODO: considering parsing schema into json


class PositionSpecifier(str, Enum):
    """
    Interpretation of OrientedPoint.position.


    The wording is chosen so that the specifier reads nicely together
    with the distance/offset in natural language, e.g.

        • 20 m LEFT_OF <reference>
        • 10 m AHEAD_OF <reference>
        • (-5, +3) OFFSET_BY <reference>
    """

    # OFFSET_BY = "offset_by"  # (dx, dy) in global frame
    # OFFSET_ALONG_BY = "offset_along_by"  # +s along reference heading
    # TODO: need to change the specifiers from ahead_off, behind_of (after, before)
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    AHEAD_OF = "ahead_of"
    BEHIND = "behind"


class OrientationSpecifier(str, Enum):
    """
    Interpretation of OrientedPoint.angle.


    Example:
        FACING_TOWARDS  <ref>  + 0°   # look exactly at the reference
        FACING_AWAY_FROM <ref>  +30°   # look away but 30° to the left …
    """

    FACING_TOWARDS = "facing_towards"
    FACING_AWAY_FROM = "facing_away_from"


class RelativePosition(BaseModel):
    """
    Relative position w. r. t. reference_id.


    value:
        • OFFSET_*         → (dx, dy)  tuple   [m, m]
        • LEFT/RIGHT/…     → distance  float   [m]
    """

    reference_name: str = Field(
        ..., description="Name of the referenced object element with 'name' as key."
    )
    # reference_id: uuid = Field(..., description= "UID of the referenced object element with 'id' as key.")  # for now, provided by the LLM
    longitudinal_specifier: PositionSpecifier = Field(
        ..., description="Longitudinal distance direction from the reference"
    )
    longitudinal_value: float = Field(
        ..., description="Longitudinal value direction from the reference in meters"
    )
    longitudinal_unit: Literal["m"] = Field(..., description="Unit: always meters")

    lateral_specifier: PositionSpecifier = Field(
        ..., description="Lateral distance direction from the reference"
    )
    lateral_value: float = Field(
        ...,
        description="Lateral distance value from the reference in meters or lanes depending on the on-road or off-road scenario",
    )
    lateral_unit: Literal["m", "lane"] = Field(
        ...,
        description=(
            "Unit: "
            'For on-road, use "lane" if the reference is a vehicle,'
            ' otherwise use "m" (meters).'
            ' For off-road, always use "m".'
        ),
    )
    # value: Union[Tuple[float, float], float] = Field(..., description="If specifier is an offset (e.g. OFFSET_*), set value to (dx, dy) as tuple [m, m]. "
    #         "If specifier is LEFT_OF, RIGHT_OF, AHEAD_OF, or BEHIND, set value to float (distance in meters).")

    # Allow writing `value=5.0` for the scalar distance-based specifiers
    # @field_validator("value")
    # def _validate_value(cls, v, values):  # noqa: N805
    #     spec = values.data.get("specifier")
    #     if spec in {
    #         PositionSpecifier.AHEAD_OF,
    #         PositionSpecifier.BEHIND,
    #         PositionSpecifier.LEFT_OF,
    #         PositionSpecifier.RIGHT_OF,
    #     }:
    #         return float(v)
    #     return tuple(v)  # works for already-tuple as well

    # ------------------------------------------------------------------ #
    # pydantic validation                                                #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def validate_longitudinal_and_lateral_specifiers(self):
        if self.longitudinal_specifier in [
            PositionSpecifier.LEFT_OF.value,
            PositionSpecifier.RIGHT_OF.value,
        ]:
            raise ModelValidationError(
                f"Ensure that the longitudinal_specifier is either {PositionSpecifier.AHEAD_OF.value} or {PositionSpecifier.BEHIND.value}. Do not allow any other option.",
                logger=logger,
            )

        if self.lateral_specifier in [
            PositionSpecifier.AHEAD_OF.value,
            PositionSpecifier.BEHIND.value,
        ]:
            raise ModelValidationError(
                f"Ensure that the longitudinal_specifier is either {PositionSpecifier.LEFT_OF.value} or {PositionSpecifier.RIGHT_OF.value}. Do not allow any other option.",
                logger=logger,
            )
        return self


class RelativeOrientation(BaseModel):
    reference_name: str = Field(
        ..., description="Name of the reference element"
    )  # TODO: also convert to reference_name?
    specifier: OrientationSpecifier
    offset_angle: float = Field(
        0.0,
        description="Additional yaw offset in degrees (applied after the specifier rule), as objects are not always perfectly alligned",
    )


class OrientedPoint(BaseModel):
    """
    A full 2-D pose expressed relatively.
    """

    position: RelativePosition
    orientation: RelativeOrientation
