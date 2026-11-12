from pydantic import BaseModel, Field

from mela_business_logic.scenario_preview.pydantic_classes.scene.oriented_point import (
    OrientedPoint,
)


class CheckerboardGrid(BaseModel):
    """
    Simple square grid that subdivides an OffRoadSurface.


    • (0, 0) refers to the *origin* cell.
    • Cell centres are spaced `cell_size` metres apart along X/Y.
    """

    cell_size: float = Field(..., gt=0.0, description="Edge length of one cell [m]")
    origin: OrientedPoint = Field(
        ...,
        description="Pose of the grid's (0,0) cell centre, given *relatively*.",
    )
    orientation: float = Field(
        0.0,
        ge=-360.0,
        le=360.0,
        description="Rotation of the grid in ° (0° = aligned with +X).",
    )
