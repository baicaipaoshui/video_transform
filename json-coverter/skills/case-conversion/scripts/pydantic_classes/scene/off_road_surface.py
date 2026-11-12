from typing import Any, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.asset import Asset
from mela_business_logic.scenario_preview.pydantic_classes.scene.border_feature import (
    BorderFeature,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.checkerboard import (
    CheckerboardGrid,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.path_feature import (
    PathFeature,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.terrain_patch import (
    TerrainPatch,
)

from . import logger


class OffRoadSurface(BaseClass):
    """
    Top-level container that owns all off-road ground elements of a
    Scene (comparable to RoadNetwork on the on-road).
    """

    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the parent Scene",
        # exclude=True,
    )
    name: Optional[str] = Field(None, description="Human readable network name")
    description: Optional[str] = Field(None, description="Optional description")

    # -------- checkerboard grid --------------------------------------- #
    grid: CheckerboardGrid | None = Field(
        None,
        description="Optional checkerboard grid that terrain patches can reference",
    )

    # -------- child collections --------------------------------------- #

    terrain_patches: List[TerrainPatch] = Field(
        default_factory=list,
        description="Homogeneous ground pieces",
        # exclude=True,
    )
    border_features: List[BorderFeature] = Field(
        default_factory=list,
        description="Edges framing the terrain",
        # exclude=True,
    )
    assets: List[Asset] = Field(
        default_factory=list,
        description="Localized static objects (tree group, pole …)",
        # exclude=True,
    )

    path_features: List[PathFeature] = Field(
        default_factory=list, description="Special ground for off-road paths"
    )

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(
            f"Initialized OffRoadSurface '{self.name}' with {len(self.terrain_patches)} patch(es), "
            f"{len(self.border_features)} border feature(s) and {len(self.assets)} asset(s).",
        )

    # ------------------------------------------------------------------ #
    # Public API – add helpers                                           #
    # ------------------------------------------------------------------ #
    def add_terrain_patch(self, patch: TerrainPatch) -> None:
        self._register_element(patch, container="terrain_patches")

    def add_border_feature(self, feature: BorderFeature) -> None:
        self._register_element(feature, container="border_features")

    def add_asset(self, asset: Asset) -> None:
        self._register_element(asset, container="assets")

    def add_path_feature(self, path: PathFeature) -> None:  # NEW
        self._register_element(path, container="path_features")

    # ---- Getters (return *copies* to protect internal state) ---------- #
    def get_terrain_patches(self) -> List[TerrainPatch]:
        return self.terrain_patches.copy()

    def get_border_features(self) -> List[BorderFeature]:
        return self.border_features.copy()

    def get_assets(self) -> List[Asset]:
        return self.assets.copy()

    def get_path_features(self) -> List[PathFeature]:
        return self.path_features.copy()

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #
    def _register_element(self, element: BaseClass, *, container: str) -> None:
        elements: List[BaseClass] = getattr(self, container)

        if any(el.id == element.id for el in elements):
            logger.warning(
                f"Duplicate id '{element.id}' while adding {element.__class__.__name__} to OffRoadSurface '{self.id}'."
            )
            return

        element.parent_id = self.id  # type: ignore[attr-defined]
        elements.append(element)
        logger.info(
            f"Added {element.__class__.__name__} '{element.name or element.id}' to OffRoadSurface '{self.name or self.id}'."
        )

    # ------------------------------------------------------------------ #
    # Validation stubs – to be implemented later                         #
    # ------------------------------------------------------------------ #
    def validate_consistency(self) -> None:
        """
        Placeholder for overlap / connectivity checks.
        """
        logger.warning("OffRoadSurface.validate_consistency() not implemented yet.")
