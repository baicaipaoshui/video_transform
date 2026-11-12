from typing import Any, List, Optional, Union

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mela_business_logic.error import ModelValidationError
from mela_business_logic.scenario_preview.pydantic_classes.common.base_class import (
    BaseClass,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.actor import Actor
from mela_business_logic.scenario_preview.pydantic_classes.scenario.animal import Animal
from mela_business_logic.scenario_preview.pydantic_classes.scenario.pedestrian import (
    Pedestrian,
)
from mela_business_logic.scenario_preview.pydantic_classes.scenario.vehicle import (
    Vehicle,
)
from mela_business_logic.scenario_preview.pydantic_classes.scene.scene import Scene

from . import logger


class Scenario(BaseClass):
    """
    Root node of a *driving‑scenario* description.

    A Scenario
    ----------
    • owns a human‑readable *name* and optional *description*;
    • references exactly one road file via *scene_id*;
    • maintains a list of dynamic *Actor* objects.

    Note
    ----
    Only the **id** of the Scene is stored.  The Scene object itself can be
    fetched on demand via ``get_scene()``, which resorts to the global
    `BaseClass` registry.
    """

    # ------------------------------------------------------------------ #
    # Identification / meta                                              #
    # ------------------------------------------------------------------ #

    name: str = Field(..., description="Human readable scenario name")
    description: Optional[str] = Field(
        None, description="Optional free‑form description"
    )

    # ------------------------------------------------------------------ #
    # Mandatory road‑network reference                                   #
    # ------------------------------------------------------------------ #

    scene_id: SkipJsonSchema[str] = Field(
        ...,
        description="UID of the Scene (road file) this scenario is based on",
    )

    # ------------------------------------------------------------------ #
    # Dynamic contents                                                   #
    # ------------------------------------------------------------------ #

    # actors: List[Actor] = Field(  # type: ignore[name-defined]
    #     default_factory=list,
    #     description="Dynamic actors that participate in this scenario",
    #     exclude=True,
    # )

    vehicles: List[Vehicle] = Field(
        default_factory=list,
        description="List of vehicles that participate in this scenario",
        # exclude=True,
    )
    pedestrians: List[Pedestrian] = Field(
        default_factory=list,
        description="List of pedestrians that participate in this scenario",
        # exclude=True,
    )
    animals: List[Animal] = Field(
        default_factory=list,
        description="List of animals that participate in this scenario",
        # exclude=True,
    )

    # ------------------------------------------------------------------ #
    # Pydantic hooks                                                     #
    # ------------------------------------------------------------------ #

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """
        Post‑initialization: *no* validation against the Scene registry.
        Check for strictly one ego vehicle actor.
        Log what we have.
        """
        super().model_post_init(__context)
        num_actors = len(self.animals) + len(self.pedestrians) + len(self.vehicles)
        logger.info(
            f"Initialized Scenario '{self.name}' (scene_id={self.scene_id}, actors={num_actors})."
        )

    def validate_after_assembly(self) -> "Scenario":
        self.__set_one_vehicle_ego_actor()
        self.__check_start_position_or_remove()
        logger.info("All post assembly checks passed successfully.")
        return self

    def __set_one_vehicle_ego_actor(self) -> None:
        if len(self.vehicles) == 0:
            raise ModelValidationError(
                "Ego actor cannot be set if no vehicles are present in the scenario.",
                logger=logger,
            )
        for animal in self.animals:
            if animal.is_ego_actor:
                animal.is_ego_actor = False  # set all animals to non-ego actors
        for pedestrian in self.pedestrians:
            if pedestrian.is_ego_actor:
                pedestrian.is_ego_actor = False  # set all pedestrians to non-ego actors
        num_ego_actors = 0
        for vehicle in self.vehicles:
            if vehicle.is_ego_actor:
                num_ego_actors += 1
        if num_ego_actors != 1:
            for veh_idx, vehicle in enumerate(self.vehicles):
                if veh_idx == 0:
                    vehicle.is_ego_actor = True
                else:
                    vehicle.is_ego_actor = False
            logger.warning(
                f"Invalid number of ego actors, is_ego_actor → True forced for first vehicle, False for all others."
            )

    def __check_start_position_or_remove(self) -> None:
        """
        Checking if there is only one start_position omitted by the GPT and removing unnecessary start positions in subsequent maneuvers.
        """
        all_actors: List[Actor] = self.vehicles + self.pedestrians + self.animals
        for actor in all_actors:
            for pos, maneuver in enumerate(actor.timeline):
                if pos == 0:
                    if maneuver.action.start_position is None:
                        raise ModelValidationError(
                            "Ensure start position is provided for the initial maneuver.",
                            logger=logger,
                        )
                else:
                    if maneuver.action.start_position is not None:
                        logger.warning(
                            f"Setting unnecessary start position attribute of non-initial maneuver (maneuver {pos+1}) to None."
                        )
                        maneuver.action.start_position = None
        return self

    # ------------------------------------------------------------------ #
    # Public helpers                                                     #
    # ------------------------------------------------------------------ #

    # ---- Scene handling ---------------------------------------------- #

    def get_scene(self) -> Scene:
        """
        Retrieve the Scene from the global registry or raise `NotFoundError`.
        """
        return Scene.get_by_id(self.scene_id)

    # ---- Actor management -------------------------------------------- #

    def add_actor(self, actor: Union[Animal, Pedestrian, Vehicle]) -> None:
        """
        Register an `Actor` with this scenario.
        """
        actor.parent_id = self.id
        if isinstance(actor, Animal):
            actor_list_to_append = self.animals
        elif isinstance(actor, Pedestrian):
            actor_list_to_append = self.pedestrians
        elif isinstance(actor, Vehicle):
            actor_list_to_append = self.vehicles

        actor_list_to_append.append(actor)
        actor_name = getattr(actor, "name")
        logger.info(
            f"Added '{actor.__class__.__name__}' '{actor_name}' to Scenario '{self.name}' (total={len(actor_list_to_append)})."
        )

    def get_actors(self) -> List[Actor]:
        """
        Return a **copy** of the actor list to protect internal state.
        """
        # return self.actors.copy()
        actors = self.animals.copy() + self.pedestrians.copy() + self.vehicles.copy()
        return actors

    # ------------------------------------------------------------------ #
    # Dunder helpers                                                     #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:  # pragma: no cover
        return (
            f"Scenario(name={self.name}, "
            f"scene_id={self.scene_id}, "
            f"animals={len(self.animals)})"
            f"pedestrians={len(self.pedestrians)})"
            f"vehicles={len(self.vehicles)})"
            # f"actors={len(self.actors)})"
        )
