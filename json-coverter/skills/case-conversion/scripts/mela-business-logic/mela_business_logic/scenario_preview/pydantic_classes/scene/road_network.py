from enum import Enum
from typing import Any, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError, ProcessingError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.asset import Asset
from mela_business_logic.scenario_preview.pydantic_classes.scene.junction import (
    Junction,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.road import Road

from . import logger


class TrafficType(str, Enum):
    RIGHT_HAND_TRAFFIC = "right_hand_traffic"
    LEFT_HAND_TRAFFIC = "left_hand_traffic"


class Country(str, Enum):
    JAPAN = "japan"
    USA = "usa"
    GERMANY = "germany"
    AUSTRIA = "austria"
    KOREA = "korea"
    GREAT_BRITAIN = "great_britain"
    FRANCE = "france"
    CHINA = "china"
    UNKNOWN = "unknown"


class RoadNetwork(BaseClass):
    """
    A container that groups all `Road` and `Junction` objects belonging to one
    `Scene`.  It owns the elements – therefore their ``parent_id`` must be this
    network’s id – and offers helpers to add / remove / query them.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="ID of the parent Scene",
        # exclude=True
    )
    name: Optional[str] = Field(None, description="Human readable network name")
    description: Optional[str] = Field(None, description="Optional description")
    traffic_type: Optional[TrafficType] = Field(
        default=None,
        description="Right-hand-traffic or Left-hand-traffic depending on the traffic type",
    )
    country: Optional[Country] = Field(
        default=None,
        description="Country name from the given input text/image/video cues only from enums",
    )

    # ------------------------------------------------------------------ #
    # Elements                                                            #
    # ------------------------------------------------------------------ #

    roads: List[Road] = Field(
        default_factory=list,
        description="Roads that belong to this network",
        # exclude=True,
    )
    junctions: List[Junction] = Field(
        default_factory=list,
        description="Junctions that belong to this network",
        # exclude=True,
    )
    # assets: List[Asset] = Field(
    #     default_factory=list,
    #     description="Generic assets or/static objects (buildings, vegetation…)",
    #     # exclude=True,
    # )

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Finalize object after Pydantic base validation."""
        super().model_post_init(__context)

        logger.info(
            f"Initialized RoadNetwork '{self.name or self.id}' containing {len(self.roads)} road(s) and {len(self.junctions)} junction(s)."
        )
        # TODO: is this function a placeholder for now? no validation is taking place

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def add_road(self, road: Road) -> None:
        """
        Register *road* with this network.
        """
        self._register_element(road, container="roads")

    def add_junction(self, junction: Junction) -> None:
        """Register *junction* with this network."""
        self._register_element(junction, container="junctions")

    def add_asset(self, asset: Asset) -> None:
        self._register_element(asset, container="assets")

    # ---- Getters (return **copies** to protect internal state) -------- #

    def get_roads(self) -> List[Road]:
        return self.roads.copy()

    def get_junctions(self) -> List[Junction]:
        return self.junctions.copy()

    # ---- Validation stubs -------------------------------------------- #

    def validate_connectivity(self) -> None:
        """
        Placeholder for a topological validation routine.
        """
        logger.warning("validate_connectivity() not implemented yet.")
        # TODO: implement actual checks
        return

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _register_element(self, element: BaseClass, *, container: str) -> None:
        """
        Shared logic for `add_road` / `add_junction`.

        Parameters
        ----------
        element
            The child object to register.
        container
            Either the string ``"roads"`` or ``"junctions"``.
        """
        # TODO: can this be moved to BaseClass, and used by scene to add asset, for example?
        # might need a more generic name than register element in that case, since element means road/junction here.
        elements: List[BaseClass] = getattr(self, container)

        # Check duplicates
        if any(el.id == element.id for el in elements):
            logger.warning(
                f"Duplicate id '{element.id}' detected while adding {element.name} to RoadNetwork '{self.id}'."
            )
            return

        # All good – finalise registration
        # TODO: is it ok to comment the line below out?
        # because when you add an element you HAVE to initialize with a parent id anyway, so this would just be resetting the value to the already valid one
        element.parent_id = self.id  # type: ignore[attr-defined]
        elements.append(element)
        logger.info(
            f"Added {element.__class__.__name__} '{element.name}' to RoadNetwork '{self.name or self.id}'."
        )

    # ------------------------------------------------------------------ #
    # Validation helpers                                                 #
    # ------------------------------------------------------------------ #

    def validate_after_assembly(self, scene_type: str) -> "RoadNetwork":
        self.__validate_junction_existence(scene_type)
        self.__validate_minimum_num_roads_based_on_num_connections()
        logger.info("All post assembly checks passed successfully.")
        return self

    def __validate_junction_existence(self, scene_type) -> None:
        """
        Ensure at least one junction is present in the road network of the intersection
        """
        if scene_type == "intersection":
            if len(self.junctions) == 0:
                raise ModelValidationError(
                    "AT LEAST ONE junction MUST be present in the road network of an intersection.",
                    logger=logger,
                )

    def __validate_minimum_num_roads_based_on_num_connections(self) -> None:
        """
        Ensure minimum number of roads based on number of junctions/junction type present.
        """
        minimum_connections = 0
        for junction in self.junctions:
            minimum_connections += junction.num_connections

        if minimum_connections < 3:  # no junction present, not even T-junction
            # no constraint on the number of roads
            pass
        if minimum_connections == 3:  # t-junction present
            if len(self.roads) < 3:
                raise ModelValidationError(
                    "A road network with a T-junction MUST contain 3 roads."
                )
        if minimum_connections == 4:  # regular junction present
            if len(self.roads) < 4:
                raise ModelValidationError(
                    "A road network with a regular junction MUST contain 4 roads."
                )
        if minimum_connections > 4:  # multiple junctions present
            if len(self.roads) < 5:
                raise ModelValidationError(
                    "A road network with multiple junctions MUST contain MORE THAN 4 roads."
                )

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"RoadNetwork(name={self.name}, "
            f"roads={len(self.roads)}, "
            f"junctions={len(self.junctions)})"
        )

    # ------------------------------------------------------------------ #
    # Country to traffic sign utilities                                  #
    # ------------------------------------------------------------------ #

    def set_country(self, country: str) -> "RoadNetwork":
        earlier_value = self.country

        def strip_characters(country: str):
            return country.replace(" ", "").replace("_", "").replace("-", "").lower()

        if strip_characters(country) in [
            "us",
            "usa",
            "unitedstates",
            "unitedstatesofamerica",
            "america",
        ]:
            self.country = Country.USA.value
        elif strip_characters(country) in [
            "uk",
            "gb",
            "gbr",
            "unitedkingdom",
            "greatbritain",
            "england",
            "wales",
            "scotland",
        ]:
            self.country = Country.GREAT_BRITAIN.value
        elif strip_characters(country) in ["jp", "japan"]:
            self.country = Country.JAPAN.value
        elif strip_characters(country) in ["de", "germany", "deutschland"]:
            self.country = Country.GERMANY.value
        elif strip_characters(country) in [
            "at",
            "austria",
            "oesterreich",
            "österreich",
        ]:
            self.country = Country.AUSTRIA.value
        elif strip_characters(country) in ["fr", "france"]:
            self.country = Country.FRANCE.value
        elif strip_characters(country) in ["cn", "china"]:
            self.country = Country.CHINA.value
        elif strip_characters(country) in ["kr", "korea", "southkorea"]:
            self.country == Country.KOREA.value
        # add more countries here...
        else:
            # processing error because it shouldn't be caught by retry mechanism
            raise ProcessingError(f"Couldn't set country '{country}'.", logger=logger)
        if self.country != earlier_value:
            logger.warning(
                f"Country attribute overwritten by camera config. Was {earlier_value}, set to {self.country}."
            )
        return self

    def set_traffic_type_based_on_country(self) -> "RoadNetwork":
        earlier_value = self.traffic_type
        left_hand_traffic_countries = [
            Country.GREAT_BRITAIN.value,
            Country.JAPAN.value,
            # add more countries here...
        ]
        self.traffic_type = TrafficType.RIGHT_HAND_TRAFFIC.value
        if self.country in left_hand_traffic_countries:
            self.traffic_type = TrafficType.LEFT_HAND_TRAFFIC.value
        if self.traffic_type != earlier_value:
            logger.warning(
                f"Traffic type attribute overwritten by camera config. Was {earlier_value}, set to {self.traffic_type}."
            )
        return self
