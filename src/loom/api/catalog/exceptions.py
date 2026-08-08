from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
