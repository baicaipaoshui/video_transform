from enum import Enum
from typing import Any, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


# --------------------------------------------------------------------------- #
# Enum definitions                                                            #
# --------------------------------------------------------------------------- #
class ShoulderType(str, Enum):
    """Surface category of a road shoulder."""

    PAVED = "paved"
    UNPAVED = "unpaved"
    GRAVEL = "gravel"
    VEGETATED = "vegetated"
    NONE = "none"


# --------------------------------------------------------------------------- #
# Data model                                                                  #
# --------------------------------------------------------------------------- #
class Shoulder(BaseClass):
    """
    Road shoulder attached to a RoadSegment.

    Only width and surface type are modelled for now.
    """

    # ------------------------------ Identification -------------------------- #
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="ID of the parent RoadSegment",
        # exclude=True
    )
    name: Optional[str] = Field(None, description="Optional name")
    description: Optional[str] = Field(None, description="Optional description")

    # ------------------------------ Geometry / semantics -------------------- #
    width: float = Field(
        ...,
        gt=0,
        le=10.0,
        description="Shoulder width in metres",
    )
    type: ShoulderType = Field(
        default=ShoulderType.PAVED.value,
        description="Surface type",
    )

    # ------------------------------------------------------------------ #
    # Pydantic post‑init hook                                            #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized Shoulder '{self.name}' (width={self.width} m, type={self.type})."
        )

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return f"name={self.name}, width={self.width} m, " f"type={self.type}"
