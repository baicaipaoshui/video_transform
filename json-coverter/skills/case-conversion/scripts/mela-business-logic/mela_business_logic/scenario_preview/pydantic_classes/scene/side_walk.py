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
class SidewalkMaterial(str, Enum):
    """Surface material used for a sidewalk."""

    CONCRETE = "concrete"
    ASPHALT = "asphalt"
    STONE = "stone"
    DIRT = "dirt"
    NONE = "none"  # placeholder: no sidewalk / grass strip, etc.


# --------------------------------------------------------------------------- #
# Data model                                                                  #
# --------------------------------------------------------------------------- #
class Sidewalk(BaseClass):
    """
    Sidewalk attached to a RoadSegment.

    Only basic geometric and surface properties are modelled for now.
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
        description="Sidewalk width in metres",
    )
    material: SidewalkMaterial = Field(
        default=SidewalkMaterial.CONCRETE,
        description="Surface material",
    )

    # ------------------------------------------------------------------ #
    # Pydantic post‑init hook                                            #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized Sidewalk '{self.name or self.id}' (width={self.width} m, material={self.material})."
        )

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return f"name={self.name}, width={self.width} m, " f"material={self.material}"
