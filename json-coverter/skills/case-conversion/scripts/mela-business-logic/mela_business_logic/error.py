"""Mela validation exception types required by the bundled pydantic models."""


class MelaError(Exception):
    def __init__(self, message="", logger=None, critical=False, **_kwargs):
        if logger is not None:
            (logger.critical if critical else logger.error)(message)
        super().__init__(message)


class ProcessingError(MelaError):
    pass


class ModelValidationError(MelaError):
    pass


class DuplicateIDError(ModelValidationError):
    def __init__(self, obj_type, obj_id, *args, **kwargs):
        super().__init__(f"Duplicate id '{obj_id}' detected in {obj_type}")


class NotFoundError(ModelValidationError):
    def __init__(self, obj_type, obj_id, *args, **kwargs):
        super().__init__(f"No {obj_type} found with id '{obj_id}'")


class InvalidConnectionPosition(ModelValidationError):
    pass


class ConnectionAlreadyExists(ModelValidationError):
    pass


class InvalidElement(ModelValidationError):
    pass
