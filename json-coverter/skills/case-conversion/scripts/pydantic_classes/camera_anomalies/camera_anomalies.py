from typing import Any, List

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


class CameraAnomalies(BaseClass):
    """
    Root node of a *camera anomaly* description.

    Note
    ----
    Only the **id** of the Scene is stored.  The Scene object itself can be
    fetched on demand via ``get_scene()``, which resorts to the global
    `BaseClass` registry.
    """

    # ------------------------------------------------------------------ #
    # (Mandatory) road‑network reference                                   #
    # ------------------------------------------------------------------ #

    scene_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the Scene (road file) this scenario is based on",
    )

    # ------------------------------------------------------------------ #
    # Dynamic contents                                                   #
    # ------------------------------------------------------------------ #

    detection: List[str] = Field(
        default_factory=list,
        description="List of camera anomalies - hindrance or affected visibility of the camera",
        # exclude=True,
    )

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized Camera Anomalies with {len(self.detection)} detection(s)."
        )
