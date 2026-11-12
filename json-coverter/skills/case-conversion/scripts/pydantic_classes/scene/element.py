from typing import Dict, List, Optional

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import (
    ConnectionAlreadyExists,
    InvalidConnectionPosition,
    InvalidElement,
    ModelValidationError,
)
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.connection_point import (
    ConnectionPoint,
)

from . import logger


class Element(BaseClass):
    """
    Base class for all items that can be placed inside a ``RoadNetwork``
    (e.g., ``Road``, ``Junction``).  Sub‑classes must override
    `get_available_connection_positions`.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #
    name: Optional[str] = Field(None, description="Human readable element name")
    description: Optional[str] = Field(None, description="Optional description")
    parent_id: SkipJsonSchema[str] = Field(
        ...,
        description="Parent road network ID",
        # exclude=True
    )

    # ------------------------------------------------------------------ #
    # Connectivity                                                       #
    # ------------------------------------------------------------------ #
    connection_points: SkipJsonSchema[Dict[str, ConnectionPoint]] = Field(
        default_factory=dict,
        # exclude=True
    )

    # ------------------------------------------------------------------ #
    # API that sub‑classes **must** implement                            #
    # ------------------------------------------------------------------ #
    def get_available_connection_positions(self) -> List[str]:  # noqa: D401
        """Return connection identifiers allowed for this element."""
        raise ModelValidationError(
            f"{self.__class__.__name__} must implement "
            "`get_available_connection_positions`.",
            logger=logger,
        )

    # ------------------------------------------------------------------ #
    # Public connectivity helpers                                        #
    # ------------------------------------------------------------------ #
    def connect(
        self,
        other_element: "Element",
        this_position: str,
        other_position: str,
    ) -> ConnectionPoint:
        """
        Create a shared ``ConnectionPoint`` between *self* and *other_element*.

        Raises
        ------
        InvalidConnectionPosition
            if either position is not allowed for the respective element.
        ConnectionAlreadyExists
            if the requested position is already occupied.
        InvalidElement
            if trying to connect an element to itself.
        """
        # Validate different elements
        self._ensure_different_element(other_element)

        # Validate both requested positions
        self._validate_position(this_position)
        other_element._validate_position(other_position)

        # Ensure positions are free
        self._ensure_position_free(this_position)
        other_element._ensure_position_free(other_position)

        connection_point = ConnectionPoint(
            element_ids=[self.id, other_element.id],
            positions={
                self.id: this_position,
                other_element.id: other_position,
            },
        )
        self.connection_points[this_position] = connection_point
        other_element.connection_points[other_position] = connection_point

        logger.info(
            f"Connected '{self.name}' ({this_position}) <-> '{other_element.name}' ({other_position})."
        )
        return connection_point

    def initialize_unconnected_positions(self) -> None:
        """
        Ensure *all* allowed positions have at least a single‑element
        ``ConnectionPoint``, so the dictionary is complete.
        """
        for pos in self.get_available_connection_positions():
            # Idempotent: only create if not present.
            if pos not in self.connection_points:
                self.connection_points[pos] = ConnectionPoint(
                    element_ids=[self.id], positions={self.id: pos}
                )
                logger.info(
                    f"Initialized standalone connection '{pos}' on '{self.name}'."
                )

    def get_connected_element_at_position(self, position: str) -> Optional["Element"]:
        """
        Return the *other* element connected at ``position``, or ``None`` if
        unconnected / self‑connected.
        """
        cp = self.connection_points.get(position)
        if not cp:
            logger.info(f"Position '{position}' not present on '{self.name}'.")
            return None

        # Exclude own id
        foreign_ids = [eid for eid in cp.element_ids if eid != self.id]
        if not foreign_ids:
            logger.info(
                f"Position '{position}' on '{self.name}' is not connected to another element."
            )
            return None

        # Only one other element expected; ignore the rest if present.
        return self.__class__.get_by_id(foreign_ids[0])

    # ------------------------------------------------------------------ #
    # Validation helpers                                                 #
    # ------------------------------------------------------------------ #
    def _validate_position(self, position: str) -> None:
        """Raise InvalidConnectionPosition if the position is invalid."""
        if position not in self.get_available_connection_positions():
            raise InvalidConnectionPosition(position, self._log_id(), logger=logger)

    def _ensure_position_free(self, position: str) -> None:
        """Raise ConnectionAlreadyExists if position already occupied."""
        if position in self.connection_points:
            raise ConnectionAlreadyExists(position, self._log_id(), logger=logger)

    def _ensure_different_element(self, other_element: "Element"):
        """Raise InvalidElement if connecting to the same element."""
        if self is other_element:
            raise InvalidElement(obj_id=other_element.name, logger=logger)

    # ------------------------------------------------------------------ #
    # Dunder methods                                                     #
    # ------------------------------------------------------------------ #
    # Must be implemented in child classes
