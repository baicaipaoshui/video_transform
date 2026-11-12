from enum import Enum

from pydantic import Field

from mela_business_logic.scenario_preview.pydantic_classes.scenario.actor import Actor


class Color(str, Enum):
    """Basic colour palette – keep it tiny and unambiguous."""

    WHITE = "white"
    BLACK = "black"
    GREY = "grey"
    SILVER = "silver"
    RED = "red"
    BLUE = "blue"
    YELLOW = "yellow"
    GREEN = "green"
    BROWN = "brown"
    ORANGE = "orange"


class VehicleClass(str, Enum):
    """Coarse vehicle categories that most traffic simulators distinguish."""

    SEDAN = "sedan"
    SUV = "suv"
    TRUCK = "truck"
    BUS = "bus"
    MOTORCYCLE = "motorcycle"
    BICYCLE = "bicycle"
    VAN = "van"
    TRACTOR = "tractor"
    PICKUP = "pickup"


class VehicleAttachment(str, Enum):
    """All types of vehicle attachments specially for off-road vehicles"""

    BOX = "box"
    PLOUGH = "plough"
    HARROW = "harrow"
    CULTIVATOR = "cultivator"
    ROTAVATOR = "rotavator"
    # SEED_DRILL = "seed_drill"
    # PLANTER = "planter"
    # FERTILIZER_SPREAD = "fertilizer_spreader"
    # SPRAYER = "sprayer"
    # REAPER = "reaper"
    MOWER = "mower"
    BALER = "baler"
    TRAILER = "trailer"
    BACKHOE = "backhoe"
    POST_HOLE_DIGGER = "post_hole_digger"
    DOZER_BLADE = "dozer_blade"
    # LEVELER = "leveler"
    # MULCHER = "mulcher"
    THRESHER = "thresher"


class Vehicle(Actor):
    """Motorised or muscle‑powered road vehicle."""

    vehicle_class: VehicleClass = Field(
        default=VehicleClass.SEDAN, description="High‑level vehicle category"
    )

    color: Color = Field(default=Color.SILVER, description="Main body colour")

    # ------------------------------------------------------------------ #
    # Dunder                                                             #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Vehicle(name={self.name}, class={self.vehicle_class}, "
            f"color={self.color})"
        )
