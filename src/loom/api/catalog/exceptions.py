from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from loom.domain.errors import (
    ConstraintViolation,
    InvariantViolation,
    NotFoundError,
    ValidationError,
)


class EntityNotFoundError(Exception):
    """Raised when a requested entity or version does not exist."""


class IllegalTransitionError(Exception):
    """Raised when a lifecycle transition is not structurally legal."""


class DomainValidationError(Exception):
    """Raised when a write violates a database-level invariant."""


def register_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to a consistent JSON error envelope."""

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(request: Request, exc: EntityNotFoundError) -> JSONResponse:
        del request
        content = {'error_code': 'not_found', 'message': str(exc)}
        return JSONResponse(status_code=404, content=content)

    @app.exception_handler(IllegalTransitionError)
    async def _illegal_transition(
        request: Request, exc: IllegalTransitionError
    ) -> JSONResponse:
        del request
        content = {'error_code': 'illegal_transition', 'message': str(exc)}
        return JSONResponse(status_code=409, content=content)

    @app.exception_handler(DomainValidationError)
    async def _validation(request: Request, exc: DomainValidationError) -> JSONResponse:
        del request
        content = {'error_code': 'validation_error', 'message': str(exc)}
        return JSONResponse(status_code=422, content=content)

    # loom.domain.errors -- raised by Aggregates/Repositories that have
    # gone through the DDD separation (see CONTEXT.md). FastAPI dispatches
    # on the exact exception type first, then walks the MRO for the
    # closest registered ancestor, so a new subclass of NotFoundError/
    # InvariantViolation in loom.domain is mapped automatically without
    # touching this file.
    @app.exception_handler(NotFoundError)
    async def _domain_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        del request
        content = {'error_code': 'not_found', 'message': str(exc)}
        return JSONResponse(status_code=404, content=content)

    @app.exception_handler(ValidationError)
    async def _domain_validation(request: Request, exc: ValidationError) -> JSONResponse:
        del request
        content = {'error_code': 'validation_error', 'message': str(exc)}
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(InvariantViolation)
    async def _domain_invariant(
        request: Request, exc: InvariantViolation
    ) -> JSONResponse:
        del request
        content = {'error_code': 'invariant_violation', 'message': str(exc)}
        return JSONResponse(status_code=409, content=content)

    @app.exception_handler(ConstraintViolation)
    async def _domain_constraint(
        request: Request, exc: ConstraintViolation
    ) -> JSONResponse:
        del request
        content = {'error_code': 'validation_error', 'message': str(exc)}
        return JSONResponse(status_code=422, content=content)
