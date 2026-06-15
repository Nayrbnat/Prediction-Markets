"""Exception hierarchy and FastAPI handlers that map errors to clean HTTP
responses without leaking stack traces. Catch narrowly; never bare ``except``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base application error. Subclasses set ``status_code`` and ``code``."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class SourceError(AppError):
    status_code = 502
    code = "source_error"


class RateLimitError(SourceError):
    status_code = 429
    code = "rate_limited"


class SchemaDriftError(SourceError):
    status_code = 502
    code = "schema_drift"


class PersistenceError(AppError):
    status_code = 503
    code = "persistence_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


# v2 seams (declared so callers can import them; unused in v1)
class OnChainError(SourceError):
    code = "onchain_error"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app.error",
            extra={"code": exc.code, "status": exc.status_code, "message": exc.message},
        )
        body: dict[str, object] = {"error": exc.code, "message": exc.message}
        if exc.detail is not None:
            body["detail"] = exc.detail
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("app.unhandled", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "An unexpected error occurred."},
        )
