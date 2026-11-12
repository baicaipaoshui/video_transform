from typing import Dict, List, Optional

from pydantic import Field

from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)

from . import logger


class ConnectionPoint(BaseClass):
    """
    Internal representation of a logical joint between up to *two* `Element`
    instances.

    A fully connected point keeps both element IDs plus the position at which
    each Element participates.
    """

    # ------------------------------------------------------------------ #
    # Data fields                                                        #
    # ------------------------------------------------------------------ #

    element_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="List with one (stub) or two (connected) element IDs.",
    )

    positions: Dict[str, str] = Field(
        ...,
        description=(
            "Mapping element‑id → named position at which the element is attached."
        ),
    )

    # Arbitrary metadata (lane index, heading, etc.) – free for users.
    # properties: Dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #

    def is_connected_to_element(self, element_id: str) -> bool:
        """Return True iff ``element_id`` participates in this connection."""
        return element_id in self.element_ids

    def get_other_element_id(self, element_id: str) -> Optional[str]:
        """
        Return the *other* participant or ``None`` if:
        • the supplied element is not part of this joint, or
        • the connection is still a single stub.
        """
        if not self.is_connected_to_element(element_id) or len(self.element_ids) < 2:
            logger.info(
                f"ConnectionPoint '{self.id}': no peer found for '{element_id}'."
            )
            return None

        return next(eid for eid in self.element_ids if eid != element_id)

    def get_position_for_element(self, element_id: str) -> Optional[str]:
        """
        Return the position label used by ``element_id``, or ``None`` if the
        element is not part of this joint.
        """
        return self.positions.get(element_id)

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        joined = ", ".join(self.element_ids)
        return f"ConnectionPoint(id={self.id}, element_ids=[{joined}])"
