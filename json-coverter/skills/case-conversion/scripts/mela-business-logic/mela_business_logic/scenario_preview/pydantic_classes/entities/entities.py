from typing import Any, List

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.asset import Asset
from mela_business_logic.scenario_preview.pydantic_classes.scene.infrastructure import (
    Crosswalk,
    TrafficLight,
    TrafficSign,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.scene import Scene

from . import logger


class Entities(BaseClass):
    """

    A Entity
    ----------
    • asset holding class

    """

    # ------------------------------------------------------------------ #
    # Mandatory road‑network reference                                   #
    # ------------------------------------------------------------------ #

    scene_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the Scene (road file) this scenario is based on",
    )

    # ------------------------------------------------------------------ #
    # Dynamic contents                                                   #
    # ------------------------------------------------------------------ #

    assets: List[Asset] = Field(
        default_factory=list,
        description="List of Assets that present in the Scene",
        # exclude=True,
    )

    traffic_lights: List[TrafficLight] = Field(
        default_factory=list,
        description="List of Traffic lights that present in the Scene",
    )

    traffic_signs: List[TrafficSign] = Field(
        default_factory=list,
        description="List of Traffic signs that present in the Scene",
    )

    crosswalks: List[Crosswalk] = Field(
        default_factory=list,
        description=(
            "Optional detailed crosswalk metadata for this asset, including type "
            "(e.g., zebra, ladder) and its exact position/orientation. "
            "Present only if this asset represents a pedestrian crossing."
        ),
    )

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """
        Post‑initialization: *no* validation against the Scene registry,
        merely log what we have.
        """
        super().model_post_init(__context)
        num_assets = len(self.assets)
        num_traffic_lights = len(self.traffic_lights)
        num_traffic_signs = len(self.traffic_signs)
        num_crosswalks = len(self.crosswalks)
        logger.info(
            f"Initialized Entity (scene_id={self.scene_id}, assets={num_assets}), traffic lights={num_traffic_lights}., traffic signs={num_traffic_signs}., and crosswalks={num_crosswalks}"
        )

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #

    # ---- Scene handling ---------------------------------------------- #

    def get_scene(self) -> Scene:
        """
        Retrieve the Scene from the global registry or raise `NotFoundError`.
        """
        return Scene.get_by_id(self.scene_id)

    # ---- Entities management -------------------------------------------- #

    def add_asset(self, asset: Asset) -> None:
        """
        Register an `Asset` with this Entity.
        """
        asset.parent_id = self.id
        self.assets.append(asset)
        asset_name = getattr(asset, "name")
        logger.info(
            f"Added '{asset.__class__.__name__}' '{asset_name}' to Entities (total={len(self.assets)})."
        )

    def get_assets(self) -> List[Asset]:
        """
        Return a **copy** of the asset list to protect internal state.
        """
        return self.assets.copy()

    def add_traffic_lights(self, traffic_light: TrafficLight) -> None:
        """
        Register an `TrafficLight` with this Entity.
        """
        traffic_light.parent_id = self.id
        self.traffic_lights.append(traffic_light)
        asset_name = getattr(traffic_light, "name")
        logger.info(
            f"Added '{traffic_light.__class__.__name__}' '{asset_name}' to Entities (total={len(self.traffic_lights)})."
        )

    def get_traffic_lights(self) -> List[TrafficLight]:
        """
        Return a **copy** of the asset list to protect internal state.
        """
        return self.traffic_lights.copy()

    def add_traffic_signs(self, traffic_sign: TrafficSign) -> None:
        """
        Register an `Asset` with this Entity.
        """
        traffic_sign.parent_id = self.id
        self.traffic_signs.append(traffic_sign)
        asset_name = getattr(traffic_sign, "name")
        logger.info(
            f"Added '{traffic_sign.__class__.__name__}' '{asset_name}' to Entities (total={len(self.traffic_signs)})."
        )

    def get_traffic_signs(self) -> List[TrafficSign]:
        """
        Return a **copy** of the asset list to protect internal state.
        """
        return self.traffic_signs.copy()

    def add_crosswalks(self, crosswalk: Crosswalk) -> None:
        """
        Register an `Asset` with this Entity.
        """
        crosswalk.parent_id = self.id
        self.crosswalks.append(crosswalk)
        asset_name = getattr(crosswalk, "name")
        logger.info(
            f"Added '{crosswalk.__class__.__name__}' '{asset_name}' to Entities (total={len(self.crosswalks)})."
        )

    def get_crosswalks(self) -> List[Crosswalk]:
        """
        Return a **copy** of the asset list to protect internal state.
        """
        return self.crosswalks.copy()

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"scene_id={self.scene_id}, "
            f"assets={len(self.assets)})"
            f"traffic lights={len(self.traffic_lights)})"
            f"traffic signs={len(self.traffic_signs)})"
            f"crosswalks={len(self.crosswalks)})"
        )
