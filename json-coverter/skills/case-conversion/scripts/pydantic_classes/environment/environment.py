from enum import Enum
from typing import Any, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


class Daytime(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class SunPosition(str, Enum):
    IN_FRONT_OF_EGO = "in_front_of_ego"
    LEFT_OF_EGO = "left_of_ego"
    RIGHT_OF_EGO = "right_of_ego"
    REAR_OF_EGO = "rear_of_ego"
    # EAST_OF_EGO = "east_of_ego"
    # WEST_OF_EGO = "west_of_ego"
    # NORTH_OF_EGO = "north_of_ego"
    # SOUTH_OF_EGO = "south_of_ego"
    # NORTH_EAST_OF_EGO = "north_east_of_ego"
    # SOUTH_EAST_OF_EGO = "south_east_of_ego"
    # SOUTH_WEST_OF_EGO = "south_west_of_ego"
    # NORTH_WEST_OF_EGO = "north_west_of_ego"
    # OVERHEAD_OF_EGO = "overhead_of_ego"


class Weather(str, Enum):
    CLEAR = "clear"
    CLOUDS = "clouds"
    OVERCAST = "overcast"
    RAIN = "rain"
    STRONG_RAIN = "strong_rain"
    SNOW = "snow"
    STRONG_SNOWFALL = "strong_snowfall"
    DUST = "dust"
    STRONG_DUST = "strong_dust"
    FOG = "fog"
    STRONG_FOG = "strong_fog"
    SLEET = "sleet"
    THUNDERSTORM = "thunderstorm"


class Topology(str, Enum):
    HILLY = "hilly"
    MOUNTAINOUS = "mountainous"
    COASTAL = "coastal"
    DESERT = "desert"
    FLAT = "flat"
    # add more...


class Season(str, Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Environment(BaseClass):
    """
    Root node of an *environment* description.

    An environment consists of the following:
    ----------
    • daytime
    • sun position
    • weather
    • topology
    • season

    Note
    ----
    Only the **id** of the Scene is stored.  The Scene object itself can be
    fetched on demand via ``get_scene()``, which resorts to the global
    `BaseClass` registry.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    name: str = Field(..., description="Human readable environment name")
    description: Optional[str] = Field(
        None, description="Optional free‑form description"
    )

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

    daytime: Daytime = Field(..., description="The time of day")
    sun_position: Optional[SunPosition] = Field(
        ...,
        description="Position of the sun relative to the ego vehicle, check the shadows of the other objects and mention the sun direction, or null if unknown or at night",
    )
    weather: Weather = Field(..., description="Weather conditions")
    topology: Topology = Field(..., description="Topology of the scenario")
    season: Season = Field(..., description="Season of the scenario")

    # ------------------------------------------------------------------ #
    # Pydantic hook                                                      #
    # ------------------------------------------------------------------ #
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        super().model_post_init(__context)
        logger.info(f"Initialized Environment '{self.name}'.")

    # ------------------------------------------------------------------ #
    # pydantic validator                                                 #
    # ------------------------------------------------------------------ #
    # TODO:

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #
    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Environment(name={self.name}, "
            f"daytime={self.daytime}, "
            f"sun_position={len(self.sun_position)}, "
            f"topology={len(self.topology)}, "
            f"season={len(self.season)})"
        )
