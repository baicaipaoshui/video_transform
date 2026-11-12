from enum import Enum
from typing import ClassVar, Dict, List, Literal

from pydantic import Field, ValidationInfo, field_validator

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.scene.element import Element

from . import logger


class JunctionType(str, Enum):
    """Enumeration of supported junction topologies."""

    ROUNDABOUT = "roundabout"
    INTERSECTION = "intersection"
    T_JUNCTION = "t_junction"


class Junction(Element):
    """
    A junction/intersection within a `RoadNetwork`.

    Attributes:
        type: The geometric/topological junction type.
        num_connections: The number of stubs/legs that can connect to this junction.
                        (Minimum 3; overridden to 3 for a `T_JUNCTION`.)
    """

    CARDINAL_LETTERS: ClassVar[List[str]] = Literal[
        "N", "S", "E", "W", "NE", "NW", "SE", "SW"
    ]

    # ------------------------------------------------------------------ #
    # Data fields                                                        #
    # ------------------------------------------------------------------ #

    type: JunctionType = Field(
        default=JunctionType.INTERSECTION,
        description="Type of junction topology",
    )
    num_connections: int = Field(
        default=4,
        ge=3,
        description="Number of connection points for the junction",
    )

    cardinal_positions: List[CARDINAL_LETTERS] = Field(
        ..., description="The junction connection point directions in cardinal system."
    )

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    # letter  <->  cardinal
    _cardinal_mapping: Dict[str, str] = {
        "A": "N",
        "N": "A",
        "B": "E",
        "E": "B",
        "C": "S",
        "S": "C",
        "D": "W",
        "W": "D",
        "L": "NE",
        "NE": "L",
        "M": "SE",
        "SE": "M",
        "O": "SW",
        "SW": "O",
        "P": "NW",
        "NW": "P",
    }

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context) -> None:  # noqa: D401
        """
        Finalize initialization (called by Pydantic v2 after validation).
        """
        super().model_post_init(__context)

        # Ensure all expected positions are present (unconnected initially).
        self.initialize_unconnected_positions()
        logger.info(
            f"Initialized Junction '{self.name}' ({self.type}) with {self.num_connections} connection(s)."
        )

    # ------------------------------------------------------------------ #
    # Element API overrides                                              #
    # ------------------------------------------------------------------ #

    def get_available_connection_positions(self) -> List[str]:
        """
        Return *all* acceptable identifiers for this junction.

        Both letters (A, B, C…) and their cardinal synonyms (N, E, S…)
        are accepted, so we expose both variants to keep validation simple.
        """
        letters = [chr(65 + i) for i in range(self.num_connections)]  # "A", "B", …
        cardinals = [
            self._cardinal_mapping[ltr]
            for ltr in letters
            if ltr in self._cardinal_mapping
        ]
        return letters + cardinals

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #

    def get_canonical_position(self, position: str) -> str:
        """
        Map any accepted identifier (cardinal **or** letter) to its canonical
        form (letter).  If no mapping exists the original value is returned.
        """
        pos_up = position.upper()
        return self._cardinal_mapping.get(pos_up, pos_up)

    # ------------------------------------------------------------------ #
    # Pydantic validators                                                #
    # ------------------------------------------------------------------ #

    @field_validator("num_connections")
    @classmethod
    def _validate_num_connections(
        cls, v: int, info: ValidationInfo
    ) -> int:  # noqa: D401
        """
        Enforce junction–type specific rules on ``num_connections``.
        """
        junction_type: JunctionType = info.data.get("type", JunctionType.INTERSECTION)

        # T‑junctions *must* have exactly three legs, irrespective of input.
        if junction_type == JunctionType.T_JUNCTION.value:
            if v != 3:
                logger.warning(f"T-junction num_connections forced from {v} → 3.")
            return 3

        if v < 3:
            # Any other junction still needs ≥ 3 legs.
            raise ModelValidationError(
                "A junction must have at least 3 connection points.", logger=logger
            )

        return v

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Junction(name={self.name}, "
            f"type={self.type}, connections={self.num_connections})"
        )
