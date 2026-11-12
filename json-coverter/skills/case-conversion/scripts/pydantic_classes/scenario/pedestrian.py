from enum import Enum

from pydantic import Field

from mela_business_logic.scenario_preview.pydantic_classes.scenario.actor import Actor


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"


class AgeGroup(str, Enum):
    CHILD = "child"
    ADULT = "adult"
    SENIOR = "senior"


class TopWear(str, Enum):
    T_SHIRT = "t_shirt"  # t-shirt creates issues because llm responds with a different hyphen character than the one here
    SHIRT = "shirt"
    JACKET = "jacket"
    HOODIE = "hoodie"


class BottomWear(str, Enum):
    JEANS = "jeans"
    TROUSERS = "trousers"
    SHORTS = "shorts"
    SKIRT = "skirt"


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    BLACK = "black"
    ASMBLUE = "ASMBlue"
    ANTHRACITE = "Anthracite"
    BABYBLUE = "Babyblue"
    # ", , BabyBlue, BrightRed, Bronze, Brown, BurntOrange, DarkBlue, DarkRed, Green, LightBlue, LimeGreen, MatteAnthracite, MatteBlack, Orange, PaleBlue, Silver, White, Yellow"


class Pedestrian(Actor):
    """Human road user on foot."""

    gender: Gender = Field(default=Gender.MALE, description="Biological gender")
    age_group: AgeGroup = Field(
        default=AgeGroup.ADULT, description="Approximate age group"
    )
    top_wear: TopWear = Field(
        default=TopWear.T_SHIRT, description="Type of upper clothing"
    )
    # top_wear_color: Color = Field(
    #     default=Color.RED, description="Colour of top wear cloth"
    # )
    bottom_wear: BottomWear = Field(
        default=BottomWear.JEANS, description="Type of lower clothing"
    )
    # bottom_wear_color: Color = Field(
    #     default=Color.BLACK, description="Colour of bottom wear cloth"
    # )

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Pedestrian(name={self.name}, gender={self.gender}, "
            f"age_group={self.age_group}, "
            f"top={self.top_wear}, bottom={self.bottom_wear})"
        )
