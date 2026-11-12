from enum import Enum
from typing import Any, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.asset import Asset
from mela_business_logic.scenario_preview.pydantic_classes.scene.junction import (
    Junction,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.off_road_surface import (
    OffRoadSurface,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.road import Road
from mela_business_logic.scenario_preview.pydantic_classes.scene.road_network import (
    RoadNetwork,
)

from . import logger


class SceneType(str, Enum):
    """Supported top‑level scene types."""

    # TODO: move this and all other enums to separate files
    # separate based on whether they are common or just for scenario/environment/etc.
    HIGHWAY = "highway"
    INTERSECTION = "intersection"
    OFF_ROAD = "off_road"


class Scene(BaseClass):
    """
    Root node of a simulation description.

    A ``Scene`` contains exactly one ``RoadNetwork``, optionally an
    ``OffRoadSurface`` (only meaningful for ``OFF_ROAD`` scenes) and an
    arbitrary list of additional ``Asset`` items.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    name: str = Field(..., description="Human readable scene name")
    type: SceneType = Field(
        ...,
        description="Scene topology / use‑case type",
    )
    description: Optional[str] = Field(
        None, description="Optional free‑form description"
    )

    # ------------------------------------------------------------------ #
    # Child nodes                                                        #
    # ------------------------------------------------------------------ #

    road_network: SkipJsonSchema[Optional[RoadNetwork]] = Field(
        default=None,
        description="Logical road network belonging to this scene",
        # exclude=True,
    )
    off_road_surface: SkipJsonSchema[Optional[OffRoadSurface]] = Field(
        default=None,
        description="Off‑road terrain surface (only for OFF_ROAD scenes)",
        # exclude=True,
    )
    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Finalize initialization (called by Pydantic v2)."""
        super().model_post_init(__context)

        # Ensure we always have a network ------------------------------ #
        if self.road_network is None:
            self.road_network = RoadNetwork(
                parent_id=self.id, name=f"{self.name}‑network"
            )
            logger.info(f"Created default RoadNetwork for Scene '{self.name}'.")

        # OFF‑ROAD consistency ----------------------------------------- #
        if self.type is SceneType.OFF_ROAD.value:
            if self.off_road_surface is None:
                self.off_road_surface = OffRoadSurface(parent_id=self.id)
                logger.info(f"Created default OffRoadSurface for Scene '{self.name}'.")

        logger.info(f"Initialized Scene '{self.name}' ({self.type})")
        logger.info("************************")
        logger.info(self.type)
        logger.info("************************")

    def validate_after_assembly(self) -> "Scene":
        if self.type == "off_road":
            self.__validate_off_road_surface_names()
        else:
            self.__validate_road_network_names()
        logger.info("All post assembly checks passed successfully.")
        return self

    def __validate_road_network_names(self) -> None:
        """
        Ensure:
        - road names are Road_1, Road_2, etc.
        - road segment names are Road_1_Seg_1, ..., Road_2_Seg_3, etc.
        - lane names are Road_1_Seg_1_Lane_1, ..., Road_2_Seg_3_Lane_5, etc.
        """
        # 1) road
        for road_idx, road in enumerate(self.road_network.roads):
            expected_road_name = f"Road_{road_idx+1}"
            # road name correction
            if road.name != expected_road_name:
                logger.warning(
                    f"Changing road name: {road.name} → {expected_road_name}."
                )
                road.name = expected_road_name
            for segment_idx, segment in enumerate(road.segments):
                # segment name correction
                expected_segment_name = f"{road.name}_Seg_{segment_idx+1}"
                if segment.name != expected_segment_name:
                    logger.warning(
                        f"Changing segment name: {segment.name} → {expected_segment_name}."
                    )
                    segment.name = expected_segment_name
                # lane name correction
                for lane_idx, lane in enumerate(segment.lanes):
                    expected_lane_name = f"{segment.name}_Lane_{lane_idx+1}"
                    if lane.name != expected_lane_name:
                        logger.warning(
                            f"Changing lane name: {lane.name} → {expected_lane_name}."
                        )
                        lane.name = expected_lane_name
                # left/right shoulder/sidewalk name correction
                for road_side_type in ["shoulder", "sidewalk"]:
                    for side in ["left", "right"]:
                        road_side_object = getattr(segment, f"{side}_{road_side_type}")
                        if road_side_object:
                            expected_name = f"{segment.name}_{side.capitalize()}_{road_side_type.capitalize()}"
                            if road_side_object.name != expected_name:
                                logger.warning(
                                    f"Changing {side} {road_side_type} name: {road_side_object.name} → {expected_name}."
                                )
                                setattr(road_side_object, "name", expected_name)

        # 2) junction
        for junction_idx, junction in enumerate(self.road_network.junctions):
            # junction name correction
            expected_junction_name = f"Junction_{junction_idx+1}"
            if junction.name != expected_junction_name:
                logger.warning(
                    f"Changing junction name: {junction.name} → {expected_junction_name}."
                )
                junction.name = expected_junction_name

    def __validate_off_road_surface_names(self) -> None:
        """
        Ensure:
        - terrain patch names are TerrainPatch_1, TerrainPatch_2, etc.
        - same for border features
        - same for path features
        """
        # 1) terrain patches
        for terrain_patch_idx, terrain_patch in enumerate(
            self.off_road_surface.terrain_patches
        ):
            expected_terrain_patch_name = f"TerrainPatch_{terrain_patch_idx+1}"
            if terrain_patch.name != expected_terrain_patch_name:
                logger.warning(
                    f"Changing terrain patch name: {terrain_patch.name} → {expected_terrain_patch_name}."
                )
                terrain_patch.name = expected_terrain_patch_name
        # 2) border features
        for border_feature_idx, border_feature in enumerate(
            self.off_road_surface.border_features
        ):
            expected_border_feature_name = f"BorderFeature_{border_feature_idx+1}"
            if border_feature.name != expected_border_feature_name:
                logger.warning(
                    f"Changing border feature name: {border_feature.name} → {expected_border_feature_name}."
                )
                border_feature.name = expected_border_feature_name
        # 3) path features
        for path_feature_idx, path_feature in enumerate(
            self.off_road_surface.path_features
        ):
            expected_path_feature_name = f"PathFeature_{path_feature_idx+1}"
            if path_feature.name != expected_path_feature_name:
                logger.warning(
                    f"Changing path feature name: {path_feature.name} → {expected_path_feature_name}."
                )
                path_feature.name = expected_path_feature_name

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #

    # ---- Update road network ----------------------------------------- #
    def update_road_network(self, road_network: RoadNetwork):
        """
        Attach *road network* to this scene.
        """
        road_network.parent_id = self.id
        self.road_network = road_network
        logger.info("Updated scene with new road network.")

    # ---- Update off-road surface-------------------------------------- #
    def update_off_road_surface(self, off_road_surface: OffRoadSurface):
        """
        Attach *off-road surface* to this scene.
        """
        off_road_surface.parent_id = self.id
        self.off_road_surface = off_road_surface
        logger.info("Updated scene with new off-road surface.")

    # ---- Asset management -------------------------------------------- #

    def add_asset(self, asset: Asset) -> None:
        """
        Attach *asset* to this scene.
        """
        asset.parent_id = self.id
        self.assets.append(asset)
        logger.info(f"Added Asset '{asset.name or asset.id}' to Scene '{self.name}'.")

    def get_assets(self) -> List[Asset]:
        """Return a **copy** of the asset list to protect internal state."""
        return self.assets.copy()

    # ---- Convenience pass‑through ----------------------------------- #

    def get_roads(self) -> List[Road]:
        """Proxy accessor to the scene’s road list."""
        return self.road_network.get_roads()

    def get_junctions(self) -> List[Junction]:
        """Proxy accessor to the scene’s junction list."""
        return self.road_network.get_junctions()

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Scene(name={self.name}, "
            f"type={self.type.value}, "
            f"roads={len(self.road_network.roads)}, "
            f"junctions={len(self.road_network.junctions)}, "
            f"assets={len(self.assets)})"
        )
