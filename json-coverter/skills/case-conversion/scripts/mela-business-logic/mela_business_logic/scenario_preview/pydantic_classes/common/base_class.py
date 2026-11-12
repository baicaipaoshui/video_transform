from typing import Any, ClassVar, Dict, Type, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import DuplicateIDError, NotFoundError

from . import logger

T_BaseClass = TypeVar("T_BaseClass", bound="BaseClass")


class BaseClass(BaseModel):
    """
    Common ancestor for *all* domain objects.

    Features
    --------
    • Auto‑generated UUID‑4 id (field alias ``UID``).
    • Per‑sub‑class registry for fast look‑ups by id.
    • Convenience helpers: ``get_by_id``, ``get_all``, ``unregister_by_id``.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    id: SkipJsonSchema[str] = Field(
        default_factory=lambda: str(uuid4()),
        alias="UID",
        description="Globally unique identifier (UUID‑4)",
    )

    # Registry lives **per concrete subclass**.
    _registry: ClassVar[Dict[str, "BaseClass"]] = {}

    # ------------------------------------------------------------------ #
    # Pydantic config                                                    #
    # ------------------------------------------------------------------ #

    class Config:
        extra = "forbid"
        use_enum_values = True
        arbitrary_types_allowed = False

    # ------------------------------------------------------------------ #
    # Construction / destruction                                         #
    # ------------------------------------------------------------------ #

    def __init__(self, **data: Any) -> None:  # noqa: D401
        """
        Validate *data*, create the instance, then insert it into the class
        registry.  Duplicate ids raise `DuplicateIDError`.
        """
        super().__init__(**data)  # Pydantic validation first.
        cls = self.__class__
        if self.id in cls._registry:
            raise DuplicateIDError(obj_type=cls.__name__, obj_id=self.id, logger=logger)

        cls._registry[self.id] = self
        logger.info(f"Registered {cls.__name__} '{self.id}' in registry.")

    # ------------------------------------------------------------------ #
    # Registry helpers                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_by_id(cls: Type[T_BaseClass], id_: str) -> T_BaseClass:
        """
        Return the object with *id_* or raise `NotFoundError`.
        """
        try:
            return cls._registry[id_]
        except KeyError:
            raise NotFoundError(obj_type=cls.__name__, obj_id=id_, logger=logger)

    @classmethod
    def unregister_by_id(cls, id_: str) -> None:
        """
        Remove the object with *id_* from the registry.

        Raises
        ------
        NotFoundError
            If the id is unknown.
        """
        if cls._registry.pop(id_, None) is None:
            raise NotFoundError(obj_type=cls.__name__, obj_id=id_, logger=logger)
        logger.info(f"Unregistered {cls.__name__} id '{id_}'.")

    @classmethod
    def clear_registry(cls) -> None:
        """
        Remove *all* entries from the registry (mainly for unit testing).
        """
        cls._registry.clear()
        logger.info(f"Cleared registry for {cls.__name__}.")
