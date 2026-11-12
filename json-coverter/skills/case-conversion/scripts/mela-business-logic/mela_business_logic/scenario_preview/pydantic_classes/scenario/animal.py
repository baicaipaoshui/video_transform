from enum import Enum

from pydantic import Field

from mela_business_logic.scenario_preview.pydantic_classes.scenario.actor import Actor


class AnimalSpecies(str, Enum):
    DOG = "dog"
    DEER = "deer"
    CAT = "cat"
    HORSE = "horse"
    COW = "cow"
    SHEEP = "sheep"
    OTHER = "other"


class Animal(Actor):
    """Animal road participant."""

    species: AnimalSpecies = Field(..., description="Animal species")

    def __str__(self) -> str:  # pragma: no cover
        return f"Animal(name={self.name}, species={self.species}"
