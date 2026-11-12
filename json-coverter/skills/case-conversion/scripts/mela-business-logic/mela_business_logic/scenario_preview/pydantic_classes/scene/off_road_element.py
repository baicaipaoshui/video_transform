from typing import List, Optional, Tuple

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)

from . import logger


class OffRoadElement(BaseClass):
    """
    Common superclass of *all* objects that live on an `OffRoadSurface`.

    Positioning variants
    --------------------
    1. pose        – `OrientedPoint` (relative, free-form)
    2. grid_cells  – list[(row, col)] indices into the owning surface’s
                     `CheckerboardGrid`.  The *order* in the list **is
                     significant** (first → last cell).

    Exactly **one** of the two variants must be provided.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #
    parent_id: SkipJsonSchema[str] = Field(..., description="UID of OffRoadSurface")
    name: Optional[str] = Field(None, description="Human readable name")
    description: Optional[str] = Field(None, description="Optional free-form text")

    # ------------------------------------------------------------------ #
    # Positioning variants                                               #
    # ------------------------------------------------------------------ #
    pose: Optional[OrientedPoint] = Field(
        None, description="Relative pose (see oriented_point.py)"
    )
    grid_cells: SkipJsonSchema[Optional[List[Tuple[int, int]]]] = Field(
        None,
        min_length=1,
        description="Sequence of grid cell indices (row, col)",
    )

    # ------------------------------------------------------------------ #
    # Validation – ensure **exactly one** variant is used                #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _check_positioning_variant(self) -> "OffRoadElement":
        uses_pose = self.pose is not None
        uses_grid = self.grid_cells is not None
        if uses_pose == uses_grid:  # both True or both False
            raise ModelValidationError(
                f"{self.__class__.__name__}: provide *either* 'pose' *or* "
                "'grid_cells', but not both.",
                logger=logger,
            )
        return self

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #
    def __str__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(name={self.name}, id={self.id})"
